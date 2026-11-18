# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit and loopback tests for Virtual NIC mode (0x0044, opcode 0xFF, 0x39 handshake)."""

import struct

import pytest

from openeasysync.client.session import SessionManager
from openeasysync.constants import (
    MODE_OFFLINE,
    MODE_VIRTUAL_NIC,
    OP_VNIC_SWITCH,
    VNIC_BLOCK_SIZE,
)
from openeasysync.framing.vnic import (
    NDIS_PACKET_TYPE_DEFAULT,
    NDIS_PACKET_TYPE_PROMISCUOUS,
    VNIC_MAX_FRAME_LEN,
    VNIC_MAX_FRAMES_PER_BLOCK,
    VNIC_PAYLOAD_OPCODE,
    decode_vnic_batch,
    encode_vnic_batch,
    encode_vnic_batches,
    generate_vnic_mac,
    is_vnic_block,
    matches_packet_filter,
)
from openeasysync.protocol.commands import (
    VNIC_CMD_ACCEPT_SWITCH,
    VNIC_CMD_ME_SWITCH_NONE_ROUTER,
    VNIC_CMD_ME_SWITCH_ROUTER,
    VNIC_CMD_PLEASE_SWITCH,
    VNIC_CMD_REJECT_SWITCH,
    decode_command,
    encode_extended_xml,
    encode_vnic_switch_accept,
    encode_vnic_switch_reject,
    encode_vnic_switch_request,
    encode_vnic_switch_role,
    parse_extra_xml_command,
)
from openeasysync.server.handler import PeerServerHandler
from openeasysync.transport.mock import MockBridgeCable
from openeasysync.vnic import InMemoryTapDevice, VirtualNicBridge


def _make_eth_frame(dst_mac: bytes, src_mac: bytes, ethertype: int = 0x0800, payload: bytes = b"hello") -> bytes:
    """Builds a valid IEEE 802.3 Ethernet frame (14..1514 bytes)."""
    frame = dst_mac + src_mac + struct.pack(">H", ethertype) + payload
    if len(frame) < 14:
        frame = frame.ljust(14, b"\x00")
    return frame


# ---------------------------------------------------------------------------
# 1. 0x8A00 0xFF Ethernet Frame Batch Framing
# ---------------------------------------------------------------------------


def test_vnic_batch_single_and_multi_frame_roundtrip():
    mac_a = b"\x02\x0e\xa0\x00\x00\x01"
    mac_b = b"\x02\x0e\xa0\x00\x00\x02"
    frames = [
        _make_eth_frame(mac_b, mac_a, payload=b"frame-1-" + b"A" * 40),
        _make_eth_frame(b"\xff\xff\xff\xff\xff\xff", mac_a, ethertype=0x0806, payload=b"arp-req"),
        _make_eth_frame(mac_a, mac_b, payload=b"frame-3"),
    ]

    block = encode_vnic_batch(frames)
    assert len(block) == VNIC_BLOCK_SIZE
    assert is_vnic_block(block)

    # Verify 20-byte header and 0xFF payload marker
    b_seq, m_seq, c_idx, total_c, payload_len = struct.unpack_from("<IIIII", block, 0)
    assert (b_seq, m_seq, c_idx, total_c, payload_len) == (0, 0, 0, 1, VNIC_BLOCK_SIZE)
    assert block[20] == VNIC_PAYLOAD_OPCODE

    # Verify 0x0000 terminator immediately after last frame
    end_off = 21 + sum(2 + len(f) for f in frames)
    assert struct.unpack_from("<H", block, end_off)[0] == 0

    decoded = decode_vnic_batch(block)
    assert decoded == frames


def test_vnic_batch_max_23_full_size_frames():
    mac_a = b"\x02\x0e\xa0\x11\x22\x33"
    mac_b = b"\x02\x0e\xa0\x44\x55\x66"
    max_payload = b"X" * (VNIC_MAX_FRAME_LEN - 14)
    frames = [_make_eth_frame(mac_b, mac_a, payload=max_payload) for _ in range(VNIC_MAX_FRAMES_PER_BLOCK)]
    assert all(len(f) == VNIC_MAX_FRAME_LEN for f in frames)

    block = encode_vnic_batch(frames)
    assert len(block) == VNIC_BLOCK_SIZE
    assert decode_vnic_batch(block) == frames

    with pytest.raises(ValueError, match="Too many frames"):
        encode_vnic_batch(frames + [frames[0]])


def test_vnic_batches_splits_over_23_frames():
    mac_a = b"\x02\x0e\xa0\x01\x02\x03"
    mac_b = b"\x02\x0e\xa0\x04\x05\x06"
    frames = [_make_eth_frame(mac_b, mac_a, payload=f"pkt-{i}".encode()) for i in range(50)]

    assert encode_vnic_batches([]) == []
    blocks = encode_vnic_batches(frames)
    assert len(blocks) == 3  # 23 + 23 + 4

    reconstructed: list[bytes] = []
    for blk in blocks:
        assert len(blk) == VNIC_BLOCK_SIZE
        reconstructed.extend(decode_vnic_batch(blk))
    assert reconstructed == frames


