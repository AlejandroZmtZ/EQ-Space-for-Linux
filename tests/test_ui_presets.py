"""Offscreen Presets tab tests."""

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
        name="Warm", description="Gentle warmth", research_basis="Research note",
        bands=[BandModel(band_type="peaking", freq_hz=200.0, gain_db=2.0, q=1.0)],
    ),
    "bright": FakePreset(
        name="Bright", description="Air boost", research_basis="Research note",
        bands=[BandModel(band_type="peaking", freq_hz=4000.0, gain_db=2.0, q=1.0)],
    ),
}


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def widget(qapp, tmp_path, monkeypatch):
    from eqspace.ui.presets import PresetsWidget
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return PresetsWidget(
        list_presets_fn=lambda: sorted(FAKE_PRESETS),
        load_preset_fn=lambda name: FAKE_PRESETS[name],
    )


def test_selection_shows_details_without_applying(widget):
    seen = []
    widget.preset_apply_requested.connect(seen.append)
    widget.preset_list.setCurrentRow(0)
    assert widget.description_label.text() == FAKE_PRESETS["bright"].description
    assert widget.research_label.text() == FAKE_PRESETS["bright"].research_basis
    assert seen == []
    assert not hasattr(widget, "preview_button")


def test_apply_waits_for_result_and_blocks_repeat(widget):
    seen = []
    widget.preset_apply_requested.connect(seen.append)
    widget.preset_list.setCurrentRow(1)
    widget.apply_button.click()
    widget._on_apply()  # direct repeat, even if a click was disabled
    assert len(seen) == 1
    assert seen[0].name == "Warm"
    assert widget.apply_button.text() == "Applying…"
    assert not widget.apply_button.isEnabled()
    widget.set_apply_result(True, "Applied")
    assert widget.apply_button.text() == "Active ✓"
    assert "active" in widget.status_label.text()


def test_failure_keeps_prior_active_selection(widget):
    widget.preset_list.setCurrentRow(0)
    widget.apply_button.click()
    widget.set_apply_result(True, "Applied")
    widget.preset_list.setCurrentRow(1)
    widget.apply_button.click()
    widget.set_apply_result(False, "backend error")
    assert "backend error" in widget.status_label.text()
    assert widget.apply_button.text() == "Apply"
    widget.preset_list.setCurrentRow(0)
    assert widget.apply_button.text() == "Active ✓"


def test_bypass_changes_active_preset_to_loaded(widget):
    widget.preset_list.setCurrentRow(0)
    widget.apply_button.click()
    widget.set_apply_result(True, "Applied")
    widget.set_eq_enabled(False)
    assert "EQ off" in widget.status_label.text()
    assert widget.apply_button.text() == "Apply"
    widget.set_eq_enabled(True)
    assert widget.apply_button.text() == "Active ✓"


def test_save_as_profile_persists(widget, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from eqspace.core.profiles import storage
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    widget.preset_list.setCurrentRow(0)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("my-bright", True)))
    widget.save_button.click()
    assert storage.load_profile("my-bright").name == "my-bright"
