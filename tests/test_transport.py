# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for transport abstractions, BOT wrappers, sense decoders, and mock bridge."""

import struct

import pytest

from openeasysync.constants import (
    CSW_STATUS_PASSED,
    MODE_TRANSFER_LINE,
    OTI_CBW_TAG,
    SENSE_MAP,
    SPEED_USB20,
    USBC_SIGNATURE,
    USBS_SIGNATURE,
    decode_sense_code,
)
from openeasysync.transport.mock import MockBridgeCable
from openeasysync.transport.usb_bot import parse_bot_csw, wrap_bot_cbw


def test_wrap_bot_cbw():
    """Verify CBW conforms to 31-byte USB BOT specification."""
    cdb = bytes([0xF0] + [0x00] * 13 + [0x4F, 0x54])
    cbw = wrap_bot_cbw(cdb, 12, direction_in=True, tag=OTI_CBW_TAG)

    assert len(cbw) == 31
    sig, tag, xfer_len, flags, lun, cdb_len = struct.unpack_from("<IIIBBB", cbw, 0)
    assert sig == USBC_SIGNATURE
    assert tag == OTI_CBW_TAG
    assert xfer_len == 12
    assert flags == 0x80  # IN
    assert lun == 0
    assert cdb_len == 16
    assert cbw[15:31] == cdb


def test_parse_bot_csw():
    """Verify CSW parsing and tag validation."""
    csw_raw = struct.pack("<IIIB", USBS_SIGNATURE, OTI_CBW_TAG, 0, CSW_STATUS_PASSED)
    tag, residue, status = parse_bot_csw(csw_raw, expected_tag=OTI_CBW_TAG)
    assert tag == OTI_CBW_TAG
    assert residue == 0
    assert status == CSW_STATUS_PASSED

    # Invalid signature raises ValueError
    bad_sig = struct.pack("<IIIB", 0x12345678, OTI_CBW_TAG, 0, 0)
    with pytest.raises(ValueError, match="Invalid CSW signature"):
        parse_bot_csw(bad_sig)

    # Tag mismatch raises ValueError
    with pytest.raises(ValueError, match="CSW tag mismatch"):
        parse_bot_csw(csw_raw, expected_tag=0x99999999)


def test_decode_sense_codes():
    """Verify all 24-bit SCSI sense codes match Part 1 Section 4 lookup table."""
    for sense24, (expected_code, _) in SENSE_MAP.items():
        sk = (sense24 >> 16) & 0x0F
        asc = (sense24 >> 8) & 0xFF
        ascq = sense24 & 0xFF
        decoded24, code, _ = decode_sense_code(sk, asc, ascq)
        assert decoded24 == sense24
        assert code == expected_code

    # Unrecognized code returns 0xFFFF
    _, code, _ = decode_sense_code(0x05, 0x99, 0x99)
    assert code == 0xFFFF


def test_mock_bridge_cable_loopback():
    """Verify MockBridgeCable simulates registers, speeds, and pipe transfers."""
    cable = MockBridgeCable()
    host_a, host_b = cable.create_pair()

    # Identify
    info_a = host_a.identify()
    assert info_a["chip_id"] == "OTi-2108"
    assert info_a["fw_rev"] == 3
    assert info_a["fw_date"] == "030729"

    # Speeds
    local_spd, peer_spd = host_a.get_usb_speed()
    assert local_spd == SPEED_USB20
    assert peer_spd == SPEED_USB20

    # Modes
    assert host_a.get_peer_mode() == MODE_TRANSFER_LINE
    host_b.set_mode(0x0044)
    assert host_a.get_peer_mode() == 0x0044

    # Bulk Block Write (A -> B)
    test_block = b"HELLO_OTI2108_" * 4000
    host_a.write_bulk_block(test_block)

    # Poll from B should detect remote block
    action, rem_blocks, _, _ = host_b.poll_link(0)
    assert action == 5
    assert rem_blocks == 1

    # Bulk Block Read on B
    received = host_b.read_bulk_block()
    assert len(received) == 65536
    assert received.startswith(b"HELLO_OTI2108_")

    host_a.close()
    host_b.close()


