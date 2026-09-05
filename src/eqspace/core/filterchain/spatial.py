"""Spatial (HRTF) filter-chain rendering.

Renders an SPA-style ``context.modules`` config for
``libpipewire-module-filter-chain`` that builds a virtual surround
stage: the stereo input is mixed down and convolved with each virtual
speaker's left/right HRTF pair using the builtin ``convolver`` filter,
and all ears sum back onto the stereo output.

Only config rendering lives here; loading/unloading is handled by
:class:`~eqspace.core.filterchain.manager.FilterChainManager`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

DEFAULT_NODE_NAME = "eqspace.spatial"
DEFAULT_DESCRIPTION = "EQ-Space Spatial"


@dataclass(frozen=True)
class SpeakerIR:
    """Left/right IR file paths for one virtual speaker position."""

    azimuth: float
    left_ir: Path
    right_ir: Path


def _spa_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _convolver_node(name: str, filename: Path, inputs: Sequence[str]) -> str:
    input_list = " ".join(_spa_quote(port) for port in inputs)
    return (
        "                {\n"
        "                    type  = builtin\n"
        f"                    name  = {_spa_quote(name)}\n"
        "                    label = convolver\n"
        f"                    control = {{ \"filename\" = {_spa_quote(str(filename))} }}\n"
        f"                    input  = [ {input_list} ]\n"
        "                }"
    )


def _azimuth_tag(azimuth: float) -> str:
    return f"az{azimuth:+g}".replace("-", "m").replace("+", "p").replace(".", "_")


class SpatialChainRenderer:
    """Render a convolver-based spatial filter-chain config."""

    def __init__(
        self,
        node_name: str = DEFAULT_NODE_NAME,
        description: str = DEFAULT_DESCRIPTION,
    ) -> None:
        self.node_name = node_name
        self.description = description

    def render_config(
        self,
        speakers: Sequence[SpeakerIR],
        gain: float = 1.0,
        channels: Sequence[str] = ("FL", "FR"),
    ) -> str:
        """Render the spatial filter-chain config string.

        Each speaker contributes two convolver nodes (one per ear), fed
        by a mono sum of the stereo input scaled by ``gain / N``.
        """
        if not speakers:
            raise ValueError("at least one speaker IR pair is required")
        if gain <= 0.0:
            raise ValueError("gain must be positive")

        per_speaker_gain = gain / len(speakers)
        nodes = []
        mixers = []
        for spk in speakers:
            tag = _azimuth_tag(spk.azimuth)
            for ear, ir_path in (("L", spk.left_ir), ("R", spk.right_ir)):
                node_name = f"conv_{tag}_{ear}"
                nodes.append(
                    _convolver_node(
                        node_name,
                        ir_path,
                        (f"{self.node_name}:playback_{c}" for c in channels),
                    )
                )
                mixers.append(f"{self.node_name}.{node_name}:Out")

        # Sum all convolver outputs into the stereo out via builtin mixers.
        left_inputs = " ".join(
            _spa_quote(m) for i, m in enumerate(mixers) if i % 2 == 0
        )
        right_inputs = " ".join(
            _spa_quote(m) for i, m in enumerate(mixers) if i % 2 == 1
        )
        for ear, inputs in (("L", left_inputs), ("R", right_inputs)):
            nodes.append(
                "                {\n"
                "                    type  = builtin\n"
                f"                    name  = {_spa_quote(f'mix_{ear}')}\n"
                "                    label = mixer\n"
                f"                    control = {{ \"Gain 1\" = {per_speaker_gain!r} }}\n"
                f"                    input  = [ {inputs} ]\n"
                f"                    output = [ {_spa_quote(f'{self.node_name}:capture_{ear}')} ]\n"
                "                }"
            )

        positions = " ".join(channels)
        return (
            "context.modules = [\n"
            "  { name = libpipewire-module-filter-chain\n"
            "    args = {\n"
            f"      node.description = {_spa_quote(self.description)}\n"
            f"      media.name = {_spa_quote(self.description)}\n"
            "      filter.graph = {\n"
            "        nodes = [\n"
            f"{chr(10).join(nodes)}\n"
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


def render_from_ir_paths(
    ir_paths: Mapping[float, Tuple[Path, Path]],
    gain: float = 1.0,
    node_name: str = DEFAULT_NODE_NAME,
    description: str = DEFAULT_DESCRIPTION,
    channels: Sequence[str] = ("FL", "FR"),
) -> str:
    """Convenience wrapper: render from the ``extract_speaker_irs`` mapping."""
    speakers = [
        SpeakerIR(azimuth=az, left_ir=paths[0], right_ir=paths[1])
        for az, paths in sorted(ir_paths.items())
    ]
    return SpatialChainRenderer(node_name=node_name, description=description).render_config(
        speakers, gain=gain, channels=channels
    )
