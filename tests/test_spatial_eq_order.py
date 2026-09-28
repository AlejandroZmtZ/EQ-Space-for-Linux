"""Verify the scalar stereo EQ / Spatial cascade ordering used by the product."""
import numpy as np
import pytest
from scipy.signal import fftconvolve, sosfilt

from eqspace.core.dsp.filter_design import design_filters
from eqspace.core.dsp.hrtf import generate_holospace_irs
from eqspace.core.dsp.spatial import hybrid_transfers
from eqspace.core.profiles.presets import load_preset
from scipy.io import wavfile


@pytest.mark.parametrize('rate',[44100,48000,96000])
def test_identical_stereo_punch_eq_commutes_with_hybrid_matrix(tmp_path,rate):
    paths={}
    for azimuth,pair in generate_holospace_irs((-100,-30,0,30,100),rate).items():
        files=tuple(tmp_path/f'{azimuth}-{ear}.wav' for ear in 'LR')
        for path,ir in zip(files,pair):
            wavfile.write(path,rate,ir.astype(np.float32))
        paths[azimuth]=files
    matrix=hybrid_transfers(paths,rate)
    coeffs=design_filters(load_preset('holo_punch_experimental').to_bands(),rate)
    sos=np.asarray([[c.b0,c.b1,c.b2,1,c.a1,c.a2] for c in coeffs])
    inputs=np.random.default_rng(42).normal(size=(2,2048))*.05
    total_length=inputs.shape[1]+matrix.shape[-1]-1
    for ear in range(2):
        spatial_first=sosfilt(sos,sum(fftconvolve(inputs[source],matrix[ear,source]) for source in range(2)))
        eq_first=sum(fftconvolve(sosfilt(sos,np.pad(inputs[source],(0,matrix.shape[-1]-1))),
                                matrix[ear,source])[:total_length] for source in range(2))
        np.testing.assert_allclose(spatial_first,eq_first,atol=2e-12,rtol=2e-10)
