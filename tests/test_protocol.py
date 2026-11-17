# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for wire protocol codecs, commands, replies, XML, and path mapping."""

import struct

import pytest

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
    OP_FILE_CHUNK,
    OP_FORMAT_WIN32_ERRORS,
    OP_LIST_DIR,
    OP_NOTIFY_ACTIVE_DIR,
    OP_PRECHECK_BATCH,
    OP_PRECHECK_BATCH_EXT,
    OP_QUERY_CURRENT_DIR,
    OP_QUERY_ROOT,
    OP_RENAME_DIR,
    OP_RENAME_FILE,
    OP_SET_FILE_ATTRS,
    OP_TRANSFER_COMPLETE,
    OP_UI_NOTIFICATION,
    OP_UPLOAD_SINGLE,
    OP_VERIFY_PEER_NAME,
    OP_VNIC_SWITCH,
    OP_XML_CONTROL,
    PEER_MODE_POSIX,
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
    STATUS_EXISTS,
    STATUS_INLINE_SMALL_FILE,
    STATUS_OK,
    STATUS_ZERO_LENGTH,
)
from openeasysync.protocol.commands import (
    decode_command,
    default_peer_flags,
    encode_abort_batch,
    encode_abort_upload,
    encode_batch_clear_readonly,
    encode_batch_download_ids,
    encode_cancel_keep_partial,
    encode_cancel_splitter,
    encode_create_batch_zero_files,
    encode_create_dir,
    encode_delete_dir,
    encode_delete_file,
    encode_download_batch,
    encode_download_single,
    encode_extended_xml,
    encode_file_chunk,
    encode_format_win32_errors,
    encode_list_dir,
    encode_notify_active_dir,
    encode_precheck_batch,
    encode_precheck_batch_ext,
    encode_query_root,
    encode_refresh_dir,
    encode_rename_dir,
    encode_rename_file,
    encode_set_file_attrs,
    encode_transfer_complete,
    encode_ui_notification,
    encode_upload_single,
    encode_verify_peer_name,
    encode_xml_control,
)
from openeasysync.protocol.directory_entry import make_file_entry
from openeasysync.protocol.path_mapping import PathMapper
from openeasysync.protocol.replies import (
    decode_reply,
    encode_download_batch_done,
    encode_download_batch_empty,
    encode_download_unreadable,
    encode_error_code,
    encode_formatted_errors,
    encode_listing_reply,
    encode_precheck_batch_ext_reply,
    encode_precheck_batch_reply,
    encode_roots_reply,
    encode_status_ack,
    encode_upload_batch_error,
    encode_upload_error,
)
from openeasysync.protocol.xml_msg import build_oti_msg, parse_oti_msg
from openeasysync.server.handler import PeerServerHandler


