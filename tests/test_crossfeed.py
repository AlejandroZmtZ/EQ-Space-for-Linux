"""Tests for binaural crossfeed DSP filter design."""

import numpy as np
import pytest

from eqspace.core.dsp.crossfeed import (
    CROSSFEED_PRESETS,
    CrossfeedConfig,
    bauer_crossfeed_coefficients,
    crossfeed_delay_samples,
    render_crossfeed_chain_args,
)


class TestCrossfeedDSP:
    def test_presets_exist(self):
        assert "bauer" in CROSSFEED_PRESETS
        assert "meier" in CROSSFEED_PRESETS
        assert "strong" in CROSSFEED_PRESETS

        bauer = CROSSFEED_PRESETS["bauer"]
        assert 600.0 <= bauer.f0_hz <= 800.0
        assert bauer.feed_db < 0.0
        assert 200.0 <= bauer.delay_us <= 400.0

    def test_crossfeed_delay_samples(self):
        samples_48k = crossfeed_delay_samples(300.0, fs=48000.0)
        # 300 µs at 48 kHz is ~14.4 -> 14 samples
        assert samples_48k == 14

        samples_44k = crossfeed_delay_samples(300.0, fs=44100.0)
        assert samples_44k == 13

    def test_bauer_crossfeed_coefficients(self):
        direct, cross = bauer_crossfeed_coefficients(f0_hz=700.0, feed_db=-4.5, fs=48000.0)

        # Check normalization (a0 == 1)
        assert direct.a0 == 1.0
        assert cross.a0 == 1.0

        # Crossfeed is a low-pass filter: high frequencies must be strongly attenuated compared to DC
        # At DC (z=1): H(1) = (b0+b1+b2)/(a0+a1+a2)
        h_cross_dc = (cross.b0 + cross.b1 + cross.b2) / (cross.a0 + cross.a1 + cross.a2)
        # At Nyquist (z=-1): H(-1) = (b0-b1+b2)/(a0-a1+a2)
        h_cross_nyq = (cross.b0 - cross.b1 + cross.b2) / (cross.a0 - cross.a1 + cross.a2)

        assert abs(h_cross_dc) > abs(h_cross_nyq)
        assert abs(h_cross_nyq) < 0.1  # Significant high-frequency attenuation

    def test_render_crossfeed_chain_args(self):
        args = render_crossfeed_chain_args(preset="bauer", fs=48000.0)
        assert "media.class = Audio/Sink" in args
        assert "node.name" in args
        assert "filter.graph" in args
        assert "bq_lowpass" in args
        assert '"Gain 2"' in args
        # bq_lowpass must not have Gain control (ignored by PipeWire)
        assert 'label = bq_lowpass name = "lp_l2r" control = { "Freq" = 700 "Q" = 0.5 }' in args
        # Linear gain for -6.0 dB is ~0.501187
        bauer_gain = 10.0 ** (-6.0 / 20.0)
        assert f'"Gain 2" = {bauer_gain:g}' in args
        assert "'" not in args  # Single-line SPA requirement

        # Check meier preset
        args_meier = render_crossfeed_chain_args(preset="meier", fs=48000.0)
        assert 'label = bq_lowpass name = "lp_l2r" control = { "Freq" = 650 "Q" = 0.5 }' in args_meier
        meier_gain = 10.0 ** (-4.5 / 20.0)
        assert f'"Gain 2" = {meier_gain:g}' in args_meier


@pytest.mark.parametrize('preset', ['bauer', 'meier', 'strong'])
def test_crossfeed_playback_waits_for_controller_verified_linking(preset):
    """Staging a module must not connect its output to an arbitrary default."""
    import re
    args = render_crossfeed_chain_args(preset=preset, node_name='eqspace.crossfeed.staged')
    playback = re.search(r'playback.props = \{ ([^}]+) \}', args)[1]
    assert 'node.name = "eqspace.crossfeed.staged.playback"' in playback
    assert 'node.passive = true' in playback
    assert 'node.autoconnect = false' in playback
    assert 'audio.position = [ FL FR ]' in playback
