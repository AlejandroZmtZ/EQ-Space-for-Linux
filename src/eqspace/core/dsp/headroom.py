"""Static peak estimates used to choose a conservative EQ output trim."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from scipy.io import wavfile
from scipy.signal import chirp, fftconvolve

from eqspace.core.dsp.biquads import magnitude_response
from eqspace.core.dsp.crossfeed import CROSSFEED_PRESETS
from eqspace.core.dsp.biquads import low_pass
from eqspace.core.dsp.filter_design import EQBand, design_filters
from eqspace.core.filterchain.spatial import LAYOUT_CHANNEL_SPEAKER_MAP


def eq_peak_db(bands: Sequence[EQBand], fs: float) -> float:
    freqs = np.geomspace(20.0, min(20000.0, fs * 0.499), 4096)
    response = magnitude_response(design_filters(list(bands), fs), freqs, fs)
    return float(np.max(response)) if len(response) else 0.0


def automatic_trim_db(bands: Sequence[EQBand], fs: float, manual_db: float = 0.0,
                      spatial_db: float = 0.0, *, limiter_enabled: bool = False) -> float:
    """Reserve effect headroom; allow manual drive only with a downstream limiter.

    Negative manual gain always reduces output. Positive manual gain without
    the limiter is included in the conservative peak estimate.
    """
    manual_reserve = 0.0 if limiter_enabled else max(manual_db, 0.0)
    predicted = manual_reserve + eq_peak_db(bands, fs) + spatial_db
    return -(predicted + 1.0) if predicted > 0.0 else 0.0


def crossfeed_peak_db(preset: str, fs: float) -> float:
    """Correlated stereo reference peak for the current direct-plus-feed mixer."""
    cfg = CROSSFEED_PRESETS[preset.lower()]
    freqs = np.geomspace(20.0, fs * 0.499, 4096)
    lp_db = magnitude_response([low_pass(cfg.f0_hz, 0.5, fs)], freqs, fs)
    gain = 10.0 ** (cfg.feed_db / 20.0)
    return 20.0 * math.log10(float(np.max(1.0 + gain * 10.0 ** (lp_db / 20.0))))


def _spatial_transfers(ir_paths: Mapping[float, tuple[Path, Path]], layout: str,
                       fs: float, level: float, stereo_input: bool = False) -> dict[str, np.ndarray]:
    """Resolve each source channel to its two ear impulse responses."""
    mapping = LAYOUT_CHANNEL_SPEAKER_MAP[layout]
    stereo_input = stereo_input or layout == "HoloSpace 3D"
    channels = ("FL", "FR") if stereo_input else tuple(ch for ch, _ in mapping)
    transfers: dict[str, list[np.ndarray]] = {ch: [] for ch in channels}
    for channel, azimuth in mapping:
        pair = []
        for path in ir_paths[azimuth]:
            file_fs, values = wavfile.read(path)
            if file_fs != int(fs):
                raise ValueError(f"IR rate {file_fs} does not match graph rate {fs:g}")
            signal = np.asarray(values, dtype=np.float64)
            if np.issubdtype(values.dtype, np.integer):
                signal /= np.iinfo(values.dtype).max
            pair.append(signal)
        if stereo_input:
            sources = (("FL", 0.5), ("FR", 0.5)) if channel in ("FC", "LFE") else (
                ("FL", 1.0),) if channel in ("FL", "SL", "RL") else (("FR", 1.0),)
        else:
            sources = ((channel, 1.0),)
        for source, weight in sources:
            transfers[source].append(np.stack(pair) * (level * weight / len(mapping)))
    length = max((term.shape[1] for terms in transfers.values() for term in terms), default=1)
    resolved = {}
    for channel, terms in transfers.items():
        impulse = np.zeros((2, length))
        for term in terms:
            impulse[:, :term.shape[1]] += term
        resolved[channel] = impulse
    return resolved


def spatial_reference_peaks_db(ir_paths: Mapping[float, tuple[Path, Path]], layout: str,
                               fs: float, level: float = 1.0, *, stereo_input: bool = False) -> dict[str, float]:
    """Offline impulse and sweep checks for mono, stereo, and correlated inputs."""
    transfers = _spatial_transfers(ir_paths, layout, fs, level, stereo_input)
    channels = tuple(transfers)
    length = max(256, int(fs * 0.125))
    t = np.arange(length) / fs
    sweep = chirp(t, f0=20.0, f1=min(20000.0, fs * 0.45), t1=t[-1], method="logarithmic")
    impulse = np.zeros(length)
    impulse[0] = 1.0
    scenarios: dict[str, dict[str, np.ndarray]] = {
        "mono_impulse": {channels[0]: impulse},
        "mono_sweep": {channels[0]: sweep},
        "correlated_sweep": {ch: sweep for ch in channels},
    }
    if len(channels) >= 2:
        scenarios["stereo_opposed_sweep"] = {channels[0]: sweep, channels[1]: -sweep}
    if len(channels) > 2:
        scenarios["multichannel_impulse"] = {ch: impulse for ch in channels}
    for channel in channels:
        scenarios[f"isolated_{channel}"] = {channel: impulse}
    peaks = {}
    for label, inputs in scenarios.items():
        output = np.zeros((2, length + next(iter(transfers.values())).shape[1] - 1))
        for channel, signal in inputs.items():
            for ear in range(2):
                output[ear] += fftconvolve(signal, transfers[channel][ear])
        peaks[label] = 20.0 * math.log10(max(float(np.max(np.abs(output))), 1e-12))
    return peaks


def spatial_peak_db(ir_paths: Mapping[float, tuple[Path, Path]], layout: str,
                    fs: float, level: float = 1.0, *, stereo_input: bool = False) -> float:
    """Estimate peak gain from isolated/correlated transfers and reference signals.

    HoloSpace follows the renderer's copy and 0.5/0.5 center mix. This is not
    a true-peak or loudness measurement of arbitrary program material.
    """
    transfers = _spatial_transfers(ir_paths, layout, fs, level, stereo_input)
    isolated = list(transfers.values())
    length = isolated[0].shape[1]
    candidates = isolated + [sum(isolated, np.zeros((2, length)))]
    peak = max(float(np.max(np.abs(np.fft.rfft(signal, n=length * 8, axis=1))))
               for signal in candidates)
    impulse_peak = max(float(np.max(np.abs(signal))) for signal in candidates)
    measured = max(spatial_reference_peaks_db(ir_paths, layout, fs, level, stereo_input=stereo_input).values())
    return max(20.0 * math.log10(max(peak, impulse_peak, 1e-12)), measured)
