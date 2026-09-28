"""Native hybrid graph contracts and offline matrix reference checks."""
import re

import numpy as np
import pytest
from scipy.io import wavfile
from scipy.signal import freqz

from eqspace.core.dsp.hrtf import generate_holospace_irs


@pytest.fixture
def ir_paths(tmp_path):
    def make(fs):
        paths = {}
        for az, pair in generate_holospace_irs((-30, 30, 0, -100, 100), fs).items():
            files = tuple(tmp_path / f'{fs}_{az}_{ear}.wav' for ear in 'LR')
            for path, samples in zip(files, pair):
                wavfile.write(path, int(fs), samples.astype(np.float32))
            paths[az] = files
        return paths
    return make


@pytest.mark.parametrize('fs', [44100, 48000, 96000])
def test_parallel_native_graph_and_common_delay(ir_paths, fs):
    from eqspace.core.filterchain.spatial import render_hybrid_chain_args, hybrid_controls
    args = render_hybrid_chain_args(ir_paths(fs), fs)
    assert '\n' not in args
    assert args.count('label = convolver') == 10
    assert args.count('label = bq_lowpass') == 2
    assert args.count('label = delay') == 2
    assert 'node.autoconnect = false' in args
    assert 'ramp' not in args
    assert 'Freq" = 1400' in args and 'Q" = 0.5' in args
    seconds = float(re.search(r'"Delay \(s\)" = ([0-9.e+-]+)', args)[1])
    assert int(np.float32(seconds) * fs) == 10
    assert hybrid_controls() == {'hybrid_l:Gain 1': .6, 'hybrid_l:Gain 2': .4,
                                 'hybrid_r:Gain 1': .6, 'hybrid_r:Gain 2': .4}
    assert hybrid_controls(0) == {key: 0 for key in hybrid_controls()}
    from eqspace.core.dsp.crossfeed import render_crossfeed_chain_args
    assert 'Freq" = 650' in render_crossfeed_chain_args('meier', fs)


@pytest.mark.parametrize('fs', [44100, 48000, 96000])
def test_fixed_complex_matrix_matches_branches_and_conservative_headroom(ir_paths, fs):
    from eqspace.core.dsp.spatial import hybrid_branch_transfers, hybrid_transfers, hybrid_peak_db, hybrid_reference_peaks_db
    paths = ir_paths(fs)
    h, m = hybrid_branch_transfers(paths, fs)
    combined = hybrid_transfers(paths, fs)
    np.testing.assert_allclose(combined, .6*h+.4*m, atol=1e-12)
    assert np.all(combined[:, :, :10] == 0)
    assert m[0, 0, 10] == 1
    assert m[1, 1, 10] == 1
    assert m[0, 1, 10] > 0  # lowpass begins at same common offset, no300us extra
    responses = np.fft.rfft(combined, n=32768, axis=-1)
    row_bound = np.max(np.sum(np.abs(responses), axis=1))
    peak = hybrid_peak_db(paths, fs)
    assert 10**(peak/20) >= row_bound * (1-1e-8)
    assert peak >= max(hybrid_reference_peaks_db(paths, fs).values()) - 1e-8
    silent = hybrid_transfers(paths, fs, level=0)
    assert not silent.any()


def test_hybrid_input_validation(ir_paths):
    from eqspace.core.filterchain.spatial import render_hybrid_chain_args
    for kwargs in ({'level': float('nan')}, {'level': -1}, {'fs': 0}, {'fs': 2800}):
        with pytest.raises(ValueError):
            render_hybrid_chain_args(ir_paths(48000), **({'fs': 48000} | kwargs))


def test_legacy_hybrid_settings_cannot_change_graph_or_headroom(ir_paths, monkeypatch):
    from eqspace.core.dsp import spatial
    from eqspace.core.filterchain.spatial import HYBRID_DESCRIPTION
    monkeypatch.setattr(spatial, 'extract_speaker_irs', lambda *a, **k: ir_paths(48000))
    state = {'profile': HYBRID_DESCRIPTION, 'wet': 75}
    baseline = spatial.prepare_spatial(state, 48000)
    legacy = spatial.prepare_spatial(dict(state, hybrid_balance=100, crossover_hz=650, crossover=2500), 48000)
    assert legacy == baseline
    assert 'Freq" = 1400' in legacy[0]
    assert '"Gain 1" = 0.45 "Gain 2" = 0.3' in legacy[0]
