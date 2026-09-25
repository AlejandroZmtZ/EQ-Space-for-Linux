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

from ..dsp.hrtf import _azimuth_tag

DEFAULT_NODE_NAME = "eqspace.spatial"
DEFAULT_DESCRIPTION = "EQ-Space Spatial"

#: Layout channel -> speaker azimuth mappings.
LAYOUT_CHANNEL_SPEAKER_MAP: dict[str, tuple[tuple[str, float], ...]] = {
    "Stereo": (("FL", -30.0), ("FR", 30.0)),
    "5.1": (
        ("FL", -30.0),
        ("FR", 30.0),
        ("FC", 0.0),
        ("LFE", 0.0),
        ("SL", -110.0),
        ("SR", 110.0),
    ),
    "7.1": (
        ("FL", -30.0),
        ("FR", 30.0),
        ("FC", 0.0),
        ("LFE", 0.0),
        ("SL", -90.0),
        ("SR", 90.0),
        ("RL", -110.0),
        ("RR", 110.0),
    ),
}

#: Virtual layouts -> node audio positions.
LAYOUT_CHANNELS: dict[str, tuple[str, ...]] = {
    layout: tuple(ch for ch, _ in mapping)
    for layout, mapping in LAYOUT_CHANNEL_SPEAKER_MAP.items()
}

#: Virtual layouts -> virtual-speaker azimuths (degrees).
LAYOUT_AZIMUTHS: dict[str, tuple[float, ...]] = {
    layout: tuple(sorted(set(az for _, az in mapping)))
    for layout, mapping in LAYOUT_CHANNEL_SPEAKER_MAP.items()
}

#: Crossfeed uses a near-field ±30° pair (bs2b-style approximation).
CROSSFEED_AZIMUTHS = (-30.0, 30.0)


