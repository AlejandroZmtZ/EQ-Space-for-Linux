"""Tests for MainWindow tray integration and closeEvent behavior."""

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from eqspace.core.pipewire.registry import PwSnapshot
from eqspace.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeRegistry:
    def snapshot(self):
        return PwSnapshot()


class FakeFilterManager:
    @property
    def is_loaded(self):
        return False
    def load(self, specs):
        return 1


def test_main_window_has_tray_manager(qapp):
    window = MainWindow(
        registry=FakeRegistry(),
        filter_manager=FakeFilterManager(),
        poll_interval_ms=0,
        restore_profile=False,
    )
    assert hasattr(window, "tray_manager")
    assert window.tray_manager is not None


def test_close_event_hides_when_tray_available(qapp, monkeypatch):
    window = MainWindow(
        registry=FakeRegistry(),
        filter_manager=FakeFilterManager(),
        poll_interval_ms=0,
        restore_profile=False,
    )
    window.show()
    assert window.isVisible()

    monkeypatch.setattr(window, "_is_tray_available", lambda: True)

    event = QCloseEvent()
    window.closeEvent(event)

    # When tray is available, event is ignored and window hides
    assert event.isAccepted() is False
    assert not window.isVisible()


def test_tray_profile_selected_calls_apply_profile(qapp, monkeypatch):
    from eqspace.core.profiles.models import BandModel, EQProfile

    window = MainWindow(
        registry=FakeRegistry(),
        filter_manager=FakeFilterManager(),
        poll_interval_ms=0,
        restore_profile=False,
    )
    dummy_prof = EQProfile(
        name="test_prof",
        bands=[BandModel(band_type="peaking", freq_hz=1000.0, gain_db=2.0, q=1.0)],
    )
    monkeypatch.setattr("eqspace.core.profiles.storage.load_profile", lambda name: dummy_prof)

    window.tray_manager.profile_selected.emit("test_prof")
    assert window.peq.bands[0].gain_db == 2.0
