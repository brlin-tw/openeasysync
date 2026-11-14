# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Encoders and decoders for all Server Replies (0xA1..0xAC)."""

import struct
from dataclasses import dataclass
from typing import Any

from openeasysync.constants import (
    DIR_ENTRY_SIZE,
    REPLY_DOWNLOAD_BATCH_DONE,
    REPLY_DOWNLOAD_BATCH_EMPTY,
    REPLY_DOWNLOAD_BATCH_STREAM,
    REPLY_DOWNLOAD_SINGLE,
    REPLY_DOWNLOAD_UNREADABLE,
    REPLY_ERROR_CODE,
    REPLY_FORMATTED_ERROR,
    REPLY_LISTING,
    REPLY_PRECHECK_BATCH,
    REPLY_PRECHECK_BATCH_EXT,
    REPLY_ROOTS,
    REPLY_STATUS_ACK,
    REPLY_UPLOAD_BATCH_ERROR,
    WIN32_FIND_DATA_SIZE,
)
from openeasysync.protocol.directory_entry import Entry552, Win32FindData


@dataclass
class BatchDownloadStreamRecord:
    rel_path: str
    find_data: Win32FindData
    data: bytes = b""


@dataclass
class BatchDownloadEmptyRecord:
    rel_path: str
    find_data: Win32FindData


@dataclass
class BatchPrecheckExtConflict:
    rel_path: str
    peer_find_data: Win32FindData
    src_find_data: Win32FindData


# Encoders


def encode_status_ack(status_code: int) -> bytes:
    """Opcode 0xA1: Command Status / ACK."""
    return bytes([REPLY_STATUS_ACK, status_code & 0xFF])


def encode_error_code(win32_error: int) -> bytes:
    """Opcode 0xA2: Win32 Error Code."""
    return bytes([REPLY_ERROR_CODE]) + struct.pack("<I", win32_error & 0xFFFFFFFF)


def encode_listing_reply(entries: list[Entry552], encoding: str = "utf-8") -> bytes:
    """Opcode 0xA3: Directory / Drive Listing Reply."""
    body = bytearray([REPLY_LISTING])
    body.extend(struct.pack("<I", len(entries)))
    for e in entries:
        body.extend(e.pack(encoding=encoding))
    return bytes(body)


def encode_roots_reply(drives: list[tuple[str, int]]) -> bytes:
    """Opcode 0xA4: Root Drive Listing Reply."""
    body = bytearray([REPLY_ROOTS])
    body.extend(struct.pack("<H", len(drives)))
    for letter, dtype in drives:
        char_b = letter[0].upper().encode("ascii")
        body.extend([char_b[0], dtype & 0xFF])
    return bytes(body)


def encode_download_single_reply(
    find_data: Win32FindData,
    file_data: bytes,
    encoding: str = "utf-8",
) -> bytes:
    """Opcode 0xA4: Single File Download Data Reply."""
    fd_b = find_data.pack(encoding=encoding)
    header = bytes([REPLY_DOWNLOAD_SINGLE]) + struct.pack("<I", len(file_data)) + fd_b
    return header + file_data


def encode_download_batch_stream(
    records: list[BatchDownloadStreamRecord],
    encoding: str = "utf-8",
) -> bytes:
    """Opcode 0xA5: Batch File Download Stream Reply."""
    body = bytearray([REPLY_DOWNLOAD_BATCH_STREAM])
    body.extend(struct.pack("<I", len(records)))
    for r in records:
        pb = r.rel_path.encode(encoding, errors="replace")
        fd_b = r.find_data.pack(encoding=encoding)
        rec_hdr = struct.pack("<II", len(pb), len(r.data)) + fd_b + pb + r.data
        body.extend(rec_hdr)
    return bytes(body)


def encode_download_batch_empty(
    records: list[BatchDownloadEmptyRecord],
    encoding: str = "utf-8",
) -> bytes:
    """Opcode 0xA6: Batch Zero-Length Files / Empty Directories."""
    body = bytearray([REPLY_DOWNLOAD_BATCH_EMPTY])
    body.extend(struct.pack("<I", len(records)))
    for r in records:
        pb = r.rel_path.encode(encoding, errors="replace")
        fd_b = r.find_data.pack(encoding=encoding)
        body.extend(struct.pack("<I", len(pb)) + fd_b + pb)
    return bytes(body)


def encode_download_batch_done() -> bytes:
    """Opcode 0xA7: Batch Download Complete."""
    return bytes([REPLY_DOWNLOAD_BATCH_DONE])


def encode_precheck_batch_reply(conflicts: list[str], encoding: str = "utf-8") -> bytes:
    """Opcode 0xA8: Batch Upload Pre-Check Reply."""
    body = bytearray([REPLY_PRECHECK_BATCH])
    body.extend(struct.pack("<I", len(conflicts)))
    for p in conflicts:
        pb = p.encode(encoding, errors="replace")
        body.extend(struct.pack("<I", len(pb)) + pb)
    return bytes(body)


