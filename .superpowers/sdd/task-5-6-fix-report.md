# Task 5/6 code-review fix report (2026-09-24)

## Findings → changes

1. **Preset.volume unbounded** (`src/eqspace/core/profiles/presets.py`)
   - `Preset.volume` now `Field(default=1.0, ge=0.0, le=2.0)`, matching `EQProfile`.
   - `load_preset(name)` now rejects empty names and names containing `/`, `\`, or `..` (ValueError), mirroring `storage._profile_path` sanitization.
   - Tests: `test_volume_bounds_enforced`, parametrized `test_load_rejects_unsafe_names` in `tests/test_presets.py`.

2. **`export_profile` not atomic** (`src/eqspace/core/profiles/storage.py`)
   - Switched to tempfile-in-target-dir + `os.replace`, same pattern as `save_profile` (cleans up temp file on failure).
   - Test: `test_export_is_atomic_no_temp_left` in `tests/test_profiles.py`; existing round-trip test still passes.

3. **Misleading `LADSPA_PLUGIN_LABEL`** (`src/eqspace/core/filterchain/mic.py`)
   - Renamed to `LADSPA_NODE_TYPE = "ladspa"` (it is the node *type*); added `DEEPFILTERNET_LABEL = "deep_filter_ladspa"` for the previously hardcoded plugin label. Both used in rendering.
   - Tests updated/added in `tests/test_mic.py` (constants, node presence).

4. **Duplicated `_azimuth_tag`** (`src/eqspace/core/filterchain/spatial.py`, `src/eqspace/core/dsp/hrtf.py`)
   - Single definition kept in `hrtf.py`; `spatial.py` imports it (`from ..dsp.hrtf import _azimuth_tag`). No circular import (hrtf depends only on numpy).

5. **No single-line args form for mic/spatial renderers**
   - `MicChainRenderer.render_args()` + module-level `render_mic_args()` added (`mic.py`); shared node-building via `_df_node` (file form) / `_df_node_inline` (single-line form).
   - `SpatialChainRenderer.render_args()` added (`spatial.py`), producing the same convolver/mixer nodes and capture/playback props as `render_config` in the single-line SPA properties form documented in `manager.py` (`pw-cli load-module` / `pw_properties_new_string`).
   - Tests: `test_render_args_single_line`, `test_render_args_matches_config_nodes`, validation and wrapper tests in `tests/test_mic.py` and `tests/test_spatial.py` — assert no newlines and presence of the key nodes/controls from the file form.

## Test output

```
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q
........................................................................ [ 35%]
........................................................................ [ 71%]
..........................................................               [100%]
202 passed in 3.15s
```

Full suite green; no new dependencies; only the profiles/filterchain/dsp files and their tests were touched.
