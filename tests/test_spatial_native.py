"""Optional offline proof against the actual PipeWire 1.0.5 native DSP sources.

Set EQSPACE_PIPEWIRE_SOURCE to a pristine extracted 1.0.5 source tree.
No daemon, sound device, Python realtime audio, or hardware is involved.
"""
import ctypes
import os
from pathlib import Path
import subprocess

import numpy as np
import pytest


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    source = os.environ.get('EQSPACE_PIPEWIRE_SOURCE')
    if not source:
        pytest.skip('set EQSPACE_PIPEWIRE_SOURCE for native upstream DSP proof')
    source = Path(source)
    core = source/'src/modules/module-filter-chain'
    work = tmp_path_factory.mktemp('native')
    # Compile upstream convolver/FFT/biquad/mixer implementations directly.
    # Extract delay_run verbatim so its exact float32 truncation is exercised.
    builtin = (core/'builtin_plugin.c').read_text()
    start = builtin.index('static void delay_run(')
    stop = builtin.index('static struct fc_port delay_ports', start)
    delay = builtin[start:stop]
    harness = '''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "convolver.h"
#include "biquad.h"
#include <spa/utils/defs.h>
struct delay_impl { unsigned long rate; float *port[4]; float delay;
uint32_t delay_samples, buffer_samples, ptr; float *buffer; };
''' + delay + '''
void native_mix(float *left, float *right, float *out, int count, float a, float b) {
 struct dsp_ops ops={0}; dsp_ops_init(&ops);
 const void *sources[2]={left,right};float gains[2]={a,b};
 dsp_ops_mix_gain(&ops,out,sources,gains,2,count);
}
void native_convolve(float *ir, int length, float *in, float *out, int count, int chunk) {
 struct dsp_ops ops = {0}; dsp_ops_init(&ops);
 struct convolver *conv = convolver_new(&ops, SPA_CLAMP(length,64,256), 4096, ir, length);
 for(int offset=0; offset<count; offset+=chunk) {
  int n = count-offset < chunk ? count-offset : chunk;
  convolver_run(conv, in+offset, out+offset, n);
 }
 convolver_free(conv);
}
void native_meier(int rate, float cutoff, float seconds, float *in, float *out, int count, int cross, int chunk) {
 struct dsp_ops ops={0}; dsp_ops_init(&ops);
 float *filtered=calloc(count,sizeof(float));
 if(cross) { struct biquad bq={0}; biquad_set(&bq,BQ_LOWPASS,cutoff/(rate*.5),.5,0);
 dsp_ops_biquad_run(&ops,&bq,filtered,in,count); }
 else memcpy(filtered,in,count*sizeof(float));
 struct delay_impl d={0}; d.rate=rate; d.buffer_samples=rate/100;
 d.buffer=calloc(d.buffer_samples,sizeof(float)); d.port[2]=&seconds;
 for(int offset=0;offset<count;offset+=chunk) {
  int n=count-offset<chunk ? count-offset : chunk;
  d.port[0]=out+offset; d.port[1]=filtered+offset; delay_run(&d,n);
 }
 free(filtered);free(d.buffer);
}
'''
    (work/'harness.c').write_text(harness)
    output = work/'native.so'
    subprocess.run(['cc','-shared','-fPIC','-O2','-I'+str(core),'-I'+str(source/'spa/include'),
                    str(work/'harness.c'), *[str(core/name) for name in
                    ('convolver.c','dsp-ops.c','dsp-ops-c.c','pffft.c','biquad.c')],
                    '-lm','-o',str(output)], check=True, capture_output=True)
    lib=ctypes.CDLL(str(output))
    array = np.ctypeslib.ndpointer(dtype=np.float32,ndim=1,flags='C_CONTIGUOUS')
    lib.native_mix.argtypes=[array,array,array,ctypes.c_int,ctypes.c_float,ctypes.c_float]
    lib.native_convolve.argtypes=[array,ctypes.c_int,array,array,ctypes.c_int,ctypes.c_int]
    lib.native_meier.argtypes=[ctypes.c_int,ctypes.c_float,ctypes.c_float,array,array,ctypes.c_int,ctypes.c_int,ctypes.c_int]
    return lib


