"""Offscreen tests for the Spatial tab (Tasks 5, 6, 7)."""

import numpy as np
import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakeExtractor:
    """Synthetic IRExtractor: unit impulses, no pysofa needed."""

    def extract(self, path, azimuths_deg):
        from eqspace.core.dsp.hrtf import ExtractedIRs

        irs = {
            float(az): (np.array([1.0, 0.0]), np.array([1.0, 0.0]))
            for az in azimuths_deg
        }
        return ExtractedIRs(sample_rate=48000.0, irs=irs)


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
def make_widget(qapp, tmp_path, monkeypatch):
    from eqspace.core.dsp import hrtf as hrtf_module
    from eqspace.ui.spatial import SpatialWidget

    monkeypatch.setattr(hrtf_module, "PySofaExtractor", FakeExtractor)
    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    hrtf_dir = tmp_path / "hrtf"
    hrtf_dir.mkdir()

    def _make(files=("dummy.sofa",), **kwargs):
        for name in files:
            (hrtf_dir / name).write_bytes(b"fake")
        manager = FakeManager()
        widget = SpatialWidget(
            manager=manager,
            hrtf_dir=hrtf_dir,
            pysofa_available=kwargs.pop("pysofa_available", lambda: True),
            **kwargs,
        )
        return widget, manager

    return _make


def test_constructs_and_lists_sofa_files(make_widget):
    widget, _ = make_widget(files=("a.sofa", "b.sofa"))
    # Item 0 is built-in KEMAR, followed by external files
    assert widget.sofa_combo.count() == 3
    assert widget.sofa_combo.itemText(0) == "Synthetic KEMAR model (Built-in)"
    assert widget.apply_button.isEnabled()


def test_no_pysofa_state(make_widget):
    # Built-in KEMAR works without pysofa, but selecting an external file disables apply
    widget, _ = make_widget(files=("external.sofa",), pysofa_available=lambda: False)
    assert widget.apply_button.isEnabled()  # Built-in selected by default

    widget.sofa_combo.setCurrentIndex(1)  # Select external file
    assert not widget.apply_button.isEnabled()
    assert "pysofa" in widget.status_label.text()


def test_no_sofa_files_state(make_widget):
    # Even without external files, built-in KEMAR ensures combo is never empty
    widget, _ = make_widget(files=())
    assert widget.sofa_combo.count() == 1
    assert widget.sofa_combo.itemText(0) == "Synthetic KEMAR model (Built-in)"
    assert widget.apply_button.isEnabled()


def test_apply_loads_51_chain(make_widget, monkeypatch):
    from eqspace.core.dsp import hrtf as hrtf_module

    monkeypatch.setattr(hrtf_module, "PySofaExtractor", FakeExtractor)
    widget, manager = make_widget()
    widget.sofa_combo.setCurrentIndex(0)
    widget.layout_combo.setCurrentText("5.1")
    widget.wetdry_slider.setValue(50)
    widget.apply_button.click()
    assert len(manager.loaded) == 1
    args = manager.loaded[0]
    assert "convolver" in args
    assert "audio.position = [ FL FR FC LFE SL SR ]" in args
    assert 'media.class = "Audio/Sink"' in args
    assert "conv_FL_L" in args and "conv_SL_L" in args


def test_apply_loads_71_chain(make_widget, monkeypatch):
    from eqspace.core.dsp import hrtf as hrtf_module

    monkeypatch.setattr(hrtf_module, "PySofaExtractor", FakeExtractor)
    widget, manager = make_widget()
    widget.sofa_combo.setCurrentIndex(0)
    widget.layout_combo.setCurrentText("7.1")
    widget.apply_button.click()
    assert len(manager.loaded) == 1
    args = manager.loaded[0]
    assert "convolver" in args
    assert "audio.position = [ FL FR FC LFE SL SR RL RR ]" in args
    assert "conv_RL_L" in args and "conv_RR_L" in args
    assert "Default KEMAR" in widget.status_label.text()


def test_crossfeed_mode_selection_and_disable(make_widget):
    widget, manager = make_widget()
    assert widget.layout_combo.isEnabled()
    assert not widget.crossfeed_combo.isEnabled()

    widget.crossfeed_check.setChecked(True)
    assert not widget.layout_combo.isEnabled()
    assert widget.crossfeed_combo.isEnabled()
    assert "disabled" in widget.layout_hint_label.text().lower()

    # Bauer mode
    widget.crossfeed_combo.setCurrentText("Bauer")
    widget.apply_button.click()
    assert len(manager.loaded) == 1
    args = manager.loaded[0]
    assert "eqspace.crossfeed" in args or "lp_l2r" in args
    assert "Bauer" in widget.status_label.text()

    # Meier mode
    widget.crossfeed_combo.setCurrentText("Meier")
    widget.apply_button.click()
    assert len(manager.loaded) == 2
    args2 = manager.loaded[1]
    assert "eqspace.crossfeed" in args2 or "lp_l2r" in args2
    assert "Meier" in widget.status_label.text()


def test_unload(make_widget):
    widget, manager = make_widget()
    widget.sofa_combo.setCurrentIndex(0)
    widget.apply_button.click()
    widget.unload_button.click()
    assert manager.unloaded == 1
    assert "unloaded" in widget.status_label.text()
