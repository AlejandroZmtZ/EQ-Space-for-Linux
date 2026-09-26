"""Tests for the built-in preset library (Task 5)."""

import numpy as np
import pytest
from pydantic import ValidationError

from eqspace.core.dsp.filter_design import VALID_BAND_TYPES, design_filters
from eqspace.core.dsp.biquads import magnitude_response
from eqspace.core.dsp.target_curves import HARMAN_IE_2019, HARMAN_OE_2018, evaluate
from eqspace.core.profiles import models, presets

EXPECTED_PRESETS = [
    "flat",
    "harman_over_ear_2018",
    "harman_in_ear_2019",
    "bass_boost",
    "vocal_clarity",
    "podcast",
    "gaming_footsteps",
    "late_night",
    "loudness_low_listening",
]

GAIN_LIMIT_DB = 15.0


class TestPresetLibrary:
    def test_all_expected_presets_present(self):
        assert sorted(presets.list_presets()) == sorted(EXPECTED_PRESETS)

    def test_flat_preserves_active_spatial_on_apply(self):
        flat = presets.load_preset("flat").to_profile()
        assert flat.bands == []
        assert flat.preamp_db == 0.0
        assert flat.spatial_enabled is None
        assert flat.automatic_headroom is True

    @pytest.mark.parametrize("name", EXPECTED_PRESETS)
    def test_metadata_fields(self, name):
        preset = presets.load_preset(name)
        assert preset.name == name
        assert preset.description.strip()
        assert preset.research_basis.strip()

    @pytest.mark.parametrize("name", EXPECTED_PRESETS)
    def test_bands_within_valid_ranges(self, name):
        preset = presets.load_preset(name)
        for band in preset.bands:
            assert band.band_type in VALID_BAND_TYPES
            assert 0.0 < band.freq_hz <= 22050.0
            assert band.q > 0.0
            assert abs(band.gain_db) <= GAIN_LIMIT_DB
            # Must construct a real EQBand without error.
            band.to_eqband()

    @pytest.mark.parametrize("name", EXPECTED_PRESETS)
    def test_preset_to_profile(self, name):
        profile = presets.preset_to_profile(name)
        assert isinstance(profile, models.EQProfile)
        assert profile.name == name
        assert profile.to_bands() == presets.load_preset(name).to_bands()

    def test_load_missing_raises(self):
        with pytest.raises(FileNotFoundError):
            presets.load_preset("does_not_exist")

    @pytest.mark.parametrize("bad_name", ["../secret", "a/b", "a\\b", "..", ""])
    def test_load_rejects_unsafe_names(self, bad_name):
        with pytest.raises(ValueError):
            presets.load_preset(bad_name)

    def test_volume_bounds_enforced(self):
        preset = presets.load_preset("bass_boost")
        assert 0.0 <= preset.volume <= 2.0
        payload = preset.model_dump()
        for bad_volume in (-0.5, 2.5):
            payload["volume"] = bad_volume
            with pytest.raises(ValidationError):
                presets.Preset.model_validate(payload)


class TestHarmanFits:
    def _response(self, name, freqs):
        preset = presets.load_preset(name)
        bands = preset.to_bands()
        return magnitude_response(design_filters(bands, 48000.0), freqs, 48000.0)

    @pytest.mark.parametrize(
        ("name", "curve"),
        [("harman_over_ear_2018", HARMAN_OE_2018), ("harman_in_ear_2019", HARMAN_IE_2019)],
    )
    def test_fit_tracks_target(self, name, curve):
        freqs = np.geomspace(30.0, 16000.0, 60)
        target = evaluate(curve, freqs)
        response = self._response(name, freqs)
        # Least-squares peaking fit should track the target within ~2 dB RMS.
        assert np.sqrt(np.mean((response - target) ** 2)) < 2.0

    def test_oe_bass_shelf_sign(self):
        freqs = np.array([60.0])
        assert self._response("harman_over_ear_2018", freqs)[0] > 2.0

    def test_ie_treble_feature(self):
        freqs = np.array([10000.0])
        # Target is +6 dB at 10 kHz; the smooth 10-band fit reproduces a clear positive lift.
        assert self._response("harman_in_ear_2019", freqs)[0] > 1.5


class TestFunctionalPresets:
    def _response(self, name, freqs):
        preset = presets.load_preset(name)
        return magnitude_response(design_filters(preset.to_bands(), 48000.0), freqs, 48000.0)

    def test_bass_boost_raises_bass(self):
        resp = self._response("bass_boost", np.array([60.0, 1000.0]))
        assert resp[0] > 3.0
        assert abs(resp[1]) < 1.5

    def test_vocal_clarity_raises_presence(self):
        resp = self._response("vocal_clarity", np.array([3000.0, 100.0]))
        assert resp[0] > 2.0
        assert resp[0] > resp[1] + 2.0

    def test_loudness_low_listening_shape(self):
        resp = self._response("loudness_low_listening", np.array([50.0, 1000.0, 10000.0]))
        assert resp[0] > 3.0
        assert abs(resp[1]) < 2.0
        assert resp[2] > 1.0

    def test_crossfeed_bauer_has_spatial_settings(self):
        preset = presets.load_preset("crossfeed_bauer")
        assert preset.spatial.get("type") == "crossfeed"
        assert preset.to_bands() == []
        assert "crossfeed_bauer" not in presets.list_presets()
