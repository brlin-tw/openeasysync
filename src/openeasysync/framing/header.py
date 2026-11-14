# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""20-byte Bulk Block Header packing, unpacking, and validation."""

import struct
from dataclasses import dataclass

from openeasysync.constants import (
    BLOCK_HEADER_SIZE,
    BULK_BLOCK_SIZE,
    MAX_PAYLOAD_PER_BLOCK,
)


@dataclass(slots=True)
class BulkBlockHeader:
    """Represents the 20-byte little-endian header of a 64 KB bulk block."""

    block_seq_id: int
    msg_seq_id: int
    chunk_index: int
    total_chunks: int
    chunk_payload_len: int

    def pack(self) -> bytes:
        """Packs header into 20 bytes little-endian."""
        if not (0 <= self.chunk_payload_len <= MAX_PAYLOAD_PER_BLOCK):
            raise ValueError(
                f"Invalid chunk_payload_len {self.chunk_payload_len}; max is {MAX_PAYLOAD_PER_BLOCK}"
            )
        return struct.pack(
            "<IIIII",
            self.block_seq_id,
            self.msg_seq_id,
            self.chunk_index,
            self.total_chunks,
            self.chunk_payload_len,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "BulkBlockHeader":
        """Unpacks 20-byte header."""
        if len(data) < BLOCK_HEADER_SIZE:
            raise ValueError(
                f"Header data too short: expected at least {BLOCK_HEADER_SIZE} bytes, got {len(data)}"
            )
        block_seq, msg_seq, chunk_idx, total_chunks, payload_len = struct.unpack_from(
            "<IIIII", data, 0
        )
        if payload_len > MAX_PAYLOAD_PER_BLOCK:
            raise ValueError(
                f"Decoded chunk_payload_len {payload_len} exceeds max {MAX_PAYLOAD_PER_BLOCK}"
            )
        return cls(
            block_seq_id=block_seq,
            msg_seq_id=msg_seq,
            chunk_index=chunk_idx,
            total_chunks=total_chunks,
            chunk_payload_len=payload_len,
        )


def build_bulk_block(
    header: BulkBlockHeader,
    payload_chunk: bytes,
    pad_to_block_size: bool = True,
) -> bytes:
    """Builds a full 64 KB bulk block with 20-byte header and payload."""
    hdr_bytes = header.pack()
    block = bytearray(hdr_bytes)
    block.extend(payload_chunk[: header.chunk_payload_len])
    if pad_to_block_size and len(block) < BULK_BLOCK_SIZE:
        block.extend(bytes(BULK_BLOCK_SIZE - len(block)))
    return bytes(block)


def parse_bulk_block(block_data: bytes) -> tuple[BulkBlockHeader, bytes]:
    """Parses a 64 KB block, returning (header, payload_chunk)."""
    if len(block_data) < BLOCK_HEADER_SIZE:
        raise ValueError(f"Block data too short: {len(block_data)} bytes")
    header = BulkBlockHeader.unpack(block_data[:BLOCK_HEADER_SIZE])
    payload = block_data[BLOCK_HEADER_SIZE : BLOCK_HEADER_SIZE + header.chunk_payload_len]
    return header, payload
