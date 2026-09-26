import json
from pathlib import Path

import pytest

from eqspace.core.pipewire.control import (
    PipeWireControlError,
    get_active_output_links,
    connected_filter_output,
    get_default_sink_name,
    get_volume,
    is_system_routed,
    link_filter_output,
    move_stream,
    relink_stream_ports,
    set_default_sink,
    set_mute,
    set_system_routing,
    set_volume,
)
from eqspace.core.pipewire.registry import PipeWireRegistry, PwNode, PwSnapshot

FIXTURE = Path(__file__).parent / "fixtures" / "pw_dump.json"


class Recorder:
    def __init__(self, stdout=""):
        self.calls = []
        self.stdout = stdout

    def __call__(self, cmd, timeout):
        self.calls.append((list(cmd), timeout))
        return self.stdout


def test_set_volume_invokes_wpctl():
    rec = Recorder()
    set_volume(48, 0.75, runner=rec)
    (cmd, timeout), = rec.calls
    assert cmd == ["wpctl", "set-volume", "48", "0.75"]
    assert timeout > 0


def test_set_volume_rejects_out_of_range():
    with pytest.raises(ValueError):
        set_volume(48, 1.5, runner=Recorder())


def test_get_volume_reads_real_wpctl_value():
    rec = Recorder("Volume: 0.37\n")
    assert get_volume(48, runner=rec) == 0.37
    assert rec.calls[0][0] == ["wpctl", "get-volume", "48"]


def test_get_volume_rejects_unrecognized_output():
    with pytest.raises(PipeWireControlError, match="could not read volume"):
        get_volume(48, runner=Recorder("no volume"))


def test_set_mute_on_and_off():
    rec = Recorder()
    set_mute(48, True, runner=rec)
    set_mute(48, False, runner=rec)
    assert rec.calls[0][0] == ["wpctl", "set-mute", "48", "1"]
    assert rec.calls[1][0] == ["wpctl", "set-mute", "48", "0"]


def test_set_default_sink_resolves_name_to_id():
    rec = Recorder()
    registry = PipeWireRegistry(runner=Recorder(FIXTURE.read_text()))
    set_default_sink(
        "alsa_output.pci-0000_00_1f.3.analog-stereo", registry=registry, runner=rec
    )
    assert rec.calls[0][0] == ["wpctl", "set-default", "48"]


def test_set_default_sink_unknown_name_raises():
    registry = PipeWireRegistry(runner=Recorder(FIXTURE.read_text()))
    with pytest.raises(PipeWireControlError, match="no such sink"):
        set_default_sink("nope", registry=registry, runner=Recorder())


def _dump_with_serials(stream_serial: int, sink_serial: int) -> str:
    data = json.loads(FIXTURE.read_text())
    for entry in data:
        props = (entry.get("info") or {}).get("props") or {}
        if entry.get("id") == 71:
            props["object.serial"] = stream_serial
        elif entry.get("id") == 48:
            props["object.serial"] = sink_serial
    return json.dumps(data)


def test_move_stream_sets_metadata_target_by_serial():
    rec = Recorder()
    registry = PipeWireRegistry(runner=Recorder(_dump_with_serials(7171, 4848)))
    move_stream(71, 48, runner=rec, registry=registry)
    (cmd, _), = rec.calls
    assert cmd == ["pw-metadata", "7171", "target.object", "4848"]


def test_move_stream_by_sink_name():
    rec = Recorder()
    registry = PipeWireRegistry(runner=Recorder(_dump_with_serials(7171, 4848)))
    move_stream(71, "alsa_output.pci-0000_00_1f.3.analog-stereo", runner=rec, registry=registry)
    (cmd, _), = rec.calls
    assert cmd == ["pw-metadata", "7171", "target.object", "4848"]


