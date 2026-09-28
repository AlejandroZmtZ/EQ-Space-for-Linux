"""Main window: tab shell (Mixer | Parametric EQ | Presets | Spatial | Mic).

Wiring done here:

- Presets tab: Apply loads the EQ chain, then routes system audio on success.
- PEQ tab: ``save_profile_requested`` opens a small name dialog and saves
  via :mod:`eqspace.core.profiles.storage`.
- Spatial / Mic tabs get a :class:`~eqspace.ui.module_args_manager.ModuleArgsManager`
  each, so their pre-rendered module-args strings can be loaded.
- Startup: the last saved profile is loaded into the editor. Applying it
  requires an explicit action; a missing profile is tolerated.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot, QThreadPool, QUrl, QSize
from PySide6.QtGui import QCloseEvent, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QApplication,
    QProgressBar,
)

from eqspace.core.dsp.filter_design import EQBand
from eqspace.core.filterchain.manager import FilterChainManager, FilterSpec
from eqspace.core.filterchain.limiter import detect_limiter_with_reason, lv2_host_directory
from eqspace.core.pipewire import control as _control
from eqspace.core.pipewire.registry import PipeWireRegistry, PipeWireUnavailable
from eqspace.core.profiles import storage
from eqspace.core.profiles.models import EQProfile
from eqspace.ui.mic import MicWidget
from eqspace.ui.audio_graph import AudioGraphController
from eqspace.ui.mixer import MixerWidget
from eqspace.ui.module_args_manager import ModuleArgsManager
from eqspace.ui.peq import PeqWidget
from eqspace.ui.peq.peq_widget import _BAND_TYPE_LABELS
from eqspace.ui.presets import PresetsWidget
from eqspace.ui.profile_state import load_last_profile, save_last_profile
from eqspace.ui.spatial import SpatialWidget
from eqspace.ui.theme import DARK_STYLESHEET
from eqspace.ui.tray import SystemTrayManager

logger = logging.getLogger(__name__)


def _placeholder(text: str) -> QWidget:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


class MainWindow(QMainWindow):
    _graph_result = Signal(bool, str, object)
    _curve_requested = Signal()
    _graph_phase = Signal(str)

    def __init__(
        self,
        registry: Optional[PipeWireRegistry] = None,
        filter_manager: Optional[FilterChainManager] = None,
        poll_interval_ms: int = 1000,
        restore_profile: bool = True,
    ) -> None:
        super().__init__()
        self.setWindowTitle("EQ-Space")
        self.setStyleSheet(DARK_STYLESHEET)
        self._profile_apply_busy = False
        self._automatic_headroom_enabled = True
        self._applied_eq_profile: EQProfile | None = None
        self._editor_revision = 0
        self._syncing_editor = False
        self._graph_worker = None
        self._last_graph_verified = False
        self._graph_callback = None
        self._graph_result.connect(self._finish_graph_action)

        self.tabs = QTabWidget()
        active_registry = registry or PipeWireRegistry(timeout=2.0)
        self.mixer = MixerWidget(
            registry=active_registry,
            poll_interval_ms=poll_interval_ms,
        )
        self.peq = PeqWidget(manager=filter_manager or FilterChainManager(
            node_name=f"eqspace.filter-chain.{uuid.uuid4().hex[:12]}"),
                             fs=active_registry.graph_rate() if hasattr(active_registry, "graph_rate") else 48000.0)
        self._curve_requested.connect(self.peq.update_curve)
        self.presets = PresetsWidget()
        self.limiter_capability, self.limiter_unavailable_reason = detect_limiter_with_reason()
        if self.limiter_capability and lv2_host_directory() is None:
            self.limiter_capability = None
            self.limiter_unavailable_reason = (
                "PipeWire LV2 host is missing. Install the matching LV2 host; see docs/lv2-host.md."
            )
        if self.limiter_capability:
            self.peq.limiter_check.setToolTip(
                "Independent LSP stage. Adds at least 5 ms lookahead; oversampling may add latency."
            )
        else:
            self.peq.limiter_check.setToolTip(self.limiter_unavailable_reason or "LSP limiter unavailable")
            self.peq.limiter_latency_label.setText(self.limiter_unavailable_reason or "Limiter unavailable")
        self.peq.limiter_check.toggled.connect(self._on_limiter_toggled)
        self.spatial = SpatialWidget(manager=ModuleArgsManager())
        self.audio_graph = AudioGraphController(
            active_registry, lambda: self.mixer.selected_output_name, lambda: self.peq.manager
        )
        self.audio_graph.on_progress = self._graph_phase.emit
        self._graph_phase.connect(self._show_graph_phase)
        self._sync_limiter_ui()
        self.spatial.graph_controller = self.audio_graph
        self.audio_graph.on_spatial_changed = self._on_spatial_changed
        self.audio_graph.on_eq_enabling = lambda: self._on_spatial_changed(self.audio_graph.spatial_peak_db)
        self.mixer.mutation_allowed = self._graph_mutation_allowed
        self.spatial.mutation_allowed = self._graph_mutation_allowed
        self.presets.current_spatial_enabled = lambda: self.audio_graph.spatial_name is not None
        self.presets.current_spatial_state = self.spatial.get_state
        self.presets.current_limiter_enabled = lambda: self.audio_graph.limiter_name is not None
        if type(self.peq.manager) is FilterChainManager:
            self.mixer.graph_controller = self.audio_graph
        self.mic = MicWidget(manager=ModuleArgsManager())

        self.peq.save_profile_requested.connect(self._on_save_profile_requested)
        self.peq.apply_completed.connect(self._on_peq_apply_completed)
        self.peq.apply_button.clicked.disconnect()
        self.peq.apply_button.clicked.connect(self._on_manual_peq_apply)
        self._live_eq_pending = False
        self._live_eq_timer = QTimer(self)
        self._live_eq_timer.setSingleShot(True)
        self._live_eq_timer.setInterval(50)
        self._live_eq_timer.timeout.connect(self._flush_live_eq)
        self.peq.settings_edited.connect(self._schedule_live_eq)
        self.peq.preamp_spin.valueChanged.connect(self._schedule_live_eq)
        self.presets.preset_apply_requested.connect(self._on_preset_apply_requested)
        self.mixer.routing_changed.connect(self._on_routing_changed)
        self.mic.open_peq_requested.connect(lambda: self.tabs.setCurrentWidget(self.peq))

        self.tabs.addTab(self.mixer, "Mixer")
        self.tabs.addTab(self.peq, "Parametric EQ")
        self.tabs.addTab(self.presets, "Presets")
        self.tabs.addTab(self.spatial, "Spatial (Experimental)")
        self.tabs.addTab(self.mic, "Mic (Experimental)")

        self.tabs.setTabToolTip(
            0, "Live per-app volume sliders, master output, and system-wide audio routing"
        )
        self.tabs.setTabToolTip(
            1, "10-band parametric equalizer with interactive frequency response canvas"
        )
        self.tabs.setTabToolTip(
            2, "Curated Harman target curves, headphone profiles, and acoustic presets"
        )
        self.tabs.setTabToolTip(
            3, "Experimental headphone crossfeed and synthetic spatial model; external SOFA files optional"
        )
        self.tabs.setTabToolTip(
            4, "Experimental microphone noise reduction via optional DeepFilterNet plugin"
        )

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(8, 8, 8, 8)
        central_layout.setSpacing(6)

        self.brand_header = self._create_brand_header()
        central_layout.addWidget(self.brand_header)
        self.quick_start_banner = self._create_quick_start_banner()
        central_layout.addWidget(self.quick_start_banner)
        central_layout.addWidget(self.tabs, stretch=1)
        self.setCentralWidget(central)

        # Menu bar Help action
        help_menu = self.menuBar().addMenu("&Help")
        self.quick_start_action = help_menu.addAction("Help / Quick-Start")
        self.quick_start_action.setToolTip("Toggle Quick-Start Guide banner")
        self.quick_start_action.triggered.connect(self._toggle_quick_start_banner)
        self.help_action = self.quick_start_action
        help_menu.addAction("Open logs", self._open_logs)
        help_menu.addAction("Copy diagnostic report", self._copy_diagnostics)

        # Status bar Help button
        status_bar = self.statusBar()
        self.help_button = QPushButton("Help / Quick-Start")
        self.help_button.setObjectName("statusBarHelpButton")
        self.help_button.setToolTip("Toggle Quick-Start Guide banner")
        self.help_button.clicked.connect(self._toggle_quick_start_banner)
        status_bar.addPermanentWidget(self.help_button)
        self.loading_indicator = QProgressBar()
        self.loading_indicator.setRange(0, 0)
        self.loading_indicator.setMaximumWidth(100)
        self.loading_indicator.hide()
        status_bar.addPermanentWidget(self.loading_indicator)
        self._loading_timer = QTimer(self)
        self._loading_timer.setSingleShot(True)
        self._loading_timer.setInterval(150)
        self._loading_timer.timeout.connect(self.loading_indicator.show)

        self.resize(1000, 650)

        self.tray_manager = SystemTrayManager(parent=self)
        self.tray_manager.profile_selected.connect(self._on_tray_profile_selected)

        self.tray_manager.show_hide_triggered.connect(self._toggle_visible)
        self.tray_manager.quit_requested.connect(self.close_completely)
        if hasattr(self.presets, "set_state_supplier"):
            self.presets.set_state_supplier(self._editor_profile)
            self.presets.profile_saved.connect(self._profile_saved)
            self.presets.profile_renamed.connect(self._profile_renamed)
            self.presets.profile_deleted.connect(self._profile_deleted)
        self.spatial.action_dispatcher = self._dispatch_graph
        self._live_spatial_pending = None
        self._live_spatial_timer = QTimer(self)
        self._live_spatial_timer.setSingleShot(True)
        self._live_spatial_timer.setInterval(50)
        self._live_spatial_timer.timeout.connect(self._flush_live_spatial)
        self.spatial.on_live_controls_changed = self._schedule_live_spatial
        self.mixer.action_dispatcher = self._dispatch_graph if type(self.peq.manager) is FilterChainManager else None
        self.audio_graph.atomic_transitions = type(self.peq.manager) is FilterChainManager
        self.mixer.eq_enable_requested = self._on_manual_peq_apply
        from eqspace.diagnostics import log_location
        _, logging_error = log_location()
        if logging_error:
            self.statusBar().showMessage(logging_error)
        if self._is_tray_available():
            self.tray_manager.show()

        if restore_profile:
            self.restore_last_profile()

    def _create_brand_header(self) -> QFrame:
        """Show the full product logo in the app content area."""
        header = QFrame()
        header.setObjectName("appBrandHeader")
        header.setFixedHeight(84)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 4, 12, 4)
        layout.setSpacing(12)

        logo_path = Path(__file__).resolve().parents[1] / "data" / "icons" / "eqspace.svg"
        self.brand_logo = QLabel()
        self.brand_logo.setObjectName("appBrandLogo")
        self.brand_logo.setFixedSize(QSize(72, 72))
        self.brand_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if logo_path.is_file():
            self.brand_logo.setPixmap(QIcon(str(logo_path)).pixmap(QSize(72, 72)))
        self.brand_logo.setAccessibleName("EQ-Space logo")
        layout.addWidget(self.brand_logo)

        title = QLabel("EQ-Space")
        title.setObjectName("appBrandTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(title)
        layout.addStretch(1)
        return header

    def _create_quick_start_banner(self) -> QFrame:
        """Create the collapsible Quick-Start Onboarding banner."""
        banner = QFrame()
        banner.setObjectName("quickStartBanner")

        layout = QVBoxLayout(banner)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Header row with title and dismiss button
        header_row = QHBoxLayout()
        self.quick_start_header = QLabel("🚀 Quick-Start Guide")
        self.quick_start_header.setObjectName("quickStartHeader")
        header_row.addWidget(self.quick_start_header)
        header_row.addStretch(1)

        self.dismiss_button = QPushButton("✕")
        self.dismiss_button.setObjectName("quickStartDismissButton")
        self.dismiss_button.setToolTip(
            "Dismiss guide (reopen anytime via Help / Quick-Start)"
        )
        self.dismiss_button.setFixedSize(24, 24)
        self.dismiss_button.clicked.connect(self.hide_quick_start_banner)
        self.banner_close_button = self.dismiss_button
        header_row.addWidget(self.dismiss_button)
        layout.addLayout(header_row)

        # 3 clear steps row
        steps_row = QHBoxLayout()
        steps_row.setSpacing(12)

        steps_data = [
            (
                "1. Play Audio",
                "Start playback in your media player or browser.",
            ),
            (
                "2. Choose Output",
                "Choose your headphones or speakers in Mixer.",
            ),
            (
                "3. Enhance Sound",
                "Select a preset and click Apply to turn on EQ. Use Mixer to return to direct output.",
            ),
        ]

        for title, desc in steps_data:
            card = QFrame()
            card.setObjectName("quickStartStepCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 6, 8, 6)
            card_layout.setSpacing(3)

            title_label = QLabel(title)
            title_label.setObjectName("quickStartStepTitle")
            desc_label = QLabel(desc)
            desc_label.setObjectName("quickStartStepDesc")
            desc_label.setWordWrap(True)

            card_layout.addWidget(title_label)
            card_layout.addWidget(desc_label)
            steps_row.addWidget(card, stretch=1)

        layout.addLayout(steps_row)
        return banner

    def toggle_quick_start_banner(self) -> None:
        """Toggle visibility of the quick-start onboarding banner."""
        if self.quick_start_banner.isHidden():
            self.quick_start_banner.show()
        else:
            self.quick_start_banner.hide()

    def show_quick_start_banner(self) -> None:
        """Show the quick-start onboarding banner."""
        self.quick_start_banner.show()

    def hide_quick_start_banner(self) -> None:
        """Hide the quick-start onboarding banner."""
        self.quick_start_banner.hide()

    def _toggle_quick_start_banner(self) -> None:
        self.toggle_quick_start_banner()

    # ---- profile handling ---------------------------------------------------

    def apply_profile(
        self,
        profile: EQProfile,
        async_mode: bool = False,
        on_done: Optional[Callable[[bool, str], None]] = None,
    ) -> bool:
        """Load a profile into the PEQ model and push it to the manager."""
        if self._profile_apply_busy or self.peq._apply_worker is not None:
            if on_done:
                on_done(False, "another apply is in progress")
            return False
        if async_mode and type(self.peq.manager) is FilterChainManager:
            return self._apply_profile_background(profile, on_done)
        if hasattr(self.mixer.registry, "graph_rate"):
            try:
                self.peq.fs = self.mixer.registry.graph_rate(required=True)
            except PipeWireUnavailable as exc:
                message = str(exc)
                self.peq.status_label.setText(f"Apply failed: {message}")
                if on_done:
                    on_done(False, message)
                return False
        old_bands = list(self.peq.bands)
        old_preamp = self.peq.preamp_db
        old_trim = self.peq.auto_trim_db
        old_auto_enabled = self._automatic_headroom_enabled
        old_applied_profile = self._applied_eq_profile
        old_limiter_enabled = bool(self.audio_graph.limiter_name)
        old_eq_enabled = self.audio_graph.eq_enabled
        old_specs = self.peq._last_good_specs
        previously_routed = self.mixer.is_routed()
        next_bands = [band.to_eqband() for band in profile.bands]
        try:
            from eqspace.core.dsp.filter_design import design_filters
            design_filters(next_bands, self.peq.fs)
        except ValueError as exc:
            if on_done:
                on_done(False, str(exc))
            return False
        next_layout = [("preamp", "linear")] + [
            (f"band_{i}", _BAND_TYPE_LABELS[band.band_type])
            for i, band in enumerate(next_bands) if band.enabled]
        active_specs = old_specs or getattr(self.peq.manager, "_active_filters", None)
        old_layout = [(spec.name, spec.filter_type) for spec in active_specs] if active_specs else []
        replacing = bool(self.peq.manager.is_loaded and old_layout != next_layout)
        old_manager = self.peq.manager
        staged = replacing and type(old_manager) is FilterChainManager
        if staged:
            self.peq.manager = FilterChainManager(
                node_name=f"eqspace.filter-chain.{uuid.uuid4().hex[:12]}",
                description="EQ-Space Filter Chain",
                runner=old_manager._runner,
                popen=old_manager._popen,
            )
        if replacing and previously_routed and not staged:
            try:
                _control.set_system_routing(False,
                    fallback_sink_name=self.mixer.selected_output_name,
                    registry=self.mixer.registry)
                if self.mixer.is_routed():
                    raise RuntimeError("direct output could not be verified")
                selected = self.mixer.selected_output_name
                current = self.mixer._default_physical_name()
                if selected and current and current != selected:
                    raise RuntimeError("selected physical output could not be verified")
            except Exception as exc:
                if on_done:
                    on_done(False, f"could not switch to direct output: {exc}")
                return False
        result = {"success": False}
        self._set_apply_busy(True)
        self.peq.bands = next_bands
        self.peq.set_preamp(profile.preamp_db)
        self.peq.auto_trim_db = 0.0
        if profile.automatic_headroom:
            from eqspace.core.dsp.headroom import automatic_trim_db
            self.peq.auto_trim_db = automatic_trim_db(next_bands, self.peq.fs,
                                                       profile.preamp_db, self.peq.spatial_peak_db)
        self.peq._refresh_table()
        self.peq.update_curve()

        def _after_apply(success: bool, msg: str) -> None:
            if type(old_manager) is FilterChainManager:
                if success:
                    try:
                        self._automatic_headroom_enabled = profile.automatic_headroom
                        self._applied_eq_profile = profile.model_copy(deep=True)
                        self.audio_graph.eq_applied()
                        self._change_limiter(profile.limiter_enabled)
                        self._sync_limiter_ui()
                        if profile.spatial_enabled is False:
                            self.audio_graph.spatial_off()
                        elif profile.spatial_enabled is True:
                            if profile.spatial:
                                self.spatial.set_state(profile.spatial)
                            self.spatial.apply(internal=True)
                            if "active" not in self.spatial.status_label.text().lower():
                                raise RuntimeError(self.spatial.status_label.text())
                        if profile.mic:
                            self.mic.set_state(profile.mic)
                    except Exception as exc:
                        success = False
                        msg = f"routing failed: {exc}"
                if success and staged:
                    try:
                        old_manager.unload()
                    except Exception:
                        self.audio_graph.retain_until_shutdown(old_manager)
                        logger.exception("previous EQ module cleanup failed after verified switch")
                if not success:
                    self._automatic_headroom_enabled = old_auto_enabled
                    self._applied_eq_profile = old_applied_profile
                    candidate_to_retire = None
                    try:
                        if staged:
                            candidate_to_retire = self.peq.manager
                            self.peq.manager = old_manager
                        if old_specs and old_manager.is_loaded:
                            # Old controls may have relied on a limiter that the
                            # attempted profile removed. Publish conservative
                            # controls before reconnecting this previous owner.
                            restored_specs, restored_trim = self._headroom_specs(
                                old_specs, old_applied_profile,
                                self.peq.spatial_peak_db, bool(self.audio_graph.limiter_name),
                            )
                            old_manager.reload(restored_specs)
                            old_manager.verify_controls(restored_specs)
                            self.peq._last_good_specs = restored_specs
                            self.peq.auto_trim_db = restored_trim
                            self.audio_graph.eq_applied()
                            self._change_limiter(old_limiter_enabled)
                            if not old_eq_enabled:
                                self.audio_graph.eq_off()
                        else:
                            self.audio_graph.eq_off()
                            if self.audio_graph.limiter_name and not old_limiter_enabled:
                                self._change_limiter(False)
                            if old_manager.is_loaded:
                                old_manager.unload()
                            self.audio_graph.eq_enabled = False
                            self.peq._last_good_specs = None
                        # Retire the staged graph only after the old path or
                        # direct physical path has been verified above.
                        if candidate_to_retire and candidate_to_retire.is_loaded:
                            candidate_to_retire.unload()
                    except Exception as exc:
                        if candidate_to_retire and candidate_to_retire.is_loaded:
                            self.audio_graph.retain_until_shutdown(candidate_to_retire)
                        msg += f"; previous path unverified: {exc}"
                        self.peq._last_good_specs = None
                    self.peq.bands = old_bands
                    self.peq.set_preamp(old_preamp)
                    if self.peq._last_good_specs is None:
                        self.peq.auto_trim_db = old_trim
                    self.peq._refresh_table()
                    self.peq.update_curve()
                    self.peq.status_label.setText(f"Apply failed: {self.peq._brief_error(msg)}")
                    self.presets.set_eq_enabled(False)
                    self._sync_limiter_ui()
                else:
                    self.mixer.refresh()
                    self.peq.status_label.setText(f"Profile “{profile.name}” applied")
                self._set_apply_busy(False)
                result["success"] = success
                if on_done:
                    on_done(success, msg if success else self.peq._brief_error(msg))
                if getattr(self, "_close_when_done", False):
                    self._close_when_done = False
                    self.close()
                return
            if success:
                try:
                    if hasattr(self.mixer, "is_routed") and not self.mixer.is_routed():
                        _control.set_system_routing(
                            True,
                            fallback_sink_name=self.mixer.selected_output_name,
                            registry=self.mixer.registry,
                        )
                    self.mixer.refresh()
                    if not self.mixer.is_routed():
                        raise RuntimeError("EQ route could not be verified")
                except Exception as exc:
                    success = False
                    msg = f"routing failed: {exc}"
                    try:
                        if not old_specs and self.peq.manager.is_loaded:
                            self.peq.manager.unload()
                    except Exception as rollback_exc:
                        logger.exception("could not restore previous audio path")
                        msg += f"; rollback failed: {rollback_exc}"
                if success:
                    if profile.spatial:
                        self.spatial.set_state(profile.spatial)
                    if profile.mic:
                        self.mic.set_state(profile.mic)
                    self.peq.status_label.setText(f"Profile “{profile.name}” applied")
            if not success:
                restored = False
                if old_specs and "unverified" not in msg.lower():
                    try:
                        if self.peq.manager.is_loaded:
                            self.peq.manager.reload(old_specs)
                        else:
                            self.peq.manager.load(old_specs)
                        if hasattr(self.peq.manager, "verify_controls"):
                            self.peq.manager.verify_controls(old_specs)
                        self.peq._last_good_specs = old_specs
                        restored = True
                    except Exception as restore_exc:
                        logger.exception("previous EQ chain could not be restored")
                        msg += f"; previous chain unverified: {restore_exc}"
                if restored and previously_routed:
                    try:
                        _control.set_system_routing(True, fallback_sink_name=self.mixer.selected_output_name,
                                                    registry=self.mixer.registry)
                        if not self.mixer.is_routed():
                            raise RuntimeError("previous EQ route could not be verified")
                    except Exception as restore_exc:
                        msg += f"; previous route unverified: {restore_exc}"
                        restored = False
                if not restored or not previously_routed or "unverified" in msg.lower():
                    try:
                        _control.set_system_routing(False, fallback_sink_name=self.mixer.selected_output_name,
                                                    registry=self.mixer.registry)
                    except Exception:
                        logger.exception("could not restore direct audio route")
                if not restored and not self.mixer.is_routed() and self.peq.manager.is_loaded:
                    try:
                        self.peq.manager.unload()
                    except Exception:
                        logger.exception("could not unload unverified EQ chain")
                self.peq._last_good_specs = old_specs if restored else None
                self.presets.set_eq_enabled(restored and previously_routed and self.mixer.is_routed())
                self.peq.bands = old_bands
                self.peq.set_preamp(old_preamp)
                self.peq._refresh_table()
                self.peq.update_curve()
                self.peq.status_label.setText(f"Apply failed: {self.peq._brief_error(msg)}")
            self._set_apply_busy(False)
            result["success"] = success
            if on_done:
                on_done(success, msg if success else self.peq._brief_error(msg))
            if getattr(self, "_close_when_done", False):
                self._close_when_done = False
                self.close()

        started = self.peq.apply(async_mode=async_mode, on_done=_after_apply)
        if not started and self._profile_apply_busy:
            self._set_apply_busy(False)
            return False

        return started if async_mode else result["success"]

    def _apply_profile_background(self, profile, on_done=None):
        """Prepare a complete target in a worker, publish editor state after commit."""
        profile = profile.model_copy(deep=True)
        graph = self.audio_graph
        old_manager = self.peq.manager
        old_specs = self.peq._last_good_specs
        old_profile = self._applied_eq_profile
        eq_only = profile.scope == "eq"
        eq_enabled = True if eq_only else profile.eq_enabled
        spatial_state = dict(self.spatial._applied_state or self.spatial.get_state())
        spatial_enabled = bool(graph.spatial_name)
        if not eq_only and profile.spatial_enabled is not None:
            spatial_enabled = profile.spatial_enabled
            if profile.spatial:
                spatial_state = dict(profile.spatial)
        limiter_enabled = bool(graph.limiter_name) if eq_only else profile.limiter_enabled
        applied_profile = profile.model_copy(update={"limiter_enabled": limiter_enabled})
        editor_revision = self._editor_revision

        def backend():
            from eqspace.core.dsp.filter_design import design_filters
            from eqspace.core.dsp.headroom import automatic_trim_db
            from eqspace.core.dsp.spatial import prepare_spatial
            rate = self.mixer.registry.graph_rate(required=True)
            logger.info("Profile target name=%r EQ=%s Spatial=%s LSP=%s rate=%s scope=%s",
                        profile.name, eq_enabled, spatial_enabled, limiter_enabled, rate, profile.scope)
            bands = profile.to_bands()
            design_filters(bands, rate)
            self._graph_phase.emit("Preparing Spatial…" if spatial_enabled else "Preparing EQ…")
            args, peak, spatial_name = prepare_spatial(spatial_state, rate) if spatial_enabled else (None, 0.0, "Off")
            # The candidate EQ is isolated; it never processes the old Spatial branch.
            trim = automatic_trim_db(bands, rate, profile.preamp_db, peak,
                                     limiter_enabled=limiter_enabled) if profile.automatic_headroom else 0.0
            specs = [FilterSpec("preamp", "linear", {"Mult": 10 ** ((profile.preamp_db + trim)/20), "Add": 0.0})]
            specs.extend(FilterSpec(f"band_{i}", _BAND_TYPE_LABELS[b.band_type],
                         {"Freq": b.freq_hz, "Gain": b.gain_db, "Q": b.q})
                         for i, b in enumerate(bands) if b.enabled)
            compatible = bool(eq_only and old_manager.is_loaded and old_specs and
                              [(s.name,s.filter_type) for s in specs] == [(s.name,s.filter_type) for s in old_specs])
            candidate = old_manager
            if eq_enabled:
                if compatible:
                    candidate.reload(specs)
                else:
                    candidate = FilterChainManager(node_name=f"eqspace.filter-chain.{uuid.uuid4().hex[:12]}",
                                                   runner=old_manager._runner, popen=old_manager._popen)
                    try:
                        self._graph_phase.emit("Loading EQ…")
                        candidate.load(specs)
                        candidate.verify_controls(specs)
                    except Exception:
                        # Preparation never linked this isolated candidate.
                        if candidate.is_loaded:
                            try:
                                candidate.unload()
                            except Exception:
                                graph.retain_until_shutdown(candidate)
                        raise
            try:
                # Compatible EQ-only changes retain the current sink and route.
                if not (compatible and graph.eq_enabled):
                    graph.configure_playback(eq_enabled=eq_enabled, spatial_args=args,
                        spatial_peak_db=peak, limiter_enabled=limiter_enabled,
                        limiter_capability=self.limiter_capability,
                        eq_candidate=candidate if eq_enabled else None)
                elif not graph.is_path_verified():
                    raise RuntimeError("Updated EQ controls, but playback route is unverified")
            except Exception:
                if compatible and old_specs:
                    candidate.reload(old_specs)
                elif candidate is not old_manager and candidate.is_loaded:
                    # Controller retains potentially active candidates if rollback is unverified.
                    if graph.is_path_verified() and graph._eq_name() != candidate.node_name:
                        candidate.unload()
                    else:
                        graph.retain_until_shutdown(candidate)
                raise
            return candidate, specs if eq_enabled else None, trim, rate, peak, spatial_state, spatial_enabled

        def finished(success, message, result):
            if success:
                manager, specs, trim, rate, peak, state, spatial_on = result
                self._syncing_editor = True
                try:
                    if self._editor_revision == editor_revision:
                        if profile.to_bands() != self.peq.bands:
                            self.peq.bands = profile.to_bands()
                            self.peq._refresh_table()
                        self.peq.set_preamp(profile.preamp_db)
                finally:
                    self._syncing_editor = False
                self.peq.manager = manager
                self.peq._last_good_specs = specs
                self.peq.fs = rate
                self.peq.auto_trim_db = trim
                self.peq.spatial_peak_db = peak
                self._applied_eq_profile = applied_profile if eq_enabled else old_profile
                self._automatic_headroom_enabled = profile.automatic_headroom
                if self._editor_revision != editor_revision and eq_enabled:
                    self._live_eq_pending = True
                if not eq_only:
                    self.spatial.set_state(state)
                self.spatial._applied_state = dict(state) if spatial_on else None
                self.spatial.estimated_peak_db = peak
                self.spatial.estimated_peak_gain_db = peak
                self.peq.status_label.setText(f"Profile “{profile.name}” applied")
                self.presets.set_eq_enabled(eq_enabled)
                self.peq.update_curve()
                self._sync_limiter_ui()
                self.mixer.refresh()
            else:
                self.peq.status_label.setText(f"Apply failed: {message}")
                self.presets.clear_active()
            if on_done:
                on_done(success, message)
            if success:
                self.presets.set_eq_enabled(eq_enabled)
        return self._dispatch_graph("Preparing playback → connecting → verifying…", backend, finished)

    def restore_last_profile(self) -> None:
        """Load the last saved profile into the editor without changing audio."""
        name = load_last_profile()
        if not name:
            return
        try:
            profile = storage.load_profile(name)
        except FileNotFoundError:
            logger.info("last profile %r not found; skipping restore", name)
            return
        except Exception:
            logger.exception("failed to restore profile %r", name)
            return
        self.peq.bands = [band.to_eqband() for band in profile.bands]
        self._automatic_headroom_enabled = profile.automatic_headroom
        self.peq.set_preamp(profile.preamp_db)
        self.peq._refresh_table()
        self.peq.update_curve()
        if profile.spatial:
            self.spatial.set_state(profile.spatial)
        if profile.mic:
            self.mic.set_state(profile.mic)
        self.peq.status_label.setText(f"Profile “{name}” loaded — click Apply to enable EQ")

    def _on_preset_apply_requested(self, profile: EQProfile) -> None:
        self._live_eq_timer.stop()
        self._live_eq_pending = False
        if profile.scope == "eq":
            profile = profile.model_copy(update={
                "limiter_enabled": self.audio_graph.limiter_name is not None,
            })
        self.apply_profile(
            profile,
            async_mode=True,
            on_done=self.presets.set_apply_result,
        )

    def _on_manual_peq_apply(self) -> None:
        self._live_eq_timer.stop()
        self._live_eq_pending = False
        profile = EQProfile.from_bands("Current EQ", list(self.peq.bands),
                                       preamp_db=self.peq.preamp_db,
                                       spatial_enabled=None,
                                       scope="eq",
                                       automatic_headroom=self._automatic_headroom_enabled,
                                       limiter_enabled=self.audio_graph.limiter_name is not None)
        self.apply_profile(profile, async_mode=True,
                           on_done=lambda success, _: self.presets.clear_active() if success else None)

    def _schedule_live_eq(self, *_args) -> None:
        if self._syncing_editor:
            return
        self._editor_revision += 1
        if self.peq.peak_label.text() == "Response unavailable":
            self._live_eq_pending = False
            self._live_eq_timer.stop()
            return
        if not self.peq.manager.is_loaded or not self.audio_graph.eq_enabled:
            return
        self._live_eq_pending = True
        if not self._live_eq_timer.isActive():
            self._live_eq_timer.start()
        if self._graph_mutation_allowed():
            self.peq.status_label.setText("Updating sound…")

    def _flush_live_eq(self) -> None:
        if not self._live_eq_pending:
            return
        if not self._graph_mutation_allowed():
            self._live_eq_timer.start()
            return
        self._live_eq_pending = False
        if self.peq.manager.is_loaded and self.audio_graph.eq_enabled:
            self._apply_live_eq()

    def _apply_live_eq(self):
        """Snapshot the editor once, then update compatible controls off the UI thread."""
        profile = EQProfile.from_bands(
            "Current EQ", list(self.peq.bands), preamp_db=self.peq.preamp_db,
            spatial_enabled=None, scope="eq", automatic_headroom=self._automatic_headroom_enabled,
            limiter_enabled=bool(self.audio_graph.limiter_name))
        manager = self.peq.manager
        old_specs = self.peq._last_good_specs
        old_trim = self.peq.auto_trim_db
        revision = getattr(self, "_live_revision", 0) + 1
        self._live_revision = revision
        # Topology changes use the staged profile path.
        desired = [("preamp", "linear")] + [(f"band_{i}", _BAND_TYPE_LABELS[b.band_type])
                     for i, b in enumerate(profile.to_bands()) if b.enabled]
        if not old_specs or desired != [(s.name, s.filter_type) for s in old_specs]:
            self.apply_profile(profile, async_mode=True)
            return

        def update():
            from eqspace.core.dsp.filter_design import design_filters
            from eqspace.core.dsp.headroom import automatic_trim_db
            rate = self.mixer.registry.graph_rate(required=True)
            bands = profile.to_bands()
            design_filters(bands, rate)
            trim = automatic_trim_db(bands, rate, profile.preamp_db, self.audio_graph.spatial_peak_db,
                                     limiter_enabled=bool(self.audio_graph.limiter_name)) if profile.automatic_headroom else 0.0
            specs = [FilterSpec("preamp", "linear", {"Mult": 10 ** ((profile.preamp_db + trim)/20), "Add": 0.0})]
            specs.extend(FilterSpec(f"band_{i}", _BAND_TYPE_LABELS[b.band_type],
                         {"Freq": b.freq_hz, "Gain": b.gain_db, "Q": b.q})
                         for i, b in enumerate(bands) if b.enabled)
            manager.reload(specs)
            # reload performs a verified readback; no route or duplicate Props scan for a control-only edit.
            return specs, trim, rate

        def finished(success, message, result):
            if success:
                specs, trim, rate = result
                self.peq._last_good_specs = specs
                self.peq.auto_trim_db = trim
                self.peq.fs = rate
                self._applied_eq_profile = profile
                self.presets.clear_active()
            else:
                self.peq.auto_trim_db = old_trim
            if not self._live_eq_pending:
                self.peq.status_label.setText("Sound updated" if success else f"Update failed: {message}")

        self._dispatch_graph("Updating sound…", update, finished, verify_route=False)

    def _schedule_live_spatial(self, controls, old_peak):
        self._live_spatial_pending = (dict(controls), dict(self.spatial._pending_live_state))
        if not self._live_spatial_timer.isActive():
            self._live_spatial_timer.start()

    def _flush_live_spatial(self):
        if self._live_spatial_pending is None:
            return
        if not self._graph_mutation_allowed():
            self._live_spatial_timer.start()
            return
        controls, state = self._live_spatial_pending
        self._live_spatial_pending = None
        graph = self.audio_graph
        owner = graph.spatial_manager
        if owner is None or self.spatial._applied_state is None:
            return
        if state.get("profile") != self.spatial._applied_state.get("profile"):
            return

        def update():
            from eqspace.core.dsp.spatial import prepare_spatial
            rate = graph.registry.graph_rate(required=True)
            args, peak, name = prepare_spatial(state, rate)
            previous_peak = graph.spatial_peak_db
            reserve = max(peak, previous_peak)
            # Reserve before raising any branch weight; release only after readback.
            self._on_spatial_changed(reserve)
            if not graph.eq_enabled:
                required_db = -(reserve + 1.0) if reserve > 0 else 0.0
                if graph.output_gain_manager:
                    if graph.output_gain_db > required_db:
                        gain = 10 ** (required_db / 20)
                        graph.output_gain_manager.update_controls({"gain_l:Mult": gain, "gain_r:Mult": gain})
                        graph.output_gain_db = required_db
                elif required_db < 0:
                    graph._prepare_output_gain(reserve, graph._physical())
                    graph._link(graph.spatial_name, graph.output_gain_name)
            try:
                owner.update_controls(controls)
            except Exception:
                # A timeout and failed rollback may leave higher weights applied.
                # Keep the maximum reserve until a verified update or Apply.
                raise
            graph.spatial_peak_db = peak
            graph.spatial_args = args
            try:
                self._on_spatial_changed(peak)
            except Exception:
                logger.exception("Spatial controls verified; conservative reserve retained")
            # Keep a larger native reserve after live changes; topology remains stable.
            return rate, peak

        def finished(success, message, result):
            if success:
                rate, peak = result
                self.spatial.fs = rate
                self.spatial.estimated_peak_db = peak
                self.spatial.estimated_peak_gain_db = peak
                self.spatial._applied_state = state
            self.spatial.status_label.setText("Spatial sound updated" if success else f"Spatial update failed: {message}")
        self._dispatch_graph("Updating Spatial sound…", update, finished)

    def _headroom_specs(self, specs, profile, peak_db, limiter_enabled):
        """Change only the gain of the immutable applied EQ layout."""
        if profile is None or not profile.automatic_headroom:
            return list(specs), self.peq.auto_trim_db
        from eqspace.core.dsp.headroom import automatic_trim_db
        trim = automatic_trim_db(profile.to_bands(), self.peq.fs, profile.preamp_db,
                                 peak_db, limiter_enabled=limiter_enabled)
        gain = 10 ** ((profile.preamp_db + trim) / 20)
        updated = [FilterSpec(spec.name, spec.filter_type,
                   dict(spec.params, Mult=gain) if spec.name == "preamp" else dict(spec.params))
                   for spec in specs]
        return updated, trim

    def _on_spatial_changed(self, peak_db: float, *, limiter_enabled: bool | None = None) -> None:
        previous_peak = self.peq.spatial_peak_db
        previous_trim = self.peq.auto_trim_db
        self.peq.spatial_peak_db = peak_db
        try:
            manager = self.audio_graph._active_eq_manager()
            active_specs = getattr(manager, "_active_filters", None) or self.peq._last_good_specs
            if (manager.is_loaded and self._applied_eq_profile is not None
                    and self._applied_eq_profile.automatic_headroom and active_specs):
                specs, trim = self._headroom_specs(
                    active_specs, self._applied_eq_profile, peak_db,
                    bool(self.audio_graph.limiter_name) if limiter_enabled is None else limiter_enabled,
                )
                manager.reload(specs)
                if type(manager) is not FilterChainManager:
                    manager.verify_controls(specs)
                self.peq.auto_trim_db = trim
                self.peq._last_good_specs = specs
            self._curve_requested.emit()
        except Exception:
            self.peq.spatial_peak_db = previous_peak
            self.peq.auto_trim_db = previous_trim
            self._curve_requested.emit()
            raise

    def _graph_mutation_allowed(self) -> bool:
        return not self._profile_apply_busy and self.peq._apply_worker is None and self._graph_worker is None

    @Slot(str)
    def _show_graph_phase(self, phase):
        self.statusBar().showMessage(phase)
        self.peq.status_label.setText(phase)

    def _dispatch_graph(self, label, action, finished=None, *, verify_route=True):
        """Serialize graph work; result callbacks always execute on the GUI thread."""
        if not self._graph_mutation_allowed():
            self.statusBar().showMessage("Finishing the current sound update…")
            return False
        from eqspace.ui.async_worker import AsyncActionWorker
        from eqspace.diagnostics import operation

        def execute():
            with operation(label, target="+".join(self.audio_graph.active_stages()) or "direct",
                           rate=self.peq.fs):
                try:
                    return action()
                finally:
                    if verify_route:
                        try:
                            self._last_graph_verified = self.audio_graph.is_path_verified()
                        except Exception:
                            self._last_graph_verified = False

        self._graph_callback = finished
        self._graph_worker = AsyncActionWorker(execute)
        self._graph_worker.signals.result.connect(self._graph_result.emit)
        self._set_apply_busy(True)
        self.statusBar().showMessage(label)
        self._loading_timer.start()
        QThreadPool.globalInstance().start(self._graph_worker)
        return True

    @Slot(bool, str, object)
    def _finish_graph_action(self, success, message, result):
        callback = self._graph_callback
        self._graph_callback = None
        self._graph_worker = None
        # Atomic graph transitions can replace the EQ owner even when initiated
        # from the Spatial or Mixer tab. Adopt it before any UI refresh/callback.
        active_manager = self.audio_graph._active_eq_manager()
        if active_manager is not self.peq.manager:
            self.peq.manager = active_manager
            self.peq._last_good_specs = list(active_manager._active_filters or []) if active_manager.is_loaded else None
        self._loading_timer.stop()
        self.loading_indicator.hide()
        self.statusBar().showMessage("Sound updated" if success else f"Update failed: {message}")
        if callback:
            callback(success, message, result)
        self._set_apply_busy(False)
        self.peq.update_curve()
        if self._live_eq_pending and not self._live_eq_timer.isActive():
            self._live_eq_timer.start()
        if getattr(self, "_close_when_done", False):
            self._close_when_done = False
            self.close()

    def _editor_profile(self):
        return EQProfile.from_bands(
            "Current setup", list(self.peq.bands), preamp_db=self.peq.preamp_db,
            spatial=self.spatial.get_state(), spatial_enabled=bool(self.audio_graph.spatial_name),
            mic=self.mic.get_state(),
            limiter_enabled=bool(self.audio_graph.limiter_name),
            automatic_headroom=self._automatic_headroom_enabled,
            eq_enabled=self.audio_graph.eq_enabled, scope="playback")

    def _profile_saved(self, profile):
        save_last_profile(profile.name)
        self.tray_manager.refresh_profiles()
        self.peq.status_label.setText(f"Saved profile “{profile.name}”")

    def _profile_renamed(self, old, new):
        if load_last_profile() == old:
            save_last_profile(new)
        if self.presets._draft_name == old:
            self.presets.set_draft_name(new)
        self.tray_manager.refresh_profiles()

    def _profile_deleted(self, name):
        if load_last_profile() == name:
            save_last_profile(None)
        self.tray_manager.refresh_profiles()

    def _open_logs(self):
        from eqspace.diagnostics import log_location
        path, error = log_location()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        else:
            self.statusBar().showMessage(error or "Logging is not configured")

    def _copy_diagnostics(self):
        from eqspace.diagnostics import diagnostic_report
        QApplication.clipboard().setText(diagnostic_report({
            "EQ": self.audio_graph.eq_enabled,
            "Spatial": self.audio_graph.spatial_name,
            "LSP": self.audio_graph.limiter_name,
            "output": self.mixer.selected_output_name,
            "rate": self.peq.fs,
            "busy": self._profile_apply_busy,
        }))
        self.statusBar().showMessage("Diagnostic report copied")

    def _set_apply_busy(self, busy: bool) -> None:
        self._profile_apply_busy = busy
        self.peq.apply_button.setEnabled(not busy)
        if busy:
            self.mixer.invalidate_observation()
            self.presets.apply_button.setEnabled(False)
        else:
            self.presets._on_selection_changed(self.presets.preset_list.currentRow())
        self.spatial.refresh_mutation_controls()
        if busy:
            self.mixer.refresh_mutation_controls()
        else:
            self.mixer.refresh()
        self._sync_limiter_ui()

    def _sync_limiter_ui(self) -> None:
        enabled = self.audio_graph.limiter_name is not None
        available = self.limiter_capability is not None
        self.peq.limiter_check.setEnabled(
            available and self._graph_mutation_allowed()
        )
        self.peq.limiter_check.blockSignals(True)
        self.peq.limiter_check.setChecked(enabled)
        self.peq.limiter_check.blockSignals(False)
        self.peq.update_curve()
        limiter_status = (
            "Limiter active · at least 5 ms lookahead; total latency depends on oversampling"
            if enabled and self._last_graph_verified
            else "Limiter loaded · Route unverified"
        )
        self.peq.limiter_latency_label.setText(
            limiter_status if enabled else ("Limiter off" if available
                             else (self.limiter_unavailable_reason or "Limiter unavailable"))
        )

    def _on_routing_changed(self, enabled: bool) -> None:
        self.presets.set_eq_enabled(enabled)
        self._sync_limiter_ui()

    def _change_limiter(self, enabled: bool) -> None:
        # Restore conservative gain before removing the protecting output stage.
        if not enabled:
            self._on_spatial_changed(self.peq.spatial_peak_db, limiter_enabled=False)
        try:
            self.audio_graph.set_limiter(enabled, self.limiter_capability)
            self._on_spatial_changed(self.peq.spatial_peak_db)
        except Exception:
            self._on_spatial_changed(self.peq.spatial_peak_db)
            raise

    def _on_limiter_toggled(self, enabled: bool) -> None:
        if not self._graph_mutation_allowed():
            self.peq.status_label.setText("Limiter change rejected: EQ Apply is in progress")
            self._sync_limiter_ui()
            return
        self._dispatch_graph("Loading limiter…" if enabled else "Removing limiter…",
                             lambda: self._change_limiter(enabled),
                             lambda success, message, _: self.peq.status_label.setText(
                                 "Limiter updated" if success else f"Limiter change failed: {message}"))

    def _on_peq_apply_completed(self, success: bool) -> None:
        if success and not self._profile_apply_busy:
            self.presets.clear_active()

    def _on_save_profile_requested(self, bands: list[EQBand]) -> None:
        self.presets._on_save_as_profile()

    # ---- tray & window lifecycle --------------------------------------------

    def _is_tray_available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _on_tray_profile_selected(self, name: str) -> None:
        try:
            profile = storage.load_profile(name)
            self.apply_profile(
                profile,
                async_mode=True,
                on_done=lambda success, _: self.presets.clear_active() if success else None,
            )
        except Exception as exc:
            logger.error("Failed to load profile %r from tray: %s", name, exc)

    def _toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def close_completely(self) -> None:
        self._quitting = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._profile_apply_busy or self.peq._apply_worker is not None:
            self._close_when_done = True
            event.ignore()
            return
        if getattr(self, "_quitting", False) or not self._is_tray_available():
            if (type(self.peq.manager) is FilterChainManager and self.audio_graph.has_owned_modules
                    and not getattr(self, "_shutdown_complete", False)):
                self._live_eq_timer.stop()
                self._live_spatial_timer.stop()
                self._live_eq_pending = False
                self._live_spatial_pending = None
                def shutdown_done(success, message, result):
                    if success:
                        self._shutdown_complete = True
                        self._close_when_done = True
                    else:
                        self._close_when_done = False
                        self.peq.status_label.setText(f"Close blocked: audio handoff or cleanup failed: {message}")
                self._dispatch_graph("Restoring direct output before closing…", self.audio_graph.shutdown, shutdown_done)
                event.ignore()
                return
            try:
                if type(self.peq.manager) is FilterChainManager and self.audio_graph.has_owned_modules and not getattr(self, "_shutdown_complete", False):
                    self.audio_graph.shutdown()
                elif type(self.peq.manager) is FilterChainManager and self.peq.manager.is_loaded:
                    self.peq.manager.unload()
                elif hasattr(self.mixer, "is_routed") and self.mixer.is_routed():
                    _control.set_system_routing(
                        False,
                        fallback_sink_name=self.mixer.selected_output_name,
                        registry=self.mixer.registry,
                    )
            except Exception as exc:
                logger.exception("failed to verify direct system routing on close")
                if type(self.peq.manager) is FilterChainManager and self.audio_graph.has_owned_modules:
                    self.peq.status_label.setText(f"Close blocked: audio handoff or cleanup failed: {exc}")
                    event.ignore()
                    return
            event.accept()
        else:
            event.ignore()
            self.hide()
