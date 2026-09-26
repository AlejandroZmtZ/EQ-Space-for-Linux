"""Mixer tab: one system EQ route, physical output selection, and app volume/mute."""

from __future__ import annotations

import logging
import math
import time
from typing import Callable, Optional, Protocol

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from eqspace.core.pipewire import control as _control
from eqspace.core.pipewire.registry import PipeWireRegistry, PipeWireUnavailable, PwNode

logger = logging.getLogger(__name__)


def format_vol_db(volume_percent: int) -> str:
    if volume_percent <= 0:
        return "0% (-∞ dB)"
    if volume_percent >= 100:
        return f"{volume_percent}% (0.0 dB)"
    db = 20.0 * math.log10(volume_percent / 100.0)
    return f"{volume_percent}% ({db:+.1f} dB)"


class ControlLike(Protocol):
    def set_volume(self, node_id: int, volume: float) -> None: ...
    def get_volume(self, node_id: int) -> float: ...
    def set_mute(self, node_id: int, mute: bool) -> None: ...
    def set_default_sink(self, name: str) -> None: ...


def is_eqspace_sink(node: PwNode) -> bool:
    return node.name.startswith("eqspace.")


def sink_display_name(node: PwNode) -> str:
    """Present a physical sink as a listening device."""
    if is_eqspace_sink(node):
        return "EQ-Space virtual sink"
    desc = node.description or node.app_name or node.name
    name_lower = node.name.lower()
    desc_lower = desc.lower()
    if (
        "bluez" in name_lower
        or "bluetooth" in desc_lower
        or "wh-" in desc_lower
        or "airpods" in desc_lower
        or "buds" in desc_lower
    ):
        suffix = "" if "(bluetooth)" in desc_lower else " (Bluetooth)"
        return f"🎧 {desc}{suffix}"
    if "analog" in name_lower or "built-in" in desc_lower or "speaker" in desc_lower:
        return f"🔊 {desc}"
    if "hdmi" in name_lower or "hdmi" in desc_lower:
        return f"🖥️ {desc}"
    return f"🔈 {desc}"


class StreamRow(QWidget):
    """Playback stream controls. Routing follows the system EQ state."""

    action_failed = Signal(str)

    def __init__(self, node: PwNode, control: ControlLike, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.node = node
        self._control = control
        self._volume_timer = QTimer(self)
        self._volume_timer.setSingleShot(True)
        self._volume_timer.timeout.connect(self._commit_volume)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(8)

        self.name_label = QLabel(node.app_name or node.description or node.name or f"node {node.id}")
        self.name_label.setMinimumWidth(160)
        layout.addWidget(self.name_label)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setToolTip("Application volume")
        try:
            volume = float(control.get_volume(node.id))
        except Exception:
            volume = None
        pct = max(0, min(100, round(volume * 100))) if volume is not None else 0
        self.slider.setValue(pct)
        self.slider.setEnabled(volume is not None)
        self.slider.valueChanged.connect(self._on_volume)
        layout.addWidget(self.slider, stretch=1)

        self.vol_label = QLabel(format_vol_db(pct) if volume is not None else "Unavailable")
        self.vol_label.setFixedWidth(82)
        self.vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.vol_label)

        self.mute_button = QPushButton("Mute")
        self.mute_button.setCheckable(True)
        self.mute_button.setChecked(bool(node.mute))
        self.mute_button.setText("Unmute" if node.mute else "Mute")
        self.mute_button.toggled.connect(self._on_mute)
        layout.addWidget(self.mute_button)

    def update_node(self, node: PwNode) -> None:
        self.node = node
        self.name_label.setText(node.app_name or node.description or node.name or f"node {node.id}")

    def _on_volume(self, value: int) -> None:
        self.vol_label.setText(format_vol_db(value))
        self._volume_timer.start(180)

    def _commit_volume(self) -> None:
        try:
            if self._control is _control:
                self._control.set_volume(self.node.id, self.slider.value() / 100.0, timeout=1.0)
            else:
                self._control.set_volume(self.node.id, self.slider.value() / 100.0)
        except Exception as exc:
            logger.warning("application volume change failed: %s", exc)
            self.action_failed.emit(f"Could not change {self.name_label.text()} volume: {exc}")
            try:
                actual = round(self._control.get_volume(self.node.id) * 100)
            except Exception:
                self.slider.setEnabled(False)
                self.vol_label.setText("Unavailable")
            else:
                self.slider.blockSignals(True)
                self.slider.setValue(max(0, min(100, actual)))
                self.slider.blockSignals(False)
                self.vol_label.setText(format_vol_db(self.slider.value()))

    def _on_mute(self, checked: bool) -> None:
        try:
            if self._control is _control:
                self._control.set_mute(self.node.id, checked, timeout=1.0)
            else:
                self._control.set_mute(self.node.id, checked)
        except Exception as exc:
            logger.warning("application mute change failed: %s", exc)
            self.mute_button.blockSignals(True)
            self.mute_button.setChecked(not checked)
            self.mute_button.blockSignals(False)
            self.action_failed.emit(f"Could not change {self.name_label.text()} mute: {exc}")
        self.mute_button.setText("Unmute" if self.mute_button.isChecked() else "Mute")


