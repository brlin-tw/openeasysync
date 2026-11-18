# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Encoders and decoders for all Client Commands (0x01..0x39)."""

import struct
import sys
from dataclasses import dataclass
from typing import Any

from openeasysync.constants import (
    OP_ABORT_BATCH,
    OP_ABORT_UPLOAD,
    OP_BATCH_CLEAR_READONLY,
    OP_BATCH_DOWNLOAD_IDS_16,
    OP_BATCH_DOWNLOAD_IDS_32,
    OP_CANCEL_KEEP_PARTIAL,
    OP_CANCEL_SPLITTER,
    OP_CREATE_BATCH_ZERO_FILES,
    OP_CREATE_DIR,
    OP_DELETE_DIR,
    OP_DELETE_FILE,
    OP_DOWNLOAD_BATCH,
    OP_DOWNLOAD_SINGLE,
    OP_EXTENDED_XML_OPCODES,
    OP_EXTRA_XML,
    OP_FILE_CHUNK,
    OP_FORMAT_WIN32_ERRORS,
    OP_LIST_DIR,
    OP_NOTIFY_ACTIVE_DIR,
    OP_PLUGIN_RPC,
    OP_PRECHECK_BATCH,
    OP_PRECHECK_BATCH_EXT,
    OP_QUERY_CURRENT_DIR,
    OP_QUERY_ROOT,
    OP_REMOVE_DIR,
    OP_RENAME,
    OP_RENAME_DIR,
    OP_RENAME_FILE,
    OP_SET_FILE_ATTRS,
    OP_TRANSFER_COMPLETE,
    OP_UI_NOTIFICATION,
    OP_UPLOAD_SINGLE,
    OP_VERIFY_PEER_NAME,
    PEER_FLAG_WINXP,
    PEER_MODE_MAC,
    PEER_MODE_POSIX,
)
from openeasysync.protocol.directory_entry import Win32FindData

_ERRSTR_MARKER = b"ErrStr"


@dataclass
class BatchUploadRecord:
    rel_path: str
    find_data: Win32FindData
    data: bytes = b""


def default_peer_flags() -> int:
    """Returns default 0x01 flags matching the running OS and endianness."""
    if sys.platform == "win32":
        return PEER_FLAG_WINXP
    if sys.byteorder == "big":
        return PEER_MODE_MAC
    return PEER_MODE_POSIX


def _encode_id_path_records(items: list[tuple[int, str]], encoding: str = "utf-16le") -> bytes:
    """Packs back-to-back (item_id:u32_le, path_len:u16_le, path:wchar_t) records."""
    body = bytearray()
    for item_id, path in items:
        pb = path.encode(encoding, errors="replace")
        body.extend(struct.pack("<IH", item_id & 0xFFFFFFFF, len(pb)))
        body.extend(pb)
    return bytes(body)


def _decode_id_path_records(
    payload: bytes, offset: int, count: int, encoding: str = "utf-16le"
) -> list[tuple[int, str]]:
    """Unpacks back-to-back (item_id:u32_le, path_len:u16_le, path:wchar_t) records."""
    records: list[tuple[int, str]] = []
    for _ in range(count):
        if offset + 6 > len(payload):
            break
        item_id, path_len = struct.unpack_from("<IH", payload, offset)
        offset += 6
        path_str = payload[offset : offset + path_len].decode(encoding, errors="replace")
        offset += path_len
        records.append((item_id, path_str))
    return records


# Encoders


def encode_query_root(
    computer_name: str = "TXLIN1",
    flags: int | None = None,
    code_page: int = 1252,
    path: str | None = None,
) -> bytes:
    """Opcode 0x01: Query Peer Root / Drives.

    EasySuite.exe wire format (Section 4.2.1):
    [0x01][flags:u8][code_page:u16_le][name_len:u8][computer_name:bytes]
    (11 bytes total for default host query)
    """
    effective_flags = default_peer_flags() if flags is None else flags
    name = path if path and computer_name == "TXLIN1" else computer_name
    name_bytes = name.encode("ascii", errors="replace")[:255]
    return (
        bytes([OP_QUERY_ROOT, effective_flags & 0xFF])
        + struct.pack("<HB", code_page & 0xFFFF, len(name_bytes))
        + name_bytes
    )


