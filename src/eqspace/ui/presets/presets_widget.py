"""Searchable factory/user library, preview, explicit Apply and draft saving."""

from __future__ import annotations

import errno
import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from eqspace.core.profiles import storage
from eqspace.core.profiles.models import EQProfile
from eqspace.core.profiles.presets import list_presets, load_preset

Identity = tuple[str, str]
logger = logging.getLogger(__name__)


def profile_summary(profile: EQProfile) -> str:
    """Complete processing preview, including disabled bands and saved stage state."""
    lines = [f'{profile.name} · {"EQ only" if profile.scope == "eq" else "Full playback"}',
             f'EQ: {"on" if profile.scope == "eq" or profile.eq_enabled else "off"} · Preamp: {profile.preamp_db:g} dB',
             f'Automatic headroom: {"on" if profile.automatic_headroom else "off"}']
    if profile.scope == 'playback':
        spatial = 'preserve current' if profile.spatial_enabled is None else ('on' if profile.spatial_enabled else 'off')
        lines += [f'Spatial: {spatial} · LSP limiter: {"on" if profile.limiter_enabled else "off"}',
                  f'Spatial settings: {profile.spatial}']
    else:
        lines.append('Spatial and LSP limiter are preserved when applied.')
    lines += [f'{i}: {"ON" if b.enabled else "OFF"} {b.band_type} · {b.freq_hz:g} Hz · {b.gain_db:+g} dB · Q {b.q:g}' for i, b in enumerate(profile.bands, 1)]
    if not profile.bands:
        lines.append('No EQ bands (flat).')
    return '\n'.join(lines)


