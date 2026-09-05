"""Tests for filter_design: EQBand model, design_filters, fit_to_target."""

import numpy as np
import pytest

from eqspace.core.dsp.biquads import magnitude_response
from eqspace.core.dsp.filter_design import EQBand, design_filters, fit_to_target

FS = 48000.0


class TestEQBand:
    def test_defaults(self):
        band = EQBand(band_type="peaking", freq_hz=1000.0, gain_db=3.0, q=1.0)
        assert band.enabled is True

    def test_rejects_unknown_type(self):
        with pytest.raises(ValueError):
            EQBand(band_type="warp", freq_hz=1000.0, gain_db=3.0, q=1.0)

    def test_rejects_nonpositive_freq(self):
        with pytest.raises(ValueError):
            EQBand(band_type="peaking", freq_hz=0.0, gain_db=3.0, q=1.0)

    def test_rejects_nonpositive_q(self):
        with pytest.raises(ValueError):
            EQBand(band_type="peaking", freq_hz=1000.0, gain_db=3.0, q=-1.0)


class TestDesignFilters:
    @pytest.mark.parametrize("band_type", ["peaking", "low_shelf", "high_shelf", "low_pass", "high_pass", "notch"])
    def test_round_trip_band_types(self, band_type):
        gain = 5.0 if band_type in ("peaking", "low_shelf", "high_shelf") else 0.0
        q = 1.2 if band_type in ("peaking", "low_shelf", "high_shelf", "notch") else 0.7071
        band = EQBand(band_type=band_type, freq_hz=1000.0, gain_db=gain, q=q)
        coeffs = design_filters([band], FS)
        assert len(coeffs) == 1
        resp = magnitude_response(coeffs, np.array([1000.0]), FS)
        if band_type == "peaking":
            assert abs(resp[0] - gain) < 0.5
        elif band_type == "low_shelf":
            assert abs(resp[0] - gain / 2.0) < 0.5
        elif band_type == "high_shelf":
            assert abs(resp[0] - gain / 2.0) < 0.5
        elif band_type == "notch":
            assert resp[0] < -40.0
        else:  # pass filters: -3 dB at f0 for Butterworth-ish Q
            assert -6.0 < resp[0] < 0.5

    def test_disabled_bands_are_skipped(self):
        bands = [
            EQBand(band_type="peaking", freq_hz=1000.0, gain_db=6.0, q=1.0, enabled=False),
            EQBand(band_type="peaking", freq_hz=8000.0, gain_db=-3.0, q=1.0),
        ]
        coeffs = design_filters(bands, FS)
        assert len(coeffs) == 1
        resp = magnitude_response(coeffs, np.array([1000.0, 8000.0]), FS)
        assert abs(resp[0]) < 0.3
        assert abs(resp[1] + 3.0) < 0.3

    def test_empty_input(self):
        assert design_filters([], FS) == []


class TestFitToTarget:
    def _target(self, freqs: np.ndarray) -> np.ndarray:
        # +6 dB bump at 1 kHz, -4 dB dip at 4 kHz, in dB
        bump = 6.0 * np.exp(-0.5 * (np.log2(freqs / 1000.0) / 0.5) ** 2)
        dip = -4.0 * np.exp(-0.5 * (np.log2(freqs / 4000.0) / 0.4) ** 2)
        return bump + dip

    def test_fit_reduces_rms_error(self):
        freqs = np.geomspace(20.0, 20000.0, 200)
        target = self._target(freqs)
        bands = fit_to_target(freqs, target, n_filters=10)
        assert len(bands) == 10
        assert all(b.band_type == "peaking" for b in bands)
        coeffs = design_filters(bands, FS)
        fitted = magnitude_response(coeffs, freqs, FS)
        rms_before = float(np.sqrt(np.mean(target**2)))
        rms_after = float(np.sqrt(np.mean((target - fitted) ** 2)))
        assert rms_after < rms_before
        assert rms_after < 0.4 * rms_before

    def test_flat_target_gives_small_gains(self):
        freqs = np.geomspace(20.0, 20000.0, 100)
        bands = fit_to_target(freqs, np.zeros_like(freqs), n_filters=4)
        coeffs = design_filters(bands, FS)
        fitted = magnitude_response(coeffs, freqs, FS)
        assert float(np.max(np.abs(fitted))) < 0.5

    def test_invalid_n_filters(self):
        freqs = np.geomspace(20.0, 20000.0, 50)
        with pytest.raises(ValueError):
            fit_to_target(freqs, np.zeros_like(freqs), n_filters=0)
