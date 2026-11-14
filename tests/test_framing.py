# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for 20-byte Bulk Block Header, PacketSplitter, and PacketReassembler."""

from unittest.mock import MagicMock

import pytest

from openeasysync.constants import (
    ACTION_READY_WRITE_BURST,
    BULK_BLOCK_SIZE,
    MAX_PAYLOAD_PER_BLOCK,
)
from openeasysync.framing.header import BulkBlockHeader, build_bulk_block, parse_bulk_block
from openeasysync.framing.poller import LinkPoller
from openeasysync.framing.reassembler import PacketReassembler
from openeasysync.framing.splitter import PacketSplitter


def test_bulk_block_header_pack_unpack():
    """Verify 20-byte Bulk Block Header packs and unpacks accurately."""
    hdr = BulkBlockHeader(
        block_seq_id=42,
        msg_seq_id=7,
        chunk_index=2,
        total_chunks=5,
        chunk_payload_len=1024,
    )
    raw = hdr.pack()
    assert len(raw) == 20

    unpacked = BulkBlockHeader.unpack(raw)
    assert unpacked.block_seq_id == 42
    assert unpacked.msg_seq_id == 7
    assert unpacked.chunk_index == 2
    assert unpacked.total_chunks == 5
    assert unpacked.chunk_payload_len == 1024


def test_build_and_parse_bulk_block():
    """Verify full 64 KB block construction and payload recovery."""
    hdr = BulkBlockHeader(1, 1, 0, 1, 10)
    payload = b"0123456789"
    block = build_bulk_block(hdr, payload)

    assert len(block) == BULK_BLOCK_SIZE
    parsed_hdr, parsed_payload = parse_bulk_block(block)
    assert parsed_hdr == hdr
    assert parsed_payload == payload


def test_packet_splitter_various_sizes():
    """Verify PacketSplitter splits payloads across boundary conditions."""
    splitter = PacketSplitter()

    # 1. Empty message
    blocks = splitter.split_message(b"")
    assert len(blocks) == 1
    hdr, p = parse_bulk_block(blocks[0])
    assert hdr.chunk_payload_len == 0
    assert hdr.total_chunks == 1

    # 2. Exactly MAX_PAYLOAD_PER_BLOCK (65516 bytes)
    exact_payload = b"X" * MAX_PAYLOAD_PER_BLOCK
    blocks = splitter.split_message(exact_payload)
    assert len(blocks) == 1
    hdr, p = parse_bulk_block(blocks[0])
    assert hdr.chunk_payload_len == MAX_PAYLOAD_PER_BLOCK
    assert hdr.total_chunks == 1
    assert p == exact_payload

    # 3. MAX_PAYLOAD_PER_BLOCK + 1 (boundary crossing to 2 blocks)
    boundary_payload = b"Y" * (MAX_PAYLOAD_PER_BLOCK + 1)
    blocks = splitter.split_message(boundary_payload)
    assert len(blocks) == 2
    hdr0, p0 = parse_bulk_block(blocks[0])
    hdr1, p1 = parse_bulk_block(blocks[1])

    assert (
        hdr0.chunk_index == 0
        and hdr0.total_chunks == 2
        and hdr0.chunk_payload_len == MAX_PAYLOAD_PER_BLOCK
    )
    assert hdr1.chunk_index == 1 and hdr1.total_chunks == 2 and hdr1.chunk_payload_len == 1
    assert p0 + p1 == boundary_payload

    # 4. Large message (200,000 bytes -> 4 chunks)
    large_payload = b"ABCDEFGHIJ" * 20000
    blocks = splitter.split_message(large_payload)
    assert len(blocks) == 4
    reassembled = bytearray()
    for b in blocks:
        _, p = parse_bulk_block(b)
        reassembled.extend(p)
    assert bytes(reassembled) == large_payload


def test_packet_reassembler_in_memory():
    """Verify PacketReassembler accumulates multi-block payloads correctly."""
    splitter = PacketSplitter()
    reassembler = PacketReassembler()

    original = b"TEST_PAYLOAD_" * 15000  # ~195 KB
    blocks = splitter.split_message(original)

    completed = None
    for b in blocks:
        res = reassembler.feed_block(b)
        if res is not None:
            completed = res

    assert completed == original


def test_packet_reassembler_streaming():
    """Verify streaming consumer receives chunks without accumulating in RAM."""
    splitter = PacketSplitter()
    reassembler = PacketReassembler()

    received_chunks: list[bytes] = []

    def stream_cb(chunk: bytes, idx: int, total: int):
        received_chunks.append(chunk)

    reassembler.set_stream_consumer(stream_cb)

    original = b"STREAMING_TEST_" * 10000
    blocks = splitter.split_message(original)

    for b in blocks:
        res = reassembler.feed_block(b)
        assert res is None  # None because consumed by stream callback

    assert b"".join(received_chunks) == original


def test_packet_reassembler_sequence_mismatch():
    """Verify sequence ID or chunk order mismatches raise ValueError."""
    reassembler = PacketReassembler()

    # Block with total_chunks=2, chunk_index=0, msg_seq=1
    hdr0 = BulkBlockHeader(1, 1, 0, 2, 100)
    b0 = build_bulk_block(hdr0, b"A" * 100)
    reassembler.feed_block(b0)

    # Corrupt block with msg_seq=2 (different message sequence)
    hdr_bad = BulkBlockHeader(2, 2, 1, 2, 100)
    b_bad = build_bulk_block(hdr_bad, b"B" * 100)

    with pytest.raises(ValueError, match="msg_seq_id mismatch"):
        reassembler.feed_block(b_bad)


def test_link_poller_write_failure_requeues_block():
    """Verify transient bulk write failures requeue block without dropping or flushing pipes."""
    mock_transport = MagicMock()
    # First write raises transient OSError (0x0985 Peer Not Reading), second succeeds
    mock_transport.write_bulk_block.side_effect = [
        OSError("Bulk write failed: status=1, OTi Vendor: Peer Not Reading / Waiting for Peer"),
        None,
    ]
    mock_transport.poll_link.return_value = (ACTION_READY_WRITE_BURST, 0, 0, 0)

    poller = LinkPoller(transport=mock_transport, poll_interval_s=0.001)
    test_block = b"X" * BULK_BLOCK_SIZE
    poller.enqueue_tx(test_block)
    assert poller.tx_queue.qsize() == 1

    # First poll triggers State 1, write_bulk_block fails, block must be preserved
    poller._state_poll()
    assert poller.tx_queue.qsize() == 1
    # Verify pipes were NOT flushed
    mock_transport.flush_pipes.assert_not_called()

    # Second poll triggers State 1, write_bulk_block succeeds, queue emptied
    poller._state_poll()
    assert poller.tx_queue.qsize() == 0
    assert mock_transport.write_bulk_block.call_count == 2
