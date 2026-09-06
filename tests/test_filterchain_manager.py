import io
import os
from pathlib import Path

import pytest

from eqspace.core.filterchain.manager import (
    FilterChainError,
    FilterChainManager,
    FilterSpec,
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


class FakePwCli:
    """Stand-in for a persistent interactive ``pw-cli`` subprocess.

    stdin writes are recorded; a ``load-module`` command triggers the canned
    response on the (real) stdout pipe, preceded by a startup banner so the
    manager's prompt-drain has something to consume.
    """

    def __init__(self, load_output: str):
        self.commands: list[str] = []
        self.stdin = io.StringIO()
        read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "r", encoding="utf-8", buffering=1)
        self._load_output = load_output
        self.killed = False
        self.wait_calls = 0
        os.write(self._write_fd, b"Welcome to PipeWire\n>> ")
        self._real_write = self.stdin.write
        self.stdin.write = self._stdin_write  # type: ignore[method-assign]

    def _stdin_write(self, data: str) -> int:
        self.commands.append(data)
        if data.startswith("load-module"):
            os.write(self._write_fd, self._load_output.encode())
        return len(data)

    def wait(self, timeout=None):
        self.wait_calls += 1
        return 0

    def kill(self):
        self.killed = True


def fake_popen(process: FakePwCli):
    def factory(cmd, **kwargs):
        assert cmd == ["pw-cli"]
        assert kwargs["stdin"] is not None
        assert kwargs["stdout"] is not None
        return process

    return factory


FILTERS = [
    FilterSpec(name="band_0", filter_type="bq_peaking", params={"Freq": 100.0, "Gain": 6.0, "Q": 1.4}),
    FilterSpec(name="band_1", filter_type="bq_highshelf", params={"Freq": 8000.0, "Gain": -3.0}),
]


def test_render_config_contains_graph_and_params():
    manager = FilterChainManager(node_name="eqspace.test", description="Test EQ")
    conf = manager.render_config(FILTERS)

    assert "libpipewire-module-filter-chain" in conf
    assert 'node.description = "Test EQ"' in conf
    assert "bq_peaking" in conf
    assert "bq_highshelf" in conf
    assert '"Freq" = 100.0' in conf
    assert '"Gain" = 6.0' in conf
    assert '"Q" = 1.4' in conf
    assert '"Freq" = 8000.0' in conf
    assert '"Gain" = -3.0' in conf
    assert "audio.position = [ FL FR ]" in conf
    assert 'node.name = "eqspace.test"' in conf


def test_render_args_is_single_line_properties_string():
    manager = FilterChainManager(node_name="eqspace.test", description="Test EQ")
    args = manager.render_args(FILTERS)

    assert "\n" not in args
    assert not args.startswith("{")
    assert 'node.name = "eqspace.test"' in args
    assert 'name = "band_0" label = bq_peaking' in args
    assert 'name = "band_1" label = bq_highshelf' in args
    assert '"Freq" = 100.0' in args
    assert '"Freq" = 8000.0' in args
    assert "audio.position = [ FL FR ]" in args
    assert 'media.class = "Stream/Filter"' in args


def test_render_config_escapes_quotes():
    manager = FilterChainManager(node_name='bad"name', description='desc "q" \\')
    conf = manager.render_config([])
    assert '\\"' in conf
    assert "\\\\" in conf


def test_load_passes_inline_args_and_parses_module_id():
    proc = FakePwCli("1 = @module:77\npipewire-0>> ")
    manager = FilterChainManager(
        node_name="eqspace.test", description="Test EQ", popen=fake_popen(proc)
    )
    module_id = manager.load(FILTERS)

    assert module_id == 77
    assert manager.is_loaded
    (command,) = proc.commands
    assert command.startswith("load-module libpipewire-module-filter-chain '")
    assert command.endswith("'\n")
    assert command.count("\n") == 1
    assert "bq_peaking" in command
    assert "bq_highshelf" in command
    assert 'node.name = "eqspace.test"' in command


def test_load_failure_raises_and_cleans_up():
    proc = FakePwCli('Error: "Could not load module"\npipewire-0>> ')
    manager = FilterChainManager(
        node_name="eqspace.test", description="T", popen=fake_popen(proc)
    )
    with pytest.raises(FilterChainError, match="module id"):
        manager.load(FILTERS, timeout=1.0)
    assert not manager.is_loaded
    assert proc.killed


def test_load_rejects_single_quotes_in_names():
    manager = FilterChainManager(node_name="bad'name", description="T")
    with pytest.raises(FilterChainError, match="quotes"):
        manager.load(FILTERS)


def test_unload_destroys_module_and_quits_pw_cli():
    proc = FakePwCli("1 = @module:77\npipewire-0>> ")
    manager = FilterChainManager(
        node_name="eqspace.test", description="T", popen=fake_popen(proc)
    )
    manager.load(FILTERS)
    manager.unload()

    assert not manager.is_loaded
    assert proc.commands[-1] == "destroy 77\nquit\n"
    assert proc.wait_calls == 1


def test_unload_when_not_loaded_is_noop():
    proc = FakePwCli("")
    manager = FilterChainManager(
        node_name="eqspace.test", description="T", popen=fake_popen(proc)
    )
    manager.unload()
    assert proc.commands == []


def test_set_filter_param_resolves_node_and_sets_props():
    dump = FIXTURE.read_text()
    rec = Recorder()

    def runner(cmd, timeout):
        rec.calls.append((list(cmd), timeout))
        if cmd[0] == "pw-dump":
            return dump
        return ""

    registry = PipeWireRegistry(runner=runner)
    manager = FilterChainManager(
        node_name="eqspace.test", description="T", runner=runner
    )
    manager.set_filter_param(
        "alsa_output.pci-0000_00_1f.3.analog-stereo",
        "band_0:Freq",
        250.0,
        registry=registry,
    )

    cmd = rec.calls[-1][0]
    assert cmd[:3] == ["pw-cli", "set-param", "48"]
    assert cmd[3] == "Props"
    assert '"params": ["band_0:Freq", 250.0]' in cmd[4]


def test_set_filter_param_unknown_node_raises():
    registry = PipeWireRegistry(runner=Recorder(FIXTURE.read_text()))
    manager = FilterChainManager(
        node_name="eqspace.test", description="T", runner=Recorder()
    )
    with pytest.raises(FilterChainError, match="no node named"):
        manager.set_filter_param("missing", "Freq", 1.0, registry=registry)


def test_live_update_supported_true_on_success():
    registry = PipeWireRegistry(runner=Recorder(FIXTURE.read_text()))
    rec = Recorder(stdout="enum ok")
    manager = FilterChainManager(
        node_name="alsa_output.pci-0000_00_1f.3.analog-stereo",
        description="T",
        runner=rec,
    )
    assert manager.live_update_supported(registry=registry) is True
    assert rec.calls[0][0][:2] == ["pw-cli", "enum-params"]


def test_live_update_supported_false_on_failure():
    registry = PipeWireRegistry(runner=Recorder(FIXTURE.read_text()))

    def failing(cmd, timeout):
        raise RuntimeError("no pw-cli")

    manager = FilterChainManager(
        node_name="alsa_output.pci-0000_00_1f.3.analog-stereo",
        description="T",
        runner=failing,
    )
    assert manager.live_update_supported(registry=registry) is False