def test_move_stream_unknown_node_raises():
    registry = PipeWireRegistry(runner=Recorder(FIXTURE.read_text()))
    with pytest.raises(PipeWireControlError, match="no such stream node"):
        move_stream(999, 48, runner=Recorder(), registry=registry)
    with pytest.raises(PipeWireControlError, match="no such sink node"):
        move_stream(71, 999, runner=Recorder(), registry=registry)


def test_command_failure_raises_control_error():
    def failing(cmd, timeout):
        raise RuntimeError("exit 1")

    with pytest.raises(PipeWireControlError):
        set_volume(48, 0.5, runner=failing)


class MockRegistry:
    def __init__(self, sinks):
        self._sinks = tuple(sinks)

    def snapshot(self):
        return PwSnapshot(sinks=self._sinks, streams=())


def test_get_default_sink_name_from_sinks_star():
    output = """
Audio
 ├─ Devices:
 │      47. Built-in Audio                      [alsa]
 ├─ Sinks:
 │      48. Built-in Audio Analog Stereo        [vol: 0.00]
 │  *   63. WH-CH720N                           [vol: 0.68]
 ├─ Sources:
 │  *   49. Built-in Audio Analog Stereo        [vol: 0.00]
"""
    rec = Recorder(output)
    assert get_default_sink_name(runner=rec) == "WH-CH720N"
    assert rec.calls[0][0] == ["wpctl", "status"]


def test_get_default_sink_name_from_sinks_id_only():
    output = """
Audio
 ├─ Sinks:
 │  *   63.
"""
    rec = Recorder(output)
    assert get_default_sink_name(runner=rec) == "63"


def test_get_default_sink_name_from_default_configured_node_names():
    output = """
Settings
 └─ Default Configured Node Names:
         0. Audio/Sink    eqspace.filter-chain
         1. Audio/Source  alsa_input.pci
"""
    rec = Recorder(output)
    assert get_default_sink_name(runner=rec) == "eqspace.filter-chain"


def test_get_default_sink_name_not_found():
    output = """
Audio
 ├─ Sinks:
 │      48. Built-in Audio Analog Stereo
"""
    rec = Recorder(output)
    assert get_default_sink_name(runner=rec) is None


def test_get_default_sink_name_command_failure():
    def failing(cmd, timeout):
        raise RuntimeError("wpctl not found")

    assert get_default_sink_name(runner=failing) is None


def test_set_system_routing_enable_success():
    rec = Recorder()
    reg = MockRegistry([
        PwNode(48, "alsa_output.pci", "Built-in Audio", "Audio/Sink", 1.0, False),
        PwNode(95, "eqspace.filter-chain", "EQ-Space Filter Chain", "Audio/Sink", 1.0, False),
    ])
    result = set_system_routing(True, registry=reg, runner=rec)
    assert result is True
    assert rec.calls[-1][0] == ["wpctl", "set-default", "95"]


def test_reapply_does_not_replace_original_sink_with_filter_description(monkeypatch):
    from eqspace.core.pipewire import control

    monkeypatch.setattr(control, "_last_physical_sink_name", "alsa_output.pci")
    output = "Audio\n ├─ Sinks:\n │  *   95. filter-chain-123-20\n"
    rec = Recorder(output)
    reg = MockRegistry([
        PwNode(48, "alsa_output.pci", "Built-in Audio", "Audio/Sink", 1.0, False),
        PwNode(63, "bluez_output.headphones", "Headphones", "Audio/Sink", 1.0, False),
        PwNode(95, "eqspace.filter-chain", "filter-chain-123-20", "Audio/Sink", 1.0, False),
    ])
    set_system_routing(True, registry=reg, runner=rec)
    set_system_routing(False, registry=reg, runner=rec)
    assert rec.calls[-1][0] == ["wpctl", "set-default", "48"]


def test_set_system_routing_enable_filter_chain_not_loaded_raises():
    reg = MockRegistry([
        PwNode(48, "alsa_output.pci", "Built-in Audio", "Audio/Sink", 1.0, False),
    ])
    with pytest.raises(
        PipeWireControlError, match="Filter chain sink 'eqspace.filter-chain' is not loaded"
    ):
        set_system_routing(True, registry=reg, runner=Recorder())


