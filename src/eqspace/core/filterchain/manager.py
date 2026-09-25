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

The owning ``pw-cli`` continuously prints registry events. Its stdout must
be drained after loading: a full pipe blocks the process that hosts the
filter-chain node, and subsequent ``set-param`` calls can time out. Live
updates use one-shot ``pw-cli set-param <node-id> Props '{...}'`` commands.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import select
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence, Union

from ..pipewire.registry import PipeWireRegistry, PipeWireUnavailable, Runner

COMMAND_TIMEOUT = 5.0
logger = logging.getLogger(__name__)
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
        self._output_thread: Optional[threading.Thread] = None
        self._active_filters: Optional[tuple[FilterSpec, ...]] = None
        self._active_channels: Optional[tuple[str, ...]] = None

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

        graph_parts = [f"nodes = [ {nodes} ]"]
        if len(filters) > 1:
            links = " ".join(
                f"{{ output = {_spa_quote(f'{filters[i].name}:Out')} input = {_spa_quote(f'{filters[i+1].name}:In')} }}"
                for i in range(len(filters) - 1)
            )
            graph_parts.append(f"links = [ {links} ]")
        graph = " ".join(graph_parts)

        return (
            f"node.description = {_spa_quote(self.description)} "
            f"media.name = {_spa_quote(self.description)} "
            f"filter.graph = {{ {graph} }} "
            f"audio.channels = {len(channels)} "
            f"audio.position = [ {positions} ] "
            "capture.props = { "
            f"node.name = {_spa_quote(self.node_name)} "
            'media.class = "Audio/Sink" '
            f"audio.channels = {len(channels)} "
            f"audio.position = [ {positions} ] }} "
            "playback.props = { "
            f"node.name = {_spa_quote(self.node_name + '.playback')} "
            "node.passive = true "
            f"audio.channels = {len(channels)} "
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

        graph_sections = [
            "        nodes = [\n"
            f"{nodes}\n"
            "        ]\n"
        ]
        if len(filters) > 1:
            links = "\n".join(
                f"          {{ output = {_spa_quote(f'{filters[i].name}:Out')} input = {_spa_quote(f'{filters[i+1].name}:In')} }}"
                for i in range(len(filters) - 1)
            )
            graph_sections.append(
                "        links = [\n"
                f"{links}\n"
                "        ]\n"
            )
        graph = "".join(graph_sections)

        return (
            "context.modules = [\n"
            "  { name = libpipewire-module-filter-chain\n"
            "    args = {\n"
            f"      node.description = {_spa_quote(self.description)}\n"
            f"      media.name = {_spa_quote(self.description)}\n"
            "      filter.graph = {\n"
            f"{graph}"
            "      }\n"
            f"      audio.channels = {len(channels)}\n"
            f"      audio.position = [ {positions} ]\n"
            "      capture.props = {\n"
            f"        node.name = {_spa_quote(self.node_name)}\n"
            '        media.class = "Audio/Sink"\n'
            f"        audio.channels = {len(channels)}\n"
            f"        audio.position = [ {positions} ]\n"
            "      }\n"
            "      playback.props = {\n"
            f"        node.name = {_spa_quote(self.node_name + '.playback')}\n"
            "        node.passive = true\n"
            f"        audio.channels = {len(channels)}\n"
            f"        audio.position = [ {positions} ]\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "]\n"
        )

    def load(
        self,
        filters: Sequence[FilterSpec],
        channels: Sequence[str] = DEFAULT_CHANNELS,
        timeout: float = COMMAND_TIMEOUT,
    ) -> int:
        """Load the module via a persistent pw-cli subprocess; returns its id."""
        if self.is_loaded:
            raise FilterChainError("filter chain already loaded")
        if "'" in self.node_name or "'" in self.description:
            raise FilterChainError("node name/description must not contain quotes")
        args = self.render_args(filters, channels=channels)
        process = self._popen(
            ["pw-cli"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        try:
            startup = self._read_until_prompt(process, timeout)
            if ">>" not in startup:
                detail = startup.strip().splitlines()[-1] if startup.strip() else "no prompt"
                raise FilterChainError(f"pw-cli could not connect: {detail}")
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
        self._active_filters = tuple(filters)
        self._active_channels = tuple(channels)
        self._output_thread = threading.Thread(
            target=self._drain_output,
            args=(process,),
            name="eqspace-pw-cli-output",
            daemon=True,
        )
        self._output_thread.start()
        return self._module_id

    def unload(self, timeout: float = COMMAND_TIMEOUT) -> None:
        """Destroy the loaded module; a no-op when nothing is loaded."""
        if not self.is_loaded:
            return
        process = self._process
        output_thread = self._output_thread
        module_id = self._module_id
        self._module_id = None
        self._process = None
        self._output_thread = None
        self._active_filters = None
        self._active_channels = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write(f"destroy {module_id}\nquit\n")
                process.stdin.flush()
            process.wait(timeout=timeout)
        except Exception:
            self._kill(process)
        if output_thread is not None:
            output_thread.join(timeout=1.0)

    @staticmethod
    def _drain_output(process: "subprocess.Popen[str]") -> None:
        """Prevent the owning pw-cli's event stream from filling its pipe."""
        if process.stdout is None:
            return
        try:
            fd = process.stdout.fileno()
            while os.read(fd, 65536):
                pass
        except (OSError, ValueError):
            logger.debug("pw-cli output stream closed", exc_info=True)

    def reload(
        self,
        filters: Sequence[FilterSpec],
        channels: Sequence[str] = DEFAULT_CHANNELS,
        timeout: float = COMMAND_TIMEOUT,
    ) -> int:
        """Update controls in place when the graph shape is unchanged.

        Keeping the module alive preserves the virtual sink and its playback
        links while switching between compatible EQ presets.
        """
        next_filters = tuple(filters)
        if (
            self.is_loaded
            and self._active_filters is not None
            and self._active_channels == tuple(channels)
            and [(spec.name, spec.filter_type) for spec in self._active_filters]
            == [(spec.name, spec.filter_type) for spec in next_filters]
        ):
            params: list[Union[str, float, int]] = []
            for previous, current in zip(self._active_filters, next_filters):
                for key, value in current.params.items():
                    if previous.params.get(key) != value:
                        params.extend((f"{current.name}:{key}", value))
            if params:
                previous = self._controls(self._active_filters)
                requested = self._controls(next_filters)
                failure = "control readback did not match"
                for attempt in range(2):
                    try:
                        node_id = self._resolve_node_id(self.node_name, None)
                        output = self._run(
                            ["pw-cli", "set-param", str(node_id), "Props", json.dumps({"params": params})],
                            timeout,
                        )
                        if "error:" in output.lower():
                            raise FilterChainError(output.strip())
                    except FilterChainError as exc:
                        failure = str(exc)
                        logger.exception("EQ control update failed on attempt %s", attempt + 1)
                    try:
                        if self._readback_matches(requested, timeout):
                            break
                    except FilterChainError as exc:
                        failure = str(exc)
                    if attempt == 1:
                        try:
                            self._restore_controls(previous, timeout)
                        except FilterChainError as exc:
                            raise FilterChainError(
                                f"EQ controls unverified; direct output recommended. Update failed: {failure}; restoration failed: {exc}"
                            ) from exc
                        raise FilterChainError(f"EQ update failed; previous controls restored: {failure}")
            self._active_filters = next_filters
            assert self._module_id is not None
            return self._module_id
        if self.is_loaded:
            self.unload(timeout=timeout)
        return self.load(next_filters, channels=channels, timeout=timeout)

    @staticmethod
    def same_layout(left: Sequence[FilterSpec], right: Sequence[FilterSpec]) -> bool:
        return [(s.name, s.filter_type) for s in left] == [(s.name, s.filter_type) for s in right]

    @staticmethod
    def _controls(filters: Sequence[FilterSpec]) -> dict[str, float]:
        return {f"{spec.name}:{key}": float(value)
                for spec in filters for key, value in spec.params.items()}

    def _read_controls(self, timeout: float) -> dict[str, float]:
        try:
            raw = self._run(["pw-dump"], timeout).lstrip()
            entries, _ = json.JSONDecoder().raw_decode(raw)
            for entry in entries:
                info = entry.get("info") or {}
                if (info.get("props") or {}).get("node.name") != self.node_name:
                    continue
                for prop in (info.get("params") or {}).get("Props", []):
                    pairs = prop.get("params") if isinstance(prop, dict) else None
                    if (isinstance(pairs, list) and len(pairs) % 2 == 0
                            and any(":" in str(key) for key in pairs[::2])):
                        return {str(pairs[i]): float(pairs[i + 1]) for i in range(0, len(pairs), 2)
                                if isinstance(pairs[i + 1], (int, float))}
        except (ValueError, TypeError, KeyError) as exc:
            raise FilterChainError(f"could not parse EQ control readback: {exc}") from exc
        raise FilterChainError("EQ control readback unavailable")

    def _readback_matches(self, requested: Mapping[str, float], timeout: float) -> bool:
        actual = self._read_controls(timeout)
        return all(key in actual and math.isclose(actual[key], value, rel_tol=1e-5, abs_tol=0.002)
                   for key, value in requested.items())

    def verify_controls(self, filters: Sequence[FilterSpec], timeout: float = COMMAND_TIMEOUT) -> None:
        """Require actual Props values before reporting a loaded graph as active."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                if self._readback_matches(self._controls(filters), timeout):
                    return
            except FilterChainError:
                pass
            if time.monotonic() >= deadline:
                raise FilterChainError("EQ controls could not be verified; check PipeWire and retry")
            time.sleep(0.05)

    def _restore_controls(self, previous: Mapping[str, float], timeout: float) -> None:
        try:
            if self._readback_matches(previous, timeout):
                return
        except FilterChainError:
            pass
        node_id = self._resolve_node_id(self.node_name, None)
        pairs: list[Union[str, float]] = []
        for key, value in previous.items():
            pairs.extend((key, value))
        try:
            output = self._run(["pw-cli", "set-param", str(node_id), "Props", json.dumps({"params": pairs})], timeout)
            if "error:" in output.lower():
                raise FilterChainError(output.strip())
        except FilterChainError:
            logger.exception("previous EQ control restoration command failed")
        self.verify_controls_from_mapping(previous, timeout)

    def verify_controls_from_mapping(self, controls: Mapping[str, float], timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if self._readback_matches(controls, timeout):
                    return
            except FilterChainError:
                pass
            if time.monotonic() >= deadline:
                raise FilterChainError("previous control values could not be verified")
            time.sleep(0.05)

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
        accordingly.
        """
        node_id = self._resolve_node_id(node_name, registry)
        payload = json.dumps({"params": [param, value]})
        cmd = ["pw-cli", "set-param", str(node_id), "Props", payload]
        if self._runner is not None:
            self._run(cmd, timeout)
            return

        proc = self._popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            line = proc.stdout.readline() if proc.stdout else ""
            if "Error" in line:
                raise FilterChainError(f"set-param failed: {line.strip()}")
        finally:
            self._kill(proc)

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
            text += FilterChainManager._read_chunk(process, deadline)
            if ">>" in text:
                break
            if process.poll() is not None:
                break
        return text

    @staticmethod
    def _read_chunk(process: "subprocess.Popen[str]", deadline: float) -> str:
        assert process.stdout is not None
        fd = process.stdout.fileno()
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            return chunk.decode(errors="replace")
        return ""

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
