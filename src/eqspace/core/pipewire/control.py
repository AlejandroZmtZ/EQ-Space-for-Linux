"""PipeWire node control via ``wpctl`` / ``pw-cli`` subprocesses.

Every function shells out with a timeout and raises
:class:`PipeWireControlError` on failure. These are live-hardware paths:
they are exercised here only through injected runners, not in CI.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .registry import PipeWireRegistry, Runner, default_runner

COMMAND_TIMEOUT = 5.0


class PipeWireControlError(RuntimeError):
    """Raised when a PipeWire control command fails."""


def _run(
    cmd: Sequence[str],
    runner: Optional[Runner],
    timeout: float,
) -> str:
    try:
        return (runner or default_runner)(cmd, timeout)
    except (FileNotFoundError, RuntimeError) as exc:
        raise PipeWireControlError(f"{cmd[0]} {' '.join(cmd[1:])} failed: {exc}") from exc


def set_volume(
    node_id: int,
    volume: float,
    runner: Optional[Runner] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> None:
    """Set the volume of a node (0.0–1.0, where 1.0 is 100%)."""
    if not 0.0 <= volume <= 1.0:
        raise ValueError(f"volume must be within [0.0, 1.0], got {volume}")
    _run(["wpctl", "set-volume", str(node_id), f"{volume:g}"], runner, timeout)


def set_mute(
    node_id: int,
    mute: bool,
    runner: Optional[Runner] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> None:
    """Mute or unmute a node."""
    _run(["wpctl", "set-mute", str(node_id), "1" if mute else "0"], runner, timeout)


def set_default_sink(
    name: str,
    registry: Optional[PipeWireRegistry] = None,
    runner: Optional[Runner] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> None:
    """Make the sink named *name* the session default.

    ``wpctl set-default`` wants a numeric id, so the name is resolved
    against a fresh registry snapshot.
    """
    registry = registry or PipeWireRegistry(runner=runner)
    for sink in registry.snapshot().sinks:
        if sink.name == name:
            _run(["wpctl", "set-default", str(sink.id)], runner, timeout)
            return
    raise PipeWireControlError(f"no such sink: {name}")


def move_stream(
    stream_id: int,
    sink_id: int,
    runner: Optional[Runner] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> None:
    """Move a playback stream to a different sink.

    Implemented by setting the ``target.object`` session metadata key, which
    WirePlumber honours by relinking the stream. Best-effort: depends on the
    session manager's policy and is untestable in CI.
    """
    _run(
        ["pw-metadata", str(stream_id), "target.object", str(sink_id)],
        runner,
        timeout,
    )
