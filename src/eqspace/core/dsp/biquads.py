"""RBJ Audio EQ Cookbook biquad coefficient design.

Implements the standard peaking, shelving, pass, and notch filter designs
from Robert Bristow-Johnson's "Cookbook Formulae for Audio EQ Biquad Filter
Coefficients", with all coefficients returned normalized so that a0 == 1.
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BiquadCoeffs:
    """Normalized biquad transfer-function coefficients (a0 always 1.0)."""

    b0: float
    b1: float
    b2: float
    a0: float
    a1: float
    a2: float


def _normalize(b0: float, b1: float, b2: float, a0: float, a1: float, a2: float) -> BiquadCoeffs:
    return BiquadCoeffs(b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0)


def _omega_alpha(f0_hz: float, q: float, fs: float) -> tuple[float, float, float, float]:
    w0 = 2.0 * np.pi * f0_hz / fs
    cos_w0 = float(np.cos(w0))
    sin_w0 = float(np.sin(w0))
    alpha = sin_w0 / (2.0 * q)
    return w0, cos_w0, sin_w0, alpha


def peaking(f0_hz: float, gain_db: float, q: float, fs: float) -> BiquadCoeffs:
    """Peaking EQ filter; gain_db applies at f0_hz."""
    a = 10.0 ** (gain_db / 40.0)
    _, cos_w0, _, alpha = _omega_alpha(f0_hz, q, fs)
    return _normalize(
        1.0 + alpha * a,
        -2.0 * cos_w0,
        1.0 - alpha * a,
        1.0 + alpha / a,
        -2.0 * cos_w0,
        1.0 - alpha / a,
    )


def low_shelf(f0_hz: float, gain_db: float, q_or_s: float, fs: float) -> BiquadCoeffs:
    """Low-shelf filter; q_or_s is interpreted as Q (shelf slope)."""
    a = 10.0 ** (gain_db / 40.0)
    _, cos_w0, _, alpha = _omega_alpha(f0_hz, q_or_s, fs)
    sqrt_a = np.sqrt(a)
    two_sqrt_a_alpha = 2.0 * sqrt_a * alpha
    return _normalize(
        a * ((a + 1.0) - (a - 1.0) * cos_w0 + two_sqrt_a_alpha),
        2.0 * a * ((a - 1.0) - (a + 1.0) * cos_w0),
        a * ((a + 1.0) - (a - 1.0) * cos_w0 - two_sqrt_a_alpha),
        (a + 1.0) + (a - 1.0) * cos_w0 + two_sqrt_a_alpha,
        -2.0 * ((a - 1.0) + (a + 1.0) * cos_w0),
        (a + 1.0) + (a - 1.0) * cos_w0 - two_sqrt_a_alpha,
    )


def high_shelf(f0_hz: float, gain_db: float, q_or_s: float, fs: float) -> BiquadCoeffs:
    """High-shelf filter; q_or_s is interpreted as Q (shelf slope)."""
    a = 10.0 ** (gain_db / 40.0)
    _, cos_w0, _, alpha = _omega_alpha(f0_hz, q_or_s, fs)
    sqrt_a = np.sqrt(a)
    two_sqrt_a_alpha = 2.0 * sqrt_a * alpha
    return _normalize(
        a * ((a + 1.0) + (a - 1.0) * cos_w0 + two_sqrt_a_alpha),
        -2.0 * a * ((a - 1.0) + (a + 1.0) * cos_w0),
        a * ((a + 1.0) + (a - 1.0) * cos_w0 - two_sqrt_a_alpha),
        (a + 1.0) - (a - 1.0) * cos_w0 + two_sqrt_a_alpha,
        2.0 * ((a - 1.0) - (a + 1.0) * cos_w0),
        (a + 1.0) - (a - 1.0) * cos_w0 - two_sqrt_a_alpha,
    )


def low_pass(f0_hz: float, q: float, fs: float) -> BiquadCoeffs:
    """Second-order low-pass with -3 dB point at f0_hz when q = 1/sqrt(2)."""
    _, cos_w0, _, alpha = _omega_alpha(f0_hz, q, fs)
    return _normalize(
        (1.0 - cos_w0) / 2.0,
        1.0 - cos_w0,
        (1.0 - cos_w0) / 2.0,
        1.0 + alpha,
        -2.0 * cos_w0,
        1.0 - alpha,
    )


def high_pass(f0_hz: float, q: float, fs: float) -> BiquadCoeffs:
    """Second-order high-pass with -3 dB point at f0_hz when q = 1/sqrt(2)."""
    _, cos_w0, _, alpha = _omega_alpha(f0_hz, q, fs)
    return _normalize(
        (1.0 + cos_w0) / 2.0,
        -(1.0 + cos_w0),
        (1.0 + cos_w0) / 2.0,
        1.0 + alpha,
        -2.0 * cos_w0,
        1.0 - alpha,
    )


def notch(f0_hz: float, q: float, fs: float) -> BiquadCoeffs:
    """Notch (band-reject) filter with null at f0_hz."""
    _, cos_w0, _, alpha = _omega_alpha(f0_hz, q, fs)
    return _normalize(
        1.0,
        -2.0 * cos_w0,
        1.0,
        1.0 + alpha,
        -2.0 * cos_w0,
        1.0 - alpha,
    )


def magnitude_response(
    coeffs_list: list[BiquadCoeffs],
    freqs: np.ndarray,
    fs: float,
) -> np.ndarray:
    """Combined magnitude response of a biquad cascade, in dB, at freqs (Hz)."""
    freqs = np.asarray(freqs, dtype=float)
    total = np.zeros_like(freqs)
    w = 2.0 * np.pi * freqs / fs
    z1 = np.exp(-1j * w)
    z2 = np.exp(-2j * w)
    for c in coeffs_list:
        num = c.b0 + c.b1 * z1 + c.b2 * z2
        den = c.a0 + c.a1 * z1 + c.a2 * z2
        total += 20.0 * np.log10(np.maximum(np.abs(num / den), 1e-12))
    return total