class MixerWidget(QWidget):
    routing_changed = Signal(bool)

    def __init__(
        self,
        registry: Optional[PipeWireRegistry] = None,
        control: Optional[ControlLike] = None,
        poll_interval_ms: int = 1000,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.mutation_allowed: Callable[[], bool] = lambda: True
        self.registry = registry or PipeWireRegistry(timeout=2.0)
        self.control: ControlLike = control or _control  # type: ignore[assignment]
        self._physical_sinks: tuple[PwNode, ...] = ()
        self._selected_output_name: Optional[str] = None
        self._synced_output_name: Optional[str] = None
        self._volume_retry_at = 0.0
        self._all_sinks: tuple[PwNode, ...] = ()
        self._last_routed_state: Optional[bool] = None
        self.stream_rows: list[StreamRow] = []

        layout = QVBoxLayout(self)
        self.subtitle_label = QLabel("Choose where to listen, then apply a preset to turn on EQ.")
        self.subtitle_label.setObjectName("tabSubtitle")
        layout.addWidget(self.subtitle_label)

        self.routing_card = QFrame()
        self.routing_card.setObjectName("routingCard")
        route_layout = QHBoxLayout(self.routing_card)
        self.routing_status_label = QLabel("EQ off")
        self.routing_status_label.setWordWrap(True)
        self.routing_button = QPushButton("Turn on EQ")
        self.routing_button.clicked.connect(self._on_toggle_routing)
        route_layout.addWidget(self.routing_status_label, stretch=1)
        route_layout.addWidget(self.routing_button)
        layout.addWidget(self.routing_card)

        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("Listening device:"))
        self.device_combo = QComboBox()
        self.device_combo.setToolTip("Physical device where sound plays")
        self.device_combo.currentIndexChanged.connect(self._on_default_sink)
        device_row.addWidget(self.device_combo, stretch=1)
        layout.addLayout(device_row)
        self.device_hint = QLabel("Use direct output to change the listening device.")
        self.device_hint.setVisible(False)
        layout.addWidget(self.device_hint)

        master_row = QHBoxLayout()
        master_row.addWidget(QLabel("Device volume:"))
        self.master_slider = QSlider(Qt.Orientation.Horizontal)
        self.master_slider.setRange(0, 100)
        self.master_slider.setEnabled(False)
        self.master_slider.valueChanged.connect(self._on_master_volume)
        self._master_volume_timer = QTimer(self)
        self._master_volume_timer.setSingleShot(True)
        self._master_volume_timer.timeout.connect(self._commit_master_volume)
        master_row.addWidget(self.master_slider, stretch=1)
        self.master_vol_label = QLabel("—")
        self.master_vol_label.setFixedWidth(82)
        self.master_vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        master_row.addWidget(self.master_vol_label)
        layout.addLayout(master_row)

        header = QLabel("Application volume")
        header.setObjectName("sectionHeader")
        layout.addWidget(header)
        self.stream_help_label = QLabel("Adjust volume or mute each application.")
        layout.addWidget(self.stream_help_label)
        self._streams_container = QWidget()
        self.streams_layout = QVBoxLayout(self._streams_container)
        self.streams_layout.setContentsMargins(0, 0, 0, 0)
        self.streams_layout.addStretch(1)
        layout.addWidget(self._streams_container, stretch=1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        if poll_interval_ms > 0:
            self._timer.start(poll_interval_ms)
        QTimer.singleShot(0, self.refresh)

    @property
    def selected_output_name(self) -> Optional[str]:
        return self._selected_output_name

    def is_routed(self) -> bool:
        graph = getattr(self, "graph_controller", None)
        if graph is not None:
            return graph.is_eq_active()
        checker = getattr(self.control, "is_system_routed", _control.is_system_routed)
        runner = getattr(self.registry, "_runner", None)
        try:
            return bool(checker(runner=runner, registry=self.registry, timeout=2.0))
        except TypeError:
            try:
                return bool(checker(registry=self.registry))
            except TypeError:
                return bool(checker())
        except Exception:
            return False

    def _default_physical_name(self) -> Optional[str]:
        getter = getattr(self.control, "get_default_sink_name", None)
        if getter is None:
            return None
        runner = getattr(self.registry, "_runner", None)
        try:
            current = getter(runner=runner, timeout=2.0)
        except TypeError:
            current = getter()
        except Exception:
            return None
        for sink in self._physical_sinks:
            if current in (sink.name, sink.description, str(sink.id)):
                return sink.name
        return None

    def _active_links(self) -> Optional[dict[str, list[str]]]:
        getter = getattr(self.control, "get_active_output_links", None)
        if getter is None:
            return None
        try:
            return getter(timeout=2.0) if getter is _control.get_active_output_links else getter()
        except Exception:
            return None

    def _linked_output_name(self, links: dict[str, list[str]]) -> Optional[str]:
        physical_names = {s.name for s in self._physical_sinks}
        graph = getattr(self, "graph_controller", None)
        output_stage = (graph.limiter_name or (graph._eq_name() if graph.eq_enabled else None) or graph.spatial_name) if graph is not None else "eqspace.filter-chain"
        channels = []
        for channel in ("FL", "FR"):
            port = f"{output_stage}.playback:output_{channel}"
            channels.append({
                destination.rsplit(":", 1)[0]
                for destination in links.get(port, [])
                if destination.rsplit(":", 1)[0] in physical_names
            })
        shared = channels[0] & channels[1]
        return sorted(shared)[0] if shared else None

    @staticmethod
    def _misrouted_stream_count(
        streams: tuple[PwNode, ...], links: dict[str, list[str]], target: str
    ) -> int:
        count = 0
        for stream in streams:
            ports = [destinations for port, destinations in links.items()
                     if port.startswith(f"{stream.name}:output_")]
            if ports and any(
                not destinations or not all(
                    destination.startswith(f"{target}:")
                    for destination in destinations
                )
                for destinations in ports
            ):
                count += 1
        return count

    def _update_routing_banner(
        self, routed: bool, linked_output: Optional[str], misrouted_count: int,
        links_available: bool,
    ) -> None:
        output = next((s for s in self._physical_sinks if s.name == self._selected_output_name), None)
        if routed:
            if not links_available:
                self.routing_status_label.setText("EQ on · Route unverified")
            elif not linked_output:
                self.routing_status_label.setText("EQ on · Output disconnected")
            elif misrouted_count:
                noun = "app" if misrouted_count == 1 else "apps"
                self.routing_status_label.setText(
                    f"EQ on · {misrouted_count} {noun} not using EQ"
                )
            else:
                self.routing_status_label.setText("EQ on")
            self.routing_button.setText("Turn off EQ" if getattr(self, "graph_controller", None) else "Use direct output")
            self.routing_button.setEnabled(True)
            self.device_combo.setEnabled(bool(self._physical_sinks))
            self.device_combo.setToolTip("Physical device where sound plays")
            self.device_hint.setVisible(False)
        else:
            if output and links_available and misrouted_count:
                noun = "app" if misrouted_count == 1 else "apps"
                self.routing_status_label.setText(
                    f"EQ off · {misrouted_count} {noun} on another device"
                )
            else:
                self.routing_status_label.setText("EQ off" if output else "No listening device available")
            self.routing_button.setText("Turn on EQ")
            graph = getattr(self, "graph_controller", None)
            chain_available = (bool(graph._eq_name()) if graph is not None else
                               any(s.name == "eqspace.filter-chain" for s in self._all_sinks))
            self.routing_button.setEnabled(chain_available and output is not None)
            self.routing_button.setToolTip("Apply a preset first" if not chain_available else "Route system audio through EQ-Space")
            self.device_combo.setEnabled(bool(self._physical_sinks))
            self.device_combo.setToolTip("Physical device where sound plays")
            self.device_hint.setVisible(False)

        graph = getattr(self, "graph_controller", None)
        if graph is not None:
            stages = graph.active_stages()
            if stages:
                combination = " + ".join(stages)
                self.routing_status_label.setText(
                    f"{combination} active" if graph.is_path_verified()
                    else f"{combination} · Route unverified"
                )
            else:
                self.routing_status_label.setText(
                    "EQ off · Direct output" if graph.is_path_verified()
                    else "Direct output · Route unverified"
                )
            self.routing_button.setText("Turn off EQ" if graph.eq_enabled else "Turn on EQ")

        self.refresh_mutation_controls()

    def refresh_mutation_controls(self) -> None:
        """Keep graph-changing controls disabled while Apply owns the graph."""
        if not self.mutation_allowed():
            self.routing_button.setEnabled(False)
            self.device_combo.setEnabled(False)

    def _on_toggle_routing(self) -> None:
        if not self.mutation_allowed():
            self.status_label.setText("Wait for Apply to finish before changing the route.")
            return
        graph = getattr(self, "graph_controller", None)
        if graph is not None:
            try:
                graph.eq_off() if graph.eq_enabled else graph.eq_on()
            except Exception as exc:
                self.status_label.setText(f"Could not change EQ route: {exc}")
                return
            self.status_label.setText("")
            self.refresh()
            return
        enable = not self.is_routed()
        if enable and not any(s.name == "eqspace.filter-chain" for s in self._all_sinks):
            self.status_label.setText("Apply an EQ preset first to turn on EQ.")
            return
        setter = getattr(self.control, "set_system_routing", _control.set_system_routing)
        runner = getattr(self.registry, "_runner", None)
        try:
            try:
                setter(enable, fallback_sink_name=self._selected_output_name, registry=self.registry, runner=runner)
            except TypeError:
                try:
                    setter(enable, fallback_sink_name=self._selected_output_name, registry=self.registry)
                except TypeError:
                    setter(enable)
        except Exception as exc:
            logger.warning("routing change failed: %s", exc)
            self.status_label.setText(f"Could not change EQ route: {exc}")
            return
        self.status_label.setText("")
        self.refresh()

    def refresh(self) -> None:
        self.refresh_mutation_controls()
        try:
            snapshot = self.registry.snapshot()
        except PipeWireUnavailable as exc:
            self.status_label.setText(f"PipeWire unavailable: {exc}")
            return
        except Exception as exc:
            logger.warning("registry snapshot failed: %s", exc)
            self.status_label.setText("PipeWire unavailable")
            return
        self._all_sinks = snapshot.sinks
        routed = self.is_routed()
        observed_links = self._active_links()
        links = observed_links or {}
        self._rebuild_sinks(snapshot.sinks, routed, links)
        self._rebuild_streams(snapshot.streams)
        graph = getattr(self, "graph_controller", None)
        target = ((graph.spatial_name or graph._eq_name()) if routed and graph is not None else
                  "eqspace.filter-chain" if routed else
                  graph.entrance_name() if graph is not None and graph.entrance_name() else self._selected_output_name)
        self._update_routing_banner(
            routed,
            self._linked_output_name(links) if routed else None,
            self._misrouted_stream_count(snapshot.streams, links, target) if target else 0,
            observed_links is not None,
        )
        if routed != self._last_routed_state:
            self._last_routed_state = routed
            self.routing_changed.emit(routed)

    def _rebuild_sinks(
        self, sinks: tuple[PwNode, ...], routed: bool,
        links: dict[str, list[str]],
    ) -> None:
        physical = tuple(s for s in sinks if not is_eqspace_sink(s))
        self._physical_sinks = physical
        names = {s.name for s in physical}
        if self._selected_output_name and self._selected_output_name in names:
            selected = self._selected_output_name
        elif routed:
            selected = self._linked_output_name(links) or self._default_physical_name()
        else:
            selected = self._default_physical_name()
        if selected not in names:
            selected = physical[0].name if physical else None
        self._selected_output_name = selected
        options = [(sink_display_name(s), s.name) for s in physical]
        existing = [(self.device_combo.itemText(i), self.device_combo.itemData(i)) for i in range(self.device_combo.count())]
        self.device_combo.blockSignals(True)
        if existing != options:
            self.device_combo.clear()
            for label, name in options:
                self.device_combo.addItem(label, name)
        index = self.device_combo.findData(selected)
        self.device_combo.setCurrentIndex(index)
        self.device_combo.blockSignals(False)
        if selected:
            if selected != self._synced_output_name and time.monotonic() >= self._volume_retry_at:
                self._sync_master_volume()
        else:
            self._synced_output_name = None
            self.master_slider.setEnabled(False)
            self.master_vol_label.setText("—")

    def _rebuild_streams(self, streams: tuple[PwNode, ...]) -> None:
        if [r.node.id for r in self.stream_rows] == [n.id for n in streams]:
            for row, node in zip(self.stream_rows, streams):
                row.update_node(node)
            return
        for row in self.stream_rows:
            self.streams_layout.removeWidget(row)
            row.deleteLater()
        self.stream_rows = []
        for node in streams:
            row = StreamRow(node, self.control)
            row.action_failed.connect(self.status_label.setText)
            self.stream_rows.append(row)
            self.streams_layout.insertWidget(self.streams_layout.count() - 1, row)

    def _sync_master_volume(self) -> None:
        sink = next((s for s in self._physical_sinks if s.name == self._selected_output_name), None)
        if sink is None:
            return
        try:
            level = float(self.control.get_volume(sink.id))
        except Exception as exc:
            self.master_slider.setEnabled(False)
            self.master_vol_label.setText("Unavailable")
            logger.debug("could not read device volume: %s", exc)
            self._volume_retry_at = time.monotonic() + 5.0
            return
        pct = max(0, min(100, round(level * 100)))
        self.master_slider.blockSignals(True)
        self.master_slider.setValue(pct)
        self.master_slider.blockSignals(False)
        self.master_slider.setEnabled(True)
        self.master_vol_label.setText(format_vol_db(pct))
        self._synced_output_name = sink.name
        self._volume_retry_at = 0.0

    def _on_master_volume(self, value: int) -> None:
        self.master_vol_label.setText(format_vol_db(value))
        self._master_volume_timer.start(180)

    def _commit_master_volume(self) -> None:
        sink = next((s for s in self._physical_sinks if s.name == self._selected_output_name), None)
        if sink is None:
            return
        try:
            if self.control is _control:
                self.control.set_volume(sink.id, self.master_slider.value() / 100.0, timeout=1.0)
            else:
                self.control.set_volume(sink.id, self.master_slider.value() / 100.0)
        except Exception as exc:
            logger.warning("device volume change failed: %s", exc)
            self.status_label.setText(f"Could not change device volume: {exc}")
            self._sync_master_volume()
            return
        self.status_label.setText("")

    def _on_default_sink(self, index: int) -> None:
        if not self.mutation_allowed():
            self.device_combo.blockSignals(True)
            self.device_combo.setCurrentIndex(self.device_combo.findData(self._selected_output_name))
            self.device_combo.blockSignals(False)
            self.status_label.setText("Wait for Apply to finish before changing output.")
            return
        name = self.device_combo.itemData(index)
        if not name or name == self._selected_output_name:
            return
        self._master_volume_timer.stop()
        graph = getattr(self, "graph_controller", None)
        if graph is not None:
            try:
                graph.set_output(name)
            except Exception as exc:
                self.status_label.setText(f"Could not change output: {exc}")
                self.device_combo.blockSignals(True)
                self.device_combo.setCurrentIndex(self.device_combo.findData(self._selected_output_name))
                self.device_combo.blockSignals(False)
                return
            self._selected_output_name = name
            self._sync_master_volume()
            self.refresh()
            return
        if self.is_routed():
            linker = getattr(self.control, "link_filter_output", _control.link_filter_output)
            runner = getattr(self.registry, "_runner", None)
            try:
                try:
                    linker("eqspace.filter-chain", name, runner=runner)
                except TypeError:
                    linker("eqspace.filter-chain", name)
            except Exception as exc:
                logger.warning("output link change failed: %s", exc)
                self.status_label.setText(f"Could not change output: {exc}")
                self.device_combo.blockSignals(True)
                self.device_combo.setCurrentIndex(self.device_combo.findData(self._selected_output_name))
                self.device_combo.blockSignals(False)
                return
        else:
            setter = getattr(self.control, "set_system_routing", _control.set_system_routing)
            try:
                try:
                    setter(False, fallback_sink_name=name, registry=self.registry)
                except TypeError:
                    setter(False, fallback_sink_name=name)
            except Exception as exc:
                logger.warning("output change failed: %s", exc)
                self.status_label.setText(f"Could not change output: {exc}")
                self.device_combo.blockSignals(True)
                self.device_combo.setCurrentIndex(self.device_combo.findData(self._selected_output_name))
                self.device_combo.blockSignals(False)
                return
        self._selected_output_name = name
        self._volume_retry_at = 0.0
        self.status_label.setText("")
        self._sync_master_volume()
        self.refresh()
