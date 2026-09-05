#!/usr/bin/env python
"""Regenerate the built-in preset JSON files under src/eqspace/data/presets/.

EQ presets are produced by fitting peaking bands (fit_to_target) to a target
delta curve. Reference baseline: a flat 0 dB response — each delta is the
desired EQ deviation in dB relative to unity gain, with Harman deltas taken
directly from the target-curve tables (which are already expressed relative
to the target's own 1 kHz level). Fitted gains are clamped to +/-12 dB.

Run from the repo root:  .venv/bin/python tools/generate_presets.py
"""

import json
from pathlib import Path

import numpy as np

from eqspace.core.dsp.filter_design import fit_to_target
from eqspace.core.dsp.target_curves import (
    HARMAN_IE_2019,
    HARMAN_OE_2018,
    evaluate,
    iso226_loudness_compensation,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "eqspace" / "data" / "presets"

FIT_LOW_HZ = 30.0
FIT_HIGH_HZ = 16000.0
N_FILTERS = 10
GAIN_CLAMP_DB = 12.0

_FIT_FREQS = np.geomspace(FIT_LOW_HZ, FIT_HIGH_HZ, 120)

# Hand-drawn delta curves (freq Hz, dB) for the functional presets.
BASS_BOOST = np.array([(20.0, 6.0), (120.0, 6.0), (400.0, 1.0), (1000.0, 0.0), (20000.0, 0.0)])
VOCAL_CLARITY = np.array(
    [(20.0, -3.0), (100.0, -3.0), (400.0, 0.0), (2000.0, 2.5), (4000.0, 4.0), (8000.0, 1.0), (20000.0, 0.0)]
)
PODCAST = np.array(
    [(20.0, -8.0), (80.0, -6.0), (200.0, -1.0), (1000.0, 1.0), (3000.0, 3.0), (6000.0, 0.0), (20000.0, -2.0)]
)
GAMING_FOOTSTEPS = np.array(
    [(20.0, -4.0), (150.0, -4.0), (500.0, -1.0), (2000.0, 3.0), (5000.0, 5.0), (10000.0, 2.0), (20000.0, 0.0)]
)
LATE_NIGHT = np.array(
    [(20.0, -5.0), (100.0, -4.0), (500.0, 0.0), (1000.0, 1.0), (4000.0, -1.0), (10000.0, -4.0), (20000.0, -5.0)]
)

HARMAN_OE_BASIS = (
    "Harman over-ear target (Olive, Welti & Khonsaripour, 2018 update); peaking-band fit "
    "of the published target curve relative to a flat 0 dB baseline."
)
HARMAN_IE_BASIS = (
    "Harman in-ear target (Olive, Welti & Khonsaripour, 2019), with its ~+7 dB bass shelf; "
    "peaking-band fit of the published target curve relative to a flat 0 dB baseline."
)

FIT_PRESETS = {
    "harman_over_ear_2018": {
        "description": "Harman over-ear 2018 target curve, fitted with 10 peaking bands.",
        "research_basis": HARMAN_OE_BASIS,
        "delta": evaluate(HARMAN_OE_2018, _FIT_FREQS),
    },
    "harman_in_ear_2019": {
        "description": "Harman in-ear 2019 target curve, fitted with 10 peaking bands.",
        "research_basis": HARMAN_IE_BASIS,
        "delta": evaluate(HARMAN_IE_2019, _FIT_FREQS),
    },
    "bass_boost": {
        "description": "+6 dB low-shelf-style boost below ~120 Hz for bass-heavy listening.",
        "research_basis": "Generic consumer bass-shelf voicing; not tied to a specific study.",
        "delta": evaluate(BASS_BOOST, _FIT_FREQS),
    },
    "vocal_clarity": {
        "description": "2-4 kHz presence lift with a gentle low cut to bring vocals forward.",
        "research_basis": "Speech intelligibility research highlights the 2-5 kHz consonant region "
        "(e.g. ANSI S3.5 speech intelligibility weighting).",
        "delta": evaluate(VOCAL_CLARITY, _FIT_FREQS),
    },
    "podcast": {
        "description": "Spoken-word tuning: rumble cut, mild presence boost, softened top end.",
        "research_basis": "Speech-band emphasis (ANSI S3.5) plus high-pass practice from broadcast "
        "voice processing.",
        "delta": evaluate(PODCAST, _FIT_FREQS),
    },
    "gaming_footsteps": {
        "description": "Cuts low rumble and lifts 2-6 kHz to make footsteps and positional cues stand out.",
        "research_basis": "Footstep/positional audio cues concentrate in the 2-6 kHz region; common "
        "esports EQ practice.",
        "delta": evaluate(GAMING_FOOTSTEPS, _FIT_FREQS),
    },
    "late_night": {
        "description": "Reduced bass and treble extremes with a slight mid focus for quiet listening.",
        "research_basis": "Inverse of the ISO 226 equal-loudness contour direction: at low SPL the ear "
        "needs less extreme-band energy for balance.",
        "delta": evaluate(LATE_NIGHT, _FIT_FREQS),
    },
    "loudness_low_listening": {
        "description": "ISO 226-style loudness compensation for listening at ~50 phon.",
        "research_basis": "ISO 226:2003 equal-loudness contours (simplified implementation in "
        "target_curves.iso226_loudness_compensation).",
        "delta": iso226_loudness_compensation(_FIT_FREQS, spl_phon=50.0),
    },
}

STATIC_PRESETS = {
    "crossfeed_bauer": {
        "description": "Bauer stereophonic-to-binaural crossfeed for headphone listening to stereo mixes.",
        "research_basis": "B. Bauer, 'Stereophonic Earphones and Binaural Loudspeakers', "
        "J. Audio Eng. Soc. 9(2), 1961 — low-passed crossfeed (~700 Hz, ~-4.5 dB).",
        "bands": [],
        "spatial": {"type": "crossfeed", "algorithm": "bauer", "low_cut_hz": 700.0, "level_db": -4.5},
    },
}


def _fit_bands(delta: np.ndarray) -> list[dict]:
    bands = fit_to_target(_FIT_FREQS, delta, N_FILTERS)
    return [
        {
            "band_type": b.band_type,
            "freq_hz": round(b.freq_hz, 1),
            "gain_db": float(np.clip(round(b.gain_db, 2), -GAIN_CLAMP_DB, GAIN_CLAMP_DB)),
            "q": b.q,
            "enabled": True,
        }
        for b in bands
    ]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, spec in FIT_PRESETS.items():
        payload = {
            "name": name,
            "description": spec["description"],
            "research_basis": spec["research_basis"],
            "bands": _fit_bands(spec["delta"]),
            "spatial": {},
            "mic": {},
            "volume": 1.0,
        }
        (OUT_DIR / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for name, spec in STATIC_PRESETS.items():
        payload = {
            "name": name,
            "description": spec["description"],
            "research_basis": spec["research_basis"],
            "bands": spec["bands"],
            "spatial": spec["spatial"],
            "mic": {},
            "volume": 1.0,
        }
        (OUT_DIR / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(FIT_PRESETS) + len(STATIC_PRESETS)} presets to {OUT_DIR}")


if __name__ == "__main__":
    main()
