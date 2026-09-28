import io
import json
import os
import time
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

    def __init__(self, load_output: str, startup_output: str = "Welcome to PipeWire\n>> "):
        self.commands: list[str] = []
        self.stdin = io.StringIO()
        read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "r", encoding="utf-8", buffering=1)
        self._load_output = load_output
        self.killed = False
        self.wait_calls = 0
        self._write_closed = False
        os.write(self._write_fd, startup_output.encode())
        self._real_write = self.stdin.write
        self.stdin.write = self._stdin_write  # type: ignore[method-assign]

    def _stdin_write(self, data: str) -> int:
        self.commands.append(data)
        if data.startswith("load-module"):
            os.write(self._write_fd, self._load_output.encode())
        return len(data)

    def wait(self, timeout=None):
        self.wait_calls += 1
        self._close_writer()
        return 0

    def kill(self):
        self.killed = True
        self._close_writer()

    def _close_writer(self):
        if not self._write_closed:
            os.close(self._write_fd)
            self._write_closed = True

    def poll(self):
        return None


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
    assert 'media.class = "Audio/Sink"' in conf
    assert "node.passive = true" in conf
    assert "node.autoconnect = false" in conf
    assert "links = [" in conf
    assert '{ output = "band_0:Out" input = "band_1:In" }' in conf
    assert "audio.channels = 2" in conf


def test_render_args_is_single_line_properties_string():
    manager = FilterChainManager(node_name="eqspace.test", description="Test EQ")
    args = manager.render_args(FILTERS)

    assert "\n" not in args
    assert not args.startswith("{")
    assert 'node.name = "eqspace.test"' in args
    assert 'node.name = "eqspace.test.playback"' in args
    assert 'name = "band_0" label = bq_peaking' in args
    assert 'name = "band_1" label = bq_highshelf' in args
    assert '"Freq" = 100.0' in args
    assert '"Freq" = 8000.0' in args
    assert "audio.position = [ FL FR ]" in args
    assert "audio.channels = 2" in args
    assert 'media.class = "Audio/Sink"' in args
    assert "node.passive = true" in args
    assert "node.autoconnect = false" in args
    assert "links = [" in args
    assert '{ output = "band_0:Out" input = "band_1:In" }' in args


def test_render_args_contains_expected_keys():
    manager = FilterChainManager(node_name="eqspace.test", description="Test EQ")
    args = manager.render_args(FILTERS)

    assert 'node.name = "eqspace.test"' in args
    assert 'node.name = "eqspace.test.playback"' in args
    assert 'media.class = "Audio/Sink"' in args
    assert "node.passive = true" in args
    assert "node.autoconnect = false" in args
    assert "links = [" in args
    assert '{ output = "band_0:Out" input = "band_1:In" }' in args


def test_render_args_generates_series_links():
    manager = FilterChainManager(node_name="eqspace.test", description="Test EQ")
    args = manager.render_args(FILTERS)

    assert "links = [" in args
    assert '{ output = "band_0:Out" input = "band_1:In" }' in args


def test_render_args_single_and_multiple_series_links():
    manager = FilterChainManager(node_name="eqspace.test", description="Test EQ")

    # Single filter: no links between filters
    single_args = manager.render_args([FILTERS[0]])
    assert "links = [" not in single_args
    assert "audio.channels = 2" in single_args

    # Three filters: two sequential links
    three_filters = [
        FilterSpec(name="band_0", filter_type="bq_peaking", params={"Freq": 100.0}),
        FilterSpec(name="band_1", filter_type="bq_peaking", params={"Freq": 1000.0}),
        FilterSpec(name="band_2", filter_type="bq_highshelf", params={"Freq": 10000.0}),
    ]
    three_args = manager.render_args(three_filters)
    assert '{ output = "band_0:Out" input = "band_1:In" }' in three_args
    assert '{ output = "band_1:Out" input = "band_2:In" }' in three_args
    assert "audio.channels = 2" in three_args


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
    assert 'node.name = "eqspace.test.playback"' in command
    assert 'media.class = "Audio/Sink"' in command
    assert "node.passive = true" in command
    assert "links = [" in command
    assert '{ output = "band_0:Out" input = "band_1:In" }' in command
    assert "audio.channels = 2" in command


def test_load_accepts_prompt_followed_by_live_registry_events():
    proc = FakePwCli(
        "1 = @module:77\npipewire-0>> ",
        startup_output="Welcome to PipeWire\n>> remote 0 added global: id 128\n",
    )
    manager = FilterChainManager(popen=fake_popen(proc))
    assert manager.load(FILTERS) == 77
    manager.unload()


def test_loaded_pw_cli_drains_continuous_registry_output():
    proc = FakePwCli("1 = @module:77\npipewire-0>> ")
    manager = FilterChainManager(popen=fake_popen(proc))
    manager.load(FILTERS)
    # Registry events exceed a pipe's capacity in a routed desktop session.
    # A bounded nonblocking writer makes the regression fail without hanging.
    os.set_blocking(proc._write_fd, False)
    payload = b"global changed\n" * 16384
    written = 0
    deadline = time.monotonic() + 2
    try:
        while written < len(payload) and time.monotonic() < deadline:
            try:
                written += os.write(proc._write_fd, payload[written:])
            except BlockingIOError:
                time.sleep(0.001)
        assert written == len(payload)
    finally:
        manager.unload()
    assert manager._output_thread is None


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


