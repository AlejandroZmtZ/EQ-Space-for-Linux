"""Mic tab: DeepFilterNet noise reduction on the input side.

Noise reduction is enabled with the checkbox and its strength (wet/dry mix
in [0, 1]) with the slider; Apply renders the chain with
:class:`~eqspace.core.filterchain.mic.MicChainRenderer` and loads it via a
:class:`~eqspace.ui.module_args_manager.ModuleArgsManager`, exposing the
"EQ-Space Mic" virtual source. Unload tears it down again.

The status line reports whether the DeepFilterNet LADSPA plugin
(``libdf_ladspa.so``) is installed, via the injectable
``deepfilternet_available`` callable (monkeypatched in tests). When the
plugin is missing, Apply is disabled. The input monitor is a placeholder
label; routing a real capture stream to it is a later task.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from eqspace.core.filterchain.mic import MicChainRenderer, deepfilternet_available
from eqspace.core.pipewire.meter import PipeWireLevelMonitor


class MicWidget(QWidget):
    #: Emitted when the user asks to open the PEQ tab for the mic chain.
    open_peq_requested = Signal()

    def __init__(
        self,
        manager=None,
        deepfilternet_available_fn: Callable[[], bool] = deepfilternet_available,
        plugin_path: Optional[Path] = None,
        level_monitor: Optional[PipeWireLevelMonitor] = None,
        poll_interval_ms: int = 100,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self._deepfilternet_available = deepfilternet_available_fn
        self.plugin_path = Path(plugin_path) if plugin_path is not None else None
        self.level_monitor = level_monitor or PipeWireLevelMonitor()

        layout = QVBoxLayout(self)

        self.nr_check = QCheckBox("Enable noise reduction (DeepFilterNet)")
        layout.addWidget(self.nr_check)

        row = QHBoxLayout()
        row.addWidget(QLabel("Strength:"))
        self.strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.strength_slider.setRange(0, 100)
        self.strength_slider.setValue(100)
        row.addWidget(self.strength_slider)
        self.strength_label = QLabel("100%")
        row.addWidget(self.strength_label)
        layout.addLayout(row)
        self.strength_slider.valueChanged.connect(
            lambda value: self.strength_label.setText(f"{value}%")
        )

        row = QHBoxLayout()
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply)
        self.unload_button = QPushButton("Unload")
        self.unload_button.clicked.connect(self.unload)
        self.peq_button = QPushButton("Mic PEQ…")
        self.peq_button.clicked.connect(self.open_peq_requested)
        row.addWidget(self.apply_button)
        row.addWidget(self.unload_button)
        row.addWidget(self.peq_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.meter_widget = QWidget()
        meter_row = QHBoxLayout(self.meter_widget)
        meter_row.addWidget(QLabel("Input level:"))
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedHeight(12)
        self.level_bar.setStyleSheet(
            "QProgressBar { background: #22252a; border-radius: 4px; border: 1px solid #333842; }"
            "QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4caf50, stop:0.7 #ffeb3b, stop:1.0 #f44336); border-radius: 3px; }"
        )
        meter_row.addWidget(self.level_bar)
        layout.addWidget(self.meter_widget)
        # wpctl reports configured volume, not microphone signal activity.
        # Show this only when a real capture-level source is implemented.
        self.meter_widget.hide()

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addStretch(1)
        self.refresh_status()

        self.poll_timer = None

    def update_meter(self) -> None:
        level = self.level_monitor.get_level()
        self.level_bar.setValue(int(level * 100))

    # ---- availability ------------------------------------------------------

    def refresh_status(self) -> None:
        """Reflect deepfilternet_available() in the status line and buttons."""
        if self._deepfilternet_available():
            self.status_label.setText("DeepFilterNet plugin found")
            self.apply_button.setEnabled(True)
        else:
            self.status_label.setText(
                "DeepFilterNet LADSPA plugin not found — install DeepFilterNet "
                "to enable microphone noise reduction"
            )
            self.apply_button.setEnabled(False)

    # ---- chain rendering / loading ------------------------------------------

    def render_chain_args(self) -> str:
        strength = self.strength_slider.value() / 100.0
        return MicChainRenderer(plugin_path=self.plugin_path).render_args(
            strength=strength
        )

    def apply(self) -> None:
        if not self.nr_check.isChecked() or self.manager is None:
            self.status_label.setText(
                "Noise reduction disabled — enable it to load the mic chain"
            )
            return
        try:
            args = self.render_chain_args()
            if self.manager.is_loaded:
                self.manager.update_args(args)
            else:
                self.manager.load_args(args)
        except Exception as exc:
            self.status_label.setText(f"Apply failed: {exc}")
            return
        self.status_label.setText("Mic noise-reduction chain loaded")

    def unload(self) -> None:
        if self.manager is None:
            return
        try:
            self.manager.unload()
        except Exception as exc:
            self.status_label.setText(f"Unload failed: {exc}")
            return
        self.status_label.setText("Mic chain unloaded")

    # ---- state export / import ----------------------------------------------

    def get_state(self) -> dict[str, object]:
        return {
            "enabled": self.nr_check.isChecked(),
            "strength": self.strength_slider.value(),
        }

    def set_state(self, state: dict[str, object]) -> None:
        if "enabled" in state:
            self.nr_check.setChecked(bool(state["enabled"]))
        if "strength" in state:
            self.strength_slider.setValue(int(state["strength"]))
