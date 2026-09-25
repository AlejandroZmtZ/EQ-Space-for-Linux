"""Spatial tab: HRTF virtual-surround stage and binaural crossfeed.

Organized into two intuitive sections:
1. Natural Headphone Crossfeed (Bauer / Meier): Removes harsh ear isolation by
   gently blending acoustic cues between ears.
2. 3D Virtual Surround (HRTF Convolver): Simulates 5.1 / 7.1 room speaker setups
   in headphones using Head-Related Transfer Functions (built-in KEMAR or custom SOFA).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from eqspace.core.dsp.crossfeed import render_crossfeed_chain_args
from eqspace.core.pipewire.control import set_system_routing
from eqspace.core.dsp.hrtf import (
    HRTFUnavailable,
    SPEAKER_AZIMUTHS,
    builtin_kemar_path,
    extract_speaker_irs,
    is_builtin_kemar,
)
from eqspace.core.filterchain.spatial import (
    CROSSFEED_AZIMUTHS,
    LAYOUT_AZIMUTHS,
    LAYOUT_CHANNEL_SPEAKER_MAP,
    LAYOUT_CHANNELS,
    SpatialChainRenderer,
    SpeakerIR,
)

DEFAULT_FS = 48000.0

NO_SOFA_MESSAGE = (
    "No external SOFA files found. Drop .sofa files into"
)
NO_PYSOFA_MESSAGE = "HRTF unavailable — install pysofa to load external SOFA files"


def default_hrtf_dir() -> Path:
    """User HRTF directory: ``$XDG_DATA_HOME/eqspace/hrtf``."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "eqspace" / "hrtf"