def test_command_encoders_and_decoders():
    """Verify all Client Commands encode and decode correctly."""
    # 0x01 Query Root (11 bytes total for default host query)
    raw01 = encode_query_root()
    assert len(raw01) == 11
    assert raw01 == bytes([OP_QUERY_ROOT, default_peer_flags(), 0xE4, 0x04, 0x06]) + b"TXLIN1"
    c01 = decode_command(raw01)
    assert c01["opcode"] == OP_QUERY_ROOT
    assert c01["computer_name"] == "TXLIN1"
    assert c01["flags"] == default_peer_flags()
    assert c01["code_page"] == 1252

    # Explicit flags override on 0x01
    c01_win = decode_command(encode_query_root(flags=0x07))
    assert c01_win["flags"] == 0x07

    # 0x03 Refresh Current Directory
    c03 = decode_command(encode_refresh_dir())
    assert c03["opcode"] == OP_QUERY_CURRENT_DIR

    # Test backwards-compatible 1-byte decode
    assert decode_command(bytes([OP_QUERY_ROOT]))["opcode"] == OP_QUERY_ROOT

    # 0x02 Notify Active Directory (and backward-compatible verify peer name)
    encoded_02 = encode_notify_active_dir("C:\\Users\\Guest")
    assert (
        encoded_02 == bytes([OP_NOTIFY_ACTIVE_DIR, len("C:\\Users\\Guest")]) + b"C:\\Users\\Guest"
    )
    c02 = decode_command(encoded_02)
    assert c02["opcode"] == OP_NOTIFY_ACTIVE_DIR
    assert c02["path"] == "C:\\Users\\Guest"
    assert c02["computer_name"] == "C:\\Users\\Guest"

    # Backward compatibility alias test
    c02_alias = decode_command(encode_verify_peer_name("MYCOMPUTER"))
    assert c02_alias["opcode"] == OP_VERIFY_PEER_NAME
    assert c02_alias["computer_name"] == "MYCOMPUTER"

    # 0x04 List Dir
    c04 = decode_command(encode_list_dir("C:\\Users\\Guest"))
    assert c04["opcode"] == OP_LIST_DIR
    assert c04["path"] == "C:\\Users\\Guest"

    # 0x08 Delete File
    c08 = decode_command(encode_delete_file("C:\\file.txt"))
    assert c08["opcode"] == OP_DELETE_FILE
    assert c08["path"] == "C:\\file.txt"

    # 0x06 Delete Dir (Remove Directory)
    c06 = decode_command(encode_delete_dir("C:\\Folder"))
    assert c06["opcode"] == OP_DELETE_DIR
    assert c06["path"] == "C:\\Folder"

    # 0x07 Create Dir
    c07 = decode_command(encode_create_dir("C:\\NewFolder"))
    assert c07["opcode"] == OP_CREATE_DIR
    assert c07["path"] == "C:\\NewFolder"

    # 0x09 Rename File
    c09 = decode_command(encode_rename_file("old.txt", "new.txt"))
    assert c09["opcode"] == OP_RENAME_FILE
    assert c09["old_path"] == "old.txt"
    assert c09["new_path"] == "new.txt"

    # 0x09 Rename Dir
    c09b = decode_command(encode_rename_dir("old_dir", "new_dir"))
    assert c09b["opcode"] == OP_RENAME_DIR

    # 0x0A Upload Single
    raw0a = encode_upload_single("C:\\doc.txt", file_size=12, req_id=1, overwrite=False)
    assert len(raw0a) == 16 + len("C:\\doc.txt".encode("utf-16le"))
    c0a = decode_command(raw0a)
    assert c0a["opcode"] == OP_UPLOAD_SINGLE
    assert c0a["dest_path"] == "C:\\doc.txt"
    assert c0a["file_size"] == 12
    assert c0a["req_id"] == 1
    assert c0a["overwrite"] is False

    # 0x0A destination path limit (> 1024 bytes)
    oversized_path = "C:\\" + "x" * 600
    with pytest.raises(ValueError, match="1024 bytes"):
        encode_upload_single(oversized_path, file_size=100)

    # 0x0C Upload File Data Chunk
    raw0c = encode_file_chunk(item_id=1, chunk_index=1, total_chunks=3, chunk_data=b"chunk data")
    assert len(raw0c) == 17 + len(b"chunk data")
    c0c = decode_command(raw0c)
    assert c0c["opcode"] == OP_FILE_CHUNK
    assert c0c["item_id"] == 1
    assert c0c["chunk_index"] == 1
    assert c0c["total_chunks"] == 3
    assert c0c["chunk_data"] == b"chunk data"

    # 0xA5 Single-File Upload Error Notification
    raw_a5_err = encode_upload_error(item_id=1, win32_error=112)
    assert len(raw_a5_err) == 9
    r_a5_err = decode_reply(raw_a5_err)
    assert r_a5_err["opcode"] == REPLY_UPLOAD_ERROR
    assert r_a5_err["item_id"] == 1
    assert r_a5_err["win32_error"] == 112

    # 0x0E Acknowledge File Transfer Completion
    raw0e = encode_transfer_complete(item_id=1, status=0)
    assert len(raw0e) == 7
    c0e = decode_command(raw0e)
    assert c0e["opcode"] == OP_TRANSFER_COMPLETE
    assert c0e["item_id"] == 1
    assert c0e["status"] == 0

    # 0x0B Download Single
    c0b = decode_command(encode_download_single("C:\\doc.txt"))
    assert c0b["opcode"] == OP_DOWNLOAD_SINGLE
    assert c0b["path"] == "C:\\doc.txt"

    # 0x10 Batch Download
    c10 = decode_command(encode_download_batch([(1, "C:\\p1"), (2, "C:\\p2")], req_id=9))
    assert c10["opcode"] == OP_DOWNLOAD_BATCH
    assert c10["req_id"] == 9
    assert c10["records"] == [(1, "C:\\p1"), (2, "C:\\p2")]

    # 0x35 / 0x36 Multi-ID Batch Download
    c35 = decode_command(encode_batch_download_ids([(10, "C:\\a.txt")], req_id=3, flags_or_cp=1252))
    assert c35["opcode"] == OP_BATCH_DOWNLOAD_IDS_16
    assert c35["flags_or_cp"] == 1252
    assert c35["req_id"] == 3
    assert c35["records"] == [(10, "C:\\a.txt")]

    c36 = decode_command(
        encode_batch_download_ids([(20, "C:\\b.txt")], req_id=4, use_32bit_count=True)
    )
    assert c36["opcode"] == OP_BATCH_DOWNLOAD_IDS_32
    assert c36["records"] == [(20, "C:\\b.txt")]

    # 0x0D Abort Upload / 0x0F Cancel Keep Partial / 0x11 Abort Batch (with & without ErrStr)
    c0d = decode_command(encode_abort_upload(item_id=7, win32_error=5))
    assert c0d == {"opcode": OP_ABORT_UPLOAD, "item_id": 7, "win32_error": 5, "err_str": None}

    c0f = decode_command(encode_cancel_keep_partial(item_id=8, win32_error=2, err_str="Not found"))
    assert c0f == {
        "opcode": OP_CANCEL_KEEP_PARTIAL,
        "item_id": 8,
        "win32_error": 2,
        "err_str": "Not found",
    }

    c11 = decode_command(encode_abort_batch(req_id=9, win32_error=112, err_str="No space"))
    assert c11 == {
        "opcode": OP_ABORT_BATCH,
        "req_id": 9,
        "win32_error": 112,
        "err_str": "No space",
    }

    # 0x12 Batch Clear Read-Only & 0x15 Create Batch Zero-Length Files
    c12 = decode_command(encode_batch_clear_readonly([(1, "C:\\ro.txt")], req_id=5))
    assert c12 == {"opcode": OP_BATCH_CLEAR_READONLY, "req_id": 5, "records": [(1, "C:\\ro.txt")]}

    c15 = decode_command(encode_create_batch_zero_files([(2, "C:\\z.txt")], req_id=6))
    assert c15 == {"opcode": OP_CREATE_BATCH_ZERO_FILES, "req_id": 6, "records": [(2, "C:\\z.txt")]}

    # 0x13 Batch Precheck & 0x16 Batch Precheck Ext
    c13 = decode_command(
        encode_precheck_batch([(1, "C:\\Base\\f1.txt"), (2, "C:\\Base\\f2.txt")], req_id=10, total_size=2048, flags=1)
    )
    assert c13["opcode"] == OP_PRECHECK_BATCH
    assert c13["req_id"] == 10
    assert c13["total_size"] == 2048
    assert c13["flags"] == 1
    assert c13["records"] == [(1, "C:\\Base\\f1.txt"), (2, "C:\\Base\\f2.txt")]

    c16 = decode_command(
        encode_precheck_batch_ext([(3, "C:\\Base\\f3.txt")], req_id=11, total_size=4096)
    )
    assert c16["opcode"] == OP_PRECHECK_BATCH_EXT
    assert c16["req_id"] == 11
    assert c16["total_size"] == 4096
    assert c16["records"] == [(3, "C:\\Base\\f3.txt")]

    # 0x14 Cancel Splitter & 0x17 UI Notification
    c14 = decode_command(encode_cancel_splitter(job_id=42))
    assert c14 == {"opcode": OP_CANCEL_SPLITTER, "job_id": 42}

    c17 = decode_command(encode_ui_notification(-1))
    assert c17 == {"opcode": OP_UI_NOTIFICATION, "code": -1}

    # 0x34 XML Control (<OTIMSG>)
    c34 = decode_command(encode_xml_control("<OTIMSG></OTIMSG>"))
    assert c34["opcode"] == OP_XML_CONTROL
    assert c34["xml"] == "<OTIMSG></OTIMSG>"

    # 0x37 Set File Attributes & Timestamps
    c37 = decode_command(encode_set_file_attrs("C:\\a.txt", 0x21, 100, 200, 300))
    assert c37 == {
        "opcode": OP_SET_FILE_ATTRS,
        "attributes": 0x21,
        "creation_time": 100,
        "last_access_time": 200,
        "last_write_time": 300,
        "path": "C:\\a.txt",
    }

    # 0x38 Format Win32 Errors
    c38 = decode_command(encode_format_win32_errors([5, 112]))
    assert c38["opcode"] == OP_FORMAT_WIN32_ERRORS
    assert c38["error_codes"] == [5, 112]

    # 0x32 Virtual NIC Switch XML
    c32 = decode_command(
        encode_extended_xml(
            OP_VNIC_SWITCH,
            "<ExtraXmlCommand>PleaseSwitchAsVirtualNIC</ExtraXmlCommand>",
        )
    )
    assert c32["opcode"] == OP_VNIC_SWITCH
    assert "<ExtraXmlCommand>PleaseSwitchAsVirtualNIC</ExtraXmlCommand>" in c32["xml"]

    # 0x39 Extended XML Command Channel
    c39 = decode_command(
        encode_extended_xml(
            0x39,
            "<ExtraXmlCommand>LocalLibraryInformation</ExtraXmlCommand>",
        )
    )
    assert c39["opcode"] == 0x39
    assert "<ExtraXmlCommand>LocalLibraryInformation</ExtraXmlCommand>" in c39["xml"]


