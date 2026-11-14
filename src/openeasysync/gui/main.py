# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""GUI entry point for OpenEasySync."""

import sys

from PySide6.QtWidgets import QApplication

from openeasysync.client.session import SessionManager
from openeasysync.gui.window import MainWindow
from openeasysync.transport.detector import detect_device


def main(argv: list[str] | None = None) -> int:
    """Launches the OpenEasySync Dual-Pane GUI."""
    app = QApplication(argv or sys.argv)
    app.setApplicationName("OpenEasySync")

    # Detect device if present
    transport = detect_device()
    session = None
    if transport:
        session = SessionManager(transport)
        try:
            session.connect()
        except Exception:
            pass

    win = MainWindow(session=session)
    win.show()

    ret = app.exec()
    if session:
        session.disconnect()
    return ret


if __name__ == "__main__":
    sys.exit(main())
