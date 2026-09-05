"""Tests for RBJ biquad design against scipy.signal references."""

import numpy as np
import pytest
from scipy import signal

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

FS = 48000.0


def _scipy_db(coeffs: BiquadCoeffs, freqs: np.ndarray) -> np.ndarray:
    w = 2.0 * np.pi * np.asarray(freqs, dtype=float) / FS
    _, h = signal.freqz(
        [coeffs.b0, coeffs.b1, coeffs.b2],
        [coeffs.a0, coeffs.a1, coeffs.a2],
        worN=w,
    )
    return 20.0 * np.log10(np.maximum(np.abs(h), 1e-12))


def _assert_matches_scipy(coeffs: BiquadCoeffs, freqs: np.ndarray, tol_db: float = 0.1) -> None:
    ours = magnitude_response([coeffs], freqs, FS)
    ref = _scipy_db(coeffs, freqs)
    np.testing.assert_allclose(ours, ref, atol=tol_db)


FREQS = np.array([20.0, 100.0, 500.0, 1000.0, 5000.0, 10000.0, 18000.0])


class TestNotch:
    def test_coefficients_match_iirnotch(self):
        f0, q = 1000.0, 4.0
        coeffs = notch(f0, q, FS)
        b, a = signal.iirnotch(f0, q, fs=FS)
        np.testing.assert_allclose([coeffs.b0, coeffs.b1, coeffs.b2], b, atol=1e-3)
        np.testing.assert_allclose([coeffs.a0, coeffs.a1, coeffs.a2], a, atol=1e-3)

    def test_deep_null_at_center(self):
        coeffs = notch(1000.0, 4.0, FS)
        resp = magnitude_response([coeffs], np.array([1000.0]), FS)
        assert resp[0] < -60.0

    def test_unity_away_from_center(self):
        coeffs = notch(1000.0, 4.0, FS)
        _assert_matches_scipy(coeffs, FREQS)


class TestPeaking:
    @pytest.mark.parametrize("gain_db", [-12.0, -6.0, 3.0, 9.0])
    def test_center_gain_and_shape(self, gain_db):
        f0, q = 2000.0, 1.5
        coeffs = peaking(f0, gain_db, q, FS)
        resp = magnitude_response([coeffs], np.array([f0]), FS)
        assert abs(resp[0] - gain_db) < 0.1
        _assert_matches_scipy(coeffs, FREQS)

    def test_zero_gain_is_transparent(self):
        coeffs = peaking(1000.0, 0.0, 1.0, FS)
        resp = magnitude_response([coeffs], FREQS, FS)
        np.testing.assert_allclose(resp, 0.0, atol=1e-9)


class TestShelves:
    def test_low_shelf_dc_gain(self):
        gain_db = 6.0
        coeffs = low_shelf(200.0, gain_db, 0.707, FS)
        resp = magnitude_response([coeffs], np.array([1.0, 20000.0]), FS)
        assert abs(resp[0] - gain_db) < 0.1
        assert abs(resp[1]) < 0.1
        _assert_matches_scipy(coeffs, FREQS)

    def test_high_shelf_nyquist_gain(self):
        gain_db = -8.0
        coeffs = high_shelf(5000.0, gain_db, 0.707, FS)
        resp = magnitude_response([coeffs], np.array([1.0, 20000.0]), FS)
        assert abs(resp[0]) < 0.1
        assert abs(resp[1] - gain_db) < 0.2
        _assert_matches_scipy(coeffs, FREQS)

    def test_shelf_half_gain_at_corner(self):
        gain_db = 6.0
        coeffs = low_shelf(200.0, gain_db, 1.0, FS)
        resp = magnitude_response([coeffs], np.array([200.0]), FS)
        assert abs(resp[0] - gain_db / 2.0) < 0.2


class TestPassFilters:
    def test_low_pass(self):
        coeffs = low_pass(1000.0, 0.7071, FS)
        resp = magnitude_response([coeffs], np.array([20.0, 1000.0, 20000.0]), FS)
        assert abs(resp[0]) < 0.1
        assert abs(resp[1] + 3.0) < 0.2  # -3 dB at f0 for Q=1/sqrt(2)
        assert resp[2] < -60.0
        _assert_matches_scipy(coeffs, FREQS)

    def test_high_pass(self):
        coeffs = high_pass(1000.0, 0.7071, FS)
        resp = magnitude_response([coeffs], np.array([20.0, 1000.0, 20000.0]), FS)
        assert resp[0] < -60.0
        assert abs(resp[1] + 3.0) < 0.2
        assert abs(resp[2]) < 0.1
        _assert_matches_scipy(coeffs, FREQS)


class TestCascade:
    def test_cascade_is_sum_of_individual(self):
        coeffs = [peaking(500.0, 6.0, 2.0, FS), high_shelf(5000.0, -4.0, 0.7, FS)]
        combined = magnitude_response(coeffs, FREQS, FS)
        summed = sum(magnitude_response([c], FREQS, FS) for c in coeffs)
        np.testing.assert_allclose(combined, summed, atol=1e-9)

    def test_empty_cascade_is_flat(self):
        np.testing.assert_allclose(magnitude_response([], FREQS, FS), 0.0, atol=1e-12)

    def test_coeffs_normalized(self):
        for coeffs in (
            peaking(1000.0, 6.0, 1.0, FS),
            low_shelf(200.0, 6.0, 0.7, FS),
            high_shelf(5000.0, -6.0, 0.7, FS),
            low_pass(1000.0, 0.707, FS),
            high_pass(1000.0, 0.707, FS),
            notch(1000.0, 4.0, FS),
        ):
            assert coeffs.a0 == 1.0
