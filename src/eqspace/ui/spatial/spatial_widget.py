"""Spatial tab: HRTF virtual-surround stage and binaural crossfeed.

Modernized with human-centered UX, unified profile selector, subtle onboarding info card,
and zero-crash automatic unload.

Profiles:
- HoloSpace 3D (Signature Spatial Immersion) [Default]
- Cinema 7.1 Surround (Virtual Room)
- Natural Crossfeed — Bauer (Fatigue-Free Stereo)
- Natural Crossfeed — Meier (Warm Acoustic Blend)
- Studio Monitor (Nearfield ±30°)
- Custom SOFA Profile (External File)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from eqspace.core.dsp.spatial import prepare_spatial
from eqspace.core.filterchain.spatial import HYBRID_DESCRIPTION, hybrid_controls

from eqspace.core.dsp.crossfeed import render_crossfeed_chain_args
from eqspace.core.dsp.headroom import crossfeed_peak_db, spatial_peak_db
from eqspace.core.dsp.hrtf import (
    HRTFUnavailable,
    SPEAKER_AZIMUTHS,
    builtin_kemar_path,
    extract_speaker_irs,
    holospace_default_path,
    is_builtin_kemar,
    is_holospace_default,
)
from eqspace.core.filterchain.spatial import (
    CROSSFEED_AZIMUTHS,
    LAYOUT_AZIMUTHS,
    LAYOUT_CHANNEL_SPEAKER_MAP,
    LAYOUT_CHANNELS,
    SpatialChainRenderer,
    SpeakerIR,
)
from eqspace.core.pipewire.control import link_filter_output, set_system_routing

DEFAULT_FS = 48000.0

NO_SOFA_MESSAGE = "No external SOFA files found. Drop .sofa files into"
NO_PYSOFA_MESSAGE = "HRTF unavailable — install pysofa to load external SOFA files"

PROFILE_HOLOSPACE = "HoloSpace 3D (Signature Spatial Immersion)"
PROFILE_CINEMA_71 = "Cinema 7.1 Surround (Virtual Room)"
PROFILE_CROSSFEED_BAUER = "Natural Crossfeed — Bauer (Fatigue-Free Stereo)"
PROFILE_CROSSFEED_MEIER = "Natural Crossfeed — Meier (Warm Acoustic Blend)"
PROFILE_STUDIO_MONITOR = "Studio Monitor (Nearfield ±30°)"
PROFILE_CUSTOM_SOFA = "Custom SOFA Profile (External File)"

PROFILE_HYBRID = HYBRID_DESCRIPTION

PROFILES = [
    PROFILE_HOLOSPACE,
    PROFILE_CINEMA_71,
    PROFILE_CROSSFEED_BAUER,
    PROFILE_CROSSFEED_MEIER,
    PROFILE_STUDIO_MONITOR,
    PROFILE_CUSTOM_SOFA,
    PROFILE_HYBRID,
]

PROFILE_DESCRIPTIONS = {
    PROFILE_HYBRID: (
        "Designed to make stereo music feel wider and more speaker-like while keeping vocals grounded "
        "and bringing acoustic instruments and room ambience forward. Its spatial cues use a synthetic "
        "model, and the listening benefits have not yet been checked in controlled, level-matched sessions; "
        "the effect can vary with headphones and recordings. That is why HS+ remains experimental."
    ),
    PROFILE_HOLOSPACE: (
        "Expands standard stereo into a 3D holographic soundstage with front dialogue focus, "
        "pinna height cues, and subtle cinema room reflections. "
        "Best for: Movies, gaming, and immersive music on headphones."
    ),
    PROFILE_CINEMA_71: (
        "Uses a synthetic headphone model to place sound in a virtual 7.1 room. "
        "Supports stereo expansion or native multichannel input for movies and games."
    ),
    PROFILE_CROSSFEED_BAUER: (
        "Blends low frequencies between channels to soften extreme left/right separation. "
        "Best for: Classic rock, jazz, and vintage hard-panned stereo recordings."
    ),
    PROFILE_CROSSFEED_MEIER: (
        "Gentle acoustic crossfeed with warm low-end balance. "
        "Best for: Natural acoustic recordings and extended listening sessions."
    ),
    PROFILE_STUDIO_MONITOR: (
        "Uses a synthetic headphone model to suggest a pair of speakers in front of you. "
        "An experimental nearfield presentation for comparing stereo recordings."
    ),
    PROFILE_CUSTOM_SOFA: (
        "Loads custom Head-Related Transfer Function measurement files. "
        "Best for: Audiophile personalization."
    ),
}


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
        self.mutation_allowed: Callable[[], bool] = lambda: True
        self.manager = manager
        self.hrtf_dir = Path(hrtf_dir) if hrtf_dir is not None else default_hrtf_dir()
        self.fs = fs
        self._pysofa_available = pysofa_available or _default_pysofa_available
        self._syncing = False
        self.estimated_peak_db = 0.0
        self.estimated_peak_gain_db = 0.0
        self.on_live_controls_changed = None
        self.action_dispatcher = None
        self._applied_state = None

        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(12)

        # 1. Human-Centered Profile Selection
        profile_layout = QHBoxLayout()
        profile_lbl = QLabel("Spatial Profile:")
        profile_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #e0e0e0;")
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(PROFILES)
        profile_layout.addWidget(profile_lbl)
        profile_layout.addWidget(self.profile_combo, 1)
        root_layout.addLayout(profile_layout)

        # 2. Subtle Onboarding & Overview Info Box
        self.info_frame = QFrame()
        self.info_frame.setObjectName("spatialInfoCard")
        self.info_frame.setStyleSheet(
            "QFrame#spatialInfoCard {"
            "  background-color: #1a1d24;"
            "  border: 1px solid #2a3245;"
            "  border-radius: 6px;"
            "  padding: 8px 10px;"
            "}"
        )
        info_layout = QVBoxLayout(self.info_frame)
        info_layout.setContentsMargins(10, 8, 10, 8)
        self.info_desc_label = QLabel()
        self.info_desc_label.setWordWrap(True)
        self.info_desc_label.setStyleSheet("color: #a0abbd; font-size: 12px; line-height: 1.4;")
        self.info_desc_label.setText(PROFILE_DESCRIPTIONS[PROFILE_HOLOSPACE])
        info_layout.addWidget(self.info_desc_label)
        root_layout.addWidget(self.info_frame)

        # 3. Spatial Effect Level (Wet / Dry Slider)
        self.wetdry_group = QWidget()
        wet_layout = QHBoxLayout(self.wetdry_group)
        wet_layout.setContentsMargins(0, 0, 0, 0)
        wet_lbl = QLabel("Spatial level:")
        wet_lbl.setStyleSheet("color: #c0c0c0; font-size: 12px;")
        wet_layout.addWidget(wet_lbl)
        self.wetdry_slider = QSlider(Qt.Orientation.Horizontal)
        self.wetdry_slider.setRange(0, 100)
        self.wetdry_slider.setValue(100)
        wet_layout.addWidget(self.wetdry_slider, 1)
        self.wetdry_label = QLabel("100%")
        self.wetdry_label.setStyleSheet("font-weight: bold; min-width: 40px; text-align: right;")
        wet_layout.addWidget(self.wetdry_label)
        self.wetdry_slider.valueChanged.connect(
            lambda value: self.wetdry_label.setText(f"{value}%")
        )
        root_layout.addWidget(self.wetdry_group)
        self.wetdry_slider.valueChanged.connect(self._on_live_slider_changed)
        self.stereo_expansion_check = QCheckBox("Cinema: expand stereo across the virtual room")
        self.stereo_expansion_check.setChecked(True)
        self.stereo_expansion_check.setToolTip(
            "Enable for stereo movies/music. Disable for native multichannel 7.1 audio. Click Apply."
        )
        root_layout.addWidget(self.stereo_expansion_check)

        # 4. Custom SOFA Container (only shown when Custom SOFA Profile is active)
        self.custom_sofa_container = QGroupBox("Custom SOFA Settings")
        cs_layout = QVBoxLayout(self.custom_sofa_container)

        sofa_row = QHBoxLayout()
        sofa_row.addWidget(QLabel("HRTF profile (SOFA file):"))
        self.sofa_combo = QComboBox()
        sofa_row.addWidget(self.sofa_combo, 1)
        cs_layout.addLayout(sofa_row)

        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("Virtual layout:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(list(LAYOUT_CHANNEL_SPEAKER_MAP.keys()))
        layout_row.addWidget(self.layout_combo)
        layout_row.addStretch(1)
        cs_layout.addLayout(layout_row)

        self.layout_hint_label = QLabel("")
        self.layout_hint_label.setWordWrap(True)
        self.layout_hint_label.setStyleSheet("color: #e09030; font-size: 11px;")
        cs_layout.addWidget(self.layout_hint_label)

        self.custom_sofa_container.setVisible(False)
        root_layout.addWidget(self.custom_sofa_container)

        # 5. Legacy / Backward-Compatibility Crossfeed Controls
        self._legacy_crossfeed_container = QWidget()
        lcf_layout = QHBoxLayout(self._legacy_crossfeed_container)
        lcf_layout.setContentsMargins(0, 0, 0, 0)
        self.crossfeed_check = QCheckBox("Enable Crossfeed")
        self.crossfeed_combo = QComboBox()
        self.crossfeed_combo.addItems(["Bauer", "Meier"])
        self.crossfeed_combo.setEnabled(False)
        lcf_layout.addWidget(self.crossfeed_check)
        lcf_layout.addWidget(self.crossfeed_combo)
        self._legacy_crossfeed_container.setVisible(False)
        root_layout.addWidget(self._legacy_crossfeed_container)

        btn_row = QHBoxLayout()
        self.apply_button = QPushButton("Apply Spatial Audio")
        self.apply_button.clicked.connect(self.apply)
        self.unload_button = QPushButton("Spatial Off")
        self.unload_button.clicked.connect(self.unload)
        btn_row.addWidget(self.apply_button)
        btn_row.addWidget(self.unload_button)
        btn_row.addStretch(1)
        root_layout.addLayout(btn_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root_layout.addWidget(self.status_label)

        root_layout.addStretch(1)

        # Wire signals
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        self.crossfeed_check.toggled.connect(self._on_crossfeed_toggled)
        self.crossfeed_combo.currentTextChanged.connect(lambda _: self._on_crossfeed_mode_changed())
        self.layout_combo.currentTextChanged.connect(lambda _: self._sync_profile_from_controls())
        self.sofa_combo.currentIndexChanged.connect(lambda _: self._on_sofa_changed())

        self.refresh_sofa_files()

        # Set default HoloSpace 3D profile
        self.profile_combo.setCurrentText(PROFILE_HOLOSPACE)
        self._on_profile_changed(PROFILE_HOLOSPACE)

    def _on_profile_changed(self, profile_name: str) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self.info_desc_label.setText(PROFILE_DESCRIPTIONS.get(profile_name, ""))
            if profile_name in (PROFILE_HOLOSPACE, PROFILE_HYBRID):
                self.crossfeed_check.setChecked(False)
                self.layout_combo.setCurrentText("HoloSpace 3D")
                self.sofa_combo.setCurrentIndex(0)
                self.custom_sofa_container.setVisible(False)
                self.layout_hint_label.setText("")
            elif profile_name == PROFILE_CINEMA_71:
                self.crossfeed_check.setChecked(False)
                self.layout_combo.setCurrentText("7.1")
                self.sofa_combo.setCurrentIndex(0)
                self.custom_sofa_container.setVisible(False)
                self.layout_hint_label.setText("")
            elif profile_name == PROFILE_CROSSFEED_BAUER:
                self.crossfeed_check.setChecked(True)
                self.crossfeed_combo.setCurrentText("Bauer")
                self.custom_sofa_container.setVisible(False)
                self.layout_hint_label.setText(
                    "Virtual surround layout disabled while crossfeed is active."
                )
            elif profile_name == PROFILE_CROSSFEED_MEIER:
                self.crossfeed_check.setChecked(True)
                self.crossfeed_combo.setCurrentText("Meier")
                self.custom_sofa_container.setVisible(False)
                self.layout_hint_label.setText(
                    "Virtual surround layout disabled while crossfeed is active."
                )
            elif profile_name == PROFILE_STUDIO_MONITOR:
                self.crossfeed_check.setChecked(False)
                self.layout_combo.setCurrentText("Stereo")
                self.sofa_combo.setCurrentIndex(0)
                self.custom_sofa_container.setVisible(False)
                self.layout_hint_label.setText("")
            elif profile_name == PROFILE_CUSTOM_SOFA:
                self.crossfeed_check.setChecked(False)
                self.custom_sofa_container.setVisible(True)
                self.layout_hint_label.setText("")

            is_cf = self.crossfeed_check.isChecked()
            self.crossfeed_combo.setEnabled(is_cf)
            self.sofa_combo.setEnabled(not is_cf)
            self.layout_combo.setEnabled(not is_cf)
            self.wetdry_slider.setEnabled(True)
            self.stereo_expansion_check.setVisible(profile_name == PROFILE_CINEMA_71)
            self._update_availability()
        finally:
            self._syncing = False

    def _on_crossfeed_toggled(self, checked: bool) -> None:
        self.crossfeed_combo.setEnabled(checked)
        self.sofa_combo.setEnabled(not checked)
        self.layout_combo.setEnabled(not checked)
        self.wetdry_slider.setEnabled(True)
        if checked:
            self.layout_hint_label.setText(
                "Virtual surround layout disabled while crossfeed is active."
            )
        else:
            self.layout_hint_label.setText("")
        self._sync_profile_from_controls()
        self._update_availability()

    def _on_crossfeed_mode_changed(self) -> None:
        self._sync_profile_from_controls()
        self._update_availability()

    def _on_sofa_changed(self) -> None:
        self._sync_profile_from_controls()
        self._update_availability()

    def _sync_profile_from_controls(self) -> None:
        if self._syncing or self.is_hybrid_selected():
            return
        self._syncing = True
        try:
            if self.crossfeed_check.isChecked():
                mode = self.crossfeed_combo.currentText().strip()
                target_profile = (
                    PROFILE_CROSSFEED_MEIER
                    if mode.lower() == "meier"
                    else PROFILE_CROSSFEED_BAUER
                )
                self.custom_sofa_container.setVisible(False)
            else:
                sofa = self.selected_sofa()
                layout = self.selected_layout()
                is_custom_sofa = (
                    sofa is not None
                    and not is_builtin_kemar(sofa)
                    and not is_holospace_default(sofa)
                )
                if is_custom_sofa:
                    target_profile = PROFILE_CUSTOM_SOFA
                    self.custom_sofa_container.setVisible(True)
                elif layout == "HoloSpace 3D":
                    target_profile = PROFILE_HOLOSPACE
                    self.custom_sofa_container.setVisible(False)
                elif layout == "7.1":
                    target_profile = PROFILE_CINEMA_71
                    self.custom_sofa_container.setVisible(False)
                elif layout == "Stereo":
                    target_profile = PROFILE_STUDIO_MONITOR
                    self.custom_sofa_container.setVisible(False)
                else:
                    target_profile = PROFILE_CUSTOM_SOFA
                    self.custom_sofa_container.setVisible(True)

            if self.profile_combo.currentText() != target_profile:
                self.profile_combo.setCurrentText(target_profile)
            self.info_desc_label.setText(PROFILE_DESCRIPTIONS.get(target_profile, ""))
        finally:
            self._syncing = False

    # ---- SOFA discovery ---------------------------------------------------

    def sofa_files(self) -> list[Path]:
        """Sorted ``.sofa`` files in the user HRTF directory."""
        if not self.hrtf_dir.is_dir():
            return []
        return sorted(
            p for p in self.hrtf_dir.glob("*.sofa")
            if not is_builtin_kemar(p) and not is_holospace_default(p)
        )

    def refresh_sofa_files(self) -> None:
        self.sofa_combo.clear()
        # Always add Default KEMAR as first item so dropdown is never empty
        self.sofa_combo.addItem("Synthetic KEMAR model (Built-in)", userData=str(builtin_kemar_path()))
        for path in self.sofa_files():
            self.sofa_combo.addItem(path.name, userData=str(path))
        self._update_availability()

    def refresh_mutation_controls(self) -> None:
        status = self.status_label.text()
        self._update_availability()
        self.status_label.setText(status)

    def _update_availability(self) -> None:
        allowed = self.mutation_allowed()
        self.unload_button.setEnabled(allowed)
        if self.crossfeed_check.isChecked():
            mode = self.crossfeed_combo.currentText()
            self.status_label.setText(f"Crossfeed mode ready ({mode})")
            self.apply_button.setEnabled(allowed)
            return

        selected = self.selected_sofa()
        if selected is None or is_builtin_kemar(selected) or is_holospace_default(selected):
            self.status_label.setText("")
            self.apply_button.setEnabled(allowed)
            return

        if not self._pysofa_available():
            self.status_label.setText(NO_PYSOFA_MESSAGE)
            self.apply_button.setEnabled(False)
        else:
            self.status_label.setText("")
            self.apply_button.setEnabled(allowed)

    # ---- chain rendering / loading ----------------------------------------

    def selected_sofa(self) -> Optional[Path]:
        path = self.sofa_combo.currentData()
        return Path(path) if path else None

    def selected_layout(self) -> str:
        return self.layout_combo.currentText()

    def azimuths(self) -> Sequence[float]:
        return LAYOUT_AZIMUTHS.get(self.selected_layout(), (-30.0, 30.0))

    def wet_gain(self) -> float:
        """Map the spatial level slider to a positive filter-chain gain."""
        wet = self.wetdry_slider.value() / 100.0
        return max(wet, 1e-6)

    def render_chain_args(self, sofa_path: Path) -> str:
        """Extract IRs and render the single-line module args."""
        layout = self.selected_layout()
        channel_map = LAYOUT_CHANNEL_SPEAKER_MAP.get(
            layout, LAYOUT_CHANNEL_SPEAKER_MAP["Stereo"]
        )
        needed_azimuths = sorted(set(az for _, az in channel_map))
        ir_paths = extract_speaker_irs(sofa_path, self.fs, azimuths_deg=needed_azimuths)
        stereo_input = (layout == "HoloSpace 3D" or
                        (self.profile_combo.currentText() == PROFILE_CINEMA_71
                         and self.stereo_expansion_check.isChecked()))
        self.estimated_peak_db = spatial_peak_db(
            ir_paths, layout, self.fs, self.wet_gain(), stereo_input=stereo_input
        )
        speakers = [
            SpeakerIR(
                azimuth=az,
                left_ir=ir_paths[az][0],
                right_ir=ir_paths[az][1],
                channel=ch,
            )
            for ch, az in channel_map
        ]
        channels = ("FL", "FR") if stereo_input else LAYOUT_CHANNELS.get(layout, ("FL", "FR"))
        return SpatialChainRenderer().render_args(
            speakers,
            gain=self.wet_gain(),
            channels=channels,
        )

    def apply(self, internal: bool = False) -> None:
        if not internal and not self.mutation_allowed():
            self.status_label.setText("Wait for Apply to finish before changing Spatial.")
            return
        graph = getattr(self, "graph_controller", None)
        if graph is None:
            self.status_label.setText("Apply failed: audio graph controller unavailable")
            return
        state = self.get_state()
        def backend():
            fs = graph.registry.graph_rate(required=True)
            args, peak, name = prepare_spatial(state, fs)
            graph.switch_spatial(args, peak)
            return fs, peak, name
        def finished(success, message, result):
            if not success:
                self.status_label.setText(f"Apply failed: {message}")
                return
            self.fs, self.estimated_peak_db, _ = result
            self.estimated_peak_gain_db = self.estimated_peak_db
            self._applied_state = dict(state)
            self.status_label.setText("Spatial active · route verified")
        if self.action_dispatcher is not None and not internal:
            self.action_dispatcher("Apply Spatial", backend, finished)
        else:
            try:
                result = backend()
            except Exception as exc:
                finished(False, str(exc), None)
            else:
                finished(True, "", result)

    def unload(self) -> None:
        if not self.mutation_allowed():
            self.status_label.setText("Wait for Apply to finish before changing Spatial.")
            return
        graph = getattr(self, "graph_controller", None)
        if graph is None:
            self.status_label.setText("Spatial Off failed: audio graph controller unavailable")
            return
        def finished(success, message, result):
            if success:
                self._applied_state = None
                self.status_label.setText("Spatial off · route verified")
            else:
                self.status_label.setText(f"Spatial Off failed: {message}")
        if self.action_dispatcher is not None:
            self.action_dispatcher("Spatial Off", graph.spatial_off, finished)
        else:
            try:
                graph.spatial_off()
            except Exception as exc:
                finished(False, str(exc), None)
            else:
                finished(True, "", None)

    def is_hybrid_selected(self) -> bool:
        return self.profile_combo.currentText() == PROFILE_HYBRID

    def hybrid_controls(self) -> dict[str, float]:
        return hybrid_controls(self.wetdry_slider.value()/100)

    def _on_live_slider_changed(self, value) -> None:
        if self._syncing or self._applied_state is None:
            return
        if self.profile_combo.currentText() != self._applied_state.get("profile"):
            return
        if self.on_live_controls_changed is None:
            return
        state = dict(self._applied_state)
        state['wet'] = self.wetdry_slider.value()
        if self.is_hybrid_selected():
            controls = self.hybrid_controls()
        elif not state.get('crossfeed', False):
            count = len(LAYOUT_CHANNEL_SPEAKER_MAP[str(state['layout'])])
            gain = max(state['wet']/100, 1e-6)/count
            controls = {f"mix_{ear}:Gain {index}": gain for ear in ('l','r') for index in range(1, count+1)}
        else:
            from eqspace.core.dsp.crossfeed import CROSSFEED_PRESETS
            feed = 10**(CROSSFEED_PRESETS[str(state['crossfeed_mode']).lower()].feed_db/20)
            controls = {f'mix_{ear}:Gain {branch}': state['wet']/100 * gain
                        for ear in ('l','r') for branch, gain in ((1,1),(2,feed))}
        # Root's callback coalesces and performs preparation/update in a worker.
        # A pure snapshot is available to that callback; no widget access needed.
        self._pending_live_state = state
        self.on_live_controls_changed(controls, self.estimated_peak_gain_db)

    # ---- state export / import ----------------------------------------------

    def get_state(self) -> dict[str, object]:
        sofa = self.selected_sofa()
        return {
            "sofa_path": str(sofa) if sofa else None,
            "layout": self.selected_layout(),
            "wet": self.wetdry_slider.value(),
            "stereo_expansion": self.stereo_expansion_check.isChecked(),
            "crossfeed": self.crossfeed_check.isChecked(),
            "crossfeed_mode": self.crossfeed_combo.currentText(),
            "profile": self.profile_combo.currentText(),
        }

    def set_state(self, state: dict[str, object]) -> None:
        self._syncing = True
        try:
            if "layout" in state:
                idx = self.layout_combo.findText(str(state["layout"]))
                if idx >= 0:
                    self.layout_combo.setCurrentIndex(idx)
            self.stereo_expansion_check.setChecked(bool(state.get("stereo_expansion", False)))
            if "wet" in state:
                self.wetdry_slider.setValue(int(state["wet"]))
            if "crossfeed_mode" in state:
                idx = self.crossfeed_combo.findText(
                    str(state["crossfeed_mode"]), Qt.MatchFlag.MatchFixedString
                )
                if idx >= 0:
                    self.crossfeed_combo.setCurrentIndex(idx)
            elif "algorithm" in state:
                idx = self.crossfeed_combo.findText(
                    str(state["algorithm"]).capitalize(), Qt.MatchFlag.MatchFixedString
                )
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
        finally:
            self._syncing = False

        if "profile" in state:
            profile = str(state["profile"])
            if profile in ("HS+", "HoloSpace + Meier (Experimental)", "HaloSpace + Meier (Experimental)"):
                profile = PROFILE_HYBRID
            if profile in PROFILES:
                self.profile_combo.setCurrentText(profile)
                self._on_profile_changed(profile)
            else:
                self._sync_profile_from_controls()
        else:
            self._sync_profile_from_controls()
        self._update_availability()
