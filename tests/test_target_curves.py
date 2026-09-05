"""Tests for target_curves: Harman targets and ISO 226-style loudness compensation."""

import numpy as np
import pytest

from eqspace.core.dsp.target_curves import (
    HARMAN_IE_2019,
    HARMAN_OE_2018,
    evaluate,
    iso226_loudness_compensation,
)


class TestCurveTables:
    @pytest.mark.parametrize("curve", [HARMAN_OE_2018, HARMAN_IE_2019])
    def test_monotonic_frequency_grid(self, curve):
        freqs = curve[:, 0]
        assert np.all(np.diff(freqs) > 0.0)

    @pytest.mark.parametrize("curve", [HARMAN_OE_2018, HARMAN_IE_2019])
    def test_sane_db_range(self, curve):
        assert np.all(np.abs(curve[:, 1]) <= 20.0)

    @pytest.mark.parametrize("curve", [HARMAN_OE_2018, HARMAN_IE_2019])
    def test_spans_audible_band(self, curve):
        assert curve[0, 0] <= 20.0
        assert curve[-1, 0] >= 20000.0

    def test_ie_bass_shelf_positive(self):
        # Harman IE 2019 has a substantial bass shelf (~+5-8 dB below ~200 Hz)
        db_at_60 = float(evaluate(HARMAN_IE_2019, np.array([60.0]))[0])
        assert 4.0 < db_at_60 < 10.0

    def test_oe_bass_shelf_positive(self):
        db_at_60 = float(evaluate(HARMAN_OE_2018, np.array([60.0]))[0])
        assert 3.0 < db_at_60 < 10.0

    def test_ie_treble_feature_sign(self):
        # IE 2019 target has a positive 10 kHz region relative to mid reference
        db_10k = float(evaluate(HARMAN_IE_2019, np.array([10000.0]))[0])
        db_1k = float(evaluate(HARMAN_IE_2019, np.array([1000.0]))[0])
        assert db_10k > db_1k - 6.0  # not a deep null; presence feature remains elevated
        assert db_10k > 0.0


class TestEvaluate:
    def test_hits_anchor_points_exactly(self):
        freqs = HARMAN_OE_2018[:, 0]
        db = HARMAN_OE_2018[:, 1]
        np.testing.assert_allclose(evaluate(HARMAN_OE_2018, freqs), db, atol=1e-9)

    def test_interpolation_midpoint(self):
        curve = np.array([[100.0, 0.0], [1000.0, 10.0]])
        mid = float(evaluate(curve, np.array([np.sqrt(100.0 * 1000.0)]))[0])
        # log-frequency interpolation: midpoint in log f is midpoint in dB
        assert abs(mid - 5.0) < 0.1

    def test_extrapolation_clamps(self):
        curve = np.array([[100.0, 2.0], [1000.0, 4.0]])
        vals = evaluate(curve, np.array([10.0, 50000.0]))
        np.testing.assert_allclose(vals, [2.0, 4.0], atol=1e-9)


class TestLoudnessCompensation:
    def test_low_level_boosts_bass_and_treble(self):
        freqs = np.array([50.0, 1000.0, 10000.0])
        comp = iso226_loudness_compensation(freqs, spl_phon=40.0)
        assert comp[0] > 3.0  # bass boost
        assert abs(comp[1]) < 1.0  # reference midband ~flat
        assert comp[2] > 1.0  # upper-treble boost

    def test_reference_level_is_flat(self):
        freqs = np.geomspace(20.0, 20000.0, 50)
        comp = iso226_loudness_compensation(freqs, spl_phon=80.0)
        np.testing.assert_allclose(comp, 0.0, atol=0.5)

    def test_monotonic_in_level(self):
        freqs = np.array([50.0])
        low = float(iso226_loudness_compensation(freqs, spl_phon=30.0)[0])
        mid = float(iso226_loudness_compensation(freqs, spl_phon=60.0)[0])
        assert low > mid > 0.0
