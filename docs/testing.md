# Testing and support limits

## Automated checks

```bash
.venv/bin/pip install -e '.[dev]'
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ -q
.venv/bin/pip wheel . --no-deps --wheel-dir dist
python3 tools/check-public-tree.py
```

The suite covers filter math, profile migration and storage, import/export validation, stage combinations, control readback, failed handoffs and rollback, latest-value updates, worker lifetime, offscreen widgets and packaged resources. Most routing checks use fake backends so the suite does not alter a desktop audio session.

The optional native DSP harness requires a C compiler and an extracted pristine PipeWire 1.0.5 source tree:

```bash
EQSPACE_PIPEWIRE_SOURCE=/path/to/pipewire-1.0.5 \
  QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_spatial_native.py -q
```

It compares native kernels and the hybrid stereo response with offline estimates at 44.1, 48 and 96 kHz. It does not connect to a daemon or assess listening quality. Without the source tree, those optional tests are skipped.

## Desktop and package acceptance

A package needs testing on its target desktop, with a writable user profile directory and the actual PipeWire graph rate. Exercise all eight EQ/Spatial/LSP combinations, changes in enablement order, preset switching, continuous edits, physical-output changes, profile save/import/export and normal quit. Confirm both stereo links, active application routes and requested control readback. Test missing plugins and interrupted operations as well as successful operations.

Perform the checks from the installed package rather than relying on the development environment. Check the application menu, dock logo, tray, desktop scaling and persistence across upgrades. Use only one audio-controlling instance during a listening test.

Automated tests and successful GUI startup do not establish audible gaplessness, perceived fidelity, measured output true peak or complete package compatibility. Those require separate target-system observation and, for sound-quality claims, controlled listening or signal measurements.

## Current support boundaries

The source installation baseline is Ubuntu 24.04 with Python 3.12+ and a working PipeWire/WirePlumber session. CI uses offscreen Qt on Ubuntu 24.04. Spatial and Mic are experimental. Synthetic Spatial models are not measured headphone corrections. LSP and its matching LV2 host are optional. Capture activity metering and a live spectrum analyzer are not implemented.

A passing local run is not evidence that hosted CI ran successfully. See the repository's Actions page for the status of a specific public commit. No Snap Store certification or Ubuntu App Center listing is claimed.