@pytest.mark.parametrize('fs',[44100,48000,96000])
@pytest.mark.parametrize('chunk',[32,64,127,256])
@pytest.mark.parametrize('cutoff',[650,1400])
def test_upstream_native_convolver_and_whole_meier_delay(native, fs, chunk, cutoff):
    from eqspace.core.dsp.hrtf import generate_holospace_irs
    from eqspace.core.dsp.spatial import native_meier_lowpass
    from scipy.signal import lfilter
    inputs=np.zeros(4096,dtype=np.float32);inputs[0]=1
    for pair in generate_holospace_irs([-30,0,30,-100,100],fs).values():
        for ir in pair:
            ir=ir.astype(np.float32)
            output=np.zeros_like(inputs)
            native.native_convolve(ir,len(ir),inputs,output,len(inputs),chunk)
            np.testing.assert_allclose(output[:len(ir)],ir,atol=1.1e-6)
            assert np.max(np.abs(output[len(ir):])) < 1.1e-6
    for cross in (0,1):
        output=np.zeros_like(inputs)
        native.native_meier(fs,cutoff,(10.25/fs),inputs,output,len(inputs),cross,chunk)
        reference=np.roll(inputs,10)
        if cross:
            coeff=native_meier_lowpass(fs,cutoff)
            reference=lfilter([coeff.b0,coeff.b1,coeff.b2],[1,coeff.a1,coeff.a2],reference)
        np.testing.assert_allclose(output,reference,atol=2e-6)
        assert np.all(output[:10]==0)
        assert output[10]>0


@pytest.mark.parametrize('fs',[44100,48000,96000])
def test_full_native_hybrid_matrix(native, tmp_path, fs):
    from eqspace.core.dsp.spatial import hybrid_transfers
    from eqspace.core.filterchain.spatial import LAYOUT_CHANNEL_SPEAKER_MAP
    from eqspace.core.dsp.hrtf import generate_holospace_irs
    from scipy.io import wavfile
    irs=generate_holospace_irs([-30,0,30,-100,100],fs)
    paths={}
    for az,pair in irs.items():
        paths[az]=tuple(tmp_path/f'{az}_{ear}.wav' for ear in range(2))
        for path,ir in zip(paths[az],pair):
            wavfile.write(path,fs,ir.astype(np.float32))
    reference=hybrid_transfers(paths,fs,level=.83)
    count=reference.shape[-1]
    native_matrix=np.zeros_like(reference)
    for source,channel in enumerate(('FL','FR')):
        impulse=np.zeros(count,dtype=np.float32);impulse[0]=1
        holo=np.zeros((2,count),dtype=np.float32)
        for speaker,az in LAYOUT_CHANNEL_SPEAKER_MAP['HoloSpace 3D']:
            weight=.5 if speaker=='FC' else float((speaker in ('FL','SL')) == (channel=='FL'))
            if not weight:continue
            for ear,ir in enumerate(irs[az]):
                response=np.zeros(count,dtype=np.float32)
                native.native_convolve(ir.astype(np.float32),len(ir),impulse,response,count,127)
                holo[ear]+=response*(weight/5)
        for ear in range(2):
            meier=np.zeros(count,dtype=np.float32)
            native.native_meier(fs,1400,10.25/fs,impulse,meier,count,int(ear!=source),127)
            if ear!=source:meier*=10**(-4.5/20)
            output=np.zeros(count,dtype=np.float32)
            native.native_mix(holo[ear],meier,output,count,.83*.6,.83*.4)
            native_matrix[ear,source]=output
    np.testing.assert_allclose(native_matrix,reference,atol=2e-6)
