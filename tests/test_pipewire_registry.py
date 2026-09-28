import json
import threading
import time
from pathlib import Path

import pytest

from eqspace.core.pipewire.registry import (
    PipeWireRegistry,
    PipeWireUnavailable,
    PwNode,
    parse_pw_dump,
)

FIXTURE = Path(__file__).parent / "fixtures" / "pw_dump.json"


@pytest.fixture()
def dump_text() -> str:
    return FIXTURE.read_text()


def test_parse_pw_dump_classifies_nodes(dump_text):
    snapshot = parse_pw_dump(json.loads(dump_text))

    assert [n.name for n in snapshot.sinks] == [
        "alsa_output.pci-0000_00_1f.3.analog-stereo"
    ]
    assert [n.name for n in snapshot.sources] == [
        "alsa_input.pci-0000_00_1f.3.analog-stereo"
    ]
    stream_names = sorted(n.name for n in snapshot.streams)
    assert stream_names == ["Brave", "OBS Mic/Aux"]


def test_parse_pw_dump_node_fields(dump_text):
    snapshot = parse_pw_dump(json.loads(dump_text))
    sink = snapshot.sinks[0]

    assert sink == PwNode(
        id=48,
        name="alsa_output.pci-0000_00_1f.3.analog-stereo",
        app_name=None,
        media_class="Audio/Sink",
        volume=1.0,
        mute=False,
        serial=48,
        description="Built-in Audio Analog Stereo",
    )
    assert sink.description == "Built-in Audio Analog Stereo"
    source = snapshot.sources[0]
    assert source.volume == pytest.approx(0.65)
    assert source.mute is True
    assert source.description == "Built-in Audio Analog Stereo"
    brave = next(n for n in snapshot.streams if n.name == "Brave")
    assert brave.app_name == "Brave"
    assert brave.description == "Playback"
    obs = next(n for n in snapshot.streams if n.name == "OBS Mic/Aux")
    assert obs.description == "Desktop Audio"


def test_parse_pw_dump_node_description_fallback():
    # 1. node.description has highest priority
    dump = [
        {
            "id": 10,
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {
                    "media.class": "Audio/Sink",
                    "node.name": "sink1",
                    "node.description": "From Node Desc",
                    "device.description": "From Device Desc",
                    "media.name": "From Media Name",
                }
            },
        }
    ]
    snapshot = parse_pw_dump(dump)
    assert snapshot.sinks[0].description == "From Node Desc"

    # 2. device.description fallback
    dump = [
        {
            "id": 11,
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {
                    "media.class": "Audio/Sink",
                    "node.name": "sink2",
                    "device.description": "From Device Desc",
                    "media.name": "From Media Name",
                }
            },
        }
    ]
    snapshot = parse_pw_dump(dump)
    assert snapshot.sinks[0].description == "From Device Desc"

    # 3. media.name fallback
    dump = [
        {
            "id": 12,
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {
                    "media.class": "Stream/Output/Audio",
                    "node.name": "stream1",
                    "media.name": "From Media Name",
                }
            },
        }
    ]
    snapshot = parse_pw_dump(dump)
    assert snapshot.streams[0].description == "From Media Name"

    # 4. None when none present
    dump = [
        {
            "id": 13,
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {
                    "media.class": "Audio/Sink",
                    "node.name": "sink3",
                }
            },
        }
    ]
    snapshot = parse_pw_dump(dump)
    assert snapshot.sinks[0].description is None


def test_parse_pw_dump_handles_missing_props_and_params(dump_text):
    snapshot = parse_pw_dump(json.loads(dump_text))
    obs = next(n for n in snapshot.streams if n.name == "OBS Mic/Aux")
    assert obs.volume is None
    assert obs.mute is None


def test_parse_pw_dump_rejects_non_list():
    with pytest.raises(PipeWireUnavailable):
        parse_pw_dump({"not": "a list"})


def test_parse_pw_dump_skips_entries_with_malformed_id(dump_text):
    data = json.loads(dump_text)
    data.append({"id": None, "info": {"props": {"media.class": "Audio/Sink"}}})
    data.append({"id": "not-a-number", "info": {"props": {"media.class": "Audio/Source"}}})
    data.append({"info": {"props": {"media.class": "Audio/Sink"}}})

    snapshot = parse_pw_dump(data)

    assert [n.id for n in snapshot.sinks] == [48]
    assert [n.id for n in snapshot.sources] == [54]


def _fake_runner(stdout):
    def runner(cmd, timeout):
        return stdout

    return runner


def test_registry_snapshot_uses_pw_dump(dump_text):
    registry = PipeWireRegistry(runner=_fake_runner(dump_text))
    snapshot = registry.snapshot()
    assert len(snapshot.sinks) == 1


