# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Encoders and decoders for all Server Replies (0xA1..0xAC)."""

import struct
import sys
from dataclasses import dataclass
from typing import Any

from openeasysync.constants import (
    DIR_ENTRY_SIZE,
    REPLY_CHUNK_ACK,
    REPLY_DOWNLOAD_BATCH_DONE,
    REPLY_DOWNLOAD_BATCH_EMPTY,
    REPLY_DOWNLOAD_BATCH_STREAM,
    REPLY_DOWNLOAD_CHUNK,
    REPLY_DOWNLOAD_SINGLE,
    REPLY_DOWNLOAD_UNREADABLE,
    REPLY_FORMATTED_ERROR,
    REPLY_LISTING,
    REPLY_PRECHECK_BATCH,
    REPLY_PRECHECK_BATCH_EXT,
    REPLY_ROOTS,
    REPLY_STATUS_ACK,
    REPLY_UPLOAD_BATCH_ERROR,
    STATUS_ERROR,
    STATUS_OK,
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


WIN32_ERROR_MESSAGES: dict[int, str] = {
    0: "The operation completed successfully.",
    1: "Incorrect function.",
    2: "The system cannot find the file specified.",
    3: "The system cannot find the path specified.",
    4: "The system cannot open the file.",
    5: "Access is denied.",
    6: "The handle is invalid.",
    15: "The system cannot find the drive specified.",
    19: "The media is write protected.",
    21: "The device is not ready.",
    32: "The process cannot access the file because it is being used by another process.",
    33: "The process cannot access the file because another process has locked a portion of the file.",
    53: "The network path was not found.",
    67: "The network name cannot be found.",
    80: "The file exists.",
    112: "There is not enough space on the disk.",
    183: "Cannot create a file when that file already exists.",
    206: "The filename or extension is too long.",
    267: "The directory name is invalid.",
}


def format_win32_error(error_code: int) -> str:
    """Returns a human-readable description for a Win32 error code."""
    if sys.platform == "win32":
        try:
            import ctypes

            msg = ctypes.FormatError(error_code).strip()
            if msg:
                return msg
        except Exception:
            pass
    return WIN32_ERROR_MESSAGES.get(error_code, f"Win32 error {error_code}")


# Encoders


def encode_status_ack(
    status: int = STATUS_OK,
    req_opcode: int = 0,
    req_id: int = 0,
    win32_error: int = 0,
    file_size: int = 0,
    flags: int = 0,
    file_data: bytes = b"",
) -> bytes:
    """Opcode 0xA1: Command Status / ACK (24-byte structured response header + optional inline data).

    Framing:
    [0xA1][req_opcode:u8][status:u8][req_id:u32_le][win32_error:u32_le]
    [file_size_low:u32_le][file_size_high:u32_le][flags:u8][data_len:u32_le][file_data:bytes]
    Total header size: 24 bytes (0x18). Total packet size: 24 + len(file_data) bytes.
    """
    data_len = len(file_data)
    effective_file_size = file_size if file_size else data_len
    file_size_low = effective_file_size & 0xFFFFFFFF
    file_size_high = (effective_file_size >> 32) & 0xFFFFFFFF
    effective_flags = flags if flags != 0 else (2 if data_len > 0 else 0)

    header = struct.pack(
        "<BBBIIIIBI",
        REPLY_STATUS_ACK,
        req_opcode & 0xFF,
        status & 0xFF,
        req_id & 0xFFFFFFFF,
        win32_error & 0xFFFFFFFF,
        file_size_low,
        file_size_high,
        effective_flags & 0xFF,
        data_len & 0xFFFFFFFF,
    )
    return header + file_data


def encode_chunk_ack(item_id: int, chunk_index: int) -> bytes:
    """Opcode 0xA5: Upload File Data Chunk ACK (9 bytes).

    Framing: [0xA5][item_id:u32_le][chunk_index:u32_le] (Section 4.2.6).
    """
    return struct.pack("<BII", REPLY_CHUNK_ACK, item_id & 0xFFFFFFFF, chunk_index & 0xFFFFFFFF)


def encode_error_code(win32_error: int, req_opcode: int = 0, req_id: int = 0) -> bytes:
    """Encodes an error reply using the 24-byte 0xA1 structured response.

    Note: Opcode 0xA2 is reserved strictly for Directory Listings (Entry552).
    Emitting a short error reply with opcode 0xA2 is a protocol violation that
    causes peer memory corruption. Error replies MUST use 0xA1 with status=1.
    """
    return encode_status_ack(
        status=STATUS_ERROR,
        req_opcode=req_opcode,
        req_id=req_id,
        win32_error=win32_error,
    )


def encode_listing_reply(entries: list[Entry552], encoding: str = "utf-16le") -> bytes:
    """Opcode 0xA2: Directory Listing Reply.

    Framing: [0xA2][entry_count:uint16_le][entries: Entry552[entry_count]]
    Total length = 3 + 552 * entry_count bytes.
    """
    body = bytearray([REPLY_LISTING])
    body.extend(struct.pack("<H", len(entries)))
    for e in entries:
        body.extend(e.pack(encoding=encoding))
    return bytes(body)


def encode_download_chunk(
    item_id: int,
    chunk_index: int,
    total_chunks: int,
    chunk_data: bytes,
) -> bytes:
    """Opcode 0xA3: File Download Data Chunk."""
    header = bytes([REPLY_DOWNLOAD_CHUNK]) + struct.pack(
        "<IIII", item_id, chunk_index, total_chunks, len(chunk_data)
    )
    return header + chunk_data


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


def encode_download_batch_done(req_id: int = 0, status: int = 0) -> bytes:
    """Opcode 0xA7: Batch Download Complete ACK (7 bytes total)."""
    return bytes([REPLY_DOWNLOAD_BATCH_DONE]) + struct.pack("<IH", req_id, status)


def encode_precheck_batch_reply(conflicts: list[str], encoding: str = "utf-8") -> bytes:
    """Opcode 0xA8: Batch Upload Pre-Check Reply."""
    body = bytearray([REPLY_PRECHECK_BATCH])
    body.extend(struct.pack("<I", len(conflicts)))
    for p in conflicts:
        pb = p.encode(encoding, errors="replace")
        body.extend(struct.pack("<I", len(pb)) + pb)
    return bytes(body)


def encode_upload_batch_error(
    win32_error: int, failed_path: str = "", encoding: str = "utf-8"
) -> bytes:
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
        if len(payload) >= 24:
            req_op, status, req_id = struct.unpack_from("<BBI", payload, 1)
            win32_err = struct.unpack_from("<I", payload, 7)[0]
            file_size = struct.unpack_from("<Q", payload, 0x0B)[0]
            flags = payload[0x13]
            data_len = struct.unpack_from("<I", payload, 0x14)[0]
            file_data = payload[0x18 : 0x18 + data_len]
            return {
                "opcode": REPLY_STATUS_ACK,
                "req_opcode": req_op,
                "status": status,
                "req_id": req_id,
                "win32_error": win32_err,
                "file_size": file_size,
                "flags": flags,
                "data_len": data_len,
                "file_data": file_data,
            }
        req_op = payload[1] if len(payload) > 1 else 0
        status_code = payload[2] if len(payload) > 2 else 0
        req_id = struct.unpack_from("<H", payload, 3)[0] if len(payload) >= 5 else 0
        win32_err = struct.unpack_from("<I", payload, 7)[0] if len(payload) >= 11 else 0
        return {
            "opcode": REPLY_STATUS_ACK,
            "req_opcode": req_op,
            "status": status_code,
            "req_id": req_id,
            "win32_error": win32_err,
            "file_size": 0,
            "flags": 0,
            "data_len": 0,
            "file_data": b"",
        }

    if opcode == REPLY_LISTING:
        # Opcode 0xA2: Directory Listing Reply with 16-bit entry_count
        if len(payload) >= 3:
            entry_count = struct.unpack_from("<H", payload, 1)[0]
            entries: list[Entry552] = []
            offset = 3
            for _ in range(entry_count):
                if offset + DIR_ENTRY_SIZE > len(payload):
                    break
                entry_bytes = payload[offset : offset + DIR_ENTRY_SIZE]
                entries.append(Entry552.unpack(entry_bytes, encoding=encoding))
                offset += DIR_ENTRY_SIZE
            return {"opcode": REPLY_LISTING, "entries": entries}
        return {"opcode": REPLY_LISTING, "entries": []}

    if opcode == REPLY_DOWNLOAD_CHUNK:
        # Opcode 0xA3: File Download Data Chunk
        if len(payload) >= 17:
            item_id, chunk_index, total_chunks, data_len = struct.unpack_from("<IIII", payload, 1)
            chunk_data = payload[17 : 17 + data_len]
            return {
                "opcode": REPLY_DOWNLOAD_CHUNK,
                "item_id": item_id,
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "data": chunk_data,
            }
        return {"opcode": opcode, "raw": payload}

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

    if opcode in (REPLY_CHUNK_ACK, REPLY_DOWNLOAD_BATCH_STREAM):
        if len(payload) == 9:
            item_id, chunk_index = struct.unpack_from("<II", payload, 1)
            return {
                "opcode": REPLY_CHUNK_ACK,
                "item_id": item_id,
                "chunk_index": chunk_index,
            }
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
        if len(payload) >= 7:
            req_id, status_val = struct.unpack_from("<IH", payload, 1)
            return {"opcode": REPLY_DOWNLOAD_BATCH_DONE, "req_id": req_id, "status": status_val}
        return {"opcode": REPLY_DOWNLOAD_BATCH_DONE, "req_id": 0, "status": 0}

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
