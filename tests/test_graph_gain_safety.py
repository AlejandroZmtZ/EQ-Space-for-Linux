"""Applied gain state and UI mutation serialization regressions."""
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication
from eqspace.core.dsp.filter_design import EQBand
from eqspace.core.filterchain.manager import FilterSpec
from eqspace.core.pipewire.registry import PwSnapshot
from eqspace.core.profiles.models import EQProfile
from eqspace.ui.main_window import MainWindow

@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    class Registry:
        def snapshot(self): return PwSnapshot()
    w = MainWindow(registry=Registry(), poll_interval_ms=0, restore_profile=False)
    yield w
    w.peq.manager = SimpleNamespace(is_loaded=False)
    w.audio_graph.eq_enabled = False
    w.audio_graph.limiter_name = None
    w.close_completely()

class RecordingEQ:
    is_loaded = True
    node_name = 'eqspace.eq.test'
    def __init__(self): self.loaded = []
    def reload(self, specs): self.loaded.append(specs)
    def verify_controls(self, specs): pass


def test_spatial_gain_uses_applied_snapshot_not_unapplied_editor(window):
    w=window; eq=RecordingEQ(); w.peq.manager=eq
    w._applied_eq_profile=EQProfile.from_bands('Applied Flat', [], automatic_headroom=True)
    w._automatic_headroom_enabled=True;w.audio_graph.eq_enabled=True
    w.peq._last_good_specs=[FilterSpec('preamp','linear',{'Mult':1.,'Add':0.})]
    w.peq.bands=[EQBand('peaking',1000,9,1)];w.peq.preamp_db=6
    w._on_spatial_changed(3)
    assert len(eq.loaded[-1]) == 1
    assert eq.loaded[-1][0].params['Mult'] == pytest.approx(10**(-4/20))
    assert w.peq.bands[0].gain_db == 9 and w.peq.preamp_db == 6


def test_bypassed_eq_gets_conservative_gain_before_limiter_removal(window):
    w=window;eq=RecordingEQ();w.peq.manager=eq
    w._applied_eq_profile=EQProfile.from_bands('Applied Flat', [], preamp_db=6, automatic_headroom=True)
    w._automatic_headroom_enabled=True;w.audio_graph.eq_enabled=False
    w.peq._last_good_specs=[FilterSpec('preamp','linear',{'Mult':10**(6/20),'Add':0.})]
    w._on_spatial_changed(0, limiter_enabled=False)
    assert eq.loaded[-1][0].params['Mult'] == pytest.approx(10**(-1/20))


def test_limiter_toggle_rejected_during_apply(window, monkeypatch):
    calls=[];w=window;w._profile_apply_busy=True
    monkeypatch.setattr(w,'_change_limiter',lambda enabled:calls.append(enabled))
    w._on_limiter_toggled(True)
    assert calls == []
    assert 'progress' in w.peq.status_label.text()
    w._profile_apply_busy=False


def test_failed_profile_restores_limiter_before_republishing_driven_eq(window, monkeypatch):
    from eqspace.core.filterchain.manager import FilterChainManager
    w=window;manager=FilterChainManager();manager._module_id=99
    old=[FilterSpec('preamp','linear',{'Mult':10**(6/20),'Add':0.})]
    manager._active_filters=tuple(old);w.peq.manager=manager;w.peq._last_good_specs=old
    w.peq.preamp_db=6;w.peq.bands=[];w.peq.auto_trim_db=0
    w._applied_eq_profile=EQProfile.from_bands('Old', [], preamp_db=6,limiter_enabled=True)
    w._automatic_headroom_enabled=True
    loaded=[old];events=[]
    monkeypatch.setattr(manager,'reload',lambda specs:loaded.append(specs))
    monkeypatch.setattr(manager,'verify_controls',lambda specs:None)
    graph=SimpleNamespace(eq_enabled=True,limiter_name='old.limiter',spatial_name=None,spatial_peak_db=0.)
    def eq_applied():
        events.append((bool(graph.limiter_name),loaded[-1][0].params['Mult']))
        graph.eq_enabled=True
    graph.eq_applied=eq_applied
    def limiter(enabled,cap):graph.limiter_name='restored.limiter' if enabled else None
    graph.set_limiter=limiter
    graph.eq_off=lambda:setattr(graph,'eq_enabled',False)
    w.audio_graph=graph
    monkeypatch.setattr(w.mixer,'is_routed',lambda:True)
    monkeypatch.setattr(w.mixer,'refresh',lambda:None)
    monkeypatch.setattr(w,'_sync_limiter_ui',lambda:None)
    def apply(*,async_mode,on_done):
        specs=w.peq._filter_specs();loaded.append(specs);w.peq._last_good_specs=specs
        on_done(True,'Applied');return True
    monkeypatch.setattr(w.peq,'apply',apply)
    def fail_spatial(**kwargs):raise RuntimeError('Spatial rejected')
    monkeypatch.setattr(w.spatial,'apply',fail_spatial)
    requested=EQProfile.from_bands('New',[],limiter_enabled=False,spatial_enabled=True)
    assert not w.apply_profile(requested)
    assert graph.limiter_name is not None
    assert all(protected or gain <= 1.0 for protected,gain in events)
    assert loaded[-1][0].params['Mult'] == pytest.approx(old[0].params['Mult'])
    graph.has_owned_modules=False


def test_live_edits_coalesce_and_wait_for_apply(window, monkeypatch):
    w = window
    w.peq.manager = RecordingEQ()
    w.audio_graph.eq_enabled = True
    calls = []
    monkeypatch.setattr(w, '_on_manual_peq_apply', lambda: calls.append(w.peq.bands[0]))
    w.peq.set_band(0, freq_hz=2500, gain_db=3)
    w.peq.set_band(0, freq_hz=3000, gain_db=6)
    assert calls == [] and w._live_eq_timer.isActive()
    w._profile_apply_busy = True
    w._flush_live_eq()
    assert calls == [] and w._live_eq_pending
    w._profile_apply_busy = False
    w._flush_live_eq()
    assert len(calls) == 1 and calls[0].freq_hz == 3000 and calls[0].gain_db == 6
    assert not w._live_eq_pending
    w._live_eq_timer.stop()


def test_live_edit_does_not_enable_bypassed_eq(window, monkeypatch):
    w = window
    w.peq.manager = RecordingEQ()
    w.audio_graph.eq_enabled = False
    calls = []
    monkeypatch.setattr(w, '_on_manual_peq_apply', lambda: calls.append(True))
    w.peq.set_band(0, gain_db=6)
    w._flush_live_eq()
    assert calls == [] and not w._live_eq_pending
