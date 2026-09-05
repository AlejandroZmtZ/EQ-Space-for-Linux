"""Microphone noise-reduction filter-chain rendering (input side).

Renders an SPA-style ``context.modules`` config for
``libpipewire-module-filter-chain`` that runs the DeepFilterNet LADSPA
plugin (``libdf_ladspa.so``) on a capture stream and exposes the result
as a virtual source named "EQ-Space Mic".

Only config rendering and plugin detection live here; loading/unloading
is handled by
:class:`~eqspace.core.filterchain.manager.FilterChainManager`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

LADSPA_PLUGIN_FILE = "libdf_ladspa.so"
LADSPA_PLUGIN_LABEL = "ladspa"

DEFAULT_NODE_NAME = "eqspace.mic"
DEFAULT_SOURCE_NAME = "EQ-Space Mic"
DEFAULT_DESCRIPTION = "EQ-Space Mic (DeepFilterNet)"

#: Standard LADSPA plugin search directories, in priority order.
LADSPA_SEARCH_PATHS = (
    "/usr/lib/ladspa",
    "/usr/local/lib/ladspa",
    os.path.expanduser("~/.ladspa"),
    "/usr/lib/x86_64-linux-gnu/ladspa",
)


def find_deepfilternet(search_paths: Sequence[str] = LADSPA_SEARCH_PATHS) -> Optional[Path]:
    """Return the path to ``libdf_ladspa.so`` if found in the search paths."""
    for directory in search_paths:
        candidate = Path(directory) / LADSPA_PLUGIN_FILE
        if candidate.is_file():
            return candidate
    return None


def deepfilternet_available(search_paths: Sequence[str] = LADSPA_SEARCH_PATHS) -> bool:
    """True when the DeepFilterNet LADSPA plugin is installed."""
    return find_deepfilternet(search_paths) is not None


def _spa_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _spa_number(value: float) -> str:
    return repr(float(value))


class MicChainRenderer:
    """Render an input-side filter-chain with DeepFilterNet noise reduction."""

    def __init__(
        self,
        plugin_path: Optional[Path] = None,
        node_name: str = DEFAULT_NODE_NAME,
        source_name: str = DEFAULT_SOURCE_NAME,
        description: str = DEFAULT_DESCRIPTION,
    ) -> None:
        self.node_name = node_name
        self.source_name = source_name
        self.description = description
        if plugin_path is None:
            plugin_path = find_deepfilternet()
        if plugin_path is None:
            raise FileNotFoundError(
                f"DeepFilterNet LADSPA plugin ({LADSPA_PLUGIN_FILE}) not found in "
                f"any of: {', '.join(LADSPA_SEARCH_PATHS)}"
            )
        self.plugin_path = Path(plugin_path)

    def render_config(
        self,
        attenuation_db: float = -15.0,
        strength: float = 1.0,
        channels: Sequence[str] = ("MONO",),
    ) -> str:
        """Render the mic NR filter-chain config string.

        ``attenuation_db`` is the noise-attenuation limit (negative dB);
        ``strength`` is the wet/dry mix in [0, 1].
        """
        if attenuation_db > 0.0:
            raise ValueError("attenuation_db must be <= 0 dB")
        if not 0.0 <= strength <= 1.0:
            raise ValueError("strength must be in [0, 1]")

        positions = " ".join(channels)
        return (
            "context.modules = [\n"
            "  { name = libpipewire-module-filter-chain\n"
            "    args = {\n"
            f"      node.description = {_spa_quote(self.description)}\n"
            f"      media.name = {_spa_quote(self.description)}\n"
            "      filter.graph = {\n"
            "        nodes = [\n"
            "                {\n"
            f"                    type  = {LADSPA_PLUGIN_LABEL}\n"
            f"                    name  = {_spa_quote('df_noise_reduction')}\n"
            f"                    plugin = {_spa_quote(str(self.plugin_path))}\n"
            "                    label = deep_filter_ladspa\n"
            "                    control = {\n"
            f"                        \"Attenuation Limit (dB)\" = {_spa_number(attenuation_db)}\n"
            f"                        \"Strength\" = {_spa_number(strength)}\n"
            "                    }\n"
            "                }\n"
            "        ]\n"
            "      }\n"
            f"      audio.position = [ {positions} ]\n"
            "      capture.props = {\n"
            f"        node.name = {_spa_quote(self.node_name + '.capture')}\n"
            "        node.passive = true\n"
            f"        audio.position = [ {positions} ]\n"
            "      }\n"
            "      playback.props = {\n"
            f"        node.name = {_spa_quote(self.node_name)}\n"
            f"        node.description = {_spa_quote(self.source_name)}\n"
            '        media.class = "Audio/Source/Virtual"\n'
            f"        audio.position = [ {positions} ]\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "]\n"
        )


def render_mic_config(
    attenuation_db: float = -15.0,
    strength: float = 1.0,
    plugin_path: Optional[Path] = None,
    node_name: str = DEFAULT_NODE_NAME,
    source_name: str = DEFAULT_SOURCE_NAME,
    channels: Sequence[str] = ("MONO",),
) -> str:
    """Convenience wrapper around :class:`MicChainRenderer`."""
    renderer = MicChainRenderer(
        plugin_path=plugin_path, node_name=node_name, source_name=source_name
    )
    return renderer.render_config(
        attenuation_db=attenuation_db, strength=strength, channels=channels
    )
