"""EQ band model and filter design / target fitting on top of RBJ biquads."""

from dataclasses import dataclass

import numpy as np

from eqspace.core.dsp.biquads import (
    BiquadCoeffs,
    high_pass,
    high_shelf,
    low_pass,
    low_shelf,
    magnitude_response,
    notch,
    peaking,
)

VALID_BAND_TYPES = ("peaking", "low_shelf", "high_shelf", "low_pass", "high_pass", "notch")

_FIT_FS = 48000.0
_FIT_Q = 2.0


@dataclass(frozen=True)
class EQBand:
    """A single parametric EQ band."""

    band_type: str
    freq_hz: float
    gain_db: float
    q: float
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.band_type not in VALID_BAND_TYPES:
            raise ValueError(f"unknown band_type {self.band_type!r}; expected one of {VALID_BAND_TYPES}")
        if self.freq_hz <= 0.0:
            raise ValueError(f"freq_hz must be positive, got {self.freq_hz}")
        if self.q <= 0.0:
            raise ValueError(f"q must be positive, got {self.q}")


def design_filters(bands: list[EQBand], fs: float) -> list[BiquadCoeffs]:
    """Design biquad coefficients for every enabled band, in order."""
    coeffs: list[BiquadCoeffs] = []
    for band in bands:
        if not band.enabled:
            continue
        if band.band_type == "peaking":
            coeffs.append(peaking(band.freq_hz, band.gain_db, band.q, fs))
        elif band.band_type == "low_shelf":
            coeffs.append(low_shelf(band.freq_hz, band.gain_db, band.q, fs))
        elif band.band_type == "high_shelf":
            coeffs.append(high_shelf(band.freq_hz, band.gain_db, band.q, fs))
        elif band.band_type == "low_pass":
            coeffs.append(low_pass(band.freq_hz, band.q, fs))
        elif band.band_type == "high_pass":
            coeffs.append(high_pass(band.freq_hz, band.q, fs))
        else:  # notch
            coeffs.append(notch(band.freq_hz, band.q, fs))
    return coeffs


def fit_to_target(
    freqs: np.ndarray,
    delta_db: np.ndarray,
    n_filters: int,
) -> list[EQBand]:
    """Fit n_filters peaking bands to a target EQ delta curve (least squares).

    Center frequencies are spread log-uniformly across the target band; gains
    are solved by least squares against the dB response of unit-gain peaking
    filters, which is approximately linear in gain for moderate dB values.
    """
    if n_filters < 1:
        raise ValueError(f"n_filters must be >= 1, got {n_filters}")
    freqs = np.asarray(freqs, dtype=float)
    delta_db = np.asarray(delta_db, dtype=float)
    centers = np.geomspace(freqs.min(), freqs.max(), n_filters)
    basis = np.stack(
        [magnitude_response([peaking(f0, 1.0, _FIT_Q, _FIT_FS)], freqs, _FIT_FS) for f0 in centers],
        axis=1,
    )
    gains, *_ = np.linalg.lstsq(basis, delta_db, rcond=None)
    return [
        EQBand(band_type="peaking", freq_hz=float(f0), gain_db=float(g), q=_FIT_Q)
        for f0, g in zip(centers, gains)
    ]
