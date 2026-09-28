"""Atomic, readback-verified profile storage and parametric text import."""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from eqspace.core.profiles.models import BandModel, EQProfile, SCHEMA_VERSION

Collision = Literal['keep_both', 'replace', 'cancel']


def _current(profile: EQProfile) -> EQProfile:
    return EQProfile.model_validate({**profile.model_dump(), "version": SCHEMA_VERSION})


def profiles_dir() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "eqspace" / "profiles"


def _profile_path(name: str) -> Path:
    if not name.strip() or any(c in name for c in ('/', '\\', '\x00')) or name in ('.', '..'):
        raise ValueError(f"invalid profile name {name!r}")
    return profiles_dir() / f"{name}.json"


def _write_verified(profile: EQProfile, path: Path) -> None:
    current = _current(profile)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(current.model_dump_json(indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        try:
            restored = EQProfile.model_validate_json(path.read_text(encoding='utf-8'))
        except Exception as exc:
            raise OSError(f"profile readback failed: {path}") from exc
        if restored != current:
            raise OSError(f"profile readback mismatch: {path}")
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def save_profile(profile: EQProfile) -> Path:
    """Persist atomically, then validate the exact saved snapshot."""
    path = _profile_path(profile.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_verified(profile, path)
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
    _profile_path(name).unlink()


def unique_profile_name(name: str) -> str:
    _profile_path(name)
    candidate, count = name, 2
    while _profile_path(candidate).exists():
        candidate = f'{name} ({count})'
        count += 1
    return candidate


def save_library_profile(profile: EQProfile, collision: Collision = 'keep_both') -> EQProfile | None:
    """Save with explicit collision policy; default preserves existing entries."""
    if collision not in ('keep_both', 'replace', 'cancel'):
        raise ValueError(f'unknown collision policy {collision!r}')
    name = profile.name
    if _profile_path(name).exists():
        if collision == 'cancel':
            return None
        if collision == 'keep_both':
            name = unique_profile_name(name)
    saved = _current(profile.model_copy(update={'name': name}, deep=True))
    save_profile(saved)
    return load_profile(name)


def rename_profile(name: str, new_name: str, collision: Collision = 'keep_both') -> EQProfile | None:
    source = load_profile(name)
    if name == new_name:
        return source
    saved = save_library_profile(source.model_copy(update={'name': new_name}), collision)
    if saved is not None:
        delete_profile(name)
    return saved


def duplicate_profile(name: str, new_name: str | None = None) -> EQProfile:
    result = save_library_profile(load_profile(name).model_copy(update={'name': new_name or name}))
    assert result is not None
    return result


def export_profile(profile: EQProfile, path: Path) -> None:
    _write_verified(profile, Path(path))


_NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
_PREAMP = re.compile(rf'Preamp:\s*({_NUMBER})\s*dB', re.IGNORECASE)
_FILTER = re.compile(rf'Filter\s+(\d+):\s*(ON|OFF)\s+(PK|LSC?|HSC?)\s+Fc\s+({_NUMBER})\s*Hz\s+Gain\s+({_NUMBER})\s*dB\s+Q\s+({_NUMBER})', re.IGNORECASE)
_TYPES = {'PK': 'peaking', 'LS': 'low_shelf', 'LSC': 'low_shelf', 'HS': 'high_shelf', 'HSC': 'high_shelf'}


def parse_parametric_text(text: str, name: str = 'Imported EQ') -> EQProfile:
    """Read the supported APO/AutoEQ parametric subset; never discard processing."""
    bands, preamp, seen_filters = [], 0.0, set()
    preamp_seen = False
    processed = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        processed = True
        try:
            if match := _PREAMP.fullmatch(line):
                if preamp_seen:
                    raise ValueError('duplicate Preamp directive')
                preamp = EQProfile(name=name, preamp_db=float(match[1])).preamp_db
                preamp_seen = True
            elif match := _FILTER.fullmatch(line):
                if int(match[1]) in seen_filters:
                    raise ValueError('duplicate filter number')
                seen_filters.add(int(match[1]))
                bands.append(BandModel(band_type=_TYPES[match[3].upper()], freq_hz=float(match[4]), gain_db=float(match[5]), q=float(match[6]), enabled=match[2].upper() == 'ON'))
            else:
                raise ValueError('unsupported or malformed processing; expected Preamp or ON/OFF PK, LS/LSC, HS/HSC with Fc, Gain and Q')
        except ValueError as exc:
            raise ValueError(f'line {number}: {exc}') from exc
    if not processed:
        raise ValueError('line 1: no supported EQ settings found')
    return EQProfile(name=name, bands=bands, preamp_db=preamp, scope='eq', eq_enabled=True, spatial_enabled=None, automatic_headroom=False)


def import_profile(path: Path) -> EQProfile:
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.txt':
        return parse_parametric_text(text, path.stem)
    return _current(EQProfile.model_validate(json.loads(text)))
