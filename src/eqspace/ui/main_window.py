"""Main window: tab shell (Mixer | Parametric EQ | Presets | Spatial | Mic).

Wiring done here:

- Presets tab: ``preset_previewed`` updates the PEQ band model and pushes
  it to the filter-chain manager, then switches to the PEQ tab.
- PEQ tab: ``save_profile_requested`` opens a small name dialog and saves
  via :mod:`eqspace.core.profiles.storage`.
- Spatial / Mic tabs get a :class:`~eqspace.ui.module_args_manager.ModuleArgsManager`
  each, so their pre-rendered module-args strings can be loaded.
- Startup: the last active profile (see :mod:`eqspace.ui.profile_state`)
  is restored into the PEQ tab and applied; a missing profile is tolerated.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QLabel, QMainWindow, QTabWidget, QWidget

from eqspace.core.dsp.filter_design import EQBand
from eqspace.core.filterchain.manager import FilterChainManager
from eqspace.core.pipewire.registry import PipeWireRegistry
from eqspace.core.profiles import storage
from eqspace.core.profiles.models import EQProfile
from eqspace.ui.mic import MicWidget
from eqspace.ui.mixer import MixerWidget
from eqspace.ui.module_args_manager import ModuleArgsManager
from eqspace.ui.peq import PeqWidget
from eqspace.ui.presets import PresetsWidget
from eqspace.ui.profile_state import load_last_profile, save_last_profile
from eqspace.ui.spatial import SpatialWidget
from eqspace.ui.theme import DARK_STYLESHEET

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

        self.tabs = QTabWidget()
        self.mixer = MixerWidget(
            registry=registry or PipeWireRegistry(),
            poll_interval_ms=poll_interval_ms,
        )
        self.peq = PeqWidget(manager=filter_manager or FilterChainManager())
        self.presets = PresetsWidget()
        self.spatial = SpatialWidget(manager=ModuleArgsManager())
        self.mic = MicWidget(manager=ModuleArgsManager())

        self.peq.save_profile_requested.connect(self._on_save_profile_requested)
        self.presets.preset_previewed.connect(self._on_preset_previewed)
        self.mic.open_peq_requested.connect(lambda: self.tabs.setCurrentWidget(self.peq))

        self.tabs.addTab(self.mixer, "Mixer")
        self.tabs.addTab(self.peq, "Parametric EQ")
        self.tabs.addTab(self.presets, "Presets")
        self.tabs.addTab(self.spatial, "Spatial")
        self.tabs.addTab(self.mic, "Mic")

        self.setCentralWidget(self.tabs)
        self.resize(1000, 650)

        if restore_profile:
            self.restore_last_profile()

    # ---- profile handling ---------------------------------------------------

    def apply_profile(self, profile: EQProfile) -> None:
        """Load a profile into the PEQ model and push it to the manager."""
        self.peq.bands = [band.to_eqband() for band in profile.bands]
        self.peq._refresh_table()
        self.peq.update_curve()
        self.peq.apply()
        self.peq.status_label.setText(f"Profile “{profile.name}” applied")

    def restore_last_profile(self) -> None:
        """Apply the last active profile on startup; tolerate a missing one."""
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
        self.apply_profile(profile)

    def _on_preset_previewed(self, profile: EQProfile) -> None:
        self.apply_profile(profile)
        self.tabs.setCurrentWidget(self.peq)

    def _on_save_profile_requested(self, bands: list[EQBand]) -> None:
        name, ok = QInputDialog.getText(self, "Save as profile", "Profile name:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            storage.save_profile(EQProfile.from_bands(name, bands))
        except Exception as exc:
            self.peq.status_label.setText(f"Save failed: {exc}")
            return
        save_last_profile(name)
        self.peq.status_label.setText(f"Saved profile “{name}”")
