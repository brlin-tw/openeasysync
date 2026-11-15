# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for wire protocol codecs, commands, replies, XML, and path mapping."""

import struct

import pytest

from openeasysync.constants import (
    OP_CANCEL_TRANSFER,
    OP_CREATE_DIR,
    OP_DELETE_DIR,
    OP_DELETE_FILE,
    OP_DOWNLOAD_BATCH,
    OP_DOWNLOAD_SINGLE,
    OP_FILE_CHUNK,
    OP_LIST_DIR,
    OP_NOTIFY_ACTIVE_DIR,
    OP_PRECHECK_BATCH,
    OP_PRECHECK_BATCH_EXT,
    OP_PRECHECK_SINGLE,
    OP_QUERY_ERRORS,
    OP_QUERY_ROOT,
    OP_QUERY_SPACE,
    OP_RENAME_DIR,
    OP_RENAME_FILE,
    OP_TRANSFER_COMPLETE,
    OP_UPLOAD_SINGLE,
    OP_VERIFY_PEER_NAME,
    OP_VNIC_SWITCH,
    OP_XML_CONTROL,
    REPLY_CHUNK_ACK,
    REPLY_DOWNLOAD_BATCH_DONE,
    REPLY_DOWNLOAD_BATCH_EMPTY,
    REPLY_DOWNLOAD_BATCH_STREAM,
    REPLY_DOWNLOAD_SINGLE,
    REPLY_LISTING,
    REPLY_PRECHECK_BATCH,
    REPLY_PRECHECK_BATCH_EXT,
    REPLY_ROOTS,
    REPLY_STATUS_ACK,
    STATUS_ERROR,
    STATUS_OK,
)
from openeasysync.protocol.commands import (
    BatchPrecheckExtRecord,
    decode_command,
    encode_cancel_transfer,
    encode_create_dir,
    encode_delete_dir,
    encode_delete_file,
    encode_download_batch,
    encode_download_single,
    encode_extended_xml,
    encode_file_chunk,
    encode_list_dir,
    encode_notify_active_dir,
    encode_precheck_batch,
    encode_precheck_batch_ext,
    encode_precheck_single,
    encode_query_errors,
    encode_query_root,
    encode_query_space,
    encode_rename_dir,
    encode_rename_file,
    encode_transfer_complete,
    encode_upload_single,
    encode_verify_peer_name,
    encode_xml_control,
)
from openeasysync.protocol.directory_entry import Win32FindData, make_file_entry
from openeasysync.protocol.path_mapping import PathMapper
from openeasysync.protocol.replies import (
    BatchDownloadEmptyRecord,
    BatchDownloadStreamRecord,
    BatchPrecheckExtConflict,
    decode_reply,
    encode_chunk_ack,
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
from openeasysync.server.handler import PeerServerHandler


def test_command_encoders_and_decoders():
    """Verify all Client Commands encode and decode correctly."""
    # 0x01 Query Root (11 bytes total for default host query)
    raw01 = encode_query_root()
    assert len(raw01) == 11
    assert raw01 == bytes([OP_QUERY_ROOT, 0x07, 0xE4, 0x04, 0x06]) + b"TXLIN1"
    c01 = decode_command(raw01)
    assert c01["opcode"] == OP_QUERY_ROOT
    assert c01["computer_name"] == "TXLIN1"
    assert c01["flags"] == 0x07
    assert c01["code_page"] == 1252

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
    fd = Win32FindData(filename="doc.txt", file_size=12)
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

    # 0xA5 Upload File Data Chunk ACK
    raw_a5_ack = encode_chunk_ack(item_id=1, chunk_index=1)
    assert len(raw_a5_ack) == 9
    r_a5_ack = decode_reply(raw_a5_ack)
    assert r_a5_ack["opcode"] == REPLY_CHUNK_ACK
    assert r_a5_ack["item_id"] == 1
    assert r_a5_ack["chunk_index"] == 1

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
    c10 = decode_command(encode_download_batch(["C:\\p1", "C:\\p2"]))
    assert c10["opcode"] == OP_DOWNLOAD_BATCH
    assert c10["paths"] == ["C:\\p1", "C:\\p2"]

    # 0x0D Cancel Transfer
    c0d = decode_command(encode_cancel_transfer())
    assert c0d["opcode"] == OP_CANCEL_TRANSFER

    # 0x0E Query Space
    c0e = decode_command(encode_query_space("C:\\"))
    assert c0e["opcode"] == OP_QUERY_SPACE

    # 0x0F Precheck Single
    c0f = decode_command(encode_precheck_single("C:\\file.txt"))
    assert c0f["opcode"] == OP_PRECHECK_SINGLE

    # 0x13 Batch Precheck
    c13 = decode_command(encode_precheck_batch("C:\\Base", ["f1.txt", "f2.txt"]))
    assert c13["opcode"] == OP_PRECHECK_BATCH
    assert c13["dest_dir"] == "C:\\Base"
    assert c13["rel_paths"] == ["f1.txt", "f2.txt"]

    # 0x16 Batch Precheck Ext
    c16 = decode_command(
        encode_precheck_batch_ext("C:\\Base", [BatchPrecheckExtRecord("f1.txt", fd)])
    )
    assert c16["opcode"] == OP_PRECHECK_BATCH_EXT
    assert len(c16["records"]) == 1

    # 0x34 XML Control (<OTIMSG>)
    c34 = decode_command(encode_xml_control("<OTIMSG></OTIMSG>"))
    assert c34["opcode"] == OP_XML_CONTROL
    assert c34["xml"] == "<OTIMSG></OTIMSG>"

    # 0x17 Query Errors
    c17 = decode_command(encode_query_errors([5, 112]))
    assert c17["opcode"] == OP_QUERY_ERRORS
    assert c17["error_codes"] == [5, 112]

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
    # Unpack status (uint32_le at offset +0x05)
    status = struct.unpack_from("<I", replies_zero[0], 5)[0]
    assert status == 7
    assert (tmp_path / "empty.txt").exists()

    # 2. 0x0A with existing file and overwrite=False -> conflict status=2
    cmd_conflict = encode_upload_single("C:\\empty.txt", file_size=10, req_id=11, overwrite=False)
    replies_conflict = handler.handle_command(cmd_conflict)
    assert len(replies_conflict) == 1
    assert struct.unpack_from("<I", replies_conflict[0], 5)[0] == 2

    # 3. 0x0A with overwrite=True -> succeeds status=0
    cmd_ow = encode_upload_single("C:\\file.txt", file_size=11, req_id=12, overwrite=True)
    replies_ow = handler.handle_command(cmd_ow)
    assert len(replies_ow) == 1
    assert struct.unpack_from("<I", replies_ow[0], 5)[0] == 0

    # 4. Stream 0x0C chunk -> returns 9-byte 0xA5 ACK
    chunk_data = b"Hello World"
    cmd_chunk = encode_file_chunk(item_id=12, chunk_index=1, total_chunks=1, chunk_data=chunk_data)
    replies_chunk = handler.handle_command(cmd_chunk)
    assert len(replies_chunk) == 1
    assert len(replies_chunk[0]) == 9
    assert replies_chunk[0][0] == 0xA5
    dec_ack = decode_reply(replies_chunk[0])
    assert dec_ack["opcode"] == 0xA5
    assert dec_ack["item_id"] == 12
    assert dec_ack["chunk_index"] == 1

    # 5. Finalize via 0x0E -> closes handle, returns no replies
    cmd_fin = encode_transfer_complete(item_id=12, status=0)
    replies_fin = handler.handle_command(cmd_fin)
    assert replies_fin == []
    assert (tmp_path / "file.txt").read_bytes() == b"Hello World"
