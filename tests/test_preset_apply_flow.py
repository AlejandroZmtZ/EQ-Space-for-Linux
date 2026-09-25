"""End-to-end GUI preset apply lifecycle with a fake PipeWire backend."""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from eqspace.core.dsp.filter_design import EQBand
from eqspace.core.pipewire.registry import PwSnapshot
from eqspace.core.profiles.models import EQProfile
from eqspace.core.profiles.presets import list_presets
from eqspace.ui.main_window import MainWindow


class FakeRegistry:
    def snapshot(self):
        return PwSnapshot()


class FakeManager:
    def __init__(self):
        self.is_loaded = False
        self.calls = []
        self.fail = False
        self.gate = None

    def load(self, specs):
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail:
            raise RuntimeError("backend rejected graph")
        self.calls.append(("load", list(specs)))
        self.is_loaded = True
        return len(self.calls)

    def reload(self, specs):
        if self.fail:
            self.fail = False
            self.is_loaded = False
            raise RuntimeError("backend rejected graph")
        self.calls.append(("reload", list(specs)))
        self.is_loaded = True
        return len(self.calls)

    def unload(self):
        self.calls.append(("unload", []))
        self.is_loaded = False


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def setup_window(qapp, monkeypatch):
    from eqspace.core.pipewire import control
    state = {"routed": False, "fail_route": False, "calls": [], "route_threads": []}

    def route(enable, **kwargs):
        state["calls"].append(enable)
        state["route_threads"].append(threading.get_ident())
        if enable and state["fail_route"]:
            if state["fail_route"] == "once":
                state["fail_route"] = False
            raise RuntimeError("default sink rejected")
        state["routed"] = enable
        return enable

    monkeypatch.setattr(control, "set_system_routing", route)
    manager = FakeManager()
    window = MainWindow(registry=FakeRegistry(), filter_manager=manager,
                        poll_interval_ms=0, restore_profile=False)
    monkeypatch.setattr(window.mixer, "is_routed", lambda: state["routed"])
    monkeypatch.setattr(window, "_is_tray_available", lambda: False)
    yield window, manager, state
    if window._profile_apply_busy:
        if manager.gate is not None:
            manager.gate.set()
        wait_done(qapp, window)
    window.close_completely()


def wait_done(qapp, window, timeout=5):
    deadline = time.monotonic() + timeout
    while window._profile_apply_busy and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert not window._profile_apply_busy


@pytest.mark.parametrize("name", list_presets())
def test_each_core_preset_finishes_active(setup_window, qapp, name):
    window, manager, state = setup_window
    names = [window.presets.preset_list.item(i).text() for i in range(window.presets.preset_list.count())]
    assert name in names
    window.presets.preset_list.setCurrentRow(names.index(name))
    window.presets.apply_button.click()
    assert window.presets.apply_button.text() == "Applying…"
    wait_done(qapp, window)
    assert window.presets.apply_button.text() == "Active ✓"
    assert state["routed"]
    assert state["route_threads"] and all(t == threading.get_ident() for t in state["route_threads"])
    assert manager.is_loaded
    assert manager.calls[0][1]


def test_repeated_apply_clicks_start_one_operation(setup_window, qapp):
    window, manager, _ = setup_window
    gate = threading.Event()
    manager.gate = gate
    window.presets.preset_list.setCurrentRow(0)
    window.presets.apply_button.click()
    window.presets._on_apply()
    assert window.presets.apply_button.text() == "Applying…"
    gate.set()
    wait_done(qapp, window)
    assert len([call for call in manager.calls if call[0] == "load"]) == 1


def test_mixer_bypass_updates_preset_indication(setup_window, qapp):
    window, _, state = setup_window
    window.presets.preset_list.setCurrentRow(0)
    window.presets.apply_button.click()
    wait_done(qapp, window)
    assert window.presets.apply_button.text() == "Active ✓"
    window.mixer.routing_button.click()
    assert not state["routed"]
    assert window.presets.apply_button.text() == "Apply"
    assert "EQ off" in window.presets.status_label.text()


def test_manual_peq_apply_clears_old_preset_active_label(setup_window, qapp):
    window, _, _ = setup_window
    window.presets.preset_list.setCurrentRow(0)
    window.presets.apply_button.click()
    wait_done(qapp, window)
    assert window.presets.apply_button.text() == "Active ✓"
    window.peq.apply()
    assert window.presets.apply_button.text() == "Apply"
    assert window.presets.status_label.text() == ""


