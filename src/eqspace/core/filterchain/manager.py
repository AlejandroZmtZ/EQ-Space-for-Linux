"""Filter-chain module management.

Renders an SPA-style ``context.modules`` config for
``libpipewire-module-filter-chain`` and loads it into the running daemon.

Loading approach: the rendered config is written to a file in a runtime
directory (``$XDG_RUNTIME_DIR/eqspace`` or a temp dir) and loaded with
``pw-cli load-module libpipewire-module-filter-chain <conf-path>``.
Unloading uses ``pw-cli destroy <module-id>``. A ``pactl`` fallback was
considered but ``pactl load-module module-filter-chain`` does not exist in
PipeWire's Pulse emulation, so ``pw-cli`` is the only portable route.

Live-param updates use ``pw-cli set-param <node-id> Props '{...}'``; whether
a given filter-chain node honours live Props updates is probed with
``pw-cli enum-params`` (live-hardware, untestable in CI).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union

from ..pipewire.registry import PipeWireRegistry, PipeWireUnavailable, Runner

COMMAND_TIMEOUT = 5.0
DEFAULT_CHANNELS = ("FL", "FR")


class FilterChainError(RuntimeError):
    """Raised when filter-chain rendering, loading or control fails."""


@dataclass(frozen=True)
class FilterSpec:
    """One filter node inside the filter-chain graph."""

    name: str
    filter_type: str = "bq_peaking"
    params: Mapping[str, Union[float, int]] = field(default_factory=dict)


def _spa_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _spa_number(value: Union[float, int]) -> str:
    if isinstance(value, int):
        return str(value)
    return repr(float(value))


class FilterChainManager:
    """Render, load, unload and live-tweak a PipeWire filter-chain."""

    def __init__(
        self,
        node_name: str = "eqspace.filter-chain",
        description: str = "EQ-Space Filter Chain",
        runner: Optional[Runner] = None,
        runtime_dir: Optional[Path] = None,
    ) -> None:
        self.node_name = node_name
        self.description = description
        self._runner = runner
        self._runtime_dir = runtime_dir
        self._module_id: Optional[int] = None
        self._conf_path: Optional[Path] = None

    @property
    def is_loaded(self) -> bool:
        return self._module_id is not None

    def render_config(
        self,
        filters: Sequence[FilterSpec],
        channels: Sequence[str] = DEFAULT_CHANNELS,
    ) -> str:
        """Render an SPA-style filter-chain config string."""
        positions = " ".join(channels)
        node_blocks = []
        for spec in filters:
            controls = " ".join(
                f"{_spa_quote(key)} = {_spa_number(value)}"
                for key, value in spec.params.items()
            )
            node_blocks.append(
                "                {\n"
                f"                    type  = builtin\n"
                f"                    name  = {_spa_quote(spec.name)}\n"
                f"                    label = {spec.filter_type}\n"
                f"                    control = {{ {controls} }}\n"
                "                }"
            )
        nodes = "\n".join(node_blocks)
        return (
            "context.modules = [\n"
            "  { name = libpipewire-module-filter-chain\n"
            "    args = {\n"
            f"      node.description = {_spa_quote(self.description)}\n"
            f"      media.name = {_spa_quote(self.description)}\n"
            "      filter.graph = {\n"
            "        nodes = [\n"
            f"{nodes}\n"
            "        ]\n"
            "      }\n"
            f"      audio.position = [ {positions} ]\n"
            "      capture.props = {\n"
            f"        node.name = {_spa_quote(self.node_name)}\n"
            "        node.passive = true\n"
            f"        audio.position = [ {positions} ]\n"
            "      }\n"
            "      playback.props = {\n"
            f"        node.name = {_spa_quote(self.node_name + '.playback')}\n"
            '        media.class = "Stream/Filter"\n'
            f"        audio.position = [ {positions} ]\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "]\n"
        )

    def load(
        self,
        filters: Sequence[FilterSpec],
        timeout: float = COMMAND_TIMEOUT,
    ) -> int:
        """Write the config and load the module; returns the module id."""
        if self.is_loaded:
            raise FilterChainError("filter chain already loaded")
        conf = self.render_config(filters)
        conf_path = self._write_conf(conf)
        try:
            output = self._run(
                ["pw-cli", "load-module", "libpipewire-module-filter-chain",
                 str(conf_path)],
                timeout,
            )
        except FilterChainError:
            conf_path.unlink(missing_ok=True)
            raise
        match = re.search(r"\b(\d+)\b", output)
        if not match:
            conf_path.unlink(missing_ok=True)
            raise FilterChainError(
                f"could not parse module id from pw-cli output: {output!r}"
            )
        self._module_id = int(match.group(1))
        self._conf_path = conf_path
        return self._module_id

    def unload(self, timeout: float = COMMAND_TIMEOUT) -> None:
        """Destroy the loaded module; a no-op when nothing is loaded."""
        if not self.is_loaded:
            return
        try:
            self._run(["pw-cli", "destroy", str(self._module_id)], timeout)
        finally:
            self._module_id = None
            if self._conf_path is not None:
                self._conf_path.unlink(missing_ok=True)
                self._conf_path = None

    def set_filter_param(
        self,
        node_name: str,
        param: str,
        value: Union[float, int],
        registry: Optional[PipeWireRegistry] = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> None:
        """Set one Props param on a running node via ``pw-cli set-param``.

        Live-hardware path, untestable in CI with a real daemon.
        """
        node_id = self._resolve_node_id(node_name, registry)
        payload = json.dumps({param: value})
        self._run(
            ["pw-cli", "set-param", str(node_id), "Props", payload], timeout
        )

    def live_update_supported(
        self,
        registry: Optional[PipeWireRegistry] = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> bool:
        """Best-effort probe: True if the node answers ``enum-params Props``.

        Live-hardware path, untestable in CI with a real daemon.
        """
        try:
            node_id = self._resolve_node_id(self.node_name, registry)
            self._run(["pw-cli", "enum-params", str(node_id), "Props"], timeout)
        except FilterChainError:
            return False
        return True

    def _resolve_node_id(
        self,
        node_name: str,
        registry: Optional[PipeWireRegistry],
    ) -> int:
        registry = registry or PipeWireRegistry(runner=self._runner)
        try:
            snapshot = registry.snapshot()
        except PipeWireUnavailable as exc:
            raise FilterChainError(str(exc)) from exc
        for node in snapshot.all_nodes():
            if node.name == node_name:
                return node.id
        raise FilterChainError(f"no node named {node_name!r} in registry")

    def _write_conf(self, conf: str) -> Path:
        runtime_dir = self._runtime_dir
        if runtime_dir is None:
            base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
            runtime_dir = Path(base) / "eqspace"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", self.node_name)
        conf_path = runtime_dir / f"{safe_name}.conf"
        conf_path.write_text(conf)
        return conf_path

    def _run(self, cmd: Sequence[str], timeout: float) -> str:
        from ..pipewire.registry import default_runner

        try:
            return (self._runner or default_runner)(cmd, timeout)
        except (FileNotFoundError, RuntimeError) as exc:
            raise FilterChainError(f"{' '.join(cmd)} failed: {exc}") from exc
