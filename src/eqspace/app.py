"""GUI launcher and read-only profile listing command."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from eqspace.core.profiles import storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eqspace",
        description="EQ-Space for Linux — GUI audio equalizer for PipeWire.",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="print saved profile names and exit",
    )
    parser.add_argument("--debug", action="store_true", help="record verbose diagnostic logs")
    return parser


def cmd_list_profiles() -> int:
    names = storage.list_profiles()
    if names:
        for name in names:
            print(name)
    else:
        print("No profiles saved yet.")
    return 0


def _run_gui() -> int:
    import os
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon

    from eqspace.ui.main_window import MainWindow

    app = QApplication([sys.argv[0]])

    icon_path = os.path.join(os.path.dirname(__file__), "data", "icons", "eqspace.svg")
    svg_icon = QIcon(icon_path)
    app_icon = QIcon()
    # Export useful sizes to desktop shells, including X11's _NET_WM_ICON.
    # An SVG-only QIcon can otherwise advertise just a tiny fallback icon.
    for size in (16, 32, 48, 64, 128, 256):
        app_icon.addPixmap(svg_icon.pixmap(size, size))
    app.setWindowIcon(app_icon)
    app.setApplicationName("eqspace")
    app.setApplicationDisplayName("EQ-Space")
    app.setDesktopFileName("eqspace")

    window = MainWindow()
    window.setWindowIcon(app_icon)
    window.show()
    return app.exec()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    from eqspace.diagnostics import configure_logging
    configure_logging(debug=args.debug)
    if args.list_profiles:
        return cmd_list_profiles()
    return _run_gui()