def test_set_system_routing_disable_restores_fallback_sink():
    rec = Recorder()
    reg = MockRegistry([
        PwNode(95, "eqspace.filter-chain", "EQ-Space Filter Chain", "Audio/Sink", 1.0, False),
        PwNode(48, "alsa_output.pci", "Built-in Audio", "Audio/Sink", 1.0, False),
    ])
    result = set_system_routing(False, registry=reg, runner=rec)
    assert result is False
    assert rec.calls[0][0] == ["wpctl", "set-default", "48"]


def test_set_system_routing_disable_prefers_bluetooth_fallback():
    rec = Recorder()
    reg = MockRegistry([
        PwNode(95, "eqspace.filter-chain", "EQ-Space Filter Chain", "Audio/Sink", 1.0, False),
        PwNode(48, "alsa_output.pci", "Built-in Audio", "Audio/Sink", 1.0, False),
        PwNode(63, "bluez_output.headphones", "Sony WH-CH720N", "Audio/Sink", 0.7, False),
    ])
    result = set_system_routing(False, registry=reg, runner=rec)
    assert result is False
    assert rec.calls[0][0] == ["wpctl", "set-default", "63"]


def test_set_system_routing_disable_with_explicit_fallback():
    rec = Recorder()
    reg = MockRegistry([
        PwNode(95, "eqspace.filter-chain", "EQ-Space Filter Chain", "Audio/Sink", 1.0, False),
        PwNode(48, "alsa_output.pci", "Built-in Audio", "Audio/Sink", 1.0, False),
        PwNode(63, "bluez_output.headphones", "Sony WH-CH720N", "Audio/Sink", 0.7, False),
    ])
    result = set_system_routing(False, fallback_sink_name="alsa_output.pci", registry=reg, runner=rec)
    assert result is False
    assert rec.calls[0][0] == ["wpctl", "set-default", "48"]


def test_set_system_routing_disable_when_no_other_sinks():
    rec = Recorder()
    reg = MockRegistry([
        PwNode(95, "eqspace.filter-chain", "EQ-Space Filter Chain", "Audio/Sink", 1.0, False),
    ])
    result = set_system_routing(False, registry=reg, runner=rec)
    assert result is False
    assert len(rec.calls) == 0


def test_direct_route_rejects_stream_still_linked_to_eq(monkeypatch):
    from eqspace.core.pipewire import control
    from eqspace.core.pipewire.registry import PwSnapshot

    class Registry:
        def snapshot(self):
            return PwSnapshot(
                sinks=(
                    PwNode(95, "eqspace.filter-chain", None, "Audio/Sink", 1.0, False),
                    PwNode(48, "alsa_output.pci", None, "Audio/Sink", 1.0, False),
                ),
                streams=(PwNode(71, "Player", "Player", "Stream/Output/Audio", 1.0, False),),
            )

    links = {
        "Player:output_FL": ["eqspace.filter-chain:playback_FL"],
        "Player:output_FR": ["eqspace.filter-chain:playback_FR"],
    }
    monkeypatch.setattr(control, "set_default_sink", lambda *args, **kwargs: None)
    monkeypatch.setattr(control, "get_active_output_links", lambda *args, **kwargs: links)
    monkeypatch.setattr(control, "move_stream", lambda *args, **kwargs: None)
    with pytest.raises(PipeWireControlError, match="still linked"):
        set_system_routing(False, fallback_sink_name="alsa_output.pci", registry=Registry())


