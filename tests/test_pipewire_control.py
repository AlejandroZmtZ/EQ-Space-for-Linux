import json
from pathlib import Path

import pytest

from eqspace.core.pipewire.control import (
    PipeWireControlError,
    move_stream,
    set_default_sink,
    set_mute,
    set_volume,
)
from eqspace.core.pipewire.registry import PipeWireRegistry

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


def test_move_stream_sets_metadata_target():
    rec = Recorder()
    move_stream(71, 48, runner=rec)
    (cmd, _), = rec.calls
    assert cmd[0] == "pw-metadata"
    assert "71" in cmd
    assert "target.object" in cmd
    assert "48" in cmd


def test_command_failure_raises_control_error():
    def failing(cmd, timeout):
        raise RuntimeError("exit 1")

    with pytest.raises(PipeWireControlError):
        set_volume(48, 0.5, runner=failing)
