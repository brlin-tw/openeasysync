# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Main window for OpenEasySync dual-pane host-to-host file manager."""

import ntpath
import os
import posixpath
import time

from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFocusEvent, QFont, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from openeasysync.client.session import SessionManager
from openeasysync.client.transfer import SpeedTracker, TransferManager
from openeasysync.constants import SPEED_USB20
from openeasysync.gui.dialogs import AboutDialog, ConfirmReplaceDialog, ProgressDialog
from openeasysync.protocol.directory_entry import Entry552, filetime_to_unix


class StatusLamp(QLabel):
    """Visual hardware status indicator lamp."""

    def __init__(self, label: str, active_color: str = "#2ecc71", parent=None):
        super().__init__(parent)
        self.label_text = label
        self.active_color = active_color
        self._is_on = False
        self.setAlignment(Qt.AlignCenter)
        self.setFont(QFont("sans-serif", 9, QFont.Bold))
        self.set_state(False)

    def set_state(self, on: bool) -> None:
        self._is_on = on
        if on:
            self.setStyleSheet(
                f"background-color: {self.active_color}; color: #ffffff; "
                f"border: 1px solid #27ae60; border-radius: 3px; padding: 2px 6px;"
            )
        else:
            self.setStyleSheet(
                "background-color: #bdc3c7; color: #7f8c8d; "
                "border: 1px solid #95a5a6; border-radius: 3px; padding: 2px 6px;"
            )
        self.setText(self.label_text)


class FilePane(QWidget):
    """Single filesystem pane (Folder / File Table)."""

    navigate_requested = Signal(str)
    focused = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.current_path = ""
        self.entries: list[Entry552] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Header title and path bar
        header_layout = QHBoxLayout()
        self.lbl_title = QLabel(f"<b>{title}</b>")
        self.lbl_path = QLabel("")
        self.lbl_path.setStyleSheet("color: #2980b9; font-weight: bold;")
        header_layout.addWidget(self.lbl_title)
        header_layout.addWidget(self.lbl_path, 1)
        layout.addLayout(header_layout)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Name", "Size", "Type", "Modified"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.installEventFilter(self)
        layout.addWidget(self.table)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.table and event.type() == QEvent.FocusIn:
            self.focused.emit()
        return super().eventFilter(watched, event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        self.focused.emit()
        super().focusInEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.table.setFocus()
        self.focused.emit()
        super().mousePressEvent(event)

    def set_entries(self, path: str, entries: list[Entry552]) -> None:
        self.current_path = path
        self.entries = entries
        self.lbl_path.setText(path or "(Roots / Drives)")

        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            # Name
            name_item = QTableWidgetItem(e.find_data.filename)
            # Size
            if e.is_dir or e.is_drive:
                size_str = "<DIR>" if e.is_dir else "<DRIVE>"
            else:
                size_str = f"{e.find_data.file_size:,} B"
            size_item = QTableWidgetItem(size_str)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            # Type
            type_str = "Drive" if e.is_drive else ("Folder" if e.is_dir else "File")
            type_item = QTableWidgetItem(type_str)

            # Modified
            m_unix = filetime_to_unix(e.find_data.last_write_time)
            m_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m_unix)) if m_unix > 0 else ""
            m_item = QTableWidgetItem(m_str)

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, size_item)
            self.table.setItem(row, 2, type_item)
            self.table.setItem(row, 3, m_item)

    def get_selected_entry(self) -> Entry552 | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.entries):
            return self.entries[row]
        return None

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        if 0 <= row < len(self.entries):
            e = self.entries[row]
            if e.is_dir or e.is_drive:
                new_path = (
                    os.path.join(self.current_path, e.find_data.filename)
                    if self.current_path
                    else e.find_data.filename
                )
                self.navigate_requested.emit(new_path)


class _TransferWorker(QThread):
    """Background worker running a TransferManager operation without blocking the Qt UI."""

    progress = Signal(int, int, str)
    succeeded = Signal(bool)
    failed = Signal(object)

    def __init__(self, transfer_fn, parent=None):
        super().__init__(parent)
        self._transfer_fn = transfer_fn

    def run(self) -> None:
        def _emit_progress(current: int, total: int, filename: str = "") -> None:
            self.progress.emit(current, total, filename)

        try:
            result = self._transfer_fn(_emit_progress)
            self.succeeded.emit(bool(result))
        except Exception as exc:
            self.failed.emit(exc)


