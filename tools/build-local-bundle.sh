#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Install PyInstaller separately; it is a build tool, not an app dependency.
.venv/bin/python -m PyInstaller --noconfirm --clean --onedir --name eqspace \
    --paths src --collect-data eqspace --copy-metadata eqspace \
    --collect-submodules scipy._external.array_api_compat \
    --exclude-module pytest --exclude-module tkinter \
    --specpath build --distpath dist --workpath build/pyinstaller \
    packaging/entrypoint.py
cp LICENSE dist/eqspace/LICENSE
cp data/desktop/eqspace.desktop dist/eqspace/
tar -C dist -czf dist/eqspace-0.1.0-linux-x86_64.tar.gz eqspace
sha256sum dist/eqspace-0.1.0-linux-x86_64.tar.gz > dist/eqspace-0.1.0-linux-x86_64.tar.gz.sha256
