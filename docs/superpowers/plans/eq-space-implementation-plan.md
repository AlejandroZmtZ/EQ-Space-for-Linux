# EQ-Space for Linux — Implementation Plan

Greenfield Python 3.12 + PySide6 application in `/home/alex/EQ-Space for Linux`.
Virtualenv at `.venv/` (use `.venv/bin/python` and `.venv/bin/pytest`). Git repo on `main`, commit after each task.

## Global constraints

- Python never processes real-time audio; all RT DSP runs in PipeWire `libpipewire-module-filter-chain`. Python designs filters, renders filter-chain configs, and orchestrates PipeWire via `pw-dump` (JSON), `wpctl`, `pw-cli` subprocesses.
- Package layout: `src/eqspace/...`, entry point `eqspace`. Tests in `tests/`, pytest, TDD where the task says so.
- Code style: type hints, no dead code, no speculative features. Match surrounding conventions.
- UI tests run headless with `QT_QPA_PLATFORM=offscreen`.
- Do not add dependencies beyond: PySide6, pyqtgraph, numpy, scipy, pydantic, pytest (already in venv). pysofa is optional/guarded.

## Task 1 — Project scaffold

Create `pyproject.toml` (setuptools, src layout, project name `eqspace`, entry point `eqspace = eqspace.app:main`), `README.md`, `.gitignore` (venv, __pycache__, .superpowers), package skeleton `src/eqspace/__init__.py`, `src/eqspace/__main__.py`, `src/eqspace/app.py` with `main()` that builds a QApplication and shows an empty placeholder `MainWindow` (from `src/eqspace/ui/main_window.py`, titled "EQ-Space"). `src/eqspace/ui/__init__.py` and other subpackage `__init__.py` files as needed (`core/`, `core/pipewire/`, `core/dsp/`, `core/filterchain/`, `core/profiles/`, `data/`). Add `tests/test_smoke.py` verifying the app module imports and MainWindow constructs offscreen. Run pytest green, commit.

## Task 2 — DSP core: biquads and filter design (TDD)

`src/eqspace/core/dsp/biquads.py`: functions `peaking(f0_hz, gain_db, q, fs)`, `low_shelf(f0_hz, gain_db, q_or_s, fs)`, `high_shelf(...)`, `low_pass(f0_hz, q, fs)`, `high_pass(f0_hz, q, fs)`, `notch(f0_hz, q, fs)` returning normalized coefficient dataclass `BiquadCoeffs(b0,b1,b2,a0=1,a1,a2)` using RBJ Audio EQ Cookbook formulas. `magnitude_response(coeffs_list, freqs, fs)` computing combined dB response of a cascade. Tests in `tests/test_biquads.py`: compare coefficients and magnitude response against `scipy.signal` equivalents (iirpeak/iirnotch where applicable; for peaking/shelf compare frequency response sampled at several frequencies, tolerance 0.1 dB / 1e-3 coeff).

`src/eqspace/core/dsp/filter_design.py`: dataclass `EQBand(band_type, freq_hz, gain_db, q, enabled)`; `design_filters(bands, fs)` → list[BiquadCoeffs]; `fit_to_target(freqs, delta_db, n_filters)` least-squares fit of peaking filters to a target delta curve (used for Harman presets). Tests: round-trip band→coeffs→response sanity (response at band center ≈ gain within 0.5 dB); fit reduces RMS error vs zero.

## Task 3 — Target curves and preset fitting (TDD)

`src/eqspace/core/dsp/target_curves.py`: Harman over-ear 2018 and in-ear 2019 target curves as interpolated tables (frequency, dB) with sources documented in module docstring; `iso226_loudness_compensation(spl phon)` simplified equal-loudness contour deltas (ISO 226:2003 shape: bass and upper-treble boost at low levels); `evaluate(curve, freqs)` interpolation helper. Tests: monotonic frequency grids, sane dB ranges, interpolation correctness at known anchor points (e.g. Harman IE ~+5-8 dB bass shelf region, ~10 kHz treble feature sign).

## Task 4 — PipeWire backend

`src/eqspace/core/pipewire/registry.py`: `PipeWireRegistry` — snapshot via `pw-dump` (subprocess, parse JSON) into dataclasses `PwNode(id, name, app_name, media_class, volume, mute)` for sinks/sources/streams; `monitor(callback)` polling thread (500 ms) emitting change callbacks; graceful degradation with clear `PipeWireUnavailable` error when pipewire absent.

`src/eqspace/core/pipewire/control.py`: `set_volume(node_id, vol)`, `set_mute(node_id, mute)`, `set_default_sink(name)`, `move_stream(stream_id, sink_id)` using `wpctl`/`pw-cli` subprocesses with timeouts and error reporting.

