# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for wire protocol codecs, commands, replies, XML, and path mapping."""

from openeasysync.constants import (
    OP_CANCEL_TRANSFER,
    OP_CREATE_DIR,
    OP_DELETE_DIR,
    OP_DELETE_FILE,
    OP_DOWNLOAD_BATCH,
    OP_DOWNLOAD_SINGLE,
    OP_LIST_DIR,
    OP_PRECHECK_BATCH,
    OP_PRECHECK_BATCH_EXT,
    OP_PRECHECK_SINGLE,
    OP_QUERY_ERRORS,
    OP_QUERY_ROOT,
    OP_QUERY_SPACE,
    OP_RENAME_DIR,
    OP_RENAME_FILE,
    OP_SET_MAC_MODE,
    OP_UPLOAD_BATCH,
    OP_UPLOAD_SINGLE,
    OP_XML_CONTROL,
    REPLY_DOWNLOAD_BATCH_DONE,
    REPLY_DOWNLOAD_BATCH_EMPTY,
    REPLY_DOWNLOAD_BATCH_STREAM,
    REPLY_DOWNLOAD_SINGLE,
    REPLY_ERROR_CODE,
    REPLY_LISTING,
    REPLY_PRECHECK_BATCH,
    REPLY_PRECHECK_BATCH_EXT,
    REPLY_ROOTS,
    REPLY_STATUS_ACK,
    STATUS_OK,
)
from openeasysync.protocol.commands import (
    BatchPrecheckExtRecord,
    BatchUploadRecord,
    decode_command,
    encode_cancel_transfer,
    encode_create_dir,
    encode_delete_dir,
    encode_delete_file,
    encode_download_batch,
    encode_download_single,
    encode_list_dir,
    encode_precheck_batch,
    encode_precheck_batch_ext,
    encode_precheck_single,
    encode_query_errors,
    encode_query_root,
    encode_query_space,
    encode_rename_dir,
    encode_rename_file,
    encode_set_mac_mode,
    encode_upload_batch,
    encode_upload_single,
    encode_xml_control,
)
from openeasysync.protocol.directory_entry import Win32FindData, make_file_entry
from openeasysync.protocol.path_mapping import PathMapper
from openeasysync.protocol.replies import (
    BatchDownloadEmptyRecord,
    BatchDownloadStreamRecord,
    BatchPrecheckExtConflict,
    decode_reply,
    encode_download_batch_done,
    encode_download_batch_empty,
    encode_download_batch_stream,
    encode_download_single_reply,
    encode_error_code,
    encode_listing_reply,
    encode_precheck_batch_ext_reply,
    encode_precheck_batch_reply,
    encode_roots_reply,
    encode_status_ack,
)
from openeasysync.protocol.xml_msg import build_oti_msg, parse_oti_msg


def test_command_encoders_and_decoders():
    """Verify all Client Commands encode and decode correctly."""
    # 0x01 Query Root (default path is "\" for EasySuite.exe compatibility)
    raw01 = encode_query_root()
    assert len(raw01) == 6
    assert raw01 == bytes([OP_QUERY_ROOT, 0x00, 0x00, 0x00, 0x01, ord("\\")])
    c01 = decode_command(raw01)
    assert c01["opcode"] == OP_QUERY_ROOT
    assert c01["path"] == "\\"

    # 0x01 Query Root with empty path
    raw01_empty = encode_query_root("")
    assert len(raw01_empty) == 5
    assert raw01_empty == bytes([OP_QUERY_ROOT, 0x00, 0x00, 0x00, 0x00])
    c01_empty = decode_command(raw01_empty)
    assert c01_empty["opcode"] == OP_QUERY_ROOT
    assert c01_empty["path"] == ""

    # Test backwards-compatible 1-byte decode
    assert decode_command(bytes([OP_QUERY_ROOT]))["opcode"] == OP_QUERY_ROOT

    # 0x02 List Dir
    c02 = decode_command(encode_list_dir("C:\\Users\\Guest"))
    assert c02["opcode"] == OP_LIST_DIR
    assert c02["path"] == "C:\\Users\\Guest"

    # 0x03 Delete File
    c03 = decode_command(encode_delete_file("C:\\file.txt"))
    assert c03["opcode"] == OP_DELETE_FILE
    assert c03["path"] == "C:\\file.txt"

    # 0x04 Delete Dir
    c04 = decode_command(encode_delete_dir("C:\\Folder"))
    assert c04["opcode"] == OP_DELETE_DIR
    assert c04["path"] == "C:\\Folder"

    # 0x06 Create Dir
    c06 = decode_command(encode_create_dir("C:\\NewFolder"))
    assert c06["opcode"] == OP_CREATE_DIR
    assert c06["path"] == "C:\\NewFolder"

    # 0x07 Rename File
    c07 = decode_command(encode_rename_file("old.txt", "new.txt"))
    assert c07["opcode"] == OP_RENAME_FILE
    assert c07["old_path"] == "old.txt"
    assert c07["new_path"] == "new.txt"

    # 0x08 Rename Dir
    c08 = decode_command(encode_rename_dir("old_dir", "new_dir"))
    assert c08["opcode"] == OP_RENAME_DIR

    # 0x09 Upload Single
    fd = Win32FindData(filename="doc.txt", file_size=12)
    c09 = decode_command(encode_upload_single("C:\\doc.txt", fd, b"Hello World!"))
    assert c09["opcode"] == OP_UPLOAD_SINGLE
    assert c09["dest_path"] == "C:\\doc.txt"
    assert c09["file_data"] == b"Hello World!"
    assert c09["find_data"].filename == "doc.txt"

    # 0x0A Download Single
    c0a = decode_command(encode_download_single("C:\\doc.txt"))
    assert c0a["opcode"] == OP_DOWNLOAD_SINGLE
    assert c0a["path"] == "C:\\doc.txt"

    # 0x0B Batch Upload
    rec = BatchUploadRecord(rel_path="sub\\file.bin", find_data=fd, data=b"BIN_DATA")
    c0b = decode_command(encode_upload_batch("C:\\Base", [rec]))
    assert c0b["opcode"] == OP_UPLOAD_BATCH
    assert c0b["base_dir"] == "C:\\Base"
    assert len(c0b["records"]) == 1
    assert c0b["records"][0].rel_path == "sub\\file.bin"
    assert c0b["records"][0].data == b"BIN_DATA"

    # 0x0C Batch Download
    c0c = decode_command(encode_download_batch(["C:\\p1", "C:\\p2"]))
    assert c0c["opcode"] == OP_DOWNLOAD_BATCH
    assert c0c["paths"] == ["C:\\p1", "C:\\p2"]

    # 0x0D Cancel Transfer
    c0d = decode_command(encode_cancel_transfer())
    assert c0d["opcode"] == OP_CANCEL_TRANSFER

    # 0x0E Query Space
    c0e = decode_command(encode_query_space("C:\\"))
    assert c0e["opcode"] == OP_QUERY_SPACE

    # 0x0F Precheck Single
    c0f = decode_command(encode_precheck_single("C:\\file.txt"))
    assert c0f["opcode"] == OP_PRECHECK_SINGLE

    # 0x10 Mac Mode
    c10 = decode_command(encode_set_mac_mode())
    assert c10["opcode"] == OP_SET_MAC_MODE

    # 0x14 Batch Precheck
    c14 = decode_command(encode_precheck_batch("C:\\Base", ["f1.txt", "f2.txt"]))
    assert c14["opcode"] == OP_PRECHECK_BATCH
    assert c14["dest_dir"] == "C:\\Base"
    assert c14["rel_paths"] == ["f1.txt", "f2.txt"]

    # 0x15 Batch Precheck Ext
    c15 = decode_command(
        encode_precheck_batch_ext("C:\\Base", [BatchPrecheckExtRecord("f1.txt", fd)])
    )
    assert c15["opcode"] == OP_PRECHECK_BATCH_EXT
    assert len(c15["records"]) == 1

    # 0x16 XML Control
    c16 = decode_command(encode_xml_control("<OTIMSG></OTIMSG>"))
    assert c16["opcode"] == OP_XML_CONTROL
    assert c16["xml"] == "<OTIMSG></OTIMSG>"

    # 0x17 Query Errors
    c17 = decode_command(encode_query_errors([5, 112]))
    assert c17["opcode"] == OP_QUERY_ERRORS
    assert c17["error_codes"] == [5, 112]


