from __future__ import annotations

import math

import pytest

from eqspace.core.dsp.filter_design import EQBand
from eqspace.core.dsp.headroom import (automatic_trim_db, crossfeed_peak_db,
                                       eq_peak_db, spatial_peak_db,
                                       spatial_reference_peaks_db)
from eqspace.core.dsp.hrtf import builtin_kemar_path, extract_speaker_irs, holospace_default_path


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
def test_flat_is_unity_and_boost_gets_margin(rate):
    assert eq_peak_db([], rate) == 0.0
    assert automatic_trim_db([], rate) == 0.0
    boosted = [EQBand("peaking", 1000, 6, 1)]
    trim = automatic_trim_db(boosted, rate)
    assert trim < -6.5
    assert 6 + trim < 0


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
@pytest.mark.parametrize("layout,source", [("Stereo", builtin_kemar_path),
                                            ("5.1", builtin_kemar_path),
                                            ("7.1", builtin_kemar_path),
                                            ("HoloSpace 3D", holospace_default_path)])
def test_spatial_calibration_is_finite_for_isolated_and_correlated_paths(rate, layout, source, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from eqspace.core.filterchain.spatial import LAYOUT_AZIMUTHS
    paths = extract_speaker_irs(source(), rate, LAYOUT_AZIMUTHS[layout])
    peak = spatial_peak_db(paths, layout, rate)
    references = spatial_reference_peaks_db(paths, layout, rate)
    assert math.isfinite(peak)
    assert -30 < peak < 30
    assert references["mono_impulse"] <= peak + 1e-6
    assert references["correlated_sweep"] <= peak + 1e-6
    if layout in ("5.1", "7.1"):
        assert references["multichannel_impulse"] <= peak + 1e-6


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
@pytest.mark.parametrize("preset", ["bauer", "meier"])
def test_crossfeed_correlated_peak_is_calibrated(rate, preset):
    assert 0.0 < crossfeed_peak_db(preset, rate) < 6.0


def test_manual_gain_is_effective_with_limiter_and_cuts_remain_effective():
    bands = [EQBand('peaking', 1000, 6, 1)]
    base = automatic_trim_db(bands, 48000)
    assert automatic_trim_db(bands, 48000, -6) == pytest.approx(base)
    assert automatic_trim_db(bands, 48000, 6, limiter_enabled=True) == pytest.approx(base)
    assert automatic_trim_db(bands, 48000, 6) == pytest.approx(base - 6)


@pytest.mark.parametrize('rate', [44100, 48000, 96000])
def test_cinema_stereo_expansion_calibrates_the_actual_correlated_path(rate, tmp_path, monkeypatch):
    from eqspace.core.filterchain.spatial import LAYOUT_AZIMUTHS
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    paths = extract_speaker_irs(builtin_kemar_path(), rate, LAYOUT_AZIMUTHS['7.1'])
    peak = spatial_peak_db(paths, '7.1', rate, stereo_input=True)
    refs = spatial_reference_peaks_db(paths, '7.1', rate, stereo_input=True)
    assert refs['correlated_sweep'] <= peak + 1e-6
    assert peak < 6
