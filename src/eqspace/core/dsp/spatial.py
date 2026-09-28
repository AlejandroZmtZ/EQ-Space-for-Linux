"""Offline Spatial preparation and complex hybrid transfer estimates.

No runtime sample processing: impulse/sweep arrays are static design references.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.signal import chirp, fftconvolve, freqz, lfilter

from .biquads import BiquadCoeffs
from .headroom import _spatial_transfers, crossfeed_peak_db, spatial_peak_db
from .hrtf import builtin_kemar_path, extract_speaker_irs, holospace_default_path, is_builtin_kemar
from .crossfeed import render_crossfeed_chain_args
from ..filterchain.spatial import (
    HYBRID_COMMON_DELAY_SAMPLES, HYBRID_DESCRIPTION, HYBRID_HOLO_WEIGHT,
    HYBRID_MEIER_WEIGHT, HYBRID_CUTOFF_HZ, LEGACY_HYBRID_DESCRIPTIONS, LAYOUT_CHANNEL_SPEAKER_MAP,
    LAYOUT_CHANNELS, SpatialChainRenderer, SpeakerIR, hybrid_controls, render_hybrid_chain_args,
)


def native_meier_lowpass(fs: float, cutoff_hz: float = 650.0) -> BiquadCoeffs:
    """PipeWire1.0.5 bq_lowpass: Q port is resonance dB, unlike RBJ Q.

    Match biquad.c::biquad_lowpass; retain the existing rendered Q=.5 control.
    """
    if not math.isfinite(cutoff_hz) or cutoff_hz <= 0 or not math.isfinite(fs) or fs <= 2 * cutoff_hz:
        raise ValueError('crossfeed cutoff must be positive and below the graph Nyquist rate')
    resonance = .5
    gain = 10**(.05*resonance)
    damping = math.sqrt((4-math.sqrt(16-16/(gain*gain)))/2)
    theta = 2*math.pi*cutoff_hz/fs
    sn = .5*damping*math.sin(theta)
    beta = .5*(1-sn)/(1+sn)
    gamma = (.5+beta)*math.cos(theta)
    alpha = .25*(.5+beta-gamma)
    values = np.asarray((2*alpha, 4*alpha, 2*alpha, 1, -2*gamma, 2*beta), dtype=np.float32)
    return BiquadCoeffs(*map(float, values))


def hybrid_branch_transfers(ir_paths, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Unweighted offline branch references, indexed [ear,source,sample]."""
    coeff = native_meier_lowpass(fs, HYBRID_CUTOFF_HZ)
    holo = _spatial_transfers(ir_paths, "HoloSpace 3D", fs, 1, stereo_input=True)
    # 125ms makes the 1400Hz Q=.5 lowpass residual negligible at every graph rate.
    length = max(holo['FL'].shape[-1], int(fs*.125))
    h_matrix = np.zeros((2, 2, length))
    m_matrix = np.zeros_like(h_matrix)
    for source, channel in enumerate(('FL', 'FR')):
        h_matrix[:, source, :holo[channel].shape[-1]] = holo[channel]
    impulse = np.zeros(length)
    impulse[HYBRID_COMMON_DELAY_SAMPLES] = 1
    crossed = lfilter([coeff.b0, coeff.b1, coeff.b2], [1, coeff.a1, coeff.a2], impulse)
    feed = 10**(-4.5/20)
    for ear in range(2):
        m_matrix[ear, ear] = impulse
        m_matrix[ear, 1-ear] = feed*crossed
    return h_matrix, m_matrix


def hybrid_transfers(ir_paths, fs: float, level: float = 1.0) -> np.ndarray:
    """Rendered fixed 60/40 impulse matrix, indexed [ear,source,sample]."""
    hybrid_controls(level)
    holo, meier = hybrid_branch_transfers(ir_paths, fs)
    return level * (HYBRID_HOLO_WEIGHT * holo + HYBRID_MEIER_WEIGHT * meier)


