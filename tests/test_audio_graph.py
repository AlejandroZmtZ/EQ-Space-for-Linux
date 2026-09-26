"""Graph transitions must keep the last verified path when staging fails."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from eqspace.core.pipewire.registry import PwNode, PwSnapshot
from eqspace.ui.audio_graph import AudioGraphController, AudioGraphError
from eqspace.core.pipewire.control import PipeWireControlError
from eqspace.core.filterchain.limiter import LimiterCapability


@dataclass
class FakeEQ:
    is_loaded: bool = False
    node_name: str = "eqspace.filter-chain"


class FakeRegistry:
    def __init__(self):
        self.sinks = {"alsa.output"}

    def snapshot(self):
        return PwSnapshot(sinks=tuple(
            PwNode(i, name, name, "Audio/Sink", None, None)
            for i, name in enumerate(sorted(self.sinks), 1)
        ))


class FakeModule:
    def __init__(self, registry, state):
        self.registry = registry
        self.state = state
        self.is_loaded = False
        self.name = None

    def load_args(self, args):
        if self.state["fail_load"] == "before":
            raise RuntimeError("load failed before module creation")
        import re
        self.name = re.search(r'capture.props = \{ node.name = "([^"]+)"', args).group(1)
        self.registry.sinks.add(self.name)
        self.is_loaded = True
        if self.state["fail_load"] == "after":
            raise RuntimeError("load failed after module creation")

    def unload(self):
        self.registry.sinks.discard(self.name)
        self.is_loaded = False


@pytest.fixture
def graph(monkeypatch):
    from eqspace.core.pipewire import control

    registry = FakeRegistry()
    eq = FakeEQ()
    state = {"default": "alsa.output", "links": {}, "fail_channel": None,
             "fail_route": False, "fail_load": None, "fail_verify_target": None,
             "fail_direct_verify": False,
             "modules": []}

    def factory():
        module = FakeModule(registry, state)
        state["modules"].append(module)
        return module

    def link(source, target):
        for channel in ("FL", "FR"):
            if channel == state["fail_channel"]:
                continue
            state["links"][(source, channel)] = target

    def verify_link(source, target):
        for channel in ("FL", "FR"):
            if state["links"].get((source, channel)) != target:
                raise RuntimeError(f"unverified {channel} link")

    def route(enable, filter_node_name=None, fallback_sink_name=None, **_):
        if state["fail_route"]:
            raise RuntimeError("route rejected")
        state["default"] = filter_node_name if enable else fallback_sink_name

    def verify_route(target, _registry):
        if state["fail_verify_target"] == target:
            state["fail_route"] = True
            raise RuntimeError("active stream link verification failed")
        if target == "alsa.output" and state["fail_direct_verify"]:
            raise RuntimeError("direct stream links are not verified")
        if state["default"] != target:
            raise RuntimeError("default route unverified")

    monkeypatch.setattr(control, "link_filter_output", link)
    monkeypatch.setattr(control, "verify_filter_output", verify_link)
    monkeypatch.setattr(control, "set_system_routing", route)
    monkeypatch.setattr(control, "verify_playback_route", verify_route)
    controller = AudioGraphController(registry, lambda: "alsa.output", lambda: eq, factory)
    return controller, eq, registry, state


ARGS = ('capture.props = { node.name = "eqspace.spatial" } '
        'playback.props = { node.name = "eqspace.spatial.playback" }')


@pytest.mark.parametrize("eq_on,spatial_on", [(False, False), (True, False),
                                             (False, True), (True, True)])
def test_four_effect_states(graph, eq_on, spatial_on):
    controller, eq, registry, state = graph
    if eq_on:
        eq.is_loaded = True
        registry.sinks.add(eq.node_name)
        controller.eq_applied()
    if spatial_on:
        controller.switch_spatial(ARGS)
    assert state["default"] == (controller.spatial_name or eq.node_name if eq_on or spatial_on else "alsa.output")
    if eq_on and spatial_on:
        assert state["links"][(controller.spatial_name, "FL")] == eq.node_name
        assert state["links"][(eq.node_name, "FR")] == "alsa.output"


def test_spatial_off_preserves_eq(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    controller.switch_spatial(ARGS)
    controller.spatial_off()
    assert state["default"] == eq.node_name
    assert controller.spatial_name is None
    assert eq.is_loaded


def test_limiter_stage_is_inserted_after_eq_and_removed_cleanly(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    capability = LimiterCapability(
        Path("/tmp/lsp.lv2/limiter_stereo.ttl"), 21,
        ("in_l", "in_r"), ("out_l", "out_r"), "out_latency",
    )

    controller.set_limiter(True, capability)
    limiter = controller.limiter_name
    assert limiter is not None
    assert state["links"][(eq.node_name, "FL")] == limiter
    assert state["links"][(limiter, "FR")] == "alsa.output"
    assert controller.is_eq_active()

    controller.set_limiter(False, capability)
    assert controller.limiter_name is None
    assert state["links"][(eq.node_name, "FR")] == "alsa.output"
    assert controller.is_eq_active()


def test_limiter_remains_available_when_eq_is_bypassed(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    capability = LimiterCapability(
        Path("/tmp/lsp.lv2/limiter_stereo.ttl"), 21,
        ("in_l", "in_r"), ("out_l", "out_r"), "out_latency",
    )
    controller.set_limiter(True, capability)
    limiter = controller.limiter_name

    controller.eq_off()
    assert state["default"] == limiter
    assert state["links"][(limiter, "FL")] == "alsa.output"
    assert not controller.is_eq_active()

    controller.set_limiter(False, capability)
    assert controller.limiter_name is None
    assert state["default"] == "alsa.output"


def test_spatial_and_limiter_remain_connected_when_eq_is_bypassed(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    capability = LimiterCapability(
        Path("/tmp/lsp.lv2/limiter_stereo.ttl"), 21,
        ("in_l", "in_r"), ("out_l", "out_r"), "out_latency",
    )
    controller.set_limiter(True, capability)
    spatial = controller.switch_spatial(ARGS)
    limiter = controller.limiter_name

    controller.eq_off()
    assert state["default"] == spatial
    assert state["links"][(spatial, "FL")] == limiter
    assert state["links"][(limiter, "FR")] == "alsa.output"

    controller.set_limiter(False, capability)
    assert state["links"][(spatial, "FR")] == "alsa.output"
    assert state["default"] == spatial


def test_partial_channel_link_never_replaces_old_spatial(graph):
    controller, _, registry, state = graph
    old = controller.switch_spatial(ARGS)
    state["fail_channel"] = "FR"
    with pytest.raises(AudioGraphError, match="unverified"):
        controller.switch_spatial(ARGS)
    assert controller.spatial_name == old
    assert state["default"] == old
    assert old in registry.sinks
    assert not state["modules"][-1].is_loaded


def test_failed_route_retains_old_module(graph):
    controller, _, registry, state = graph
    old = controller.switch_spatial(ARGS)
    state["fail_route"] = True
    with pytest.raises(AudioGraphError, match="previous path unverified"):
        controller.switch_spatial(ARGS)
    assert old in registry.sinks
    assert controller.spatial_name == old


def test_failed_rollback_retains_candidate_module(graph, monkeypatch):
    controller, _, registry, state = graph
    old = controller.switch_spatial(ARGS)
    candidate_name = "eqspace.spatial.candidate"
    state["fail_verify_target"] = candidate_name
    import eqspace.ui.audio_graph as audio_graph
    class CandidateUUID:
        hex = "candidate"
    monkeypatch.setattr(audio_graph.uuid, "uuid4", lambda: CandidateUUID())
    with pytest.raises(AudioGraphError, match="previous path unverified"):
        controller.switch_spatial(ARGS)
    candidate = state["modules"][-1]
    assert candidate.is_loaded
    assert candidate.name in registry.sinks
    assert old in registry.sinks
    assert state["default"] == candidate_name
    state["fail_route"] = False
    controller.shutdown()
    assert not candidate.is_loaded
    assert state["default"] == "alsa.output"


@pytest.mark.parametrize("point", ["before", "after"])
def test_module_load_failure_preserves_last_verified_route(graph, point):
    controller, _, registry, state = graph
    old = controller.switch_spatial(ARGS)
    state["fail_load"] = point
    with pytest.raises(AudioGraphError, match="load failed"):
        controller.switch_spatial(ARGS)
    assert controller.spatial_name == old
    assert state["default"] == old
    assert state["modules"][-1].is_loaded is False
    assert old in registry.sinks


def test_sink_readiness_is_polled_before_route(graph, monkeypatch):
    controller, _, registry, state = graph
    original_snapshot = registry.snapshot
    calls = 0

    def delayed_snapshot():
        nonlocal calls
        calls += 1
        result = original_snapshot()
        if calls == 2:
            return PwSnapshot(sinks=tuple(s for s in result.sinks if s.name == "alsa.output"))
        return result

    monkeypatch.setattr(registry, "snapshot", delayed_snapshot)
    controller.switch_spatial(ARGS)
    assert calls >= 3


def test_route_retries_transient_sink_discovery(graph, monkeypatch):
    from eqspace.core.pipewire import control

    controller, _, _, state = graph
    original_route = control.set_system_routing
    attempts = 0

    def delayed_route(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PipeWireControlError("no such sink: staged module")
        return original_route(*args, **kwargs)

    monkeypatch.setattr(control, "set_system_routing", delayed_route)
    staged = controller.switch_spatial(ARGS)
    assert attempts == 2
    assert state["default"] == staged


def test_shutdown_verifies_direct_handoff_before_unloading_modules(graph):
    controller, _, registry, state = graph
    controller.switch_spatial(ARGS)
    manager = controller.spatial_manager
    assert manager is not None and manager.is_loaded

    controller.shutdown()

    assert state["default"] == "alsa.output"
    assert manager.is_loaded is False
    assert manager.name not in registry.sinks


def test_shutdown_keeps_modules_alive_when_direct_handoff_is_unverified(graph):
    controller, _, registry, state = graph
    controller.switch_spatial(ARGS)
    manager = controller.spatial_manager
    assert manager is not None
    state["fail_direct_verify"] = True

    with pytest.raises(RuntimeError, match="direct stream links"):
        controller.shutdown()

    assert manager.is_loaded
    assert manager.name in registry.sinks


def test_stream_disappearing_between_snapshots_is_not_a_failed_route(monkeypatch):
    from eqspace.core.pipewire import control

    stream = PwNode(42, "Brave", None, "Stream/Output/Audio", None, None)

    class Registry:
        def snapshot(self):
            return PwSnapshot(streams=())

    def stale(*args, **kwargs):
        raise PipeWireControlError("no such stream node: Brave")

    monkeypatch.setattr(control, "move_stream", stale)
    observations = iter([{"Brave:output_FL": ["old:playback_FL"]}, {}])
    monkeypatch.setattr(control, "get_active_output_links", lambda **kwargs: next(observations))
    control._move_observed_stream(stream, "eqspace.test", Registry(), None, 1.0)

    class StillActive:
        def snapshot(self):
            return PwSnapshot(streams=(stream,))

    monkeypatch.setattr(control, "get_active_output_links",
                        lambda **kwargs: {"Brave:output_FL": ["old:playback_FL"]})
    with pytest.raises(PipeWireControlError, match="no such stream"):
        control._move_observed_stream(stream, "eqspace.test", StillActive(), None, 1.0)


def test_combined_status_verifies_every_stage(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    spatial = controller.switch_spatial(ARGS)
    cap = LimiterCapability(Path('/tmp/limiter_stereo.ttl'), 21,
                            ('in_l', 'in_r'), ('out_l', 'out_r'), 'out_latency')
    controller.set_limiter(True, cap)
    assert controller.active_stages() == ('Spatial', 'EQ', 'LSP Limiter')
    assert controller.is_path_verified()
    state['links'][(spatial, 'FR')] = 'wrong.output'
    assert not controller.is_path_verified()


def test_output_change_with_eq_bypassed_moves_limiter(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    cap = LimiterCapability(Path('/tmp/limiter_stereo.ttl'), 21,
                            ('in_l', 'in_r'), ('out_l', 'out_r'), 'out_latency')
    controller.set_limiter(True, cap)
    controller.switch_spatial(ARGS)
    controller.eq_off()
    registry.sinks.add('new.output')
    controller.set_output('new.output')
    assert state['links'][(controller.limiter_name, 'FR')] == 'new.output'


def test_failed_limiter_rollback_retains_owner(graph, monkeypatch):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    cap = LimiterCapability(Path('/tmp/limiter_stereo.ttl'), 21,
                            ('in_l', 'in_r'), ('out_l', 'out_r'), 'out_latency')
    state['fail_load'] = 'after'
    state['fail_channel'] = 'FR'
    state['links'][(eq.node_name, 'FR')] = 'wrong.output'
    with pytest.raises(AudioGraphError, match='previous path unverified'):
        controller.set_limiter(True, cap)
    assert state['modules'][-1].is_loaded
    assert controller.has_owned_modules


def test_failed_output_change_restores_limiter_and_previous_device(graph, monkeypatch):
    from eqspace.core.pipewire import control
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    cap = LimiterCapability(Path('/tmp/limiter_stereo.ttl'), 21,
                            ('in_l', 'in_r'), ('out_l', 'out_r'), 'out_latency')
    controller.set_limiter(True, cap)
    registry.sinks.add('new.output')
    original = control.link_filter_output
    def partially_fail(source, target):
        original(source, target)
        if target == 'new.output':
            raise RuntimeError('device disappeared after linking')
    monkeypatch.setattr(control, 'link_filter_output', partially_fail)
    with pytest.raises(AudioGraphError, match='previous path restored'):
        controller.set_output('new.output')
    assert controller.is_path_verified()
    assert state['links'][(controller.limiter_name, 'FR')] == 'alsa.output'


def test_limiter_control_mismatch_restores_eq_without_publishing_limiter(graph, monkeypatch):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    def reject_controls(self, controls, timeout):
        assert controls['limiter:boost'] == 0
        assert controls['limiter:alr'] == 0
        raise RuntimeError('limiter controls unverified')
    monkeypatch.setattr(FakeModule, 'verify_controls_from_mapping', reject_controls, raising=False)
    cap = LimiterCapability(Path('/tmp/limiter_stereo.ttl'), 21,
                            ('in_l', 'in_r'), ('out_l', 'out_r'), 'out_latency')
    with pytest.raises(AudioGraphError, match='controls unverified'):
        controller.set_limiter(True, cap)
    assert controller.limiter_name is None
    assert not state['modules'][-1].is_loaded
    assert controller.is_eq_active()


def test_spatial_headroom_prepared_before_route_and_restored_after_failure(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    observed = []
    controller.on_spatial_changed = lambda peak: observed.append((peak, state['default']))
    spatial = controller.switch_spatial(ARGS, 4)
    assert observed[0] == (4, eq.node_name)
    assert controller.spatial_peak_db == 4
    state['fail_load'] = 'before'
    with pytest.raises(AudioGraphError):
        controller.switch_spatial(ARGS, 7)
    assert observed[-1] == (4, spatial)
    assert controller.spatial_peak_db == 4


def test_boosted_spatial_retains_native_trim_when_eq_bypassed(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    spatial = controller.switch_spatial(ARGS, 4)
    controller.eq_off()
    assert controller.output_gain_name
    assert controller.output_gain_db == -5
    assert state['links'][(spatial, 'FL')] == controller.output_gain_name
    assert state['links'][(controller.output_gain_name, 'FR')] == 'alsa.output'
    assert controller.is_path_verified()
    eq.is_loaded = False
    controller.shutdown()
    assert not any(module.is_loaded for module in state['modules'])


def test_eq_on_prepares_cached_gain_before_reconnecting(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    controller.eq_off()
    calls = []
    controller.on_eq_enabling = lambda: calls.append(state['default'])
    controller.eq_on()
    assert calls == ['alsa.output']


def test_failed_headroom_prepare_does_not_publish_spatial(graph):
    controller, _, _, state = graph
    def reject(peak):
        raise RuntimeError('gain readback failed')
    controller.on_spatial_changed = reject
    with pytest.raises(AudioGraphError, match='gain readback failed'):
        controller.switch_spatial(ARGS, 3)
    assert state['default'] == 'alsa.output'
    assert controller.spatial_name is None


def test_switch_to_quieter_spatial_keeps_old_peak_reserve_until_publish(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    old = controller.switch_spatial(ARGS, 6)
    observed = []
    controller.on_spatial_changed = lambda peak: observed.append((peak, state['default']))
    new = controller.switch_spatial(ARGS, 2)
    assert observed == [(6, old), (2, new)]


def test_boosted_spatial_limiter_removal_keeps_trim_stage(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    cap = LimiterCapability(Path('/tmp/limiter_stereo.ttl'), 21,
                            ('in_l', 'in_r'), ('out_l', 'out_r'), 'out_latency')
    controller.set_limiter(True, cap)
    spatial = controller.switch_spatial(ARGS, 5)
    controller.eq_off()
    controller.set_limiter(False)
    assert controller.limiter_name is None
    assert state['links'][(spatial, 'FR')] == controller.output_gain_name
    assert state['links'][(controller.output_gain_name, 'FL')] == 'alsa.output'
    assert controller.is_path_verified()
    registry.sinks.add('new.output')
    controller.set_output('new.output')
    assert state['links'][(controller.output_gain_name, 'FR')] == 'new.output'


def test_trim_stage_failure_keeps_eq_enabled_and_working(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    spatial = controller.switch_spatial(ARGS, 4)
    state['fail_load'] = 'after'
    with pytest.raises(RuntimeError, match='load failed'):
        controller.eq_off()
    assert controller.eq_enabled
    assert state['links'][(spatial, 'FR')] == eq.node_name
    assert controller.is_path_verified()


def test_eq_enable_aborts_before_route_on_failed_gain_verification(graph):
    controller, eq, registry, state = graph
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    controller.eq_off()
    def reject():
        raise RuntimeError('cached gain unverified')
    controller.on_eq_enabling = reject
    with pytest.raises(RuntimeError, match='cached gain unverified'):
        controller.eq_on()
    assert not controller.eq_enabled
    assert state['default'] == 'alsa.output'


def test_repeated_spatial_switches_retire_obsolete_gain_owners(graph):
    controller, eq, registry, state = graph
    for peak in (3, 6, 2, 5, 0, 4):
        controller.switch_spatial(ARGS, peak)
        assert sum(module.is_loaded for module in state['modules']) == (2 if peak > 0 else 1)
        assert not controller._retained_managers
    eq.is_loaded = True
    registry.sinks.add(eq.node_name)
    controller.eq_applied()
    assert controller.output_gain_manager is None
    assert sum(module.is_loaded for module in state['modules']) == 1
    controller.eq_off()
    controller.spatial_off()
    assert not any(module.is_loaded for module in state['modules'])


def test_gain_cleanup_does_not_retire_unverified_candidate(graph):
    controller, _, registry, state = graph
    controller.switch_spatial(ARGS, 3)
    state['fail_route'] = True
    with pytest.raises(AudioGraphError):
        controller.switch_spatial(ARGS, 6)
    retained = tuple(controller._retained_managers)
    assert retained and all(module.is_loaded for module in retained)
    state['fail_route'] = False
    controller.switch_spatial(ARGS, 2)
    assert all(module.is_loaded for module in retained)
    assert all(module in controller._retained_managers for module in retained)


def test_old_spatial_unload_failure_is_retained_for_shutdown_retry(graph, monkeypatch):
    controller, _, _, state = graph
    controller.switch_spatial(ARGS, 3)
    previous = controller.spatial_manager
    original = previous.unload
    attempts = 0
    def fail_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('owner cleanup failed')
        original()
    monkeypatch.setattr(previous, 'unload', fail_once)
    controller.switch_spatial(ARGS, 5)
    assert controller.is_path_verified()
    assert previous.is_loaded
    assert previous in controller._retained_managers
    controller.shutdown()
    assert attempts == 2
    assert not any(module.is_loaded for module in state['modules'])
