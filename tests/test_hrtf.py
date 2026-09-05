"""Tests for HRTF extraction, caching and resampling (no pysofa needed)."""

import numpy as np
import pytest

from eqspace.core.dsp.hrtf import (
    ExtractedIRs,
    HRTFUnavailable,
    PySofaExtractor,
    SPEAKER_AZIMUTHS,
    _nearest_azimuth,
    default_cache_dir,
    extract_speaker_irs,
    resample_ir,
)


class DeltaExtractor:
    """Synthetic extractor: unit deltas, length 16, at source_fs."""

    def __init__(self, source_fs=48000.0):
        self.source_fs = source_fs
        self.calls = 0

    def extract(self, path, azimuths_deg):
        self.calls += 1
        irs = {}
        for az in azimuths_deg:
            left = np.zeros(16)
            right = np.zeros(16)
            left[0] = 1.0
            right[0] = float(az) / 110.0  # encode azimuth for assertions
            irs[float(az)] = (left, right)
        return ExtractedIRs(sample_rate=self.source_fs, irs=irs)


@pytest.fixture
def sofa_file(tmp_path):
    path = tmp_path / "fake.sofa"
    path.write_bytes(b"not a real sofa file")
    return path


def test_pysofa_extractor_unavailable():
    try:
        import pysofa  # noqa: F401
        pytest.skip("pysofa is installed in this environment")
    except ImportError:
        pass
    with pytest.raises(HRTFUnavailable, match="pysofa"):
        PySofaExtractor()


def test_extract_writes_npy_pairs(sofa_file, tmp_path):
    extractor = DeltaExtractor()
    paths = extract_speaker_irs(
        sofa_file, 48000.0, cache_dir=tmp_path / "cache", extractor=extractor
    )
    assert set(paths) == set(SPEAKER_AZIMUTHS)
    for az, (left, right) in paths.items():
        assert left.exists() and right.exists()
        assert left.suffix == ".npy" and right.suffix == ".npy"
        l = np.load(left)
        r = np.load(right)
        assert l[0] == pytest.approx(1.0)
        assert r[0] == pytest.approx(az / 110.0)
    assert extractor.calls == 1


def test_extract_uses_cache_on_second_call(sofa_file, tmp_path):
    extractor = DeltaExtractor()
    cache = tmp_path / "cache"
    first = extract_speaker_irs(sofa_file, 48000.0, cache_dir=cache, extractor=extractor)
    second = extract_speaker_irs(sofa_file, 48000.0, cache_dir=cache, extractor=extractor)
    assert first == second
    assert extractor.calls == 1  # no re-extraction


def test_cache_invalidates_on_rate_change(sofa_file, tmp_path):
    extractor = DeltaExtractor()
    cache = tmp_path / "cache"
    extract_speaker_irs(sofa_file, 48000.0, cache_dir=cache, extractor=extractor)
    extract_speaker_irs(sofa_file, 44100.0, cache_dir=cache, extractor=extractor)
    assert extractor.calls == 2


def test_cache_invalidates_on_file_change(sofa_file, tmp_path):
    extractor = DeltaExtractor()
    cache = tmp_path / "cache"
    extract_speaker_irs(sofa_file, 48000.0, cache_dir=cache, extractor=extractor)
    sofa_file.write_bytes(b"changed content with different size")
    extract_speaker_irs(sofa_file, 48000.0, cache_dir=cache, extractor=extractor)
    assert extractor.calls == 2


def test_resample_changes_length():
    ir = np.zeros(16)
    ir[0] = 1.0
    out = resample_ir(ir, 48000.0, 96000.0)
    assert len(out) == 32
    assert out[0] == pytest.approx(1.0)


def test_resample_same_rate_is_identity():
    ir = np.arange(8, dtype=np.float64)
    assert np.array_equal(resample_ir(ir, 48000.0, 48000.0), ir)


def test_resample_rejects_bad_rates():
    with pytest.raises(ValueError):
        resample_ir(np.zeros(4), 0.0, 48000.0)


def test_missing_sofa_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_speaker_irs(tmp_path / "nope.sofa", 48000.0, extractor=DeltaExtractor())


def test_default_cache_dir_honours_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_cache_dir() == tmp_path / "xdg" / "eqspace" / "hrtf"


def test_default_cache_dir_home_fallback(monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert str(default_cache_dir()).endswith(".cache/eqspace/hrtf")


def test_nearest_azimuth_wraparound():
    positions = np.array([[358.0, 0.0, 1.0], [90.0, 0.0, 1.0]])
    assert _nearest_azimuth(positions, 0.0) == 0  # 358° is 2° from 0°
    assert _nearest_azimuth(positions, 90.0) == 1


def test_nearest_azimuth_no_match():
    positions = np.array([[10.0, 0.0, 1.0]])
    with pytest.raises(ValueError, match="no SOFA measurement"):
        _nearest_azimuth(positions, 90.0)