def encode_upload_batch_error(win32_error: int, failed_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0xA9: Batch Upload Error Notification."""
    pb = failed_path.encode(encoding, errors="replace")
    return bytes([REPLY_UPLOAD_BATCH_ERROR]) + struct.pack("<II", win32_error, len(pb)) + pb


def encode_precheck_batch_ext_reply(
    conflicts: list[BatchPrecheckExtConflict],
    encoding: str = "utf-8",
) -> bytes:
    """Opcode 0xAA: Extended Batch Pre-Check Reply."""
    body = bytearray([REPLY_PRECHECK_BATCH_EXT])
    body.extend(struct.pack("<I", len(conflicts)))
    for c in conflicts:
        pb = c.rel_path.encode(encoding, errors="replace")
        p_fd = c.peer_find_data.pack(encoding=encoding)
        s_fd = c.src_find_data.pack(encoding=encoding)
        body.extend(struct.pack("<I", len(pb)) + p_fd + s_fd + pb)
    return bytes(body)


def encode_download_unreadable(failures: list[tuple[str, int]], encoding: str = "utf-8") -> bytes:
    """Opcode 0xAB: Batch Unreadable Files Notification."""
    body = bytearray([REPLY_DOWNLOAD_UNREADABLE])
    body.extend(struct.pack("<I", len(failures)))
    for path, err in failures:
        pb = path.encode(encoding, errors="replace")
        body.extend(struct.pack("<II", len(pb), err) + pb)
    return bytes(body)


def encode_formatted_errors(errors: list[tuple[int, str]], encoding: str = "utf-8") -> bytes:
    """Opcode 0xAC: Formatted Win32 Error Strings Reply."""
    body = bytearray([REPLY_FORMATTED_ERROR])
    body.extend(struct.pack("<I", len(errors)))
    for err_code, msg in errors:
        mb = msg.encode(encoding, errors="replace")
        body.extend(struct.pack("<II", err_code, len(mb)) + mb)
    return bytes(body)


# Decoders


def decode_reply(payload: bytes, encoding: str = "utf-8") -> dict[str, Any]:
    """Decodes a Server Reply message payload."""
    if not payload:
        raise ValueError("Empty reply payload")

    opcode = payload[0]

    if opcode == REPLY_STATUS_ACK:
        status_code = payload[1] if len(payload) > 1 else 0
        return {"opcode": REPLY_STATUS_ACK, "status": status_code}

    if opcode == REPLY_ERROR_CODE:
        win32_err = struct.unpack_from("<I", payload, 1)[0]
        return {"opcode": REPLY_ERROR_CODE, "win32_error": win32_err}

    if opcode == REPLY_LISTING:
        entry_count = struct.unpack_from("<I", payload, 1)[0]
        entries: list[Entry552] = []
        offset = 5
        for _ in range(entry_count):
            entry_bytes = payload[offset : offset + DIR_ENTRY_SIZE]
            entries.append(Entry552.unpack(entry_bytes, encoding=encoding))
            offset += DIR_ENTRY_SIZE
        return {"opcode": REPLY_LISTING, "entries": entries}

    if opcode in (REPLY_ROOTS, REPLY_DOWNLOAD_SINGLE):
        # Distinguish between Root Drive Listing (0xA4) and Single File Download (0xA4):
        # Root Drive Listing format from EasySuite.exe (0x004410a6 / 0x0040f093):
        # [0xA4][drive_count:u16_le][(letter:u8, type:u8)...] -> max 55 bytes (26 drives)
        if len(payload) >= 3:
            drive_count = struct.unpack_from("<H", payload, 1)[0]
            if drive_count <= 26 and len(payload) == 3 + drive_count * 2:
                roots: list[tuple[str, int]] = []
                offset = 3
                for _ in range(drive_count):
                    letter_char = chr(payload[offset])
                    dtype = payload[offset + 1] & 0x0F
                    roots.append((letter_char, dtype))
                    offset += 2
                return {"opcode": REPLY_ROOTS, "roots": roots}

        file_size = struct.unpack_from("<I", payload, 1)[0]
        fd = Win32FindData.unpack(payload[5 : 5 + WIN32_FIND_DATA_SIZE], encoding=encoding)
        data_start = 5 + WIN32_FIND_DATA_SIZE
        file_data = payload[data_start : data_start + file_size]
        return {
            "opcode": REPLY_DOWNLOAD_SINGLE,
            "file_size": file_size,
            "find_data": fd,
            "file_data": file_data,
        }

    if opcode == REPLY_DOWNLOAD_BATCH_STREAM:
        count = struct.unpack_from("<I", payload, 1)[0]
        records: list[BatchDownloadStreamRecord] = []
        offset = 5
        for _ in range(count):
            p_len, d_len = struct.unpack_from("<II", payload, offset)
            fd = Win32FindData.unpack(
                payload[offset + 8 : offset + 8 + WIN32_FIND_DATA_SIZE], encoding=encoding
            )
            p_start = offset + 8 + WIN32_FIND_DATA_SIZE
            rel_p = payload[p_start : p_start + p_len].decode(encoding, errors="replace")
            rec_data = payload[p_start + p_len : p_start + p_len + d_len]
            records.append(BatchDownloadStreamRecord(rel_path=rel_p, find_data=fd, data=rec_data))
            offset = p_start + p_len + d_len
        return {"opcode": REPLY_DOWNLOAD_BATCH_STREAM, "records": records}

    if opcode == REPLY_DOWNLOAD_BATCH_EMPTY:
        count = struct.unpack_from("<I", payload, 1)[0]
        empty_records: list[BatchDownloadEmptyRecord] = []
        offset = 5
        for _ in range(count):
            p_len = struct.unpack_from("<I", payload, offset)[0]
            fd = Win32FindData.unpack(
                payload[offset + 4 : offset + 4 + WIN32_FIND_DATA_SIZE], encoding=encoding
            )
            p_start = offset + 4 + WIN32_FIND_DATA_SIZE
            rel_p = payload[p_start : p_start + p_len].decode(encoding, errors="replace")
            empty_records.append(BatchDownloadEmptyRecord(rel_path=rel_p, find_data=fd))
            offset = p_start + p_len
        return {"opcode": REPLY_DOWNLOAD_BATCH_EMPTY, "records": empty_records}

    if opcode == REPLY_DOWNLOAD_BATCH_DONE:
        return {"opcode": REPLY_DOWNLOAD_BATCH_DONE}

    if opcode == REPLY_PRECHECK_BATCH:
        count = struct.unpack_from("<I", payload, 1)[0]
        conflicts: list[str] = []
        offset = 5
        for _ in range(count):
            p_len = struct.unpack_from("<I", payload, offset)[0]
            p_str = payload[offset + 4 : offset + 4 + p_len].decode(encoding, errors="replace")
            conflicts.append(p_str)
            offset += 4 + p_len
        return {"opcode": REPLY_PRECHECK_BATCH, "conflicts": conflicts}

    if opcode == REPLY_UPLOAD_BATCH_ERROR:
        win32_err, p_len = struct.unpack_from("<II", payload, 1)
        p_str = payload[9 : 9 + p_len].decode(encoding, errors="replace")
        return {"opcode": REPLY_UPLOAD_BATCH_ERROR, "win32_error": win32_err, "failed_path": p_str}

    if opcode == REPLY_PRECHECK_BATCH_EXT:
        count = struct.unpack_from("<I", payload, 1)[0]
        ext_conflicts: list[BatchPrecheckExtConflict] = []
        offset = 5
        for _ in range(count):
            p_len = struct.unpack_from("<I", payload, offset)[0]
            peer_fd = Win32FindData.unpack(
                payload[offset + 4 : offset + 4 + WIN32_FIND_DATA_SIZE], encoding=encoding
            )
            src_start = offset + 4 + WIN32_FIND_DATA_SIZE
            src_fd = Win32FindData.unpack(
                payload[src_start : src_start + WIN32_FIND_DATA_SIZE], encoding=encoding
            )
            p_start = src_start + WIN32_FIND_DATA_SIZE
            p_str = payload[p_start : p_start + p_len].decode(encoding, errors="replace")
            ext_conflicts.append(
                BatchPrecheckExtConflict(
                    rel_path=p_str, peer_find_data=peer_fd, src_find_data=src_fd
                )
            )
            offset = p_start + p_len
        return {"opcode": REPLY_PRECHECK_BATCH_EXT, "conflicts": ext_conflicts}

    if opcode == REPLY_DOWNLOAD_UNREADABLE:
        count = struct.unpack_from("<I", payload, 1)[0]
        failures: list[tuple[str, int]] = []
        offset = 5
        for _ in range(count):
            p_len, err = struct.unpack_from("<II", payload, offset)
            p_str = payload[offset + 8 : offset + 8 + p_len].decode(encoding, errors="replace")
            failures.append((p_str, err))
            offset += 8 + p_len
        return {"opcode": REPLY_DOWNLOAD_UNREADABLE, "failures": failures}

    if opcode == REPLY_FORMATTED_ERROR:
        count = struct.unpack_from("<I", payload, 1)[0]
        errors: list[tuple[int, str]] = []
        offset = 5
        for _ in range(count):
            err_code, msg_len = struct.unpack_from("<II", payload, offset)
            msg = payload[offset + 8 : offset + 8 + msg_len].decode(encoding, errors="replace")
            errors.append((err_code, msg))
            offset += 8 + msg_len
        return {"opcode": REPLY_FORMATTED_ERROR, "errors": errors}

    return {"opcode": opcode, "raw": payload}