def test_reply_encoders_and_decoders():
    """Verify all Server Replies encode and decode correctly."""
    # 0xA1 Status ACK
    r_a1 = decode_reply(encode_status_ack(STATUS_OK))
    assert r_a1["opcode"] == REPLY_STATUS_ACK and r_a1["status"] == STATUS_OK

    # 0xA2 Error Code
    r_a2 = decode_reply(encode_error_code(112))
    assert r_a2["opcode"] == REPLY_ERROR_CODE and r_a2["win32_error"] == 112

    # 0xA3 Listing
    entry = make_file_entry("sample.txt", size=500)
    r_a3 = decode_reply(encode_listing_reply([entry]))
    assert r_a3["opcode"] == REPLY_LISTING
    assert len(r_a3["entries"]) == 1
    assert r_a3["entries"][0].find_data.filename == "sample.txt"

    # 0xA4 Roots Reply
    r_roots = decode_reply(encode_roots_reply([("C", 3), ("D", 2)]))
    assert r_roots["opcode"] == REPLY_ROOTS
    assert r_roots["roots"] == [("C", 3), ("D", 2)]

    # 0xA4 Single Download Reply
    fd = Win32FindData(filename="sample.txt", file_size=4)
    r_a4 = decode_reply(encode_download_single_reply(fd, b"TEST"))
    assert r_a4["opcode"] == REPLY_DOWNLOAD_SINGLE
    assert r_a4["file_data"] == b"TEST"

    # 0xA5 Batch Download Stream
    rec = BatchDownloadStreamRecord(rel_path="a\\b.txt", find_data=fd, data=b"DATA")
    r_a5 = decode_reply(encode_download_batch_stream([rec]))
    assert r_a5["opcode"] == REPLY_DOWNLOAD_BATCH_STREAM
    assert len(r_a5["records"]) == 1

    # 0xA6 Batch Download Empty
    rec_empty = BatchDownloadEmptyRecord(rel_path="empty_folder", find_data=fd)
    r_a6 = decode_reply(encode_download_batch_empty([rec_empty]))
    assert r_a6["opcode"] == REPLY_DOWNLOAD_BATCH_EMPTY

    # 0xA7 Batch Download Done
    r_a7 = decode_reply(encode_download_batch_done())
    assert r_a7["opcode"] == REPLY_DOWNLOAD_BATCH_DONE

    # 0xA8 Precheck Batch Reply
    r_a8 = decode_reply(encode_precheck_batch_reply(["conf1.txt", "conf2.txt"]))
    assert r_a8["opcode"] == REPLY_PRECHECK_BATCH
    assert r_a8["conflicts"] == ["conf1.txt", "conf2.txt"]

    # 0xAA Precheck Batch Ext Reply
    conflict = BatchPrecheckExtConflict("c.txt", fd, fd)
    r_aa = decode_reply(encode_precheck_batch_ext_reply([conflict]))
    assert r_aa["opcode"] == REPLY_PRECHECK_BATCH_EXT
    assert len(r_aa["conflicts"]) == 1


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