def test_reply_encoders_and_decoders():
    """Verify all Server Replies encode and decode correctly."""
    # 0xA1 Status ACK (24-byte structured ACK)
    raw_a1 = encode_status_ack(req_opcode=OP_NOTIFY_ACTIVE_DIR, status=STATUS_OK)
    assert len(raw_a1) == 24
    r_a1 = decode_reply(raw_a1)
    assert r_a1["opcode"] == REPLY_STATUS_ACK
    assert r_a1["status"] == STATUS_OK
    assert r_a1["req_opcode"] == OP_NOTIFY_ACTIVE_DIR

    # Error Code (encoded via 24-byte 0xA1 structured response)
    raw_err = encode_error_code(112, req_opcode=OP_LIST_DIR)
    assert len(raw_err) == 24
    r_err = decode_reply(raw_err)
    assert r_err["opcode"] == REPLY_STATUS_ACK
    assert r_err["status"] == STATUS_ERROR
    assert r_err["win32_error"] == 112
    assert r_err["req_opcode"] == OP_LIST_DIR

    # 0xA2 Listing (formerly 0xA3)
    entry = make_file_entry("sample.txt", size=500)
    raw_listing = encode_listing_reply([entry])
    assert raw_listing[0] == REPLY_LISTING
    assert raw_listing[0] == 0xA2
    r_a2 = decode_reply(raw_listing)
    assert r_a2["opcode"] == REPLY_LISTING
    assert len(r_a2["entries"]) == 1
    assert r_a2["entries"][0].filename == "sample.txt"

    # 0xA4 Roots Reply
    r_roots = decode_reply(encode_roots_reply([("C", 2), ("D", 1)]))
    assert r_roots["opcode"] == REPLY_ROOTS
    assert r_roots["roots"] == [("C", 2), ("D", 1)]

    # 0xA6 Batch Download Empty
    r_a6 = decode_reply(encode_download_batch_empty([10, 20], req_id=5))
    assert r_a6 == {"opcode": REPLY_DOWNLOAD_BATCH_EMPTY, "req_id": 5, "item_ids": [10, 20]}

    # 0xA7 Batch Download Done
    r_a7 = decode_reply(encode_download_batch_done(req_id=5, status=0))
    assert r_a7 == {"opcode": REPLY_DOWNLOAD_BATCH_DONE, "req_id": 5, "status": 0}

    # 0xA8 Precheck Batch Reply
    r_a8 = decode_reply(encode_precheck_batch_reply([1, 3], req_id=7, insufficient_space=True))
    assert r_a8["opcode"] == REPLY_PRECHECK_BATCH
    assert r_a8["insufficient_space"] is True
    assert r_a8["has_conflicts"] is True
    assert r_a8["req_id"] == 7
    assert r_a8["conflict_item_ids"] == [1, 3]

    # 0xA9 Batch Upload Error Notification (with & without ErrStr)
    r_a9 = decode_reply(encode_upload_batch_error(item_id=4, win32_error=112, status=1))
    assert r_a9 == {
        "opcode": REPLY_UPLOAD_BATCH_ERROR,
        "item_id": 4,
        "win32_error": 112,
        "status": 1,
        "err_str": None,
    }
    r_a9_str = decode_reply(
        encode_upload_batch_error(item_id=4, win32_error=5, status=1, err_str="Denied")
    )
    assert r_a9_str["err_str"] == "Denied"

    # 0xAA Precheck Batch Ext Reply
    r_aa = decode_reply(
        encode_precheck_batch_ext_reply([2], req_id=8, extra_pairs=[(2, 99)], flag1=1)
    )
    assert r_aa["opcode"] == REPLY_PRECHECK_BATCH_EXT
    assert r_aa["insufficient_space"] is True
    assert r_aa["has_conflicts"] is True
    assert r_aa["req_id"] == 8
    assert r_aa["conflict_item_ids"] == [2]
    assert r_aa["extra_pairs"] == [(2, 99)]

    # 0xAB Batch Unreadable Files Notification
    r_ab = decode_reply(encode_download_unreadable([9, 11], req_id=6))
    assert r_ab == {"opcode": REPLY_DOWNLOAD_UNREADABLE, "req_id": 6, "item_ids": [9, 11]}

    # 0xAC Formatted Win32 Error Strings Reply (UTF-16LE)
    r_ac = decode_reply(encode_formatted_errors([(5, "Access is denied.")]))
    assert r_ac == {"opcode": REPLY_FORMATTED_ERROR, "errors": [(5, "Access is denied.")]}