class MainWindow(QMainWindow):
    """Main application window implementing Spec Part 3."""

    def __init__(self, session: SessionManager | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OpenEasySync - OTi-2108 File Manager")
        self.resize(1024, 680)

        self.session = session
        self.transfer = TransferManager(session) if session else None

        self._setup_ui()
        self._setup_menus()
        self._setup_status_bar()

        # Activity lamp blink timer
        self._activity_counter = 0
        self.lamp_timer = QTimer(self)
        self.lamp_timer.timeout.connect(self._poll_status)
        self.lamp_timer.start(1000)

        # Initial directory load
        self.local_current_path = os.getcwd()
        self.remote_current_path = ""
        self.refresh_local()
        self.refresh_remote()

    def _setup_ui(self) -> None:
        # Central splitter
        splitter = QSplitter(Qt.Horizontal)
        self.local_pane = FilePane("Local Host (My Computer)")
        self.remote_pane = FilePane("Remote Host (Remote Computer)")
        self._focused_pane = self.local_pane
        self.local_pane.focused.connect(lambda: self._set_focused_pane(self.local_pane))
        self.remote_pane.focused.connect(lambda: self._set_focused_pane(self.remote_pane))
        self.local_pane.navigate_requested.connect(self._on_local_navigate)
        self.remote_pane.navigate_requested.connect(self._on_remote_navigate)
        self.remote_pane.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_pane.table.customContextMenuRequested.connect(self._on_remote_context_menu)

        splitter.addWidget(self.local_pane)
        splitter.addWidget(self.remote_pane)
        splitter.setSizes([500, 500])
        self.setCentralWidget(splitter)

        # Toolbar
        toolbar = QToolBar("Main Toolbar")
        self.addToolBar(toolbar)

        self.act_up = toolbar.addAction("Up")
        self.act_up.triggered.connect(self._on_up_clicked)

        self.act_refresh = toolbar.addAction("Refresh")
        self.act_refresh.triggered.connect(self._on_refresh_clicked)

        toolbar.addSeparator()

        self.act_upload = toolbar.addAction("Copy ->")
        self.act_upload.triggered.connect(self._on_upload_clicked)

        self.act_download = toolbar.addAction("<- Copy")
        self.act_download.triggered.connect(self._on_download_clicked)

        toolbar.addSeparator()

        self.act_delete = toolbar.addAction("Delete")
        self.act_delete.triggered.connect(self._on_delete_clicked)

        self.act_mkdir = toolbar.addAction("New Folder")
        self.act_mkdir.triggered.connect(self._on_mkdir_clicked)

        self.act_rename = toolbar.addAction("Rename")
        self.act_rename.triggered.connect(self._on_rename_clicked)

        # Toolbar Hardware status indicator lamps
        toolbar.addSeparator()
        self.lamp_usb11_l = StatusLamp("USB 1.1 L", active_color="#e67e22")
        self.lamp_usb20_l = StatusLamp("USB 2.0 L", active_color="#27ae60")
        self.lamp_usb11_r = StatusLamp("USB 1.1 R", active_color="#e67e22")
        self.lamp_usb20_r = StatusLamp("USB 2.0 R", active_color="#27ae60")
        self.lamp_txrx = StatusLamp("Tx/Rx", active_color="#3498db")

        toolbar.addWidget(self.lamp_usb11_l)
        toolbar.addWidget(self.lamp_usb20_l)
        toolbar.addWidget(self.lamp_usb11_r)
        toolbar.addWidget(self.lamp_usb20_r)
        toolbar.addWidget(self.lamp_txrx)

    def _setup_menus(self) -> None:
        mb = self.menuBar()

        # File
        m_file = mb.addMenu("&File")
        m_file.addAction(self.act_upload)
        m_file.addAction(self.act_download)
        m_file.addSeparator()
        m_file.addAction(self.act_delete)
        m_file.addAction(self.act_mkdir)
        m_file.addAction(self.act_rename)
        m_file.addSeparator()
        act_exit = m_file.addAction("E&xit")
        act_exit.triggered.connect(self.close)

        # View
        m_view = mb.addMenu("&View")
        m_view.addAction(self.act_refresh)

        # Help
        m_help = mb.addMenu("&Help")
        act_about = m_help.addAction("&About OpenEasySync...")
        act_about.triggered.connect(self._show_about)

    def _setup_status_bar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lbl_conn_status = QLabel("Disconnected")
        self.lbl_conn_status.setStyleSheet("color: #e74c3c; font-weight: bold;")
        sb.addPermanentWidget(self.lbl_conn_status)

    def _poll_status(self) -> None:
        if not self.session or not self.session.transport:
            return

        try:
            local_spd, peer_spd = self.session.transport.get_usb_speed()
            self.lamp_usb20_l.set_state(local_spd == SPEED_USB20)
            self.lamp_usb11_l.set_state(local_spd != 0 and local_spd != SPEED_USB20)
            self.lamp_usb20_r.set_state(peer_spd == SPEED_USB20)
            self.lamp_usb11_r.set_state(peer_spd != 0 and peer_spd != SPEED_USB20)

            conn = self.session.is_connected
            if conn:
                self.lbl_conn_status.setText("Connected")
                self.lbl_conn_status.setStyleSheet("color: #27ae60; font-weight: bold;")
            else:
                self.lbl_conn_status.setText("Disconnected")
                self.lbl_conn_status.setStyleSheet("color: #e74c3c; font-weight: bold;")
        except Exception:
            pass

    def refresh_local(self) -> None:
        """Refreshes local file list."""
        from openeasysync.protocol.directory_entry import make_file_entry

        entries: list[Entry552] = []
        try:
            with os.scandir(self.local_current_path) as it:
                for item in it:
                    try:
                        stat = item.stat()
                        is_d = item.is_dir()
                        entries.append(
                            make_file_entry(
                                item.name,
                                size=stat.st_size if not is_d else 0,
                                is_dir=is_d,
                                mtime=stat.st_mtime,
                                ctime=stat.st_ctime,
                                atime=stat.st_atime,
                            )
                        )
                    except Exception:
                        continue
            entries.sort(key=lambda x: (not x.is_dir, x.find_data.filename.lower()))
        except Exception as exc:
            QMessageBox.warning(self, "Local Error", str(exc))

        self.local_pane.set_entries(self.local_current_path, entries)

    def refresh_remote(self) -> None:
        """Refreshes remote file list."""
        if not self.transfer or not self.session or not self.session.is_connected:
            self.remote_pane.set_entries("", [])
            return

        try:
            if not self.remote_current_path:
                roots = self.transfer.query_roots()
                self.remote_pane.set_entries("", roots)
            else:
                entries = self.transfer.list_dir(self.remote_current_path)
                self.remote_pane.set_entries(self.remote_current_path, entries)
        except Exception as exc:
            self.remote_pane.set_entries(self.remote_current_path, [])
            self.statusBar().showMessage(f"Remote error: {exc}", 5000)

    def _on_local_navigate(self, new_path: str) -> None:
        if os.path.isdir(new_path):
            self.local_current_path = os.path.abspath(new_path)
            self.refresh_local()

    def _notify_remote_active_dir(self, remote_path: str) -> None:
        """Notifies the remote peer of the active remote directory if connected."""
        if not self.transfer or not self.session or not self.session.is_connected:
            return
        try:
            self.transfer.notify_active_dir(remote_path)
        except Exception:
            pass

    def _on_remote_navigate(self, new_path: str) -> None:
        self.remote_current_path = new_path
        self._notify_remote_active_dir(new_path)
        self.refresh_remote()

    def _set_focused_pane(self, pane: FilePane) -> None:
        self._focused_pane = pane

    def _get_focused_pane(self) -> FilePane:
        fw = QApplication.focusWidget()
        if fw is not None:
            if fw is self.remote_pane or self.remote_pane.isAncestorOf(fw):
                self._focused_pane = self.remote_pane
                return self.remote_pane
            if fw is self.local_pane or self.local_pane.isAncestorOf(fw):
                self._focused_pane = self.local_pane
                return self.local_pane
        return getattr(self, "_focused_pane", self.local_pane)

    def _on_up_clicked(self) -> None:
        focused = self._get_focused_pane()
        if focused is self.local_pane:
            parent_loc = os.path.dirname(self.local_current_path)
            if parent_loc and parent_loc != self.local_current_path:
                self.local_current_path = parent_loc
                self.refresh_local()
        elif focused is self.remote_pane:
            if self.remote_current_path:
                clean_r = self.remote_current_path.rstrip("\\/")
                if not clean_r or (len(clean_r) == 2 and clean_r[1] == ":"):
                    self.remote_current_path = ""
                elif "\\" in clean_r or ":" in clean_r:
                    parent_r = ntpath.dirname(clean_r)
                    if len(parent_r) == 2 and parent_r[1] == ":":
                        parent_r += "\\"
                    self.remote_current_path = parent_r
                else:
                    parent_r = posixpath.dirname(clean_r)
                    self.remote_current_path = "" if parent_r == clean_r else parent_r
                self._notify_remote_active_dir(self.remote_current_path)
                self.refresh_remote()

    def _on_refresh_clicked(self) -> None:
        self.refresh_local()
        self.refresh_remote()

    def _run_transfer_with_dialog(
        self, src: str, dest: str, transfer_fn
    ) -> tuple[bool, Exception | None]:
        """Runs a transfer in a background QThread while displaying ProgressDialog."""
        progress = ProgressDialog(parent=self)
        progress.set_paths(src, dest)
        tracker = SpeedTracker()
        worker = _TransferWorker(transfer_fn, parent=self)
        outcome: dict[str, object] = {"ok": False, "error": None, "cancelled": False}

        def _on_progress(current: int, total: int, filename: str) -> None:
            progress.update_progress(current, total, tracker.rate_kbs(current), filename)

        def _on_succeeded(ok: bool) -> None:
            outcome["ok"] = ok
            progress.accept()

        def _on_failed(exc: Exception) -> None:
            outcome["error"] = exc
            progress.reject()

        def _on_cancelled() -> None:
            outcome["cancelled"] = True
            if self.transfer:
                self.transfer.cancel_transfer()

        worker.progress.connect(_on_progress)
        worker.succeeded.connect(_on_succeeded)
        worker.failed.connect(_on_failed)
        progress.cancelled.connect(_on_cancelled)

        QTimer.singleShot(0, worker.start)
        progress.exec()
        worker.wait()

        if outcome["cancelled"]:
            return False, None
        return bool(outcome["ok"]), outcome["error"]  # type: ignore[return-value]

    def _on_upload_clicked(self) -> None:
        entry = self.local_pane.get_selected_entry()
        if not entry:
            QMessageBox.information(self, "Upload", "Please select a local file to upload.")
            return

        if not self.transfer or not self.session or not self.session.is_connected:
            QMessageBox.warning(self, "Upload Error", "Remote peer is not connected.")
            return

        src_path = os.path.join(self.local_current_path, entry.find_data.filename)
        dest_remote = (
            os.path.join(self.remote_current_path, entry.find_data.filename).replace("/", "\\")
            if self.remote_current_path
            else f"C:\\{entry.find_data.filename}"
        )

        if entry.is_dir:
            ok, exc = self._run_transfer_with_dialog(
                src_path,
                dest_remote,
                lambda cb: self.transfer.upload_batch(
                    [src_path], self.remote_current_path or "C:\\", progress_callback=cb
                ),
            )
        else:
            ok, exc = self._run_transfer_with_dialog(
                src_path,
                dest_remote,
                lambda cb: self.transfer.upload_file(
                    src_path, dest_remote, overwrite=False, progress_callback=cb
                ),
            )
            if isinstance(exc, FileExistsError):
                dlg = ConfirmReplaceDialog(
                    filename=entry.find_data.filename,
                    existing_size=entry.find_data.file_size,
                    existing_date="existing",
                    new_size=entry.find_data.file_size,
                    new_date="new",
                    parent=self,
                )
                res = dlg.exec()
                if res in (0, -1):
                    return
                ok, exc = self._run_transfer_with_dialog(
                    src_path,
                    dest_remote,
                    lambda cb: self.transfer.upload_file(
                        src_path, dest_remote, overwrite=True, progress_callback=cb
                    ),
                )

        if exc is not None:
            QMessageBox.critical(self, "Upload Failed", str(exc))
            return
        if ok:
            self.refresh_remote()
            self.statusBar().showMessage("Upload completed.", 3000)

    def _on_download_clicked(self) -> None:
        entry = self.remote_pane.get_selected_entry()
        if not entry:
            QMessageBox.information(self, "Download", "Please select a remote file to download.")
            return

        if not self.transfer or not self.session or not self.session.is_connected:
            QMessageBox.warning(self, "Download Error", "Remote peer is not connected.")
            return

        remote_src = (
            os.path.join(self.remote_current_path, entry.find_data.filename).replace("/", "\\")
            if self.remote_current_path
            else f"C:\\{entry.find_data.filename}"
        )
        local_dest = os.path.join(self.local_current_path, entry.find_data.filename)

        if entry.is_dir or entry.is_drive:
            ok, exc = self._run_transfer_with_dialog(
                remote_src,
                local_dest,
                lambda cb: self.transfer.download_batch(
                    [remote_src], self.local_current_path, progress_callback=cb
                ),
            )
        else:
            ok, exc = self._run_transfer_with_dialog(
                remote_src,
                local_dest,
                lambda cb: self.transfer.download_file(
                    remote_src, local_dest, progress_callback=cb
                ),
            )

        if exc is not None:
            QMessageBox.critical(self, "Download Failed", str(exc))
            return
        if ok:
            self.refresh_local()
            self.statusBar().showMessage("Download completed.", 3000)

    def _build_remote_path(self, filename: str) -> str:
        """Joins filename onto remote_current_path using the remote path style."""
        if "\\" in self.remote_current_path or ":" in self.remote_current_path:
            return ntpath.join(self.remote_current_path, filename)
        if self.remote_current_path:
            return posixpath.join(self.remote_current_path, filename)
        return f"C:\\{filename}"

    def _on_delete_clicked(self) -> None:
        focused = self._get_focused_pane()
        if focused is self.local_pane:
            self._delete_local_selected()
        elif focused is self.remote_pane:
            self._delete_remote_selected()

    def _delete_local_selected(self) -> None:
        loc_entry = self.local_pane.get_selected_entry()
        if not loc_entry:
            return
        path = os.path.join(self.local_current_path, loc_entry.find_data.filename)
        reply = QMessageBox.question(
            self, "Delete", f"Are you sure you want to delete '{loc_entry.find_data.filename}'?"
        )
        if reply != QMessageBox.Yes:
            return
        try:
            if loc_entry.is_dir:
                import shutil

                shutil.rmtree(path)
            else:
                os.remove(path)
            self.refresh_local()
        except Exception as exc:
            QMessageBox.warning(self, "Delete Error", str(exc))

    def _delete_remote_selected(self) -> None:
        rem_entry = self.remote_pane.get_selected_entry()
        if not rem_entry:
            return
        if rem_entry.is_drive:
            QMessageBox.warning(self, "Delete Error", "Cannot delete a drive.")
            return
        if not self.transfer or not self.session or not self.session.is_connected:
            QMessageBox.warning(self, "Delete Error", "Remote peer is not connected.")
            return

        remote_path = self._build_remote_path(rem_entry.find_data.filename)
        reply = QMessageBox.question(
            self, "Delete", f"Are you sure you want to delete '{rem_entry.find_data.filename}'?"
        )
        if reply != QMessageBox.Yes:
            return
        try:
            if rem_entry.is_dir:
                ok = self.transfer.delete_dir(remote_path)
            else:
                ok = self.transfer.delete_file(remote_path)
            if ok:
                self.refresh_remote()
            else:
                QMessageBox.warning(
                    self, "Delete Error", f"Failed to delete '{rem_entry.find_data.filename}'."
                )
        except Exception as exc:
            QMessageBox.warning(self, "Delete Error", str(exc))

    def _on_mkdir_clicked(self) -> None:
        focused = self._get_focused_pane()
        if focused is self.remote_pane:
            self._create_remote_dir()
        else:
            self._create_local_dir()

    def _create_local_dir(self) -> None:
        name, ok = QInputDialog.getText(self, "New Folder", "Enter folder name:")
        if ok and name:
            new_dir = os.path.join(self.local_current_path, name)
            try:
                os.makedirs(new_dir, exist_ok=True)
                self.refresh_local()
            except Exception as exc:
                QMessageBox.warning(self, "Create Folder Error", str(exc))

    def _create_remote_dir(self) -> None:
        if not self.transfer or not self.session or not self.session.is_connected:
            QMessageBox.warning(self, "Create Folder Error", "Remote peer is not connected.")
            return
        name, ok = QInputDialog.getText(self, "New Folder", "Enter folder name:")
        if not ok or not name:
            return
        remote_path = self._build_remote_path(name)
        try:
            if self.transfer.create_dir(remote_path):
                self.refresh_remote()
            else:
                QMessageBox.warning(
                    self, "Create Folder Error", f"Failed to create '{name}'."
                )
        except Exception as exc:
            QMessageBox.warning(self, "Create Folder Error", str(exc))

    def _on_rename_clicked(self) -> None:
        focused = self._get_focused_pane()
        if focused is self.remote_pane:
            self._rename_remote_selected()
        elif focused is self.local_pane:
            self._rename_local_selected()

    def _rename_local_selected(self) -> None:
        loc_entry = self.local_pane.get_selected_entry()
        if not loc_entry:
            return
        old_name = loc_entry.find_data.filename
        new_name, ok = QInputDialog.getText(self, "Rename", "Enter new name:", text=old_name)
        if not ok or not new_name or new_name == old_name:
            return
        old_path = os.path.join(self.local_current_path, old_name)
        new_path = os.path.join(self.local_current_path, new_name)
        try:
            os.rename(old_path, new_path)
            self.refresh_local()
        except Exception as exc:
            QMessageBox.warning(self, "Rename Error", str(exc))

    def _rename_remote_selected(self) -> None:
        rem_entry = self.remote_pane.get_selected_entry()
        if not rem_entry:
            return
        if rem_entry.is_drive:
            QMessageBox.warning(self, "Rename Error", "Cannot rename a drive.")
            return
        if not self.transfer or not self.session or not self.session.is_connected:
            QMessageBox.warning(self, "Rename Error", "Remote peer is not connected.")
            return
        old_name = rem_entry.find_data.filename
        new_name, ok = QInputDialog.getText(self, "Rename", "Enter new name:", text=old_name)
        if not ok or not new_name or new_name == old_name:
            return
        old_path = self._build_remote_path(old_name)
        new_path = self._build_remote_path(new_name)
        try:
            if rem_entry.is_dir:
                renamed = self.transfer.rename_dir(old_path, new_path)
            else:
                renamed = self.transfer.rename_file(old_path, new_path)
            if renamed:
                self.refresh_remote()
            else:
                QMessageBox.warning(self, "Rename Error", f"Failed to rename '{old_name}'.")
        except Exception as exc:
            QMessageBox.warning(self, "Rename Error", str(exc))

    def _build_remote_context_menu(self) -> QMenu:
        menu = QMenu(self)
        act_new_folder = menu.addAction("New Folder")
        act_new_folder.triggered.connect(self._create_remote_dir)
        act_rename = menu.addAction("Rename")
        act_rename.triggered.connect(self._rename_remote_selected)
        act_delete = menu.addAction("Delete")
        act_delete.triggered.connect(self._delete_remote_selected)
        return menu

    def _on_remote_context_menu(self, pos) -> None:
        self._set_focused_pane(self.remote_pane)
        menu = self._build_remote_context_menu()
        menu.exec(self.remote_pane.table.viewport().mapToGlobal(pos))

    def _show_about(self) -> None:
        dlg = AboutDialog(self)
        dlg.exec()
