import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakeManager:
    def __init__(self):
        self.node_name = "eqspace.filter-chain"
        self.is_loaded = False
        self.loaded = []
        self.params = []

    def load(self, specs):
        self.loaded.append(list(specs))
        self.is_loaded = True
        return 42

    def set_filter_param(self, node_name, param, value):
        self.params.append((node_name, param, value))


def _make(qapp, manager=None):
    from eqspace.ui.peq import PeqWidget

    widget = PeqWidget(manager=manager if manager is not None else FakeManager())
    return widget


def test_constructs_offscreen_with_one_default_band(qapp):
    widget = _make(qapp)
    assert len(widget.bands) == 1
    assert widget.table.rowCount() == 1
    assert widget.curve_updates == 1


def test_add_and_remove_band_updates_model(qapp):
    widget = _make(qapp)
    updates = widget.curve_updates
    widget.add_band()
    assert len(widget.bands) == 2
    assert widget.table.rowCount() == 2
    assert widget.curve_updates == updates + 1
    widget.table.selectRow(0)
    widget.remove_selected_band()
    assert len(widget.bands) == 1


def test_add_band_capped_at_16(qapp):
    widget = _make(qapp)
    for _ in range(30):
        widget.add_band()
    assert len(widget.bands) == 16
    assert widget.add_button.isEnabled() is False


def test_enable_toggle_updates_model(qapp):
    widget = _make(qapp)
    check = widget.table.cellWidget(0, 4)
    check.setChecked(False)
    assert widget.bands[0].enabled is False
    assert [spec.name for spec in widget._filter_specs()] == ["preamp"]


def test_table_edits_update_model(qapp):
    widget = _make(qapp)
    widget.table.cellWidget(0, 1).setValue(250.0)
    widget.table.cellWidget(0, 2).setValue(6.0)
    widget.table.cellWidget(0, 3).setValue(2.5)
    widget.table.cellWidget(0, 0).setCurrentText("low_shelf")
    band = widget.bands[0]
    assert band.freq_hz == 250.0
    assert band.gain_db == 6.0
    assert band.q == 2.5
    assert band.band_type == "low_shelf"


def test_curve_recomputed_on_band_change(qapp):
    widget = _make(qapp)
    updates = widget.curve_updates
    widget.set_band(0, gain_db=6.0)
    assert widget.curve_updates == updates + 1


def test_handle_drag_and_wheel_update_band(qapp):
    widget = _make(qapp)
    widget._on_handle_dragged(0, 500.0, 3.0)
    assert widget.bands[0].freq_hz == 500.0
    assert widget.bands[0].gain_db == 3.0
    widget._on_handle_wheeled(0, 1.0)
    assert widget.bands[0].q == pytest.approx(1.1)


def test_apply_loads_filter_chain(qapp):
    manager = FakeManager()
    widget = _make(qapp, manager)
    widget.apply()
    assert len(manager.loaded) == 1
    assert manager.loaded[0][0].filter_type == "linear"
    spec = manager.loaded[0][1]
    assert spec.name == "band_0"
    assert spec.filter_type == "bq_peaking"
    assert spec.params["Freq"] == 1000.0


def test_apply_when_loaded_uses_live_params(qapp):
    manager = FakeManager()
    manager.is_loaded = True
    widget = _make(qapp, manager)
    widget.apply()
    keys = [p[1] for p in manager.params]
    assert keys == ["preamp:Mult", "preamp:Add", "band_0:Freq", "band_0:Gain", "band_0:Q"]
    assert all(p[0] == "eqspace.filter-chain" for p in manager.params)


def test_save_profile_emits_signal(qapp):
    widget = _make(qapp)
    received = []
    widget.save_profile_requested.connect(received.append)
    widget.save_button.click()
    assert len(received) == 1
    assert received[0] == widget.bands


class FakeManagerWithReload(FakeManager):
    def __init__(self):
        super().__init__()
        self.reloaded = []

    def reload(self, specs):
        self.reloaded.append(list(specs))
        return 43


def test_apply_when_loaded_uses_reload_if_available(qapp):
    manager = FakeManagerWithReload()
    manager.is_loaded = True
    widget = _make(qapp, manager)
    widget.apply()
    assert len(manager.reloaded) == 1
    assert len(manager.params) == 0
    assert widget.status_label.text() == "Applied ✓"


def test_band_edit_marks_preview_without_changing_applied_audio(qapp):
    widget = _make(qapp)
    assert widget.apply()
    widget.set_band(0, freq_hz=3000.0, gain_db=6.0)
    assert "click Apply" in widget.status_label.text()
    assert widget.manager.loaded[-1][1].params["Freq"] == 1000.0
    assert widget.manager.params == []


def test_dragged_presence_boost_applies_correct_frequency_and_response(qapp):
    import numpy as np
    from eqspace.core.dsp.biquads import magnitude_response
    from eqspace.core.dsp.filter_design import design_filters

    widget = _make(qapp)
    widget._on_handle_dragged(0, 3000.0, 6.0)
    assert widget.apply()
    spec = widget.manager.loaded[-1][1]
    assert spec.params == {"Freq": 3000.0, "Gain": 6.0, "Q": 1.0}
    response = magnitude_response(design_filters(widget.bands, widget.fs), np.array([100., 3000., 10000.]), widget.fs)
    assert response[1] == pytest.approx(6.0)
    assert response[0] < 0.1
    assert response[2] < 1.0
