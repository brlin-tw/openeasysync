# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: E402
"""Unit tests for OpenEasySync PySide6 GUI components."""

import os

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QApplication = QtWidgets.QApplication

from openeasysync.client.session import SessionManager
from openeasysync.gui.dialogs import AboutDialog, ConfirmReplaceDialog, ProgressDialog
from openeasysync.gui.window import FilePane, MainWindow, StatusLamp
from openeasysync.protocol.directory_entry import make_file_entry
from openeasysync.transport.mock import MockBridgeCable


@pytest.fixture(scope="session")
def qapp():
    """Initializes QApplication once for testing session."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_status_lamp(qapp):
    """Verify StatusLamp toggles on/off styling."""
    lamp = StatusLamp("USB 2.0 L")
    assert lamp.text() == "USB 2.0 L"
    lamp.set_state(True)
    assert lamp._is_on is True
    lamp.set_state(False)
    assert lamp._is_on is False
    lamp.deleteLater()


def test_file_pane_display(qapp):
    """Verify FilePane displays directory entries in table."""
    pane = FilePane("Test Pane")
    entries = [
        make_file_entry("test1.txt", size=1024, is_dir=False),
        make_file_entry("docs", size=0, is_dir=True),
    ]
    pane.set_entries("/tmp", entries)
    assert pane.table.rowCount() == 2
    assert pane.table.item(0, 0).text() == "test1.txt"
    assert pane.table.item(1, 0).text() == "docs"
    pane.deleteLater()


def test_dialogs(qapp):
    """Verify instantiation and methods of GUI dialogs."""
    prog = ProgressDialog()
    prog.set_paths("src.txt", "dst.txt")
    prog.update_progress(50, 100, 120.5)
    assert prog.progress_bar.value() == 50
    prog.deleteLater()

    conf = ConfirmReplaceDialog(
        filename="conflict.dat",
        existing_size=1000,
        existing_date="2026-01-01",
        new_size=2000,
        new_date="2026-01-02",
    )
    assert conf.windowTitle() == "Confirm Replace File"
    conf.deleteLater()

    about = AboutDialog()
    assert about.windowTitle() == "About OpenEasySync"
    about.deleteLater()


def test_main_window_with_mock_session(qapp, tmp_path):
    """Verify MainWindow integrates with active SessionManager."""
    cable = MockBridgeCable()
    t_a, t_b = cable.create_pair()
    session = SessionManager(t_a)

    win = MainWindow(session=session)
    assert win.windowTitle() == "OpenEasySync - OTi-2108 File Manager"

    win._poll_status()
    win.refresh_local()
    win.refresh_remote()
    win.close()
    win.deleteLater()


def test_main_window_up_button_focused_pane(qapp, tmp_path):
    """Verify MainWindow Up action moves only the currently focused pane."""
    win = MainWindow()
    win.show()

    # Create dummy local path structure
    sub_dir = tmp_path / "sub1" / "sub2"
    sub_dir.mkdir(parents=True)
    win.local_current_path = str(sub_dir)
    win.remote_current_path = "C:\\Windows\\System32"

    # 1. Focus local pane table: Up should only navigate local pane
    win.local_pane.table.setFocus()
    qapp.processEvents()
    assert win._get_focused_pane() is win.local_pane

    win.act_up.trigger()
    assert win.local_current_path == str(sub_dir.parent)
    assert win.remote_current_path == "C:\\Windows\\System32"

    # 2. Focus remote pane table: Up should only navigate remote pane
    win.remote_pane.table.setFocus()
    qapp.processEvents()
    assert win._get_focused_pane() is win.remote_pane

    win.act_up.trigger()
    assert win.local_current_path == str(sub_dir.parent)
    assert win.remote_current_path == "C:\\Windows"

    # Continue remote navigation up to drive root and then roots view
    win.act_up.trigger()
    assert win.remote_current_path == "C:\\"

    win.act_up.trigger()
    assert win.remote_current_path == ""

    win.close()
    win.deleteLater()


def test_main_window_upload_uses_inline_overwrite_check(qapp, tmp_path, monkeypatch):
    """Verify Copy -> uses Opcode 0x0A inline overwrite check instead of Opcode 0x0F precheck."""
    from unittest.mock import MagicMock

    local_file = tmp_path / "sample.txt"
    local_file.write_text("hello", encoding="utf-8")

    cable = MockBridgeCable()
    t_a, _ = cable.create_pair()
    session = SessionManager(t_a)
    monkeypatch.setattr(SessionManager, "is_connected", property(lambda self: True))

    win = MainWindow(session=session)
    win.local_current_path = str(tmp_path)
    win.remote_current_path = "C:\\Dest"
    win.refresh_local()
    win.local_pane.table.setCurrentCell(0, 0)

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "information",
        MagicMock(side_effect=AssertionError("Unexpected QMessageBox.information")),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        MagicMock(side_effect=AssertionError("Unexpected QMessageBox.warning")),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "critical",
        MagicMock(side_effect=AssertionError("Unexpected QMessageBox.critical")),
    )

    win.transfer.precheck_single = MagicMock(side_effect=AssertionError("precheck_single must not be called"))
    win.refresh_remote = MagicMock()

    # Case 1: New file succeeds on first upload_file(..., overwrite=False)
    win.transfer.upload_file = MagicMock(return_value=True)
    win._on_upload_clicked()
    win.transfer.upload_file.assert_called_once_with(str(local_file), "C:\\Dest\\sample.txt", overwrite=False)

    # Case 2: Existing file raises FileExistsError, user confirms replacement -> overwrite=True
    win.transfer.upload_file = MagicMock(side_effect=[FileExistsError("exists"), True])
    monkeypatch.setattr(ConfirmReplaceDialog, "exec", lambda self: 1)
    win._on_upload_clicked()
    assert win.transfer.upload_file.call_count == 2
    assert win.transfer.upload_file.call_args_list[0].kwargs == {"overwrite": False}
    assert win.transfer.upload_file.call_args_list[1].kwargs == {"overwrite": True}

    # Case 3: Existing file raises FileExistsError, user cancels -> no second upload_file call
    win.transfer.upload_file = MagicMock(side_effect=FileExistsError("exists"))
    monkeypatch.setattr(ConfirmReplaceDialog, "exec", lambda self: 0)
    win._on_upload_clicked()
    win.transfer.upload_file.assert_called_once_with(str(local_file), "C:\\Dest\\sample.txt", overwrite=False)

    win.close()
    win.deleteLater()


def test_main_window_delete_button_focused_pane(qapp, tmp_path, monkeypatch):
    """Verify MainWindow Delete action operates only on the currently focused pane."""
    from unittest.mock import MagicMock

    local_file = tmp_path / "local.txt"
    local_file.write_text("local", encoding="utf-8")

    cable = MockBridgeCable()
    t_a, _ = cable.create_pair()
    session = SessionManager(t_a)
    monkeypatch.setattr(SessionManager, "is_connected", property(lambda self: True))

    win = MainWindow(session=session)
    win.show()
    win.local_current_path = str(tmp_path)
    win.remote_current_path = "C:\\Remote"
    win.refresh_local()
    win.remote_pane.set_entries(
        "C:\\Remote",
        [
            make_file_entry("remote.txt", size=12, is_dir=False),
            make_file_entry("remote_dir", size=0, is_dir=True),
        ],
    )
    win.local_pane.table.setCurrentCell(0, 0)
    win.remote_pane.table.setCurrentCell(0, 0)

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args, **kwargs: QtWidgets.QMessageBox.Yes)
    warn_mock = MagicMock()
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", warn_mock)

    win.transfer.delete_file = MagicMock(return_value=True)
    win.transfer.delete_dir = MagicMock(return_value=True)
    win.refresh_remote = MagicMock()

    # 1. Focus remote pane: Delete should delete remote file, not local file
    win.remote_pane.table.setFocus()
    qapp.processEvents()
    assert win._get_focused_pane() is win.remote_pane

    win.act_delete.trigger()
    win.transfer.delete_file.assert_called_once_with("C:\\Remote\\remote.txt")
    win.transfer.delete_dir.assert_not_called()
    win.refresh_remote.assert_called_once()
    assert local_file.exists()

    # 2. Focus remote pane on a directory: Delete should call delete_dir
    win.remote_pane.table.setCurrentCell(1, 0)
    win.act_delete.trigger()
    win.transfer.delete_dir.assert_called_once_with("C:\\Remote\\remote_dir")
    assert local_file.exists()

    # 3. Focus local pane: Delete should delete local file, not remote
    win.transfer.delete_file.reset_mock()
    win.transfer.delete_dir.reset_mock()
    win.local_pane.table.setFocus()
    qapp.processEvents()
    assert win._get_focused_pane() is win.local_pane

    win.act_delete.trigger()
    assert not local_file.exists()
    win.transfer.delete_file.assert_not_called()
    win.transfer.delete_dir.assert_not_called()
    warn_mock.assert_not_called()

    win.close()
    win.deleteLater()
