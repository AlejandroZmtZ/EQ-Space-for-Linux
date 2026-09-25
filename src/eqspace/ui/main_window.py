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
from typing import Callable, Optional

from PySide6.QtCore import Qt
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
from eqspace.core.filterchain.manager import FilterChainManager
from eqspace.core.pipewire import control as _control
from eqspace.core.pipewire.registry import PipeWireRegistry, PipeWireUnavailable
from eqspace.core.profiles import storage
from eqspace.core.profiles.models import EQProfile
from eqspace.ui.mic import MicWidget
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

        self.tabs = QTabWidget()
        active_registry = registry or PipeWireRegistry(timeout=2.0)
        self.mixer = MixerWidget(
            registry=active_registry,
            poll_interval_ms=poll_interval_ms,
        )
        self.peq = PeqWidget(manager=filter_manager or FilterChainManager(),
                             fs=active_registry.graph_rate() if hasattr(active_registry, "graph_rate") else 48000.0)
        self.presets = PresetsWidget()
        self.spatial = SpatialWidget(manager=ModuleArgsManager())
        self.mic = MicWidget(manager=ModuleArgsManager())

        self.peq.save_profile_requested.connect(self._on_save_profile_requested)
        self.peq.apply_completed.connect(self._on_peq_apply_completed)
        self.peq.apply_button.clicked.disconnect()
        self.peq.apply_button.clicked.connect(self._on_manual_peq_apply)
        self.presets.preset_apply_requested.connect(self._on_preset_apply_requested)
        self.mixer.routing_changed.connect(self.presets.set_eq_enabled)
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
        if not any(band.enabled for band in profile.bands):
            message = "profile has no enabled EQ bands"
            self.peq.status_label.setText(f"Apply failed: {message}")
            if on_done:
                on_done(False, message)
            return False
        old_bands = list(self.peq.bands)
        old_preamp = self.peq.preamp_db
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
        if replacing and previously_routed:
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
        self._profile_apply_busy = True
        self.peq.bands = next_bands
        self.peq.set_preamp(profile.preamp_db)
        self.peq._refresh_table()
        self.peq.update_curve()

        def _after_apply(success: bool, msg: str) -> None:
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
            self._profile_apply_busy = False
            result["success"] = success
            if on_done:
                on_done(success, msg if success else self.peq._brief_error(msg))
            if getattr(self, "_close_when_done", False):
                self._close_when_done = False
                self.close()

        started = self.peq.apply(async_mode=async_mode, on_done=_after_apply)
        if not started and self._profile_apply_busy:
            self._profile_apply_busy = False
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
        self.apply_profile(
            profile,
            async_mode=True,
            on_done=self.presets.set_apply_result,
        )

    def _on_manual_peq_apply(self) -> None:
        profile = EQProfile.from_bands("Current EQ", list(self.peq.bands),
                                       preamp_db=self.peq.preamp_db)
        self.apply_profile(profile, async_mode=True,
                           on_done=lambda success, _: self.presets.clear_active() if success else None)

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
                if hasattr(self.mixer, "is_routed") and self.mixer.is_routed():
                    _control.set_system_routing(
                        False,
                        fallback_sink_name=self.mixer.selected_output_name,
                        registry=self.mixer.registry,
                    )
            except Exception:
                logger.debug("failed to restore direct system routing on close")
            event.accept()
        else:
            event.ignore()
            self.hide()
