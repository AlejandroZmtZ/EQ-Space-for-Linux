"""PipeWire node registry built from ``pw-dump`` JSON snapshots."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

DUMP_TIMEOUT = 5.0

Runner = Callable[[Sequence[str], float], str]


def default_runner(cmd: Sequence[str], timeout: float) -> str:
    """Run *cmd* and return stdout; raises on missing binary or non-zero exit."""
    try:
        result = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"{cmd[0]} exited {exc.returncode}: {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{cmd[0]} timed out after {timeout}s") from exc
    return result.stdout


class PipeWireUnavailable(RuntimeError):
    """Raised when the PipeWire daemon or its CLI tools are not reachable."""


@dataclass(frozen=True)
class PwNode:
    id: int
    name: str
    app_name: Optional[str]
    media_class: str
    volume: Optional[float]
    mute: Optional[bool]
    serial: Optional[int] = None


@dataclass(frozen=True)
class PwSnapshot:
    sinks: tuple[PwNode, ...] = field(default_factory=tuple)
    sources: tuple[PwNode, ...] = field(default_factory=tuple)
    streams: tuple[PwNode, ...] = field(default_factory=tuple)

    def all_nodes(self) -> tuple[PwNode, ...]:
        return self.sinks + self.sources + self.streams


def _props(entry: dict[str, Any]) -> dict[str, Any]:
    info = entry.get("info") or {}
    return info.get("props") or {}


def _pw_node(entry: dict[str, Any]) -> PwNode:
    info = entry.get("info") or {}
    props = info.get("props") or {}
    params = info.get("params") or {}
    volume: Optional[float] = None
    mute: Optional[bool] = None
    props_param = params.get("Props")
    if isinstance(props_param, list) and props_param:
        first = props_param[0]
        if isinstance(first, dict):
            raw_volume = first.get("volume")
            if isinstance(raw_volume, (int, float)):
                volume = float(raw_volume)
            raw_mute = first.get("mute")
            if isinstance(raw_mute, bool):
                mute = raw_mute
    app_name = props.get("application.name")
    raw_serial = props.get("object.serial")
    serial: Optional[int] = None
    if raw_serial is not None:
        try:
            serial = int(raw_serial)
        except (TypeError, ValueError):
            serial = None
    return PwNode(
        id=_node_id(entry),
        name=str(props.get("node.name", "")),
        app_name=str(app_name) if app_name is not None else None,
        media_class=str(props.get("media.class", "")),
        volume=volume,
        mute=mute,
        serial=serial,
    )


def _node_id(entry: dict[str, Any]) -> int:
    info = entry.get("info") or {}
    props = info.get("props") or {}
    raw_id = entry.get("id", props.get("object.id"))
    try:
        return int(raw_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"malformed pw-dump entry id: {raw_id!r}") from exc


def parse_pw_dump(data: Any) -> PwSnapshot:
    """Classify a decoded ``pw-dump`` document into sinks, sources and streams."""
    if not isinstance(data, list):
        raise PipeWireUnavailable("pw-dump returned unexpected data (not a list)")
    sinks: list[PwNode] = []
    sources: list[PwNode] = []
    streams: list[PwNode] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        media_class = _props(entry).get("media.class", "")
        try:
            node = _pw_node(entry)
        except ValueError:
            logger.warning("skipping pw-dump entry with malformed id: %r", entry.get("id"))
            continue
        if media_class == "Audio/Sink":
            sinks.append(node)
        elif media_class == "Audio/Source":
            sources.append(node)
        elif media_class.startswith("Stream/"):
            streams.append(node)
    return PwSnapshot(
        sinks=tuple(sinks), sources=tuple(sources), streams=tuple(streams)
    )


class MonitorHandle:
    """Handle for a running monitor thread; call :meth:`stop` to terminate."""

    def __init__(self, thread: threading.Thread, stop_event: threading.Event) -> None:
        self._thread = thread
        self._stop_event = stop_event

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self._thread.join(timeout)


class PipeWireRegistry:
    """Snapshots and polls the PipeWire object registry via ``pw-dump``."""

    def __init__(
        self,
        dump_cmd: Sequence[str] = ("pw-dump",),
        timeout: float = DUMP_TIMEOUT,
        runner: Optional[Runner] = None,
    ) -> None:
        self._dump_cmd = tuple(dump_cmd)
        self._timeout = timeout
        self._runner: Runner = runner or default_runner

    def snapshot(self) -> PwSnapshot:
        """Take one snapshot of the registry.

        Raises :class:`PipeWireUnavailable` when ``pw-dump`` is missing,
        fails, or returns invalid JSON.
        """
        try:
            raw = self._runner(self._dump_cmd, self._timeout)
        except FileNotFoundError as exc:
            raise PipeWireUnavailable(
                f"{self._dump_cmd[0]} not found; is PipeWire installed?"
            ) from exc
        except RuntimeError as exc:
            raise PipeWireUnavailable(str(exc)) from exc
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise PipeWireUnavailable(f"pw-dump returned invalid JSON: {exc}") from exc
        return parse_pw_dump(data)

    def monitor(
        self,
        callback: Callable[[PwSnapshot], None],
        interval: float = 0.5,
    ) -> MonitorHandle:
        """Poll every *interval* seconds; call *callback* whenever the
        snapshot changes. Transient dump errors and exceptions raised by
        *callback* are logged and skipped so neither a restarting daemon nor
        a faulty callback kills the monitor thread."""

        stop_event = threading.Event()

        def _try_snapshot() -> Optional[PwSnapshot]:
            try:
                return self.snapshot()
            except PipeWireUnavailable as exc:
                logger.warning("pw-dump snapshot failed, skipping poll: %s", exc)
                return None

        def loop() -> None:
            last = _try_snapshot()
            while not stop_event.is_set():
                stop_event.wait(interval)
                if stop_event.is_set():
                    break
                snapshot = _try_snapshot()
                if snapshot is not None and snapshot != last:
                    last = snapshot
                    try:
                        callback(snapshot)
                    except Exception:
                        logger.exception("registry monitor callback raised")

        thread = threading.Thread(
            target=loop, name="eqspace-pw-monitor", daemon=True
        )
        thread.start()
        return MonitorHandle(thread, stop_event)
