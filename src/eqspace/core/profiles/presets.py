"""Built-in preset library shipped as JSON under ``eqspace/data/presets/``.

Each preset file carries ``name``, ``description``, ``research_basis`` and a
band list (plus optional spatial/mic settings), and converts to an
:class:`EQProfile` for use like any saved profile.
"""

from importlib import resources
from typing import Any

from pydantic import BaseModel, Field

from eqspace.core.profiles.models import BandModel, EQProfile

_PRESET_PACKAGE = "eqspace.data"
_PRESET_DIR = "presets"


class Preset(BaseModel):
    """A factory preset: an EQProfile payload plus documentation metadata."""

    name: str
    description: str
    research_basis: str
    bands: list[BandModel] = Field(default_factory=list)
    spatial: dict[str, Any] = Field(default_factory=dict)
    mic: dict[str, Any] = Field(default_factory=dict)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)

    def to_bands(self):
        return [band.to_eqband() for band in self.bands]

    def to_profile(self) -> EQProfile:
        return EQProfile(
            name=self.name,
            bands=self.bands,
            spatial=self.spatial,
            spatial_enabled=None,
            mic=self.mic,
            volume=self.volume,
            automatic_headroom=True,
        )


def _preset_dir():
    return resources.files(_PRESET_PACKAGE) / _PRESET_DIR


def list_presets() -> list[str]:
    return sorted(
        p.name[: -len(".json")]
        for p in _preset_dir().iterdir()
        if p.name.endswith(".json") and p.name != "crossfeed_bauer.json"
    )


def load_preset(name: str) -> Preset:
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"invalid preset name {name!r}")
    resource = _preset_dir() / f"{name}.json"
    if not resource.is_file():
        raise FileNotFoundError(f"no built-in preset named {name!r}")
    return Preset.model_validate_json(resource.read_text(encoding="utf-8"))


def preset_to_profile(name: str) -> EQProfile:
    return load_preset(name).to_profile()
