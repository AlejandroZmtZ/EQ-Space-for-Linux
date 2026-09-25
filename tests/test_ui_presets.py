"""Offscreen tests for the Presets tab (Task 8)."""

import pytest
from pydantic import BaseModel, Field

from eqspace.core.profiles.models import BandModel


class FakePreset(BaseModel):
    name: str
    description: str
    research_basis: str
    bands: list[BandModel] = Field(default_factory=list)

    def to_profile(self):
        from eqspace.core.profiles.models import EQProfile

        return EQProfile(name=self.name, bands=self.bands)


FAKE_PRESETS = {
    "warm": FakePreset(
        name="Warm",
        description="Gentle warmth",
        research_basis="Based on oliveira2023.",
        bands=[BandModel(band_type="peaking", freq_hz=200.0, gain_db=2.0, q=1.0)],
    ),
    "bright": FakePreset(
        name="Bright",
        description="Air boost",
        research_basis="Based on harman target.",
        bands=[],
    ),
}


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def widget(qapp, tmp_path, monkeypatch):
    from eqspace.ui.presets import PresetsWidget

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return PresetsWidget(
        list_presets_fn=lambda: sorted(FAKE_PRESETS),
        load_preset_fn=lambda name: FAKE_PRESETS[name],
    )


def test_presets_list_populated(widget):
    assert widget.preset_list.count() == 2
    assert "bright" in [
        widget.preset_list.item(i).text() for i in range(widget.preset_list.count())
    ]


def test_selection_shows_description_and_research(widget):
    widget.preset_list.setCurrentRow(0)
    assert widget.description_label.text() == FAKE_PRESETS["bright"].description
    assert widget.research_label.text() == FAKE_PRESETS["bright"].research_basis


def test_preview_emits_profile(widget, qapp):
    from eqspace.core.profiles.models import EQProfile

    seen = []
    widget.preset_previewed.connect(seen.append)
    widget.preset_list.setCurrentRow(1)  # "warm"
    widget.preview_button.click()
    assert len(seen) == 1
    assert isinstance(seen[0], EQProfile)
    assert seen[0].name == "Warm"
    assert len(seen[0].bands) == 1


def test_save_as_profile_persists(widget, qapp, tmp_path, monkeypatch):
    from eqspace.core.profiles import storage

    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    widget.preset_list.setCurrentRow(0)
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("my-warm", True))
    )
    widget.save_button.click()
    profile = storage.load_profile("my-warm")
    assert profile.name == "my-warm"
    assert len(profile.bands) == 0
