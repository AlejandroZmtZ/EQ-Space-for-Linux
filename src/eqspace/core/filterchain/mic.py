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
LADSPA_NODE_TYPE = "ladspa"
DEEPFILTERNET_LABEL = "deep_filter_ladspa"

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

    def _validate(self, attenuation_db: float, strength: float) -> None:
        if attenuation_db > 0.0:
            raise ValueError("attenuation_db must be <= 0 dB")
        if not 0.0 <= strength <= 1.0:
            raise ValueError("strength must be in [0, 1]")

    def _df_node(
        self, attenuation_db: float, strength: float, indent: str
    ) -> str:
        pad = indent + "    "
        return (
            f"{indent}{{\n"
            f"{pad}type  = {LADSPA_NODE_TYPE}\n"
            f'{pad}name  = {_spa_quote("df_noise_reduction")}\n'
            f"{pad}plugin = {_spa_quote(str(self.plugin_path))}\n"
            f"{pad}label = {DEEPFILTERNET_LABEL}\n"
            f"{pad}control = {{\n"
            f'{pad}    "Attenuation Limit (dB)" = {_spa_number(attenuation_db)}\n'
            f'{pad}    "Strength" = {_spa_number(strength)}\n'
            f"{pad}}}\n"
            f"{indent}}}"
        )

    def _df_node_inline(self, attenuation_db: float, strength: float) -> str:
        return (
            "{ "
            f"type = {LADSPA_NODE_TYPE} "
            'name = "df_noise_reduction" '
            f"plugin = {_spa_quote(str(self.plugin_path))} "
            f"label = {DEEPFILTERNET_LABEL} "
            "control = { "
            f'\"Attenuation Limit (dB)\" = {_spa_number(attenuation_db)} '
            f'"Strength" = {_spa_number(strength)} '
            "} }"
        )

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
        self._validate(attenuation_db, strength)

        positions = " ".join(channels)
        return (
            "context.modules = [\n"
            "  { name = libpipewire-module-filter-chain\n"
            "    args = {\n"
            f"      node.description = {_spa_quote(self.description)}\n"
            f"      media.name = {_spa_quote(self.description)}\n"
            "      filter.graph = {\n"
            "        nodes = [\n"
            f"{self._df_node(attenuation_db, strength, '                ')}\n"
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

    def render_args(
        self,
        attenuation_db: float = -15.0,
        strength: float = 1.0,
        channels: Sequence[str] = ("MONO",),
    ) -> str:
        """Render the module arguments as a single-line SPA properties string.

        Equivalent to :meth:`render_config` but in the single-line form that
        ``pw-cli load-module`` expects (see
        :class:`~eqspace.core.filterchain.manager.FilterChainManager`).
        """
        self._validate(attenuation_db, strength)
        positions = " ".join(channels)
        node = self._df_node_inline(attenuation_db, strength)
        return (
            f"node.description = {_spa_quote(self.description)} "
            f"media.name = {_spa_quote(self.description)} "
            f"filter.graph = {{ nodes = [ {node} ] }} "
            f"audio.position = [ {positions} ] "
            "capture.props = { "
            f"node.name = {_spa_quote(self.node_name + '.capture')} "
            "node.passive = true "
            f"audio.position = [ {positions} ] }} "
            "playback.props = { "
            f"node.name = {_spa_quote(self.node_name)} "
            f"node.description = {_spa_quote(self.source_name)} "
            'media.class = "Audio/Source/Virtual" '
            f"audio.position = [ {positions} ] }}"
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


def render_mic_args(
    attenuation_db: float = -15.0,
    strength: float = 1.0,
    plugin_path: Optional[Path] = None,
    node_name: str = DEFAULT_NODE_NAME,
    source_name: str = DEFAULT_SOURCE_NAME,
    channels: Sequence[str] = ("MONO",),
) -> str:
    """Single-line module-args counterpart of :func:`render_mic_config`."""
    renderer = MicChainRenderer(
        plugin_path=plugin_path, node_name=node_name, source_name=source_name
    )
    return renderer.render_args(
        attenuation_db=attenuation_db, strength=strength, channels=channels
    )