def encode_notify_active_dir(dir_path: str, encoding: str = "utf-8") -> bytes:
    """Opcode 0x02: Notify / Set Active Remote Directory.

    EasySuite.exe wire format (0x0040da90 / 0x0043d577):
    [0x02][path_len:u8][dir_path:bytes]
    """
    path_bytes = dir_path.encode(encoding, errors="replace")[:255]
    return bytes([OP_NOTIFY_ACTIVE_DIR, len(path_bytes)]) + path_bytes


def encode_verify_peer_name(computer_name: str, encoding: str = "ascii") -> bytes:
    """Opcode 0x02 alias for backward compatibility."""
    return encode_notify_active_dir(computer_name, encoding=encoding)


def encode_refresh_dir() -> bytes:
    """Opcode 0x03: Refresh Current Directory (1 byte)."""
    return bytes([OP_QUERY_CURRENT_DIR])


def encode_list_dir(dir_path: str, encoding: str = "utf-16le") -> bytes:
    """Opcode 0x04: Change / List Remote Directory.

    EasySuite.exe wire format (0x0040db44 / 0x0043d741):
    [0x04][path_len:u16_le][dir_path:wchar_t]
    """
    path_bytes = dir_path.encode(encoding, errors="replace")
    return bytes([OP_LIST_DIR]) + struct.pack("<H", len(path_bytes)) + path_bytes


def _encode_single_path_cmd(
    opcode: int,
    path: str,
    req_id: int = 1,
    encoding: str = "utf-16le",
    label: str = "Path",
) -> bytes:
    """Packs a 7-byte management header [opcode:u8][req_id:u32_le][path_len:u16_le] + path."""
    path_bytes = path.encode(encoding, errors="replace")
    if len(path_bytes) > 1024:
        raise ValueError(f"{label} exceeds 1024-byte EasySuite stack buffer limit")
    return struct.pack("<BIH", opcode, req_id & 0xFFFFFFFF, len(path_bytes)) + path_bytes


def encode_delete_file(file_path: str, req_id: int = 1, encoding: str = "utf-16le") -> bytes:
    """Opcode 0x08: Delete Remote File."""
    return _encode_single_path_cmd(
        OP_DELETE_FILE, file_path, req_id=req_id, encoding=encoding, label="File path"
    )


def encode_delete_dir(dir_path: str, req_id: int = 1, encoding: str = "utf-16le") -> bytes:
    """Opcode 0x06: Remove Remote Directory."""
    return _encode_single_path_cmd(
        OP_REMOVE_DIR, dir_path, req_id=req_id, encoding=encoding, label="Directory path"
    )


def encode_create_dir(dir_path: str, req_id: int = 1, encoding: str = "utf-16le") -> bytes:
    """Opcode 0x07: Create Remote Directory."""
    return _encode_single_path_cmd(
        OP_CREATE_DIR, dir_path, req_id=req_id, encoding=encoding, label="Directory path"
    )


def encode_rename_file(
    old_path: str, new_path: str, req_id: int = 1, encoding: str = "utf-16le"
) -> bytes:
    """Opcode 0x09: Rename Remote File or Directory."""
    old_b = old_path.encode(encoding, errors="replace")
    new_b = new_path.encode(encoding, errors="replace")
    if len(old_b) > 1024 or len(new_b) > 1024:
        raise ValueError("Rename path exceeds 1024-byte EasySuite stack buffer limit")
    return struct.pack("<BIHH", OP_RENAME, req_id & 0xFFFFFFFF, len(old_b), len(new_b)) + old_b + new_b


def encode_rename_dir(
    old_path: str, new_path: str, req_id: int = 1, encoding: str = "utf-16le"
) -> bytes:
    """Opcode 0x09: Rename Remote Directory."""
    return encode_rename_file(old_path, new_path, req_id=req_id, encoding=encoding)


