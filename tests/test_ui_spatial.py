"""Offscreen tests for the Spatial tab (Tasks 5, 6, 7)."""

import numpy as np
import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakeExtractor:
    """Synthetic IRExtractor: unit impulses, no pysofa needed."""

    def extract(self, path, azimuths_deg):
        from eqspace.core.dsp.hrtf import ExtractedIRs

        irs = {
            float(az): (np.array([1.0, 0.0]), np.array([1.0, 0.0]))
            for az in azimuths_deg
        }
        return ExtractedIRs(sample_rate=48000.0, irs=irs)


class FakeManager:
    def __init__(self):
        self.loaded = []
        self.unloaded = 0

    @property
    def is_loaded(self):
        return bool(self.loaded)

    def load_args(self, args):
        self.loaded.append(args)
        return 1

    def update_args(self, args):
        self.unloaded += 1
        self.loaded.append(args)
        return 1

    def unload(self):
        self.unloaded += 1
        self.loaded.clear()


@pytest.fixture()
def make_widget(qapp, tmp_path, monkeypatch):
    from eqspace.core.dsp import hrtf as hrtf_module
    from eqspace.ui.spatial import SpatialWidget

    monkeypatch.setattr(hrtf_module, "PySofaExtractor", FakeExtractor)
    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    hrtf_dir = tmp_path / "hrtf"
    hrtf_dir.mkdir()

    def _make(files=("dummy.sofa",), **kwargs):
        for name in files:
            (hrtf_dir / name).write_bytes(b"fake")
        manager = FakeManager()
        widget = SpatialWidget(
            manager=manager,
            hrtf_dir=hrtf_dir,
            pysofa_available=kwargs.pop("pysofa_available", lambda: True),
            **kwargs,
        )
        return widget, manager

    return _make


def test_constructs_and_lists_sofa_files(make_widget):
    widget, _ = make_widget(files=("a.sofa", "b.sofa"))
    # Item 0 is built-in KEMAR, followed by external files
    assert widget.sofa_combo.count() == 3
    assert widget.sofa_combo.itemText(0) == "Synthetic KEMAR model (Built-in)"
    assert widget.apply_button.isEnabled()


def test_no_pysofa_state(make_widget):
    # Built-in KEMAR works without pysofa, but selecting an external file disables apply
    widget, _ = make_widget(files=("external.sofa",), pysofa_available=lambda: False)
    assert widget.apply_button.isEnabled()  # Built-in selected by default

    widget.sofa_combo.setCurrentIndex(1)  # Select external file
    assert not widget.apply_button.isEnabled()
    assert "pysofa" in widget.status_label.text()


def test_no_sofa_files_state(make_widget):
    # Even without external files, built-in KEMAR ensures combo is never empty
    widget, _ = make_widget(files=())
    assert widget.sofa_combo.count() == 1
    assert widget.sofa_combo.itemText(0) == "Synthetic KEMAR model (Built-in)"
    assert widget.apply_button.isEnabled()


def test_apply_loads_51_chain(make_widget, monkeypatch):
    from eqspace.core.dsp import hrtf as hrtf_module

    monkeypatch.setattr(hrtf_module, "PySofaExtractor", FakeExtractor)
    widget, manager = make_widget()
    widget.sofa_combo.setCurrentIndex(0)
    widget.layout_combo.setCurrentText("5.1")
    widget.wetdry_slider.setValue(50)
    args = widget.render_chain_args(widget.selected_sofa())
    widget.apply_button.click()
    assert manager.loaded == []
    assert "convolver" in args
    assert "audio.position = [ FL FR FC LFE SL SR ]" in args
    assert 'media.class = "Audio/Sink"' in args
    assert "conv_FL_L" in args and "conv_SL_L" in args


def test_apply_loads_71_chain(make_widget, monkeypatch):
    from eqspace.core.dsp import hrtf as hrtf_module

    monkeypatch.setattr(hrtf_module, "PySofaExtractor", FakeExtractor)
    widget, manager = make_widget()
    widget.sofa_combo.setCurrentIndex(0)
    widget.layout_combo.setCurrentText("7.1")
    widget.stereo_expansion_check.setChecked(False)
    args = widget.render_chain_args(widget.selected_sofa())
    assert "convolver" in args
    assert "audio.position = [ FL FR FC LFE SL SR RL RR ]" in args
    assert "conv_RL_L" in args and "conv_RR_L" in args
    widget.apply_button.click()
    assert manager.loaded == []
    assert "controller unavailable" in widget.status_label.text()