@dataclass(frozen=True)
class SpeakerIR:
    """Left/right IR file paths for one virtual speaker position."""

    azimuth: float
    left_ir: Path
    right_ir: Path
    channel: Optional[str] = None


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
        strictly by its respective channel input port.
        """
        if not speakers:
            raise ValueError("at least one speaker IR pair is required")
        if gain <= 0.0:
            raise ValueError("gain must be positive")

        per_speaker_gain = gain / len(speakers)
        nodes = []
        mixers = []
        for i, spk in enumerate(speakers):
            channel = spk.channel or (channels[i] if i < len(channels) else f"ch{i}")
            for ear, ir_path in (("L", spk.left_ir), ("R", spk.right_ir)):
                node_name = f"conv_{channel}_{ear}"
                inputs = [f"{self.node_name}:playback_{channel}"]
                nodes.append(_convolver_node(node_name, ir_path, inputs))
                mixers.append(f"{self.node_name}.{node_name}:Out")

        # Sum all convolver outputs into the stereo out via builtin mixers.
        left_inputs = [m for i, m in enumerate(mixers) if i % 2 == 0]
        right_inputs = [m for i, m in enumerate(mixers) if i % 2 == 1]
        for ear, inputs in (("L", left_inputs), ("R", right_inputs)):
            nodes.append(
                "                {\n"
                "                    type  = builtin\n"
                f"                    name  = {_spa_quote(f'mix_{ear}')}\n"
                "                    label = mixer\n"
                f"                    control = {{ \"Gain 1\" = {per_speaker_gain!r} }}\n"
                f"                    input  = [ {' '.join(_spa_quote(p) for p in inputs)} ]\n"
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
            "        audio.channels = 2\n"
            f"        audio.position = [ FL FR ]\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "]\n"
        )

    def render_args(
        self,
        speakers: Sequence[SpeakerIR],
        gain: float = 1.0,
        channels: Sequence[str] = ("FL", "FR"),
    ) -> str:
        """Render the module arguments as a single-line SPA properties string.

        Equivalent to :meth:`render_config` but in the single-line form that
        ``pw-cli load-module`` expects (see
        :class:`~eqspace.core.filterchain.manager.FilterChainManager`).
        """
        if not speakers:
            raise ValueError("at least one speaker IR pair is required")
        if gain <= 0.0:
            raise ValueError("gain must be positive")

        nodes = []
        links = []
        inputs = []

        for idx, spk in enumerate(speakers):
            channel = spk.channel or (channels[idx] if idx < len(channels) else f"ch{idx}")
            conv_l = f"conv_{channel}_L"
            conv_r = f"conv_{channel}_R"
            copy_n = f"copy_{channel}"
            nodes.append(f"{{ type = builtin label = copy name = {_spa_quote(copy_n)} }}")
            nodes.append(
                f"{{ type = builtin label = convolver name = {_spa_quote(conv_l)} "
                f"config = {{ filename = {_spa_quote(str(spk.left_ir))} channel = 0 }} }}"
            )
            nodes.append(
                f"{{ type = builtin label = convolver name = {_spa_quote(conv_r)} "
                f"config = {{ filename = {_spa_quote(str(spk.right_ir))} channel = 0 }} }}"
            )
            links.append(f"{{ output = {_spa_quote(f'{copy_n}:Out')} input = {_spa_quote(f'{conv_l}:In')} }}")
            links.append(f"{{ output = {_spa_quote(f'{copy_n}:Out')} input = {_spa_quote(f'{conv_r}:In')} }}")
            links.append(f"{{ output = {_spa_quote(f'{conv_l}:Out')} input = {_spa_quote(f'mix_l:In {idx+1}')} }}")
            links.append(f"{{ output = {_spa_quote(f'{conv_r}:Out')} input = {_spa_quote(f'mix_r:In {idx+1}')} }}")
            inputs.append(_spa_quote(f"{copy_n}:In"))

        nodes.append("{ type = builtin label = mixer name = \"mix_l\" }")
        nodes.append("{ type = builtin label = mixer name = \"mix_r\" }")

        nodes_str = " ".join(nodes)
        links_str = " ".join(links)
        inputs_str = " ".join(inputs)
        outputs_str = '"mix_l:Out" "mix_r:Out"'
        positions_str = " ".join(channels)

        return (
            f"node.description = {_spa_quote(self.description)} "
            f"media.name = {_spa_quote(self.description)} "
            f"filter.graph = {{ nodes = [ {nodes_str} ] links = [ {links_str} ] inputs = [ {inputs_str} ] outputs = [ {outputs_str} ] }} "
            f"capture.props = {{ node.name = {_spa_quote(self.node_name)} media.class = \"Audio/Sink\" audio.channels = {len(channels)} audio.position = [ {positions_str} ] }} "
            f"playback.props = {{ node.name = {_spa_quote(self.node_name + '.playback')} node.passive = true audio.channels = 2 audio.position = [ FL FR ] }}"
        )


def render_from_ir_paths(
    ir_paths: Mapping[float, Tuple[Path, Path]],
    gain: float = 1.0,
    node_name: str = DEFAULT_NODE_NAME,
    description: str = DEFAULT_DESCRIPTION,
    channels: Sequence[str] = ("FL", "FR"),
    layout: Optional[str] = None,
) -> str:
    """Convenience wrapper: render from the ``extract_speaker_irs`` mapping."""
    if layout is None:
        for l_name, chs in LAYOUT_CHANNELS.items():
            if tuple(chs) == tuple(channels):
                layout = l_name
                break
        if layout is None:
            layout = "Stereo"
    mapping = LAYOUT_CHANNEL_SPEAKER_MAP.get(layout)
    if mapping:
        speakers = [
            SpeakerIR(
                azimuth=az,
                left_ir=ir_paths[az][0],
                right_ir=ir_paths[az][1],
                channel=ch,
            )
            for ch, az in mapping
            if az in ir_paths
        ]
    else:
        speakers = [
            SpeakerIR(
                azimuth=az,
                left_ir=paths[0],
                right_ir=paths[1],
                channel=channels[i] if i < len(channels) else f"ch{i}",
            )
            for i, (az, paths) in enumerate(sorted(ir_paths.items()))
        ]
    return SpatialChainRenderer(node_name=node_name, description=description).render_config(
        speakers, gain=gain, channels=channels
    )
