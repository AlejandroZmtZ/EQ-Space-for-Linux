import pytest

from eqspace.core.pipewire.registry import PwSnapshot


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakeRegistry:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot or PwSnapshot()

    def snapshot(self):
        return self._snapshot


def test_main_window_constructs_with_tabs(qapp):
    from eqspace.ui.main_window import MainWindow

    window = MainWindow(registry=FakeRegistry(), poll_interval_ms=0)
    assert window.windowTitle() == "EQ-Space"
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert titles == ["Mixer", "Parametric EQ", "Presets", "Spatial (Experimental)", "Mic (Experimental)"]
    assert window.styleSheet() != ""


def test_main_window_defaults_construct(qapp):
    # Smoke: no-arg construction must not crash even without PipeWire running.
    from eqspace.ui.main_window import MainWindow

    window = MainWindow(poll_interval_ms=0)
    assert window.tabs.count() == 5


def test_peq_tab_present_with_plot(qapp):
    from eqspace.ui.main_window import MainWindow

    window = MainWindow(registry=FakeRegistry(), poll_interval_ms=0)
    assert window.peq.plot is not None
    assert len(window.peq.bands) == 1


def test_quick_start_banner_instantiated_and_dismiss_toggle(qapp):
    from eqspace.ui.main_window import MainWindow

    window = MainWindow(registry=FakeRegistry(), poll_interval_ms=0, restore_profile=False)
    assert hasattr(window, "quick_start_banner")
    assert not window.quick_start_banner.isHidden()

    # When window is shown, banner is visible
    window.show()
    qapp.processEvents()
    assert window.quick_start_banner.isVisible()

    # Dismiss banner
    window.dismiss_button.click()
    assert window.quick_start_banner.isHidden()
    assert not window.quick_start_banner.isVisible()

    # Reopen via menu Help / Quick-Start action
    window.quick_start_action.trigger()
    assert not window.quick_start_banner.isHidden()
    assert window.quick_start_banner.isVisible()

    # Toggle via status bar button
    window.help_button.click()
    assert window.quick_start_banner.isHidden()

    # Toggle back on
    window.help_button.click()
    assert not window.quick_start_banner.isHidden()


def test_quick_start_banner_content(qapp):
    from PySide6.QtWidgets import QLabel
    from eqspace.ui.main_window import MainWindow

    window = MainWindow(registry=FakeRegistry(), poll_interval_ms=0, restore_profile=False)
    labels = [lbl.text() for lbl in window.quick_start_banner.findChildren(QLabel)]
    combined = " ".join(labels)
    assert "🚀 Quick-Start Guide" in combined
    assert "1. Play Audio" in combined
    assert "Start playback in your media player or browser" in combined
    assert "2. Choose Output" in combined
    assert "Choose your headphones or speakers" in combined
    assert "3. Enhance Sound" in combined
    assert "Select a preset and click Apply" in combined


def test_tab_tooltips_and_subtitles(qapp):
    from eqspace.ui.main_window import MainWindow

    window = MainWindow(registry=FakeRegistry(), poll_interval_ms=0, restore_profile=False)
    for i in range(window.tabs.count()):
        assert len(window.tabs.tabToolTip(i)) > 0
    assert "Choose where to listen" in window.mixer.subtitle_label.text()
    assert "Parametric EQ" in window.peq.subtitle_label.text()


def test_apply_profile_auto_enables_system_routing(qapp, monkeypatch):
    from eqspace.core.pipewire import control as ctrl_module
    from eqspace.core.profiles.models import EQProfile
    from eqspace.ui.main_window import MainWindow

    routing_calls = []
    routed = {"value": False}

    def fake_set_system_routing(enable, **kwargs):
        routing_calls.append(enable)
        routed["value"] = enable
        return enable

    monkeypatch.setattr(ctrl_module, "set_system_routing", fake_set_system_routing)
    monkeypatch.setattr(ctrl_module, "is_system_routed", lambda **kw: routed["value"])

    class Manager:
        is_loaded = False
        def load(self, specs):
            return 1
    window = MainWindow(registry=FakeRegistry(), filter_manager=Manager(), poll_interval_ms=0, restore_profile=False)
    from eqspace.core.profiles.models import BandModel
    prof = EQProfile(name="TestProf", bands=[BandModel(band_type="peaking", freq_hz=1000.0, gain_db=2.0, q=1.0)])
    window.apply_profile(prof, async_mode=False)

    assert routing_calls == [True]
    assert "TestProf" in window.peq.status_label.text()


def test_close_restores_direct_system_routing(qapp, monkeypatch):
    from eqspace.core.pipewire import control as ctrl_module
    from eqspace.ui.main_window import MainWindow

    restore_calls = []

    def fake_set_system_routing(enable, **kwargs):
        restore_calls.append(enable)
        return enable

    monkeypatch.setattr(ctrl_module, "set_system_routing", fake_set_system_routing)

    window = MainWindow(registry=FakeRegistry(), poll_interval_ms=0, restore_profile=False)
    # Simulate window currently routed
    monkeypatch.setattr(window.mixer, "is_routed", lambda: True)
    monkeypatch.setattr(window, "_is_tray_available", lambda: False)

    window.close_completely()
    assert restore_calls == [False]
