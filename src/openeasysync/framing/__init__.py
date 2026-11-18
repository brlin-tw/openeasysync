# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Framing, packet splitting, reassembly, and polling engine."""

from openeasysync.framing.header import BulkBlockHeader, build_bulk_block, parse_bulk_block
from openeasysync.framing.poller import LinkPoller
from openeasysync.framing.reassembler import PacketReassembler
from openeasysync.framing.splitter import PacketSplitter
from openeasysync.framing.vnic import (
    NDIS_PACKET_TYPE_ALL_MULTICAST,
    NDIS_PACKET_TYPE_BROADCAST,
    NDIS_PACKET_TYPE_DEFAULT,
    NDIS_PACKET_TYPE_DIRECTED,
    NDIS_PACKET_TYPE_MULTICAST,
    NDIS_PACKET_TYPE_PROMISCUOUS,
    VNIC_MAX_FRAME_LEN,
    VNIC_MAX_FRAMES_PER_BLOCK,
    VNIC_MIN_FRAME_LEN,
    VNIC_PAYLOAD_OPCODE,
    decode_vnic_batch,
    encode_vnic_batch,
    encode_vnic_batches,
    generate_vnic_mac,
    is_vnic_block,
    matches_packet_filter,
)

__all__ = [
    "BulkBlockHeader",
    "build_bulk_block",
    "parse_bulk_block",
    "PacketSplitter",
    "PacketReassembler",
    "LinkPoller",
    "VNIC_PAYLOAD_OPCODE",
    "VNIC_MAX_FRAMES_PER_BLOCK",
    "VNIC_MIN_FRAME_LEN",
    "VNIC_MAX_FRAME_LEN",
    "NDIS_PACKET_TYPE_DIRECTED",
    "NDIS_PACKET_TYPE_MULTICAST",
    "NDIS_PACKET_TYPE_ALL_MULTICAST",
    "NDIS_PACKET_TYPE_BROADCAST",
    "NDIS_PACKET_TYPE_PROMISCUOUS",
    "NDIS_PACKET_TYPE_DEFAULT",
    "encode_vnic_batch",
    "encode_vnic_batches",
    "decode_vnic_batch",
    "is_vnic_block",
    "generate_vnic_mac",
    "matches_packet_filter",
]