def test_otimsg_xml_build_and_parse():
    """Verify <OTIMSG> XML handshake builder and parser."""
    xml_str = build_oti_msg(
        msg_type="COMMAND",
        command="CONNECT",
        os_version="Linux 6.8",
        computer_name="test-host",
    )
    assert "<OTIMSG>" in xml_str
    assert "<MSG_TYPE>COMMAND</MSG_TYPE>" in xml_str
    assert "<COMMAND>CONNECT</COMMAND>" in xml_str
    assert "< SYSTEM_INFO >" in xml_str

    parsed = parse_oti_msg(xml_str)
    assert parsed["msg_type"] == "COMMAND"
    assert parsed["command"] == "CONNECT"
    assert parsed["os_version"] == "Linux 6.8"
    assert parsed["computer_name"] == "test-host"


def test_path_mapper():
    """Verify cross-platform path mapping."""
    mapper = PathMapper(virtual_root="/tmp/virtual_test")
    loc = mapper.wire_to_local("C:\\folder\\file.txt")
    assert loc == "/tmp/virtual_test/folder/file.txt"

    wire = mapper.local_to_wire("/tmp/virtual_test/folder/file.txt")
    assert wire == "C:\\folder\\file.txt"


def test_peer_server_handler_extended_xml():
    """Verify PeerServerHandler responds to 0x32..0x39 Extended XML commands."""
    from openeasysync.server.handler import PeerServerHandler

    handler = PeerServerHandler()

    # 0x32 Virtual NIC mode switch request -> Reject
    req_32 = encode_extended_xml(
        OP_VNIC_SWITCH,
        "<ExtraXmlCommand>PleaseSwitchAsVirtualNIC</ExtraXmlCommand>",
    )
    rep_32 = handler.handle_command(req_32)
    assert len(rep_32) == 1
    dec_32 = decode_command(rep_32[0])
    assert dec_32["opcode"] == OP_VNIC_SWITCH
    assert "RejectSwitchAsVirtualNIC" in dec_32["xml"]

    # 0x39 Extended XML request (e.g. LocalLibraryInformation) -> Acknowledge OK
    req_39 = encode_extended_xml(
        0x39,
        "<ExtraXmlCommand>LocalLibraryInformation</ExtraXmlCommand>",
    )
    rep_39 = handler.handle_command(req_39)
    assert len(rep_39) == 1
    dec_39 = decode_command(rep_39[0])
    assert dec_39["opcode"] == 0x39
    assert "<ExtraXmlCommand>OK</ExtraXmlCommand>" in dec_39["xml"]


