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
    from PySide6.QtWidgets import QApplication

    from eqspace.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_profiles:
        return cmd_list_profiles()
    return _run_gui()
