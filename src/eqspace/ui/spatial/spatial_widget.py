"""Spatial tab: HRTF virtual-surround stage.

SOFA files are picked up from the user HRTF directory,
``$XDG_DATA_HOME/eqspace/hrtf`` (default ``~/.local/share/eqspace/hrtf``).
Drop ``.sofa`` files there — e.g. from https://sofa.acn…  (SOFA conventions
site / KEMAR, CIEC, Listen measurements) — then select one and hit Apply.

Controls: HRTF profile picker, virtual layout (stereo / 5.1 / 7.1), wet/dry
slider and a crossfeed toggle. With crossfeed on, the stereo layout collapses
to a near-field ±30° pair (a bs2b-style crossfeed approximation); with it
off the full virtual-speaker ring for the chosen layout is rendered.

Graceful states: without ``pysofa`` the tab shows "HRTF unavailable
(install pysofa)"; with an empty HRTF directory it shows "no SOFA files
found (download from e.g. sofa.acn…)". The chain is rendered by
:class:`~eqspace.core.filterchain.spatial.SpatialChainRenderer` and loaded
through a :class:`~eqspace.ui.module_args_manager.ModuleArgsManager`.
"""

from __future__ import annotations

import os
from importlib.util import find_spec
from pathlib import Path
from typing import Callable, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from eqspace.core.dsp.hrtf import HRTFUnavailable, SPEAKER_AZIMUTHS, extract_speaker_irs
from eqspace.core.filterchain.spatial import SpatialChainRenderer, SpeakerIR

DEFAULT_FS = 48000.0

#: Virtual layouts -> node audio positions.
LAYOUT_CHANNELS = {
    "Stereo": ("FL", "FR"),
    "5.1": ("FL", "FR", "FC", "LFE", "SL", "SR"),
    "7.1": ("FL", "FR", "FC", "LFE", "SL", "SR", "RL", "RR"),
}

#: Virtual layouts -> virtual-speaker azimuths (degrees).
LAYOUT_AZIMUTHS = {
    "Stereo": (-110.0, -90.0, -30.0, 30.0, 90.0, 110.0),
    "5.1": (-110.0, -30.0, 0.0, 30.0, 110.0),
    "7.1": (-110.0, -90.0, -30.0, 0.0, 30.0, 90.0, 110.0),
}

#: Crossfeed uses a near-field ±30° pair (bs2b-style approximation).
CROSSFEED_AZIMUTHS = (-30.0, 30.0)

NO_SOFA_MESSAGE = (
    "No SOFA files found. Download HRTF sets (e.g. from sofa.acn… "
    "conventions / KEMAR / Listen) into"
)
NO_PYSOFA_MESSAGE = "HRTF unavailable — install pysofa to load SOFA files"