def test_format_win32_error():
    """Verify Win32 error code formatting."""
    from openeasysync.protocol.replies import format_win32_error

    assert format_win32_error(0) == "The operation completed successfully."
    assert format_win32_error(2) == "The system cannot find the file specified."
    assert format_win32_error(3) == "The system cannot find the path specified."
    assert format_win32_error(5) == "Access is denied."
    assert format_win32_error(21) == "The device is not ready."
    assert format_win32_error(999999) == "Win32 error 999999"


def test_upload_protocol_wire_framing_and_server(tmp_path):
    """Verify single file upload wire framing: 0x0A, 0xA1, 0x0C, 0xA5, 0x0E."""
    mapper = PathMapper(virtual_root=str(tmp_path))
    handler = PeerServerHandler(path_mapper=mapper)

    # 1. 0x0A with zero-length file -> returns 0xA1 with status=7
    cmd_zero = encode_upload_single("C:\\empty.txt", file_size=0, req_id=10, overwrite=False)
    replies_zero = handler.handle_command(cmd_zero)
    assert len(replies_zero) == 1
    assert len(replies_zero[0]) == 24
    assert replies_zero[0][0] == 0xA1
    # Unpack status (uint8 at offset +0x02)
    status = replies_zero[0][2]
    assert status == 7
    assert (tmp_path / "empty.txt").exists()

    # 2. 0x0A with existing file and overwrite=False -> conflict status=2
    cmd_conflict = encode_upload_single("C:\\empty.txt", file_size=10, req_id=11, overwrite=False)
    replies_conflict = handler.handle_command(cmd_conflict)
    assert len(replies_conflict) == 1
    assert replies_conflict[0][2] == 2

    # 3. 0x0A with overwrite=True -> succeeds status=0
    cmd_ow = encode_upload_single("C:\\file.txt", file_size=11, req_id=12, overwrite=True)
    replies_ow = handler.handle_command(cmd_ow)
    assert len(replies_ow) == 1
    assert replies_ow[0][2] == 0

    # 4. Stream 0x0C chunk -> unacknowledged on success (returns no replies)
    chunk_data = b"Hello World"
    cmd_chunk = encode_file_chunk(item_id=12, chunk_index=1, total_chunks=1, chunk_data=chunk_data)
    replies_chunk = handler.handle_command(cmd_chunk)
    assert replies_chunk == []

    # 5. Finalize via 0x0E -> returns no replies
    cmd_fin = encode_transfer_complete(item_id=12, status=0)
    replies_fin = handler.handle_command(cmd_fin)
    assert replies_fin == []
    assert (tmp_path / "file.txt").read_bytes() == b"Hello World"

    # 6. Write error on unknown/closed item_id -> emits 9-byte 0xA5 error notification
    err_replies = handler.handle_command(
        encode_file_chunk(item_id=999, chunk_index=1, total_chunks=1, chunk_data=b"fail")
    )
    assert len(err_replies) == 1
    assert len(err_replies[0]) == 9
    assert err_replies[0][0] == 0xA5
    dec_err = decode_reply(err_replies[0])
    assert dec_err["opcode"] == 0xA5
    assert dec_err["item_id"] == 999
    assert dec_err["win32_error"] == 6


