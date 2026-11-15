# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Encoders and decoders for all Client Commands (0x01..0x39)."""

import struct
from dataclasses import dataclass
from typing import Any

from openeasysync.constants import (
    OP_CANCEL_TRANSFER,
    OP_CREATE_DIR,
    OP_DELETE_DIR,
    OP_DELETE_FILE,
    OP_DOWNLOAD_BATCH,
    OP_DOWNLOAD_SINGLE,
    OP_EXTENDED_XML_MAX,
    OP_EXTENDED_XML_MIN,
    OP_EXTRA_XML,
    OP_LIST_DIR,
    OP_NOTIFY_ACTIVE_DIR,
    OP_PLUGIN_RPC,
    OP_PRECHECK_BATCH,
    OP_PRECHECK_BATCH_EXT,
    OP_PRECHECK_SINGLE,
    OP_QUERY_ERRORS,
    OP_QUERY_ROOT,
    OP_QUERY_SPACE,
    OP_REMOVE_DIR,
    OP_RENAME,
    OP_RENAME_DIR,
    OP_RENAME_FILE,
    OP_SET_MAC_MODE,
    OP_UPLOAD_SINGLE,
    OP_VERIFY_PEER_NAME,
    WIN32_FIND_DATA_SIZE,
)
from openeasysync.protocol.directory_entry import Win32FindData


@dataclass
class BatchUploadRecord:
    rel_path: str
    find_data: Win32FindData
    data: bytes = b""


@dataclass
class BatchPrecheckExtRecord:
    rel_path: str
    find_data: Win32FindData


# Encoders


def encode_query_root(
    computer_name: str = "TXLIN1",
    flags: int = 0x07,
    code_page: int = 1252,
    path: str | None = None,
) -> bytes:
    """Opcode 0x01: Query Peer Root / Drives.

    EasySuite.exe wire format (Section 4.2.1):
    [0x01][flags:u8][code_page:u16_le][name_len:u8][computer_name:bytes]
    (11 bytes total for default host query)
    """
    name = path if path and computer_name == "TXLIN1" else computer_name
    name_bytes = name.encode("ascii", errors="replace")[:255]
    return (
        bytes([OP_QUERY_ROOT, flags & 0xFF])
        + struct.pack("<HB", code_page & 0xFFFF, len(name_bytes))
        + name_bytes
    )