def test_crossfeed_mode_selection_and_disable(make_widget):
    widget, manager = make_widget()
    assert widget.layout_combo.isEnabled()
    assert not widget.crossfeed_combo.isEnabled()

    widget.crossfeed_check.setChecked(True)
    assert not widget.layout_combo.isEnabled()
    assert widget.crossfeed_combo.isEnabled()
    assert "disabled" in widget.layout_hint_label.text().lower()

    # Bauer mode
    widget.crossfeed_combo.setCurrentText("Bauer")
    widget.apply_button.click()
    assert manager.loaded == []
    assert "controller unavailable" in widget.status_label.text()

    # Meier mode
    widget.crossfeed_combo.setCurrentText("Meier")
    widget.apply_button.click()
    assert manager.loaded == []
    assert "controller unavailable" in widget.status_label.text()


def test_unload(make_widget):
    widget, manager = make_widget()
    widget.sofa_combo.setCurrentIndex(0)
    widget.apply_button.click()
    widget.unload_button.click()
    assert manager.unloaded == 0
    assert "controller unavailable" in widget.status_label.text()


def test_apply_holospace_3d_layout(make_widget):
    widget, manager = make_widget()
    widget.layout_combo.setCurrentText("HoloSpace 3D")
    args = widget.render_chain_args(widget.selected_sofa())
    assert "conv_FC_L" in args
    assert "audio.channels = 2" in args
    widget.apply_button.click()
    assert manager.loaded == []
    assert "controller unavailable" in widget.status_label.text()


def test_profile_combo_options_and_default(make_widget):
    widget, _ = make_widget()
    assert widget.profile_combo.count() == 7
    expected_profiles = [
        "HoloSpace 3D (Signature Spatial Immersion)",
        "Cinema 7.1 Surround (Virtual Room)",
        "Natural Crossfeed — Bauer (Fatigue-Free Stereo)",
        "Natural Crossfeed — Meier (Warm Acoustic Blend)",
        "Studio Monitor (Nearfield ±30°)",
        "Custom SOFA Profile (External File)",
        "HS+ (Experimental)",
    ]
    items = [widget.profile_combo.itemText(i) for i in range(widget.profile_combo.count())]
    assert items == expected_profiles
    assert widget.profile_combo.currentText() == "HoloSpace 3D (Signature Spatial Immersion)"
    assert widget.info_frame.objectName() == "spatialInfoCard"
    assert widget.custom_sofa_container.isHidden()
    assert "holographic" in widget.info_desc_label.text().lower()


def test_info_card_content_updates(make_widget):
    widget, _ = make_widget()

    widget.profile_combo.setCurrentText("Cinema 7.1 Surround (Virtual Room)")
    assert "synthetic" in widget.info_desc_label.text().lower()
    assert "7.1" in widget.info_desc_label.text()

    widget.profile_combo.setCurrentText("Natural Crossfeed — Bauer (Fatigue-Free Stereo)")
    assert "low frequencies" in widget.info_desc_label.text().lower()
    assert "250µs" not in widget.info_desc_label.text()

    widget.profile_combo.setCurrentText("Natural Crossfeed — Meier (Warm Acoustic Blend)")
    assert "meier" in widget.info_desc_label.text().lower() or "warm" in widget.info_desc_label.text().lower()

    widget.profile_combo.setCurrentText("Studio Monitor (Nearfield ±30°)")
    assert "nearfield" in widget.info_desc_label.text().lower()

    widget.profile_combo.setCurrentText("Custom SOFA Profile (External File)")
    assert "custom head-related transfer function" in widget.info_desc_label.text().lower()


def test_custom_sofa_container_visibility_toggle(make_widget):
    widget, _ = make_widget()
    assert widget.custom_sofa_container.isHidden()

    widget.profile_combo.setCurrentText("Custom SOFA Profile (External File)")
    assert not widget.custom_sofa_container.isHidden()

    widget.profile_combo.setCurrentText("HoloSpace 3D (Signature Spatial Immersion)")
    assert widget.custom_sofa_container.isHidden()


