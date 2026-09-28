"""Preset library contracts; no live graph required."""
import json
import errno
import logging
import sys
import pytest
from eqspace.core.profiles import storage, presets
from eqspace.core.profiles.models import EQProfile, SCHEMA_VERSION

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

def test_schema_scope_and_legacy_semantics():
    assert SCHEMA_VERSION == 4
    assert EQProfile(name='draft', scope='eq', eq_enabled=False).scope == 'eq'
    for version in (1, 2, 3):
        p = EQProfile(name='legacy', version=version)
        assert p.scope == 'playback' and p.eq_enabled
        if version < 3:
            assert p.spatial_enabled is None and not p.automatic_headroom

def test_factory_is_eq_only_and_holo_punch_explicit():
    p = presets.load_preset('holo_punch_experimental').to_profile()
    assert p.scope == 'eq' and p.eq_enabled
    assert p.preamp_db == 0 and p.automatic_headroom
    assert [b.freq_hz for b in p.bands] == [70, 110, 300, 1800, 3800, 8000]
    assert [b.gain_db for b in p.bands] == [5.5, 2.5, -2.5, 3, 4, 1.5]
    assert 'experimental' in presets.load_preset('holo_punch_experimental').description.lower()

def test_collision_keep_both_replace_and_cancel():
    storage.save_profile(EQProfile(name='same', preamp_db=-1))
    second = storage.save_library_profile(EQProfile(name='same', preamp_db=-2))
    assert second.name == 'same (2)'
    assert storage.load_profile('same').preamp_db == -1
    assert storage.save_library_profile(EQProfile(name='same'), collision='cancel') is None
    storage.save_library_profile(EQProfile(name='same', preamp_db=-3), collision='replace')
    assert storage.load_profile('same').preamp_db == -3

def test_rename_duplicate_and_failure_preserves_original(monkeypatch):
    storage.save_profile(EQProfile(name='original', eq_enabled=False))
    duplicate = storage.duplicate_profile('original')
    assert duplicate.name == 'original (2)' and not duplicate.eq_enabled
    renamed = storage.rename_profile('original', 'renamed')
    assert renamed.name == 'renamed' and 'original' not in storage.list_profiles()
    monkeypatch.setattr(storage, 'save_profile', lambda p: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError):
        storage.rename_profile('renamed', 'fail')
    assert storage.load_profile('renamed').name == 'renamed'

def test_save_checks_readback(monkeypatch):
    real_replace = storage.os.replace
    def corrupt(src, dst):
        real_replace(src, dst)
        dst.write_text('{"name":"changed"}')
    monkeypatch.setattr(storage.os, 'replace', corrupt)
    with pytest.raises(OSError, match='readback'):
        storage.save_profile(EQProfile(name='expected'))

def test_parametric_import_with_disabled_shelves_and_preamp(tmp_path):
    path = tmp_path / 'AutoEQ.txt'
    path.write_text('# Equalizer APO\nPreamp: -5.2 dB\nFilter 1: ON PK Fc 110 Hz Gain 2.5 dB Q 0.9\nFilter 2: OFF LSC Fc 70 Hz Gain 5.5 dB Q 0.707\nFilter 3: ON HSC Fc 8000 Hz Gain 1.5 dB Q 0.707\n')
    p = storage.import_profile(path)
    assert p.scope == 'eq' and not p.automatic_headroom and p.preamp_db == -5.2
    assert [b.band_type for b in p.bands] == ['peaking', 'low_shelf', 'high_shelf']
    assert not p.bands[1].enabled

@pytest.mark.parametrize('line', ['Include: other.txt', 'Convolution: ir.wav', 'Filter 1: ON HP Fc 80 Hz', 'Channel: L', 'Preamp: nan dB', 'Filter 1: ON PK Fc 0 Hz Gain 2 dB Q 1'])
def test_text_rejects_unsupported_and_invalid_with_line(line, tmp_path):
    path = tmp_path / 'bad.txt'
    path.write_text('# comment\n' + line)
    with pytest.raises(ValueError, match='line 2'):
        storage.import_profile(path)

@pytest.fixture(scope='session')
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

@pytest.fixture
def library(qapp):
    from eqspace.ui.presets.presets_widget import PresetsWidget
    from eqspace.core.profiles.presets import Preset
    return PresetsWidget(list_presets_fn=lambda: ['same'], load_preset_fn=lambda n: Preset(name=n, description='factory', research_basis='test'))

def test_library_names_do_not_collide_and_search_has_no_activation(library):
    storage.save_profile(EQProfile(name='same', preamp_db=-8))
    library.refresh()
    seen = []
    library.preset_apply_requested.connect(seen.append)
    library.preset_list.setCurrentRow(0)
    assert library.selected_identity() == ('builtin', 'same')
    library.preset_list.setCurrentRow(1)
    assert library.selected_identity() == ('user', 'same')
    assert library.selected_profile().preamp_db == -8
    library.search_input.setText('missing')
    assert library.preset_list.count() == 0 and seen == []

