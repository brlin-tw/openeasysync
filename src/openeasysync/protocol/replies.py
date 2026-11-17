# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Encoders and decoders for all Server Replies (0xA1..0xAC)."""

import struct
import sys
from typing import Any

from openeasysync.constants import (
    DIR_ENTRY_SIZE,
    REPLY_DOWNLOAD_BATCH_DONE,
    REPLY_DOWNLOAD_BATCH_EMPTY,
    REPLY_DOWNLOAD_CHUNK,
    REPLY_DOWNLOAD_UNREADABLE,
    REPLY_FORMATTED_ERROR,
    REPLY_LISTING,
    REPLY_PRECHECK_BATCH,
    REPLY_PRECHECK_BATCH_EXT,
    REPLY_ROOTS,
    REPLY_STATUS_ACK,
    REPLY_UPLOAD_BATCH_ERROR,
    REPLY_UPLOAD_ERROR,
    STATUS_ERROR,
    STATUS_OK,
)
from openeasysync.protocol.directory_entry import Entry552

_ERRSTR_MARKER = b"ErrStr"

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
    """Opcode 0xA1: Command Status / ACK (24-byte structured response header + optional inline data)."""
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


def encode_upload_error(item_id: int, win32_error: int) -> bytes:
    """Opcode 0xA5: Single-File Upload Error Notification (9 bytes)."""
    return struct.pack("<BII", REPLY_UPLOAD_ERROR, item_id & 0xFFFFFFFF, win32_error & 0xFFFFFFFF)


def encode_error_code(win32_error: int, req_opcode: int = 0, req_id: int = 0) -> bytes:
    """Encodes an error reply using the 24-byte 0xA1 structured response."""
    return encode_status_ack(
        status=STATUS_ERROR,
        req_opcode=req_opcode,
        req_id=req_id,
        win32_error=win32_error,
    )


def encode_listing_reply(entries: list[Entry552], encoding: str = "utf-16le") -> bytes:
    """Opcode 0xA2: Directory Listing Reply."""
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
        "<IIII", item_id & 0xFFFFFFFF, chunk_index, total_chunks, len(chunk_data)
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


def _encode_req_item_ids(opcode: int, req_id: int, item_ids: list[int]) -> bytes:
    """Packs [opcode:u8][req_id:u32_le][count:u16_le][item_ids:u32_le[count]] for 0xA6/0xAB."""
    header = struct.pack("<BIH", opcode, req_id & 0xFFFFFFFF, len(item_ids) & 0xFFFF)
    if not item_ids:
        return header
    return header + struct.pack(f"<{len(item_ids)}I", *(i & 0xFFFFFFFF for i in item_ids))


def encode_download_batch_empty(item_ids: list[int], req_id: int = 1) -> bytes:
    """Opcode 0xA6: Batch Zero-Length Files / Empty Directories."""
    return _encode_req_item_ids(REPLY_DOWNLOAD_BATCH_EMPTY, req_id, item_ids)


def encode_download_batch_done(req_id: int = 0, status: int = 0) -> bytes:
    """Opcode 0xA7: Batch Download Complete ACK (7 bytes total)."""
    return bytes([REPLY_DOWNLOAD_BATCH_DONE]) + struct.pack("<IH", req_id & 0xFFFFFFFF, status & 0xFFFF)


def encode_precheck_batch_reply(
    conflict_item_ids: list[int],
    req_id: int = 1,
    insufficient_space: bool = False,
    has_conflicts: bool | None = None,
    flag0: int = 0,
    flag3: int = 0,
) -> bytes:
    """Opcode 0xA8: Batch Upload Pre-Check Reply.

    Wire layout:
    [0xA8][flag0:u8][insufficient_space:u8][has_conflicts:u8][flag3:u8]
    [req_id:u32_le][conflict_count:u32_le][conflict_item_ids:u32_le[conflict_count]]
    """
    conflicts_flag = 1 if (has_conflicts if has_conflicts is not None else bool(conflict_item_ids)) else 0
    header = struct.pack(
        "<BBBBBII",
        REPLY_PRECHECK_BATCH,
        flag0 & 0xFF,
        1 if insufficient_space else 0,
        conflicts_flag,
        flag3 & 0xFF,
        req_id & 0xFFFFFFFF,
        len(conflict_item_ids),
    )
    if not conflict_item_ids:
        return header
    return header + struct.pack(
        f"<{len(conflict_item_ids)}I", *(i & 0xFFFFFFFF for i in conflict_item_ids)
    )