def encode_upload_single(
    dest_path: str,
    file_size: int | Win32FindData = 0,
    req_id: int | bytes = 1,
    overwrite: bool = False,
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0x0A: Single File Upload Request."""
    actual_size = 0
    actual_req_id = 1

    if isinstance(file_size, Win32FindData):
        actual_size = len(req_id) if isinstance(req_id, (bytes, bytearray)) else file_size.file_size
    elif isinstance(file_size, int):
        actual_size = file_size
        if isinstance(req_id, int):
            actual_req_id = req_id

    dest_path_bytes = dest_path.encode(encoding, errors="replace")
    if len(dest_path_bytes) > 1024:
        raise ValueError("Destination path exceeds 1024 bytes (EasySuite stack limit)")

    header = struct.pack(
        "<BBIQH",
        OP_UPLOAD_SINGLE,
        0x01 if overwrite else 0x00,
        actual_req_id & 0xFFFFFFFF,
        actual_size,
        len(dest_path_bytes),
    )
    return header + dest_path_bytes


def encode_download_single(
    remote_path: str = "",
    local_path: str = "",
    req_id: int = 1,
    *,
    src_path: str = "",
    dest_path: str = "",
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0x0B: Single File Download Request (Section 4.2.5)."""
    effective_src = remote_path or src_path
    effective_dest = local_path or dest_path

    src_path_bytes = effective_src.encode(encoding, errors="replace")
    if len(src_path_bytes) > 1024:
        raise ValueError("Source path exceeds 1024-byte EasySuite stack buffer limit")

    dest_path_bytes = effective_dest.encode(encoding, errors="replace") if effective_dest else b""
    if len(dest_path_bytes) > 1024:
        raise ValueError("Destination path exceeds 1024-byte EasySuite stack buffer limit")

    header = struct.pack("<BIH", OP_DOWNLOAD_SINGLE, req_id & 0xFFFFFFFF, len(src_path_bytes))
    return header + src_path_bytes + struct.pack("<H", len(dest_path_bytes)) + dest_path_bytes


def encode_file_chunk(
    item_id: int,
    chunk_index: int,
    total_chunks: int,
    chunk_data: bytes,
) -> bytes:
    """Opcode 0x0C: Upload File Data Chunk (Section 4.2.6)."""
    header = struct.pack(
        "<BIIII",
        OP_FILE_CHUNK,
        item_id & 0xFFFFFFFF,
        total_chunks & 0xFFFFFFFF,
        chunk_index & 0xFFFFFFFF,
        len(chunk_data),
    )
    return header + chunk_data


def _encode_abort_with_errstr(
    opcode: int,
    id_val: int,
    win32_error: int = 0,
    err_str: str | None = None,
    encoding: str = "utf-16le",
) -> bytes:
    """Packs [opcode:u8][id:u32_le][win32_error:u32_le] + optional ErrStr suffix."""
    pkt = struct.pack("<BII", opcode, id_val & 0xFFFFFFFF, win32_error & 0xFFFFFFFF)
    if err_str is not None:
        err_b = err_str.encode(encoding, errors="replace")
        pkt += _ERRSTR_MARKER + struct.pack("<I", len(err_b)) + err_b
    return pkt


def encode_abort_upload(
    item_id: int = 1,
    win32_error: int = 0,
    err_str: str | None = None,
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0x0D: Abort Single File Upload (Delete Partial)."""
    return _encode_abort_with_errstr(OP_ABORT_UPLOAD, item_id, win32_error, err_str, encoding)


def encode_transfer_complete(item_id: int = 1, status: int = 0) -> bytes:
    """Opcode 0x0E: Acknowledge File Transfer Completion (7 bytes)."""
    return struct.pack("<BIH", OP_TRANSFER_COMPLETE, item_id & 0xFFFFFFFF, status & 0xFFFF)


def encode_cancel_keep_partial(
    item_id: int = 1,
    win32_error: int = 0,
    err_str: str | None = None,
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0x0F: Cancel Single File Transfer (Keep Partial)."""
    return _encode_abort_with_errstr(OP_CANCEL_KEEP_PARTIAL, item_id, win32_error, err_str, encoding)


def encode_download_batch(
    items: list[tuple[int, str]],
    req_id: int = 1,
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0x10: Batch File Download Request (16-bit Count).

    Wire layout: [0x10][req_id:u32_le][item_count:u16_le][records: (item_id:u32_le, path_len:u16_le, path:wchar_t)...]
    """
    return struct.pack("<BIH", OP_DOWNLOAD_BATCH, req_id & 0xFFFFFFFF, len(items) & 0xFFFF) + _encode_id_path_records(
        items, encoding=encoding
    )


def encode_abort_batch(
    req_id: int = 1,
    win32_error: int = 0,
    err_str: str | None = None,
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0x11: Abort Batch File Transfer."""
    return _encode_abort_with_errstr(OP_ABORT_BATCH, req_id, win32_error, err_str, encoding)


def _encode_id_path_cmd(
    opcode: int,
    items: list[tuple[int, str]],
    req_id: int = 1,
    encoding: str = "utf-16le",
) -> bytes:
    """Packs [opcode:u8][req_id:u32_le][item_count:u16_le][records...] for 0x12 and 0x15."""
    return struct.pack("<BIH", opcode, req_id & 0xFFFFFFFF, len(items) & 0xFFFF) + _encode_id_path_records(
        items, encoding=encoding
    )


def encode_batch_clear_readonly(
    items: list[tuple[int, str]], req_id: int = 1, encoding: str = "utf-16le"
) -> bytes:
    """Opcode 0x12: Batch Prepare / Clear Read-Only Attributes."""
    return _encode_id_path_cmd(OP_BATCH_CLEAR_READONLY, items, req_id=req_id, encoding=encoding)


def _encode_precheck_batch_common(
    opcode: int,
    items: list[tuple[int, str]],
    req_id: int = 1,
    total_size: int = 0,
    flags: int = 0,
    encoding: str = "utf-16le",
) -> bytes:
    """Packs [opcode:u8][req_id:u32_le][total_size:u64_le][flags:u16_le][item_count:u16_le][records...]."""
    header = struct.pack(
        "<BIQHH",
        opcode,
        req_id & 0xFFFFFFFF,
        total_size,
        flags & 0xFFFF,
        len(items) & 0xFFFF,
    )
    return header + _encode_id_path_records(items, encoding=encoding)


def encode_precheck_batch(
    items: list[tuple[int, str]],
    req_id: int = 1,
    total_size: int = 0,
    flags: int = 0,
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0x13: Batch Upload Pre-Check (Space & Conflicts)."""
    return _encode_precheck_batch_common(
        OP_PRECHECK_BATCH, items, req_id=req_id, total_size=total_size, flags=flags, encoding=encoding
    )


def encode_cancel_splitter(job_id: int) -> bytes:
    """Opcode 0x14: Cancel Active Packet Splitter Job (5 bytes)."""
    return struct.pack("<BI", OP_CANCEL_SPLITTER, job_id & 0xFFFFFFFF)


def encode_create_batch_zero_files(
    items: list[tuple[int, str]], req_id: int = 1, encoding: str = "utf-16le"
) -> bytes:
    """Opcode 0x15: Create Batch Zero-Length Files."""
    return _encode_id_path_cmd(OP_CREATE_BATCH_ZERO_FILES, items, req_id=req_id, encoding=encoding)


def encode_precheck_batch_ext(
    items: list[tuple[int, str]],
    req_id: int = 1,
    total_size: int = 0,
    flags: int = 0,
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0x16: Extended Batch Upload Pre-Check (Section 4.2.7)."""
    return _encode_precheck_batch_common(
        OP_PRECHECK_BATCH_EXT, items, req_id=req_id, total_size=total_size, flags=flags, encoding=encoding
    )


def encode_ui_notification(code: int) -> bytes:
    """Opcode 0x17: UI Notification Command (2 bytes, signed int8 code)."""
    return struct.pack("<Bb", OP_UI_NOTIFICATION, code)


def encode_xml_control(
    xml_content: str, dll_name: str = "ECN001.DLL", encoding: str = "ascii"
) -> bytes:
    """Opcode 0x34: <OTIMSG> Dynamic Library Plugin RPC XML Tunnel."""
    dll_b = dll_name.encode("ascii", errors="replace")
    xml_b = xml_content.encode(encoding, errors="replace")
    return bytes([OP_PLUGIN_RPC, len(dll_b)]) + dll_b + struct.pack("<I", len(xml_b)) + xml_b


def encode_batch_download_ids(
    items: list[tuple[int, str]],
    req_id: int = 1,
    flags_or_cp: int = 0,
    has_extra: int = 0,
    use_32bit_count: bool = False,
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0x35 (16-bit count) or 0x36 (32-bit count): Multi-ID Batch Download Request."""
    if use_32bit_count or len(items) > 0xFFFF:
        header = struct.pack(
            "<BHIBI",
            OP_BATCH_DOWNLOAD_IDS_32,
            flags_or_cp & 0xFFFF,
            req_id & 0xFFFFFFFF,
            has_extra & 0xFF,
            len(items) & 0xFFFFFFFF,
        )
    else:
        header = struct.pack(
            "<BHIBH",
            OP_BATCH_DOWNLOAD_IDS_16,
            flags_or_cp & 0xFFFF,
            req_id & 0xFFFFFFFF,
            has_extra & 0xFF,
            len(items) & 0xFFFF,
        )
    return header + _encode_id_path_records(items, encoding=encoding)


def encode_set_file_attrs(
    path: str,
    attributes: int,
    creation_time: int = 0,
    last_access_time: int = 0,
    last_write_time: int = 0,
    encoding: str = "utf-16le",
) -> bytes:
    """Opcode 0x37: Set Remote File Attributes and Timestamps (fire-and-forget)."""
    path_b = path.encode(encoding, errors="replace")
    header = struct.pack(
        "<BIQQQI",
        OP_SET_FILE_ATTRS,
        attributes & 0xFFFFFFFF,
        creation_time,
        last_access_time,
        last_write_time,
        len(path_b),
    )
    return header + path_b


def encode_format_win32_errors(error_codes: list[int]) -> bytes:
    """Opcode 0x38: Format Win32 Error Codes Request."""
    body = bytearray([OP_FORMAT_WIN32_ERRORS])
    body.extend(struct.pack("<I", len(error_codes)))
    for code in error_codes:
        body.extend(struct.pack("<I", code & 0xFFFFFFFF))
    return bytes(body)


def encode_extended_xml(opcode: int, xml_content: str) -> bytes:
    """Opcode 0x32 / 0x33 / 0x39: Extended XML Command."""
    xml_b = xml_content.encode("ascii", errors="replace")
    if opcode == OP_EXTRA_XML:
        return bytes([opcode]) + struct.pack("<I", len(xml_b)) + xml_b
    if not xml_b.endswith(b"\x00"):
        xml_b += b"\x00"
    return bytes([opcode]) + xml_b


VNIC_CMD_PLEASE_SWITCH = "PleaseSwitchAsVirtualNIC"
VNIC_CMD_ACCEPT_SWITCH = "AcceptSwitchAsVirtualNIC"
VNIC_CMD_REJECT_SWITCH = "RejectSwitchAsVirtualNIC"
VNIC_CMD_ME_SWITCH_ROUTER = "MeSwitchAsRouter"
VNIC_CMD_ME_SWITCH_NONE_ROUTER = "MeSwitchAsNoneRouter"


def build_extra_xml_command(command: str, opcode: int = OP_EXTRA_XML) -> bytes:
    """Encodes an `<ExtraXmlCommand>...</ExtraXmlCommand>` message (opcode 0x39)."""
    return encode_extended_xml(opcode, f"<ExtraXmlCommand>{command}</ExtraXmlCommand>")


def parse_extra_xml_command(xml_content: str) -> str | None:
    """Extracts the command text from `<ExtraXmlCommand>...</ExtraXmlCommand>`."""
    stripped = xml_content.strip()
    if not stripped:
        return None
    start_tag = "<ExtraXmlCommand>"
    end_tag = "</ExtraXmlCommand>"
    start_idx = stripped.find(start_tag)
    if start_idx != -1:
        end_idx = stripped.find(end_tag, start_idx + len(start_tag))
        if end_idx != -1:
            return stripped[start_idx + len(start_tag) : end_idx].strip()
    return None


def encode_vnic_switch_request() -> bytes:
    """Opcode 0x39: Initiates Virtual NIC mode-switch handshake."""
    return build_extra_xml_command(VNIC_CMD_PLEASE_SWITCH)


def encode_vnic_switch_accept() -> bytes:
    """Opcode 0x39: Accepts a peer's Virtual NIC mode-switch request."""
    return build_extra_xml_command(VNIC_CMD_ACCEPT_SWITCH)


def encode_vnic_switch_reject() -> bytes:
    """Opcode 0x39: Rejects a peer's Virtual NIC mode-switch request."""
    return build_extra_xml_command(VNIC_CMD_REJECT_SWITCH)


def encode_vnic_switch_role(is_router: bool = False) -> bytes:
    """Opcode 0x39: Announces local router/standard role before switching to 0x0044."""
    cmd = VNIC_CMD_ME_SWITCH_ROUTER if is_router else VNIC_CMD_ME_SWITCH_NONE_ROUTER
    return build_extra_xml_command(cmd)


# Decoders


def _decode_abort_with_errstr(
    opcode: int, payload: bytes, id_key: str = "item_id", encoding: str = "utf-16le"
) -> dict[str, Any]:
    id_val, win32_err = struct.unpack_from("<II", payload, 1)
    result: dict[str, Any] = {
        "opcode": opcode,
        id_key: id_val,
        "win32_error": win32_err,
        "err_str": None,
    }
    if len(payload) >= 9 + len(_ERRSTR_MARKER) + 4 and payload[9 : 9 + len(_ERRSTR_MARKER)] == _ERRSTR_MARKER:
        str_off = 9 + len(_ERRSTR_MARKER)
        str_len = struct.unpack_from("<I", payload, str_off)[0]
        result["err_str"] = payload[str_off + 4 : str_off + 4 + str_len].decode(
            encoding, errors="replace"
        )
    return result


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

    if opcode == OP_QUERY_CURRENT_DIR:
        return {"opcode": OP_QUERY_CURRENT_DIR}

    if opcode == OP_LIST_DIR:
        path_len = struct.unpack_from("<H", payload, 1)[0]
        raw_path = payload[3 : 3 + path_len]
        try:
            path_str = raw_path.decode("utf-16le")
        except Exception:
            path_str = raw_path.decode(encoding, errors="replace")
        return {"opcode": opcode, "path": path_str}

    if opcode in (OP_DELETE_FILE, OP_REMOVE_DIR, OP_DELETE_DIR, OP_CREATE_DIR):
        req_id, path_len = struct.unpack_from("<IH", payload, 1)
        path_str = payload[7 : 7 + path_len].decode("utf-16le", errors="replace")
        return {"opcode": opcode, "req_id": req_id, "path": path_str}

    if opcode in (OP_RENAME, OP_RENAME_FILE, OP_RENAME_DIR):
        req_id, old_len, new_len = struct.unpack_from("<IHH", payload, 1)
        old_path = payload[9 : 9 + old_len].decode("utf-16le", errors="replace")
        new_path = payload[9 + old_len : 9 + old_len + new_len].decode("utf-16le", errors="replace")
        return {"opcode": opcode, "req_id": req_id, "old_path": old_path, "new_path": new_path}

    if opcode == OP_UPLOAD_SINGLE:
        flags, req_id, file_size, path_len = struct.unpack_from("<BIQH", payload, 1)
        dest_path = payload[16 : 16 + path_len].decode("utf-16le", errors="replace")
        return {
            "opcode": OP_UPLOAD_SINGLE,
            "flags": flags,
            "overwrite": bool(flags & 0x01),
            "req_id": req_id,
            "file_size": file_size,
            "dest_path": dest_path,
        }

    if opcode == OP_DOWNLOAD_SINGLE:
        req_id, src_len = struct.unpack_from("<IH", payload, 1)
        src_path = payload[7 : 7 + src_len].decode("utf-16le", errors="replace")
        dest_path = ""
        if len(payload) >= 7 + src_len + 2:
            dest_len = struct.unpack_from("<H", payload, 7 + src_len)[0]
            dest_path = payload[9 + src_len : 9 + src_len + dest_len].decode(
                "utf-16le", errors="replace"
            )
        return {
            "opcode": OP_DOWNLOAD_SINGLE,
            "req_id": req_id,
            "path": src_path,
            "src_path": src_path,
            "dest_path": dest_path,
        }

    if opcode == OP_FILE_CHUNK:
        item_id, total_chunks, chunk_index, chunk_len = struct.unpack_from("<IIII", payload, 1)
        chunk_data = payload[17 : 17 + chunk_len]
        return {
            "opcode": OP_FILE_CHUNK,
            "item_id": item_id,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "chunk_len": chunk_len,
            "chunk_data": chunk_data,
        }

    if opcode == OP_ABORT_UPLOAD:
        return _decode_abort_with_errstr(OP_ABORT_UPLOAD, payload, id_key="item_id")

    if opcode == OP_TRANSFER_COMPLETE:
        item_id, status = struct.unpack_from("<IH", payload, 1)
        return {"opcode": OP_TRANSFER_COMPLETE, "item_id": item_id, "status": status}

    if opcode == OP_CANCEL_KEEP_PARTIAL:
        return _decode_abort_with_errstr(OP_CANCEL_KEEP_PARTIAL, payload, id_key="item_id")

    if opcode == OP_DOWNLOAD_BATCH:
        req_id, count = struct.unpack_from("<IH", payload, 1)
        records = _decode_id_path_records(payload, 7, count)
        return {"opcode": OP_DOWNLOAD_BATCH, "req_id": req_id, "records": records}

    if opcode == OP_ABORT_BATCH:
        return _decode_abort_with_errstr(OP_ABORT_BATCH, payload, id_key="req_id")

    if opcode in (OP_BATCH_CLEAR_READONLY, OP_CREATE_BATCH_ZERO_FILES):
        req_id, count = struct.unpack_from("<IH", payload, 1)
        records = _decode_id_path_records(payload, 7, count)
        return {"opcode": opcode, "req_id": req_id, "records": records}

    if opcode in (OP_PRECHECK_BATCH, OP_PRECHECK_BATCH_EXT):
        req_id, total_size, flags, count = struct.unpack_from("<IQHH", payload, 1)
        records = _decode_id_path_records(payload, 17, count)
        return {
            "opcode": opcode,
            "req_id": req_id,
            "total_size": total_size,
            "flags": flags,
            "records": records,
        }

    if opcode == OP_CANCEL_SPLITTER:
        job_id = struct.unpack_from("<I", payload, 1)[0]
        return {"opcode": OP_CANCEL_SPLITTER, "job_id": job_id}

    if opcode == OP_UI_NOTIFICATION:
        code = struct.unpack_from("<b", payload, 1)[0]
        return {"opcode": OP_UI_NOTIFICATION, "code": code}

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

    if opcode in (OP_BATCH_DOWNLOAD_IDS_16, OP_BATCH_DOWNLOAD_IDS_32):
        count_fmt = "<HIBH" if opcode == OP_BATCH_DOWNLOAD_IDS_16 else "<HIBI"
        hdr_size = struct.calcsize(count_fmt)
        flags_or_cp, req_id, has_extra, count = struct.unpack_from(count_fmt, payload, 1)
        records = _decode_id_path_records(payload, 1 + hdr_size, count)
        return {
            "opcode": opcode,
            "flags_or_cp": flags_or_cp,
            "req_id": req_id,
            "has_extra": has_extra,
            "records": records,
        }

    if opcode == OP_SET_FILE_ATTRS:
        attrs, ctime, atime, mtime, path_len = struct.unpack_from("<IQQQI", payload, 1)
        path_str = payload[33 : 33 + path_len].decode("utf-16le", errors="replace")
        return {
            "opcode": OP_SET_FILE_ATTRS,
            "attributes": attrs,
            "creation_time": ctime,
            "last_access_time": atime,
            "last_write_time": mtime,
            "path": path_str,
        }

    if opcode == OP_FORMAT_WIN32_ERRORS:
        count = struct.unpack_from("<I", payload, 1)[0]
        codes = [struct.unpack_from("<I", payload, 5 + i * 4)[0] for i in range(count)]
        return {"opcode": OP_FORMAT_WIN32_ERRORS, "error_codes": codes}

    if opcode in OP_EXTENDED_XML_OPCODES:
        if opcode == OP_EXTRA_XML and len(payload) >= 5:
            xml_len = struct.unpack_from("<I", payload, 1)[0]
            xml_str = payload[5 : 5 + xml_len].decode("ascii", errors="replace")
        else:
            xml_str = payload[1:].decode("ascii", errors="replace").rstrip("\x00")
        return {
            "opcode": opcode,
            "xml": xml_str,
            "extra_xml_command": parse_extra_xml_command(xml_str),
        }

    return {"opcode": opcode, "raw": payload}
