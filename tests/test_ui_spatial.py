"""Offscreen tests for the Spatial tab (Task 8)."""

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
    assert widget.sofa_combo.count() == 2
    assert widget.apply_button.isEnabled()


def test_no_pysofa_state(make_widget):
    widget, _ = make_widget(pysofa_available=lambda: False)
    assert not widget.apply_button.isEnabled()
    assert "pysofa" in widget.status_label.text()


def test_no_sofa_files_state(make_widget):
    widget, _ = make_widget(files=())
    assert not widget.apply_button.isEnabled()
    assert "No SOFA files" in widget.status_label.text()


def test_apply_loads_chain(make_widget, monkeypatch):
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


def test_crossfeed_uses_stereo_pair(make_widget, monkeypatch):
    from eqspace.core.dsp import hrtf as hrtf_module

    monkeypatch.setattr(hrtf_module, "PySofaExtractor", FakeExtractor)
    widget, manager = make_widget()
    widget.crossfeed_check.setChecked(True)
    widget.sofa_combo.setCurrentIndex(0)
    widget.apply_button.click()
    args = manager.loaded[0]
    assert "azm30" in args and "azp30" in args
    assert "azm110" not in args


def test_unload(make_widget):
    widget, manager = make_widget()
    widget.sofa_combo.setCurrentIndex(0)
    widget.apply_button.click()
    widget.unload_button.click()
    assert manager.unloaded == 1
