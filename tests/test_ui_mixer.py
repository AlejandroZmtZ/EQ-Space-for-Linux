"""Offscreen checks for the simplified Mixer and safe output selection."""

import time

import pytest

from eqspace.core.pipewire.registry import PwNode, PwSnapshot
from eqspace.ui.mixer import MixerWidget, sink_display_name
from eqspace.ui.mixer.mixer_widget import format_vol_db


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class FakeControl:
    def __init__(self, default="alsa_output.usb", routed=False):
        self.default = default
        self.routed = routed
        self.fail_output = False
        self.fail_route = False
        self.fail_mute = False
        self.fail_volume = False
        self.calls = []
        self.volumes = {40: 0.75, 41: 0.37, 60: 0.8, 61: 0.5}

    def get_volume(self, node_id):
        return self.volumes[node_id]

    def set_volume(self, node_id, volume):
        if self.fail_volume:
            raise RuntimeError("volume rejected")
        self.calls.append(("set_volume", node_id, volume))
        self.volumes[node_id] = volume

    def set_mute(self, node_id, mute):
        if self.fail_mute:
            raise RuntimeError("mute rejected")
        self.calls.append(("set_mute", node_id, mute))

    def set_default_sink(self, name, **kwargs):
        if self.fail_output:
            raise RuntimeError("device unavailable")
        self.default = name
        self.calls.append(("set_default_sink", name))

    def get_default_sink_name(self, **kwargs):
        return "EQ-Space virtual sink" if self.routed else self.default

    def is_system_routed(self, **kwargs):
        return self.routed

    def set_system_routing(self, enable, **kwargs):
        if self.fail_route or (not enable and self.fail_output):
            raise RuntimeError("route rejected")
        self.routed = enable
        if not enable and kwargs.get("fallback_sink_name"):
            self.default = kwargs["fallback_sink_name"]
        self.calls.append(("set_system_routing", enable, kwargs.get("fallback_sink_name")))
        return enable

    def link_filter_output(self, filter_node_name, target_sink_name, **kwargs):
        if self.fail_output:
            raise RuntimeError("device unavailable")
        self.default = target_sink_name
        self.calls.append(("link_filter_output", filter_node_name, target_sink_name))

    def get_active_output_links(self):
        if not self.routed:
            return {}
        return {
            "eqspace.filter-chain.playback:output_FL": [f"{self.default}:playback_FL"],
            "eqspace.filter-chain.playback:output_FR": [f"{self.default}:playback_FR"],
        }


class FakeRegistry:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


def test_poll_started_before_output_change_is_discarded(qapp, monkeypatch):
    widget = MixerWidget(registry=FakeRegistry(PwSnapshot()), control=FakeControl(), poll_interval_ms=0)
    widget._observing_revision = 0
    widget._refresh_worker = object()
    rendered, retries = [], []
    monkeypatch.setattr(widget, '_render_snapshot', lambda snapshot: rendered.append(snapshot))
    monkeypatch.setattr(widget, 'refresh', lambda: retries.append(True))
    widget.invalidate_observation()
    widget._finish_observation(True, '', {'snapshot': PwSnapshot(), 'volume': .37})
    assert not rendered
    assert retries == [True]
    assert widget._refresh_worker is None
    widget.close()


def _snapshot(with_chain=False):
    sinks = [
        PwNode(40, "alsa_output.pci", "Built-in Audio", "Audio/Sink", 1.0, False),
        PwNode(41, "alsa_output.usb", "USB DAC", "Audio/Sink", 1.0, False),
    ]
    if with_chain:
        sinks.append(PwNode(95, "eqspace.filter-chain", None, "Audio/Sink", 1.0, False))
    return PwSnapshot(
        sinks=tuple(sinks),
        streams=(
            PwNode(60, "firefox.playback", "Firefox", "Stream/Output/Audio", 0.8, False),
            PwNode(61, "mpv.playback", "mpv", "Stream/Output/Audio", 0.5, True),
        ),
    )


def _make(qapp, with_chain=False, routed=False):
    control = FakeControl(routed=routed)
    registry = FakeRegistry(_snapshot(with_chain))
    widget = MixerWidget(registry=registry, control=control, poll_interval_ms=0)
    qapp.processEvents()
    return widget, control, registry


def test_output_list_contains_only_physical_devices_and_selects_real_default(qapp):
    widget, control, _ = _make(qapp, with_chain=True)
    assert [widget.device_combo.itemData(i) for i in range(widget.device_combo.count())] == [
        "alsa_output.pci", "alsa_output.usb"
    ]
    assert widget.device_combo.currentData() == "alsa_output.usb"
    assert widget.selected_output_name == "alsa_output.usb"
    assert widget.master_slider.value() == 37
    assert widget.routing_status_label.text() == "EQ off"
    assert not any(call[0] == "set_default_sink" for call in control.calls)