def _reference_peaks(matrix: np.ndarray, fs: float) -> dict[str, float]:
    length = int(fs*.125)
    t = np.arange(length)/fs
    sweep = chirp(t, f0=20, f1=min(20000, fs*.45), t1=t[-1], method='logarithmic')
    impulse = np.zeros(length)
    impulse[0] = 1
    zeros = np.zeros(length)
    scenarios = {'isolated_FL_impulse': (impulse, zeros), 'isolated_FR_impulse': (zeros, impulse),
                 'correlated_impulse': (impulse, impulse), 'isolated_FL_sweep': (sweep, zeros),
                 'isolated_FR_sweep': (zeros, sweep), 'correlated_sweep': (sweep, sweep),
                 'opposed_sweep': (sweep, -sweep)}
    peaks = {}
    for name, inputs in scenarios.items():
        peak = max(float(np.max(np.abs(sum(fftconvolve(inputs[source], matrix[ear, source])
                                           for source in range(2))))) for ear in range(2))
        peaks[name] = 20*math.log10(max(peak, 1e-12))
    return peaks


def hybrid_reference_peaks_db(ir_paths, fs, level=1.0):
    return _reference_peaks(hybrid_transfers(ir_paths, fs, level), fs)


def hybrid_peak_db(ir_paths, fs, level=1.0) -> float:
    """Row-sum complex response plus reference transient reserve.

    Includes DC/Nyquist. Row sums bound independently phased full-scale stereo
    tones; the impulse L1 bound additionally reserves bounded sample sequences.
    This is a conservative static estimate, not measured true-peak loudness.
    """
    matrix = hybrid_transfers(ir_paths, fs, level)
    response = np.fft.rfft(matrix, n=max(32768, 2**math.ceil(math.log2(matrix.shape[-1]*8))), axis=-1)
    row_peak = float(np.max(np.sum(np.abs(response), axis=1)))
    transient_bound = float(np.max(np.sum(np.abs(matrix), axis=(1, 2))))
    return max(20*math.log10(max(row_peak, transient_bound, 1e-12)),
               max(_reference_peaks(matrix, fs).values()))


def prepare_spatial(state: dict, fs: float) -> tuple[str, float, str]:
    """Prepare a main-thread state snapshot without accessing Qt widgets."""
    profile = str(state.get('profile', 'HoloSpace 3D (Signature Spatial Immersion)'))
    level = int(state.get('wet', 100))/100
    if profile == HYBRID_DESCRIPTION or profile in LEGACY_HYBRID_DESCRIPTIONS:
        mapping = LAYOUT_CHANNEL_SPEAKER_MAP['HoloSpace 3D']
        irs = extract_speaker_irs(holospace_default_path(), fs, azimuths_deg=sorted({az for _, az in mapping}))
        # Legacy balance/crossover keys do not override the fixed product tuning.
        return render_hybrid_chain_args(irs, fs, level), hybrid_peak_db(irs, fs, level), profile
    if state.get('crossfeed', False):
        mode = str(state.get('crossfeed_mode', 'Bauer')).lower()
        args = render_crossfeed_chain_args(mode, fs)
        if level != 1:
            from .crossfeed import CROSSFEED_PRESETS
            gain = 10**(CROSSFEED_PRESETS[mode].feed_db/20)
            args = args.replace('\"Gain 1\" = 1.0', f'\"Gain 1\" = {level:.12g}')
            args = args.replace(f'\"Gain 2\" = {gain:g}', f'\"Gain 2\" = {level*gain:.12g}')
        peak = crossfeed_peak_db(mode, fs) + 20*math.log10(max(level, 1e-12))
        return args, peak, profile
    layout = str(state.get('layout', 'HoloSpace 3D'))
    mapping = LAYOUT_CHANNEL_SPEAKER_MAP[layout]
    path = Path(str(state['sofa_path'])) if state.get('sofa_path') else builtin_kemar_path()
    if layout == 'HoloSpace 3D' and is_builtin_kemar(path):
        path = holospace_default_path()
    irs = extract_speaker_irs(path, fs, azimuths_deg=sorted({az for _, az in mapping}))
    stereo = layout == 'HoloSpace 3D' or (profile.startswith('Cinema') and state.get('stereo_expansion', False))
    level = max(level, 1e-6)
    speakers = [SpeakerIR(az, *irs[az], channel=ch) for ch, az in mapping]
    args = SpatialChainRenderer().render_args(speakers, gain=level,
                                             channels=('FL','FR') if stereo else LAYOUT_CHANNELS[layout])
    return args, spatial_peak_db(irs, layout, fs, level, stereo_input=stereo), profile