def _default_pysofa_available() -> bool:
    try:
        import pysofa  # noqa: F401
        return True
    except (ImportError, Exception):
        return False


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
        self._pysofa_available = pysofa_available or _default_pysofa_available

        root_layout = QVBoxLayout(self)

        # Section 1: Natural Headphone Crossfeed
        crossfeed_group = QGroupBox("Natural Headphone Crossfeed (Bauer / Meier)")
        cf_layout = QVBoxLayout(crossfeed_group)

        cf_info = QLabel(
            "Removes harsh left/right ear isolation on headphones by gently blending "
            "acoustic cues between ears, recreating a natural stereo speaker soundstage."
        )
        cf_info.setWordWrap(True)
        cf_info.setStyleSheet("color: #a0a0a0; font-size: 11px;")
        cf_layout.addWidget(cf_info)

        cf_controls = QHBoxLayout()
        self.crossfeed_check = QCheckBox("Enable Crossfeed")
        cf_controls.addWidget(self.crossfeed_check)

        cf_controls.addSpacing(20)
        cf_controls.addWidget(QLabel("Mode:"))
        self.crossfeed_combo = QComboBox()
        self.crossfeed_combo.addItems(["Bauer", "Meier"])
        self.crossfeed_combo.setEnabled(False)
        cf_controls.addWidget(self.crossfeed_combo)
        cf_controls.addStretch(1)
        cf_layout.addLayout(cf_controls)

        root_layout.addWidget(crossfeed_group)

        # Section 2: 3D Virtual Surround (HRTF Convolver)
        surround_group = QGroupBox("3D Virtual Surround (HRTF Convolver)")
        sr_layout = QVBoxLayout(surround_group)

        sr_info = QLabel(
            "Simulates full 5.1 / 7.1 room speaker setups in your headphones using "
            "Head-Related Transfer Functions."
        )
        sr_info.setWordWrap(True)
        sr_info.setStyleSheet("color: #a0a0a0; font-size: 11px;")
        sr_layout.addWidget(sr_info)

        sofa_row = QHBoxLayout()
        sofa_row.addWidget(QLabel("HRTF profile (SOFA file):"))
        self.sofa_combo = QComboBox()
        sofa_row.addWidget(self.sofa_combo, 1)
        sr_layout.addLayout(sofa_row)

        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("Virtual layout:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(list(LAYOUT_CHANNEL_SPEAKER_MAP.keys()))
        layout_row.addWidget(self.layout_combo)
        layout_row.addStretch(1)
        sr_layout.addLayout(layout_row)

        self.layout_hint_label = QLabel("")
        self.layout_hint_label.setWordWrap(True)
        self.layout_hint_label.setStyleSheet("color: #e09030; font-size: 11px;")
        sr_layout.addWidget(self.layout_hint_label)

        wet_row = QHBoxLayout()
        wet_row.addWidget(QLabel("Wet / dry:"))
        self.wetdry_slider = QSlider(Qt.Orientation.Horizontal)
        self.wetdry_slider.setRange(0, 100)
        self.wetdry_slider.setValue(100)
        wet_row.addWidget(self.wetdry_slider, 1)
        self.wetdry_label = QLabel("100%")
        wet_row.addWidget(self.wetdry_label)
        sr_layout.addLayout(wet_row)
        self.wetdry_slider.valueChanged.connect(
            lambda value: self.wetdry_label.setText(f"{value}%")
        )

        root_layout.addWidget(surround_group)

        # Action Buttons & Status
        btn_row = QHBoxLayout()
        self.apply_button = QPushButton("Apply Spatial Audio")
        self.apply_button.clicked.connect(self.apply)
        self.unload_button = QPushButton("Unload")
        self.unload_button.clicked.connect(self.unload)
        btn_row.addWidget(self.apply_button)
        btn_row.addWidget(self.unload_button)
        btn_row.addStretch(1)
        root_layout.addLayout(btn_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root_layout.addWidget(self.status_label)

        root_layout.addStretch(1)

        # Wire events
        self.crossfeed_check.toggled.connect(self._on_crossfeed_toggled)
        self.crossfeed_combo.currentTextChanged.connect(lambda _: self._update_availability())
        self.sofa_combo.currentIndexChanged.connect(lambda _: self._update_availability())

        self.refresh_sofa_files()

    def _on_crossfeed_toggled(self, checked: bool) -> None:
        self.crossfeed_combo.setEnabled(checked)
        self.sofa_combo.setEnabled(not checked)
        self.layout_combo.setEnabled(not checked)
        self.wetdry_slider.setEnabled(not checked)
        if checked:
            self.layout_hint_label.setText(
                "Virtual surround layout disabled while crossfeed is active."
            )
        else:
            self.layout_hint_label.setText("")
        self._update_availability()

    # ---- SOFA discovery ---------------------------------------------------

    def sofa_files(self) -> list[Path]:
        """Sorted ``.sofa`` files in the user HRTF directory."""
        if not self.hrtf_dir.is_dir():
            return []
        return sorted(p for p in self.hrtf_dir.glob("*.sofa") if not is_builtin_kemar(p))

    def refresh_sofa_files(self) -> None:
        self.sofa_combo.clear()
        # Always add Default KEMAR as first item so dropdown is never empty
        self.sofa_combo.addItem("Synthetic KEMAR model (Built-in)", userData=str(builtin_kemar_path()))
        for path in self.sofa_files():
            self.sofa_combo.addItem(path.name, userData=str(path))
        self._update_availability()

    def _update_availability(self) -> None:
        if self.crossfeed_check.isChecked():
            mode = self.crossfeed_combo.currentText()
            self.status_label.setText(f"Crossfeed mode ready ({mode})")
            self.apply_button.setEnabled(True)
            return

        selected = self.selected_sofa()
        if selected is None or is_builtin_kemar(selected):
            self.status_label.setText("")
            self.apply_button.setEnabled(True)
            return

        if not self._pysofa_available():
            self.status_label.setText(NO_PYSOFA_MESSAGE)
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
        return LAYOUT_AZIMUTHS.get(self.selected_layout(), (-30.0, 30.0))

    def wet_gain(self) -> float:
        """Map the wet/dry slider to a positive chain gain."""
        wet = self.wetdry_slider.value() / 100.0
        return 0.05 + 0.95 * wet

    def render_chain_args(self, sofa_path: Path) -> str:
        """Extract IRs and render the single-line module args."""
        layout = self.selected_layout()
        channel_map = LAYOUT_CHANNEL_SPEAKER_MAP.get(
            layout, LAYOUT_CHANNEL_SPEAKER_MAP["Stereo"]
        )
        needed_azimuths = sorted(set(az for _, az in channel_map))
        ir_paths = extract_speaker_irs(sofa_path, self.fs, azimuths_deg=needed_azimuths)
        speakers = [
            SpeakerIR(
                azimuth=az,
                left_ir=ir_paths[az][0],
                right_ir=ir_paths[az][1],
                channel=ch,
            )
            for ch, az in channel_map
        ]
        channels = LAYOUT_CHANNELS.get(layout, ("FL", "FR"))
        return SpatialChainRenderer().render_args(
            speakers,
            gain=self.wet_gain(),
            channels=channels,
        )

    def apply(self) -> None:
        if self.manager is None:
            return

        if self.crossfeed_check.isChecked():
            mode = self.crossfeed_combo.currentText()
            try:
                args = render_crossfeed_chain_args(preset=mode.lower(), fs=self.fs)
                if self.manager.is_loaded:
                    self.manager.update_args(args)
                else:
                    self.manager.load_args(args)
            except Exception as exc:
                self.status_label.setText(f"Apply failed: {exc}")
                return
            try:
                set_system_routing(enable=True, filter_node_name="eqspace.crossfeed")
            except Exception:
                pass
            self.status_label.setText(f"Crossfeed chain loaded ({mode})")
            return

        sofa = self.selected_sofa() or builtin_kemar_path()
        layout = self.selected_layout()
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

        try:
            set_system_routing(enable=True, filter_node_name="eqspace.spatial")
        except Exception:
            pass

        profile_display = "Default KEMAR" if is_builtin_kemar(sofa) else sofa.name
        self.status_label.setText(
            f"Spatial chain loaded: {profile_display} ({layout})"
        )

    def unload(self) -> None:
        if self.manager is None:
            return
        try:
            from eqspace.core.pipewire.registry import PipeWireRegistry
            reg = PipeWireRegistry()
            peq_loaded = any(s.name == "eqspace.filter-chain" for s in reg.snapshot().sinks)
            if peq_loaded:
                set_system_routing(enable=True, filter_node_name="eqspace.filter-chain")
            else:
                set_system_routing(enable=False)
        except Exception:
            pass
        try:
            self.manager.unload()
        except Exception as exc:
            self.status_label.setText(f"Unload failed: {exc}")
            return
        self.status_label.setText("Spatial chain unloaded")

    # ---- state export / import ----------------------------------------------

    def get_state(self) -> dict[str, object]:
        sofa = self.selected_sofa()
        return {
            "sofa_path": str(sofa) if sofa else None,
            "layout": self.selected_layout(),
            "wet": self.wetdry_slider.value(),
            "crossfeed": self.crossfeed_check.isChecked(),
            "crossfeed_mode": self.crossfeed_combo.currentText(),
        }

    def set_state(self, state: dict[str, object]) -> None:
        if "layout" in state:
            idx = self.layout_combo.findText(str(state["layout"]))
            if idx >= 0:
                self.layout_combo.setCurrentIndex(idx)
        if "wet" in state:
            self.wetdry_slider.setValue(int(state["wet"]))
        if "crossfeed_mode" in state:
            idx = self.crossfeed_combo.findText(str(state["crossfeed_mode"]), Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self.crossfeed_combo.setCurrentIndex(idx)
        elif "algorithm" in state:
            idx = self.crossfeed_combo.findText(str(state["algorithm"]).capitalize(), Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self.crossfeed_combo.setCurrentIndex(idx)
        if "crossfeed" in state:
            self.crossfeed_check.setChecked(bool(state["crossfeed"]))
        elif state.get("type") == "crossfeed":
            self.crossfeed_check.setChecked(True)
        if "sofa_path" in state and state["sofa_path"] is not None:
            path_str = str(state["sofa_path"])
            idx = self.sofa_combo.findData(path_str)
            if idx >= 0:
                self.sofa_combo.setCurrentIndex(idx)
            else:
                p = Path(path_str)
                if p.is_file():
                    self.sofa_combo.addItem(p.name, userData=path_str)
                    self.sofa_combo.setCurrentIndex(self.sofa_combo.count() - 1)
