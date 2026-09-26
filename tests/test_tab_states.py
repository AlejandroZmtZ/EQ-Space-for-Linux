"""Tests for SpatialWidget and MicWidget state export and import."""

import pytest
from PySide6.QtWidgets import QApplication

from eqspace.ui.spatial.spatial_widget import SpatialWidget
from eqspace.ui.mic.mic_widget import MicWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestSpatialWidgetState:
    def test_get_and_set_state(self, qapp, tmp_path):
        sofa_file = tmp_path / "test.sofa"
        sofa_file.write_text("fake sofa")

        widget = SpatialWidget(hrtf_dir=tmp_path, pysofa_available=lambda: True)
        assert widget.sofa_combo.count() == 2
        widget.sofa_combo.setCurrentIndex(1)

        state = widget.get_state()
        assert "layout" in state
        assert "wet" in state
        assert "crossfeed" in state
        assert "sofa_path" in state
        assert state["layout"] == "HoloSpace 3D"
        assert state["wet"] == 100
        assert state["crossfeed"] is False
        assert state["sofa_path"] == str(sofa_file)

        # Mutate via set_state
        widget.set_state({
            "layout": "5.1",
            "wet": 45,
            "crossfeed": True,
            "sofa_path": str(sofa_file),
        })

        assert widget.layout_combo.currentText() == "5.1"
        assert widget.wetdry_slider.value() == 45
        assert widget.crossfeed_check.isChecked() is True

        new_state = widget.get_state()
        assert new_state["layout"] == "5.1"
        assert new_state["wet"] == 45
        assert new_state["crossfeed"] is True

    def test_set_state_partial(self, qapp, tmp_path):
        widget = SpatialWidget(hrtf_dir=tmp_path, pysofa_available=lambda: False)
        widget.set_state({"wet": 30})
        assert widget.wetdry_slider.value() == 30
        assert widget.layout_combo.currentText() == "HoloSpace 3D"


class TestMicWidgetState:
    def test_get_and_set_state(self, qapp):
        widget = MicWidget(deepfilternet_available_fn=lambda: True)
        state = widget.get_state()
        assert state == {"enabled": False, "strength": 100}

        widget.set_state({"enabled": True, "strength": 65})
        assert widget.nr_check.isChecked() is True
        assert widget.strength_slider.value() == 65
        assert widget.strength_label.text() == "65%"

        assert widget.get_state() == {"enabled": True, "strength": 65}

    def test_set_state_partial(self, qapp):
        widget = MicWidget(deepfilternet_available_fn=lambda: True)
        widget.set_state({"enabled": True})
        assert widget.nr_check.isChecked() is True
        assert widget.strength_slider.value() == 100
