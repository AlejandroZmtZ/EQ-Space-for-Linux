# EQ-Space architecture and runtime design

This document describes the current GUI-first prototype. The authoritative behavior is in `src/eqspace/`; use [engineering lessons](agent-lessons.md) for the development and validation workflow and [verification](verification.md) for dated observations. Python configures and monitors PipeWire. Audio samples are processed by PipeWire filter-chain modules, not by Python.

## Components and ownership

| Area | Main files | Responsibility |
| --- | --- | --- |
| Entry and window | `app.py`, `ui/main_window.py`, `ui/audio_graph.py`, `ui/async_worker.py` | Start the GUI or list saved profiles; own the selected physical sink and verified playback graph; coordinate Apply and worker completion. |
| EQ editor | `ui/peq/peq_widget.py`, `core/dsp/` | Hold editable bands and manual preamp; design a response preview; build PipeWire filter specs. |
| Presets and profiles | `ui/presets/`, `core/profiles/`, `ui/profile_state.py` | Load packaged presets; validate, save, import and export versioned JSON; remember the last saved profile for editor restoration. |
| PipeWire observation | `core/pipewire/registry.py` | Parse `pw-dump` snapshots, poll changes, and read `clock.rate` from `pw-metadata`. |
| PipeWire control | `core/pipewire/control.py`, `ui/mixer/` | Select the physical output; inspect links; move playback streams; set the default sink and per-stream volume or mute. |
| Filter-chain lifetime | `core/filterchain/manager.py` | Render single-line SPA properties; own a persistent interactive `pw-cli`; load or destroy its module; update and verify live controls. |
| Spatial and Mic | `ui/spatial/`, `ui/mic/`, `core/filterchain/{spatial,mic}.py` | Spatial renders HRTF or crossfeed modules for the shared playback graph. Mic remains an independent experimental capture path. |

The playback graph is:

```text
application playback channels
        │
        ▼
Spatial sink, when enabled (unique staged node)
        │
        ▼
EQ sink, when enabled (manual preamp + automatic trim + biquads)
  or native stereo headroom stage for boosted Spatial with EQ bypassed
        │
        ▼
optional LSP Limiter Stereo LV2 sink
        │
        ▼
selected physical output FL/FR
```

The direct path connects application streams to the selected physical output. `AudioGraphController`, owned by `MainWindow`, is the only UI owner of Spatial and EQ playback routing. It stores the selected physical sink separately from effect state. Spatial Off restores the EQ path if EQ is active. The Mixer EQ button changes EQ state while preserving Spatial. Spatial-only playback uses a verified native stereo gain stage when its estimated peak gain is positive, including after EQ bypass or limiter removal. This reserve remains independent of the EQ profile's automatic-headroom policy. The controller requires both output links and every observed application output port to match the requested path before reporting success. A default sink name alone is insufficient.

## Profile Apply lifecycle

1. `PresetsWidget` converts a packaged preset into an `EQProfile` and emits `preset_apply_requested`. Manual EQ Apply constructs a temporary profile from the editor. `MainWindow.apply_profile` serializes requests with `_profile_apply_busy` and the PEQ worker state. While Apply owns the transaction, external Spatial, limiter, EQ bypass, and output-selection actions are disabled and rejected by their handlers. The transaction can invoke its own internal Spatial Apply.
2. Apply requires a live PipeWire graph rate. `PipeWireRegistry.graph_rate(required=True)` reads `clock.rate`; its 48 kHz fallback is for preview only. `design_filters` rejects enabled bands with nonfinite values or a center frequency at or above Nyquist. Flat has no bands and applies a neutral preamp stage.
3. The editor builds a `linear` gain node (`Mult = 10 ** ((preamp_db + auto_trim_db) / 20)`, `Add = 0`) followed by enabled biquads. Manual preamp remains separate. New profiles and built-in presets enable static automatic headroom: a 4096-point EQ response estimate plus Spatial channel-isolated, correlated, impulse, and sweep reference peaks yields trim only for a positive estimated gain, with 1 dB margin. Negative manual preamp always remains an effective cut. Without LSP, positive manual gain is reserved in the static trim. With LSP enabled, trim reserves EQ and Spatial gain only, allowing manual preamp to drive the downstream limiter. Removing LSP restores conservative gain and verifies controls before disconnecting it. This is a peak estimate, not loudness normalization or guaranteed clipping protection.
4. If the loaded graph has the same node names, types and channel layout, `FilterChainManager.reload` collects changed controls into one `pw-cli set-param <node> Props` request. It resolves the current node ID and reads the node's `Props` from `pw-dump`. A command timeout may have applied the values: readback, rather than command exit alone, decides success. On mismatch it resolves the node and retries once. If the second readback fails, it writes the previous verified controls and verifies them. Failure to prove restoration is reported as unverified.
5. A changed EQ layout stages a new manager under a unique node name. The old EQ manager remains alive until controls, downstream links, and the application route are verified. Spatial switching first reserves the maximum of the old and new estimated gain, stages a uniquely named module, connects it to EQ or the native output stage, verifies the path, then releases surplus reserve and unloads the old Spatial owner. Gain callbacks use a deep copy of the applied profile and preserve the applied filter layout; unapplied editor bands and preamp values are never loaded by graph toggles. Re-enabling EQ verifies cached gain first. Failed profile rollback reconnects conservative previous controls, restores the previous limiter selection, and only then restores permitted manual drive. Obsolete gain owners are retired after verified handoff, and failed cleanup owners are retained for shutdown retry. Errors attempt the previous verified route or direct physical output. If rollback cannot be verified, the candidate module owner stays alive so a possibly active sink is not destroyed; shutdown can retry the direct handoff and retire retained owners only after verification.
6. After a successful load or update, `PeqWidget` verifies controls and the graph controller checks all route links. The Presets button can show **Active** only while the requested preset and EQ route are verified. A failed or bypassed route clears that indication.