def test_registry_uses_complete_first_snapshot_when_pw_dump_appends_json(dump_text):
    registry = PipeWireRegistry(runner=_fake_runner(dump_text + "\n[]\n"))
    snapshot = registry.snapshot()
    assert len(snapshot.sinks) == 1
    assert snapshot.sinks[0].id == 48


def test_registry_rejects_malformed_trailing_document(dump_text):
    registry = PipeWireRegistry(runner=_fake_runner(dump_text + "\n{broken"))
    with pytest.raises(PipeWireUnavailable, match="invalid JSON"):
        registry.snapshot()


def test_registry_unavailable_when_binary_missing():
    def runner(cmd, timeout):
        raise FileNotFoundError(cmd[0])

    registry = PipeWireRegistry(runner=runner)
    with pytest.raises(PipeWireUnavailable):
        registry.snapshot()


def test_registry_unavailable_on_bad_json():
    registry = PipeWireRegistry(runner=_fake_runner("not json {"))
    with pytest.raises(PipeWireUnavailable):
        registry.snapshot()


def test_monitor_emits_on_change(dump_text):
    outputs = [dump_text]
    events = []
    done = threading.Event()

    def runner(cmd, timeout):
        return outputs[-1]

    registry = PipeWireRegistry(runner=runner)

    def callback(snapshot):
        events.append(snapshot)
        done.set()

    handle = registry.monitor(callback, interval=0.05)
    try:
        changed = json.loads(dump_text)
        for obj in changed:
            props = (obj.get("info") or {}).get("props") or {}
            if props.get("media.class") == "Audio/Sink":
                obj["info"]["params"]["Props"][0]["volume"] = 0.5
        outputs.append(json.dumps(changed))
        assert done.wait(2.0), "monitor did not emit a change callback"
        assert events[-1].sinks[0].volume == pytest.approx(0.5)
    finally:
        handle.stop()


def test_monitor_ignores_unchanged_snapshots(dump_text):
    events = []

    registry = PipeWireRegistry(runner=_fake_runner(dump_text))
    handle = registry.monitor(events.append, interval=0.05)
    try:
        time.sleep(0.3)
    finally:
        handle.stop()
    assert events == []


def test_monitor_survives_transient_dump_errors():
    outputs = ["garbage {", ""]
    events = []
    done = threading.Event()

    def runner(cmd, timeout):
        if len(outputs) > 1:
            return outputs.pop(0)
        return outputs[0]

    registry = PipeWireRegistry(runner=runner)

    fixture = json.loads(FIXTURE.read_text())
    outputs.append(json.dumps(fixture))

    def callback(snapshot):
        events.append(snapshot)
        done.set()

    handle = registry.monitor(callback, interval=0.05)
    try:
        assert done.wait(2.0)
        assert len(events[0].sinks) == 1
    finally:
        handle.stop()


def test_monitor_survives_callback_exceptions(dump_text, caplog):
    outputs = [dump_text]
    first_called = threading.Event()
    done = threading.Event()

    def runner(cmd, timeout):
        return outputs[-1]

    registry = PipeWireRegistry(runner=runner)

    def callback(snapshot):
        if not first_called.is_set():
            first_called.set()
            raise RuntimeError("callback boom")
        done.set()

    handle = registry.monitor(callback, interval=0.05)
    try:
        changed = json.loads(dump_text)
        for obj in changed:
            props = (obj.get("info") or {}).get("props") or {}
            if props.get("media.class") == "Audio/Sink":
                obj["info"]["params"]["Props"][0]["volume"] = 0.5
        outputs.append(json.dumps(changed))
        assert first_called.wait(2.0), "first callback never fired"
        changed_again = json.loads(dump_text)
        for obj in changed_again:
            props = (obj.get("info") or {}).get("props") or {}
            if props.get("media.class") == "Audio/Sink":
                obj["info"]["params"]["Props"][0]["volume"] = 0.25
        outputs.append(json.dumps(changed_again))
        assert done.wait(2.0), "monitor thread died after callback exception"
    finally:
        handle.stop()
    assert any(
        "monitor callback" in record.getMessage() for record in caplog.records
    )


def test_registry_skips_tombstone_events_before_full_initial_snapshot(dump_text):
    # Captured during concurrent GUI observations on the private PipeWire daemon:
    # pw-dump emitted a client-removal event before its full enumeration.
    raw = '[{"id": 55, "info": null}]\n' + dump_text + '\n[{"id": 56, "info": null}]'
    registry = PipeWireRegistry(runner=lambda *_: raw)
    snapshot = registry.snapshot()
    assert len(snapshot.sinks) == 1
    assert snapshot.sinks[0].id == 48