def encode_upload_batch_error(
    item_id: int,
    win32_error: int,
    status: int = 1,
    err_str: str | None = None,
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0xA9: Batch Upload Error Notification.

    Wire layout: [0xA9][item_id:u32_le][win32_error:u32_le][status:u32_le]
    (appends "ErrStr" + err_str_len:u32_le + err_str:wchar_t[] in 0x10 mode).
    """
    pkt = struct.pack(
        "<BIII",
        REPLY_UPLOAD_BATCH_ERROR,
        item_id & 0xFFFFFFFF,
        win32_error & 0xFFFFFFFF,
        status & 0xFFFFFFFF,
    )
    if err_str is not None:
        err_b = err_str.encode(encoding, errors="replace")
        pkt += _ERRSTR_MARKER + struct.pack("<I", len(err_b)) + err_b
    return pkt


def encode_precheck_batch_ext_reply(
    conflict_item_ids: list[int],
    req_id: int = 1,
    extra_pairs: list[tuple[int, int]] | None = None,
    flag0: int = 0,
    flag1: int = 0,
    flag2: int | None = None,
    flag3: int = 0,
) -> bytes:
    """Opcode 0xAA: Extended Batch Pre-Check Reply.

    Wire layout:
    [0xAA][flag0:u8][flag1:u8][flag2:u8][flag3:u8][req_id:u32_le]
    [conflict_count:u32_le][extra_count:u32_le]
    [conflict_item_ids:u32_le[conflict_count]][extra_pairs: (item_id:u32_le, val:u32_le)[extra_count]]
    """
    pairs = extra_pairs or []
    eff_flag2 = (1 if conflict_item_ids else 0) if flag2 is None else flag2
    body = bytearray(
        struct.pack(
            "<BBBBBIII",
            REPLY_PRECHECK_BATCH_EXT,
            flag0 & 0xFF,
            flag1 & 0xFF,
            eff_flag2 & 0xFF,
            flag3 & 0xFF,
            req_id & 0xFFFFFFFF,
            len(conflict_item_ids),
            len(pairs),
        )
    )
    for item_id in conflict_item_ids:
        body.extend(struct.pack("<I", item_id & 0xFFFFFFFF))
    for item_id, val in pairs:
        body.extend(struct.pack("<II", item_id & 0xFFFFFFFF, val & 0xFFFFFFFF))
    return bytes(body)


def encode_download_unreadable(item_ids: list[int], req_id: int = 1) -> bytes:
    """Opcode 0xAB: Batch Unreadable Files Notification."""
    return _encode_req_item_ids(REPLY_DOWNLOAD_UNREADABLE, req_id, item_ids)


def encode_formatted_errors(errors: list[tuple[int, str]], encoding: str = "utf-16le") -> bytes:
    """Opcode 0xAC: Formatted Win32 Error Strings Reply (UTF-16LE)."""
    body = bytearray([REPLY_FORMATTED_ERROR])
    body.extend(struct.pack("<I", len(errors)))
    for err_code, msg in errors:
        mb = msg.encode(encoding, errors="replace")
        body.extend(struct.pack("<II", err_code & 0xFFFFFFFF, len(mb)) + mb)
    return bytes(body)


# Decoders


def _decode_req_item_ids(payload: bytes) -> tuple[int, list[int]]:
    req_id, count = struct.unpack_from("<IH", payload, 1)
    item_ids = [
        struct.unpack_from("<I", payload, 7 + i * 4)[0]
        for i in range(count)
        if 7 + (i + 1) * 4 <= len(payload)
    ]
    return req_id, item_ids


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
        if len(payload) >= 3:
            entry_count = struct.unpack_from("<H", payload, 1)[0]
            entries: list[Entry552] = []
            offset = 3
            for _ in range(entry_count):
                if offset + DIR_ENTRY_SIZE > len(payload):
                    break
                entries.append(Entry552.unpack(payload[offset : offset + DIR_ENTRY_SIZE], encoding=encoding))
                offset += DIR_ENTRY_SIZE
            return {"opcode": REPLY_LISTING, "entries": entries}
        return {"opcode": REPLY_LISTING, "entries": []}

    if opcode == REPLY_DOWNLOAD_CHUNK:
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

    if opcode == REPLY_ROOTS:
        drive_count = struct.unpack_from("<H", payload, 1)[0]
        roots: list[tuple[str, int]] = []
        offset = 3
        for _ in range(drive_count):
            if offset + 2 > len(payload):
                break
            letter_char = chr(payload[offset])
            dtype = payload[offset + 1] & 0x0F
            roots.append((letter_char, dtype))
            offset += 2
        return {"opcode": REPLY_ROOTS, "roots": roots}

    if opcode == REPLY_UPLOAD_ERROR:
        item_id, win32_error = struct.unpack_from("<II", payload, 1)
        return {
            "opcode": REPLY_UPLOAD_ERROR,
            "item_id": item_id,
            "win32_error": win32_error,
        }

    if opcode == REPLY_DOWNLOAD_BATCH_EMPTY:
        req_id, item_ids = _decode_req_item_ids(payload)
        return {"opcode": REPLY_DOWNLOAD_BATCH_EMPTY, "req_id": req_id, "item_ids": item_ids}

    if opcode == REPLY_DOWNLOAD_BATCH_DONE:
        if len(payload) >= 7:
            req_id, status_val = struct.unpack_from("<IH", payload, 1)
            return {"opcode": REPLY_DOWNLOAD_BATCH_DONE, "req_id": req_id, "status": status_val}
        return {"opcode": REPLY_DOWNLOAD_BATCH_DONE, "req_id": 0, "status": 0}

    if opcode == REPLY_PRECHECK_BATCH:
        flag0, insuff_space, has_conflicts, flag3, req_id, count = struct.unpack_from(
            "<BBBBII", payload, 1
        )
        conflict_ids = [
            struct.unpack_from("<I", payload, 13 + i * 4)[0]
            for i in range(count)
            if 13 + (i + 1) * 4 <= len(payload)
        ]
        return {
            "opcode": REPLY_PRECHECK_BATCH,
            "flag0": flag0,
            "insufficient_space": bool(insuff_space),
            "has_conflicts": bool(has_conflicts),
            "flag3": flag3,
            "req_id": req_id,
            "conflict_item_ids": conflict_ids,
        }

    if opcode == REPLY_UPLOAD_BATCH_ERROR:
        item_id, win32_err, status_val = struct.unpack_from("<III", payload, 1)
        err_str = None
        marker_off = 13
        if (
            len(payload) >= marker_off + len(_ERRSTR_MARKER) + 4
            and payload[marker_off : marker_off + len(_ERRSTR_MARKER)] == _ERRSTR_MARKER
        ):
            str_off = marker_off + len(_ERRSTR_MARKER)
            str_len = struct.unpack_from("<I", payload, str_off)[0]
            err_str = payload[str_off + 4 : str_off + 4 + str_len].decode(
                "utf-16le", errors="replace"
            )
        return {
            "opcode": REPLY_UPLOAD_BATCH_ERROR,
            "item_id": item_id,
            "win32_error": win32_err,
            "status": status_val,
            "err_str": err_str,
        }

    if opcode == REPLY_PRECHECK_BATCH_EXT:
        flag0, flag1, flag2, flag3, req_id, conflict_count, extra_count = struct.unpack_from(
            "<BBBBIII", payload, 1
        )
        offset = 17
        conflict_ids: list[int] = []
        for _ in range(conflict_count):
            if offset + 4 > len(payload):
                break
            conflict_ids.append(struct.unpack_from("<I", payload, offset)[0])
            offset += 4
        extra_pairs: list[tuple[int, int]] = []
        for _ in range(extra_count):
            if offset + 8 > len(payload):
                break
            item_id, val = struct.unpack_from("<II", payload, offset)
            extra_pairs.append((item_id, val))
            offset += 8
        return {
            "opcode": REPLY_PRECHECK_BATCH_EXT,
            "flag0": flag0,
            "flag1": flag1,
            "flag2": flag2,
            "flag3": flag3,
            "insufficient_space": bool(flag1),
            "has_conflicts": bool(flag2),
            "req_id": req_id,
            "conflict_item_ids": conflict_ids,
            "extra_pairs": extra_pairs,
        }

    if opcode == REPLY_DOWNLOAD_UNREADABLE:
        req_id, item_ids = _decode_req_item_ids(payload)
        return {"opcode": REPLY_DOWNLOAD_UNREADABLE, "req_id": req_id, "item_ids": item_ids}

    if opcode == REPLY_FORMATTED_ERROR:
        count = struct.unpack_from("<I", payload, 1)[0]
        errors: list[tuple[int, str]] = []
        offset = 5
        for _ in range(count):
            if offset + 8 > len(payload):
                break
            err_code, m_len = struct.unpack_from("<II", payload, offset)
            m_str = payload[offset + 8 : offset + 8 + m_len].decode("utf-16le", errors="replace")
            errors.append((err_code, m_str))
            offset += 8 + m_len
        return {"opcode": REPLY_FORMATTED_ERROR, "errors": errors}

    return {"opcode": opcode, "raw": payload}