def test_one_system_route_control_and_no_duplicate_status(qapp):
    widget, _, _ = _make(qapp, with_chain=True)
    assert widget.routing_button.text() == "Turn on EQ"
    assert not hasattr(widget, "routing_badge")
    assert not hasattr(widget, "sink_status_badge")
    assert not hasattr(widget.stream_rows[0], "eq_button")
    assert not hasattr(widget.stream_rows[0], "status_indicator")
    assert not hasattr(widget.stream_rows[0], "sink_combo")


def test_eq_button_disabled_until_chain_exists(qapp):
    widget, _, _ = _make(qapp)
    assert not widget.routing_button.isEnabled()
    assert "Apply a preset" in widget.routing_button.toolTip()


def test_turn_on_eq_keeps_output_selection_enabled(qapp):
    widget, control, _ = _make(qapp, with_chain=True)
    route_states = []
    widget.routing_changed.connect(route_states.append)
    widget.routing_button.click()
    assert control.routed
    assert widget.routing_status_label.text() == "EQ on"
    assert widget.routing_button.text() == "Use direct output"
    assert widget.device_combo.isEnabled()
    assert widget.device_hint.isHidden()
    assert widget.device_combo.count() == 2
    widget.routing_button.click()
    assert not control.routed
    assert widget.device_combo.isEnabled()
    assert widget.device_hint.isHidden()
    assert control.calls[-1] == ("set_system_routing", False, "alsa_output.usb")
    assert route_states == [True, False]


def test_eq_status_exposes_app_still_using_direct_output(qapp):
    widget, control, _ = _make(qapp, with_chain=True, routed=True)
    control.get_active_output_links = lambda: {
        "eqspace.filter-chain.playback:output_FL": ["alsa_output.usb:playback_FL"],
        "eqspace.filter-chain.playback:output_FR": ["alsa_output.usb:playback_FR"],
        "firefox.playback:output_FL": ["alsa_output.pci:playback_FL"],
        "firefox.playback:output_FR": ["alsa_output.pci:playback_FR"],
    }
    widget.refresh()
    assert widget.routing_status_label.text() == "EQ on · 1 app not using EQ"
    assert widget.device_combo.currentData() == "alsa_output.usb"


def test_eq_status_exposes_disconnected_output(qapp):
    widget, control, _ = _make(qapp, with_chain=True, routed=True)
    control.get_active_output_links = lambda: {
        "firefox.playback:output_FL": ["eqspace.filter-chain:playback_FL"],
    }
    widget.refresh()
    assert widget.routing_status_label.text() == "EQ on · Output disconnected"


def test_eq_status_does_not_claim_disconnection_if_link_inspection_fails(qapp):
    widget, control, _ = _make(qapp, with_chain=True, routed=True)

    def failing():
        raise RuntimeError("pw-link unavailable")

    control.get_active_output_links = failing
    widget.refresh()
    assert widget.routing_status_label.text() == "EQ on · Route unverified"


def test_direct_status_exposes_app_on_wrong_device(qapp):
    widget, control, _ = _make(qapp)
    control.get_active_output_links = lambda: {
        "firefox.playback:output_FL": ["alsa_output.pci:playback_FL"],
        "firefox.playback:output_FR": ["alsa_output.pci:playback_FR"],
    }
    widget.refresh()
    assert widget.routing_status_label.text() == "EQ off · 1 app on another device"


def test_change_physical_output_when_direct(qapp):
    widget, control, _ = _make(qapp, with_chain=True)
    widget.device_combo.setCurrentIndex(0)
    assert ("set_system_routing", False, "alsa_output.pci") in control.calls
    assert widget.selected_output_name == "alsa_output.pci"
    assert widget.master_slider.value() == 75
    assert widget.routing_status_label.text() == "EQ off"


def test_change_physical_output_when_routed(qapp):
    widget, control, _ = _make(qapp, with_chain=True, routed=True)
    assert widget.device_combo.isEnabled()
    widget.device_combo.setCurrentIndex(0)
    assert ("link_filter_output", "eqspace.filter-chain", "alsa_output.pci") in control.calls
    assert widget.selected_output_name == "alsa_output.pci"
    assert widget.master_slider.value() == 75
    assert widget.routing_status_label.text() == "EQ on"


def test_failed_output_change_when_routed_restores_selection(qapp):
    widget, control, _ = _make(qapp, with_chain=True, routed=True)
    control.fail_output = True
    widget.device_combo.setCurrentIndex(0)
    assert widget.device_combo.currentData() == "alsa_output.usb"
    assert widget.selected_output_name == "alsa_output.usb"
    assert "device unavailable" in widget.status_label.text()


def test_rebuild_sinks_preserves_selected_output_name(qapp):
    widget, control, _ = _make(qapp, with_chain=True)
    widget.device_combo.setCurrentIndex(0)
    assert widget.selected_output_name == "alsa_output.pci"
    # Even if default sink getter temporarily reports old sink
    control.default = "alsa_output.usb"
    widget.refresh()
    assert widget.selected_output_name == "alsa_output.pci"
    assert widget.device_combo.currentData() == "alsa_output.pci"


