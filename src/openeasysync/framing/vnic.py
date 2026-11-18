# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Virtual NIC (mode 0x0044) 0x8A00-byte Ethernet frame batch framing and filtering."""

import os
import struct
from collections.abc import Sequence

from openeasysync.constants import BLOCK_HEADER_SIZE, VNIC_BLOCK_SIZE

VNIC_PAYLOAD_OPCODE = 0xFF
VNIC_HEADER_OVERHEAD = BLOCK_HEADER_SIZE + 1  # 20-byte header + 1-byte 0xFF marker
VNIC_MAX_FRAMES_PER_BLOCK = 23
VNIC_MIN_FRAME_LEN = 14
VNIC_MAX_FRAME_LEN = 1514  # 0x05EA

# NDIS packet filter bitmask constants (OID_GEN_CURRENT_PACKET_FILTER)
NDIS_PACKET_TYPE_DIRECTED = 0x0001
NDIS_PACKET_TYPE_MULTICAST = 0x0002
NDIS_PACKET_TYPE_ALL_MULTICAST = 0x0004
NDIS_PACKET_TYPE_BROADCAST = 0x0008
NDIS_PACKET_TYPE_PROMISCUOUS = 0x0020
NDIS_PACKET_TYPE_DEFAULT = (
    NDIS_PACKET_TYPE_DIRECTED | NDIS_PACKET_TYPE_MULTICAST | NDIS_PACKET_TYPE_BROADCAST
)

_BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"


def encode_vnic_batch(frames: Sequence[bytes]) -> bytes:
    """Packs up to 23 raw IEEE 802.3 Ethernet frames into a 0x8A00-byte Virtual NIC block.

    Wire layout:
      - +0x00..0x13 (20B): block_seq=0, msg_seq=0, chunk_idx=0, total_chunks=1, payload_len=0x8A00
      - +0x14 (1B): 0xFF payload marker
      - +0x15..: [frame_len:u16_le][frame_bytes]... + 0x0000 terminator (zero-padded to 0x8A00)
    """
    if len(frames) > VNIC_MAX_FRAMES_PER_BLOCK:
        raise ValueError(
            f"Too many frames for a single Virtual NIC block: {len(frames)} > {VNIC_MAX_FRAMES_PER_BLOCK}"
        )

    buf = bytearray(VNIC_BLOCK_SIZE)
    struct.pack_into("<IIIII", buf, 0, 0, 0, 0, 1, VNIC_BLOCK_SIZE)
    buf[BLOCK_HEADER_SIZE] = VNIC_PAYLOAD_OPCODE

    offset = VNIC_HEADER_OVERHEAD
    for frame in frames:
        frame_len = len(frame)
        if not (VNIC_MIN_FRAME_LEN <= frame_len <= VNIC_MAX_FRAME_LEN):
            raise ValueError(
                f"Invalid Ethernet frame length {frame_len}; "
                f"expected {VNIC_MIN_FRAME_LEN}..{VNIC_MAX_FRAME_LEN}"
            )
        if offset + 2 + frame_len > VNIC_BLOCK_SIZE:
            raise ValueError("Ethernet frame batch exceeds 0x8A00-byte Virtual NIC block")
        struct.pack_into("<H", buf, offset, frame_len)
        offset += 2
        buf[offset : offset + frame_len] = frame
        offset += frame_len

    # Remaining bytes in buf are already zero, providing the 0x0000 terminator when space remains.
    return bytes(buf)