def test_reload_same_graph_updates_controls_without_destroying_sink():
    proc = FakePwCli("1 = @module:77\npipewire-0>> ")
    calls = []
    values = FilterChainManager._controls(FILTERS)
    def dump():
        pairs = [item for key, value in values.items() for item in (key, value)]
        return json.dumps([{
            "id": 95,
            "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.name": "eqspace.test", "media.class": "Audio/Sink"},
                     "params": {"Props": [{"params": pairs}]}},
        }])

    def runner(cmd, timeout):
        calls.append(list(cmd))
        if cmd[0] == "pw-dump":
            return dump()
        values.update(dict(zip(json.loads(cmd[4])["params"][::2], json.loads(cmd[4])["params"][1::2])))
        return "Object: updated Props"

    manager = FilterChainManager(node_name="eqspace.test", runner=runner, popen=fake_popen(proc))
    manager.load(FILTERS)
    changed = [
        FilterSpec(name="band_0", filter_type="bq_peaking", params={"Freq": 120.0, "Gain": 8.0, "Q": 1.4}),
        FILTERS[1],
    ]
    assert manager.reload(changed) == 77
    assert manager.is_loaded
    assert len(proc.commands) == 1
    commands = [cmd for cmd in calls if cmd[:2] == ["pw-cli", "set-param"]]
    assert len(commands) == 1
    assert commands[0][:4] == ["pw-cli", "set-param", "95", "Props"]
    assert json.loads(commands[0][4]) == {
        "params": ["band_0:Freq", 120.0, "band_0:Gain", 8.0]
    }
    manager.unload()


def test_failed_control_update_keeps_loaded_sink():
    proc = FakePwCli("1 = @module:77\npipewire-0>> ")
    pairs = [item for key, value in FilterChainManager._controls(FILTERS).items()
             for item in (key, value)]
    dump = json.dumps([{
        "id": 95,
        "type": "PipeWire:Interface:Node",
        "info": {"props": {"node.name": "eqspace.test", "media.class": "Audio/Sink"},
                 "params": {"Props": [{"params": pairs}]}},
    }])

    def runner(cmd, timeout):
        if cmd[0] == "pw-dump":
            return dump
        raise RuntimeError("set-param timed out")

    manager = FilterChainManager(node_name="eqspace.test", runner=runner, popen=fake_popen(proc))
    manager.load(FILTERS)
    changed = [
        FilterSpec(name="band_0", filter_type="bq_peaking", params={"Freq": 120.0, "Gain": 8.0, "Q": 1.4}),
        FILTERS[1],
    ]
    with pytest.raises(FilterChainError, match="set-param timed out"):
        manager.reload(changed)
    assert manager.is_loaded
    assert len(proc.commands) == 1
    assert proc.commands[0].startswith("load-module")
    manager.unload()


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


def test_module_args_manager_loads_and_starts_output_thread():
    from eqspace.ui.module_args_manager import ModuleArgsManager

    proc = FakePwCli("1 = @module:88\npipewire-0>> ")
    manager = ModuleArgsManager(popen=fake_popen(proc))
    args = "node.description = \"Test\" media.name = \"Test\""
    mod_id = manager.load_args(args)
    assert mod_id == 88
    assert manager.is_loaded
    assert manager._output_thread is not None
    assert manager._output_thread.is_alive()

    # Verify background draining works without blocking
    os.set_blocking(proc._write_fd, False)
    payload = b"event chunk\n" * 1024
    written = 0
    deadline = time.monotonic() + 1.0
    while written < len(payload) and time.monotonic() < deadline:
        try:
            written += os.write(proc._write_fd, payload[written:])
        except BlockingIOError:
            time.sleep(0.001)
    assert written == len(payload)

    manager.unload()
    assert not manager.is_loaded
    assert manager._output_thread is None


def test_module_args_manager_update_args_transitions_cleanly():
    from eqspace.ui.module_args_manager import ModuleArgsManager

    proc1 = FakePwCli("1 = @module:88\npipewire-0>> ")
    proc2 = FakePwCli("1 = @module:99\npipewire-0>> ")
    procs = [proc1, proc2]

    def popen_multi(cmd, **kwargs):
        return procs.pop(0)

    manager = ModuleArgsManager(popen=popen_multi)
    manager.load_args("node.description = \"V1\"")
    assert manager._module_id == 88

    # update_args should unload first and load second
    mod_id = manager.update_args("node.description = \"V2\"")
    assert mod_id == 99
    assert manager._module_id == 99
    assert proc1.commands[-1] == "destroy 88\nquit\n"
    assert proc1.wait_calls == 1

    manager.unload()
    assert not manager.is_loaded


def test_read_controls_skips_removal_event_before_initial_snapshot():
    import json
    snapshot = [{"id": 99, "type": "PipeWire:Interface:Node", "info": {
        "props": {"node.name": "eqspace.test"},
        "params": {"Props": [{"params": ["preamp:Mult", .25, "preamp:Add", 0]}]}}}]
    raw = '[{"id": 55, "info": null}]\n' + json.dumps(snapshot)
    manager = FilterChainManager(node_name='eqspace.test', runner=lambda *_: raw)
    assert manager._read_controls(1) == {'preamp:Mult': .25, 'preamp:Add': 0}
