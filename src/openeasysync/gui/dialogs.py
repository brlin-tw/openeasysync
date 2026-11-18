# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""GUI Dialogs: Progress Dialog, Overwrite Confirmation, and About Box."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from openeasysync.client.transfer import format_rate


class ProgressDialog(QDialog):
    """Modal transfer progress dialog (Spec Part 3 Dialog ID 131)."""

    cancelled = Signal()

    def __init__(self, title: str = "Progress Information", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(450)
        self.setModal(True)

        layout = QVBoxLayout(self)

        self.lbl_src = QLabel("Source: ")
        self.lbl_src.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_src)

        self.lbl_dest = QLabel("Destination: ")
        self.lbl_dest.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.lbl_dest)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.lbl_speed = QLabel("Transfer Rate: -- KB/s")
        layout.addWidget(self.lbl_speed)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_box.addWidget(self.btn_cancel)
        layout.addLayout(btn_box)

    def set_paths(self, src: str, dest: str) -> None:
        self.lbl_src.setText(f"Source: {src}")
        self.lbl_dest.setText(f"Destination: {dest}")

    def update_progress(
        self, current: int, total: int, rate_kbs: float = 0.0, filename: str = ""
    ) -> None:
        rate_str = format_rate(rate_kbs)
        if total > 0:
            self.progress_bar.setRange(0, 100)
            pct = min(100, max(0, int((current / total) * 100)))
            self.progress_bar.setValue(pct)
            text = f"{pct}% — {current:,} / {total:,} bytes ({rate_str})"
        elif current > 0:
            self.progress_bar.setRange(0, 0)
            text = f"{current:,} bytes ({rate_str})"
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            text = f"0% — 0 / 0 bytes ({rate_str})"
        if filename:
            text += f" — {filename}"
        self.lbl_speed.setText(text)

    def _on_cancel(self) -> None:
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Cancelling...")
        self.cancelled.emit()
        self.reject()


class ConfirmReplaceDialog(QDialog):
    """Overwrite confirmation dialog (Spec Part 3 Dialog ID 133)."""

    def __init__(
        self,
        filename: str,
        existing_size: int,
        existing_date: str,
        new_size: int,
        new_date: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Confirm Replace File")
        self.setMinimumWidth(480)
        self.setModal(True)

        layout = QVBoxLayout(self)

        lbl_prompt = QLabel(
            f"This folder already contains a file named '<b>{filename}</b>'.<br><br>"
            f"Would you like to replace the existing file:<br>"
            f"&nbsp;&nbsp;&nbsp;&nbsp;<b>{existing_size} bytes</b>, modified {existing_date}<br><br>"
            f"with this one:<br>"
            f"&nbsp;&nbsp;&nbsp;&nbsp;<b>{new_size} bytes</b>, modified {new_date}?"
        )
        lbl_prompt.setTextFormat(Qt.RichText)
        layout.addWidget(lbl_prompt)

        btn_layout = QHBoxLayout()
        self.btn_yes = QPushButton("&Yes")
        self.btn_yes_all = QPushButton("Yes to &All")
        self.btn_no = QPushButton("&No")
        self.btn_cancel = QPushButton("&Cancel")

        self.btn_yes.clicked.connect(lambda: self.done(1))
        self.btn_yes_all.clicked.connect(lambda: self.done(2))
        self.btn_no.clicked.connect(lambda: self.done(0))
        self.btn_cancel.clicked.connect(lambda: self.done(-1))

        btn_layout.addWidget(self.btn_yes)
        btn_layout.addWidget(self.btn_yes_all)
        btn_layout.addWidget(self.btn_no)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)


class AboutDialog(QDialog):
    """About dialog (Spec Part 3 Dialog ID 174)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About OpenEasySync")
        self.setFixedSize(400, 220)

        layout = QVBoxLayout(self)
        title = QLabel("<h2>OpenEasySync</h2>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        desc = QLabel(
            "Cross-Platform Clean-Room Companion Application for the<br>"
            "<b>OTi-2108 USB Host-to-Host Data Bridge (0ea0:2108)</b><br><br>"
            "Application Version: 0.1.0 (EasySync 4.1.272.5 Compatible)<br>"
            "SPDX-License-Identifier: AGPL-3.0-or-later"
        )
        desc.setAlignment(Qt.AlignCenter)
        desc.setTextFormat(Qt.RichText)
        layout.addWidget(desc)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok)
        bbox.accepted.connect(self.accept)
        layout.addWidget(bbox)
