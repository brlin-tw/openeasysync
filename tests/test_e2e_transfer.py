# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end integration tests between two simulated hosts using MockLoopbackTransport."""

import hashlib

import pytest

from openeasysync.client.session import SessionManager
from openeasysync.client.transfer import TransferManager
from openeasysync.protocol.path_mapping import PathMapper
from openeasysync.transport.mock import MockBridgeCable


def sha256_of_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture
def dual_host_setup(tmp_path):
    """Sets up a simulated two-host connection over MockBridgeCable with isolated filesystems."""
    dir_a = tmp_path / "host_a"
    dir_b = tmp_path / "host_b"
    dir_a.mkdir()
    dir_b.mkdir()

    cable = MockBridgeCable()
    transport_a, transport_b = cable.create_pair()

    mapper_a = PathMapper(virtual_root=str(dir_a))
    mapper_b = PathMapper(virtual_root=str(dir_b))

    session_a = SessionManager(transport_a, path_mapper=mapper_a)
    session_b = SessionManager(transport_b, path_mapper=mapper_b)

    # Connect both peers
    assert session_b.connect() is True
    assert session_a.connect() is True

    transfer_a = TransferManager(session_a)
    transfer_b = TransferManager(session_b)

    yield {
        "dir_a": dir_a,
        "dir_b": dir_b,
        "session_a": session_a,
        "session_b": session_b,
        "transfer_a": transfer_a,
        "transfer_b": transfer_b,
    }

    session_a.disconnect()
    session_b.disconnect()


def test_e2e_query_roots_and_list_dir(dual_host_setup):
    """Verify Host A can query roots and list directory on Host B."""
    dir_b = dual_host_setup["dir_b"]
    transfer_a = dual_host_setup["transfer_a"]

    # Create dummy files and subdirs on Host B
    (dir_b / "sample1.txt").write_text("Hello from Host B!")
    sub = dir_b / "subfolder"
    sub.mkdir()
    (sub / "nested.dat").write_bytes(b"\x00\x01\x02\x03")

    # 1. Query roots
    roots = transfer_a.query_roots()
    assert len(roots) >= 1
    assert roots[0].is_drive is True

    # 2. List root directory
    entries = transfer_a.list_dir("C:\\")
    names = [e.find_data.filename for e in entries]
    assert "sample1.txt" in names
    assert "subfolder" in names

    # 3. List subfolder
    sub_entries = transfer_a.list_dir("C:\\subfolder")
    sub_names = [e.find_data.filename for e in sub_entries]
    assert "nested.dat" in sub_names

    # 4. Notify active directory
    assert transfer_a.notify_active_dir("C:\\subfolder") is True
    assert dual_host_setup["session_b"].server_handler.peer_active_dir == "C:\\subfolder"


def test_e2e_single_file_upload_and_download(dual_host_setup):
    """Verify single file upload (A -> B) and download (B -> A) with checksum verification."""
    dir_a = dual_host_setup["dir_a"]
    dir_b = dual_host_setup["dir_b"]
    transfer_a = dual_host_setup["transfer_a"]

    # 1. Create a large test file on Host A (150 KB, spans 3 bulk blocks)
    src_file_a = dir_a / "test_large_upload.bin"
    payload = b"OTI_2108_TEST_PAYLOAD_" * 7000  # ~154 KB
    src_file_a.write_bytes(payload)
    original_hash = sha256_of_file(str(src_file_a))

    # Precheck before upload: should NOT exist yet
    assert transfer_a.precheck_single("C:\\uploaded.bin") is False

    # Upload A -> B
    success = transfer_a.upload_file(str(src_file_a), "C:\\uploaded.bin")
    assert success is True

    # Verify file arrived on Host B
    dest_file_b = dir_b / "uploaded.bin"
    assert dest_file_b.exists()
    assert sha256_of_file(str(dest_file_b)) == original_hash

    # Precheck after upload: should now report EXISTS
    assert transfer_a.precheck_single("C:\\uploaded.bin") is True

    # 2. Download B -> A
    download_dest_a = dir_a / "downloaded_copy.bin"
    dl_success = transfer_a.download_file("C:\\uploaded.bin", str(download_dest_a))
    assert dl_success is True
    assert download_dest_a.exists()
    assert sha256_of_file(str(download_dest_a)) == original_hash


