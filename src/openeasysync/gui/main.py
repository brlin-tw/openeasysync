# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""GUI entry point for OpenEasySync."""

import argparse
import sys

from PySide6.QtWidgets import QApplication

from openeasysync.client.session import SessionManager
from openeasysync.gui.window import MainWindow
from openeasysync.transport.detector import detect_device


def main(argv: list[str] | None = None) -> int:
    """Launches the OpenEasySync Dual-Pane GUI."""
    parser = argparse.ArgumentParser(
        prog="openeasysync-gui",
        description="OpenEasySync Dual-Pane File Manager GUI",
    )
    parser.add_argument(
        "-d",
        "--device",
        default=None,
        help="Specify device identifier (e.g. '1:4', '0', or '/dev/sg1'). Default: auto-select first available",
    )
    parsed_args, qt_args = parser.parse_known_args(argv or sys.argv[1:])

    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("OpenEasySync")

    # Detect device if present (auto-selecting free device if device_spec is None)
    transport = detect_device(parsed_args.device)
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
