"""Offscreen tests for startup profile restore and PEQ save-as wiring (Task 8)."""

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakeRegistry:
    def __init__(self, snapshot=None):
        from eqspace.core.pipewire.registry import PwSnapshot

        self._snapshot = snapshot or PwSnapshot()

    def snapshot(self):
        return self._snapshot


class FakeFilterManager:
    """Stands in for FilterChainManager on the PEQ path."""

    def __init__(self):
        self.loaded = []

    @property
    def is_loaded(self):
        return False

    def load(self, specs):
        self.loaded.append(list(specs))
        return 1


def _profile(name, freq=500.0, gain=3.0):
    from eqspace.core.profiles.models import BandModel, EQProfile

    return EQProfile(
        name=name,
        bands=[BandModel(band_type="peaking", freq_hz=freq, gain_db=gain, q=1.0)],
    )


def test_startup_loads_last_profile_without_routing(qapp, tmp_path, monkeypatch):
    routing_calls = []
    monkeypatch.setattr(
        "eqspace.ui.main_window._control.set_system_routing",
        lambda *a, **k: routing_calls.append((a, k)),
    )
    from eqspace.core.profiles import storage
    from eqspace.ui.main_window import MainWindow
    from eqspace.ui.profile_state import save_last_profile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    storage.save_profile(_profile("studio", freq=250.0, gain=4.0))
    save_last_profile("studio")

    manager = FakeFilterManager()
    window = MainWindow(
        registry=FakeRegistry(), filter_manager=manager, poll_interval_ms=0
    )
    assert manager.loaded == []
    assert routing_calls == []
    assert window.peq.bands[0].freq_hz == 250.0
    assert window.peq.bands[0].gain_db == 4.0
    assert "click Apply" in window.peq.status_label.text()
    from eqspace.core.dsp.filter_design import EQBand

    assert isinstance(window.peq.bands[0], EQBand)


def test_startup_tolerates_missing_profile(qapp, tmp_path, monkeypatch):
    from eqspace.ui.main_window import MainWindow
    from eqspace.ui.profile_state import save_last_profile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    save_last_profile("does-not-exist")
    manager = FakeFilterManager()
    window = MainWindow(
        registry=FakeRegistry(), filter_manager=manager, poll_interval_ms=0
    )
    assert manager.loaded == []
    assert len(window.peq.bands) == 1  # default band untouched


def test_startup_tolerates_missing_state_file(qapp, tmp_path, monkeypatch):
    from eqspace.ui.main_window import MainWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    window = MainWindow(
        registry=FakeRegistry(),
        filter_manager=FakeFilterManager(),
        poll_interval_ms=0,
    )
    assert len(window.peq.bands) == 1


def test_save_as_profile_from_peq(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from eqspace.core.profiles import storage
    from eqspace.ui.main_window import MainWindow
    from eqspace.ui.profile_state import load_last_profile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    window = MainWindow(
        registry=FakeRegistry(),
        filter_manager=FakeFilterManager(),
        poll_interval_ms=0,
        restore_profile=False,
    )
    monkeypatch.setattr(window.presets, "_request_save_details", lambda profile: ("my-preset", "playback"))
    window.peq.save_profile_requested.emit(list(window.peq.bands))
    profile = storage.load_profile("my-preset")
    assert profile.name == "my-preset"
    assert len(profile.bands) == 1
    assert load_last_profile() == "my-preset"


def test_save_captures_spatial_and_mic(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from eqspace.core.profiles import storage
    from eqspace.ui.main_window import MainWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    window = MainWindow(
        registry=FakeRegistry(),
        filter_manager=FakeFilterManager(),
        poll_interval_ms=0,
        restore_profile=False,
    )
    window.spatial.set_state({"layout": "7.1", "wet": 40, "crossfeed": True})
    window.mic.set_state({"enabled": True, "strength": 80})

    monkeypatch.setattr(window.presets, "_request_save_details", lambda profile: ("full-setup", "playback"))
    window.peq.save_profile_requested.emit(list(window.peq.bands))

    profile = storage.load_profile("full-setup")
    assert profile.spatial.get("layout") == "7.1"
    assert profile.spatial.get("wet") == 40
    assert profile.spatial.get("crossfeed") is True
    assert profile.mic.get("enabled") is True
    assert profile.mic.get("strength") == 80


def test_apply_profile_restores_spatial_and_mic(qapp, tmp_path, monkeypatch):
    from eqspace.core.profiles.models import BandModel, EQProfile
    from eqspace.ui.main_window import MainWindow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("eqspace.ui.main_window._control.set_system_routing", lambda *a, **k: True)
    monkeypatch.setattr("eqspace.ui.main_window._control.is_system_routed", lambda **k: True)
    window = MainWindow(
        registry=FakeRegistry(),
        filter_manager=FakeFilterManager(),
        poll_interval_ms=0,
        restore_profile=False,
    )
    profile = EQProfile(
        name="restored",
        bands=[BandModel(band_type="peaking", freq_hz=1000.0, gain_db=0.0, q=1.0)],
        spatial={"layout": "5.1", "wet": 50, "crossfeed": True},
        mic={"enabled": True, "strength": 75},
    )
    window.apply_profile(profile)

    assert window.spatial.layout_combo.currentText() == "5.1"
    assert window.spatial.wetdry_slider.value() == 50
    assert window.spatial.crossfeed_check.isChecked() is True
    assert window.mic.nr_check.isChecked() is True
    assert window.mic.strength_slider.value() == 75