def test_failed_output_change_restores_selection_and_reports_error(qapp):
    widget, control, _ = _make(qapp, with_chain=True)
    control.fail_output = True
    widget.device_combo.setCurrentIndex(0)
    assert widget.device_combo.currentData() == "alsa_output.usb"
    assert widget.selected_output_name == "alsa_output.usb"
    assert "route rejected" in widget.status_label.text()


def test_failed_route_keeps_direct_state_and_reports_error(qapp):
    widget, control, _ = _make(qapp, with_chain=True)
    control.fail_route = True
    widget.routing_button.click()
    assert not control.routed
    assert "route rejected" in widget.status_label.text()
    assert widget.routing_button.text() == "Turn on EQ"


def test_application_rows_only_control_volume_and_mute(qapp):
    widget, control, _ = _make(qapp)
    assert [row.name_label.text() for row in widget.stream_rows] == ["Firefox", "mpv"]
    row = widget.stream_rows[0]
    row.slider.setValue(50)
    row.mute_button.setChecked(True)
    time.sleep(0.22)
    qapp.processEvents()
    assert ("set_volume", 60, 0.5) in control.calls
    assert ("set_mute", 60, True) in control.calls
    assert row.mute_button.text() == "Unmute"


def test_failed_application_controls_restore_visible_state(qapp):
    widget, control, _ = _make(qapp)
    row = widget.stream_rows[0]
    control.fail_mute = True
    row.mute_button.click()
    assert not row.mute_button.isChecked()
    assert row.mute_button.text() == "Mute"
    assert "mute rejected" in widget.status_label.text()
    control.fail_volume = True
    row.slider.setValue(30)
    time.sleep(0.22)
    qapp.processEvents()
    assert row.slider.value() == 80
    assert "volume rejected" in widget.status_label.text()


def test_device_slider_starts_at_actual_volume_and_targets_selected_device(qapp):
    widget, control, _ = _make(qapp)
    assert widget.master_slider.value() == 37
    widget.master_slider.setValue(40)
    time.sleep(0.22)
    qapp.processEvents()
    assert ("set_volume", 41, 0.4) in control.calls
    assert not any(c[0] == "set_volume" and c[1] == 40 for c in control.calls)


def test_refresh_does_not_rebuild_stream_rows_or_send_audio_commands(qapp):
    widget, control, registry = _make(qapp)
    rows = list(widget.stream_rows)
    control.calls.clear()
    registry._snapshot = PwSnapshot(sinks=registry._snapshot.sinks, streams=(
        PwNode(60, "firefox.playback", "Firefox", "Stream/Output/Audio", 0.4, False),
        registry._snapshot.streams[1],
    ))
    widget.refresh()
    assert widget.stream_rows == rows
    assert control.calls == []
    assert widget.master_slider.isEnabled()
    assert widget.master_vol_label.text() == format_vol_db(37)


def test_sink_labels_describe_physical_devices():
    assert sink_display_name(PwNode(1, "bluez_output.a", None, "Audio/Sink", 1, False, description="Headphones")) == "🎧 Headphones (Bluetooth)"
    assert sink_display_name(PwNode(2, "alsa_output.pci", None, "Audio/Sink", 1, False, description="Built-in Audio")) == "🔊 Built-in Audio"


@pytest.mark.parametrize('verified', [True, False])
def test_mixer_banner_reports_combined_profile_stages(qapp, verified):
    class Graph:
        eq_enabled = True
        def active_stages(self):
            return ('Spatial', 'EQ', 'LSP Limiter')
        def is_path_verified(self):
            return verified
    widget = MixerWidget(registry=FakeRegistry(_snapshot()), control=FakeControl(), poll_interval_ms=0)
    widget.graph_controller = Graph()
    widget._update_routing_banner(True, 'alsa_output.usb', 0, True)
    assert widget.routing_status_label.text() == (
        'Spatial + EQ + LSP Limiter active' if verified
        else 'Spatial + EQ + LSP Limiter · Route unverified')
    widget.close()


def test_busy_apply_rejects_route_and_output_changes_after_refresh(qapp):
    widget, control, _ = _make(qapp, with_chain=True)
    widget.mutation_allowed = lambda: False
    widget.refresh()
    assert not widget.routing_button.isEnabled()
    assert not widget.device_combo.isEnabled()
    before = list(control.calls)
    selected = widget.selected_output_name
    widget._on_toggle_routing()
    widget._on_default_sink(widget.device_combo.findData('alsa_output.pci'))
    assert control.calls == before
    assert widget.selected_output_name == selected
    assert widget.device_combo.currentData() == selected
    widget.mutation_allowed = lambda: True
    widget.refresh()
    assert widget.device_combo.isEnabled()
    assert widget.routing_button.isEnabled()
