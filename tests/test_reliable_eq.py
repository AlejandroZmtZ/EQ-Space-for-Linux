"""Control readback, profile preamp, and responsive editor regressions."""

import json

import pytest
import numpy as np

from eqspace.core.dsp.biquads import magnitude_response
from eqspace.core.dsp.filter_design import EQBand, design_filters
from eqspace.core.filterchain.manager import FilterChainError, FilterChainManager, FilterSpec
from eqspace.core.profiles.models import EQProfile
from eqspace.core.profiles import storage
from eqspace.core.pipewire.registry import PipeWireRegistry, PipeWireUnavailable


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


OLD = [FilterSpec("preamp", "linear", {"Mult": 1.0, "Add": 0.0}),
       FilterSpec("band_0", "bq_peaking", {"Freq": 1000.0, "Gain": 0.0, "Q": 1.0})]
NEW = [FilterSpec("preamp", "linear", {"Mult": 0.5, "Add": 0.0}),
       FilterSpec("band_0", "bq_peaking", {"Freq": 1200.0, "Gain": 4.0, "Q": 1.0})]


class LiveFake:
    def __init__(self, mode):
        self.mode = mode
        self.node_id = 95
        self.values = FilterChainManager._controls(OLD)
        self.set_calls = []

    def __call__(self, cmd, timeout):
        if cmd[0] == "pw-dump":
            pairs = [item for key, value in self.values.items() for item in (key, value)]
            return json.dumps([{"id": self.node_id, "info": {
                "props": {"node.name": "eqspace.test", "media.class": "Audio/Sink"},
                "params": {"Props": [{"params": pairs}]}}}])
        if cmd[:2] == ["pw-cli", "set-param"]:
            self.set_calls.append(list(cmd))
            attempt = len(self.set_calls)
            requested = dict(zip(json.loads(cmd[4])["params"][::2],
                                 json.loads(cmd[4])["params"][1::2]))
            if self.mode == "before" and attempt == 1:
                raise RuntimeError("set-param timed out")
            if self.mode == "stale" and attempt == 1:
                self.node_id = 96
                raise RuntimeError("stale node id")
            if self.mode == "partial" and attempt <= 2:
                self.values["preamp:Mult"] = requested["preamp:Mult"]
                return ""
            if self.mode == "restore_fail" and attempt == 3:
                raise RuntimeError("restore rejected")
            if self.mode == "restore_fail" and attempt <= 2:
                self.values["preamp:Mult"] = requested["preamp:Mult"]
                return ""
            self.values.update(requested)
            if self.mode == "after" and attempt == 1:
                raise RuntimeError("set-param timed out")
            return ""
        raise AssertionError(cmd)


@pytest.mark.parametrize("mode,calls", [("before", 2), ("after", 1), ("stale", 2)])
def test_reload_recovers_timeout_or_stale_id(mode, calls):
    fake = LiveFake(mode)
    manager = FilterChainManager(node_name="eqspace.test", runner=fake)
    manager._module_id = 77
    manager._active_filters = tuple(OLD)
    manager._active_channels = ("FL", "FR")
    assert manager.reload(NEW) == 77
    assert len(fake.set_calls) == calls
    assert fake.values == FilterChainManager._controls(NEW)
    if mode == "stale":
        assert fake.set_calls[-1][2] == "96"


def test_partial_update_restores_verified_controls():
    fake = LiveFake("partial")
    manager = FilterChainManager(node_name="eqspace.test", runner=fake)
    manager._module_id = 77
    manager._active_filters = tuple(OLD)
    manager._active_channels = ("FL", "FR")
    with pytest.raises(FilterChainError, match="previous controls restored"):
        manager.reload(NEW)
    assert fake.values == FilterChainManager._controls(OLD)
    assert manager._active_filters == tuple(OLD)
    assert len(fake.set_calls) == 3


def test_failed_restoration_reports_unverified_state():
    fake = LiveFake("restore_fail")
    manager = FilterChainManager(node_name="eqspace.test", runner=fake)
    manager._module_id = 77
    manager._active_filters = tuple(OLD)
    manager._active_channels = ("FL", "FR")
    with pytest.raises(FilterChainError, match="unverified"):
        manager.reload(NEW, timeout=0.05)


def test_profile_v1_migrates_to_v3_with_gain_behavior_intact(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = storage.profiles_dir() / "older.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"name": "older", "version": 1, "bands": []}))
    profile = storage.load_profile("older")
    assert profile.version == 3 and profile.preamp_db == 0
    assert profile.automatic_headroom is False
    assert profile.spatial_enabled is None
    storage.save_profile(profile.model_copy(update={"preamp_db": -6.0}))
    assert json.loads(path.read_text())["version"] == 3
    assert storage.load_profile("older").preamp_db == -6.0


def test_preamp_gain_and_invalid_bands():
    assert 10 ** (-6 / 20) == pytest.approx(0.501187)
    for fs in (44100, 48000, 96000):
        coeffs = design_filters([EQBand("peaking", 1000, 6, 1)], fs)
        response = magnitude_response(coeffs, np.array([1000.0]), fs)
        assert response[0] == pytest.approx(6.0, abs=0.01)
        assert response[0] - 6 == pytest.approx(0, abs=0.01)
    with pytest.raises(ValueError, match="lower this band"):
        design_filters([EQBand("peaking", 20000, 2, 1)], 32000)
    with pytest.raises(ValueError, match="finite"):
        EQBand("peaking", 1000, float("nan"), 1)
    with pytest.raises(ValueError, match="preamp_db"):
        EQProfile(name="bad", preamp_db=13)


def test_graph_rate_reads_pipewire_metadata():
    registry = PipeWireRegistry(runner=lambda cmd, timeout:
                                "update: id:0 key:'clock.rate' value:'44100' type:''")
    assert registry.graph_rate() == 44100


def test_apply_requires_known_graph_rate():
    def unavailable(cmd, timeout):
        raise RuntimeError("PipeWire settings unavailable")

    registry = PipeWireRegistry(runner=unavailable)
    assert registry.graph_rate() == 48000
    with pytest.raises(PipeWireUnavailable, match="Cannot read PipeWire graph rate"):
        registry.graph_rate(required=True)


def test_edit_keeps_widget_and_focus(qapp):
    from eqspace.ui.peq.peq_widget import PeqWidget
    widget = PeqWidget()
    widget.show()
    spin = widget.table.cellWidget(0, 1)
    spin.setFocus()
    qapp.processEvents()
    widget.set_band(0, freq_hz=1200)
    assert widget.table.cellWidget(0, 1) is spin
    assert spin.hasFocus()
    original = widget.curve_item.getData()[1].copy()
    widget.set_preamp(-6)
    assert widget._filter_specs()[0].params["Mult"] == pytest.approx(0.501187)
    assert np.allclose(widget.curve_item.getData()[1], original - 6, atol=0.01)
    assert "Estimated combined peak" in widget.peak_label.text()
    widget.fs = 32000
    widget.set_band(0, freq_hz=20000)
    assert "Response unavailable" == widget.peak_label.text()
    assert "lower this band" in widget.status_label.text()
    assert not widget.apply()
    widget.close()