def test_direct_route_accepts_stream_relinked_to_physical_output(monkeypatch):
    from eqspace.core.pipewire import control
    from eqspace.core.pipewire.registry import PwSnapshot

    class Registry:
        def snapshot(self):
            return PwSnapshot(
                sinks=(
                    PwNode(95, "eqspace.filter-chain", None, "Audio/Sink", 1.0, False),
                    PwNode(48, "alsa_output.pci", None, "Audio/Sink", 1.0, False),
                ),
                streams=(PwNode(71, "Player", "Player", "Stream/Output/Audio", 1.0, False),),
            )

    links = {
        "Player:output_FL": ["eqspace.filter-chain:playback_FL"],
        "Player:output_FR": ["eqspace.filter-chain:playback_FR"],
    }

    def move(*args, **kwargs):
        links["Player:output_FL"] = ["alsa_output.pci:playback_FL"]
        links["Player:output_FR"] = ["alsa_output.pci:playback_FR"]

    monkeypatch.setattr(control, "set_default_sink", lambda *args, **kwargs: None)
    monkeypatch.setattr(control, "get_active_output_links", lambda *args, **kwargs: links)
    monkeypatch.setattr(control, "move_stream", move)
    assert set_system_routing(False, fallback_sink_name="alsa_output.pci", registry=Registry()) is False


def test_is_system_routed_true_when_sink_name_matches():
    output = """
Settings
 └─ Default Configured Node Names:
         0. Audio/Sink    eqspace.filter-chain
"""
    assert is_system_routed("eqspace.filter-chain", runner=Recorder(output)) is True


def test_is_system_routed_true_when_sinks_star_matches_name():
    output = """
Audio
 ├─ Sinks:
 │  *   95. eqspace.filter-chain
"""
    assert is_system_routed("eqspace.filter-chain", runner=Recorder(output)) is True


def test_is_system_routed_true_when_sinks_star_matches_desc_or_registry():
    output = """
Audio
 ├─ Sinks:
 │  *   95. EQ-Space Filter Chain
"""
    reg = MockRegistry([
        PwNode(95, "eqspace.filter-chain", "EQ-Space Filter Chain", "Audio/Sink", 1.0, False),
    ])
    assert is_system_routed("eqspace.filter-chain", runner=Recorder(output), registry=reg) is True


def test_is_system_routed_false_when_other_sink_default():
    output = """
Audio
 ├─ Sinks:
 │  *   63. WH-CH720N
"""
    reg = MockRegistry([
        PwNode(63, "bluez_output", "WH-CH720N", "Audio/Sink", 1.0, False),
        PwNode(95, "eqspace.filter-chain", "EQ-Space Filter Chain", "Audio/Sink", 1.0, False),
    ])
    assert is_system_routed("eqspace.filter-chain", runner=Recorder(output), registry=reg) is False


def test_is_system_routed_false_on_runner_error():
    def failing(cmd, timeout):
        raise RuntimeError("wpctl error")

    assert is_system_routed("eqspace.filter-chain", runner=failing) is False


def test_get_active_output_links_parses_pw_link_output():
    output = """
Brave:output_FL
  |-> bluez_output.00_11_22_33_44_55.1:playback_FL
Brave:output_FR
  |-> bluez_output.00_11_22_33_44_55.1:playback_FR
"""
    rec = Recorder(output)
    links = get_active_output_links(runner=rec)
    assert links == {
        "Brave:output_FL": ["bluez_output.00_11_22_33_44_55.1:playback_FL"],
        "Brave:output_FR": ["bluez_output.00_11_22_33_44_55.1:playback_FR"],
    }
    assert rec.calls[0][0] == ["pw-link", "-l"]


def test_get_active_output_links_reports_inspection_failure():
    def failing(cmd, timeout):
        raise RuntimeError("pw-link unavailable")

    with pytest.raises(PipeWireControlError, match="pw-link unavailable"):
        get_active_output_links(runner=failing)


def test_connected_filter_output_requires_both_channels_on_same_physical_sink():
    output = """
eqspace.filter-chain.playback:output_FL
  |-> bluez_output.headphones:playback_FL
eqspace.filter-chain.playback:output_FR
  |-> bluez_output.headphones:playback_FR
"""
    assert connected_filter_output(
        "eqspace.filter-chain", {"bluez_output.headphones"}, runner=Recorder(output)
    ) == "bluez_output.headphones"
    assert connected_filter_output(
        "eqspace.filter-chain", {"alsa_output.pci"}, runner=Recorder(output)
    ) is None
    assert connected_filter_output(
        "eqspace.filter-chain", {"bluez_output.headphones"},
        runner=Recorder(output.replace("  |-> bluez_output.headphones:playback_FR", "")),
    ) is None


