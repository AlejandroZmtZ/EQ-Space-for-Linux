"""Detect an installed LSP Limiter Stereo LV2 descriptor before offering it."""

from __future__ import annotations

import os
import re
import sysconfig
from dataclasses import dataclass
from pathlib import Path

PLUGIN_URI = "http://lsp-plug.in/plugins/lv2/limiter_stereo"


@dataclass(frozen=True)
class LimiterCapability:
    path: Path
    true_peak_value: int
    inputs: tuple[str, str]
    outputs: tuple[str, str]
    latency_symbol: str


def _port_blocks(ttl: str) -> dict[str, str]:
    blocks = {}
    for match in re.finditer(r'\[\s*a\s+lv2:(?:InputPort|OutputPort)', ttl, re.S):
        depth = 0
        end = match.start()
        for end in range(match.start(), len(ttl)):
            if ttl[end] == "[":
                depth += 1
            elif ttl[end] == "]":
                depth -= 1
                if depth == 0:
                    break
        block = ttl[match.start():end + 1]
        symbol = re.search(r'lv2:symbol\s+"([^"]+)"', block)
        if symbol:
            blocks[symbol.group(1)] = block
    return blocks


def _search_paths(search_paths: tuple[Path, ...] | None) -> tuple[Path, ...]:
    if search_paths is None:
        multiarch = sysconfig.get_config_var("MULTIARCH")
        defaults = ["/usr/lib/lv2", "/usr/local/lib/lv2", "/usr/lib64/lv2",
                    str(Path.home() / ".lv2")]
        if multiarch:
            defaults.append(f"/usr/lib/{multiarch}/lv2")
        roots = os.environ.get("LV2_PATH", ":".join(defaults))
        search_paths = tuple(Path(root) for root in roots.split(":"))
    return search_paths


def detect_limiter_with_reason(
    search_paths: tuple[Path, ...] | None = None,
) -> tuple[LimiterCapability | None, str | None]:
    """Return the usable limiter capability or an actionable reason it is unavailable."""
    found_descriptor = False
    saw_stereo_ports = False
    saw_latency_port = False
    for root in _search_paths(search_paths):
        if not root.is_dir():
            continue
        for path in root.glob("**/limiter_stereo.ttl"):
            found_descriptor = True
            ttl = path.read_text(encoding="utf-8", errors="replace")
            ports = _port_blocks(ttl)
            required = ("ovs", "th", "boost", "alr", "g_in", "g_out", "lk")
            if any(symbol not in ports for symbol in required):
                continue
            peak_point = next((point for point in re.findall(r'\[[^\[\]]*\]', ports["ovs"])
                               if 'rdfs:label "True Peak' in point), None)
            peak = re.search(r'rdf:value\s+(\d+)', peak_point or "")
            if peak is None:
                continue
            audio_in = tuple(symbol for symbol, block in ports.items()
                             if "lv2:InputPort" in block and "lv2:AudioPort" in block)
            audio_out = tuple(symbol for symbol, block in ports.items()
                              if "lv2:OutputPort" in block and "lv2:AudioPort" in block)
            latency = next((symbol for symbol, block in ports.items()
                            if re.search(r"\b(?:[A-Za-z][\w-]*:)?reportsLatency\b", block)), None)
            saw_latency_port |= latency is not None
            if len(audio_in) != 2 or len(audio_out) != 2:
                continue
            saw_stereo_ports = True
            if latency is None:
                continue
            def ordered(pair: tuple[str, str]) -> tuple[str, str] | None:
                left = next((s for s in pair if re.search(r'(?:_|-)(?:l|left)$', s)), None)
                right = next((s for s in pair if re.search(r'(?:_|-)(?:r|right)$', s)), None)
                return (left, right) if left and right else None
            inputs = ordered(audio_in)
            outputs = ordered(audio_out)
            if inputs is None or outputs is None:
                continue
            return LimiterCapability(path, int(peak.group(1)), inputs, outputs, latency), None
    if not found_descriptor:
        return None, "Install LSP Limiter Stereo LV2 1.2.24+ (Ubuntu package: lsp-plugins-lv2), then restart EQ-Space."
    if saw_stereo_ports and not saw_latency_port:
        return None, "The installed LSP LV2 descriptor does not report latency; install a compatible LSP build."
    return None, "The installed LSP LV2 build lacks the required true-peak mode or compatible stereo ports. Update to LSP 1.2.24+, then restart EQ-Space."


def detect_limiter(search_paths: tuple[Path, ...] | None = None) -> LimiterCapability | None:
    """Detect an installed true-peak LSP Limiter Stereo LV2 descriptor."""
    return detect_limiter_with_reason(search_paths)[0]


def render_limiter_args(capability: LimiterCapability, node_name: str) -> str:
    ceiling = 10.0 ** (-1.0 / 20.0)
    left_in, right_in = capability.inputs
    left_out, right_out = capability.outputs
    return (
        f'node.description = "EQ-Space Limiter" media.name = "EQ-Space Limiter" '
        'filter.graph = { nodes = [ { type = lv2 name = "limiter" '
        f'plugin = "{PLUGIN_URI}" control = {{ "ovs" = {capability.true_peak_value} '
        f'"th" = {ceiling:.9f} "boost" = 0 "alr" = 0 "lk" = 5 '
        '"g_in" = 1 "g_out" = 1 } } ] '
        f'inputs = [ "limiter:{left_in}" "limiter:{right_in}" ] '
        f'outputs = [ "limiter:{left_out}" "limiter:{right_out}" ] }} '
        f'capture.props = {{ node.name = "{node_name}" media.class = "Audio/Sink" '
        'audio.channels = 2 audio.position = [ FL FR ] } '
        f'playback.props = {{ node.name = "{node_name}.playback" node.passive = true '
        'audio.channels = 2 audio.position = [ FL FR ] }'
    )


def lv2_host_directory() -> Path | None:
    """Find a matching host; a user-local helper never replaces system services."""
    import subprocess
    try:
        result = subprocess.run(['pw-cli', '--version'], capture_output=True,
                                text=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    versions = re.findall(r'(\d+\.\d+\.\d+)', result.stdout)
    if not versions:
        return None
    version = versions[-1]
    multiarch = sysconfig.get_config_var('MULTIARCH')
    roots = [Path(os.environ['PIPEWIRE_MODULE_DIR'])] if os.environ.get('PIPEWIRE_MODULE_DIR') else []
    roots += [Path(f'/usr/lib/{multiarch}/pipewire-0.3'),
              Path('/usr/lib/pipewire-0.3'), Path('/usr/lib64/pipewire-0.3'),
              Path.home() / '.local/lib/eqspace' / f'pipewire-{version}']
    return next((root for root in roots
                 if (root / 'libpipewire-module-filter-chain-lv2.so').is_file()
                 and (root / 'libpipewire-module-filter-chain.so').is_file()), None)
