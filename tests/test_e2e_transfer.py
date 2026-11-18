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

    # Upload A -> B
    success = transfer_a.upload_file(str(src_file_a), "C:\\uploaded.bin")
    assert success is True
    dual_host_setup["session_b"].wait_settled(quiet_interval_s=0.05, timeout_s=2.0, min_settle_s=0.02)

    # Verify file arrived on Host B
    dest_file_b = dir_b / "uploaded.bin"
    assert dest_file_b.exists()
    assert sha256_of_file(str(dest_file_b)) == original_hash

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
    dual_host_setup["session_b"].wait_settled(quiet_interval_s=0.05, timeout_s=2.0, min_settle_s=0.02)

    # Verify structure on Host B
    b_proj = dir_b / "target_project" / "my_project"
    assert b_proj.exists()
    assert (b_proj / "empty.txt").exists()
    assert (b_proj / "data.csv").read_text() == "id,val\n1,100\n2,200\n"
    assert (b_proj / "assets" / "image.raw").stat().st_size == 50004

    # Batch Upload with conflict_resolver (0x16 -> 0xAA) on existing target
    conflicts_seen: list[str] = []

    def _resolve_conflict(rel_path: str, _peer_fd: object) -> str:
        conflicts_seen.append(rel_path)
        return "overwrite"

    assert transfer_a.upload_batch(
        [str(batch_dir)], "C:\\target_project", conflict_resolver=_resolve_conflict
    )
    assert len(conflicts_seen) > 0

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
    dual_host_setup["session_b"].wait_settled(quiet_interval_s=0.05, timeout_s=2.0, min_settle_s=0.02)
    assert existing_b.read_text() == "New content from A"


def test_e2e_download_small_file_inline(dual_host_setup):
    """Verify single file download of a small file (<= 64,512 bytes) using inline delivery (status=9)."""
    dir_a = dual_host_setup["dir_a"]
    dir_b = dual_host_setup["dir_b"]
    transfer_a = dual_host_setup["transfer_a"]

    small_b = dir_b / "small_inline.txt"
    content = b"Inline small payload test string!" * 50
    small_b.write_bytes(content)

    dest_a = dir_a / "downloaded_inline.txt"
    progress_calls: list[tuple[int, int]] = []
    success = transfer_a.download_file(
        "C:\\small_inline.txt",
        str(dest_a),
        progress_callback=lambda cur, tot: progress_calls.append((cur, tot)),
    )
    assert success is True
    assert dest_a.exists()
    assert dest_a.read_bytes() == content
    assert progress_calls == [(len(content), len(content))]


def test_e2e_download_zero_length(dual_host_setup):
    """Verify single file download of a 0-byte file completing immediately (status=7)."""
    dir_a = dual_host_setup["dir_a"]
    dir_b = dual_host_setup["dir_b"]
    transfer_a = dual_host_setup["transfer_a"]

    empty_b = dir_b / "empty_remote.bin"
    empty_b.write_bytes(b"")

    dest_a = dir_a / "downloaded_empty.bin"
    success = transfer_a.download_file("C:\\empty_remote.bin", str(dest_a))
    assert success is True
    assert dest_a.exists()
    assert dest_a.stat().st_size == 0


def test_e2e_download_missing_file_raises_filenotfound(dual_host_setup):
    """Verify download of nonexistent file on peer raises FileNotFoundError with Win32 error."""
    dir_a = dual_host_setup["dir_a"]
    transfer_a = dual_host_setup["transfer_a"]

    dest_a = dir_a / "should_not_exist.bin"
    with pytest.raises(FileNotFoundError, match="Download failed on peer with Win32 error 2"):
        transfer_a.download_file("C:\\does_not_exist.dat", str(dest_a))
    assert not dest_a.exists()


