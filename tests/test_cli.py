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
