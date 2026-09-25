"""Profile persistence: JSON files under the XDG config directory.

Profiles live in ``$XDG_CONFIG_HOME/eqspace/profiles/`` (defaulting to
``~/.config`` when the variable is unset). Writes are atomic: content goes to
a temporary file in the same directory and is then renamed over the target.
"""

import json
import os
import tempfile
from pathlib import Path

from eqspace.core.profiles.models import EQProfile


def _current(profile: EQProfile) -> EQProfile:
    return profile.model_copy(update={"version": 2})


def profiles_dir() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "eqspace" / "profiles"


def _profile_path(name: str) -> Path:
    if not name or "/" in name or name in (".", ".."):
        raise ValueError(f"invalid profile name {name!r}")
    return profiles_dir() / f"{name}.json"


def save_profile(profile: EQProfile) -> Path:
    """Persist a profile, returning the path written. Atomic via rename."""
    path = _profile_path(profile.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_current(profile).model_dump_json(indent=2))
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise
    return path


def load_profile(name: str) -> EQProfile:
    path = _profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"no profile named {name!r} at {path}")
    return _current(EQProfile.model_validate_json(path.read_text(encoding="utf-8")))


def list_profiles() -> list[str]:
    directory = profiles_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json"))


def delete_profile(name: str) -> None:
    path = _profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"no profile named {name!r} at {path}")
    path.unlink()


def export_profile(profile: EQProfile, path: Path) -> None:
    """Export a profile to an arbitrary path. Atomic via rename."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_current(profile).model_dump_json(indent=2))
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise


def import_profile(path: Path) -> EQProfile:
    return _current(EQProfile.model_validate(json.loads(Path(path).read_text(encoding="utf-8"))))