def test_two_way_synchronization(make_widget):
    widget, _ = make_widget(files=("test_ext.sofa",))

    # Profile -> Controls
    widget.profile_combo.setCurrentText("Natural Crossfeed — Bauer (Fatigue-Free Stereo)")
    assert widget.crossfeed_check.isChecked()
    assert widget.crossfeed_combo.currentText() == "Bauer"

    widget.profile_combo.setCurrentText("Natural Crossfeed — Meier (Warm Acoustic Blend)")
    assert widget.crossfeed_check.isChecked()
    assert widget.crossfeed_combo.currentText() == "Meier"

    widget.profile_combo.setCurrentText("Cinema 7.1 Surround (Virtual Room)")
    assert not widget.crossfeed_check.isChecked()
    assert widget.layout_combo.currentText() == "7.1"

    widget.profile_combo.setCurrentText("Studio Monitor (Nearfield ±30°)")
    assert not widget.crossfeed_check.isChecked()
    assert widget.layout_combo.currentText() == "Stereo"

    # Controls -> Profile
    widget.crossfeed_check.setChecked(True)
    widget.crossfeed_combo.setCurrentText("Meier")
    assert widget.profile_combo.currentText() == "Natural Crossfeed — Meier (Warm Acoustic Blend)"

    widget.crossfeed_check.setChecked(False)
    widget.layout_combo.setCurrentText("HoloSpace 3D")
    assert widget.profile_combo.currentText() == "HoloSpace 3D (Signature Spatial Immersion)"

    # External SOFA selection -> Custom SOFA Profile
    widget.sofa_combo.setCurrentIndex(1)
    assert widget.profile_combo.currentText() == "Custom SOFA Profile (External File)"
    assert not widget.custom_sofa_container.isHidden()


def test_apply_without_graph_owner_never_loads_or_unloads_chain(make_widget):
    widget, manager = make_widget()
    # First apply
    widget.apply_button.click()
    assert manager.loaded == []
    assert manager.unloaded == 0

    # User changes profile and applies again without clicking Unload
    widget.profile_combo.setCurrentText("Cinema 7.1 Surround (Virtual Room)")
    widget.apply_button.click()
    assert manager.unloaded == 0
    assert manager.loaded == []
    assert "controller unavailable" in widget.status_label.text()


def test_zero_crash_apply_on_failure(make_widget):
    widget, manager = make_widget()
    from types import SimpleNamespace

    def failing_switch(args, peak_db):
        raise RuntimeError("PipeWire daemon unreachable")

    widget.graph_controller = SimpleNamespace(
        registry=SimpleNamespace(graph_rate=lambda required: 48000),
        switch_spatial=failing_switch,
    )
    widget.apply_button.click()
    assert "Apply failed: PipeWire daemon unreachable" in widget.status_label.text()


def test_set_and_get_state_profile(make_widget):
    widget, _ = make_widget()
    widget.set_state({
        "layout": "7.1",
        "wet": 80,
        "crossfeed": False,
        "profile": "Cinema 7.1 Surround (Virtual Room)",
    })
    assert widget.profile_combo.currentText() == "Cinema 7.1 Surround (Virtual Room)"
    assert widget.layout_combo.currentText() == "7.1"
    assert widget.wetdry_slider.value() == 80

    state = widget.get_state()
    assert state["profile"] == "Cinema 7.1 Surround (Virtual Room)"
    assert state["layout"] == "7.1"
    assert state["wet"] == 80


def test_cinema_stereo_expansion_and_native_input(make_widget):
    from eqspace.core.dsp.hrtf import builtin_kemar_path
    widget, _ = make_widget()
    widget.profile_combo.setCurrentText('Cinema 7.1 Surround (Virtual Room)')
    assert widget.stereo_expansion_check.isChecked()
    args = widget.render_chain_args(builtin_kemar_path())
    assert 'copy_FL:In' in args and 'copy_FR:In' in args
    assert 'audio.channels = 8' not in args
    widget.stereo_expansion_check.setChecked(False)
    args = widget.render_chain_args(builtin_kemar_path())
    assert 'audio.channels = 8' in args
    state = widget.get_state()
    widget.stereo_expansion_check.setChecked(True)
    widget.set_state(state)
    assert not widget.stereo_expansion_check.isChecked()