def test_download_protocol_wire_framing_and_server(tmp_path):
    """Verify single file download wire framing (0x0B), buffer limits, and server handling."""
    mapper = PathMapper(virtual_root=str(tmp_path))
    handler = PeerServerHandler(path_mapper=mapper)

    # 1. Wire Framing and Buffer Overflow validation
    with pytest.raises(ValueError, match="Source path exceeds 1024-byte"):
        encode_download_single("C:\\" + "a" * 600)

    with pytest.raises(ValueError, match="Destination path exceeds 1024-byte"):
        encode_download_single("C:\\file.txt", local_path="D:\\" + "b" * 600)

    cmd_pkt = encode_download_single("C:\\src.bin", local_path="D:\\dest.bin", req_id=42)
    assert cmd_pkt[0] == OP_DOWNLOAD_SINGLE
    req_id, src_len = struct.unpack_from("<IH", cmd_pkt, 1)
    assert req_id == 42
    assert src_len == len("C:\\src.bin".encode("utf-16le"))
    dest_len = struct.unpack_from("<H", cmd_pkt, 7 + src_len)[0]
    assert dest_len == len("D:\\dest.bin".encode("utf-16le"))

    # When local_path omitted, dest_len MUST still be present as 0x0000 (2 bytes)
    cmd_no_dest = encode_download_single("C:\\src.bin", req_id=43)
    assert len(cmd_no_dest) == 9 + len("C:\\src.bin".encode("utf-16le"))
    assert struct.unpack_from("<H", cmd_no_dest, len(cmd_no_dest) - 2)[0] == 0

    # 2. Server response for missing file -> status=1 (STATUS_ERROR), win32_error=2
    cmd_missing = encode_download_single("C:\\nonexistent.txt", req_id=100)
    reps_missing = handler.handle_command(cmd_missing)
    assert len(reps_missing) == 1
    rep_missing = decode_reply(reps_missing[0])
    assert rep_missing["opcode"] == REPLY_STATUS_ACK
    assert rep_missing["status"] == STATUS_ERROR
    assert rep_missing["win32_error"] == 2
    assert rep_missing["req_id"] == 100

    # 3. Server response for 0-byte file -> status=7 (STATUS_ZERO_LENGTH)
    empty_file = tmp_path / "empty.dat"
    empty_file.write_bytes(b"")
    cmd_empty = encode_download_single("C:\\empty.dat", req_id=101)
    reps_empty = handler.handle_command(cmd_empty)
    assert len(reps_empty) == 1
    rep_empty = decode_reply(reps_empty[0])
    assert rep_empty["opcode"] == REPLY_STATUS_ACK
    assert rep_empty["status"] == STATUS_ZERO_LENGTH
    assert rep_empty["req_id"] == 101

    # 4. Server response for small file (<= 64,512 bytes) -> inline status=9
    small_content = b"Small file inline payload content!" * 10
    small_file = tmp_path / "small.txt"
    small_file.write_bytes(small_content)
    cmd_small = encode_download_single("C:\\small.txt", req_id=102)
    reps_small = handler.handle_command(cmd_small)
    assert len(reps_small) == 1
    assert len(reps_small[0]) == 24 + len(small_content)
    rep_small = decode_reply(reps_small[0])
    assert rep_small["opcode"] == REPLY_STATUS_ACK
    assert rep_small["status"] == STATUS_INLINE_SMALL_FILE
    assert rep_small["req_id"] == 102
    assert rep_small["data_len"] == len(small_content)
    assert rep_small["file_data"] == small_content

    # 5. Server response for large file (> 64,512 bytes) -> initial 0xA1 (status=0) + streaming 0xA3 chunks
    large_content = b"ABCDEF0123456789" * 5000  # 80,000 bytes
    large_file = tmp_path / "large.bin"
    large_file.write_bytes(large_content)
    cmd_large = encode_download_single("C:\\large.bin", req_id=103)
    reps_large = handler.handle_command(cmd_large)
    assert len(reps_large) >= 2
    # First packet: initial 24-byte ACK
    rep_init = decode_reply(reps_large[0])
    assert rep_init["opcode"] == REPLY_STATUS_ACK
    assert rep_init["status"] == STATUS_OK
    assert rep_init["file_size"] == 80000
    assert rep_init["req_id"] == 103

    # Subsequent packets: 0xA3 data chunks
    assembled_bytes = bytearray()
    for chunk_pkt in reps_large[1:]:
        assert chunk_pkt[0] == REPLY_DOWNLOAD_CHUNK
        rep_chunk = decode_reply(chunk_pkt)
        assert rep_chunk["item_id"] == 103
        assembled_bytes.extend(rep_chunk["data"])
    assert bytes(assembled_bytes) == large_content


