"""Offscreen tests for the Mic tab (Task 8)."""

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakeManager:
    def __init__(self):
        self.loaded = []
        self.unloaded = 0

    @property
    def is_loaded(self):
        return bool(self.loaded)

    def load_args(self, args):
        self.loaded.append(args)
        return 1

    def update_args(self, args):
        self.loaded.append(args)
        return 1

    def unload(self):
        self.unloaded += 1
        self.loaded.clear()


@pytest.fixture()
def make_widget(qapp, tmp_path):
    from eqspace.ui.mic import MicWidget

    def _make(plugin_found=True, **kwargs):
        manager = FakeManager()
        widget = MicWidget(
            manager=manager,
            deepfilternet_available_fn=lambda: plugin_found,
            plugin_path=tmp_path / "libdf_ladspa.so",
            **kwargs,
        )
        return widget, manager

    return _make


def test_constructs_with_plugin_available(make_widget):
    widget, _ = make_widget(plugin_found=True)
    assert widget.apply_button.isEnabled()
    assert "found" in widget.status_label.text()


def test_plugin_missing_state(make_widget):
    widget, _ = make_widget(plugin_found=False)
    assert not widget.apply_button.isEnabled()
    assert "not found" in widget.status_label.text()


def test_apply_loads_chain_with_strength(make_widget):
    widget, manager = make_widget()
    widget.nr_check.setChecked(True)
    widget.strength_slider.setValue(40)
    widget.apply_button.click()
    assert len(manager.loaded) == 1
    assert '"Strength" = 0.4' in manager.loaded[0]
    assert "deep_filter_ladspa" in manager.loaded[0]


def test_apply_without_nr_enabled_does_nothing(make_widget):
    widget, manager = make_widget()
    widget.apply_button.click()
    assert manager.loaded == []


def test_unload(make_widget):
    widget, manager = make_widget()
    widget.nr_check.setChecked(True)
    widget.apply_button.click()
    widget.unload_button.click()
    assert manager.unloaded == 1


def test_mic_widget_has_level_meter(make_widget):
    from eqspace.core.pipewire.meter import PipeWireLevelMonitor
    from PySide6.QtWidgets import QProgressBar

    monitor = PipeWireLevelMonitor(level_fn=lambda: 0.65)
    widget, _ = make_widget(level_monitor=monitor)
    assert hasattr(widget, "level_bar")
    assert isinstance(widget.level_bar, QProgressBar)
    assert widget.meter_widget.isHidden()

    widget.update_meter()
    assert widget.level_bar.value() == 65
