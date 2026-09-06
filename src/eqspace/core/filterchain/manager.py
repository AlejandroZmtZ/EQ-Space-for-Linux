"""Filter-chain module management.

Renders arguments for ``libpipewire-module-filter-chain`` and loads them
into the running daemon.

Loading approach: ``pw-cli load-module`` runs the module inside the pw-cli
process itself, so the module — and its filter node — disappear as soon as
that pw-cli process exits. :meth:`FilterChainManager.load` therefore keeps an
interactive ``pw-cli`` subprocess alive for as long as the filter chain is
loaded, feeding it ``load-module`` on stdin with the module arguments passed
inline as a single-line SPA properties string. (Passing a config file path
as the module arguments does not work: pw-cli forwards the string verbatim
to ``pw_properties_new_string``, which cannot parse a ``context.modules``
document, and the module fails to load.) Unloading writes ``destroy
<module-id>`` followed by ``quit`` to the same subprocess.

Live-param updates use the stateless one-shot ``pw-cli set-param <node-id>
Props '{...}'``; whether a given filter-chain node honours live Props
updates is probed with ``pw-cli enum-params`` (live-hardware, untestable in
CI).
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence, Union

from ..pipewire.registry import PipeWireRegistry, PipeWireUnavailable, Runner

COMMAND_TIMEOUT = 5.0
DEFAULT_CHANNELS = ("FL", "FR")

PopenFactory = Callable[..., "subprocess.Popen[str]"]


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


def _spawn_pw_cli(cmd: Sequence[str], **kwargs: object) -> "subprocess.Popen[str]":
    return subprocess.Popen(cmd, **kwargs)  # type: ignore[arg-type]


class FilterChainManager:
    """Render, load, unload and live-tweak a PipeWire filter-chain."""

    def __init__(
        self,
        node_name: str = "eqspace.filter-chain",
        description: str = "EQ-Space Filter Chain",
        runner: Optional[Runner] = None,
        popen: PopenFactory = _spawn_pw_cli,
    ) -> None:
        self.node_name = node_name
        self.description = description
        self._runner = runner
        self._popen = popen
        self._module_id: Optional[int] = None
        self._process: Optional["subprocess.Popen[str]"] = None

    @property
    def is_loaded(self) -> bool:
        return self._module_id is not None

    def render_args(
        self,
        filters: Sequence[FilterSpec],
        channels: Sequence[str] = DEFAULT_CHANNELS,
    ) -> str:
        """Render the module arguments as a single-line SPA properties string.

        This is the format ``pw-cli load-module`` expects: whitespace-separated
        ``key = value`` pairs (parseable by ``pw_properties_new_string``),
        with no newlines because interactive pw-cli reads one command per line.
        """
        positions = " ".join(channels)
        node_blocks = []
        for spec in filters:
            controls = " ".join(
                f"{_spa_quote(key)} = {_spa_number(value)}"
                for key, value in spec.params.items()
            )
            node_blocks.append(
                "{ type = builtin "
                f"name = {_spa_quote(spec.name)} "
                f"label = {spec.filter_type} "
                f"control = {{ {controls} }} }}"
            )
        nodes = " ".join(node_blocks)
        return (
            f"node.description = {_spa_quote(self.description)} "
            f"media.name = {_spa_quote(self.description)} "
            f"filter.graph = {{ nodes = [ {nodes} ] }} "
            f"audio.position = [ {positions} ] "
            "capture.props = { "
            f"node.name = {_spa_quote(self.node_name)} "
            "node.passive = true "
            f"audio.position = [ {positions} ] }} "
            "playback.props = { "
            f"node.name = {_spa_quote(self.node_name + '.playback')} "
            'media.class = "Stream/Filter" '
            f"audio.position = [ {positions} ] }}"
        )

    def render_config(
        self,
        filters: Sequence[FilterSpec],
        channels: Sequence[str] = DEFAULT_CHANNELS,
    ) -> str:
        """Render the equivalent ``context.modules`` config-file snippet.

        Useful for permanent installs via ``pipewire.conf.d`` drop-ins; the
        live path uses :meth:`render_args` instead.
        """
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
        """Load the module via a persistent pw-cli subprocess; returns its id."""
        if self.is_loaded:
            raise FilterChainError("filter chain already loaded")
        if "'" in self.node_name or "'" in self.description:
            raise FilterChainError("node name/description must not contain quotes")
        args = self.render_args(filters)
        process = self._popen(
            ["pw-cli"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        try:
            self._read_until_prompt(process, timeout)  # discard startup banner
            assert process.stdin is not None
            process.stdin.write(
                f"load-module libpipewire-module-filter-chain '{args}'\n"
            )
            process.stdin.flush()
            output = self._read_output(process, timeout)
        except Exception:
            self._kill(process)
            raise
        match = re.search(r"@module:(\d+)", output)
        if not match:
            self._kill(process)
            raise FilterChainError(
                f"could not parse module id from pw-cli output: {output!r}"
            )
        self._module_id = int(match.group(1))
        self._process = process
        return self._module_id

    def unload(self, timeout: float = COMMAND_TIMEOUT) -> None:
        """Destroy the loaded module; a no-op when nothing is loaded."""
        if not self.is_loaded:
            return
        process = self._process
        module_id = self._module_id
        self._module_id = None
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write(f"destroy {module_id}\nquit\n")
                process.stdin.flush()
            process.wait(timeout=timeout)
        except Exception:
            self._kill(process)

    def set_filter_param(
        self,
        node_name: str,
        param: str,
        value: Union[float, int],
        registry: Optional[PipeWireRegistry] = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> None:
        """Set one filter control param on a running node via ``pw-cli set-param``.

        Filter-chain control params are exposed in the node's ``Props``
        param under the ``params`` struct as ``"<filter-name>:<Param>"``
        pairs, e.g. ``"band_0:Freq"``; the payload wraps *param*/*value*
        accordingly. Live-hardware path, untestable in CI.
        """
        node_id = self._resolve_node_id(node_name, registry)
        payload = json.dumps({"params": [param, value]})
        self._run(
            ["pw-cli", "set-param", str(node_id), "Props", payload], timeout
        )

    def live_update_supported(
        self,
        registry: Optional[PipeWireRegistry] = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> bool:
        """Best-effort probe: True if the node answers ``enum-params Props``.

        Live-hardware path. Verified against PipeWire 1.0.5: a loaded
        filter-chain node answers ``enum-params Props`` and honours
        ``set-param Props`` updates of the ``params`` struct (e.g.
        ``band_0:Freq``), so live updates work while the module is loaded.
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

    @staticmethod
    def _read_until_prompt(process: "subprocess.Popen[str]", timeout: float) -> str:
        """Read pw-cli output until the interactive prompt appears."""
        deadline = time.monotonic() + timeout
        text = ""
        while time.monotonic() < deadline:
            text = FilterChainManager._read_chunk(process, deadline)
            if text.rstrip().endswith(">>"):
                break
        return text

    @staticmethod
    def _read_chunk(process: "subprocess.Popen[str]", deadline: float) -> str:
        assert process.stdout is not None
        fd = process.stdout.fileno()
        chunks: list[bytes] = []
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                if chunks:
                    break  # idle gap: assume the burst is complete
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode(errors="replace")

    @staticmethod
    def _read_output(process: "subprocess.Popen[str]", timeout: float) -> str:
        """Read pw-cli output until the module id or an error line appears."""
        deadline = time.monotonic() + timeout
        text = ""
        while time.monotonic() < deadline:
            text += FilterChainManager._read_chunk(process, deadline)
            if "@module:" in text or "Error:" in text:
                break
        return text

    @staticmethod
    def _kill(process: "subprocess.Popen[str]") -> None:
        try:
            process.kill()
            process.wait(timeout=1.0)
        except Exception:
            pass

    def _run(self, cmd: Sequence[str], timeout: float) -> str:
        from ..pipewire.registry import default_runner

        try:
            return (self._runner or default_runner)(cmd, timeout)
        except (FileNotFoundError, RuntimeError) as exc:
            raise FilterChainError(f"{' '.join(cmd)} failed: {exc}") from exc
