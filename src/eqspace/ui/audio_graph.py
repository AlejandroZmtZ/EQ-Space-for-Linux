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


class AudioGraphController:
    """Stage spatial modules and publish only routes verified from PipeWire links."""

    def __init__(self, registry: PipeWireRegistry, physical_output: Callable[[], str | None],
                 eq_manager: Callable[[], object], manager_factory=ModuleArgsManager) -> None:
        self.registry = registry
        self.physical_output = physical_output
        self.eq_manager = eq_manager
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

    @property
    def has_owned_modules(self) -> bool:
        eq_manager = self.eq_manager()
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

    def _eq_name(self) -> str | None:
        manager = self.eq_manager()
        return getattr(manager, "node_name", "eqspace.filter-chain") if manager.is_loaded else None

    def _prepare_output_gain(self, peak_db: float, physical: str) -> str | None:
        """Own a native stereo headroom stage when Spatial bypasses EQ."""
        gain_db = -(peak_db + 1.0) if peak_db > 0.0 else 0.0
        if gain_db == 0.0:
            return None
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
            f'playback.props = {{ node.name = "{name}.playback" node.passive = true '
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
        needed = bool(self.spatial_name and not self.eq_enabled and self.spatial_peak_db > 0)
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

    def _verify_route(self, target: str) -> None:
        deadline = time.monotonic() + 2.0
        while True:
            try:
                control.verify_playback_route(target, self.registry)
                return
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def active_stages(self) -> tuple[str, ...]:
        return tuple(label for enabled, label in (
            (self.spatial_name, "Spatial"), (self.eq_enabled, "EQ"),
            (self.limiter_name, "LSP Limiter")) if enabled)

    def entrance_name(self) -> str | None:
        return self.spatial_name or (self._eq_name() if self.eq_enabled else None) or self.limiter_name

    def is_path_verified(self) -> bool:
        try:
            physical = self._physical()
            eq_name = self._eq_name() if self.eq_enabled else None
            if self.eq_enabled and not eq_name:
                return False
            output_gain = self.output_gain_name if self.spatial_name and not self.eq_enabled and self.spatial_peak_db > 0 else None
            stages = [name for name in (self.spatial_name, eq_name or output_gain, self.limiter_name) if name]
            for source, target in zip(stages, stages[1:] + [physical]):
                control.verify_filter_output(source, target)
            control.verify_playback_route(stages[0] if stages else physical, self.registry)
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
                break
            except Exception as exc:
                detail = str(exc).lower()
                if (time.monotonic() >= deadline or
                    not any(marker in detail for marker in (
                        "no such sink", "is not loaded", "output stage", "unavailable"
                    ))):
                    raise
                time.sleep(0.05)
        self._verify_route(target)

    def switch_spatial(self, args: str, peak_db: float = 0.0) -> str:
        """Keep the old module alive until the staged path is fully verified."""
        physical = self._physical()
        eq_name = self._eq_name() if self.eq_enabled else None
        previous_peak = self.spatial_peak_db
        downstream_name = eq_name or self.limiter_name
        if eq_name:
            self._link(eq_name, self.limiter_name or physical)
        elif self.limiter_name:
            self._link(self.limiter_name, physical)
        name = f"eqspace.spatial.{uuid.uuid4().hex[:12]}"
        manager = self.manager_factory()
        previous = self.spatial_manager
        previous_name = self.spatial_name
        try:
            transition_peak = max(previous_peak, peak_db)
            if self.on_spatial_changed:
                self.on_spatial_changed(transition_peak)
            downstream_name = self._spatial_output(transition_peak, physical)
            manager.load_args(self._renamed_args(args, name))
            self._wait_sink(name)
            self._link(name, downstream_name or physical)
            self._route(name, physical, downstream=downstream_name)
            if self.on_spatial_changed:
                self.on_spatial_changed(peak_db)
            if not self.eq_enabled and peak_db != transition_peak:
                downstream_name = self._spatial_output(peak_db, physical)
                self._link(name, downstream_name)
        except Exception as exc:
            restored = False
            try:
                if self.on_spatial_changed:
                    self.on_spatial_changed(max(previous_peak, peak_db))
                if previous_name:
                    downstream_name = self._spatial_output(max(previous_peak, peak_db), physical)
                    self._link(previous_name, downstream_name or physical)
                    self._route(previous_name, physical, downstream=downstream_name)
                elif eq_name:
                    self._route(eq_name, physical, downstream=self.limiter_name)
                elif self.limiter_name:
                    self._route(self.limiter_name, physical)
                else:
                    control.set_system_routing(False, filter_node_name=name,
                                               fallback_sink_name=physical, registry=self.registry)
                    self._verify_route(physical)
                if self.on_spatial_changed:
                    self.on_spatial_changed(previous_peak)
                if previous_name and not self.eq_enabled:
                    self._link(previous_name, self._spatial_output(previous_peak, physical))
                restored = True
            except Exception as restore_exc:
                exc = AudioGraphError(f"{exc}; previous path unverified: {restore_exc}")
            if manager.is_loaded:
                if restored:
                    manager.unload()
                else:
                    self.retain_until_shutdown(manager)
            raise AudioGraphError(f"spatial route unverified: {exc}") from exc
        self.spatial_manager = manager
        self.spatial_name = name
        self.spatial_peak_db = peak_db
        if previous:
            try:
                previous.unload()
            except Exception:
                self.retain_until_shutdown(previous)
                logger.exception("previous spatial module cleanup failed after verified switch")
        if previous is None or not previous.is_loaded:
            self._retire_unused_output_gains()
        return name

    def spatial_off(self) -> None:
        if self.spatial_manager is None:
            return
        physical = self._physical()
        eq_name = self._eq_name() if self.eq_enabled else None
        output_name = eq_name or self.limiter_name
        if output_name:
            if eq_name:
                self._link(eq_name, self.limiter_name or physical)
            else:
                self._link(output_name, physical)
            self._route(output_name, physical, downstream=self.limiter_name if eq_name else None)
        else:
            control.set_system_routing(False, filter_node_name=self.spatial_name or "eqspace.spatial",
                                       fallback_sink_name=physical, registry=self.registry)
            self._verify_route(physical)
        if self.on_spatial_changed:
            try:
                self.on_spatial_changed(0.0)
            except Exception:
                if self.on_spatial_changed:
                    self.on_spatial_changed(self.spatial_peak_db)
                restored_output = self._spatial_output(self.spatial_peak_db, physical)
                self._link(self.spatial_name, restored_output)
                self._route(self.spatial_name, physical, downstream=restored_output)
                raise
        self.spatial_manager.unload()
        self.spatial_manager = None
        self.spatial_name = None
        self.spatial_peak_db = 0.0
        self._retire_unused_output_gains()

    def eq_applied(self) -> None:
        """Keep Spatial at the graph entrance after EQ updates or preset changes."""
        physical = self._physical()
        eq_name = self._eq_name()
        if not eq_name:
            raise AudioGraphError("EQ module is unavailable")
        self._wait_sink(eq_name)
        if self.limiter_name:
            self._link(self.limiter_name, physical)
        self._link(eq_name, self.limiter_name or physical)
        if self.spatial_name:
            self._link(self.spatial_name, eq_name)
            self._verify_route(self.spatial_name)
        else:
            self._route(eq_name, physical, downstream=self.limiter_name)
        self.eq_enabled = True
        self._retire_unused_output_gains()

    def eq_off(self) -> None:
        physical = self._physical()
        if self.limiter_name:
            self._link(self.limiter_name, physical)
        if self.spatial_name:
            output = self._prepare_output_gain(self.spatial_peak_db, physical) or self.limiter_name or physical
            self._link(self.spatial_name, output)
            self._verify_route(self.spatial_name)
        elif self.limiter_name:
            self._link(self.limiter_name, physical)
            self._route(self.limiter_name, physical)
        else:
            control.set_system_routing(False, filter_node_name=self._eq_name() or "eqspace.filter-chain",
                                       fallback_sink_name=physical, registry=self.registry)
            self._verify_route(physical)
        self.eq_enabled = False

    def set_limiter(self, enabled: bool, capability: LimiterCapability | None = None) -> None:
        if enabled == bool(self.limiter_manager):
            return
        eq_name = self._eq_name()
        physical = self._physical()
        if enabled:
            if not eq_name or not self.eq_enabled:
                raise AudioGraphError("Apply EQ before enabling the limiter")
            if capability is None:
                raise AudioGraphError("LSP Limiter Stereo LV2 with true-peak ports is unavailable")
            name = f"eqspace.limiter.{uuid.uuid4().hex[:12]}"
            manager = self.manager_factory()
            try:
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
                self._link(name, physical)
                self._link(eq_name, name)
                self._verify_route(self.spatial_name or eq_name)
            except Exception as exc:
                restored = False
                try:
                    self._link(eq_name, physical)
                    if self.spatial_name:
                        self._link(self.spatial_name, eq_name)
                    self._verify_route(self.spatial_name or eq_name)
                    restored = True
                except Exception as restore_exc:
                    exc = AudioGraphError(f"{exc}; previous path unverified: {restore_exc}")
                if manager.is_loaded:
                    if restored:
                        manager.unload()
                    else:
                        self.retain_until_shutdown(manager)
                raise AudioGraphError(f"limiter route unverified: {exc}") from exc
            self.limiter_manager = manager
            self.limiter_name = name
        else:
            if self.eq_enabled and eq_name:
                self._link(eq_name, physical)
                self._verify_route(self.spatial_name or eq_name)
            elif self.spatial_name:
                output = self._prepare_output_gain(self.spatial_peak_db, physical)
                if output:
                    self._link(output, physical)
                self._link(self.spatial_name, output or physical)
                self._verify_route(self.spatial_name)
            else:
                control.set_system_routing(False, fallback_sink_name=physical,
                                            registry=self.registry)
                self._verify_route(physical)
            assert self.limiter_manager is not None
            self.limiter_manager.unload()
            self.limiter_manager = None
            self.limiter_name = None

    def eq_on(self) -> None:
        if not self._eq_name():
            raise AudioGraphError("Apply an EQ preset first")
        if self.on_eq_enabling:
            self.on_eq_enabling()
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
        eq_name = self._eq_name() if self.eq_enabled else None
        if self.limiter_name:
            self._link(self.limiter_name, name)
        if eq_name:
            self._link(eq_name, self.limiter_name or name)
        if self.spatial_name:
            self._link(self.spatial_name, eq_name or self._prepare_output_gain(self.spatial_peak_db, name) or self.limiter_name or name)
            self._verify_route(self.spatial_name)
        elif eq_name:
            self._route(eq_name, name, downstream=self.limiter_name)
        elif self.limiter_name:
            self._route(self.limiter_name, name)
        else:
            control.set_system_routing(False, fallback_sink_name=name, registry=self.registry)
            self._verify_route(name)

    def shutdown(self) -> None:
        physical = self._physical()
        current = self.spatial_name or self._eq_name()
        eq_manager = self.eq_manager()
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
