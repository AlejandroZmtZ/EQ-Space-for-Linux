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


def test_startup_restores_last_profile(qapp, tmp_path, monkeypatch):
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
    assert len(manager.loaded) == 1
    assert window.peq.bands[0].freq_hz == 250.0
    assert window.peq.bands[0].gain_db == 4.0
    # The loaded FilterSpec carries the restored band params.
    spec = manager.loaded[0][0]
    assert spec.params["Freq"] == 250.0
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
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("my-preset", True))
    )
    window.peq.save_profile_requested.emit(list(window.peq.bands))
    profile = storage.load_profile("my-preset")
    assert profile.name == "my-preset"
    assert len(profile.bands) == 1
    assert load_last_profile() == "my-preset"
