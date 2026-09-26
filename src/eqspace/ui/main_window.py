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
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
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
        self._automatic_headroom_enabled = False
        self._applied_eq_profile: EQProfile | None = None

        self.tabs = QTabWidget()
        active_registry = registry or PipeWireRegistry(timeout=2.0)
        self.mixer = MixerWidget(
            registry=active_registry,
            poll_interval_ms=poll_interval_ms,
        )
        self.peq = PeqWidget(manager=filter_manager or FilterChainManager(
            node_name=f"eqspace.filter-chain.{uuid.uuid4().hex[:12]}"),
                             fs=active_registry.graph_rate() if hasattr(active_registry, "graph_rate") else 48000.0)
        self.presets = PresetsWidget()
        self.limiter_capability, self.limiter_unavailable_reason = detect_limiter_with_reason()
        if self.limiter_capability and lv2_host_directory() is None:
            self.limiter_capability = None
            self.limiter_unavailable_reason = (
                "PipeWire LV2 host is missing. Install the matching LV2 host; see docs/lv2-host.md."
            )
        if self.limiter_capability:
            self.peq.limiter_check.setToolTip(
                "Apply EQ first. Adds at least 5 ms lookahead; oversampling may add latency."
            )
        else:
            self.peq.limiter_check.setToolTip(self.limiter_unavailable_reason or "LSP limiter unavailable")
            self.peq.limiter_latency_label.setText(self.limiter_unavailable_reason or "Limiter unavailable")
        self.peq.limiter_check.toggled.connect(self._on_limiter_toggled)
        self.spatial = SpatialWidget(manager=ModuleArgsManager())
        self.audio_graph = AudioGraphController(
            active_registry, lambda: self.mixer.selected_output_name, lambda: self.peq.manager
        )
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
        self._live_eq_timer.setInterval(180)
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

        # Status bar Help button
        status_bar = self.statusBar()
        self.help_button = QPushButton("Help / Quick-Start")
        self.help_button.setObjectName("statusBarHelpButton")
        self.help_button.setToolTip("Toggle Quick-Start Guide banner")
        self.help_button.clicked.connect(self._toggle_quick_start_banner)
        status_bar.addPermanentWidget(self.help_button)

        self.resize(1000, 650)

        self.tray_manager = SystemTrayManager(parent=self)
        self.tray_manager.profile_selected.connect(self._on_tray_profile_selected)
        self.tray_manager.show_hide_triggered.connect(self._toggle_visible)
        self.tray_manager.quit_requested.connect(self.close_completely)
        if self._is_tray_available():
            self.tray_manager.show()

        if restore_profile:
            self.restore_last_profile()

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
                                       automatic_headroom=True,
                                       limiter_enabled=self.audio_graph.limiter_name is not None)
        self.apply_profile(profile, async_mode=True,
                           on_done=lambda success, _: self.presets.clear_active() if success else None)

    def _schedule_live_eq(self, *_args) -> None:
        if self.peq.peak_label.text() == "Response unavailable":
            self._live_eq_pending = False
            self._live_eq_timer.stop()
            return
        if not self.peq.manager.is_loaded or not self.audio_graph.eq_enabled:
            return
        self._live_eq_pending = True
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
            self._on_manual_peq_apply()

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
            if (self.peq.manager.is_loaded and self._applied_eq_profile is not None
                    and self._applied_eq_profile.automatic_headroom and self.peq._last_good_specs):
                specs, trim = self._headroom_specs(
                    self.peq._last_good_specs, self._applied_eq_profile, peak_db,
                    bool(self.audio_graph.limiter_name) if limiter_enabled is None else limiter_enabled,
                )
                self.peq.manager.reload(specs)
                self.peq.manager.verify_controls(specs)
                self.peq.auto_trim_db = trim
                self.peq._last_good_specs = specs
            self.peq.update_curve()
        except Exception:
            self.peq.spatial_peak_db = previous_peak
            self.peq.auto_trim_db = previous_trim
            self.peq.update_curve()
            raise

    def _graph_mutation_allowed(self) -> bool:
        return not self._profile_apply_busy and self.peq._apply_worker is None

    def _set_apply_busy(self, busy: bool) -> None:
        self._profile_apply_busy = busy
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
            available and self._graph_mutation_allowed() and (self.audio_graph.eq_enabled or enabled)
        )
        self.peq.limiter_check.blockSignals(True)
        self.peq.limiter_check.setChecked(enabled)
        self.peq.limiter_check.blockSignals(False)
        self.peq.update_curve()
        limiter_status = (
            "Limiter active · at least 5 ms lookahead; total latency depends on oversampling"
            if enabled and self.audio_graph.is_path_verified()
            else "Limiter loaded · Route unverified"
        )
        self.peq.limiter_latency_label.setText(
            limiter_status if enabled else ("Limiter off" if available and self.audio_graph.eq_enabled
                             else (self.limiter_unavailable_reason or "Apply EQ before enabling the limiter"))
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
        try:
            self._change_limiter(enabled)
        except Exception as exc:
            self.peq.status_label.setText(f"Limiter change failed: {exc}")
        self._sync_limiter_ui()
        self.mixer.refresh()

    def _on_peq_apply_completed(self, success: bool) -> None:
        if success and not self._profile_apply_busy:
            self.presets.clear_active()

    def _on_save_profile_requested(self, bands: list[EQBand]) -> None:
        name, ok = QInputDialog.getText(self, "Save as profile", "Profile name:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            profile = EQProfile.from_bands(
                name=name,
                bands=bands,
                preamp_db=self.peq.preamp_db,
                spatial=self.spatial.get_state(),
                spatial_enabled=self.audio_graph.spatial_name is not None,
                automatic_headroom=True,
                limiter_enabled=self.audio_graph.limiter_name is not None,
                mic=self.mic.get_state(),
            )
            storage.save_profile(profile)
        except Exception as exc:
            self.peq.status_label.setText(f"Save failed: {exc}")
            return
        save_last_profile(name)
        self.peq.status_label.setText(f"Saved profile “{name}”")

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
            try:
                if type(self.peq.manager) is FilterChainManager and self.audio_graph.has_owned_modules:
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