def test_routing_rejects_disconnected_eq_before_changing_default(monkeypatch):
    from eqspace.core.pipewire import control

    reg = MockRegistry([
        PwNode(48, "alsa_output.pci", "Built-in Audio", "Audio/Sink", 1.0, False),
        PwNode(95, "eqspace.filter-chain", "EQ-Space", "Audio/Sink", 1.0, False),
    ])
    calls = []
    def runner(cmd, timeout):
        calls.append(list(cmd))
        return ""
    ticks = iter([0.0, 3.0])
    monkeypatch.setattr(control.time, "monotonic", lambda: next(ticks))
    with pytest.raises(PipeWireControlError, match="unverified"):
        set_system_routing(True, registry=reg, runner=runner)
    assert not any(cmd[:2] == ["wpctl", "set-default"] for cmd in calls)


def test_relink_stream_ports_disconnects_and_reconnects():
    pw_link_l = """
Brave:output_FL
  |-> bluez_output.00_11_22_33_44_55.1:playback_FL
Brave:output_FR
  |-> bluez_output.00_11_22_33_44_55.1:playback_FR
"""
    calls = []

    def runner(cmd, timeout):
        calls.append(list(cmd))
        if cmd == ["pw-link", "-l"]:
            return pw_link_l
        return ""

    result = relink_stream_ports("Brave", "eqspace.filter-chain", runner=runner)
    assert result is True
    assert ["pw-link", "-d", "Brave:output_FL", "bluez_output.00_11_22_33_44_55.1:playback_FL"] in calls
    assert ["pw-link", "-d", "Brave:output_FR", "bluez_output.00_11_22_33_44_55.1:playback_FR"] in calls
    assert ["pw-link", "Brave:output_FL", "eqspace.filter-chain:playback_FL"] in calls
    assert ["pw-link", "Brave:output_FR", "eqspace.filter-chain:playback_FR"] in calls
    additions = [i for i, cmd in enumerate(calls) if cmd[:1] == ["pw-link"] and len(cmd) == 3 and cmd[1] != "-d"]
    removals = [i for i, cmd in enumerate(calls) if cmd[:2] == ["pw-link", "-d"]]
    assert max(additions) < min(removals)


def test_relink_failure_keeps_old_audio_links():
    old_links = """
Brave:output_FL
  |-> bluez_output.headphones:playback_FL
Brave:output_FR
  |-> bluez_output.headphones:playback_FR
"""
    calls = []

    def runner(cmd, timeout):
        calls.append(list(cmd))
        if cmd == ["pw-link", "-l"]:
            return old_links
        if cmd == ["pw-link", "Brave:output_FR", "eqspace.filter-chain:playback_FR"]:
            raise RuntimeError("target port unavailable")
        return ""

    with pytest.raises(PipeWireControlError, match="target port unavailable"):
        relink_stream_ports("Brave", "eqspace.filter-chain", runner=runner)
    assert ["pw-link", "Brave:output_FL", "eqspace.filter-chain:playback_FL"] in calls
    assert ["pw-link", "-d", "Brave:output_FL", "eqspace.filter-chain:playback_FL"] in calls
    assert not any(cmd[:2] == ["pw-link", "-d"] and "bluez_output" in cmd[-1] for cmd in calls)