def encode_notify_active_dir(dir_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0x02: Notify / Set Active Remote Directory.

    EasySuite.exe wire format (0x0040da90 / 0x0043d577):
    [0x02][path_len:u8][dir_path:bytes]
    """
    path_bytes = dir_path.encode(encoding, errors="replace")
    if len(path_bytes) > 255:
        path_bytes = path_bytes[:255]
    return bytes([OP_NOTIFY_ACTIVE_DIR, len(path_bytes)]) + path_bytes


def encode_verify_peer_name(computer_name: str, encoding: str = "ascii") -> bytes:
    """Opcode 0x02 alias for backward compatibility."""
    return encode_notify_active_dir(computer_name, encoding=encoding)


def encode_list_dir(dir_path: str, encoding: str = "utf-16le") -> bytes:
    """Opcode 0x04: Change / List Remote Directory.

    EasySuite.exe wire format (0x0040db44 / 0x0043d741):
    [0x04][path_len:u16_le][dir_path:wchar_t]
    """
    path_bytes = dir_path.encode(encoding, errors="replace")
    return bytes([OP_LIST_DIR]) + struct.pack("<H", len(path_bytes)) + path_bytes


def encode_delete_file(file_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0x08: Delete Remote File."""
    path_bytes = file_path.encode(encoding, errors="replace")
    return bytes([OP_DELETE_FILE]) + struct.pack("<I", len(path_bytes)) + path_bytes


def encode_delete_dir(dir_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0x06: Remove Remote Directory."""
    path_bytes = dir_path.encode(encoding, errors="replace")
    return bytes([OP_REMOVE_DIR]) + struct.pack("<I", len(path_bytes)) + path_bytes


def encode_create_dir(dir_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0x07: Create Remote Directory."""
    path_bytes = dir_path.encode(encoding, errors="replace")
    return bytes([OP_CREATE_DIR]) + struct.pack("<I", len(path_bytes)) + path_bytes


def encode_rename_file(old_path: str, new_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0x09: Rename Remote File."""
    old_b = old_path.encode(encoding, errors="replace")
    new_b = new_path.encode(encoding, errors="replace")
    return bytes([OP_RENAME]) + struct.pack("<II", len(old_b), len(new_b)) + old_b + new_b


def encode_rename_dir(old_path: str, new_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0x09: Rename Remote Directory."""
    old_b = old_path.encode(encoding, errors="replace")
    new_b = new_path.encode(encoding, errors="replace")
    return bytes([OP_RENAME]) + struct.pack("<II", len(old_b), len(new_b)) + old_b + new_b


def encode_upload_single(
    dest_path: str,
    find_data: Win32FindData,
    file_data: bytes,
    encoding: str = "utf-8",
) -> bytes:
    """Opcode 0x0A: Single File Upload Request."""
    path_b = dest_path.encode(encoding, errors="replace")
    fd_b = find_data.pack(encoding=encoding)
    header = bytes([OP_UPLOAD_SINGLE]) + struct.pack("<II", len(path_b), len(file_data)) + fd_b
    return header + path_b + file_data


def encode_download_single(src_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0x0B: Single File Download Request."""
    path_b = src_path.encode(encoding, errors="replace")
    return bytes([OP_DOWNLOAD_SINGLE]) + struct.pack("<I", len(path_b)) + path_b


def encode_download_batch(paths: list[str], encoding: str = "utf-8") -> bytes:
    """Opcode 0x10: Batch Download Files/Directories from Peer."""
    body = bytearray([OP_DOWNLOAD_BATCH])
    body.extend(struct.pack("<H", len(paths)))
    for p in paths:
        pb = p.encode(encoding, errors="replace")
        body.extend(struct.pack("<I", len(pb)))
        body.extend(pb)
    return bytes(body)


def encode_cancel_transfer() -> bytes:
    """Opcode 0x0D: Abort / Cancel Active Transfer."""
    return bytes([OP_CANCEL_TRANSFER])


def encode_query_space(root_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0x0E: Query Drive Free/Total Space."""
    path_b = root_path.encode(encoding, errors="replace")
    return bytes([OP_QUERY_SPACE]) + struct.pack("<I", len(path_b)) + path_b


def encode_precheck_single(dest_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0x0F: Pre-Check Single File Overwrite."""
    path_b = dest_path.encode(encoding, errors="replace")
    return bytes([OP_PRECHECK_SINGLE]) + struct.pack("<I", len(path_b)) + path_b


def encode_set_mac_mode() -> bytes:
    """Opcode 0x10: Set Peer Platform = Mac (legacy)."""
    return bytes([OP_SET_MAC_MODE])


def encode_precheck_batch(dest_dir: str, rel_paths: list[str], encoding: str = "utf-8") -> bytes:
    """Opcode 0x13: Batch Upload Pre-Check."""
    dir_b = dest_dir.encode(encoding, errors="replace")
    body = bytearray([OP_PRECHECK_BATCH])
    body.extend(struct.pack("<II", len(dir_b), len(rel_paths)))
    body.extend(dir_b)
    for rel_p in rel_paths:
        rb = rel_p.encode(encoding, errors="replace")
        body.extend(struct.pack("<I", len(rb)))
        body.extend(rb)
    return bytes(body)


def encode_precheck_batch_ext(
    dest_dir: str,
    records: list[BatchPrecheckExtRecord],
    encoding: str = "utf-8",
) -> bytes:
    """Opcode 0x16: Extended Batch Pre-Check with Metadata."""
    dir_b = dest_dir.encode(encoding, errors="replace")
    body = bytearray([OP_PRECHECK_BATCH_EXT])
    body.extend(struct.pack("<II", len(dir_b), len(records)))
    body.extend(dir_b)
    for rec in records:
        rb = rec.rel_path.encode(encoding, errors="replace")
        fd_b = rec.find_data.pack(encoding=encoding)
        body.extend(struct.pack("<I", len(rb)))
        body.extend(fd_b)
        body.extend(rb)
    return bytes(body)


def encode_xml_control(
    xml_content: str, dll_name: str = "ECN001.DLL", encoding: str = "ascii"
) -> bytes:
    """Opcode 0x34: <OTIMSG> Dynamic Library Plugin RPC XML Tunnel."""
    dll_b = dll_name.encode("ascii", errors="replace")
    xml_b = xml_content.encode(encoding, errors="replace")
    return bytes([OP_PLUGIN_RPC, len(dll_b)]) + dll_b + struct.pack("<I", len(xml_b)) + xml_b


def encode_query_errors(error_codes: list[int]) -> bytes:
    """Opcode 0x17: Query Localized Win32 Error Strings."""
    body = bytearray([OP_QUERY_ERRORS])
    body.extend(struct.pack("<I", len(error_codes)))
    for code in error_codes:
        body.extend(struct.pack("<I", code))
    return bytes(body)


def encode_extended_xml(opcode: int, xml_content: str) -> bytes:
    """Opcode 0x32..0x39: Extended XML Command."""
    xml_b = xml_content.encode("ascii", errors="replace")
    if opcode == OP_EXTRA_XML:
        return bytes([opcode]) + struct.pack("<I", len(xml_b)) + xml_b
    if not xml_b.endswith(b"\x00"):
        xml_b += b"\x00"
    return bytes([opcode]) + xml_b


# Decoders


def decode_command(payload: bytes, encoding: str = "utf-8") -> dict[str, Any]:
    """Decodes an arbitrary Client Command message payload."""
    if not payload:
        raise ValueError("Empty command payload")

    opcode = payload[0]

    if opcode == OP_QUERY_ROOT:
        flags = 0
        code_page = 0
        comp_name = ""
        path = ""
        if len(payload) >= 5:
            flags = payload[1]
            code_page, name_len = struct.unpack_from("<HB", payload, 2)
            comp_name = payload[5 : 5 + name_len].decode("ascii", errors="replace")
            path = comp_name or "\\"
        return {
            "opcode": OP_QUERY_ROOT,
            "flags": flags,
            "code_page": code_page,
            "computer_name": comp_name,
            "path": path,
        }

    if opcode in (OP_NOTIFY_ACTIVE_DIR, OP_VERIFY_PEER_NAME):
        path_len = payload[1] if len(payload) > 1 else 0
        path_str = payload[2 : 2 + path_len].decode(encoding, errors="replace")
        return {"opcode": opcode, "path": path_str, "computer_name": path_str}

    if opcode == OP_LIST_DIR:
        path_len = struct.unpack_from("<H", payload, 1)[0]
        raw_path = payload[3 : 3 + path_len]
        try:
            path_str = raw_path.decode("utf-16le")
        except Exception:
            path_str = raw_path.decode(encoding, errors="replace")
        return {"opcode": opcode, "path": path_str}

    if opcode in (
        OP_DELETE_FILE,
        OP_REMOVE_DIR,
        OP_DELETE_DIR,
        OP_CREATE_DIR,
        OP_DOWNLOAD_SINGLE,
        OP_QUERY_SPACE,
        OP_PRECHECK_SINGLE,
    ):
        path_len = struct.unpack_from("<I", payload, 1)[0]
        path_str = payload[5 : 5 + path_len].decode(encoding, errors="replace")
        return {"opcode": opcode, "path": path_str}

    if opcode in (OP_RENAME, OP_RENAME_FILE, OP_RENAME_DIR):
        old_len, new_len = struct.unpack_from("<II", payload, 1)
        old_path = payload[9 : 9 + old_len].decode(encoding, errors="replace")
        new_path = payload[9 + old_len : 9 + old_len + new_len].decode(encoding, errors="replace")
        return {"opcode": opcode, "old_path": old_path, "new_path": new_path}

    if opcode == OP_UPLOAD_SINGLE:
        path_len, data_len = struct.unpack_from("<II", payload, 1)
        fd = Win32FindData.unpack(payload[9 : 9 + WIN32_FIND_DATA_SIZE], encoding=encoding)
        path_start = 9 + WIN32_FIND_DATA_SIZE
        dest_path = payload[path_start : path_start + path_len].decode(encoding, errors="replace")
        file_data = payload[path_start + path_len : path_start + path_len + data_len]
        return {
            "opcode": OP_UPLOAD_SINGLE,
            "dest_path": dest_path,
            "find_data": fd,
            "file_data": file_data,
        }

    if opcode == OP_DOWNLOAD_BATCH:
        if len(payload) < 3:
            return {"opcode": OP_DOWNLOAD_BATCH, "paths": []}
        count = struct.unpack_from("<H", payload, 1)[0]
        paths: list[str] = []
        offset = 3
        for _ in range(count):
            p_len = struct.unpack_from("<I", payload, offset)[0]
            p_str = payload[offset + 4 : offset + 4 + p_len].decode(encoding, errors="replace")
            paths.append(p_str)
            offset += 4 + p_len
        return {"opcode": OP_DOWNLOAD_BATCH, "paths": paths}

    if opcode == OP_CANCEL_TRANSFER:
        return {"opcode": OP_CANCEL_TRANSFER}

    if opcode == OP_SET_MAC_MODE:
        return {"opcode": OP_SET_MAC_MODE}

    if opcode == OP_PRECHECK_BATCH:
        dir_len, count = struct.unpack_from("<II", payload, 1)
        dest_dir = payload[9 : 9 + dir_len].decode(encoding, errors="replace")
        rel_paths: list[str] = []
        offset = 9 + dir_len
        for _ in range(count):
            r_len = struct.unpack_from("<I", payload, offset)[0]
            r_path = payload[offset + 4 : offset + 4 + r_len].decode(encoding, errors="replace")
            rel_paths.append(r_path)
            offset += 4 + r_len
        return {"opcode": OP_PRECHECK_BATCH, "dest_dir": dest_dir, "rel_paths": rel_paths}

    if opcode == OP_PRECHECK_BATCH_EXT:
        dir_len, count = struct.unpack_from("<II", payload, 1)
        dest_dir = payload[9 : 9 + dir_len].decode(encoding, errors="replace")
        ext_records: list[BatchPrecheckExtRecord] = []
        offset = 9 + dir_len
        for _ in range(count):
            r_len = struct.unpack_from("<I", payload, offset)[0]
            fd = Win32FindData.unpack(
                payload[offset + 4 : offset + 4 + WIN32_FIND_DATA_SIZE], encoding=encoding
            )
            r_start = offset + 4 + WIN32_FIND_DATA_SIZE
            r_path = payload[r_start : r_start + r_len].decode(encoding, errors="replace")
            ext_records.append(BatchPrecheckExtRecord(rel_path=r_path, find_data=fd))
            offset = r_start + r_len
        return {"opcode": OP_PRECHECK_BATCH_EXT, "dest_dir": dest_dir, "records": ext_records}

    if opcode == OP_PLUGIN_RPC:
        if len(payload) >= 2:
            dll_len = payload[1]
            dll_name = payload[2 : 2 + dll_len].decode("ascii", errors="replace")
            if len(payload) >= 2 + dll_len + 4:
                xml_len = struct.unpack_from("<I", payload, 2 + dll_len)[0]
                xml_start = 2 + dll_len + 4
                xml_str = payload[xml_start : xml_start + xml_len].decode("ascii", errors="replace")
                return {"opcode": opcode, "dll_name": dll_name, "xml": xml_str}
        return {"opcode": opcode, "raw": payload}

    if opcode == OP_QUERY_ERRORS:
        count = struct.unpack_from("<I", payload, 1)[0]
        codes = [struct.unpack_from("<I", payload, 5 + i * 4)[0] for i in range(count)]
        return {"opcode": OP_QUERY_ERRORS, "error_codes": codes}

    if OP_EXTENDED_XML_MIN <= opcode <= OP_EXTENDED_XML_MAX:
        if opcode == OP_EXTRA_XML and len(payload) >= 5:
            xml_len = struct.unpack_from("<I", payload, 1)[0]
            xml_str = payload[5 : 5 + xml_len].decode("ascii", errors="replace")
        else:
            xml_str = payload[1:].decode("ascii", errors="replace").rstrip("\x00")
        return {"opcode": opcode, "xml": xml_str}

    return {"opcode": opcode, "raw": payload}
