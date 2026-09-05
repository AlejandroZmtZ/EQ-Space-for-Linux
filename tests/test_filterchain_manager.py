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
    assert "bq_highshelf" in conf
    assert "audio.position = [ FL FR ]" in conf
    assert 'node.name = "eqspace.test"' in conf


def test_render_config_escapes_quotes():
    manager = FilterChainManager(node_name='bad"name', description='desc "q" \\')
    conf = manager.render_config([])
    assert '\\"' in conf
    assert "\\\\" in conf


def test_load_writes_conf_and_calls_pw_cli(tmp_path):
    rec = Recorder(stdout="module 77 libpipewire-module-filter-chain")
    manager = FilterChainManager(
        node_name="eqspace.test",
        description="Test EQ",
        runner=rec,
        runtime_dir=tmp_path,
    )
    module_id = manager.load(FILTERS)

    assert module_id == 77
    assert manager.is_loaded
    (cmd, timeout), = rec.calls
    assert cmd[:2] == ["pw-cli", "load-module"]
    assert cmd[2] == "libpipewire-module-filter-chain"
    conf_path = Path(cmd[3])
    assert conf_path.exists()
    assert "bq_peaking" in conf_path.read_text()
    assert timeout > 0


def test_load_failure_raises(tmp_path):
    def failing(cmd, timeout):
        raise RuntimeError("boom")

    manager = FilterChainManager(
        node_name="eqspace.test", description="T", runner=failing, runtime_dir=tmp_path
    )
    with pytest.raises(FilterChainError):
        manager.load(FILTERS)
    assert not manager.is_loaded


def test_unload_destroys_module(tmp_path):
    rec = Recorder(stdout="module 77 libpipewire-module-filter-chain")
    manager = FilterChainManager(
        node_name="eqspace.test", description="T", runner=rec, runtime_dir=tmp_path
    )
    manager.load(FILTERS)
    manager.unload()

    assert not manager.is_loaded
    assert rec.calls[-1][0] == ["pw-cli", "destroy", "77"]


def test_unload_when_not_loaded_is_noop(tmp_path):
    rec = Recorder()
    manager = FilterChainManager(
        node_name="eqspace.test", description="T", runner=rec, runtime_dir=tmp_path
    )
    manager.unload()
    assert rec.calls == []


def test_set_filter_param_resolves_node_and_sets_props(tmp_path):
    dump = FIXTURE.read_text()
    rec = Recorder()

    def runner(cmd, timeout):
        rec.calls.append((list(cmd), timeout))
        if cmd[0] == "pw-dump":
            return dump
        return ""

    registry = PipeWireRegistry(runner=runner)
    manager = FilterChainManager(
        node_name="eqspace.test", description="T", runner=runner, runtime_dir=tmp_path
    )
    manager.set_filter_param(
        "alsa_output.pci-0000_00_1f.3.analog-stereo", "Freq", 250.0, registry=registry
    )

    cmd = rec.calls[-1][0]
    assert cmd[:3] == ["pw-cli", "set-param", "48"]
    assert cmd[3] == "Props"
    assert '"Freq": 250.0' in cmd[4]


def test_set_filter_param_unknown_node_raises(tmp_path):
    registry = PipeWireRegistry(runner=Recorder(FIXTURE.read_text()))
    manager = FilterChainManager(
        node_name="eqspace.test", description="T", runner=Recorder(), runtime_dir=tmp_path
    )
    with pytest.raises(FilterChainError, match="no node named"):
        manager.set_filter_param("missing", "Freq", 1.0, registry=registry)


def test_live_update_supported_true_on_success(tmp_path):
    registry = PipeWireRegistry(runner=Recorder(FIXTURE.read_text()))
    rec = Recorder(stdout="enum ok")
    manager = FilterChainManager(
        node_name="alsa_output.pci-0000_00_1f.3.analog-stereo",
        description="T",
        runner=rec,
        runtime_dir=tmp_path,
    )
    assert manager.live_update_supported(registry=registry) is True
    assert rec.calls[0][0][:2] == ["pw-cli", "enum-params"]


def test_live_update_supported_false_on_failure(tmp_path):
    registry = PipeWireRegistry(runner=Recorder(FIXTURE.read_text()))

    def failing(cmd, timeout):
        raise RuntimeError("no pw-cli")

    manager = FilterChainManager(
        node_name="alsa_output.pci-0000_00_1f.3.analog-stereo",
        description="T",
        runner=failing,
        runtime_dir=tmp_path,
    )
    assert manager.live_update_supported(registry=registry) is False
