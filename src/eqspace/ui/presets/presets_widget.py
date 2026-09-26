"""Presets tab: built-in EQ presets, apply, save, import and export.

The preset source is dependency-injected (``list_presets`` / ``load_preset``)
so tests can substitute a fake library. Applying emits an EQProfile; the
window reports completion with :meth:`set_apply_result`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from eqspace.core.profiles import storage
from eqspace.core.profiles.models import EQProfile
from eqspace.core.profiles.presets import list_presets, load_preset


class PresetsWidget(QWidget):
    """List built-in presets and act on the selection."""

    preset_apply_requested = Signal(object)

    def __init__(
        self,
        list_presets_fn: Callable[[], list[str]] = list_presets,
        load_preset_fn: Callable[[str], object] = load_preset,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._list_presets = list_presets_fn
        self._load_preset = load_preset_fn
        self._applying_name: Optional[str] = None
        self._active_name: Optional[str] = None
        self._eq_enabled = False

        layout = QVBoxLayout(self)

        self.preset_list = QListWidget()
        self.preset_list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self.preset_list)

        self.description_label = QLabel("")
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)

        self.research_label = QLabel("")
        self.research_label.setWordWrap(True)
        self.research_label.setStyleSheet("color: #8a8f98;")
        layout.addWidget(self.research_label)

        buttons = QHBoxLayout()
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self._on_apply)
        self.save_button = QPushButton("Save as profile")
        self.save_button.clicked.connect(self._on_save_as_profile)
        self.import_button = QPushButton("Import profile…")
        self.import_button.clicked.connect(self._on_import)
        self.export_button = QPushButton("Export profile…")
        self.export_button.clicked.connect(self._on_export)
        for button in (
            self.apply_button,
            self.save_button,
            self.import_button,
            self.export_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.refresh()

    # ---- preset list -----------------------------------------------------

    def refresh(self) -> None:
        """Reload the preset list from the (injected) preset source."""
        self.preset_list.clear()
        self.preset_list.addItems(self._list_presets())
        self._on_selection_changed(self.preset_list.currentRow())

    def selected_preset_name(self) -> Optional[str]:
        item = self.preset_list.currentItem()
        return item.text() if item is not None else None

    def _on_selection_changed(self, row: int) -> None:
        name = self.selected_preset_name()
        self.apply_button.setText("Active ✓" if name == self._active_name and self._eq_enabled else "Apply")
        if name is None:
            self.description_label.setText("")
            self.research_label.setText("")
            self.apply_button.setEnabled(False)
            self.save_button.setEnabled(False)
            return
        try:
            preset = self._load_preset(name)
        except Exception:
            self.description_label.setText("")
            self.research_label.setText("")
            self.apply_button.setEnabled(False)
            self.save_button.setEnabled(False)
            return
        self.description_label.setText(getattr(preset, "description", ""))
        self.research_label.setText(getattr(preset, "research_basis", ""))
        self.apply_button.setEnabled(self._applying_name is None)
        self.save_button.setEnabled(True)

    # ---- actions ----------------------------------------------------------

    def _on_apply(self) -> None:
        if self._applying_name is not None:
            return
        name = self.selected_preset_name()
        if name is None:
            return
        try:
            preset = self._load_preset(name)
        except Exception as exc:
            self.status_label.setText(f"Could not load preset: {exc}")
            return
        profile = preset.to_profile() if hasattr(preset, "to_profile") else preset
        self._applying_name = name
        self.preset_list.setEnabled(False)
        self.apply_button.setText("Applying…")
        self.apply_button.setEnabled(False)
        self.status_label.setText(f"Applying preset “{name}”…")
        self.preset_apply_requested.emit(profile)

    def set_apply_result(self, success: bool, message: str) -> None:
        name = self._applying_name
        if name is None:
            return
        self._applying_name = None
        self.preset_list.setEnabled(True)
        if success:
            self._active_name = name
            self._eq_enabled = True
            self.status_label.setText(f"Preset “{name}” active")
        else:
            self.status_label.setText(f"Apply failed: {message}")
        self._on_selection_changed(self.preset_list.currentRow())

    def set_eq_enabled(self, enabled: bool) -> None:
        self._eq_enabled = enabled
        if self._active_name and self._applying_name is None:
            self.status_label.setText(
                f"Preset “{self._active_name}” active"
                if enabled else f"EQ off · Preset “{self._active_name}” loaded"
            )
            self._on_selection_changed(self.preset_list.currentRow())

    def clear_active(self) -> None:
        self._active_name = None
        self._eq_enabled = False
        self.status_label.setText("")
        self._on_selection_changed(self.preset_list.currentRow())

    def _on_save_as_profile(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name = self.selected_preset_name()
        if name is None:
            return
        try:
            preset = self._load_preset(name)
        except Exception as exc:
            self.status_label.setText(f"Could not load preset: {exc}")
            return
        profile = preset.to_profile() if hasattr(preset, "to_profile") else preset
        profile_name, ok = QInputDialog.getText(
            self, "Save as profile", "Profile name:", text=profile.name
        )
        profile_name = profile_name.strip()
        if not ok or not profile_name:
            return
        try:
            active_spatial = getattr(self, "current_spatial_enabled", lambda: False)()
            spatial_state = getattr(self, "current_spatial_state", lambda: {})()
            limiter_enabled = getattr(self, "current_limiter_enabled", lambda: False)()
            saved = profile.model_copy(update={"name": profile_name,
                                               "spatial": spatial_state,
                                               "spatial_enabled": active_spatial,
                                               "limiter_enabled": limiter_enabled})
            storage.save_profile(saved)
        except Exception as exc:
            self.status_label.setText(f"Save failed: {exc}")
            return
        self.status_label.setText(f"Saved profile “{profile_name}”")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import profile", str(Path.home()), "EQ-Space profiles (*.json)"
        )
        if not path:
            return
        try:
            profile = storage.import_profile(Path(path))
            storage.save_profile(profile)
        except Exception as exc:
            self.status_label.setText(f"Import failed: {exc}")
            return
        self.status_label.setText(f"Imported profile “{profile.name}”")

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export profile", str(Path.home()), "EQ-Space profiles (*.json)"
        )
        if not path:
            return
        name = self.selected_preset_name()
        try:
            source = self._load_preset(name) if name is not None else None
            if source is None:
                self.status_label.setText("Select a preset to export first")
                return
            profile = source.to_profile() if hasattr(source, "to_profile") else source
            storage.export_profile(profile, Path(path))
        except Exception as exc:
            self.status_label.setText(f"Export failed: {exc}")
            return
        self.status_label.setText(f"Exported to {path}")