class FilterLinkRunner:
    def __init__(self, old_sink=None, fail_channel=None, omit_channel=None, fail_removal=False):
        self.links = {
            f"eqspace.filter-chain.playback:output_{ch}":
            [f"{old_sink}:playback_{ch}"] if old_sink else []
            for ch in ("FL", "FR")
        }
        self.calls = []
        self.fail_channel = fail_channel
        self.omit_channel = omit_channel
        self.fail_removal = fail_removal

    def __call__(self, cmd, timeout):
        self.calls.append(list(cmd))
        if cmd == ["pw-link", "-l"]:
            return "\n".join(src + "\n" + "\n".join(f"  |-> {dst}" for dst in dsts)
                             for src, dsts in self.links.items())
        if cmd[:2] == ["pw-link", "-d"]:
            if self.fail_removal:
                raise RuntimeError("unlink failed")
            self.links[cmd[2]].remove(cmd[3])
        elif len(cmd) == 3:
            if cmd[1].endswith(self.fail_channel or "never"):
                raise RuntimeError("port unavailable")
            if not cmd[1].endswith(self.omit_channel or "never"):
                self.links[cmd[1]].append(cmd[2])
        return ""


def test_link_filter_output_links_both_channels_when_unconnected():
    runner = FilterLinkRunner()
    link_filter_output("eqspace.filter-chain", "new_sink", runner=runner)
    assert all(dsts == [f"new_sink:playback_{ch}"]
               for ch, dsts in zip(("FL", "FR"), runner.links.values()))


def test_link_filter_output_verifies_both_channels_before_removing_old_destinations():
    runner = FilterLinkRunner(old_sink="old_sink")
    link_filter_output("eqspace.filter-chain", "new_sink", runner=runner)
    first_removal = next(i for i, cmd in enumerate(runner.calls) if cmd[:2] == ["pw-link", "-d"])
    assert runner.calls[first_removal - 1] == ["pw-link", "-l"]
    assert ["pw-link", "eqspace.filter-chain.playback:output_FR", "new_sink:playback_FR"] in runner.calls[:first_removal]
    assert all(dsts == [f"new_sink:playback_{ch}"]
               for ch, dsts in zip(("FL", "FR"), runner.links.values()))


def test_link_filter_output_ignores_already_connected_target():
    runner = FilterLinkRunner(old_sink="target_sink")
    link_filter_output("eqspace.filter-chain", "target_sink", runner=runner)
    assert not any(len(cmd) > 2 for cmd in runner.calls)


@pytest.mark.parametrize("channel", ["FL", "FR"])
def test_link_filter_output_failed_addition_preserves_old_channels(channel):
    runner = FilterLinkRunner(old_sink="old_sink", fail_channel=channel)
    with pytest.raises(PipeWireControlError, match="port unavailable"):
        link_filter_output("eqspace.filter-chain", "new_sink", runner=runner)
    assert all(dsts == [f"old_sink:playback_{ch}"]
               for ch, dsts in zip(("FL", "FR"), runner.links.values()))


def test_link_filter_output_missing_added_channel_preserves_old_channels():
    runner = FilterLinkRunner(old_sink="old_sink", omit_channel="FR")
    with pytest.raises(PipeWireControlError, match="unverified"):
        link_filter_output("eqspace.filter-chain", "new_sink", runner=runner)
    assert all(dsts == [f"old_sink:playback_{ch}"]
               for ch, dsts in zip(("FL", "FR"), runner.links.values()))


def test_link_filter_output_failed_rollback_reports_unverified_state():
    runner = FilterLinkRunner(old_sink="old_sink", fail_channel="FR", fail_removal=True)
    with pytest.raises(PipeWireControlError, match="rollback.*failed"):
        link_filter_output("eqspace.filter-chain", "new_sink", runner=runner)
    assert all(f"old_sink:playback_{ch}" in dsts
               for ch, dsts in zip(("FL", "FR"), runner.links.values()))


def test_link_filter_output_command_failure_after_effect_rolls_back_new_links():
    runner = FilterLinkRunner(old_sink="old_sink")
    def after_effect(cmd, timeout):
        output = runner(cmd, timeout)
        if len(cmd) == 3 and cmd[1].endswith("FR"):
            raise RuntimeError("timeout after linking")
        return output
    with pytest.raises(PipeWireControlError, match="timeout after linking"):
        link_filter_output("eqspace.filter-chain", "new_sink", runner=after_effect)
    assert all(dsts == [f"old_sink:playback_{ch}"]
               for ch, dsts in zip(("FL", "FR"), runner.links.values()))