def test_upload_streams_chunks_without_waiting_for_replies(tmp_path):
    """Verify 0x0A awaits 0xA1, while 0x0C chunks and 0x0E are sent asynchronously via send_packet."""
    from unittest.mock import MagicMock

    from openeasysync.constants import OP_FILE_CHUNK, OP_TRANSFER_COMPLETE, OP_UPLOAD_SINGLE
    from openeasysync.protocol.replies import encode_status_ack

    src = tmp_path / "stream_test.bin"
    src.write_bytes(b"A" * 130000)  # 3 chunks at 64000 bytes

    mock_session = MagicMock()
    mock_session.send_command.return_value = encode_status_ack(status=0, req_opcode=OP_UPLOAD_SINGLE, req_id=1)
    transfer = TransferManager(mock_session)

    assert transfer.upload_file(str(src), "C:\\stream_test.bin", overwrite=True) is True

    # Only 0x0A should use send_command
    assert mock_session.send_command.call_count == 1
    assert mock_session.send_command.call_args.args[0][0] == OP_UPLOAD_SINGLE

    # 3 chunks (0x0C) + 1 completion (0x0E) sent via send_packet
    sent_opcodes = [call.args[0][0] for call in mock_session.send_packet.call_args_list]
    assert sent_opcodes == [OP_FILE_CHUNK, OP_FILE_CHUNK, OP_FILE_CHUNK, OP_TRANSFER_COMPLETE]
    mock_session.flush_tx.assert_called_once()


def test_session_catches_async_0xa5_and_0xa9_and_aborts_upload(tmp_path):
    """Verify SessionManager catches async 0xA5 and 0xA9 replies and aborts the upload pipeline."""
    from unittest.mock import MagicMock

    from openeasysync.constants import OP_FILE_CHUNK, OP_TRANSFER_COMPLETE, OP_UPLOAD_SINGLE
    from openeasysync.protocol.replies import (
        encode_status_ack,
        encode_upload_batch_error,
        encode_upload_error,
    )

    transport = MagicMock()
    session = SessionManager(transport)
    transfer = TransferManager(session)

    src = tmp_path / "abort_test.bin"
    src.write_bytes(b"B" * 130000)  # 3 chunks

    # 1. Single-file upload error 0xA5 arriving after chunk 1
    session.send_command = MagicMock(
        return_value=encode_status_ack(status=0, req_opcode=OP_UPLOAD_SINGLE, req_id=1)
    )
    sent_packets: list[bytes] = []

    def _fake_send_packet_a5(pkt: bytes) -> None:
        sent_packets.append(pkt)
        if pkt[0] == OP_FILE_CHUNK and len(sent_packets) == 1:
            session._route_message(encode_upload_error(item_id=1, win32_error=112))

    session.send_packet = _fake_send_packet_a5  # type: ignore[method-assign]

    with pytest.raises(OSError, match="Upload write error"):
        transfer.upload_file(str(src), "C:\\abort_test.bin", overwrite=True)

    # Pipeline aborted after chunk 1; 0x0E was never sent
    assert len(sent_packets) == 1
    assert sent_packets[0][0] == OP_FILE_CHUNK
    assert all(p[0] != OP_TRANSFER_COMPLETE for p in sent_packets)

    # 2. Batch upload error 0xA9 arriving after chunk 1
    sent_packets.clear()

    def _fake_send_packet_a9(pkt: bytes) -> None:
        sent_packets.append(pkt)
        if pkt[0] == OP_FILE_CHUNK and len(sent_packets) == 1:
            session._route_message(encode_upload_batch_error(item_id=1, win32_error=112))

    session.send_packet = _fake_send_packet_a9  # type: ignore[method-assign]

    with pytest.raises(OSError, match="Batch upload write error"):
        transfer.upload_file(str(src), "C:\\abort_test.bin", overwrite=True)

    assert len(sent_packets) == 1
    assert sent_packets[0][0] == OP_FILE_CHUNK


def test_flush_tx_resets_timeout_on_progress():
    """Verify flush_tx() does not time out while tx_queue is actively draining."""
    import threading
    import time
    from unittest.mock import MagicMock

    session = SessionManager(MagicMock())
    session._running = True
    for _ in range(5):
        session.poller.enqueue_tx(b"x" * 64)

    def _drain() -> None:
        for _ in range(5):
            time.sleep(0.02)
            with session.poller._lock:
                if session.poller._tx_deque:
                    session.poller._tx_deque.popleft()

    t = threading.Thread(target=_drain, daemon=True)
    t.start()

    # Total drain time (~0.10 s) exceeds timeout (0.05 s), but each step makes progress
    session.flush_tx(timeout=0.05)
    t.join(timeout=1.0)
    assert session.poller.tx_queue.empty()