`src/eqspace/core/filterchain/manager.py`: `FilterChainManager` — render a filter-chain config (SPA-like syntax, template string) for a given node name/description and list of filter nodes; `load()` via `pw-cli load-module libpipewire-module-filter-chain <conf-path>`... (use `context.object` param via pw-cli or write conf and use `pipewire -c`? — implement load/unload through `pw-cli load-module` / `pw-cli destroy`, fallback to pactl module loading if simpler; document choice), `unload()`, `set_filter_param(node_name, param, value)` via `pw-cli set-param Props`, with a `live_update_supported` probe.

Unit-test the pure parts (config rendering, pw-dump parsing with fixture JSON files in `tests/fixtures/`); mark live-hardware functions as untestable in CI with a comment. Commit.

## Task 5 — Profiles and presets

`src/eqspace/core/profiles/models.py`: pydantic models `EQProfile` (name, version, bands: list of EQBand-serializable dicts, spatial settings dict, mic settings dict, output device binding, volume) with schema versioning.

`src/eqspace/core/profiles/storage.py`: save/load/list/delete JSON profiles under `~/.config/eqspace/profiles/` (respect `$XDG_CONFIG_HOME`), atomic writes, import/export.

`src/eqspace/data/presets/` JSON preset library built from Task 3 target curves + fitting: `harman_over_ear_2018.json`, `harman_in_ear_2019.json`, `bass_boost.json`, `vocal_clarity.json`, `podcast.json`, `gaming_footsteps.json`, `late_night.json`, `loudness_low_listening.json`, `crossfeed_bauer.json` — each with name, description, research basis note, band list. A small generator script `tools/generate_presets.py` regenerates them. `src/eqspace/core/profiles/presets.py`: load built-in presets, convert preset → EQProfile. Tests: profile round-trip, preset validity (all bands in range), storage CRUD with tmp XDG dir.

## Task 6 — Spatial (HRTF) and mic NR backends

`src/eqspace/core/dsp/hrtf.py`: given a SOFA file path, extract stereo IR pairs for standard virtual speaker positions (±30°, ±90°, ±110°, 0°) — use pysofa if importable, else raise a clear `HRTFUnavailable`; resample IRs to session rate; cache extracted IRs as .npy under `~/.cache/eqspace/hrtf/`. `src/eqspace/core/filterchain/spatial.py`: render filter-chain convolver stage config from IR file paths (builtin convolver). `src/eqspace/core/filterchain/mic.py`: render input-side filter-chain with DeepFilterNet LADSPA (`libdf_ladspa.so`, configurable path + attenuation/strength controls) producing virtual source "EQ-Space Mic"; detection helper `deepfilternet_available()` searching standard LADSPA paths. Unit-test config rendering and IR cache logic with a synthetic in-memory HRTF (no real SOFA needed: generate delta IRs via a tiny fake extractor interface). Commit.

## Task 7 — GUI: main window, mixer, parametric EQ

`src/eqspace/ui/main_window.py`: QMainWindow with tabs: Mixer | Parametric EQ | Presets | Spatial | Mic; dark theme via stylesheet.

Mixer tab (`ui/mixer/`): live per-app stream list (QSlider + mute per stream, app name/icon, target sink selector), master volume, device selector; wired to Task 4 registry/control via Qt signals (QTimer-driven refresh).

Parametric EQ tab (`ui/peq/`): pyqtgraph log-frequency canvas 20 Hz–20 kHz, ±24 dB; draggable band handles (freq/gain, wheel = Q); combined response curve from Task 2 `magnitude_response`; band table editor (type combo, freq, gain, Q, enable); add/remove band (max 16); Apply → filterchain manager; Save as profile.

Headless smoke tests (offscreen): each tab constructs, band add/remove updates model, mixer populates from a fake registry (dependency-inject registry). Commit.

## Task 8 — GUI: presets, spatial, mic tabs + wiring

Presets tab: list built-in presets with description + research note, Preview (apply to EQ engine), Save as profile, profile import/export buttons.

Spatial tab: HRTF profile picker (available SOFA files), virtual layout selector (stereo/5.1/7.1), wet/dry slider, crossfeed toggle; renders spatial filter-chain via Task 6.

Mic tab: NR enable + strength, mic PEQ shortcut, input monitor placeholder, `deepfilternet_available()` status indicator.

Wire tabs into main window; app startup restores last active profile; tray icon optional if trivial, otherwise skip. Offscreen smoke tests. Commit.

## Task 9 — Integration, docs, final wiring

`__main__` CLI: `eqspace` (GUI), `eqspace --apply-profile NAME` (headless), `eqspace --list-profiles`. README: features, architecture diagram, install/run instructions, Ubuntu deps (`pipewire`, DeepFilterNet LADSPA install note, SOFA download note). Ensure full pytest suite green offscreen. Commit.
