"""Mixer tab: live per-app stream list, master volume, output device selector.

The PipeWire registry and the control functions are dependency-injected so
tests can substitute fakes. A QTimer polls ``registry.snapshot()``; slider and
mute changes call into ``eqspace.core.pipewire.control``.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from eqspace.core.pipewire import control as _control
from eqspace.core.pipewire.registry import (
    PipeWireRegistry,
    PipeWireUnavailable,
    PwNode,
)

logger = logging.getLogger(__name__)


class ControlLike(Protocol):
    def set_volume(self, node_id: int, volume: float) -> None: ...
    def set_mute(self, node_id: int, mute: bool) -> None: ...
    def set_default_sink(self, name: str) -> None: ...
    def move_stream(self, stream_id: int, sink_id: int) -> None: ...


class StreamRow(QWidget):
    """One playback stream: app name, volume slider, mute, target sink."""

    def __init__(
        self,
        node: PwNode,
        control: ControlLike,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.node = node
        self._control = control

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self.name_label = QLabel(node.app_name or node.name or f"node {node.id}")
        self.name_label.setMinimumWidth(180)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        initial = node.volume if node.volume is not None else 1.0
        self.slider.setValue(round(initial * 100))
        self.slider.valueChanged.connect(self._on_volume)

        self.mute_button = QPushButton("Mute")
        self.mute_button.setCheckable(True)
        self.mute_button.setChecked(bool(node.mute))
        self.mute_button.toggled.connect(self._on_mute)

        self.sink_combo = QComboBox()
        self.sink_combo.currentIndexChanged.connect(self._on_sink_changed)

        layout.addWidget(self.name_label)
        layout.addWidget(self.slider, stretch=1)
        layout.addWidget(self.mute_button)
        layout.addWidget(self.sink_combo)

    def _on_volume(self, value: int) -> None:
        try:
            self._control.set_volume(self.node.id, value / 100.0)
        except Exception:
            logger.exception("set_volume failed for node %s", self.node.id)

    def _on_mute(self, checked: bool) -> None:
        try:
            self._control.set_mute(self.node.id, checked)
        except Exception:
            logger.exception("set_mute failed for node %s", self.node.id)

    def _on_sink_changed(self, index: int) -> None:
        sink_id = self.sink_combo.itemData(index)
        if sink_id is None:
            return
        try:
            move = getattr(self._control, "move_stream", None)
            if move is not None:
                move(self.node.id, sink_id)
        except Exception:
            logger.exception("move_stream failed for node %s", self.node.id)


class MixerWidget(QWidget):
    def __init__(
        self,
        registry: Optional[PipeWireRegistry] = None,
        control: Optional[ControlLike] = None,
        poll_interval_ms: int = 1000,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry or PipeWireRegistry()
        self.control: ControlLike = control or _control  # type: ignore[assignment]

        layout = QVBoxLayout(self)

        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("Output device:"))
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._on_default_sink)
        device_row.addWidget(self.device_combo, stretch=1)
        layout.addLayout(device_row)

        master_row = QHBoxLayout()
        master_row.addWidget(QLabel("Master volume:"))
        self.master_slider = QSlider(Qt.Orientation.Horizontal)
        self.master_slider.setRange(0, 100)
        self.master_slider.setValue(100)
        self.master_slider.valueChanged.connect(self._on_master_volume)
        master_row.addWidget(self.master_slider, stretch=1)
        layout.addLayout(master_row)

        header = QLabel("Application streams")
        header.setObjectName("sectionHeader")
        layout.addWidget(header)

        self._streams_container = QWidget()
        self.streams_layout = QVBoxLayout(self._streams_container)
        self.streams_layout.setContentsMargins(0, 0, 0, 0)
        self.streams_layout.addStretch(1)
        layout.addWidget(self._streams_container, stretch=1)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.stream_rows: list[StreamRow] = []

        self._sinks: tuple[PwNode, ...] = ()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        if poll_interval_ms > 0:
            self._timer.start(poll_interval_ms)

        # Defer the first poll so window construction isn't blocked on a
        # slow real registry; rows populate on the next event loop pass.
        QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        """Poll the registry and rebuild the stream rows."""
        try:
            snapshot = self.registry.snapshot()
        except PipeWireUnavailable as exc:
            self.status_label.setText(f"PipeWire unavailable: {exc}")
            return
        except Exception:
            logger.exception("registry snapshot failed")
            self.status_label.setText("PipeWire unavailable")
            return
        self.status_label.setText("")
        self._rebuild_sinks(snapshot.sinks)
        self._rebuild_streams(snapshot.streams)

    def _rebuild_sinks(self, sinks: tuple[PwNode, ...]) -> None:
        self._sinks = sinks
        current = [
            (self.device_combo.itemText(i), self.device_combo.itemData(i))
            for i in range(self.device_combo.count())
        ]
        new = [(s.app_name or s.name, s.name) for s in sinks]
        if current == new:
            return
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for label, name in new:
            self.device_combo.addItem(label, name)
        self.device_combo.blockSignals(False)

    def _rebuild_streams(self, streams: tuple[PwNode, ...]) -> None:
        if [r.node for r in self.stream_rows] == list(streams):
            return
        for row in self.stream_rows:
            self.streams_layout.removeWidget(row)
            row.deleteLater()
        self.stream_rows = []
        sinks = [
            (self.device_combo.itemText(i), self.device_combo.itemData(i))
            for i in range(self.device_combo.count())
        ]
        for node in streams:
            row = StreamRow(node, self.control)
            for label, sink_name in sinks:
                row.sink_combo.addItem(label, sink_name)
            self.stream_rows.append(row)
            self.streams_layout.insertWidget(self.streams_layout.count() - 1, row)

    def _on_master_volume(self, value: int) -> None:
        # Apply to the selected output device.
        index = self.device_combo.currentIndex()
        name = self.device_combo.itemData(index) if index >= 0 else None
        for sink in self._sinks:
            if sink.name == name:
                try:
                    self.control.set_volume(sink.id, value / 100.0)
                except Exception:
                    logger.exception("master volume change failed")
                return

    def _on_default_sink(self, index: int) -> None:
        name = self.device_combo.itemData(index)
        if not name:
            return
        try:
            self.control.set_default_sink(name)
        except Exception:
            logger.exception("set_default_sink failed")
