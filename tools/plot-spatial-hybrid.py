#!/usr/bin/env python3
"""Export static design-response figures; no playback or sound-device access."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy as np
import pyqtgraph as pg
from pyqtgraph.exporters import ImageExporter
from PySide6.QtWidgets import QApplication
from eqspace.core.dsp.hrtf import extract_speaker_irs, holospace_default_path
from eqspace.core.dsp.spatial import hybrid_branch_transfers, hybrid_transfers
from eqspace.core.profiles.presets import load_preset
from eqspace.core.dsp.biquads import magnitude_response
from eqspace.core.dsp.filter_design import design_filters

app = QApplication.instance() or QApplication([])
pg.setConfigOptions(background='w', foreground='k', antialias=True)
rate = 48000
paths = extract_speaker_irs(holospace_default_path(), rate, azimuths_deg=[-100,-30,0,30,100])
frequencies = np.fft.rfftfreq(65536, 1/rate)
mask = (frequencies >= 20) & (frequencies <= 20000)
freq = frequencies[mask]
plot = pg.PlotWidget(title='Spatial design response · correlated stereo input · 48 kHz')
plot.resize(1000, 540)
plot.setLabel('bottom', 'Frequency', units='Hz')
plot.setLabel('left', 'Gain', units='dB')
plot.setLogMode(x=True)
axis = plot.getAxis('bottom')
axis.enableAutoSIPrefix(False)
axis.setTicks([[(np.log10(value), str(value)) for value in
                (20,50,100,200,500,1000,2000,5000,10000,20000)]])
plot.setXRange(np.log10(20),np.log10(20000),padding=.02)
plot.showGrid(x=True,y=True,alpha=.2)
plot.addLegend(offset=(-20, 15))
holo, meier = hybrid_branch_transfers(paths, rate)
for matrix, color, name in ((holo,'#375aaf','HoloSpace'),
                            (meier,'#bb6030','Aligned Meier branch · 1400 Hz'),
                            (hybrid_transfers(paths, rate),'#228057','60% HoloSpace / 40% Meier')):
    transfer = np.fft.rfft(matrix,n=65536,axis=-1)
    gain = 20*np.log10(np.maximum(np.abs(transfer[0,0]+transfer[0,1]),1e-12))[mask]
    plot.plot(freq,gain,pen=pg.mkPen(color,width=2),name=name)
plot.setYRange(-25,8)
plot.show();app.processEvents()
directory = Path(__file__).resolve().parents[1]/'docs/assets'
directory.mkdir(exist_ok=True)
ImageExporter(plot.plotItem).export(str(directory/'spatial-hybrid-response.png'))
plot.clear()
plot.setTitle('Holo Punch — Experimental · editor EQ response estimate · 48 kHz')
gain=magnitude_response(design_filters(load_preset('holo_punch_experimental').to_bands(),rate),freq,rate)
plot.plot(freq,gain,pen=pg.mkPen('#784fb4',width=2),name='Manual preamp 0 dB · before automatic trim')
plot.setYRange(-4,8)
app.processEvents()
ImageExporter(plot.plotItem).export(str(directory/'holo-punch-response.png'))
print(directory)
