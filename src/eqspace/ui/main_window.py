"""Main window: tab shell (Mixer | Parametric EQ | Presets | Spatial | Mic)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget, QWidget

from eqspace.core.filterchain.manager import FilterChainManager
from eqspace.core.pipewire.registry import PipeWireRegistry
from eqspace.ui.mixer import MixerWidget
from eqspace.ui.peq import PeqWidget
from eqspace.ui.theme import DARK_STYLESHEET


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

        self.tabs.addTab(self.mixer, "Mixer")
        self.tabs.addTab(self.peq, "Parametric EQ")
        self.tabs.addTab(_placeholder("Presets — coming soon"), "Presets")
        self.tabs.addTab(_placeholder("Spatial — coming soon"), "Spatial")
        self.tabs.addTab(_placeholder("Mic — coming soon"), "Mic")

        self.setCentralWidget(self.tabs)
        self.resize(1000, 650)