def test_link_filter_output_rollback_keeps_preexisting_target_link():
    runner = FilterLinkRunner(old_sink="old_sink", fail_channel="FR")
    runner.links["eqspace.filter-chain.playback:output_FL"].append("new_sink:playback_FL")
    with pytest.raises(PipeWireControlError, match="port unavailable"):
        link_filter_output("eqspace.filter-chain", "new_sink", runner=runner)
    assert runner.links["eqspace.filter-chain.playback:output_FL"] == [
        "old_sink:playback_FL", "new_sink:playback_FL"]


def test_link_filter_output_old_destination_removal_failure_is_reported():
    runner = FilterLinkRunner(old_sink="old_sink", fail_removal=True)
    with pytest.raises(PipeWireControlError, match="unlink failed"):
        link_filter_output("eqspace.filter-chain", "new_sink", runner=runner)
    assert all(f"new_sink:playback_{ch}" in dsts
               for ch, dsts in zip(("FL", "FR"), runner.links.values()))


def test_link_filter_output_snapshot_failure_prevents_mutation():
    runner = Recorder()
    def failing_snapshot(cmd, timeout):
        runner(cmd, timeout)
        raise RuntimeError("snapshot unavailable")
    with pytest.raises(PipeWireControlError, match="snapshot unavailable"):
        link_filter_output("eqspace.filter-chain", "new_sink", runner=failing_snapshot)
    assert len(runner.calls) == 1


def test_set_system_routing_enable_links_target_output():
    reg = MockRegistry([
        PwNode(48, "alsa_output.pci", "Built-in Audio", "Audio/Sink", 1.0, False),
        PwNode(95, "eqspace.filter-chain", "EQ-Space", "Audio/Sink", 1.0, False),
    ])
    links = {}
    calls = []

    def runner(cmd, timeout):
        calls.append(list(cmd))
        if cmd == ["pw-link", "-l"]:
            lines = []
            for src, dsts in links.items():
                lines.append(src)
                for dst in dsts:
                    lines.append(f"  |-> {dst}")
            return "\n".join(lines)
        if cmd[0] == "pw-link" and len(cmd) == 3:
            links.setdefault(cmd[1], []).append(cmd[2])
            return ""
        if cmd[:2] == ["wpctl", "status"]:
            return "Audio\n ├─ Sinks:\n │  *   48. alsa_output.pci\n"
        return ""

    result = set_system_routing(True, fallback_sink_name="alsa_output.pci", registry=reg, runner=runner)
    assert result is True
    assert ["pw-link", "eqspace.filter-chain.playback:output_FL", "alsa_output.pci:playback_FL"] in calls
    assert ["pw-link", "eqspace.filter-chain.playback:output_FR", "alsa_output.pci:playback_FR"] in calls
    assert any(cmd[:2] == ["wpctl", "set-default"] and cmd[2] == "95" for cmd in calls)


def test_native_surround_relink_preserves_each_channel(monkeypatch):
    from eqspace.core.pipewire import control
    channels = ('FL', 'FR', 'FC', 'LFE', 'SL', 'SR', 'RL', 'RR')
    monkeypatch.setattr(control, 'get_active_output_links', lambda **kwargs:
                        {f'player:output_{ch}': [f'old:playback_{ch}'] for ch in channels})
    calls = []
    def runner(cmd, timeout):
        calls.append(tuple(cmd))
        if list(cmd) == ['pw-link', '-i']:
            return '\n'.join(f'cinema:playback_{ch}' for ch in channels)
        return ''
    control.relink_stream_ports('player', 'cinema', runner=runner)
    for ch in channels:
        assert ('pw-link', f'player:output_{ch}', f'cinema:playback_{ch}') in calls
