"""System tray icon and quick-actions menu."""

from __future__ import annotations

from typing import Callable, Optional, Sequence
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from eqspace.core.profiles import storage


class SystemTrayManager(QObject):
    """Controls the system tray icon, context menu, and signals."""

    profile_selected = Signal(str)
    mute_toggled = Signal(bool)
    show_hide_triggered = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        list_profiles_fn: Callable[[], Sequence[str]] = storage.list_profiles,
        icon: Optional[QIcon] = None,
    ) -> None:
        super().__init__(parent)
        self._list_profiles = list_profiles_fn
        self.tray_icon = QSystemTrayIcon(parent)
        if icon is None:
            from importlib import resources
            icon_path = resources.files("eqspace.data") / "icons" / "eqspace-tray.svg"
            if icon_path.is_file():
                icon = QIcon(str(icon_path))
        if icon and not icon.isNull():
            self.tray_icon.setIcon(icon)

        self.menu = QMenu(parent)
        self._setup_menu()
        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self._on_activated)

    def _setup_menu(self) -> None:
        self.menu.clear()

        # Show / Hide
        self.show_action = QAction("Show / Hide", self.menu)
        self.show_action.triggered.connect(self.show_hide_triggered.emit)
        self.menu.addAction(self.show_action)

        # Mute toggle
        self.mute_action = QAction("Mute Audio", self.menu)
        self.mute_action.setCheckable(True)
        self.mute_action.toggled.connect(self.mute_toggled.emit)
        self.menu.addAction(self.mute_action)

        self.menu.addSeparator()

        # Profiles Submenu
        self.profiles_menu = self.menu.addMenu("Profiles")
        self.refresh_profiles()

        self.menu.addSeparator()

        # Quit
        self.quit_action = QAction("Quit", self.menu)
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self.menu.addAction(self.quit_action)

    def refresh_profiles(self) -> None:
        self.profiles_menu.clear()
        names = self._list_profiles()
        if not names:
            empty_action = QAction("(No profiles)", self.profiles_menu)
            empty_action.setEnabled(False)
            self.profiles_menu.addAction(empty_action)
            return

        for name in names:
            action = QAction(name, self.profiles_menu)
            action.triggered.connect(
                lambda checked=False, n=name: self.profile_selected.emit(n)
            )
            self.profiles_menu.addAction(action)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_hide_triggered.emit()

    def show(self) -> None:
        self.tray_icon.show()

    def hide(self) -> None:
        self.tray_icon.hide()
