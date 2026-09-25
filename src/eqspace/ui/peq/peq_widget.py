"""Parametric EQ tab: pyqtgraph response curve with draggable band handles.

The x axis is plotted as log10(freq) so log spacing and drag math stay simple;
tick labels show real frequencies. Dragging a handle moves freq/gain, the
mouse wheel over a handle adjusts Q. ``Apply`` pushes the band list to the
injected FilterChainManager; ``Save as profile`` only emits the
:attr:`PeqWidget.save_profile_requested` signal — profile persistence is
wired up in a later task.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from eqspace.core.dsp.biquads import magnitude_response
from eqspace.core.dsp.filter_design import VALID_BAND_TYPES, EQBand, design_filters
from eqspace.core.filterchain.manager import FilterChainManager, FilterSpec

logger = logging.getLogger(__name__)

MAX_BANDS = 16
F_MIN = 20.0
F_MAX = 20000.0
GAIN_LIMIT = 24.0
Q_MIN = 0.1
Q_MAX = 10.0
DEFAULT_FS = 48000.0

_BAND_TYPE_LABELS = {
    "peaking": "bq_peaking",
    "low_shelf": "bq_lowshelf",
    "high_shelf": "bq_highshelf",
    "low_pass": "bq_lowpass",
    "high_pass": "bq_highpass",
    "notch": "bq_notch",
}

_X_MIN = float(np.log10(F_MIN))
_X_MAX = float(np.log10(F_MAX))
_FREQ_TICKS = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]


class _BandHandles(pg.ScatterPlotItem):
    """Draggable band handles: drag = freq/gain, wheel = Q."""

    dragged = Signal(int, float, float)  # band index, freq_hz, gain_db
    wheeled = Signal(int, float)  # band index, wheel steps (Q direction)

    def __init__(self) -> None:
        super().__init__(size=12, pen=pg.mkPen("w", width=1), brush=pg.mkBrush(91, 157, 255))
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self._drag_index: Optional[int] = None

    def mouseDragEvent(self, ev) -> None:  # noqa: N802 (pyqtgraph API)
        if ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        ev.accept()
        vb = self.getViewBox()
        if vb is None:
            return
        if ev.isStart():
            points = self.pointsAt(ev.buttonDownPos())
            if not points:
                ev.ignore()
                return
            self._drag_index = int(points[0].data())
        elif ev.isFinish():
            self._drag_index = None
        elif self._drag_index is not None:
            pos = vb.mapSceneToView(ev.scenePos())
            freq = float(np.clip(10 ** pos.x(), F_MIN, F_MAX))
            gain = float(np.clip(pos.y(), -GAIN_LIMIT, GAIN_LIMIT))
            self.dragged.emit(self._drag_index, freq, gain)

    def wheelEvent(self, ev) -> None:  # noqa: N802
        points = self.pointsAt(ev.pos())
        if not points:
            ev.ignore()
            return
        ev.accept()
        index = int(points[0].data())
        self.wheeled.emit(index, ev.delta() / 120.0)


class PeqWidget(QWidget):
    # Stub for the profiles feature (later task): emitted with the current
    # list of EQBand when the user clicks "Save as profile".
    save_profile_requested = Signal(list)

    def __init__(
        self,
        manager: Optional[FilterChainManager] = None,
        fs: float = DEFAULT_FS,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.fs = fs
        self.bands: list[EQBand] = [
            EQBand(band_type="peaking", freq_hz=1000.0, gain_db=0.0, q=1.0)
        ]
        self.curve_updates = 0  # test hook: incremented on every recompute

        layout = QVBoxLayout(self)

        self.plot = pg.PlotWidget()
        self.plot.setBackground("#232429")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Frequency", units="Hz")
        self.plot.setLabel("left", "Gain", units="dB")
        self.plot.setXRange(_X_MIN, _X_MAX, padding=0)
        self.plot.setYRange(-GAIN_LIMIT, GAIN_LIMIT, padding=0)
        axis = self.plot.getAxis("bottom")
        axis.setTicks([[(float(np.log10(f)), str(f)) for f in _FREQ_TICKS]])
        self.curve_item = self.plot.plot(pen=pg.mkPen((91, 157, 255), width=2))
        self.handles = _BandHandles()
        self.handles.dragged.connect(self._on_handle_dragged)
        self.handles.wheeled.connect(self._on_handle_wheeled)
        self.plot.addItem(self.handles)
        layout.addWidget(self.plot, stretch=1)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Type", "Freq (Hz)", "Gain (dB)", "Q", "Enabled"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add band")
        self.add_button.clicked.connect(self.add_band)
        self.remove_button = QPushButton("Remove band")
        self.remove_button.clicked.connect(self.remove_selected_band)
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply)
        self.save_button = QPushButton("Save as profile")
        self.save_button.clicked.connect(
            lambda: self.save_profile_requested.emit(list(self.bands))
        )
        for button in (self.add_button, self.remove_button, self.apply_button, self.save_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self._refresh_table()
        self.update_curve()

    # ---- band model ----------------------------------------------------

    def add_band(self) -> None:
        if len(self.bands) >= MAX_BANDS:
            self.status_label.setText(f"Maximum of {MAX_BANDS} bands reached")
            return
        self.status_label.setText("")
        self.bands.append(EQBand(band_type="peaking", freq_hz=1000.0, gain_db=0.0, q=1.0))
        self._refresh_table()
        self.update_curve()

    def remove_selected_band(self) -> None:
        row = self.table.currentRow()
        if row < 0 and self.bands:
            row = len(self.bands) - 1
        if 0 <= row < len(self.bands):
            del self.bands[row]
            self._refresh_table()
            self.update_curve()

    def set_band(self, index: int, **changes) -> None:
        band = replace(self.bands[index], **changes)
        self.bands[index] = band
        self._refresh_table()
        self.update_curve()

    # ---- curve ----------------------------------------------------------

    def update_curve(self) -> None:
        """Recompute the combined response from the current bands."""
        self.curve_updates += 1
        freqs = np.logspace(np.log10(F_MIN), np.log10(F_MAX), 512)
        coeffs = design_filters(self.bands, self.fs)
        db = magnitude_response(coeffs, freqs, self.fs)
        self.curve_item.setData(np.log10(freqs), np.clip(db, -GAIN_LIMIT, GAIN_LIMIT))
        self.handles.setData(
            x=[float(np.log10(b.freq_hz)) for b in self.bands],
            y=[b.gain_db for b in self.bands],
            data=list(range(len(self.bands))),
        )

    # ---- handle callbacks -----------------------------------------------

    def _on_handle_dragged(self, index: int, freq: float, gain: float) -> None:
        self.set_band(index, freq_hz=freq, gain_db=gain)

    def _on_handle_wheeled(self, index: int, steps: float) -> None:
        q = self.bands[index].q * (1.1 ** steps)
        self.set_band(index, q=float(np.clip(q, Q_MIN, Q_MAX)))

    # ---- table -----------------------------------------------------------

    def _refresh_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.bands))
        for row, band in enumerate(self.bands):
            type_combo = QComboBox()
            type_combo.addItems(VALID_BAND_TYPES)
            type_combo.setCurrentText(band.band_type)
            type_combo.currentTextChanged.connect(
                lambda text, r=row: self.set_band(r, band_type=text)
            )
            self.table.setCellWidget(row, 0, type_combo)

            freq_spin = QDoubleSpinBox()
            freq_spin.setRange(F_MIN, F_MAX)
            freq_spin.setValue(band.freq_hz)
            freq_spin.valueChanged.connect(
                lambda value, r=row: self.set_band(r, freq_hz=float(value))
            )
            self.table.setCellWidget(row, 1, freq_spin)

            gain_spin = QDoubleSpinBox()
            gain_spin.setRange(-GAIN_LIMIT, GAIN_LIMIT)
            gain_spin.setValue(band.gain_db)
            gain_spin.valueChanged.connect(
                lambda value, r=row: self.set_band(r, gain_db=float(value))
            )
            self.table.setCellWidget(row, 2, gain_spin)

            q_spin = QDoubleSpinBox()
            q_spin.setRange(Q_MIN, Q_MAX)
            q_spin.setSingleStep(0.1)
            q_spin.setValue(band.q)
            q_spin.valueChanged.connect(lambda value, r=row: self.set_band(r, q=float(value)))
            self.table.setCellWidget(row, 3, q_spin)

            enabled_check = QCheckBox()
            enabled_check.setChecked(band.enabled)
            enabled_check.toggled.connect(
                lambda checked, r=row: self.set_band(r, enabled=bool(checked))
            )
            self.table.setCellWidget(row, 4, enabled_check)

        self.table.blockSignals(False)
        self.add_button.setEnabled(len(self.bands) < MAX_BANDS)

    # ---- apply -----------------------------------------------------------

    def _filter_specs(self) -> list[FilterSpec]:
        specs = []
        for i, band in enumerate(self.bands):
            if not band.enabled:
                continue
            specs.append(
                FilterSpec(
                    name=f"band_{i}",
                    filter_type=_BAND_TYPE_LABELS[band.band_type],
                    params={"Freq": band.freq_hz, "Gain": band.gain_db, "Q": band.q},
                )
            )
        return specs

    def apply(self) -> None:
        """Push the current bands to the filter-chain manager."""
        if self.manager is None:
            self.status_label.setText("No filter-chain manager configured")
            return
        specs = self._filter_specs()
        try:
            if self.manager.is_loaded:
                for spec in specs:
                    for param, value in spec.params.items():
                        self.manager.set_filter_param(
                            self.manager.node_name, f"{spec.name}:{param}", value
                        )
            else:
                self.manager.load(specs)
        except Exception as exc:
            logger.exception("apply failed")
            self.status_label.setText(f"Apply failed: {exc}")
            return
        self.status_label.setText("Applied")
