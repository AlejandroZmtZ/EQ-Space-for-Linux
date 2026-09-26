"""Tests for profile models and storage (Task 5)."""

import json

import pytest
from pydantic import ValidationError

from eqspace.core.dsp.filter_design import EQBand
from eqspace.core.profiles import models, storage


class TestBandModel:
    def test_round_trip_from_eqband(self):
        band = EQBand(band_type="peaking", freq_hz=1000.0, gain_db=-3.5, q=1.4, enabled=False)
        model = models.BandModel.from_eqband(band)
        assert model.to_eqband() == band

    def test_rejects_unknown_band_type(self):
        with pytest.raises(ValidationError):
            models.BandModel(band_type="warp", freq_hz=100.0, gain_db=1.0, q=1.0)

    def test_rejects_nonpositive_freq(self):
        with pytest.raises(ValidationError):
            models.BandModel(band_type="peaking", freq_hz=0.0, gain_db=1.0, q=1.0)

    def test_rejects_nonpositive_q(self):
        with pytest.raises(ValidationError):
            models.BandModel(band_type="peaking", freq_hz=100.0, gain_db=1.0, q=-1.0)


class TestEQProfile:
    def _sample(self) -> models.EQProfile:
        return models.EQProfile(
            name="test",
            bands=[models.BandModel(band_type="low_shelf", freq_hz=100.0, gain_db=4.0, q=0.7)],
            spatial={"type": "crossfeed"},
            mic={"enabled": False},
            output_device="alsa_output.pci-0000_00_1f.3.analog-stereo",
            volume=0.8,
        )

    def test_defaults(self):
        profile = models.EQProfile(name="empty")
        assert profile.version == models.SCHEMA_VERSION
        assert profile.bands == []
        assert profile.spatial == {}
        assert profile.mic == {}
        assert profile.output_device is None
        assert profile.volume == 1.0
        assert profile.spatial_enabled is False
        assert profile.automatic_headroom is True

    def test_json_round_trip(self):
        profile = self._sample()
        restored = models.EQProfile.model_validate_json(profile.model_dump_json())
        assert restored == profile

    def test_to_bands(self):
        profile = self._sample()
        bands = profile.to_bands()
        assert bands == [EQBand(band_type="low_shelf", freq_hz=100.0, gain_db=4.0, q=0.7)]

    def test_from_bands(self):
        bands = [EQBand(band_type="notch", freq_hz=50.0, gain_db=0.0, q=2.0)]
        profile = models.EQProfile.from_bands("notchy", bands)
        assert profile.to_bands() == bands

    def test_rejects_bad_volume(self):
        with pytest.raises(ValidationError):
            models.EQProfile(name="x", volume=-0.5)

    def test_rejects_empty_name(self):
        with pytest.raises(ValidationError):
            models.EQProfile(name="")


class TestStorage:
    @pytest.fixture()
    def xdg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        return tmp_path

    def _profile(self, name="mine") -> models.EQProfile:
        return models.EQProfile(
            name=name,
            bands=[models.BandModel(band_type="peaking", freq_hz=500.0, gain_db=2.0, q=1.0)],
        )

    def test_profiles_dir_respects_xdg(self, xdg):
        assert storage.profiles_dir() == xdg / "eqspace" / "profiles"

    def test_save_load_round_trip(self, xdg):
        profile = self._profile()
        path = storage.save_profile(profile)
        assert path.exists()
        assert storage.load_profile("mine") == profile

    def test_save_is_atomic_no_temp_left(self, xdg):
        storage.save_profile(self._profile())
        leftovers = [p for p in storage.profiles_dir().iterdir() if p.suffix != ".json"]
        assert leftovers == []

    def test_list_profiles(self, xdg):
        storage.save_profile(self._profile("alpha"))
        storage.save_profile(self._profile("beta"))
        assert storage.list_profiles() == ["alpha", "beta"]

    def test_delete_profile(self, xdg):
        storage.save_profile(self._profile("gone"))
        storage.delete_profile("gone")
        assert storage.list_profiles() == []

    def test_delete_missing_raises(self, xdg):
        with pytest.raises(FileNotFoundError):
            storage.delete_profile("nope")

    def test_load_missing_raises(self, xdg):
        with pytest.raises(FileNotFoundError):
            storage.load_profile("nope")

    def test_export_import_round_trip(self, xdg, tmp_path):
        profile = self._profile("portable")
        out = tmp_path / "export.json"
        storage.export_profile(profile, out)
        assert json.loads(out.read_text())["name"] == "portable"
        imported = storage.import_profile(out)
        assert imported == profile

    def test_export_is_atomic_no_temp_left(self, xdg, tmp_path):
        out = tmp_path / "export.json"
        storage.export_profile(self._profile("portable"), out)
        leftovers = [p for p in tmp_path.iterdir() if p.name != "export.json"]
        assert leftovers == []

    def test_saved_file_is_valid_profile_json(self, xdg):
        storage.save_profile(self._profile("raw"))
        raw = json.loads((storage.profiles_dir() / "raw.json").read_text())
        assert models.EQProfile.model_validate(raw).name == "raw"

    def test_v2_migration_keeps_manual_gain_and_unspecified_spatial(self, xdg):
        path = storage.profiles_dir() / "legacy.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"name": "legacy", "version": 2,
                                    "preamp_db": -4.5, "spatial": {"layout": "7.1"}}))
        loaded = storage.load_profile("legacy")
        assert loaded.version == 3
        assert loaded.preamp_db == -4.5
        assert loaded.automatic_headroom is False
        assert loaded.spatial_enabled is None
        storage.save_profile(loaded)
        raw = json.loads(path.read_text())
        assert raw["version"] == 3 and raw["automatic_headroom"] is False

    def test_new_profile_defaults_to_static_headroom(self, xdg):
        profile = models.EQProfile.from_bands("new", [])
        assert profile.automatic_headroom is True
        assert profile.spatial_enabled is False