def encode_vnic_batches(frames: Sequence[bytes]) -> list[bytes]:
    """Splits an arbitrary sequence of Ethernet frames into 0x8A00-byte Virtual NIC blocks."""
    if not frames:
        return []

    blocks: list[bytes] = []
    current: list[bytes] = []
    current_bytes = VNIC_HEADER_OVERHEAD

    for frame in frames:
        frame_len = len(frame)
        if not (VNIC_MIN_FRAME_LEN <= frame_len <= VNIC_MAX_FRAME_LEN):
            raise ValueError(
                f"Invalid Ethernet frame length {frame_len}; "
                f"expected {VNIC_MIN_FRAME_LEN}..{VNIC_MAX_FRAME_LEN}"
            )
        entry_len = 2 + frame_len
        if len(current) >= VNIC_MAX_FRAMES_PER_BLOCK or current_bytes + entry_len > VNIC_BLOCK_SIZE:
            blocks.append(encode_vnic_batch(current))
            current = []
            current_bytes = VNIC_HEADER_OVERHEAD
        current.append(frame)
        current_bytes += entry_len

    if current:
        blocks.append(encode_vnic_batch(current))
    return blocks


def is_vnic_block(block: bytes) -> bool:
    """Returns True if `block` has the 0x8A00 chunk_payload_len and 0xFF payload marker."""
    if len(block) < VNIC_HEADER_OVERHEAD:
        return False
    chunk_payload_len = struct.unpack_from("<I", block, 0x10)[0]
    return chunk_payload_len == VNIC_BLOCK_SIZE and block[BLOCK_HEADER_SIZE] == VNIC_PAYLOAD_OPCODE


def decode_vnic_batch(block: bytes, *, strict: bool = True) -> list[bytes]:
    """Extracts raw IEEE 802.3 Ethernet frames from a 0x8A00-byte Virtual NIC block."""
    if len(block) < VNIC_HEADER_OVERHEAD:
        if strict:
            raise ValueError(
                f"Virtual NIC block too short: expected >= {VNIC_HEADER_OVERHEAD}, got {len(block)}"
            )
        return []

    chunk_payload_len = struct.unpack_from("<I", block, 0x10)[0]
    if chunk_payload_len != VNIC_BLOCK_SIZE:
        if strict:
            raise ValueError(
                f"Invalid Virtual NIC chunk_payload_len: {chunk_payload_len:#x} != {VNIC_BLOCK_SIZE:#x}"
            )
        return []

    marker = block[BLOCK_HEADER_SIZE]
    if marker != VNIC_PAYLOAD_OPCODE:
        if strict:
            raise ValueError(f"Invalid Virtual NIC payload marker: {marker:#04x} != 0xff")
        return []

    limit = min(len(block), VNIC_BLOCK_SIZE)
    offset = VNIC_HEADER_OVERHEAD
    frames: list[bytes] = []

    while offset + 2 <= limit:
        frame_len = struct.unpack_from("<H", block, offset)[0]
        if frame_len == 0 or frame_len > VNIC_MAX_FRAME_LEN or offset + 2 + frame_len > limit:
            break
        frames.append(bytes(block[offset + 2 : offset + 2 + frame_len]))
        offset += 2 + frame_len

    return frames


def generate_vnic_mac(seed: bytes | None = None) -> bytes:
    """Generates a locally-administered unicast MAC address (02:0E:A0:xx:xx:xx)."""
    if seed is None:
        tail = os.urandom(3)
    else:
        import hashlib

        tail = hashlib.sha256(seed).digest()[:3]
    return bytes([0x02, 0x0E, 0xA0, tail[0], tail[1], tail[2]])


def matches_packet_filter(
    frame: bytes,
    local_mac: bytes,
    packet_filter: int = NDIS_PACKET_TYPE_DEFAULT,
) -> bool:
    """Checks whether an Ethernet frame passes the active NDIS-style packet filter."""
    if len(frame) < VNIC_MIN_FRAME_LEN:
        return False
    if packet_filter & NDIS_PACKET_TYPE_PROMISCUOUS:
        return True

    dst_mac = frame[:6]
    if dst_mac == _BROADCAST_MAC:
        return bool(packet_filter & NDIS_PACKET_TYPE_BROADCAST)
    if dst_mac[0] & 0x01:
        return bool(
            packet_filter & (NDIS_PACKET_TYPE_MULTICAST | NDIS_PACKET_TYPE_ALL_MULTICAST)
        )
    return bool((packet_filter & NDIS_PACKET_TYPE_DIRECTED) and dst_mac == local_mac)