def test_file_management_wire_framing(tmp_path):
    """Verify 0x08, 0x06, 0x07, and 0x09 wire framing (UTF-16LE + req_id + u16 lengths) and 0xA1 replies."""
    # 1. Verify exact 7-byte header + UTF-16LE framing for 0x08 (Delete File)
    target = "C:\\REUSE.toml"
    expected_bytes = target.encode("utf-16-le")
    raw_08 = encode_delete_file(target, req_id=42)
    assert raw_08 == struct.pack("<BIH", OP_DELETE_FILE, 42, len(expected_bytes)) + expected_bytes
    assert len(raw_08) == 7 + 2 * len(target)
    dec_08 = decode_command(raw_08)
    assert dec_08 == {"opcode": OP_DELETE_FILE, "req_id": 42, "path": target}

    # 2. Verify symmetrical 7-byte header + UTF-16LE framing for 0x06 and 0x07
    raw_06 = encode_delete_dir("C:\\EmptyDir", req_id=43)
    assert len(raw_06) == 7 + 2 * len("C:\\EmptyDir")
    assert decode_command(raw_06)["req_id"] == 43

    raw_07 = encode_create_dir("C:\\NewDir", req_id=44)
    assert len(raw_07) == 7 + 2 * len("C:\\NewDir")
    assert decode_command(raw_07)["req_id"] == 44

    # 3. Verify 9-byte header + UTF-16LE framing for 0x09 (Rename)
    raw_09 = encode_rename_file("C:\\a.txt", "C:\\b.txt", req_id=45)
    assert len(raw_09) == 9 + 2 * len("C:\\a.txt") + 2 * len("C:\\b.txt")
    dec_09 = decode_command(raw_09)
    assert dec_09 == {
        "opcode": OP_RENAME_FILE,
        "req_id": 45,
        "old_path": "C:\\a.txt",
        "new_path": "C:\\b.txt",
    }

    # 4. Verify 1024-byte stack buffer guard
    with pytest.raises(ValueError, match="1024-byte"):
        encode_delete_file("C:\\" + "a" * 512)

    # 5. PeerServerHandler echoes req_opcode and req_id in 24-byte 0xA1 reply
    mapper = PathMapper(virtual_root=str(tmp_path))
    handler = PeerServerHandler(path_mapper=mapper)
    victim = tmp_path / "victim.txt"
    victim.write_text("bye", encoding="utf-8")

    reps = handler.handle_command(encode_delete_file("C:\\victim.txt", req_id=77))
    assert len(reps) == 1
    assert len(reps[0]) == 24
    rep = decode_reply(reps[0])
    assert rep["opcode"] == REPLY_STATUS_ACK
    assert rep["req_opcode"] == OP_DELETE_FILE
    assert rep["req_id"] == 77
    assert rep["status"] == STATUS_OK
    assert not victim.exists()

    # Deleting a missing file returns status=1 (ERROR) with req_id echoed
    reps_err = handler.handle_command(encode_delete_file("C:\\missing.txt", req_id=78))
    rep_err = decode_reply(reps_err[0])
    assert rep_err["opcode"] == REPLY_STATUS_ACK
    assert rep_err["req_opcode"] == OP_DELETE_FILE
    assert rep_err["req_id"] == 78
    assert rep_err["status"] == STATUS_ERROR
    assert rep_err["win32_error"] == 2


