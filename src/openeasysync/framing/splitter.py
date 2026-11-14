# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Splits logical wire messages into one or more 64 KB bulk blocks."""

import math
from collections.abc import Iterator

from openeasysync.constants import MAX_PAYLOAD_PER_BLOCK
from openeasysync.framing.header import BulkBlockHeader, build_bulk_block


class PacketSplitter:
    """Manages sequence numbers and splits logical messages into 64 KB blocks."""

    def __init__(self, start_block_seq: int = 1, start_msg_seq: int = 1):
        self._block_seq_id = start_block_seq
        self._msg_seq_id = start_msg_seq

    def next_msg_seq_id(self) -> int:
        seq = self._msg_seq_id
        self._msg_seq_id = (self._msg_seq_id + 1) & 0xFFFFFFFF
        return seq

    def next_block_seq_id(self) -> int:
        seq = self._block_seq_id
        self._block_seq_id = (self._block_seq_id + 1) & 0xFFFFFFFF
        return seq

    def split_message(self, message: bytes) -> list[bytes]:
        """Splits an entire in-memory logical message into 64 KB blocks."""
        return list(self.iter_split_message(message))

    def iter_split_message(self, message: bytes) -> Iterator[bytes]:
        """Iteratively yields 64 KB blocks for a logical message."""
        msg_len = len(message)
        msg_seq = self.next_msg_seq_id()

        if msg_len <= MAX_PAYLOAD_PER_BLOCK:
            header = BulkBlockHeader(
                block_seq_id=self.next_block_seq_id(),
                msg_seq_id=msg_seq,
                chunk_index=0,
                total_chunks=1,
                chunk_payload_len=msg_len,
            )
            yield build_bulk_block(header, message)
            return

        total_chunks = math.ceil(msg_len / MAX_PAYLOAD_PER_BLOCK)
        for chunk_idx in range(total_chunks):
            offset = chunk_idx * MAX_PAYLOAD_PER_BLOCK
            chunk_len = min(MAX_PAYLOAD_PER_BLOCK, msg_len - offset)
            chunk_payload = message[offset : offset + chunk_len]

            header = BulkBlockHeader(
                block_seq_id=self.next_block_seq_id(),
                msg_seq_id=msg_seq,
                chunk_index=chunk_idx,
                total_chunks=total_chunks,
                chunk_payload_len=chunk_len,
            )
            yield build_bulk_block(header, chunk_payload)