class PresetsWidget(QWidget):
    preset_apply_requested = Signal(object)
    profile_saved = Signal(object)
    profile_renamed = Signal(str, str)
    profile_deleted = Signal(str)

    def __init__(self, list_presets_fn: Callable[[], list[str]] = list_presets,
                 load_preset_fn: Callable[[str], object] = load_preset,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._list_presets = list_presets_fn
        self._load_preset = load_preset_fn
        self._state_supplier: Callable[[], EQProfile] | None = None
        self._applying_name: Identity | None = None
        self._active_name: Identity | None = None
        self._eq_enabled = False
        self._applying_eq_requested = True
        self._active_eq_requested = True
        self._draft_name = 'Current draft'
        layout = QVBoxLayout(self)
        self.current_draft_label = QLabel('Current draft · Save captures the editor')
        layout.addWidget(self.current_draft_label)
        filters = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('Search presets…')
        self.source_filter = QComboBox()
        self.source_filter.addItems(['All presets', 'Built-in', 'My presets'])
        filters.addWidget(self.search_input)
        filters.addWidget(self.source_filter)
        layout.addLayout(filters)
        self.preset_list = QListWidget()
        self.preset_list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self.preset_list)
        self.description_label = QLabel('')
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)
        self.research_label = QLabel('')
        self.research_label.setWordWrap(True)
        self.research_label.setStyleSheet('color: #8a8f98;')
        layout.addWidget(self.research_label)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(190)
        layout.addWidget(self.preview_text)
        buttons = QHBoxLayout()
        self.apply_button = QPushButton('Apply')
        self.save_button = QPushButton('Save current draft…')
        self.import_button = QPushButton('Import…')
        self.export_button = QPushButton('Export…')
        self.apply_button.clicked.connect(self._on_apply)
        self.save_button.clicked.connect(self._on_save_as_profile)
        self.import_button.clicked.connect(self._on_import)
        self.export_button.clicked.connect(self._on_export)
        for button in (self.apply_button, self.save_button, self.import_button, self.export_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        manage = QHBoxLayout()
        self.rename_button = QPushButton('Rename…')
        self.duplicate_button = QPushButton('Duplicate')
        self.delete_button = QPushButton('Delete…')
        self.rename_button.clicked.connect(self._on_rename)
        self.duplicate_button.clicked.connect(self._on_duplicate)
        self.delete_button.clicked.connect(self._on_delete)
        for button in (self.rename_button, self.duplicate_button, self.delete_button):
            manage.addWidget(button)
        manage.addStretch(1)
        layout.addLayout(manage)
        self.status_label = QLabel('')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.search_input.textChanged.connect(self.refresh)
        self.source_filter.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def set_state_supplier(self, supplier: Callable[[], EQProfile]) -> None:
        """Supply the editor snapshot; saving never reconstructs selected factory state."""
        self._state_supplier = supplier
        self._on_selection_changed(self.preset_list.currentRow())

    def set_draft_name(self, name: str) -> None:
        self._draft_name = name or 'Current draft'
        self.current_draft_label.setText(f'{self._draft_name} · Save captures the editor')

    def selected_identity(self) -> Identity | None:
        item = self.preset_list.currentItem()
        return tuple(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def selected_preset_name(self) -> str | None:
        identity = self.selected_identity()
        return identity[1] if identity else None

    def selected_profile(self) -> EQProfile | None:
        identity = self.selected_identity()
        if identity is None:
            return None
        if identity[0] == 'user':
            return storage.load_profile(identity[1])
        source = self._load_preset(identity[1])
        return source.to_profile() if hasattr(source, 'to_profile') else source

    def refresh(self, *_args, select: Identity | None = None) -> None:
        previous = select or self.selected_identity()
        self.preset_list.blockSignals(True)
        self.preset_list.clear()
        query = self.search_input.text().strip().casefold()
        source = self.source_filter.currentIndex()
        names = ([] if source == 2 else [('builtin', n) for n in self._list_presets()])
        names += ([] if source == 1 else [('user', n) for n in storage.list_profiles()])
        selected_row = -1
        for kind, name in names:
            display, description = name, ''
            if kind == 'builtin':
                try:
                    preset = self._load_preset(name)
                    display = getattr(preset, 'display_name', None) or name
                    description = getattr(preset, 'description', '')
                except Exception:
                    pass  # retain broken entries so their validation error is visible
            if query not in f'{name} {display} {description}'.casefold():
                continue
            item = QListWidgetItem(f'{display} · {"Built-in" if kind == "builtin" else "My presets"}')
            item.setData(Qt.ItemDataRole.UserRole, (kind, name))
            self.preset_list.addItem(item)
            if (kind, name) == previous:
                selected_row = self.preset_list.count() - 1
        self.preset_list.setCurrentRow(selected_row)
        self.preset_list.blockSignals(False)
        self._on_selection_changed(selected_row)

    def _on_selection_changed(self, _row: int) -> None:
        identity = self.selected_identity()
        busy = self._applying_name is not None
        self.apply_button.setText('Active ✓' if identity is not None and identity == self._active_name and self._eq_enabled == self._active_eq_requested else 'Apply')
        self.save_button.setEnabled(self._state_supplier is not None and not busy)
        self.import_button.setEnabled(not busy)
        self.source_filter.setEnabled(not busy)
        self.search_input.setEnabled(not busy)
        user = identity is not None and identity[0] == 'user'
        self.rename_button.setEnabled(user and not busy)
        self.delete_button.setEnabled(user and not busy)
        self.duplicate_button.setEnabled(user and not busy)
        self.export_button.setEnabled(identity is not None and not busy)
        self.apply_button.setEnabled(identity is not None and not busy)
        self.description_label.clear()
        self.research_label.clear()
        self.preview_text.clear()
        if identity is None:
            return
        try:
            profile = self.selected_profile()
            if identity[0] == 'builtin':
                source = self._load_preset(identity[1])
                self.description_label.setText(getattr(source, 'description', ''))
                self.research_label.setText(getattr(source, 'research_basis', ''))
            else:
                self.description_label.setText('My preset · EQ only' if profile.scope == 'eq' else 'My preset · Full playback')
            self.preview_text.setPlainText(profile_summary(profile))
        except Exception as exc:
            self.description_label.setText(f'Could not load preset: {exc}')
            for button in (self.apply_button, self.export_button, self.duplicate_button, self.rename_button):
                button.setEnabled(False)

    def _on_apply(self) -> None:
        if self._applying_name is not None:
            return
        identity = self.selected_identity()
        if identity is None:
            return
        try:
            profile = self.selected_profile()
        except Exception as exc:
            self.status_label.setText(f'Could not load preset: {exc}')
            return
        self._applying_name = identity
        self._applying_eq_requested = True if profile.scope == 'eq' else profile.eq_enabled
        self.preset_list.setEnabled(False)
        self._on_selection_changed(self.preset_list.currentRow())
        self.apply_button.setText('Applying…')
        self.status_label.setText(f'Applying preset “{identity[1]}”…')
        self.preset_apply_requested.emit(profile)

    def set_apply_result(self, success: bool, message: str) -> None:
        identity = self._applying_name
        if identity is None:
            return
        self._applying_name = None
        self.preset_list.setEnabled(True)
        if success:
            self._active_name = identity
            self._active_eq_requested = self._applying_eq_requested
            self._eq_enabled = self._active_eq_requested
            suffix = ' · EQ off' if not self._active_eq_requested else ''
            self.status_label.setText(f'Preset “{identity[1]}” active{suffix}')
        else:
            self.status_label.setText(f'Apply failed: {message}')
        self._on_selection_changed(self.preset_list.currentRow())

    def set_eq_enabled(self, enabled: bool) -> None:
        self._eq_enabled = enabled
        if self._active_name and self._applying_name is None:
            name = self._active_name[1]
            if enabled == self._active_eq_requested:
                suffix = ' · EQ off' if not enabled else ''
                self.status_label.setText(f'Preset “{name}” active{suffix}')
            else:
                self.status_label.setText(f'EQ {"on" if enabled else "off"} · Preset “{name}” loaded')
            self._on_selection_changed(self.preset_list.currentRow())

    def clear_active(self) -> None:
        self._active_name = None
        self._eq_enabled = False
        self.status_label.clear()
        self._on_selection_changed(self.preset_list.currentRow())

    def _request_save_details(self, profile: EQProfile) -> tuple[str, str] | None:
        dialog = QDialog(self)
        dialog.setWindowTitle('Save current draft')
        layout = QFormLayout(dialog)
        name = QLineEdit(profile.name)
        scope = QComboBox()
        scope.addItem('Full playback — EQ, Spatial and LSP limiter', 'playback')
        scope.addItem('EQ only — preserve Spatial and LSP limiter on Apply', 'eq')
        layout.addRow('Preset name:', name)
        layout.addRow('Save:', scope)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not name.text().strip():
            return None
        return name.text().strip(), scope.currentData()

    def _collision_choice(self, name: str) -> str:
        if name not in storage.list_profiles():
            return 'keep_both'
        box = QMessageBox(self)
        box.setWindowTitle('Preset name already exists')
        box.setText(f'A preset named “{name}” already exists.')
        keep = box.addButton('Keep both', QMessageBox.ButtonRole.AcceptRole)
        replace = box.addButton('Replace', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep)
        box.exec()
        return 'keep_both' if box.clickedButton() == keep else ('replace' if box.clickedButton() == replace else 'cancel')

    def _reveal_user(self, name: str) -> None:
        self.search_input.blockSignals(True)
        self.source_filter.blockSignals(True)
        self.search_input.clear()
        self.source_filter.setCurrentIndex(2)
        self.search_input.blockSignals(False)
        self.source_filter.blockSignals(False)
        self.refresh(select=('user', name))

    def _save_to_library(self, profile: EQProfile) -> EQProfile | None:
        saved = storage.save_library_profile(profile, self._collision_choice(profile.name))
        if saved is not None:
            if self._active_name == ('user', saved.name):
                self.clear_active()
            self._reveal_user(saved.name)
        return saved

    def _on_save_as_profile(self) -> None:
        if self._state_supplier is None or self._applying_name is not None:
            return
        try:
            profile = self._state_supplier().model_copy(deep=True)
        except Exception as exc:
            logger.exception('Could not read current draft for saving')
            self.status_label.setText(f'Could not read current draft: {exc}')
            return
        try:
            details = self._request_save_details(profile)
            if details is None:
                return
            name, scope = details
            updates = {'name': name, 'scope': scope}
            if scope == 'eq':
                updates['eq_enabled'] = True
            saved = self._save_to_library(profile.model_copy(update=updates))
            if saved is None:
                return
            self.set_draft_name(saved.name)
            self.profile_saved.emit(saved)
            self.status_label.setText(f'Saved current draft as “{saved.name}”')
        except Exception as exc:
            directory = storage.profiles_dir()
            logger.exception('Could not save current draft to %s', directory)
            if isinstance(exc, OSError) and exc.errno == errno.EROFS:
                self.status_label.setText(
                    f'Save failed: preset folder is on a read-only filesystem: {directory}. '
                    'Run EQ-Space outside the restricted environment, or set '
                    'XDG_CONFIG_HOME to a writable folder.')
            else:
                self.status_label.setText(f'Save failed: {exc}')

    def _confirm_import(self, profile: EQProfile) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle('Preview import')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Review the imported processing before adding it to My presets.'))
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText(profile_summary(profile))
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Import')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(560, 400)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Import preset', str(Path.home()), 'Presets (*.json *.txt);;EQ-Space JSON (*.json);;APO / AutoEQ parametric text (*.txt)')
        if not path:
            return
        try:
            profile = storage.import_profile(Path(path))
            if not self._confirm_import(profile):
                return
            saved = self._save_to_library(profile)
            if saved is not None:
                self.status_label.setText(f'Imported “{saved.name}” · Select Apply to activate')
        except Exception as exc:
            self.status_label.setText(f'Import failed: {exc}')

    def _on_export(self) -> None:
        try:
            profile = self.selected_profile()
            if profile is None:
                return
            path, _ = QFileDialog.getSaveFileName(self, 'Export preset', f'{profile.name}.json', 'EQ-Space JSON (*.json)')
            if not path:
                return
            storage.export_profile(profile, Path(path))
            self.status_label.setText(f'Exported to {path}')
        except Exception as exc:
            self.status_label.setText(f'Export failed: {exc}')

    def _on_rename(self) -> None:
        identity = self.selected_identity()
        if identity is None or identity[0] != 'user':
            return
        new_name, ok = QInputDialog.getText(self, 'Rename preset', 'Preset name:', text=identity[1])
        if not ok or not new_name.strip() or new_name.strip() == identity[1]:
            return
        try:
            renamed = storage.rename_profile(identity[1], new_name.strip(), self._collision_choice(new_name.strip()))
            if renamed is not None:
                if self._active_name == identity:
                    self._active_name = ('user', renamed.name)
                elif self._active_name == ('user', renamed.name):
                    # Replace overwrote the active destination with the source
                    # contents; its old Active marker no longer describes disk.
                    self.clear_active()
                self._reveal_user(renamed.name)
                self.profile_renamed.emit(identity[1], renamed.name)
                self.status_label.setText(f'Renamed to “{renamed.name}”')
        except Exception as exc:
            self.status_label.setText(f'Rename failed: {exc}')

    def _on_duplicate(self) -> None:
        identity = self.selected_identity()
        if identity is None or identity[0] != 'user':
            return
        try:
            saved = storage.duplicate_profile(identity[1])
            self._reveal_user(saved.name)
            self.status_label.setText(f'Duplicated as “{saved.name}”')
        except Exception as exc:
            self.status_label.setText(f'Duplicate failed: {exc}')

    def _on_delete(self) -> None:
        identity = self.selected_identity()
        if identity is None or identity[0] != 'user':
            return
        result = QMessageBox.question(self, 'Delete preset', f'Delete “{identity[1]}” from My presets?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if result != QMessageBox.StandardButton.Yes:
            return
        try:
            storage.delete_profile(identity[1])
            if self._active_name == identity:
                self.clear_active()
            self.refresh()
            self.profile_deleted.emit(identity[1])
            self.status_label.setText(f'Deleted “{identity[1]}”')
        except Exception as exc:
            self.status_label.setText(f'Delete failed: {exc}')
