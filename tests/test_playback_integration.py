"""Playback/UI boundaries with staged mock owners; never connects to PipeWire."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from eqspace.core.dsp.filter_design import EQBand
from eqspace.core.filterchain.manager import FilterSpec
from eqspace.core.pipewire.registry import PwSnapshot
from eqspace.core.profiles import storage
from eqspace.core.profiles.models import EQProfile
from eqspace.ui.main_window import MainWindow


class Registry:
    def snapshot(self):
        return PwSnapshot()

    def graph_rate(self, **kwargs):
        return 48000


class Owner:
    instances = []
    fail_readback = False

    def __init__(self, node_name='old', **kwargs):
        self.node_name = node_name
        self.is_loaded = False
        self._runner = None
        self._popen = None
        self._active_filters = None
        self.reloads = []
        self.instances.append(self)

    def load(self, specs):
        self.is_loaded = True
        self._active_filters = tuple(specs)

    def verify_controls(self, specs):
        if self.fail_readback:
            raise RuntimeError('candidate readback failed')

    def reload(self, specs):
        self.reloads.append(list(specs))
        self._active_filters = tuple(specs)

    def unload(self):
        self.is_loaded = False


class Graph:
    """Records requested targets and retires old EQ only on a verified commit."""
    def __init__(self, owner):
        self.owner = owner
        self.registry = Registry()
        self.eq_enabled = True
        self.spatial_name = 'old.spatial'
        self.limiter_name = 'old.limiter'
        self.spatial_peak_db = 3
        self.spatial_args = 'old spatial args'
        self.spatial_manager = None
        self.targets = []
        self.retained = []

    def _active_eq_manager(self):
        return self.owner

    def _eq_name(self):
        return self.owner.node_name if self.owner.is_loaded else None

    def is_path_verified(self):
        return True

    def configure_playback(self, **target):
        self.targets.append(target)
        candidate = target['eq_candidate']
        if candidate is not None and candidate is not self.owner:
            self.owner.unload()
            self.owner = candidate
        self.eq_enabled = target['eq_enabled']
        self.spatial_name = 'new.spatial' if target['spatial_args'] else None
        self.spatial_peak_db = target['spatial_peak_db']
        self.limiter_name = 'new.limiter' if target['limiter_enabled'] else None
        return self.owner

    def retain_until_shutdown(self, owner):
        self.retained.append(owner)


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    w = MainWindow(registry=Registry(), poll_interval_ms=0, restore_profile=False)
    monkeypatch.setattr(w.mixer, 'refresh', lambda: None)
    monkeypatch.setattr(w, '_sync_limiter_ui', lambda: None)
    Owner.instances = []
    monkeypatch.setattr(Owner, 'fail_readback', False)
    old = Owner()
    old.load([FilterSpec('preamp', 'linear', {'Mult': 1, 'Add': 0})])
    w.peq.manager = old
    w.peq._last_good_specs = list(old._active_filters)
    w.audio_graph = Graph(old)
    w.mixer.graph_controller = w.audio_graph
    w.spatial._applied_state = {'layout': 'Stereo', 'wet': 100}
    monkeypatch.setattr('eqspace.ui.main_window.FilterChainManager', Owner)
    monkeypatch.setattr('eqspace.core.dsp.spatial.prepare_spatial', lambda state, rate: ('new spatial args', 6, 'Test Spatial'))
    outcomes = []

    def dispatch(label, action, finished=None):
        try:
            result = action()
        except Exception as exc:
            outcomes.append((False, str(exc)))
            if finished:
                finished(False, str(exc), None)
        else:
            outcomes.append((True, ''))
            if finished:
                finished(True, '', result)
        return True

    monkeypatch.setattr(w, '_dispatch_graph', dispatch)
    w.review_outcomes = outcomes
    yield w
    w.audio_graph = SimpleNamespace(eq_enabled=False, limiter_name=None, has_owned_modules=False)
    w.peq.manager = SimpleNamespace(is_loaded=False)
    monkeypatch.setattr(w.mixer, 'is_routed', lambda: False)
    w.close_completely()


def test_full_profile_restores_all_stage_flags_and_current_editor(window):
    w = window
    p = EQProfile.from_bands('Saved full', [EQBand('peaking', 1200, 3, 1)],
                             preamp_db=-2, scope='playback', eq_enabled=True,
                             spatial_enabled=False, limiter_enabled=False)
    old = w.peq.manager
    w._apply_profile_background(p)
    assert w.review_outcomes == [(True, '')]
    assert w.audio_graph.eq_enabled
    assert w.audio_graph.spatial_name is None and w.audio_graph.limiter_name is None
    assert w.peq.manager is w.audio_graph.owner and not old.is_loaded
    assert w.peq.bands == p.to_bands() and w.peq.preamp_db == -2
    assert w._applied_eq_profile.name == 'Saved full'


def test_eq_only_preserves_spatial_limiter_and_compatible_owner(window):
    w = window
    old = w.peq.manager
    p = EQProfile(name='EQ only', scope='eq', spatial_enabled=False,
                  limiter_enabled=False, preamp_db=-2)
    w._apply_profile_background(p)
    assert w.review_outcomes == [(True, '')]
    assert w.audio_graph.targets == []
    assert w.peq.manager is old and old.is_loaded
    assert w.audio_graph.spatial_name == 'old.spatial'
    assert w.audio_graph.limiter_name == 'old.limiter'
    assert w._applied_eq_profile.limiter_enabled


def test_isolated_candidate_has_final_gain_before_commit_without_late_reload(window):
    w = window
    # Removing a boosted old Spatial must not require a failure-prone postcommit trim.
    w.audio_graph.spatial_peak_db = 9
    old = w.peq.manager
    w._apply_profile_background(EQProfile(name='Direct EQ', spatial_enabled=False,
                                         limiter_enabled=False, scope='playback'))
    assert w.review_outcomes == [(True, '')]
    assert w.peq.manager is w.audio_graph.owner and not old.is_loaded
    assert w.peq.manager.reloads == []
    assert w.peq._last_good_specs[0].params['Mult'] == 1


def test_unlinked_candidate_readback_failure_cleans_owner_and_keeps_previous(window, monkeypatch):
    w = window
    old = w.peq.manager
    monkeypatch.setattr(Owner, 'fail_readback', True)
    w._apply_profile_background(EQProfile(name='Rejected full', scope='playback'))
    assert w.review_outcomes == [(False, 'candidate readback failed')]
    assert w.audio_graph.targets == [] and w.peq.manager is old and old.is_loaded
    assert not Owner.instances[-1].is_loaded


def test_unverified_spatial_update_retains_maximum_reserve(window, monkeypatch):
    w = window
    w.audio_graph.spatial_peak_db = 1
    w.audio_graph.spatial_manager = SimpleNamespace(update_controls=lambda controls: (_ for _ in ()).throw(RuntimeError('restoration unverified')))
    w._live_spatial_pending = ({'branch:Mult': 2}, {'layout': 'Stereo'})
    reserves = []
    monkeypatch.setattr(w, '_on_spatial_changed', reserves.append)
    w._flush_live_spatial()
    assert w.review_outcomes == [(False, 'restoration unverified')]
    assert reserves == [6]


def test_turn_on_eq_uses_current_draft_even_when_cached_owner_exists(window, monkeypatch):
    w = window
    w.audio_graph.eq_enabled = False
    w.peq.bands = [EQBand('peaking', 900, 5, 1)]
    w.peq.set_preamp(-4)
    w._automatic_headroom_enabled = False
    applied = []
    monkeypatch.setattr(w, 'apply_profile', lambda profile, **kwargs: applied.append(profile))
    w.mixer._on_toggle_routing()
    assert len(applied) == 1
    assert applied[0].to_bands() == w.peq.bands and applied[0].preamp_db == -4
    assert applied[0].scope == 'eq' and not applied[0].automatic_headroom


def test_peq_save_captures_bypassed_eq_and_current_stages(window, monkeypatch):
    w = window
    w.audio_graph.eq_enabled = False
    w._automatic_headroom_enabled = False
    w.peq.bands = [EQBand('peaking', 900, 5, 1)]
    w.peq.set_preamp(-4)
    monkeypatch.setattr(w.presets, '_request_save_details', lambda p: ('Bypassed setup', 'playback'))
    w._on_save_profile_requested(w.peq.bands)
    p = storage.load_profile('Bypassed setup')
    assert p.scope == 'playback' and not p.eq_enabled
    assert p.spatial_enabled and p.limiter_enabled
    assert p.to_bands() == w.peq.bands and p.preamp_db == -4
    assert not p.automatic_headroom


def test_full_profile_can_bypass_eq_while_restoring_spatial_and_limiter(window):
    w = window
    p = EQProfile.from_bands('Spatial only setup', [EQBand('peaking', 2200, -3, 1)],
                             preamp_db=-7, scope='playback', eq_enabled=False,
                             spatial_enabled=True, spatial={'layout': 'Stereo', 'wet': 75},
                             limiter_enabled=False)
    w._apply_profile_background(p)
    assert w.review_outcomes == [(True, '')]
    assert not w.audio_graph.eq_enabled
    assert w.audio_graph.spatial_name is not None and w.audio_graph.limiter_name is None
    assert not w.audio_graph.targets[0]['eq_enabled']
    assert w.audio_graph.targets[0]['eq_candidate'] is None
    assert w.peq.bands == p.to_bands() and w.peq.preamp_db == -7
    assert w.spatial._applied_state == p.spatial
