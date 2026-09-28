"""One owner for the playback path between application streams and the selected output."""

from __future__ import annotations

import re
import uuid
import logging
import time
from typing import Callable

from eqspace.core.pipewire import control
from eqspace.core.filterchain.limiter import LimiterCapability, render_limiter_args
from eqspace.core.pipewire.registry import PipeWireRegistry
from eqspace.ui.module_args_manager import ModuleArgsManager


class AudioGraphError(RuntimeError):
    pass


logger = logging.getLogger(__name__)
_PRESERVE = object()


class AudioGraphController:
    """Stage spatial modules and publish only routes verified from PipeWire links."""

    def __init__(self, registry: PipeWireRegistry, physical_output: Callable[[], str | None],
                 eq_manager: Callable[[], object], manager_factory=ModuleArgsManager) -> None:
        self.registry = registry
        self.physical_output = physical_output
        self.eq_manager = eq_manager
        self._committed_eq_manager = None
        self.atomic_transitions = False
        self.on_progress: Callable[[str], None] | None = None
        self.spatial_args: str | None = None
        self.limiter_capability: LimiterCapability | None = None
        self.manager_factory = manager_factory
        self.spatial_manager: ModuleArgsManager | None = None
        self.spatial_name: str | None = None
        self.eq_enabled = False
        self.spatial_peak_db = 0.0
        self.output_gain_manager = None
        self.output_gain_name: str | None = None
        self.output_gain_db = 0.0
        self.on_eq_enabling: Callable[[], None] | None = None
        self.limiter_manager: ModuleArgsManager | None = None
        self.limiter_name: str | None = None
        self._retained_managers: list[ModuleArgsManager] = []
        self._retired_output_gain_managers: list[ModuleArgsManager] = []
        self.on_spatial_changed: Callable[[float], None] | None = None

    def _progress(self, phase: str) -> None:
        logger.info("playback transition: %s", phase)
        if self.on_progress:
            self.on_progress(phase)

    @property
    def has_owned_modules(self) -> bool:
        eq_manager = self._active_eq_manager()
        return bool(
            self.spatial_manager or self.limiter_manager or self.output_gain_manager or self._retained_managers or self._retired_output_gain_managers
            or getattr(eq_manager, "is_loaded", False)
        )

    def retain_until_shutdown(self, manager: ModuleArgsManager) -> None:
        """Keep an unverified staged owner alive until a later safe handoff."""
        if manager.is_loaded and manager not in self._retained_managers:
            self._retained_managers.append(manager)

    def _physical(self) -> str:
        name = self.physical_output()
        if not name or name.startswith("eqspace."):
            raise AudioGraphError("selected physical output is unavailable")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(sink.name == name for sink in self.registry.snapshot().sinks):
                return name
            time.sleep(0.05)
        raise AudioGraphError("selected physical output is unavailable")

    def _active_eq_manager(self):
        current = self.eq_manager()
        if current is self._committed_eq_manager:
            self._committed_eq_manager = None
        return self._committed_eq_manager or current

    def _eq_name(self) -> str | None:
        manager = self._active_eq_manager()
        return getattr(manager, "node_name", "eqspace.filter-chain") if manager.is_loaded else None

    def _prepare_output_gain(self, peak_db: float, physical: str) -> str | None:
        """Own a native stereo headroom stage when Spatial bypasses EQ."""
        gain_db = -(peak_db + 1.0) if peak_db > 0.0 else 0.0
        if gain_db == 0.0:
            # Preserve an already verified conservative stage until EQ or
            # Spatial is disabled; bypassing it on a nonpositive estimate can
            # leave the graph route inconsistent during live level changes.
            return self.output_gain_name if self.output_gain_manager else None
        if self.output_gain_manager and self.output_gain_db == gain_db:
            self._link(self.output_gain_name, self.limiter_name or physical)
            return self.output_gain_name
        name = f"eqspace.output.{uuid.uuid4().hex[:12]}"
        gain = 10.0 ** (gain_db / 20.0)
        args = (
            'node.description = "EQ-Space Spatial headroom" '
            'filter.graph = { nodes = [ '
            f'{{ type = builtin name = gain_l label = linear control = {{ Mult = {gain:.12g} Add = 0 }} }} '
            f'{{ type = builtin name = gain_r label = linear control = {{ Mult = {gain:.12g} Add = 0 }} }} '
            '] inputs = [ "gain_l:In" "gain_r:In" ] '
            'outputs = [ "gain_l:Out" "gain_r:Out" ] } '
            f'capture.props = {{ node.name = "{name}" media.class = "Audio/Sink" '
            'audio.channels = 2 audio.position = [ FL FR ] } '
            f'playback.props = {{ node.name = "{name}.playback" node.passive = true node.autoconnect = false '
            'audio.channels = 2 audio.position = [ FL FR ] }'
        )
        manager = self.manager_factory()
        try:
            manager.load_args(args)
            self._wait_sink(name)
            manager.node_name = name
            if hasattr(manager, "verify_controls_from_mapping"):
                manager.verify_controls_from_mapping({
                    "gain_l:Mult": gain, "gain_r:Mult": gain,
                    "gain_l:Add": 0.0, "gain_r:Add": 0.0,
                }, 2.0)
            self._link(name, self.limiter_name or physical)
        except Exception:
            if manager.is_loaded:
                manager.unload()  # This stage has not been connected to playback.
            raise
        if self.output_gain_manager:
            self._retired_output_gain_managers.append(self.output_gain_manager)
        self.output_gain_manager = manager
        self.output_gain_name = name
        self.output_gain_db = gain_db
        return name

    def _retire_unused_output_gains(self) -> None:
        """Retire only known gain owners after the current playback path is verified."""
        if not self.is_path_verified():
            return
        needed = bool(self.spatial_name and not self.eq_enabled and self.output_gain_name)
        if not needed and self.output_gain_manager:
            self._retired_output_gain_managers.append(self.output_gain_manager)
            self.output_gain_manager = None
            self.output_gain_name = None
            self.output_gain_db = 0.0
        for manager in tuple(self._retired_output_gain_managers):
            try:
                if manager.is_loaded:
                    manager.unload()
                self._retired_output_gain_managers.remove(manager)
            except Exception:
                logger.exception("obsolete Spatial headroom module cleanup failed")

    def _spatial_output(self, peak_db: float, physical: str) -> str:
        if self.eq_enabled:
            return self._eq_name() or physical
        return self._prepare_output_gain(peak_db, physical) or self.limiter_name or physical

    def _wait_sink(self, name: str, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(s.name == name for s in self.registry.snapshot().sinks):
                return
            time.sleep(0.05)
        raise AudioGraphError(f"staged sink {name} did not appear")

    @staticmethod
    def _renamed_args(args: str, name: str) -> str:
        matches = list(re.finditer(r'node.name\s*=\s*"eqspace\.(?:spatial|crossfeed)(?:\.playback)?"', args))
        if len(matches) != 2:
            raise AudioGraphError("spatial module args do not expose capture and playback nodes")
        old = re.search(r'"(eqspace\.(?:spatial|crossfeed))"', matches[0].group())
        assert old is not None
        base = old.group(1)
        return args.replace(f'node.name = "{base}.playback"', f'node.name = "{name}.playback"').replace(
            f'node.name = "{base}"', f'node.name = "{name}"'
        )

    def _link(self, source: str, target: str) -> None:
        control.link_filter_output(source, target)
        deadline = time.monotonic() + 2.0
        while True:
            try:
                control.verify_filter_output(source, target)
                return
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _stages(self, *, spatial=None, eq=None, limiter=None, gain=None) -> list[str]:
        return [name for name in (spatial, eq or gain, limiter) if name]

    def _current_stages(self) -> list[str]:
        gain = self.output_gain_name if self.spatial_name and not self.eq_enabled else None
        return self._stages(spatial=self.spatial_name,
                            eq=self._eq_name() if self.eq_enabled else None,
                            limiter=self.limiter_name, gain=gain)

    def _observe_path(self, stages: list[str], physical: str) -> None:
        for source, target in zip(stages, stages[1:] + [physical]):
            control.verify_filter_output(source, target)
        control.verify_playback_route(stages[0] if stages else physical, self.registry)

    def _verify_path(self, stages: list[str], physical: str) -> None:
        """Require two fresh complete observations after the default has changed.

        Policy managers can reconnect a playback node after set-default returns.
        A successful preflight, or a single app/default observation, is insufficient.
        Failures reset the consecutive-observation count within a bounded window.
        """
        self._progress("Verifying playback")
        deadline = time.monotonic() + 2.0
        consecutive = 0
        while True:
            try:
                self._observe_path(stages, physical)
                consecutive += 1
                if consecutive == 2:
                    return
            except Exception:
                consecutive = 0
                if time.monotonic() >= deadline:
                    raise
            if time.monotonic() >= deadline:
                raise AudioGraphError("full playback path is unverified")
            time.sleep(0.05)

    def _verify_route(self, target: str) -> None:
        self._verify_path([], target)

    def active_stages(self) -> tuple[str, ...]:
        return tuple(label for enabled, label in (
            (self.spatial_name, "Spatial"), (self.eq_enabled, "EQ"),
            (self.limiter_name, "LSP Limiter")) if enabled)

    def entrance_name(self) -> str | None:
        return self.spatial_name or (self._eq_name() if self.eq_enabled else None) or self.limiter_name

    def is_path_verified(self) -> bool:
        try:
            if self.eq_enabled and not self._eq_name():
                return False
            self._observe_path(self._current_stages(), self._physical())
            return True
        except Exception:
            return False

    def is_eq_active(self) -> bool:
        return self.eq_enabled and self.is_path_verified()

    def _route(self, target: str, physical: str, downstream: str | None = None) -> None:
        deadline = time.monotonic() + 2.0
        while True:
            try:
                control.set_system_routing(True, filter_node_name=target,
                                           fallback_sink_name=physical,
                                           output_sink_name=downstream,
                                           registry=self.registry)
                return
            except Exception as exc:
                detail = str(exc).lower()
                if (time.monotonic() >= deadline or
                    not any(marker in detail for marker in (
                        "no such sink", "is not loaded", "output stage", "unavailable"
                    ))):
                    raise
                time.sleep(0.05)

    def _connect_path(self, stages: list[str], physical: str) -> None:
        self._progress("Connecting playback")
        logger.info("candidate playback route: %s -> %s", " -> ".join(stages) or "applications", physical)
        # Downstream first: no application sees a candidate with an incomplete tail.
        for source, target in reversed(list(zip(stages, stages[1:] + [physical]))):
            self._link(source, target)
        if stages:
            self._route(stages[0], physical, downstream=stages[1] if len(stages) > 1 else None)
        else:
            control.set_system_routing(False, fallback_sink_name=physical, registry=self.registry)
        self._verify_path(stages, physical)

    def _restore_path(self, stages: list[str], physical: str) -> bool:
        """Return whether the previous chain (rather than direct fallback) survived."""
        try:
            self._connect_path(stages, physical)
            return True
        except Exception as previous_error:
            try:
                self._connect_path([], physical)
            except Exception as direct_error:
                raise AudioGraphError(
                    f"previous path unverified: {previous_error}; direct path unverified: {direct_error}"
                ) from direct_error
            # Owner references remain intact for later cleanup, but state must not
            # advertise a chain which was not restored.
            raise AudioGraphError(f"previous path unverified: {previous_error}; direct output restored") from previous_error

    def _cleanup_owner(self, manager) -> None:
        if not manager:
            return
        try:
            if manager.is_loaded:
                manager.unload()
        except Exception:
            self.retain_until_shutdown(manager)
            logger.exception("obsolete filter module cleanup failed after verified handoff")

    def _gain_state(self):
        return (self.output_gain_manager, self.output_gain_name, self.output_gain_db,
                tuple(self._retired_output_gain_managers))

    def _restore_gain_state(self, previous, *, verified: bool) -> None:
        owner, name, gain, retired = previous
        new_owners = [self.output_gain_manager, *self._retired_output_gain_managers]
        self.output_gain_manager, self.output_gain_name, self.output_gain_db = owner, name, gain
        self._retired_output_gain_managers = list(retired)
        for candidate in new_owners:
            if candidate and candidate is not owner and candidate not in retired:
                if verified:
                    self._cleanup_owner(candidate)
                else:
                    self.retain_until_shutdown(candidate)

    def switch_spatial(self, args: str, peak_db: float = 0.0) -> str:
        if self.atomic_transitions:
            self.configure_current(spatial_args=args, spatial_peak_db=peak_db)
            return self.spatial_name
        physical = self._physical()
        previous_stages = self._current_stages()
        previous_gain = self._gain_state()
        eq_name = self._eq_name() if self.eq_enabled else None
        previous_peak = self.spatial_peak_db
        previous = self.spatial_manager
        name = f"eqspace.spatial.{uuid.uuid4().hex[:12]}"
        manager = self.manager_factory()
        try:
            transition_peak = max(previous_peak, peak_db)
            if self.on_spatial_changed:
                self.on_spatial_changed(transition_peak)
            output = self._spatial_output(transition_peak, physical)
            manager.load_args(self._renamed_args(args, name))
            manager.node_name = name
            self._wait_sink(name)
            stages = self._stages(spatial=name, eq=eq_name,
                                  gain=output if output not in (physical, self.limiter_name, eq_name) else None,
                                  limiter=self.limiter_name)
            self._connect_path(stages, physical)
            if self.on_spatial_changed:
                self.on_spatial_changed(peak_db)
            if not self.eq_enabled and peak_db != transition_peak:
                output = self._spatial_output(peak_db, physical)
                stages = self._stages(spatial=name, gain=output if output not in (physical, self.limiter_name) else None,
                                      limiter=self.limiter_name)
                self._connect_path(stages, physical)
            else:
                self._verify_path(stages, physical)
        except Exception as exc:
            restored = False
            try:
                if self.on_spatial_changed:
                    self.on_spatial_changed(max(previous_peak, peak_db))
                self._restore_path(previous_stages, physical)
                if self.on_spatial_changed:
                    self.on_spatial_changed(previous_peak)
                self._verify_path(previous_stages, physical)
                restored = True
            except Exception as restore_exc:
                exc = AudioGraphError(f"{exc}; previous path unverified: {restore_exc}")
            self._restore_gain_state(previous_gain, verified=restored)
            if manager.is_loaded:
                if restored:
                    self._cleanup_owner(manager)
                else:
                    self.retain_until_shutdown(manager)
            raise AudioGraphError(f"spatial route unverified: {exc}") from exc
        self.spatial_manager, self.spatial_name, self.spatial_peak_db = manager, name, peak_db
        self.spatial_args = args
        self._cleanup_owner(previous)
        if previous is None or not previous.is_loaded:
            self._retire_unused_output_gains()
        return name

    def spatial_off(self) -> None:
        if self.atomic_transitions:
            self.configure_current(spatial_args=None, spatial_peak_db=0.0)
            return
        if self.spatial_manager is None:
            return
        physical = self._physical()
        previous = self._current_stages()
        stages = self._stages(eq=self._eq_name() if self.eq_enabled else None, limiter=self.limiter_name)
        try:
            self._connect_path(stages, physical)
            if self.on_spatial_changed:
                self.on_spatial_changed(0.0)
            self._verify_path(stages, physical)
        except Exception:
            if self.on_spatial_changed:
                self.on_spatial_changed(self.spatial_peak_db)
            self._restore_path(previous, physical)
            raise
        owner = self.spatial_manager
        self.spatial_manager = self.spatial_name = None
        self.spatial_peak_db = 0.0
        self.spatial_args = None
        self._cleanup_owner(owner)
        self._retire_unused_output_gains()

    def eq_applied(self) -> None:
        physical = self._physical()
        eq_name = self._eq_name()
        if not eq_name:
            raise AudioGraphError("EQ module is unavailable")
        self._wait_sink(eq_name)
        previous = self._current_stages()
        stages = self._stages(spatial=self.spatial_name, eq=eq_name, limiter=self.limiter_name)
        try:
            self._connect_path(stages, physical)
        except Exception:
            self._restore_path(previous, physical)
            raise
        self.eq_enabled = True
        self._retire_unused_output_gains()

    def eq_off(self) -> None:
        if self.atomic_transitions:
            self.configure_current(eq_enabled=False)
            return
        physical = self._physical()
        previous = self._current_stages()
        previous_gain = self._gain_state()
        try:
            gain = self._prepare_output_gain(self.spatial_peak_db, physical) if self.spatial_name else None
            stages = self._stages(spatial=self.spatial_name, gain=gain, limiter=self.limiter_name)
            self._connect_path(stages, physical)
        except Exception:
            restored = False
            try:
                self._restore_path(previous, physical)
                restored = True
            finally:
                self._restore_gain_state(previous_gain, verified=restored)
            raise
        self.eq_enabled = False
        self._retire_unused_output_gains()

    def set_limiter(self, enabled: bool, capability: LimiterCapability | None = None) -> None:
        if enabled == bool(self.limiter_manager):
            return
        if self.atomic_transitions:
            self.configure_current(limiter_enabled=enabled, capability=capability)
            return
        physical = self._physical()
        previous = self._current_stages()
        manager = None
        name = None
        try:
            if enabled:
                if capability is None:
                    raise AudioGraphError("LSP Limiter Stereo LV2 with true-peak ports is unavailable")
                name = f"eqspace.limiter.{uuid.uuid4().hex[:12]}"
                manager = self.manager_factory()
                manager.load_args(render_limiter_args(capability, name))
                self._wait_sink(name)
                manager.node_name = name
                if hasattr(manager, "verify_controls_from_mapping"):
                    manager.verify_controls_from_mapping({
                        "limiter:ovs": capability.true_peak_value,
                        "limiter:th": 10.0 ** (-1.0 / 20.0),
                        "limiter:boost": 0, "limiter:alr": 0,
                        "limiter:lk": 5, "limiter:g_in": 1, "limiter:g_out": 1,
                    }, 2.0)
            gain = self.output_gain_name if self.spatial_name and not self.eq_enabled else None
            if self.spatial_name and not self.eq_enabled and self.spatial_peak_db > 0 and not gain:
                gain = self._prepare_output_gain(self.spatial_peak_db, physical)
            stages = self._stages(spatial=self.spatial_name,
                                  eq=self._eq_name() if self.eq_enabled else None,
                                  gain=gain, limiter=name)
            self._connect_path(stages, physical)
        except Exception as exc:
            restored = False
            try:
                self._restore_path(previous, physical)
                restored = True
            except Exception as restore_exc:
                exc = AudioGraphError(f"{exc}; previous path unverified: {restore_exc}")
            if manager and manager.is_loaded:
                if restored:
                    self._cleanup_owner(manager)
                else:
                    self.retain_until_shutdown(manager)
            raise AudioGraphError(f"limiter route unverified: {exc}") from exc
        previous_owner = self.limiter_manager
        self.limiter_manager, self.limiter_name = manager, name
        self.limiter_capability = capability if enabled else None
        self._cleanup_owner(previous_owner)
        self._retire_unused_output_gains()

    def eq_on(self) -> None:
        if not self._eq_name():
            raise AudioGraphError("Apply an EQ preset first")
        if self.on_eq_enabling:
            self.on_eq_enabling()
        if self.atomic_transitions:
            self.configure_current(eq_enabled=True)
        else:
            self.eq_applied()

    def set_output(self, name: str) -> None:
        if name.startswith("eqspace.") or not any(s.name == name for s in self.registry.snapshot().sinks):
            raise AudioGraphError("selected physical output is unavailable")
        previous = self.physical_output()
        try:
            self._connect_output(name)
        except Exception as exc:
            try:
                if not previous:
                    raise AudioGraphError("previous physical output is unavailable")
                self._connect_output(previous)
            except Exception as restore_exc:
                raise AudioGraphError(f"output change failed: {exc}; previous path unverified: {restore_exc}") from exc
            raise AudioGraphError(f"output change failed; previous path restored: {exc}") from exc

    def _connect_output(self, name: str) -> None:
        self._connect_path(self._current_stages(), name)

    def configure_current(self, *, eq_enabled: bool | None = None,
                          spatial_args=_PRESERVE, spatial_peak_db: float | None = None,
                          limiter_enabled: bool | None = None,
                          capability: LimiterCapability | None = None):
        """Clone the applied EQ snapshot for an isolated structural transition.

        Production GUI code adopts ``_active_eq_manager()`` after completion.
        The gain callback must also resolve that manager, since the GUI adoption
        happens after this worker returns. Unapplied editor state is never used.
        """
        from eqspace.core.filterchain.manager import FilterChainManager
        enabled = self.eq_enabled if eq_enabled is None else eq_enabled
        previous_peak = self.spatial_peak_db
        spatial_changed = spatial_args is not _PRESERVE or spatial_peak_db is not None
        peak = previous_peak if spatial_peak_db is None else spatial_peak_db
        if spatial_args is None:
            peak = 0.0
        current = self._active_eq_manager()
        candidate = current
        committed = False
        try:
            if spatial_changed and self.on_spatial_changed:
                self.on_spatial_changed(max(previous_peak, peak))
            if enabled:
                self._progress("Loading EQ")
                specs = getattr(current, '_active_filters', None)
                if not getattr(current, 'is_loaded', False) or not specs:
                    raise AudioGraphError("Apply an EQ preset first")
                candidate = FilterChainManager(
                    node_name=f"eqspace.filter-chain.{uuid.uuid4().hex[:12]}",
                    description=current.description, runner=current._runner, popen=current._popen)
                candidate.load(specs, channels=current._active_channels or ('FL', 'FR'))
                candidate.verify_controls(specs)
            result = self.configure_playback(
                eq_enabled=enabled, spatial_args=spatial_args, spatial_peak_db=peak,
                limiter_enabled=limiter_enabled, limiter_capability=capability,
                eq_candidate=candidate,
                finalize_candidate=(lambda: self.on_spatial_changed(peak))
                    if spatial_changed and self.on_spatial_changed else None)
            committed = True
            return result
        except Exception:
            if not committed:
                # configure_playback retains uncertain candidates. A failure before
                # it took ownership (load/readback) has never linked the new EQ.
                if candidate is not current and candidate not in self._retained_managers:
                    self._cleanup_owner(candidate)
                if spatial_changed and self.on_spatial_changed and self.is_path_verified():
                    self.on_spatial_changed(previous_peak)
            raise

    def configure_playback(self, *, eq_enabled: bool,
                           spatial_args=_PRESERVE, spatial_peak_db: float | None = None,
                           limiter_enabled: bool | None = None,
                           limiter_capability: LimiterCapability | None = None,
                           eq_candidate=None, finalize_candidate: Callable[[], None] | None = None):
        """Commit a fully staged target graph, retaining every old owner until proven.

        The caller loads and verifies ``eq_candidate`` before invoking this method;
        its gain must be safe for the candidate Spatial stage. Returning the manager
        lets the GUI adopt it on its own thread. Omitting Spatial args preserves
        its settings; None disables it. All module/link work is Qt-independent.
        """
        physical = self._physical()
        previous_stages = self._current_stages()
        previous_eq = self._active_eq_manager()
        eq_candidate = eq_candidate or previous_eq
        if eq_enabled and not getattr(eq_candidate, 'is_loaded', False):
            raise AudioGraphError("EQ candidate is unavailable")
        if spatial_args is _PRESERVE:
            spatial_args = self.spatial_args if self.spatial_name else None
            if self.spatial_name and spatial_args is None:
                raise AudioGraphError("active Spatial settings are unavailable")
        peak = self.spatial_peak_db if spatial_peak_db is None else spatial_peak_db
        if spatial_args is None:
            peak = 0.0
        limiter_enabled = bool(self.limiter_name) if limiter_enabled is None else limiter_enabled
        capability = limiter_capability or self.limiter_capability
        candidate = AudioGraphController(self.registry, lambda: physical,
                                         lambda: eq_candidate, self.manager_factory)
        candidate.eq_enabled = eq_enabled
        candidate.on_progress = self.on_progress
        self._progress("Preparing playback")
        owners = []
        if eq_candidate is not previous_eq:
            owners.append(eq_candidate)
        try:
            if limiter_enabled:
                self._progress("Loading limiter")
                if capability is None:
                    raise AudioGraphError("LSP Limiter Stereo LV2 with true-peak ports is unavailable")
                name = f"eqspace.limiter.{uuid.uuid4().hex[:12]}"
                owner = self.manager_factory()
                owners.append(owner)
                owner.load_args(render_limiter_args(capability, name))
                self._wait_sink(name)
                owner.node_name = name
                if hasattr(owner, 'verify_controls_from_mapping'):
                    owner.verify_controls_from_mapping({
                        'limiter:ovs': capability.true_peak_value,
                        'limiter:th': 10.0 ** (-1.0 / 20.0),
                        'limiter:boost': 0, 'limiter:alr': 0,
                        'limiter:lk': 5, 'limiter:g_in': 1, 'limiter:g_out': 1,
                    }, 2.0)
                candidate.limiter_manager, candidate.limiter_name = owner, name
            if spatial_args is not None:
                self._progress("Loading Spatial")
                name = f"eqspace.spatial.{uuid.uuid4().hex[:12]}"
                owner = self.manager_factory()
                owners.append(owner)
                owner.load_args(self._renamed_args(spatial_args, name))
                owner.node_name = name
                self._wait_sink(name)
                candidate.spatial_manager, candidate.spatial_name = owner, name
                candidate.spatial_peak_db = max(self.spatial_peak_db, peak)
                if not eq_enabled:
                    if candidate.limiter_name:
                        candidate._link(candidate.limiter_name, physical)
                    candidate._prepare_output_gain(candidate.spatial_peak_db, physical)
            self._connect_path(candidate._current_stages(), physical)
            if spatial_args is not None and not eq_enabled and peak != candidate.spatial_peak_db:
                candidate._prepare_output_gain(peak, physical)
                candidate.spatial_peak_db = peak
                self._connect_path(candidate._current_stages(), physical)
            candidate.spatial_peak_db = peak
            if finalize_candidate:
                # Let the gain callback address the candidate while the old path
                # and owners remain available for rollback. Never leak this
                # temporary override into the failure path.
                previous_override = self._committed_eq_manager
                self._committed_eq_manager = eq_candidate
                try:
                    finalize_candidate()
                    self._verify_path(candidate._current_stages(), physical)
                finally:
                    self._committed_eq_manager = previous_override
        except Exception as exc:
            restored = False
            try:
                self._restore_path(previous_stages, physical)
                restored = True
            except Exception as restore_exc:
                exc = AudioGraphError(f"{exc}; {restore_exc}")
            owners += [candidate.output_gain_manager, *candidate._retired_output_gain_managers]
            for owner in owners:
                if owner and getattr(owner, 'is_loaded', False):
                    if restored:
                        self._cleanup_owner(owner)
                    else:
                        self.retain_until_shutdown(owner)
            raise AudioGraphError(f"playback configuration unverified: {exc}") from exc
        old_owners = [self.spatial_manager, self.limiter_manager, self.output_gain_manager,
                      *self._retired_output_gain_managers]
        self.spatial_manager, self.spatial_name = candidate.spatial_manager, candidate.spatial_name
        self.spatial_peak_db, self.spatial_args = peak, spatial_args
        self.limiter_manager, self.limiter_name = candidate.limiter_manager, candidate.limiter_name
        self.limiter_capability = capability if limiter_enabled else None
        self.output_gain_manager, self.output_gain_name = candidate.output_gain_manager, candidate.output_gain_name
        self.output_gain_db = candidate.output_gain_db
        self._retired_output_gain_managers = candidate._retired_output_gain_managers
        self.eq_enabled = eq_enabled
        if eq_candidate is not previous_eq:
            self._committed_eq_manager = eq_candidate
            old_owners.append(previous_eq)
        for owner in old_owners:
            self._cleanup_owner(owner)
        self._retire_unused_output_gains()
        return eq_candidate

    def shutdown(self) -> None:
        physical = self._physical()
        current = self.entrance_name() or self._eq_name()
        eq_manager = self._active_eq_manager()
        has_owned_modules = self.has_owned_modules
        if has_owned_modules:
            control.set_system_routing(False, filter_node_name=current or "eqspace.filter-chain",
                                       fallback_sink_name=physical, registry=self.registry)
            # A default sink change alone does not prove that every active stream
            # left the soon-to-be-destroyed filter graph.
            self._verify_route(physical)
        if self.spatial_manager:
            self.spatial_manager.unload()
            self.spatial_manager = None
            self.spatial_name = None
        if self.limiter_manager:
            self.limiter_manager.unload()
            self.limiter_manager = None
            self.limiter_name = None
        if self.output_gain_manager:
            self.output_gain_manager.unload()
            self.output_gain_manager = None
            self.output_gain_name = None
            self.output_gain_db = 0.0
        if eq_manager.is_loaded:
            eq_manager.unload()
        for manager in tuple(self._retired_output_gain_managers):
            if manager.is_loaded:
                manager.unload()
            self._retired_output_gain_managers.remove(manager)
        for manager in tuple(self._retained_managers):
            if manager.is_loaded:
                manager.unload()
            self._retained_managers.remove(manager)
        self.eq_enabled = False
