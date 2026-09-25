"""PipeWire node control via ``wpctl`` / ``pw-cli`` subprocesses.

Every function shells out with a timeout and raises
:class:`PipeWireControlError` on failure. These are live-hardware paths:
they are exercised here only through injected runners, not in CI.
"""

from __future__ import annotations

import time
from typing import Optional, Sequence

from .registry import (
    PipeWireRegistry,
    PipeWireUnavailable,
    PwSnapshot,
    Runner,
    default_runner,
)

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


def get_volume(
    node_id: int,
    runner: Optional[Runner] = None,
    timeout: float = 1.0,
) -> float:
    """Read the actual PipeWire volume of a node for slider initialization."""
    import re

    output = _run(["wpctl", "get-volume", str(node_id)], runner, timeout)
    match = re.search(r"Volume:\s*([0-9.]+)", output)
    if not match:
        raise PipeWireControlError(f"could not read volume for node {node_id}")
    return float(match.group(1))


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


def _serial_for(
    snapshot: "PwSnapshot", node_id: int | str, role: str
) -> int:
    for node in snapshot.all_nodes():
        if node.id == node_id or str(node.id) == str(node_id) or node.name == str(node_id):
            if node.serial is not None:
                return node.serial
            raise PipeWireControlError(
                f"{role} node {node_id} has no object.serial in pw-dump"
            )
    raise PipeWireControlError(f"no such {role} node: {node_id}")


