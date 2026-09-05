"""Headphone target curves and loudness compensation.

Harman target anchor points are approximate values read from the published
curves, expressed in dB relative to the target's 1 kHz level:

- Over-ear 2018: S. Olive, T. Welti, O. Khonsaripour, "A Statistical Model
  That Predicts Listeners' Preference Ratings of Around-Ear and On-Ear
  Headphones" / Harman over-ear target update (2018).
- In-ear 2019: S. Olive, T. Welti, O. Khonsaripour, "Factors that Influence
  Listeners' Preferred Bass and Treble Balance in Headphones" and the Harman
  in-ear target (2019), notable for its ~+7 dB bass shelf and elevated
  8-10 kHz treble region.

`iso226_loudness_compensation` implements a simplified equal-loudness
contour correction following the ISO 226:2003 shape (bass and upper-treble
boost at listening levels below the 80 phon reference), not the full
standard's lookup tables.
"""

import numpy as np

# (frequency Hz, level dB) anchor pairs, log-frequency interpolated.
HARMAN_OE_2018 = np.array(
    [
        (20.0, 6.5),
        (30.0, 6.5),
        (60.0, 6.0),
        (100.0, 4.5),
        (200.0, 1.5),
        (400.0, 0.3),
        (1000.0, 0.0),
        (2000.0, -0.5),
        (3000.0, 1.5),
        (4000.0, 1.0),
        (6000.0, -2.0),
        (8000.0, 2.0),
        (10000.0, 0.5),
        (12500.0, -3.0),
        (16000.0, -5.0),
        (20000.0, -6.0),
    ]
)

HARMAN_IE_2019 = np.array(
    [
        (20.0, 7.5),
        (60.0, 7.0),
        (105.0, 6.5),
        (200.0, 2.5),
        (500.0, 0.5),
        (1000.0, 0.0),
        (2000.0, 1.5),
        (3000.0, 5.0),
        (4000.0, 3.5),
        (5000.0, 0.0),
        (7000.0, 2.0),
        (8000.0, 4.0),
        (10000.0, 6.0),
        (12000.0, 2.0),
        (15000.0, -1.0),
        (20000.0, -2.0),
    ]
)

_REFERENCE_PHON = 80.0
_BASS_MAX_DB = 14.0
_TREBLE_MAX_DB = 6.0


def evaluate(curve: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Interpolate a (freq, dB) curve at freqs (log-frequency, clamped ends)."""
    freqs = np.asarray(freqs, dtype=float)
    log_f = np.log(curve[:, 0])
    return np.interp(np.log(np.clip(freqs, curve[0, 0], curve[-1, 0])), log_f, curve[:, 1])


def iso226_loudness_compensation(freqs: np.ndarray, spl_phon: float) -> np.ndarray:
    """Simplified ISO 226-style compensation in dB for listening at spl_phon.

    Zero at the 80 phon reference; below it, progressively boosts low bass
    and upper treble following the equal-loudness contour shape.
    """
    freqs = np.asarray(freqs, dtype=float)
    log_f = np.log(np.clip(freqs, 20.0, 20000.0))
    # Bass weight: 1 at/below 100 Hz, falling to 0 at 1 kHz.
    bass_w = np.clip((np.log(1000.0) - log_f) / np.log(1000.0 / 100.0), 0.0, 1.0)
    # Treble weight: 0 at/below 4 kHz, rising to 1 at 12 kHz.
    treble_w = np.clip((log_f - np.log(4000.0)) / np.log(12000.0 / 4000.0), 0.0, 1.0)
    scale = max(0.0, (_REFERENCE_PHON - spl_phon) / _REFERENCE_PHON)
    return scale * (_BASS_MAX_DB * bass_w + _TREBLE_MAX_DB * treble_w)