def test_save_uses_current_draft_and_scope_not_selected_factory(library, monkeypatch):
    draft = EQProfile(name='edited', preamp_db=-9, eq_enabled=False, spatial_enabled=True, limiter_enabled=True)
    library.set_state_supplier(lambda: draft)
    library.preset_list.setCurrentRow(0)
    monkeypatch.setattr(library, '_request_save_details', lambda p: ('saved draft', 'playback'))
    saved = []
    library.profile_saved.connect(saved.append)
    library.save_button.click()
    assert storage.load_profile('saved draft').preamp_db == -9
    assert storage.load_profile('saved draft').limiter_enabled
    assert not storage.load_profile('saved draft').eq_enabled
    assert saved[0].name == 'saved draft'
    monkeypatch.setattr(library, '_request_save_details', lambda p: ('eq draft', 'eq'))
    library.save_button.click()
    assert storage.load_profile('eq draft').scope == 'eq'

def test_save_without_supplier_cannot_save_factory(library):
    library.preset_list.setCurrentRow(0)
    assert not library.save_button.isEnabled()
    library._on_save_as_profile()
    assert storage.list_profiles() == []

def test_save_snapshot_failure_is_logged_and_does_not_open_dialog(library, monkeypatch, caplog):
    def unreadable_draft():
        raise ValueError('assignment destination is read-only')
    library.set_state_supplier(unreadable_draft)
    dialogs, saved = [], []
    monkeypatch.setattr(library, '_request_save_details', lambda p: dialogs.append(p))
    library.profile_saved.connect(saved.append)
    with caplog.at_level(logging.ERROR):
        library._on_save_as_profile()
    assert library.status_label.text() == 'Could not read current draft: assignment destination is read-only'
    assert dialogs == [] and saved == [] and storage.list_profiles() == []
    assert any(record.exc_info and 'current draft' in record.message for record in caplog.records)

@pytest.mark.parametrize('operation', ['mkdir', 'mkstemp'])
def test_read_only_save_names_destination_and_can_retry(library, monkeypatch, caplog, operation):
    library.set_state_supplier(lambda: EQProfile(name='draft', preamp_db=-4.5))
    monkeypatch.setattr(library, '_request_save_details', lambda p: ('my preset', 'playback'))
    target = storage.Path if operation == 'mkdir' else storage.tempfile
    real_operation = getattr(target, operation)
    def read_only(*args, **kwargs):
        raise OSError(errno.EROFS, 'Read-only file system')
    monkeypatch.setattr(target, operation, read_only)
    saved = []
    library.profile_saved.connect(saved.append)
    with caplog.at_level(logging.ERROR):
        library._on_save_as_profile()
    assert 'read-only filesystem' in library.status_label.text()
    assert str(storage.profiles_dir()) in library.status_label.text()
    assert saved == [] and storage.list_profiles() == []
    assert list(storage.profiles_dir().glob('*.tmp')) == []
    assert library.save_button.isEnabled()
    assert any(record.exc_info and str(storage.profiles_dir()) in record.message for record in caplog.records)
    monkeypatch.setattr(target, operation, real_operation)
    library._on_save_as_profile()
    assert storage.load_profile('my preset').preamp_db == -4.5
    assert [profile.name for profile in saved] == ['my preset']

def test_saved_callback_failure_does_not_report_persistence_failure(library, monkeypatch):
    library.set_state_supplier(lambda: EQProfile(name='draft', preamp_db=-4.5))
    monkeypatch.setattr(library, '_request_save_details', lambda p: ('my preset', 'playback'))
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda kind, exc, traceback: errors.append(exc))
    def failed_last_profile_update(profile):
        raise OSError(errno.EROFS, 'last profile state is read-only')
    library.profile_saved.connect(failed_last_profile_update)
    library._on_save_as_profile()
    assert storage.load_profile('my preset').preamp_db == -4.5
    assert library.status_label.text() == 'Saved current draft as “my preset”'
    assert len(errors) == 1 and errors[0].errno == errno.EROFS

