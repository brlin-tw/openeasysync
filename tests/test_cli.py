# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for OpenEasySync CLI commands."""

from unittest.mock import MagicMock, patch

from openeasysync.cli.main import main


def test_cli_rm_success():
    """Verify 'rm' command deletes remote file and disconnects session."""
    mock_transport = MagicMock()
    mock_session = MagicMock()
    mock_transfer = MagicMock()
    mock_transfer.delete_file.return_value = True

    with (
        patch("openeasysync.cli.main.detect_device", return_value=mock_transport) as mock_detect,
        patch("openeasysync.cli.main.SessionManager", return_value=mock_session),
        patch("openeasysync.cli.main.TransferManager", return_value=mock_transfer),
    ):
        rc = main(["-d", "1:4", "rm", "C:\\remote.txt"])

    assert rc == 0
    mock_detect.assert_called_once_with("1:4")
    mock_session.connect.assert_called_once()
    mock_transfer.delete_file.assert_called_once_with("C:\\remote.txt")
    mock_session.disconnect.assert_called_once()


def test_cli_delete_alias_success():
    """Verify 'delete' alias invokes the same remote file deletion handler."""
    mock_transport = MagicMock()
    mock_session = MagicMock()
    mock_transfer = MagicMock()
    mock_transfer.delete_file.return_value = True

    with (
        patch("openeasysync.cli.main.detect_device", return_value=mock_transport),
        patch("openeasysync.cli.main.SessionManager", return_value=mock_session),
        patch("openeasysync.cli.main.TransferManager", return_value=mock_transfer),
    ):
        rc = main(["delete", "C:\\remote.txt"])

    assert rc == 0
    mock_transfer.delete_file.assert_called_once_with("C:\\remote.txt")
    mock_session.disconnect.assert_called_once()


def test_cli_rm_failure_status():
    """Verify 'rm' returns exit code 1 when delete_file returns False."""
    mock_transport = MagicMock()
    mock_session = MagicMock()
    mock_transfer = MagicMock()
    mock_transfer.delete_file.return_value = False

    with (
        patch("openeasysync.cli.main.detect_device", return_value=mock_transport),
        patch("openeasysync.cli.main.SessionManager", return_value=mock_session),
        patch("openeasysync.cli.main.TransferManager", return_value=mock_transfer),
    ):
        rc = main(["rm", "C:\\missing.txt"])

    assert rc == 1
    mock_session.disconnect.assert_called_once()


def test_cli_rm_no_device():
    """Verify 'rm' returns exit code 1 when no OTi-2108 device is detected."""
    with patch("openeasysync.cli.main.detect_device", return_value=None):
        rc = main(["rm", "C:\\remote.txt"])

    assert rc == 1


def test_cli_rm_exception_disconnects():
    """Verify 'rm' returns exit code 1 and disconnects on exception."""
    mock_transport = MagicMock()
    mock_session = MagicMock()
    mock_transfer = MagicMock()
    mock_transfer.delete_file.side_effect = RuntimeError("transport error")

    with (
        patch("openeasysync.cli.main.detect_device", return_value=mock_transport),
        patch("openeasysync.cli.main.SessionManager", return_value=mock_session),
        patch("openeasysync.cli.main.TransferManager", return_value=mock_transfer),
    ):
        rc = main(["rm", "C:\\remote.txt"])

    assert rc == 1
    mock_session.disconnect.assert_called_once()


def test_cli_put_and_get_report_progress(tmp_path, capsys):
    """Verify 'put' (file and dir) and 'get' pass progress_callback and print percentage and speed."""
    local_file = tmp_path / "demo.bin"
    local_file.write_bytes(b"X" * 1024)
    local_dir = tmp_path / "folder"
    local_dir.mkdir()

    def _fake_upload_file(src, dst, overwrite=False, progress_callback=None):
        assert progress_callback is not None
        progress_callback(512, 1024)
        progress_callback(1024, 1024)
        return True

    def _fake_upload_batch(srcs, dst, progress_callback=None):
        assert progress_callback is not None
        progress_callback(2048, 2048, "item.txt")
        return True

    def _fake_download_file(src, dst, progress_callback=None):
        assert progress_callback is not None
        progress_callback(1024, 2048)
        progress_callback(2048, 2048)
        return True

    mock_transport = MagicMock()
    mock_session = MagicMock()
    mock_transfer = MagicMock()
    mock_transfer.upload_file.side_effect = _fake_upload_file
    mock_transfer.upload_batch.side_effect = _fake_upload_batch
    mock_transfer.download_file.side_effect = _fake_download_file

    with (
        patch("openeasysync.cli.main.detect_device", return_value=mock_transport),
        patch("openeasysync.cli.main.SessionManager", return_value=mock_session),
        patch("openeasysync.cli.main.TransferManager", return_value=mock_transfer),
    ):
        assert main(["put", str(local_file), "C:\\demo.bin"]) == 0
        out_put_file = capsys.readouterr().out
        assert "100.0%" in out_put_file
        assert "KB/s" in out_put_file or "MB/s" in out_put_file

        assert main(["put", str(local_dir), "C:\\"]) == 0
        out_put_dir = capsys.readouterr().out
        assert "100.0%" in out_put_dir
        assert "item.txt" in out_put_dir

        assert main(["get", "C:\\demo.bin", str(tmp_path / "out.bin")]) == 0
        out_get = capsys.readouterr().out
        assert "100.0%" in out_get
        assert "KB/s" in out_get or "MB/s" in out_get


def test_cli_put_force_overwrite_flag(tmp_path):
    """Verify 'put -f' / 'put --force' passes overwrite=True to upload_file."""
    local_file = tmp_path / "demo.bin"
    local_file.write_bytes(b"X" * 16)

    mock_transport = MagicMock()
    mock_session = MagicMock()
    mock_transfer = MagicMock()
    mock_transfer.upload_file.return_value = True

    with (
        patch("openeasysync.cli.main.detect_device", return_value=mock_transport),
        patch("openeasysync.cli.main.SessionManager", return_value=mock_session),
        patch("openeasysync.cli.main.TransferManager", return_value=mock_transfer),
    ):
        assert main(["put", str(local_file), "C:\\demo.bin"]) == 0
        assert mock_transfer.upload_file.call_args.kwargs["overwrite"] is False

        assert main(["put", "-f", str(local_file), "C:\\demo.bin"]) == 0
        assert mock_transfer.upload_file.call_args.kwargs["overwrite"] is True

        assert main(["put", "--force", str(local_file), "C:\\demo.bin"]) == 0
        assert mock_transfer.upload_file.call_args.kwargs["overwrite"] is True