def test_pyusb_transport_data_stall_and_recovery():
    """Verify PyUsbTransport handles endpoint stall, clears halt, and reads CSW."""
    from unittest.mock import MagicMock, patch

    import usb.core

    from openeasysync.constants import CSW_STATUS_FAILED
    from openeasysync.transport.usb_bot import PyUsbTransport

    mock_dev = MagicMock()
    mock_dev.bus = 1
    mock_dev.address = 2
    mock_dev.is_kernel_driver_active.return_value = False
    mock_intf = MagicMock()
    mock_intf.bInterfaceNumber = 0
    mock_cfg = {(0, 0): mock_intf}
    mock_dev.get_active_configuration.return_value = mock_cfg

    ep_out = MagicMock()
    ep_out.bEndpointAddress = 0x02  # OUT
    ep_in = MagicMock()
    ep_in.bEndpointAddress = 0x81  # IN

    with (
        patch("usb.util.find_descriptor", side_effect=[ep_out, ep_in]),
        patch("usb.util.claim_interface"),
    ):
        transport = PyUsbTransport(dev=mock_dev)

    # Verify set_configuration is called on setup to synchronize data toggles
    mock_dev.set_configuration.assert_called_once()

    # Simulate STALL on data read, followed by CSW reporting failure
    pipe_err = usb.core.USBError("Pipe error", -9, 32)
    ep_in.read.side_effect = [
        pipe_err,  # Data stage stalls with EPIPE
        struct.pack("<IIIB", USBS_SIGNATURE, OTI_CBW_TAG, 0, CSW_STATUS_FAILED),  # CSW
        bytes([0x70, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x0A, 0, 0, 0, 0, 0x20, 0x00]),  # Sense
        struct.pack("<IIIB", USBS_SIGNATURE, OTI_CBW_TAG, 0, CSW_STATUS_PASSED),  # Sense CSW
    ]

    status, data, sense = transport.execute_cdb(bytes([0xF0] + [0] * 15), "IN", 12)
    assert status == CSW_STATUS_FAILED
    assert data == b""
    # clear_halt should have been called on ep_in to unfreeze endpoint
    mock_dev.clear_halt.assert_any_call(ep_in)
    # Sense should be parsed (sk=5, asc=0x20, ascq=0)
    assert sense == (0x05, 0x20, 0x00)

    # Test reset_recovery
    transport.reset_recovery()
    mock_dev.ctrl_transfer.assert_called_with(0x21, 0xFF, 0x0000, 0, b"", timeout=1000)


def test_read_flash_config_cdb_layout():
    """Verify CDB 0xDB 0x28 layout matches spec section 3.2.7."""
    from openeasysync.transport.base import TransportBackend

    class DummyTransport(TransportBackend):
        def __init__(self):
            self.calls = []

        def execute_cdb(self, cdb, direction, data_or_length=0, timeout_ms=5000):
            self.calls.append((cdb, direction, data_or_length, timeout_ms))
            return CSW_STATUS_PASSED, bytes(512), (0, 0, 0)

        def close(self):
            pass

    t = DummyTransport()
    data = t.read_flash_config(addr=0x0022, count=1, timeout_ms=3000)
    assert len(data) == 512
    assert len(t.calls) == 1
    cdb, direction, length, timeout_ms = t.calls[0]
    assert len(cdb) == 16
    assert cdb == bytes.fromhex("DB280000002200000100000000004F54")
    assert direction == "IN"
    assert length == 512
    assert timeout_ms == 3000


def test_query_buffer_size_otiming_custom_lookup():
    """Verify fw_rev > 2 and size_code == 0 triggers 0xDB 0x28 OTiMing lookup."""
    cable = MockBridgeCable(fw_rev=3, buffer_size_code=0, custom_buffer_size=0x180000)
    host_a, host_b = cable.create_pair()
    try:
        info = host_a.identify()
        assert info["extended_cap"] is True
        assert host_a.query_buffer_size() == 0x180000
        assert host_a.get_buffer_size() == 0x180000
    finally:
        host_a.close()
        host_b.close()


def test_query_buffer_size_fallback_without_otiming():
    """Verify size_code == 0 without OTiMing signature falls back to 0x80000."""
    cable = MockBridgeCable(fw_rev=3, buffer_size_code=0, custom_buffer_size=None)
    host_a, host_b = cable.create_pair()
    try:
        host_a.identify()
        assert host_a.query_buffer_size() == 0x80000
    finally:
        host_a.close()
        host_b.close()


def test_query_buffer_size_skips_db28_when_fw_rev_le_2():
    """Verify fw_rev <= 2 with size_code == 0 does not issue 0xDB 0x28 and returns 0x80000."""
    from unittest.mock import patch

    cable = MockBridgeCable(fw_rev=2, buffer_size_code=0, custom_buffer_size=0x180000)
    host_a, host_b = cable.create_pair()
    try:
        info = host_a.identify()
        assert info["extended_cap"] is False
        with patch.object(host_a, "read_flash_config", wraps=host_a.read_flash_config) as spy:
            assert host_a.query_buffer_size() == 0x80000
            spy.assert_not_called()
    finally:
        host_a.close()
        host_b.close()


def test_query_buffer_size_auto_identifies_if_needed():
    """Verify query_buffer_size calls identify() when extended_cap is not yet known."""
    cable = MockBridgeCable(fw_rev=3, buffer_size_code=0, custom_buffer_size=0x200000)
    host_a, host_b = cable.create_pair()
    try:
        assert host_a.query_buffer_size() == 0x200000
    finally:
        host_a.close()
        host_b.close()