def test_vnic_batch_validation_errors():
    mac = b"\x02\x0e\xa0\x00\x00\x01"
    with pytest.raises(ValueError, match="Invalid Ethernet frame length"):
        encode_vnic_batch([b"short"])
    with pytest.raises(ValueError, match="Invalid Ethernet frame length"):
        encode_vnic_batch([b"A" * (VNIC_MAX_FRAME_LEN + 1)])

    # Non-vnic block handling
    bad_block = bytearray(VNIC_BLOCK_SIZE)
    assert not is_vnic_block(bytes(bad_block))
    assert decode_vnic_batch(bytes(bad_block), strict=False) == []
    with pytest.raises(ValueError, match="chunk_payload_len"):
        decode_vnic_batch(bytes(bad_block), strict=True)

    struct.pack_into("<I", bad_block, 0x10, VNIC_BLOCK_SIZE)
    bad_block[20] = 0x01  # Not 0xFF
    assert not is_vnic_block(bytes(bad_block))
    assert decode_vnic_batch(bytes(bad_block), strict=False) == []
    with pytest.raises(ValueError, match="payload marker"):
        decode_vnic_batch(bytes(bad_block), strict=True)

    # Empty batch (0x0000 immediately at +0x15)
    empty_block = encode_vnic_batch([])
    assert decode_vnic_batch(empty_block) == []
    assert _make_eth_frame(mac, mac)[:6] == mac


def test_vnic_mac_generation_and_packet_filter():
    mac = generate_vnic_mac(b"test-seed")
    assert len(mac) == 6
    assert mac[0] & 0x01 == 0  # Unicast (I/G bit clear)
    assert mac[0] & 0x02 == 0x02  # Locally administered
    assert mac == generate_vnic_mac(b"test-seed")

    other_mac = b"\x02\x0e\xa0\xaa\xbb\xcc"
    mcast_mac = b"\x01\x00\x5e\x00\x00\x01"
    bcast_mac = b"\xff\xff\xff\xff\xff\xff"

    assert matches_packet_filter(_make_eth_frame(mac, other_mac), mac, NDIS_PACKET_TYPE_DEFAULT)
    assert matches_packet_filter(_make_eth_frame(bcast_mac, other_mac), mac, NDIS_PACKET_TYPE_DEFAULT)
    assert matches_packet_filter(_make_eth_frame(mcast_mac, other_mac), mac, NDIS_PACKET_TYPE_DEFAULT)
    assert not matches_packet_filter(_make_eth_frame(other_mac, mac), mac, NDIS_PACKET_TYPE_DEFAULT)
    assert matches_packet_filter(_make_eth_frame(other_mac, mac), mac, NDIS_PACKET_TYPE_PROMISCUOUS)


# ---------------------------------------------------------------------------
# 2. 0x39 <ExtraXmlCommand> Mode-Switch Handshake
# ---------------------------------------------------------------------------


def test_0x39_extra_xml_command_codecs():
    req = encode_vnic_switch_request()
    assert req[0] == OP_VNIC_SWITCH
    dec_req = decode_command(req)
    assert dec_req["extra_xml_command"] == VNIC_CMD_PLEASE_SWITCH

    acc = encode_vnic_switch_accept()
    assert decode_command(acc)["extra_xml_command"] == VNIC_CMD_ACCEPT_SWITCH

    rej = encode_vnic_switch_reject()
    assert decode_command(rej)["extra_xml_command"] == VNIC_CMD_REJECT_SWITCH

    role_router = encode_vnic_switch_role(is_router=True)
    assert decode_command(role_router)["extra_xml_command"] == VNIC_CMD_ME_SWITCH_ROUTER

    role_std = encode_vnic_switch_role(is_router=False)
    assert decode_command(role_std)["extra_xml_command"] == VNIC_CMD_ME_SWITCH_NONE_ROUTER

    assert parse_extra_xml_command("<ExtraXmlCommand>Hello</ExtraXmlCommand>") == "Hello"
    assert parse_extra_xml_command("") is None
    assert parse_extra_xml_command("<Other>Test</Other>") is None