def default_hrtf_dir() -> Path:
    """User HRTF directory: ``$XDG_DATA_HOME/eqspace/hrtf``."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "eqspace" / "hrtf"


class SpatialWidget(QWidget):
    def __init__(
        self,
        manager=None,
        hrtf_dir: Optional[Path] = None,
        fs: float = DEFAULT_FS,
        pysofa_available: Optional[Callable[[], bool]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.hrtf_dir = Path(hrtf_dir) if hrtf_dir is not None else default_hrtf_dir()
        self.fs = fs
        self._pysofa_available = pysofa_available or (lambda: find_spec("pysofa") is not None)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("HRTF profile (SOFA file):"))
        self.sofa_combo = QComboBox()
        layout.addWidget(self.sofa_combo)

        row = QHBoxLayout()
        row.addWidget(QLabel("Virtual layout:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(list(LAYOUT_CHANNELS))
        row.addWidget(self.layout_combo)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Wet / dry:"))
        self.wetdry_slider = QSlider(Qt.Orientation.Horizontal)
        self.wetdry_slider.setRange(0, 100)
        self.wetdry_slider.setValue(100)
        row.addWidget(self.wetdry_slider)
        self.wetdry_label = QLabel("100%")
        row.addWidget(self.wetdry_label)
        layout.addLayout(row)
        self.wetdry_slider.valueChanged.connect(
            lambda value: self.wetdry_label.setText(f"{value}%")
        )

        self.crossfeed_check = QCheckBox("Crossfeed (near-field stereo)")
        layout.addWidget(self.crossfeed_check)

        row = QHBoxLayout()
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply)
        self.unload_button = QPushButton("Unload")
        self.unload_button.clicked.connect(self.unload)
        row.addWidget(self.apply_button)
        row.addWidget(self.unload_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addStretch(1)
        self.refresh_sofa_files()

    # ---- SOFA discovery ---------------------------------------------------

    def sofa_files(self) -> list[Path]:
        """Sorted ``.sofa`` files in the HRTF directory (empty if missing)."""
        if not self.hrtf_dir.is_dir():
            return []
        return sorted(self.hrtf_dir.glob("*.sofa"))

    def refresh_sofa_files(self) -> None:
        self.sofa_combo.clear()
        files = self.sofa_files()
        for path in files:
            self.sofa_combo.addItem(path.name, userData=str(path))
        self._update_availability(files)

    def _update_availability(self, files: Sequence[Path]) -> None:
        if not self._pysofa_available():
            self.status_label.setText(NO_PYSOFA_MESSAGE)
            self.apply_button.setEnabled(False)
        elif not files:
            self.status_label.setText(
                f"{NO_SOFA_MESSAGE} {self.hrtf_dir}"
            )
            self.apply_button.setEnabled(False)
        else:
            self.status_label.setText("")
            self.apply_button.setEnabled(True)

    # ---- chain rendering / loading ----------------------------------------

    def selected_sofa(self) -> Optional[Path]:
        path = self.sofa_combo.currentData()
        return Path(path) if path else None

    def selected_layout(self) -> str:
        return self.layout_combo.currentText()

    def azimuths(self) -> Sequence[float]:
        if self.crossfeed_check.isChecked() and self.selected_layout() == "Stereo":
            return CROSSFEED_AZIMUTHS
        return LAYOUT_AZIMUTHS[self.selected_layout()]

    def wet_gain(self) -> float:
        """Map the wet/dry slider to a positive chain gain."""
        wet = self.wetdry_slider.value() / 100.0
        return 0.05 + 0.95 * wet

    def render_chain_args(self, sofa_path: Path) -> str:
        """Extract IRs and render the single-line module args."""
        azimuths = self.azimuths()
        ir_paths = extract_speaker_irs(sofa_path, self.fs, azimuths_deg=azimuths)
        speakers = [
            SpeakerIR(azimuth=az, left_ir=paths[0], right_ir=paths[1])
            for az, paths in sorted(ir_paths.items())
        ]
        return SpatialChainRenderer().render_args(
            speakers,
            gain=self.wet_gain(),
            channels=LAYOUT_CHANNELS[self.selected_layout()],
        )

    def apply(self) -> None:
        sofa = self.selected_sofa()
        if sofa is None or self.manager is None:
            return
        try:
            args = self.render_chain_args(sofa)
            if self.manager.is_loaded:
                self.manager.update_args(args)
            else:
                self.manager.load_args(args)
        except HRTFUnavailable:
            self.status_label.setText(NO_PYSOFA_MESSAGE)
            return
        except Exception as exc:
            self.status_label.setText(f"Apply failed: {exc}")
            return
        self.status_label.setText(
            f"Spatial chain loaded: {sofa.name} ({self.selected_layout()})"
        )

    def unload(self) -> None:
        if self.manager is None:
            return
        try:
            self.manager.unload()
        except Exception as exc:
            self.status_label.setText(f"Unload failed: {exc}")
            return
        self.status_label.setText("Spatial chain unloaded")
