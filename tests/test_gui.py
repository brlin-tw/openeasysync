# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for OpenEasySync PySide6 GUI components."""

import os

import pytest
from PySide6.QtWidgets import QApplication

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