def test_server_handler_vnic_negotiation_and_no_pingpong():
    entered_roles: list[bool] = []
    handler = PeerServerHandler(
        allow_vnic_switch=True,
        on_vnic_mode_entered=entered_roles.append,
    )

    rep = handler.handle_command(encode_vnic_switch_request())
    assert len(rep) == 1
    assert decode_command(rep[0])["extra_xml_command"] == VNIC_CMD_ACCEPT_SWITCH
    assert handler.vnic_switch_accepted is True

    rep_role = handler.handle_command(encode_vnic_switch_role(is_router=True))
    assert len(rep_role) == 1
    assert decode_command(rep_role[0])["extra_xml_command"] == "OK"
    assert handler.vnic_peer_is_router is True
    assert entered_roles == [True]

    # Response messages must not trigger another reply (no infinite ping-pong)
    assert handler.handle_command(encode_vnic_switch_accept()) == []
    assert handler.handle_command(encode_vnic_switch_reject()) == []
    assert handler.handle_command(encode_extended_xml(OP_VNIC_SWITCH, "<ExtraXmlCommand>OK</ExtraXmlCommand>")) == []

    # Callback override can reject dynamically
    cb_handler = PeerServerHandler(on_vnic_switch_request=lambda _xml: False)
    rep_cb = cb_handler.handle_command(encode_vnic_switch_request())
    assert decode_command(rep_cb[0])["extra_xml_command"] == VNIC_CMD_REJECT_SWITCH


def test_session_manager_vnic_handshake_loopback():
    cable = MockBridgeCable()
    t_a, t_b = cable.create_pair()
    sess_a = SessionManager(t_a)
    sess_b = SessionManager(t_b)

    # Allow Virtual NIC mode switch on Host B
    mode_events: list[bool] = []
    sess_b.server_handler.allow_vnic_switch = True
    sess_b.server_handler.on_vnic_mode_entered = mode_events.append

    try:
        sess_a.connect(settle_delay_s=0.05, wait_settle=False)
        sess_b.connect(settle_delay_s=0.05, wait_settle=False)

        assert sess_a.request_vnic_switch(is_router=True, timeout=3.0) is True
        assert sess_b.server_handler.vnic_switch_accepted is True
        assert sess_b.server_handler.vnic_peer_is_router is True
        assert mode_events == [True]

        # Test rejection path
        sess_b.server_handler.allow_vnic_switch = False
        assert sess_a.request_vnic_switch(is_router=False, timeout=3.0) is False
    finally:
        sess_a.disconnect()
        sess_b.disconnect()


# ---------------------------------------------------------------------------
# 3. Virtual NIC Session & Loopback Packet Exchange
# ---------------------------------------------------------------------------


def test_vnic_bridge_bidirectional_packet_exchange():
    cable = MockBridgeCable()
    t_a, t_b = cable.create_pair()

    mac_a = b"\x02\x0e\xa0\x00\x00\x0a"
    mac_b = b"\x02\x0e\xa0\x00\x00\x0b"
    tap_a = InMemoryTapDevice(mac_address=mac_a)
    tap_b = InMemoryTapDevice(mac_address=mac_b)

    bridge_a = VirtualNicBridge(t_a, tap_a, poll_interval_s=0.002)
    bridge_b = VirtualNicBridge(t_b, tap_b, poll_interval_s=0.002)

    try:
        bridge_a.start()
        bridge_b.start()

        assert cable.mode["A"] == MODE_VIRTUAL_NIC
        assert cable.mode["B"] == MODE_VIRTUAL_NIC

        # Unicast A -> B and B -> A
        pkt_ab = _make_eth_frame(mac_b, mac_a, payload=b"ping-from-a-to-b")
        pkt_ba = _make_eth_frame(mac_a, mac_b, payload=b"pong-from-b-to-a")
        tap_a.inject_outbound_frame(pkt_ab)
        tap_b.inject_outbound_frame(pkt_ba)

        assert tap_b.pop_inbound_frame(timeout=2.0) == pkt_ab
        assert tap_a.pop_inbound_frame(timeout=2.0) == pkt_ba

        # Broadcast frame A -> B
        bcast = _make_eth_frame(b"\xff\xff\xff\xff\xff\xff", mac_a, ethertype=0x0806, payload=b"who-has")
        tap_a.inject_outbound_frame(bcast)
        assert tap_b.pop_inbound_frame(timeout=2.0) == bcast

        # Unicast to unknown MAC should be filtered out by Host B's default packet filter
        stranger_mac = b"\x02\x0e\xa0\x99\x99\x99"
        dropped_pkt = _make_eth_frame(stranger_mac, mac_a, payload=b"should-drop")
        accepted_pkt = _make_eth_frame(mac_b, mac_a, payload=b"after-drop")
        tap_a.inject_outbound_frame(dropped_pkt)
        tap_a.inject_outbound_frame(accepted_pkt)
        assert tap_b.pop_inbound_frame(timeout=2.0) == accepted_pkt

        assert bridge_a.tx_packets >= 4
        assert bridge_b.rx_packets >= 3
    finally:
        bridge_a.stop(restore_mode=MODE_OFFLINE)
        bridge_b.stop(restore_mode=MODE_OFFLINE)
        tap_a.close()
        tap_b.close()

    assert cable.mode["A"] == MODE_OFFLINE
    assert cable.mode["B"] == MODE_OFFLINE
