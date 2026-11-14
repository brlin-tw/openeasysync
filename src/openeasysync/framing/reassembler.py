# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reassembles incoming 64 KB bulk blocks into logical messages or streams."""

from collections.abc import Callable

from openeasysync.framing.header import parse_bulk_block


class PacketReassembler:
    """Reassembles 64 KB bulk blocks into logical wire messages."""

    def __init__(self):
        self._current_msg_seq_id: int | None = None
        self._expected_chunks: int = 0
        self._received_chunks: int = 0
        self._buffer: bytearray = bytearray()
        self._stream_consumer: Callable[[bytes, int, int], None] | None = None

    def set_stream_consumer(self, consumer: Callable[[bytes, int, int], None] | None) -> None:
        """Sets an optional streaming consumer callback: consumer(chunk_payload, chunk_idx, total_chunks)."""
        self._stream_consumer = consumer

    def reset(self) -> None:
        """Resets reassembly state."""
        self._current_msg_seq_id = None
        self._expected_chunks = 0
        self._received_chunks = 0
        self._buffer.clear()
        self._stream_consumer = None

    def feed_block(self, block_data: bytes) -> bytes | None:
        """Feeds a 64 KB block.

        Returns:
            The complete logical message (bytes) if this block completes the message,
            or None if more chunks are awaited (or if streaming mode consumed it).
        """
        header, payload = parse_bulk_block(block_data)

        # Single-block fast path
        if header.total_chunks == 1 and header.chunk_index == 0:
            self.reset()
            if self._stream_consumer is not None:
                self._stream_consumer(payload, 0, 1)
                return None
            return payload

        # Multi-block message handling
        if header.chunk_index == 0:
            self._current_msg_seq_id = header.msg_seq_id
            self._expected_chunks = header.total_chunks
            self._received_chunks = 1
            if self._stream_consumer is not None:
                self._stream_consumer(payload, 0, header.total_chunks)
            else:
                self._buffer = bytearray(payload)
            return None

        # Subsequent chunk validation
        if header.msg_seq_id != self._current_msg_seq_id:
            # Sequence mismatch or lost block; reset
            self.reset()
            raise ValueError(
                f"Block msg_seq_id mismatch: expected {self._current_msg_seq_id}, got {header.msg_seq_id}"
            )

        if header.chunk_index != self._received_chunks:
            # Chunk order mismatch
            self.reset()
            raise ValueError(
                f"Block chunk_index out of order: expected {self._received_chunks}, got {header.chunk_index}"
            )

        self._received_chunks += 1

        if self._stream_consumer is not None:
            self._stream_consumer(payload, header.chunk_index, header.total_chunks)
        else:
            self._buffer.extend(payload)

        # Check if complete
        if self._received_chunks == self._expected_chunks:
            completed_msg = bytes(self._buffer) if self._stream_consumer is None else None
            self.reset()
            return completed_msg

        return None