def test_busy_apply_rejects_external_spatial_mutations(make_widget):
    widget, manager = make_widget(files=())
    class Graph:
        def switch_spatial(self, *args):
            raise AssertionError('external Spatial Apply must not mutate graph')
        def spatial_off(self):
            raise AssertionError('external Spatial Off must not mutate graph')
    widget.graph_controller = Graph()
    widget.mutation_allowed = lambda: False
    widget.refresh_mutation_controls()
    widget.apply()
    widget.unload()
    widget._update_availability()
    assert not widget.apply_button.isEnabled()
    assert not widget.unload_button.isEnabled()
    assert not manager.loaded
    widget.mutation_allowed = lambda: True
    widget.refresh_mutation_controls()
    assert widget.apply_button.isEnabled()
    assert widget.unload_button.isEnabled()


def test_internal_spatial_apply_is_permitted_during_profile_apply(make_widget):
    widget, _ = make_widget(files=())
    calls = []
    class Registry:
        def graph_rate(self, required=False):
            return 48000
    class Graph:
        registry = Registry()
        def switch_spatial(self, *args):
            calls.append(args)
    widget.graph_controller = Graph()
    widget.mutation_allowed = lambda: False
    widget.apply(internal=True)
    assert len(calls) == 1


@pytest.mark.parametrize('status', ['Spatial active · route verified', 'Apply failed: channel missing'])
def test_busy_control_refresh_preserves_spatial_result(make_widget, status):
    widget, _ = make_widget(files=())
    widget.status_label.setText(status)
    widget.mutation_allowed = lambda: False
    widget.refresh_mutation_controls()
    assert widget.status_label.text() == status
    assert not widget.apply_button.isEnabled()
    widget.mutation_allowed = lambda: True
    widget.refresh_mutation_controls()
    assert widget.status_label.text() == status
    assert widget.apply_button.isEnabled()


def test_hybrid_selection_explicit_apply_and_live_controls(make_widget):
    from eqspace.ui.spatial.spatial_widget import PROFILE_HYBRID
    widget, manager = make_widget(files=())
    calls = []
    widget.on_live_controls_changed = lambda controls, peak: calls.append((controls, peak))
    widget.profile_combo.setCurrentText(PROFILE_HYBRID)
    assert not hasattr(widget, 'hybrid_balance_slider')
    assert not hasattr(widget, 'crossover_slider')
    assert not hasattr(widget, 'dev_controls_group')
    assert 'experimental' in widget.info_desc_label.text().lower()
    assert '60%' not in widget.info_desc_label.text() and '1400' not in widget.info_desc_label.text()
    assert not calls and not manager.loaded
    from types import SimpleNamespace
    widget.graph_controller = SimpleNamespace(
        registry=SimpleNamespace(graph_rate=lambda required: 48000),
        switch_spatial=lambda args, peak: None,
    )
    widget.apply()
    widget.wetdry_slider.setValue(50)
    assert calls and calls[-1][0]['hybrid_l:Gain 1'] == .3
    assert calls[-1][0]['hybrid_l:Gain 2'] == .2
    widget.wetdry_slider.setValue(0)
    assert all(value == 0 for value in calls[-1][0].values())
    state = widget.get_state()
    assert 'hybrid_balance' not in state and 'crossover_hz' not in state
    widget.set_state(dict(state, hybrid_balance='obsolete', crossover_hz=500))
    assert widget.get_state() == state


def test_hybrid_dispatcher_takes_snapshot_and_preserves_applied_controls(make_widget):
    from eqspace.ui.spatial.spatial_widget import PROFILE_HYBRID
    from types import SimpleNamespace
    widget, _ = make_widget(files=())
    jobs = []
    widget.action_dispatcher = lambda label, backend, finished: jobs.append((backend, finished))
    switched = []
    widget.graph_controller = SimpleNamespace(
        registry=SimpleNamespace(graph_rate=lambda required: 48000),
        switch_spatial=lambda args, peak: switched.append(args),
    )
    widget.profile_combo.setCurrentText(PROFILE_HYBRID)
    widget.apply()
    widget.wetdry_slider.setValue(20)
    backend, finished = jobs.pop()
    result = backend()
    finished(True, '', result)
    assert '"Gain 1" = 0.6 "Gain 2" = 0.4' in switched[0]
    assert widget._applied_state['wet'] == 100