def test_backend_failure_restores_previous_chain(setup_window, qapp):
    window, manager, state = setup_window
    window.presets.preset_list.setCurrentRow(0)
    window.presets.apply_button.click()
    wait_done(qapp, window)
    first_specs = manager.calls[-1][1]
    manager.fail = True
    window.presets.preset_list.setCurrentRow(1)
    window.presets.apply_button.click()
    wait_done(qapp, window)
    assert "backend rejected graph" in window.presets.status_label.text()
    assert manager.calls[-1] == ("load", first_specs)
    assert state["routed"]
    assert window.presets.apply_button.text() == "Apply"


def test_routing_failure_unloads_new_chain(setup_window, qapp):
    window, manager, state = setup_window
    state["fail_route"] = True
    window.presets.preset_list.setCurrentRow(0)
    window.presets.apply_button.click()
    wait_done(qapp, window)
    assert "routing failed" in window.presets.status_label.text()
    assert not manager.is_loaded
    assert not state["routed"]
    assert window.presets.apply_button.text() == "Apply"
    assert window.peq._last_good_specs is None


def test_unverified_new_chain_unloads_before_retry(setup_window, qapp, monkeypatch):
    window, manager, state = setup_window
    monkeypatch.setattr(manager, "verify_controls", lambda specs: (_ for _ in ()).throw(
        RuntimeError("EQ controls could not be verified")), raising=False)
    window.presets.preset_list.setCurrentRow(0)
    window.presets.apply_button.click()
    wait_done(qapp, window)
    assert not manager.is_loaded
    assert window.peq._last_good_specs is None
    assert not state["routed"]
    assert "could not be verified" in window.presets.status_label.text()


def test_second_routing_failure_restores_previous_chain_and_route(setup_window, qapp, monkeypatch):
    window, manager, state = setup_window
    window.presets.preset_list.setCurrentRow(0)
    window.presets.apply_button.click()
    wait_done(qapp, window)
    first_specs = manager.calls[-1][1]
    state["fail_route"] = "once"
    checks = iter([True, False])
    monkeypatch.setattr(window.mixer, "is_routed", lambda: next(checks, state["routed"]))
    window.presets.preset_list.setCurrentRow(1)
    window.presets.apply_button.click()
    wait_done(qapp, window)
    assert "routing failed" in window.presets.status_label.text()
    assert manager.calls[-1] == ("reload", first_specs)
    assert state["routed"]
    assert state["calls"][-2:] == [True, True]


def test_close_during_apply_waits_for_completion(setup_window, qapp):
    window, manager, state = setup_window
    gate = threading.Event()
    manager.gate = gate
    window.show()
    window.presets.preset_list.setCurrentRow(0)
    window.presets.apply_button.click()
    window.close_completely()
    assert window.isVisible()
    gate.set()
    wait_done(qapp, window)
    assert not window.isVisible()
    assert state["calls"][-1] is False


def test_layout_replacement_routes_direct_before_backend_and_restores_on_failure(setup_window, qapp):
    window, manager, state = setup_window
    window.presets.preset_list.setCurrentRow(0)
    window.presets.apply_button.click()
    wait_done(qapp, window)
    previous = manager.calls[-1][1]
    profile = EQProfile.from_bands("changed", [EQBand("low_shelf", 500, 3, 1)])
    manager.fail = True
    result = []
    window.apply_profile(profile, async_mode=True, on_done=lambda ok, msg: result.append((ok, msg)))
    assert state["calls"][-1] is False
    wait_done(qapp, window)
    assert result and not result[0][0]
    assert manager.calls[-1] == ("load", previous)
    assert state["routed"]


def test_failed_replacement_and_restore_keeps_direct_output(setup_window, qapp, monkeypatch):
    window, manager, state = setup_window
    window.presets.preset_list.setCurrentRow(0)
    window.presets.apply_button.click()
    wait_done(qapp, window)
    profile = EQProfile.from_bands("changed", [EQBand("low_shelf", 500, 3, 1)])
    manager.fail = True
    monkeypatch.setattr(manager, "load", lambda specs: (_ for _ in ()).throw(RuntimeError("restore rejected")))
    result = []
    window.apply_profile(profile, async_mode=True, on_done=lambda ok, msg: result.append((ok, msg)))
    wait_done(qapp, window)
    assert result and not result[0][0]
    assert not state["routed"]
    assert window.peq._last_good_specs is None
    window.presets.preset_list.setCurrentRow(0)
    assert window.presets.apply_button.text() == "Apply"
