import pytest

from eqspace.core.pipewire.registry import PwNode, PwSnapshot


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakeControl:
    def __init__(self):
        self.calls = []

    def set_volume(self, node_id, volume):
        self.calls.append(("set_volume", node_id, volume))

    def set_mute(self, node_id, mute):
        self.calls.append(("set_mute", node_id, mute))

    def set_default_sink(self, name):
        self.calls.append(("set_default_sink", name))

    def move_stream(self, stream_id, sink_id):
        self.calls.append(("move_stream", stream_id, sink_id))


class FakeRegistry:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


def _snapshot():
    return PwSnapshot(
        sinks=(
            PwNode(40, "alsa_output.pci", "Built-in Audio", "Audio/Sink", 1.0, False),
            PwNode(41, "alsa_output.usb", "USB DAC", "Audio/Sink", 1.0, False),
        ),
        streams=(
            PwNode(60, "firefox.playback", "Firefox", "Stream/Output/Audio", 0.8, False),
            PwNode(61, "mpv.playback", "mpv", "Stream/Output/Audio", 0.5, True),
        ),
    )


def _make(qapp):
    from eqspace.ui.mixer import MixerWidget

    control = FakeControl()
    widget = MixerWidget(
        registry=FakeRegistry(_snapshot()), control=control, poll_interval_ms=0
    )
    # The initial refresh is deferred via QTimer.singleShot(0, ...).
    qapp.processEvents()
    return widget, control


def test_populates_stream_rows_from_registry(qapp):
    widget, _ = _make(qapp)
    assert len(widget.stream_rows) == 2
    assert widget.stream_rows[0].name_label.text() == "Firefox"
    assert widget.stream_rows[0].slider.value() == 80
    assert widget.stream_rows[1].mute_button.isChecked() is True


def test_populates_device_selector(qapp):
    widget, _ = _make(qapp)
    assert widget.device_combo.count() == 2
    assert widget.device_combo.itemText(0) == "Built-in Audio"
    # Stream rows offer the same sinks as targets.
    assert widget.stream_rows[0].sink_combo.count() == 2


def test_slider_change_calls_set_volume(qapp):
    widget, control = _make(qapp)
    widget.stream_rows[0].slider.setValue(50)
    assert ("set_volume", 60, 0.5) in control.calls


def test_mute_toggle_calls_set_mute(qapp):
    widget, control = _make(qapp)
    widget.stream_rows[0].mute_button.setChecked(True)
    assert ("set_mute", 60, True) in control.calls


def test_device_selection_sets_default_sink(qapp):
    widget, control = _make(qapp)
    widget.device_combo.setCurrentIndex(1)
    assert ("set_default_sink", "alsa_output.usb") in control.calls


def test_master_volume_targets_selected_sink(qapp):
    widget, control = _make(qapp)
    control.calls.clear()
    widget.master_slider.setValue(40)
    assert ("set_volume", 40, 0.4) in control.calls


def test_refresh_with_updated_registry_rebuilds_rows(qapp):
    from eqspace.ui.mixer import MixerWidget

    registry = FakeRegistry(_snapshot())
    widget = MixerWidget(registry=registry, control=FakeControl(), poll_interval_ms=0)
    registry._snapshot = PwSnapshot(
        sinks=registry._snapshot.sinks,
        streams=(PwNode(62, "spotify.playback", "Spotify", "Stream/Output/Audio", 1.0, False),),
    )
    widget.refresh()
    assert len(widget.stream_rows) == 1
    assert widget.stream_rows[0].name_label.text() == "Spotify"