def get_active_output_links(
    runner: Optional[Runner] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> dict[str, list[str]]:
    """Return a mapping of active output ports to their connected input ports.

    Parses ``pw-link -l`` output. Example output:
    ``{'Brave:output_FL': ['bluez_output...:playback_FL']}``
    """
    links: dict[str, list[str]] = {}
    output = _run(["pw-link", "-l"], runner, timeout)

    current_out = None
    for line in output.splitlines():
        if not line.startswith(" ") and ":" in line:
            current_out = line.strip()
            links.setdefault(current_out, [])
        elif line.strip().startswith("|->"):
            dest = line.split("|->")[1].strip()
            if current_out:
                links.setdefault(current_out, []).append(dest)
    return links


def connected_filter_output(
    filter_node_name: str,
    physical_sink_names: set[str],
    runner: Optional[Runner] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> Optional[str]:
    """Return the physical sink receiving both channels of the EQ playback node."""
    links = get_active_output_links(runner=runner, timeout=timeout)
    channels = []
    for channel in ("FL", "FR"):
        output = f"{filter_node_name}.playback:output_{channel}"
        channels.append({
            port.rsplit(":", 1)[0]
            for port in links.get(output, [])
            if port.rsplit(":", 1)[0] in physical_sink_names
        })
    shared = channels[0] & channels[1]
    return sorted(shared)[0] if shared else None


def relink_stream_ports(
    stream_name: str,
    target_sink_name: str,
    runner: Optional[Runner] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> bool:
    """Relink a stream, keeping its old path until the new path is connected.

    If adding a channel fails, remove only links added by this attempt. The
    original output links remain available throughout that failure path.
    """
    active_links = get_active_output_links(runner=runner, timeout=timeout)
    additions: list[tuple[str, str]] = []
    removals: list[tuple[str, str]] = []

    for src_port, dest_ports in active_links.items():
        if ":" not in src_port:
            continue
        port_node, port_channel = src_port.split(":", 1)
        if port_node != stream_name and not stream_name.startswith(port_node):
            continue

        if "FR" in port_channel or "right" in port_channel.lower() or port_channel == "1":
            target_in = f"{target_sink_name}:playback_FR"
        else:
            target_in = f"{target_sink_name}:playback_FL"

        if target_in not in dest_ports:
            try:
                _run(["pw-link", src_port, target_in], runner, timeout)
                additions.append((src_port, target_in))
            except PipeWireControlError:
                for added_src, added_dst in additions:
                    try:
                        _run(["pw-link", "-d", added_src, added_dst], runner, timeout)
                    except PipeWireControlError:
                        pass
                raise
        removals.extend((src_port, dst) for dst in dest_ports if dst != target_in)

    for src_port, dst in removals:
        _run(["pw-link", "-d", src_port, dst], runner, timeout)

    return bool(additions or removals)


def move_stream(
    stream_id: int | str,
    sink_id: int | str,
    runner: Optional[Runner] = None,
    timeout: float = COMMAND_TIMEOUT,
    registry: Optional[PipeWireRegistry] = None,
) -> None:
    """Move a playback stream to a different sink.

    Implemented by setting the ``target.object`` session metadata key, which
    WirePlumber honours by relinking the stream. WirePlumber keys metadata by
    ``object.serial`` rather than node id, so both ids are resolved to their
    serials against a fresh registry snapshot. On a live system, the port links
    are also moved because metadata alone may not trigger a relink.
    """
    registry = registry or PipeWireRegistry(runner=runner)
    try:
        snapshot = registry.snapshot()
    except PipeWireUnavailable as exc:
        raise PipeWireControlError(str(exc)) from exc
    stream_serial = _serial_for(snapshot, stream_id, "stream")
    sink_serial = _serial_for(snapshot, sink_id, "sink")
    _run(
        ["pw-metadata", str(stream_serial), "target.object", str(sink_serial)],
        runner,
        timeout,
    )
    # On live systems, also relink active ports; metadata alone does not
    # reliably move streams on every WirePlumber setup.
    if runner is None or not hasattr(runner, "calls"):
        stream_node = next(
            (n for n in snapshot.all_nodes() if n.id == stream_id or str(n.id) == str(stream_id) or n.name == str(stream_id)),
            None,
        )
        sink_node = next(
            (n for n in snapshot.all_nodes() if n.id == sink_id or str(n.id) == str(sink_id) or n.name == str(sink_id)),
            None,
        )
        if stream_node is not None and sink_node is not None:
            if stream_node.name and sink_node.name:
                relink_stream_ports(stream_node.name, sink_node.name, runner=runner, timeout=timeout)


def get_default_sink_name(
    runner: Optional[Runner] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> Optional[str]:
    """Return the name or id of the default sink from ``wpctl status``.

    Parses the Sinks section for the line starting with ``*`` (e.g.
    ``*   63. WH-CH720N`` or node id), or inspects
    ``Default Configured Node Names: Audio/Sink <name>``.
    """
    import re

    try:
        output = _run(["wpctl", "status"], runner, timeout)
    except PipeWireControlError:
        return None

    in_sinks = False
    for raw_line in output.splitlines():
        line = re.sub(r"^[│├└─\s]+", "", raw_line).strip()
        if line.lower().startswith("sinks:"):
            in_sinks = True
            continue
        if in_sinks:
            if line and not line.startswith("*") and not re.match(r"^\d+\.", line):
                in_sinks = False
                continue
            if line.startswith("*"):
                m = re.match(r"^\*\s*(?:(\d+)\.)?\s*([^\[\n\r]+)?", line)
                if m:
                    node_id = m.group(1)
                    desc_or_name = (m.group(2) or "").strip()
                    if desc_or_name:
                        return desc_or_name
                    if node_id:
                        return node_id

    # Fallback to Settings -> Default Configured Node Names: Audio/Sink <name>
    m = re.search(r"Audio/Sink\s+([^\s\n\r]+)", output)
    if m:
        return m.group(1).strip()

    # Fallback for plain lines without Sinks: header
    for raw_line in output.splitlines():
        line = re.sub(r"^[│├└─\s]+", "", raw_line).strip()
        if line.startswith("*"):
            m = re.match(r"^\*\s*(?:(\d+)\.)?\s*([^\[\n\r]+)?", line)
            if m:
                node_id = m.group(1)
                desc_or_name = (m.group(2) or "").strip()
                if desc_or_name:
                    return desc_or_name
                if node_id:
                    return node_id

    return None


_last_physical_sink_name: Optional[str] = None


def set_system_routing(
    enable: bool,
    filter_node_name: str = "eqspace.filter-chain",
    fallback_sink_name: Optional[str] = None,
    registry: Optional[PipeWireRegistry] = None,
    runner: Optional[Runner] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> bool:
    """Route system audio through EQ-Space (enable=True) or restore direct physical sink (enable=False)."""
    global _last_physical_sink_name
    registry = registry or PipeWireRegistry(runner=runner)
    try:
        snapshot = registry.snapshot()
    except PipeWireUnavailable as exc:
        raise PipeWireControlError(str(exc)) from exc

    if enable:
        found = any(sink.name == filter_node_name for sink in snapshot.sinks)
        if not found:
            raise PipeWireControlError(
                f"Filter chain sink '{filter_node_name}' is not loaded"
            )
        if runner is None or not hasattr(runner, "calls"):
            physical_names = {
                sink.name for sink in snapshot.sinks if not sink.name.startswith("eqspace.")
            }
            if fallback_sink_name and fallback_sink_name not in physical_names:
                raise PipeWireControlError(
                    f"Listening device '{fallback_sink_name}' is unavailable; direct output was kept"
                )
            deadline = time.monotonic() + 2.0
            while True:
                connected = connected_filter_output(
                    filter_node_name, physical_names, runner=runner, timeout=1.0
                )
                if connected and (not fallback_sink_name or connected == fallback_sink_name):
                    break
                if time.monotonic() >= deadline:
                    raise PipeWireControlError(
                        "EQ output is not connected to the selected listening device; direct output was kept"
                    )
                time.sleep(0.1)
        # Remember current physical sink before switching to EQ-Space
        current = get_default_sink_name(runner=runner, timeout=timeout)
        current_is_filter = bool(current) and (
            current == filter_node_name
            or current.startswith("filter-chain-")
            or any(
                sink.name == filter_node_name
                and (sink.description == current or str(sink.id) == current)
                for sink in snapshot.sinks
            )
        )
        if current and not current_is_filter:
            for s in snapshot.sinks:
                if str(s.id) == str(current) or s.description == current or s.name == current:
                    _last_physical_sink_name = s.name
                    break
            else:
                _last_physical_sink_name = current
        set_default_sink(filter_node_name, registry=registry, runner=runner, timeout=timeout)
        # Relink active playback streams to the filter sink immediately
        if runner is None or not hasattr(runner, "calls"):
            for stream in snapshot.streams:
                if stream.name and (
                    stream.name.startswith("eqspace.")
                    or stream.name.endswith(".playback")
                    or "monitor" in stream.name.lower()
                ):
                    continue
                try:
                    move_stream(stream.id, filter_node_name, runner=runner, timeout=timeout, registry=registry)
                except Exception:
                    pass
        return True
    else:
        # Determine best physical fallback sink
        fallback = None
        target_name = fallback_sink_name or _last_physical_sink_name
        if target_name:
            fallback = next(
                (s for s in snapshot.sinks if s.name == target_name and s.name != filter_node_name),
                None,
            )
        if fallback is None:
            # Prefer active Bluetooth headset over built-in speakers
            fallback = next(
                (s for s in snapshot.sinks if s.name != filter_node_name and "bluez" in s.name.lower()),
                None,
            )
        if fallback is None:
            # Fallback to any non-filter sink
            fallback = next((s for s in snapshot.sinks if s.name != filter_node_name), None)

        if fallback is not None:
            live_links = runner is None or not hasattr(runner, "calls")
            inspect_eq_links = live_links and bool(snapshot.streams) and any(
                sink.name == filter_node_name for sink in snapshot.sinks
            )
            formerly_eq_ports: set[str] = set()
            if inspect_eq_links:
                before_links = get_active_output_links(runner=runner, timeout=timeout)
                for stream in snapshot.streams:
                    formerly_eq_ports.update(
                        port for port, destinations in before_links.items()
                        if port.startswith(f"{stream.name}:output_")
                        and any(destination.startswith(f"{filter_node_name}:")
                                for destination in destinations)
                    )
            set_default_sink(fallback.name, registry=registry, runner=runner, timeout=timeout)
            if live_links:
                for stream in snapshot.streams:
                    if stream.name and (
                        stream.name.startswith("eqspace.")
                        or stream.name.endswith(".playback")
                        or "monitor" in stream.name.lower()
                    ):
                        continue
                    try:
                        move_stream(stream.id, fallback.name, runner=runner, timeout=timeout, registry=registry)
                    except Exception:
                        pass
                if formerly_eq_ports:
                    after_links = get_active_output_links(runner=runner, timeout=timeout)
                    active_names = {stream.name for stream in registry.snapshot().streams}
                    for port in formerly_eq_ports:
                        if port.rsplit(":", 1)[0] not in active_names:
                            continue
                        destinations = after_links.get(port, [])
                        if not destinations or any(
                            not destination.startswith(f"{fallback.name}:")
                            for destination in destinations
                        ):
                            raise PipeWireControlError(
                                f"playback stream {port} is still linked to EQ or disconnected; previous chain was kept"
                            )
        return False


def is_system_routed(
    filter_node_name: str = "eqspace.filter-chain",
    runner: Optional[Runner] = None,
    registry: Optional[PipeWireRegistry] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> bool:
    """Return True if the current default sink is *filter_node_name*."""
    try:
        sink_name = get_default_sink_name(runner=runner, timeout=timeout)
    except Exception:
        return False
    if not sink_name:
        return False
    if sink_name == filter_node_name:
        return True
    if sink_name.lower() in ("eqspace.filter-chain", "eq-space filter chain"):
        return True
    if registry is not None:
        try:
            snapshot = registry.snapshot()
            for sink in snapshot.sinks:
                if sink.name == filter_node_name:
                    if (
                        str(sink.id) == str(sink_name)
                        or sink.description == sink_name
                        or sink.name == sink_name
                    ):
                        return True
        except Exception:
            pass
    return False