def test_flush_tx_times_out_when_stalled():
    """Verify flush_tx() raises TimeoutError when tx_queue makes no progress."""
    from unittest.mock import MagicMock

    session = SessionManager(MagicMock())
    session._running = True
    session.poller.enqueue_tx(b"x" * 64)

    with pytest.raises(TimeoutError, match="Timeout waiting for TX queue to drain"):
        session.flush_tx(timeout=0.03)


def test_send_packet_applies_tx_backpressure():
    """Verify send_packet() blocks while tx_queue >= MAX_TX_QUEUE_BLOCKS and unblocks on drain."""
    import threading
    import time
    from unittest.mock import MagicMock

    from openeasysync.client.session import MAX_TX_QUEUE_BLOCKS

    session = SessionManager(MagicMock())
    session._running = True
    for _ in range(MAX_TX_QUEUE_BLOCKS):
        session.poller.enqueue_tx(b"x" * 64)

    def _drain_one() -> None:
        time.sleep(0.03)
        with session.poller._lock:
            session.poller._tx_deque.popleft()

    t = threading.Thread(target=_drain_one, daemon=True)
    t.start()

    start = time.monotonic()
    session.send_packet(b"\x0c" + b"\x00" * 16, tx_timeout=1.0)
    elapsed = time.monotonic() - start
    t.join(timeout=1.0)

    assert elapsed >= 0.025
    assert session.poller.tx_queue.qsize() == MAX_TX_QUEUE_BLOCKS


def test_speed_tracker_and_format_rate(monkeypatch):
    """Verify SpeedTracker computes KB/s and format_rate switches between KB/s and MB/s."""
    from openeasysync.client.transfer import SpeedTracker, format_rate

    ticks = iter([100.0, 102.0])
    monkeypatch.setattr("openeasysync.client.transfer.time.monotonic", lambda: next(ticks))
    tracker = SpeedTracker()
    assert tracker.rate_kbs(0) == 0.0
    assert tracker.rate_kbs(2048 * 1024) == pytest.approx(1024.0)
    assert format_rate(512.34) == "512.3 KB/s"
    assert format_rate(2048.0) == "2.00 MB/s"


def test_e2e_batch_upload_and_download_progress_callbacks(dual_host_setup):
    """Verify upload_batch and download_batch invoke progress_callback with cumulative bytes and filename."""
    dir_a = dual_host_setup["dir_a"]
    dir_b = dual_host_setup["dir_b"]
    transfer_a = dual_host_setup["transfer_a"]

    src_dir = dir_a / "batch_prog"
    src_dir.mkdir()
    (src_dir / "a.bin").write_bytes(b"A" * 70000)
    (src_dir / "b.bin").write_bytes(b"B" * 10000)
    total_expected = 80000

    up_calls: list[tuple[int, int, str]] = []
    assert transfer_a.upload_batch(
        [str(src_dir)],
        "C:\\",
        progress_callback=lambda cur, tot, name: up_calls.append((cur, tot, name)),
    )
    assert up_calls
    assert all(tot == total_expected for _, tot, _ in up_calls)
    assert up_calls[-1][0] == total_expected
    assert [c[0] for c in up_calls] == sorted(c[0] for c in up_calls)
    assert any("a.bin" in c[2] for c in up_calls)
    assert any("b.bin" in c[2] for c in up_calls)

    dual_host_setup["session_b"].wait_settled(quiet_interval_s=0.05, timeout_s=2.0, min_settle_s=0.02)
    assert (dir_b / "batch_prog" / "a.bin").read_bytes() == b"A" * 70000

    dl_dest = dir_a / "dl_out"
    dl_calls: list[tuple[int, int, str]] = []
    assert transfer_a.download_batch(
        ["C:\\batch_prog"],
        str(dl_dest),
        progress_callback=lambda cur, tot, name: dl_calls.append((cur, tot, name)),
    )
    assert dl_calls
    assert dl_calls[-1][0] == total_expected
    assert dl_calls[-1][1] == total_expected
    assert [c[0] for c in dl_calls] == sorted(c[0] for c in dl_calls)


