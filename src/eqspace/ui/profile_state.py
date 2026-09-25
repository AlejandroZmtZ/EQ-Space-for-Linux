"""Last-active-profile state, stored next to the profiles in the config dir.

Follows ``core/profiles/storage.py`` conventions: the state file lives at
``$XDG_CONFIG_HOME/eqspace/state.json`` (default ``~/.config/eqspace``).
A missing or corrupt file simply yields ``None`` so startup restore can
tolerate it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional


def state_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "eqspace" / "state.json"


def load_last_profile(path: Optional[Path] = None) -> Optional[str]:
    """Return the last active profile name, or None if absent/corrupt."""
    path = path or state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("last_profile")
    return name if isinstance(name, str) and name else None


def save_last_profile(name: Optional[str], path: Optional[Path] = None) -> Path:
    """Persist the last active profile name (None clears it). Atomic rename."""
    path = path or state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"last_profile": name}, fh)
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise
    return path
