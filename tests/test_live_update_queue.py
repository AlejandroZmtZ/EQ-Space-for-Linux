import threading
import time

from PySide6.QtWidgets import QApplication
from eqspace.core.filterchain.manager import FilterSpec
from eqspace.core.pipewire.registry import PwSnapshot
from eqspace.ui.main_window import MainWindow
from eqspace.ui.module_args_manager import ModuleArgsManager
from eqspace.core.filterchain.manager import FilterChainError
import pytest


class Registry:
    def snapshot(self):
        return PwSnapshot()
    def graph_rate(self, required=False):
        return 48000


def pump(app, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert condition()


def test_delayed_live_preamp_has_one_inflight_and_latest_pending(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(registry=Registry(), poll_interval_ms=0, restore_profile=False)
    window.mixer.action_dispatcher = None
    monkeypatch.setattr(window.mixer, "refresh", lambda: None)
    release = threading.Event()
    entered = threading.Event()
    calls = []
    ui_thread = threading.get_ident()

    class Manager:
        is_loaded = True
        node_name = "eqspace.test"
        def reload(self, specs):
            assert threading.get_ident() != ui_thread
            calls.append(specs[0].params["Mult"])
            entered.set()
            if len(calls) == 1:
                assert release.wait(2)

    window.peq.manager = Manager()
    window._automatic_headroom_enabled = False
    window.audio_graph.eq_enabled = True
    window.peq._last_good_specs = window.peq._filter_specs()
    monkeypatch.setattr(window.audio_graph, "is_path_verified", lambda: True)
    window.peq.preamp_spin.setValue(1)
    pump(app, entered.is_set)
    for value in (2, 3, 4, 5, 6):
        window.peq.preamp_spin.setValue(value)
        app.processEvents()
    assert len(calls) == 1
    assert window.peq.preamp_spin.value() == 6
    assert window._live_eq_pending
    release.set()
    pump(app, lambda: len(calls) == 2 and window._graph_worker is None)
    assert calls[-1] == pytest.approx(10 ** (6/20))
    assert window.peq.preamp_spin.value() == 6
    window._live_eq_timer.stop()
    window.audio_graph.eq_enabled = False
    monkeypatch.setattr(window.mixer, "is_routed", lambda: False)
    window.close_completely()


def test_spatial_timeout_after_effect_is_readback_success(monkeypatch):
    manager = ModuleArgsManager()
    manager._module_id = 1
    actual = {"hybrid_l:Gain 1": .7, "hybrid_l:Gain 2": .3}
    monkeypatch.setattr(manager, "_read_controls", lambda timeout: dict(actual))
    monkeypatch.setattr(manager, "_resolve_node_id", lambda *args: 2)
    monkeypatch.setattr(manager, "_readback_matches", lambda controls, timeout: all(actual[k] == v for k,v in controls.items()))
    def timeout(cmd, wait):
        actual.update({"hybrid_l:Gain 1": .6, "hybrid_l:Gain 2": .4})
        raise FilterChainError("timeout after write")
    monkeypatch.setattr(manager, "_run", timeout)
    manager.update_controls({"hybrid_l:Gain 1": .6, "hybrid_l:Gain 2": .4})
    assert actual["hybrid_l:Gain 1"] == .6


def test_spatial_failed_batch_restores_previous_controls(monkeypatch):
    manager = ModuleArgsManager()
    manager._module_id = 1
    actual = {"mix_l:Gain 1": 1.0, "mix_r:Gain 1": 1.0}
    monkeypatch.setattr(manager, "_read_controls", lambda timeout: dict(actual))
    monkeypatch.setattr(manager, "_resolve_node_id", lambda *args: 2)
    monkeypatch.setattr(manager, "_readback_matches", lambda controls, timeout: all(actual[k] == v for k,v in controls.items()))
    monkeypatch.setattr(manager, "_run", lambda *args: "")
    restored = []
    monkeypatch.setattr(manager, "_restore_controls", lambda previous, timeout: restored.append(previous))
    with pytest.raises(FilterChainError, match="previous controls restored"):
        manager.update_controls({"mix_l:Gain 1": .5, "mix_r:Gain 1": .5})
    assert restored == [actual]
