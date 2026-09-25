# Task 8 Report — GUI: presets, spatial, mic tabs + wiring

## Status: COMPLETE

## What was built

### Presets tab — `src/eqspace/ui/presets/presets_widget.py`
- QListWidget of presets from the injected `list_presets`/`load_preset`
  callables (defaults to `core/profiles/presets.py`), with description and
  research-basis labels updating on selection.
- **Preview** emits `preset_previewed(EQProfile)`; the main window applies
  it to the PEQ band model, pushes to the filter-chain manager, and jumps
  to the PEQ tab.
- **Save as profile** opens `QInputDialog` for the name and persists via
  `storage.save_profile`.
- **Import / Export** buttons via `QFileDialog` + `storage.import_profile`
  / `storage.export_profile`.

### Spatial tab — `src/eqspace/ui/spatial/spatial_widget.py`
- SOFA picker scans the user HRTF dir `$XDG_DATA_HOME/eqspace/hrtf`
  (default `~/.local/share/eqspace/hrtf`; documented in the module
  docstring — drop `.sofa` files there, e.g. from sofa.acn… / KEMAR /
  Listen).
- Layout selector (Stereo / 5.1 / 7.1 → `audio.position` channel sets),
  wet/dry slider (mapped to chain gain 0.05–1.0), crossfeed toggle
  (Stereo + crossfeed renders a near-field ±30° pair, bs2b-style).
- Renders via `SpatialChainRenderer.render_args` after
  `extract_speaker_irs`; loads via `ModuleArgsManager`.
- Graceful states: "HRTF unavailable — install pysofa" and "No SOFA files
  found …" (both disable Apply).

### Mic tab — `src/eqspace/ui/mic/mic_widget.py`
- NR enable checkbox, strength slider (→ `Strength` 0–1), Apply/Unload,
  input-monitor placeholder label, "Mic PEQ…" shortcut button (switches to
  the PEQ tab), and a status line driven by the injectable
  `deepfilternet_available()` callable (Apply disabled when missing).
- Renders via `MicChainRenderer.render_args` with an injectable
  `plugin_path`; loads/unloads via `ModuleArgsManager`.

### New shared helper — `src/eqspace/ui/module_args_manager.py`
- `ModuleArgsManager(FilterChainManager)` adds `load_args()` / `update_args()`
  that load a pre-rendered single-line SPA args string through the same
  persistent `pw-cli` machinery (reuses core's `_popen`/`_read_*`/`_kill`).
  UI-layer only; **core/ untouched**.

### Main window — `src/eqspace/ui/main_window.py`
- Real Presets/Spatial/Mic tabs replace the placeholders; PEQ
  `save_profile_requested` wired to a name dialog + `storage.save_profile`
  + `save_last_profile`; preset preview wiring as above; mic PEQ shortcut.
- **Startup profile restore**: `src/eqspace/ui/profile_state.py` stores
  `{"last_profile": name}` at `$XDG_CONFIG_HOME/eqspace/state.json`
  (same conventions as `storage.py`: XDG config dir, atomic rename via
  `tempfile.mkstemp`). `MainWindow(restore_profile=True)` applies the last
  profile on launch and tolerates missing/corrupt state or a deleted
  profile (logs, continues).

## Tests (all headless, `QT_QPA_PLATFORM=offscreen`)
- `tests/test_ui_presets.py` — fake preset source: list population,
  description/research display, preview signal, save-as-profile persists.
- `tests/test_ui_spatial.py` — construction, SOFA listing, both graceful
  states, apply renders 5.1 args and loads via fake manager, crossfeed
  azimuth restriction, unload. Uses a synthetic `IRExtractor` (unit
  impulses) monkeypatched over `PySofaExtractor` — no pysofa needed.
- `tests/test_ui_mic.py` — `deepfilternet_available` monkeypatched both
  ways, strength lands in args, no-op when NR disabled, unload.
- `tests/test_ui_profile_restore.py` — restore applies saved profile to PEQ
  + manager, tolerates missing profile and missing state file, PEQ
  save-as persists profile and updates state.

## Results
- Full suite: **221 passed** (was 203 before Task 8; +18 new tests).

## Commit
- Single commit: `9e1a2f3` (ui/, tests/, report only — core/ unchanged).

## Concerns / follow-ups
- Crossfeed is a documented approximation (near-field ±30° pair), not a
  true interaural-time-delay crossfeed — a real crossfeed would need a
  core renderer change (out of scope).
- `ModuleArgsManager` reuses core's underscore-prefixed pw-cli helpers; if
  Task 9+ needs this in core, promote `load_args` into
  `FilterChainManager` proper.
- HRTF extraction runs synchronously on Apply; large SOFA files may stall
  the UI (cache makes repeat applies fast).
- Tray icon skipped per brief ("optional if trivial").