def test_upload_batch_sends_clear_readonly_on_conflict(tmp_path):
    """Verify upload_batch sends 0x12 OP_BATCH_CLEAR_READONLY for conflicting items before streaming."""
    from unittest.mock import MagicMock

    from openeasysync.constants import (
        OP_BATCH_CLEAR_READONLY,
        OP_PRECHECK_BATCH_EXT,
        OP_UPLOAD_SINGLE,
        STATUS_OK,
    )
    from openeasysync.protocol.commands import decode_command
    from openeasysync.protocol.replies import (
        encode_precheck_batch_ext_reply,
        encode_status_ack,
    )

    src_file = tmp_path / "conflict.txt"
    src_file.write_bytes(b"new content")

    sent_opcodes: list[int] = []
    clear_cmd_records: list[tuple[int, str]] = []

    def _fake_send_command(packet: bytes, expected_opcodes=None) -> bytes:
        opcode = packet[0]
        sent_opcodes.append(opcode)
        if opcode == OP_PRECHECK_BATCH_EXT:
            cmd = decode_command(packet)
            conflict_ids = [item_id for item_id, _path in cmd["records"]]
            return encode_precheck_batch_ext_reply(
                conflict_ids, req_id=cmd["req_id"]
            )
        if opcode == OP_BATCH_CLEAR_READONLY:
            cmd = decode_command(packet)
            clear_cmd_records.extend(cmd["records"])
            return encode_status_ack(STATUS_OK, req_opcode=OP_BATCH_CLEAR_READONLY, req_id=cmd["req_id"])
        if opcode == OP_UPLOAD_SINGLE:
            return encode_status_ack(STATUS_OK, req_opcode=OP_UPLOAD_SINGLE, req_id=1)
        return encode_status_ack(STATUS_OK, req_opcode=opcode, req_id=1)

    mock_session = MagicMock()
    mock_session.send_command.side_effect = _fake_send_command

    transfer = TransferManager(mock_session)
    assert transfer.upload_batch([str(src_file)], "C:\\dest", overwrite=True) is True

    assert OP_PRECHECK_BATCH_EXT in sent_opcodes
    assert OP_BATCH_CLEAR_READONLY in sent_opcodes
    assert sent_opcodes.index(OP_PRECHECK_BATCH_EXT) < sent_opcodes.index(OP_BATCH_CLEAR_READONLY)
    assert sent_opcodes.index(OP_BATCH_CLEAR_READONLY) < sent_opcodes.index(OP_UPLOAD_SINGLE)
    assert len(clear_cmd_records) == 1
    assert clear_cmd_records[0][1] == "C:\\dest\\conflict.txt"


def test_e2e_batch_upload_overwrite_readonly_destination(dual_host_setup):
    """Verify upload_batch clears read-only attributes (0x12) and overwrites read-only destination files."""
    import os

    dir_a = dual_host_setup["dir_a"]
    dir_b = dual_host_setup["dir_b"]
    transfer_a = dual_host_setup["transfer_a"]

    dest_dir = dir_b / "ro_target"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / "readonly.txt"
    dest_file.write_bytes(b"old readonly content")
    os.chmod(dest_file, 0o444)

    src_file = dir_a / "readonly.txt"
    src_file.write_bytes(b"updated content")

    try:
        assert transfer_a.upload_batch([str(src_file)], "C:\\ro_target", overwrite=True) is True
        dual_host_setup["session_b"].wait_settled(
            quiet_interval_s=0.05, timeout_s=2.0, min_settle_s=0.02
        )
        assert dest_file.read_bytes() == b"updated content"
    finally:
        if dest_file.exists():
            os.chmod(dest_file, 0o644)