def test_import_preview_cancel_does_not_save_or_apply(library, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    path = tmp_path / 'import.txt'
    path.write_text('Preamp: -5 dB\nFilter 1: ON PK Fc 200 Hz Gain 2 dB Q 1')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
    previews, applies = [], []
    monkeypatch.setattr(library, '_confirm_import', lambda p: previews.append(p) or False)
    library.preset_apply_requested.connect(applies.append)
    library._on_import()
    assert len(previews) == 1 and previews[0].preamp_db == -5
    assert storage.list_profiles() == [] and applies == []

def test_user_controls_and_builtin_protection(library):
    storage.save_profile(EQProfile(name='same'))
    library.refresh()
    library.preset_list.setCurrentRow(0)
    assert not library.rename_button.isEnabled()
    assert not library.delete_button.isEnabled()
    library.preset_list.setCurrentRow(1)
    assert library.rename_button.isEnabled() and library.delete_button.isEnabled()

def test_save_dialog_defaults_to_full_playback(library, monkeypatch):
    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr(QDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    assert library._request_save_details(EQProfile(name='my draft', scope='eq')) == ('my draft', 'playback')

def test_replacing_saved_entry_clears_stale_active_identity(library, monkeypatch):
    storage.save_profile(EQProfile(name='same', preamp_db=-8))
    library.refresh(select=('user', 'same'))
    library._on_apply()
    library.set_apply_result(True, 'Applied')
    monkeypatch.setattr(library, '_collision_choice', lambda n: 'replace')
    library._save_to_library(EQProfile(name='same', preamp_db=-4))
    assert library.apply_button.text() == 'Apply'

def test_failed_atomic_replace_keeps_original_and_cleans_tmp(monkeypatch):
    storage.save_profile(EQProfile(name='original', preamp_db=-8))
    def fail(*args):
        raise OSError('rename failed')
    monkeypatch.setattr(storage.os, 'replace', fail)
    with pytest.raises(OSError):
        storage.save_profile(EQProfile(name='original', preamp_db=-2))
    assert storage.load_profile('original').preamp_db == -8
    assert list(storage.profiles_dir().glob('*.tmp')) == []

def test_invalid_copy_rejected_before_mutation():
    invalid = EQProfile(name='bad').model_copy(update={'scope': 'unknown'})
    with pytest.raises(ValueError):
        storage.save_profile(invalid)
    assert not (storage.profiles_dir() / 'bad.json').exists()

def test_saving_reveals_entry_through_builtin_and_search_filters(library, monkeypatch):
    library.source_filter.setCurrentIndex(1)
    library.search_input.setText('same')
    library.set_state_supplier(lambda: EQProfile(name='draft'))
    monkeypatch.setattr(library, '_request_save_details', lambda p: ('fresh saved', 'eq'))
    library._on_save_as_profile()
    assert library.selected_identity() == ('user', 'fresh saved')
    assert library.source_filter.currentText() == 'My presets'
    assert not library.search_input.text()

def test_import_accepts_into_library_without_applying(library, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    path = tmp_path / 'from file.txt'
    path.write_text('Preamp: -5 dB')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
    monkeypatch.setattr(library, '_confirm_import', lambda p: True)
    seen = []
    library.preset_apply_requested.connect(seen.append)
    library._on_import()
    assert library.selected_identity() == ('user', 'from file')
    assert storage.load_profile('from file').preamp_db == -5
    assert seen == []

def test_export_uses_user_identity_when_factory_has_same_name(library, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    storage.save_profile(EQProfile(name='same', preamp_db=-7, scope='eq'))
    library.refresh(select=('user', 'same'))
    path = tmp_path / 'export.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(path), ''))
    library._on_export()
    assert storage.import_profile(path).preamp_db == -7

def test_user_rename_duplicate_delete_workflow(library, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    storage.save_profile(EQProfile(name='original', preamp_db=-3))
    library.refresh(select=('user', 'original'))
    events = []
    library.profile_renamed.connect(lambda old, new: events.append((old, new)))
    library.profile_deleted.connect(lambda name: events.append(name))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('renamed', True))
    library._on_rename()
    assert library.selected_identity() == ('user', 'renamed')
    assert events == [('original', 'renamed')]
    library._on_duplicate()
    assert library.selected_identity() == ('user', 'renamed (2)')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    library._on_delete()
    assert storage.list_profiles() == ['renamed']
    assert events[-1] == 'renamed (2)'

def test_factory_profile_edits_do_not_mutate_builtin_definition():
    preset = presets.load_preset('holo_punch_experimental')
    draft = preset.to_profile()
    draft.bands[0].gain_db = -12
    assert preset.bands[0].gain_db == 5.5

def test_full_playback_can_be_active_with_eq_bypassed(library):
    storage.save_profile(EQProfile(name='Spatial setup', scope='playback', eq_enabled=False, spatial_enabled=True))
    library.refresh(select=('user', 'Spatial setup'))
    library._on_apply()
    library.set_apply_result(True, 'All requested stages verified')
    library.set_eq_enabled(False)
    assert library.apply_button.text() == 'Active ✓'
    assert 'active' in library.status_label.text() and 'EQ off' in library.status_label.text()
    library.set_eq_enabled(True)
    assert library.apply_button.text() == 'Apply'
    assert 'active' not in library.status_label.text()


def test_rename_replace_clears_active_destination_marker(library, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    storage.save_profile(EQProfile(name='source', preamp_db=-3))
    storage.save_profile(EQProfile(name='destination', preamp_db=2))
    library._active_name = ('user', 'destination')
    library.refresh(select=('user', 'source'))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('destination', True))
    monkeypatch.setattr(library, '_collision_choice', lambda name: 'replace')
    library._on_rename()
    assert storage.load_profile('destination').preamp_db == -3
    assert library._active_name is None