def test_e2e_batch_upload_and_download(dual_host_setup):
    """Verify batch upload and download with nested directories and empty files."""
    dir_a = dual_host_setup["dir_a"]
    dir_b = dual_host_setup["dir_b"]
    transfer_a = dual_host_setup["transfer_a"]

    # Construct directory structure on Host A
    batch_dir = dir_a / "my_project"
    batch_dir.mkdir()
    (batch_dir / "empty.txt").write_bytes(b"")
    (batch_dir / "data.csv").write_text("id,val\n1,100\n2,200\n")
    sub = batch_dir / "assets"
    sub.mkdir()
    (sub / "image.raw").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50000)

    # Batch Upload A -> B
    success = transfer_a.upload_batch([str(batch_dir)], "C:\\target_project")
    assert success is True

    # Verify structure on Host B
    b_proj = dir_b / "target_project" / "my_project"
    assert b_proj.exists()
    assert (b_proj / "empty.txt").exists()
    assert (b_proj / "data.csv").read_text() == "id,val\n1,100\n2,200\n"
    assert (b_proj / "assets" / "image.raw").stat().st_size == 50004

    # Batch Download B -> A into fresh folder
    dest_a_dir = dir_a / "restored_batch"
    dl_success = transfer_a.download_batch(["C:\\target_project"], str(dest_a_dir))
    assert dl_success is True

    # Check restored files
    restored_file = dest_a_dir / "target_project" / "my_project" / "data.csv"
    assert restored_file.exists()
    assert restored_file.read_text() == "id,val\n1,100\n2,200\n"


def test_e2e_transfer_cancel(dual_host_setup):
    """Verify Opcode 0x0D cancel signal executes cleanly."""
    transfer_a = dual_host_setup["transfer_a"]
    res = transfer_a.cancel_transfer()
    assert res is True


def test_query_roots_wire_format_reply_roots():
    """Verify TransferManager.query_roots parses 0xA4 roots reply correctly."""
    from unittest.mock import MagicMock

    from openeasysync.constants import FILE_ATTRIBUTE_DIRECTORY
    from openeasysync.protocol.replies import encode_roots_reply

    mock_session = MagicMock()
    mock_session.send_command.return_value = encode_roots_reply([("C", 3), ("D", 2)])
    transfer = TransferManager(mock_session)

    roots = transfer.query_roots()
    assert len(roots) == 2
    assert roots[0].find_data.filename == "C:\\"
    assert roots[0].find_data.file_attributes == FILE_ATTRIBUTE_DIRECTORY
    assert roots[0].is_drive is True
    assert roots[0].drive_type == 3

    assert roots[1].find_data.filename == "D:\\"
    assert roots[1].drive_type == 2


def test_session_wait_settled(dual_host_setup):
    """Verify SessionManager.wait_settled monitors link quieting."""
    session_a = dual_host_setup["session_a"]

    # When quiescent, wait_settled finishes quickly
    settled = session_a.wait_settled(quiet_interval_s=0.05, timeout_s=0.5, min_settle_s=0.02)
    assert settled is True


def test_e2e_list_dir_error_handling(dual_host_setup):
    """Verify listing an invalid directory returns formatted Win32 error immediately without timeout."""
    transfer_a = dual_host_setup["transfer_a"]

    with pytest.raises(OSError) as exc_info:
        transfer_a.list_dir("C:\\nonexistent_dir")
    assert "The system cannot find the path specified." in str(exc_info.value)
    assert "C:\\nonexistent_dir" in str(exc_info.value)


def test_e2e_single_file_upload_zero_length(dual_host_setup):
    """Verify single file upload of a 0-byte file (A -> B) completes via status 7."""
    dir_a = dual_host_setup["dir_a"]
    dir_b = dual_host_setup["dir_b"]
    transfer_a = dual_host_setup["transfer_a"]

    empty_src = dir_a / "empty_source.txt"
    empty_src.write_bytes(b"")

    success = transfer_a.upload_file(str(empty_src), "C:\\empty_dest.txt")
    assert success is True

    dest_file = dir_b / "empty_dest.txt"
    assert dest_file.exists()
    assert dest_file.stat().st_size == 0


def test_e2e_single_file_upload_conflict(dual_host_setup):
    """Verify upload conflict returns error when overwrite=False and succeeds when overwrite=True."""
    dir_a = dual_host_setup["dir_a"]
    dir_b = dual_host_setup["dir_b"]
    transfer_a = dual_host_setup["transfer_a"]

    src = dir_a / "conflict.txt"
    src.write_text("New content from A")

    # Pre-create file on Host B
    existing_b = dir_b / "conflict.txt"
    existing_b.write_text("Old content on B")

    # Upload with overwrite=False should raise FileExistsError
    with pytest.raises(FileExistsError):
        transfer_a.upload_file(str(src), "C:\\conflict.txt", overwrite=False)

    # Content on B should be untouched
    assert existing_b.read_text() == "Old content on B"

    # Upload with overwrite=True should succeed
    success = transfer_a.upload_file(str(src), "C:\\conflict.txt", overwrite=True)
    assert success is True
    assert existing_b.read_text() == "New content from A"