def test_peer_server_handler_batch_and_posix_mode(tmp_path):
    """Verify PeerServerHandler supports 0x01 POSIX mode (0x20), 0x03, batch ops, 0x37, and 0x38."""
    mapper = PathMapper(virtual_root=str(tmp_path))
    handler = PeerServerHandler(path_mapper=mapper)

    # 0x01 with flags=0x20 sets POSIX peer mode and returns 0xA1 + 0xA4 (GetDriveTypeA - 1)
    reps_01 = handler.handle_command(encode_query_root(flags=PEER_MODE_POSIX))
    assert len(reps_01) == 2
    assert handler.peer_mode == PEER_MODE_POSIX
    r_roots = decode_reply(reps_01[1])
    assert r_roots["roots"][0] == ("C", 2)

    # 0x03 without active dir -> status=3
    reps_03_err = handler.handle_command(encode_refresh_dir())
    assert decode_reply(reps_03_err[0])["status"] == STATUS_EXISTS

    # Set active dir via 0x02, then 0x03 -> 0xA1(status=0) + 0xA2
    (tmp_path / "hello.txt").write_bytes(b"abc")
    handler.handle_command(encode_notify_active_dir("C:\\"))
    reps_03_ok = handler.handle_command(encode_refresh_dir())
    assert len(reps_03_ok) == 2
    assert decode_reply(reps_03_ok[0])["status"] == STATUS_OK
    assert decode_reply(reps_03_ok[1])["entries"][0].filename == "hello.txt"

    # 0x15 Create Batch Zero-Length Files
    reps_15 = handler.handle_command(encode_create_batch_zero_files([(1, "C:\\empty.bin")], req_id=3))
    assert decode_reply(reps_15[0])["status"] == STATUS_OK
    assert (tmp_path / "empty.bin").read_bytes() == b""

    # 0x13 & 0x16 Batch Precheck
    reps_13 = handler.handle_command(
        encode_precheck_batch([(1, "C:\\hello.txt"), (2, "C:\\new.txt")], req_id=4, total_size=10)
    )
    dec_13 = decode_reply(reps_13[0])
    assert dec_13["conflict_item_ids"] == [1]
    assert dec_13["insufficient_space"] is False

    reps_16 = handler.handle_command(
        encode_precheck_batch_ext([(1, "C:\\hello.txt"), (2, "C:\\new.txt")], req_id=5, total_size=10)
    )
    assert decode_reply(reps_16[0])["conflict_item_ids"] == [1]

    # 0x10 / 0x35 Batch Download: 0xA3 chunks + 0xA6 empty + 0xAB unreadable + 0xA7 done
    reps_10 = handler.handle_command(
        encode_download_batch([(1, "C:\\hello.txt"), (2, "C:\\empty.bin"), (3, "C:\\missing.txt")], req_id=6)
    )
    decoded_10 = [decode_reply(r) for r in reps_10]
    assert decoded_10[0]["opcode"] == REPLY_DOWNLOAD_CHUNK
    assert decoded_10[0]["item_id"] == 1
    assert decoded_10[0]["data"] == b"abc"
    assert decoded_10[1] == {"opcode": REPLY_DOWNLOAD_BATCH_EMPTY, "req_id": 6, "item_ids": [2]}
    assert decoded_10[2] == {"opcode": REPLY_DOWNLOAD_UNREADABLE, "req_id": 6, "item_ids": [3]}
    assert decoded_10[3] == {"opcode": REPLY_DOWNLOAD_BATCH_DONE, "req_id": 6, "status": 0}

    # 0x37 Set File Attributes & Timestamps (fire-and-forget -> no reply)
    assert handler.handle_command(encode_set_file_attrs("C:\\hello.txt", 0x20, 0, 0, 0)) == []

    # 0x12 Batch Clear Read-Only -> 0xA1(status=0)
    reps_12 = handler.handle_command(encode_batch_clear_readonly([(1, "C:\\hello.txt")], req_id=7))
    assert decode_reply(reps_12[0])["status"] == STATUS_OK

    # 0x38 Format Errors -> 0xAC (UTF-16LE, strerror in POSIX mode)
    reps_38 = handler.handle_command(encode_format_win32_errors([2]))
    dec_38 = decode_reply(reps_38[0])
    assert dec_38["opcode"] == REPLY_FORMATTED_ERROR
    assert dec_38["errors"][0][0] == 2
    assert len(dec_38["errors"][0][1]) > 0
