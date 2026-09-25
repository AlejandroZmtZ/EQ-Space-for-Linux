"""Bauer & Meier binaural crossfeed DSP filter design for headphones.

Reduces extreme stereo separation fatigue by cross-feeding low-passed, slightly
delayed signals between the left and right channels to emulate loudspeaker listening.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .biquads import BiquadCoeffs, high_shelf, low_pass


@dataclass(frozen=True)
class CrossfeedConfig:
    """Parameters for binaural crossfeed."""

    f0_hz: float = 700.0
    feed_db: float = -4.5
    delay_us: float = 300.0


CROSSFEED_PRESETS: Mapping[str, CrossfeedConfig] = {
    "bauer": CrossfeedConfig(f0_hz=700.0, feed_db=-6.0, delay_us=250.0),
    "meier": CrossfeedConfig(f0_hz=650.0, feed_db=-4.5, delay_us=300.0),
    "strong": CrossfeedConfig(f0_hz=700.0, feed_db=-3.0, delay_us=350.0),
}


def crossfeed_delay_samples(delay_us: float, fs: float = 48000.0) -> int:
    """Convert interaural time delay in microseconds to integer samples."""
    return round(delay_us * 1e-6 * fs)


def bauer_crossfeed_coefficients(
    f0_hz: float = 700.0, feed_db: float = -4.5, fs: float = 48000.0
) -> tuple[BiquadCoeffs, BiquadCoeffs]:
    """Calculate normalized biquad coefficients for direct and crossfeed paths.

    Returns:
        (direct_coeffs, cross_coeffs)
    """
    # Direct path compensation: subtle high shelf to balance the summed low-end
    direct = high_shelf(f0_hz=f0_hz, gain_db=abs(feed_db) * 0.25, q_or_s=0.5, fs=fs)

    # Crossfeed path: low-pass filter attenuated by feed_db
    lp = low_pass(f0_hz=f0_hz, q=0.5, fs=fs)
    gain = 10.0 ** (feed_db / 20.0)
    cross = BiquadCoeffs(
        b0=lp.b0 * gain,
        b1=lp.b1 * gain,
        b2=lp.b2 * gain,
        a0=1.0,
        a1=lp.a1,
        a2=lp.a2,
    )
    return direct, cross


def _spa_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_crossfeed_chain_args(
    preset: str = "bauer",
    fs: float = 48000.0,
    node_name: str = "eqspace.crossfeed",
    description: str = "EQ-Space Crossfeed",
) -> str:
    """Render a single-line SPA argument string for PipeWire filter-chain."""
    cfg = CROSSFEED_PRESETS.get(preset.lower(), CROSSFEED_PRESETS["bauer"])
    f0 = cfg.f0_hz
    gain_db = cfg.feed_db

    # Node definitions using PipeWire builtin copy, bq_lowpass, and mixer nodes
    nodes = (
        '{ type = builtin label = copy name = "copy_l" } '
        '{ type = builtin label = copy name = "copy_r" } '
        f'{{ type = builtin label = bq_lowpass name = "lp_l2r" '
        f'control = {{ "Freq" = {f0:g} "Q" = 0.5 "Gain" = {gain_db:g} }} }} '
        f'{{ type = builtin label = bq_lowpass name = "lp_r2l" '
        f'control = {{ "Freq" = {f0:g} "Q" = 0.5 "Gain" = {gain_db:g} }} }} '
        '{ type = builtin label = mixer name = "mix_l" } '
        '{ type = builtin label = mixer name = "mix_r" }'
    )

    links = (
        '{ output = "copy_l:Out" input = "mix_l:In 1" } '
        '{ output = "copy_l:Out" input = "lp_l2r:In" } '
        '{ output = "copy_r:Out" input = "mix_r:In 1" } '
        '{ output = "copy_r:Out" input = "lp_r2l:In" } '
        '{ output = "lp_r2l:Out" input = "mix_l:In 2" } '
        '{ output = "lp_l2r:Out" input = "mix_r:In 2" }'
    )

    inputs = f'{_spa_quote("copy_l:In")} {_spa_quote("copy_r:In")}'
    outputs = f'{_spa_quote("mix_l:Out")} {_spa_quote("mix_r:Out")}'

    return (
        f"node.description = {_spa_quote(description)} "
        f"media.name = {_spa_quote(description)} "
        f"filter.graph = {{ nodes = [ {nodes} ] links = [ {links} ] "
        f"inputs = [ {inputs} ] outputs = [ {outputs} ] }} "
        "audio.channels = 2 "
        "audio.position = [ FL FR ] "
        f"capture.props = {{ node.name = {_spa_quote(node_name)} media.class = Audio/Sink audio.channels = 2 audio.position = [ FL FR ] }} "
        f"playback.props = {{ node.name = {_spa_quote(node_name + '.playback')} node.passive = true audio.channels = 2 audio.position = [ FL FR ] }}"
    )