`FilterChainManager` must keep its interactive `pw-cli` process alive: `load-module` creates a module owned by that process, so exiting it removes the virtual sink. Once the module ID is parsed, a daemon thread continuously drains the owner's stdout until that stream closes; `unload` joins the thread after stopping the process. PipeWire registry events printed to an unread pipe can fill it and block this module host, causing later one-shot `set-param` commands to time out. Its module arguments must be one line of SPA properties. The separately rendered `context.modules` configuration is for file based installation and cannot be passed as the live `load-module` argument. The startup prompt can precede registry events; do not assume it is the final output line.

## Data and UI boundaries

`EQProfile` is schema version 3. It stores `bands`, manual `preamp_db` (−24 to +12 dB, default 0), explicit or unspecified `spatial_enabled`, `automatic_headroom`, and `limiter_enabled`, plus optional Spatial and Mic settings. Loading v1/v2 preserves manual gain, leaves active Spatial unchanged on Apply, and disables automatic headroom. New profiles enable it. Saves and exports write v3 atomically under `$XDG_CONFIG_HOME/eqspace/profiles/` (default `~/.config/eqspace/profiles/`). `ui/profile_state.py` stores the last saved profile name; startup loads its editor values without applying audio.

Single-band changes update the existing table row and redraw the 512-point response, preserving the active editor widget and keyboard focus. Adding, removing or replacing a band list rebuilds rows. The preview uses the observed graph rate when available and 48 kHz otherwise; Apply requires the observed rate. It is a calculated biquad response, not a measurement of the current audio signal.

Cinema expands stereo across the virtual room by default; disabling its stereo expansion option selects native 7.1 input. Explicit saved input modes remain unchanged. Its peak estimator uses the same source mapping as the renderer; saved Spatial state stores the input mode. Routing preserves native speaker channel names instead of sending every non-FR channel to FL. The bundled KEMAR-style model is synthetic. External SOFA extraction needs optional `pysofa`. LSP Limiter Stereo is off by default; the control is disabled unless its LV2 descriptor exposes the required stereo ports, true-peak mode, and control symbols and a matching PipeWire LV2 host is available. See [LV2 hosting](lv2-host.md) for the user-local Ubuntu 24.04 helper. Activation verifies actual limiter control values before linking EQ to the staged limiter. EQ edits and packaged preset changes preserve the limiter; saved profiles apply their explicit setting. The Mixer banner names every enabled stage and shows Active only after verifying the full path, including EQ-bypassed Spatial or limiter routes. Its ceiling is −1 dBTP, with automatic level regulation and boost off. The UI shows the configured 5 ms lookahead as a lower bound because total latency depends on the installed plugin and oversampling. DeepFilterNet needs its LADSPA plugin and real capture routing. `PipeWireLevelMonitor` reads `wpctl get-volume`, which is a volume value rather than an audio activity meter.

## Failure and shutdown boundaries

The worker reports a short UI error and logs the full exception. Keep its Qt signal source alive until completion, especially during window close. The window defers closing while Apply is in progress. Normal quit switches the session default and every observed active playback link to the selected physical output, verifies that handoff, then unloads owned Spatial, limiter, EQ, and retained staged modules. An unverified handoff blocks close and leaves the module owners alive for another attempt. A default sink, module ID, or button label by itself is insufficient proof of working playback.

For a repeated live `set-param` timeout, capture the complete command and its output, the requested control keys, node ID and `Props` readback after each attempt. Check whether the persistent owner is draining stdout, whether the previous controls were restored, and whether both playback links still point through EQ. A 2026-09-25 desktop run timed out twice on the fourth preset because the owner's unread stdout pipe filled. With the output drainer, a fresh run applied all eight presets with a stable sink and FL/FR links. See the [verification record](verification.md) for the precise scope of both runs.
