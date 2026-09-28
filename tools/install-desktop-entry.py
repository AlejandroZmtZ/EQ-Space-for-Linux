#!/usr/bin/env python3
"""Install the current Python environment's launcher and branded user icons."""

from importlib import resources
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    executable = Path(sys.prefix) / "bin" / "eqspace"
    if not executable.is_file():
        raise SystemExit("Install EQ-Space in this Python environment first.")
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    applications = data_home / "applications"
    theme = data_home / "icons/hicolor"
    package = resources.files("eqspace.data")

    # Desktop Exec quoting follows the desktop-entry specification.
    quoted = str(executable).replace("%", "%%")
    for character in ("\\", '"', "`", "$"):
        quoted = quoted.replace(character, "\\" + character)
    desktop = (package / "desktop/eqspace.desktop").read_text(encoding="utf-8")
    desktop = desktop.replace("Exec=eqspace", f'Exec="{quoted}"')
    applications.mkdir(parents=True, exist_ok=True)
    temporary = applications / "eqspace.desktop.tmp"
    temporary.write_text(desktop, encoding="utf-8")
    temporary.chmod(0o644)
    os.replace(temporary, applications / "eqspace.desktop")

    svg = package / "icons/eqspace.svg"
    scalable = theme / "scalable/apps"
    scalable.mkdir(parents=True, exist_ok=True)
    (scalable / "eqspace.svg").write_bytes(svg.read_bytes())
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    with resources.as_file(svg) as svg_path:
        icon = QIcon(str(svg_path))
        for size in (16, 32, 48, 64, 128, 256):
            target = theme / f"{size}x{size}/apps"
            target.mkdir(parents=True, exist_ok=True)
            pixmap = icon.pixmap(size, size)
            if pixmap.isNull() or not pixmap.save(str(target / "eqspace.png")):
                raise SystemExit(f"Could not render the {size}px logo.")
    os.utime(theme, None)
    updater = shutil.which("update-desktop-database")
    if updater:
        subprocess.run([updater, str(applications)], check=True)
    print(f"Installed EQ-Space launcher and logo in {data_home}.")
    print("Open EQ-Space from the application menu after installation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
