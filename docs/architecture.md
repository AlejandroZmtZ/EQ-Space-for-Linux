# EQ-Space architecture and runtime design

This document describes the current GUI-first prototype. The authoritative behavior is in `src/eqspace/`; see [testing](testing.md) for validation commands and support boundaries. Python configures and monitors PipeWire. Audio samples are processed by PipeWire filter-chain modules, not by Python.

## Components and ownership

| Area | Main files | Responsibility |
| --- | --- | --- |
| Entry and window | `app.py`, `ui/main_window.py`, `ui/audio_graph.py`, `ui/async_worker.py` | Start the GUI or list saved profiles; own the selected physical sink and verified playback graph; coordinate Apply and worker completion. |
| EQ editor | `ui/peq/peq_widget.py`, `core/dsp/` | Hold editable bands and manual preamp; design a response preview; build PipeWire filter specs. |
| Presets and profiles | `ui/presets/`, `core/profiles/`, `ui/profile_state.py` | Search and preview factory/user presets; save editor drafts with explicit EQ/playback scope; atomically validate, import, export, rename and duplicate JSON; remember the last saved profile for editor restoration. |
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

The direct path connects application streams to the selected physical output. `AudioGraphController`, owned by `MainWindow`, is the only UI owner of Spatial and EQ playback routing. It stores the selected physical sink separately from effect state. EQ, Spatial and LSP are independent stage selections: the controller represents all eight enablement combinations, including LSP alone. Spatial Off preserves EQ and LSP. The Mixer EQ button changes EQ state while preserving Spatial and LSP. Spatial-only playback uses a verified native stereo gain stage when its estimated peak gain is positive, including after EQ bypass or limiter removal. This reserve remains independent of the EQ profile's automatic-headroom policy. The controller requires both output links and every observed application output port to match the requested path before reporting success. A default sink name alone is insufficient.

## Profile Apply lifecycle

1. Library selection previews the saved/factory snapshot; explicit Apply emits `preset_apply_requested`. EQ-only factory presets and manual EQ Apply preserve the current Spatial and limiter selections. A full-playback profile supplies EQ enablement, Spatial state and limiter selection. `MainWindow` snapshots Qt editor state on the main thread, then prepares and changes the graph in a serialized background worker. Busy operations reject competing topology changes while retaining the latest pending live edit.
2. Apply requires a live PipeWire graph rate. `PipeWireRegistry.graph_rate(required=True)` reads `clock.rate`; its 48 kHz fallback is for preview only. `design_filters` rejects enabled bands with nonfinite values or a center frequency at or above Nyquist. Flat has no bands and applies a neutral manual preamp stage, with automatic reserve for enabled upstream processing.
3. EQ uses a `linear` gain node (`Mult = 10 ** ((preamp_db + auto_trim_db) / 20)`, `Add = 0`) followed by enabled biquads. Manual preamp remains separate. Automatic headroom reserves estimated positive EQ and Spatial gain with a 1 dB margin. Without LSP, it also reserves positive manual preamp; with LSP, manual preamp may drive the downstream limiter. Negative preamp remains a cut. Hybrid Spatial uses the actual combined complex 2×2 transfer matrix, output-row sums, impulse bounds and isolated/correlated/opposed reference signals. These are static estimates, not measured loudness or guaranteed clipping protection.
4. A compatible EQ-only layout sends changed controls in one `pw-cli set-param <node> Props` batch and verifies readback. A timeout may have applied the values: readback decides success. Resolve and retry once; on failure, restore and verify previous controls. Compatible live edits perform this control transaction without a duplicate route scan or sink replacement. Existing graph verification remains separate from the controls readback.
5. Layout changes and full playback topology changes prepare isolated native owners under unique names. Playback autoconnection is disabled for staged effect outputs. The controller verifies candidate controls, downstream FL/FR links and observed application ports before reporting the new path active or retiring old owners. Spatial changes reserve the maximum old/new estimate before publishing a replacement. Removing LSP restores conservative gain before removing its path. Applied EQ snapshots supply reserve updates; pending editor changes cannot become live through another stage toggle.
6. Live EQ and applied Spatial controls use separate 50 ms throttles with one latest pending snapshot. Starting a timer only when inactive permits updates during continuous movement; this is not a debounce that waits for a pause. The serialized worker consumes a snapshot, and a later edit remains queued until completion. Hybrid output level updates four native mixer coefficients atomically while retaining its fixed branch ratio. The source profile selection still requires explicit Apply. There is no assumed native ramp or claim of sample-by-sample interpolation. Failed Spatial updates retain the maximum conservative reserve until controls are verified again.
7. Profile completion updates Qt editor and applied state on the main thread. A preset shows **Active** only after its requested controls and playback route are verified; failure or EQ bypass clears active EQ status. Rollback restores previous verified stages or direct output. Candidates with potentially active unverified links stay owned until a later verified handoff; failed cleanup owners remain available for shutdown retry.

`FilterChainManager` must keep its interactive `pw-cli` process alive: `load-module` creates a module owned by that process, so exiting it removes the virtual sink. Once the module ID is parsed, a daemon thread continuously drains the owner's stdout until that stream closes; `unload` joins the thread after stopping the process. PipeWire registry events printed to an unread pipe can fill it and block this module host, causing later one-shot `set-param` commands to time out. Its module arguments must be one line of SPA properties. The separately rendered `context.modules` configuration is for file based installation and cannot be passed as the live `load-module` argument. The startup prompt can precede registry events; do not assume it is the final output line.

## Data and UI boundaries

`EQProfile` is schema version 4. It stores bands, manual preamp (−24 to +12 dB), automatic headroom, `scope` (`eq` or `playback`), `eq_enabled`, Spatial state/enablement and limiter selection, plus legacy Mic fields. EQ-only Apply enables its EQ and preserves Spatial/LSP. Full-playback Apply restores all three stage selections, including an EQ-bypassed state. v1/v2 retain manual gain, unspecified Spatial enablement and automatic headroom off; v3 retains its previous full-playback defaults. Saves and exports normalize to v4, write a temporary file, flush/fsync, atomically replace and verify the exact stored snapshot. Storage remains `$XDG_CONFIG_HOME/eqspace/profiles/` (default `~/.config/eqspace/profiles/`). Startup loads the last saved editor values without applying audio.

The library distinguishes source identity from display name, so factory and user entries can coexist. Save captures the editor draft, not whichever factory row is selected. Import previews EQ-Space JSON or the supported APO/AutoEQ text subset (`Preamp`, ON/OFF PK, LS/LSC, HS/HSC with Fc/Gain/Q). Unsupported processing, malformed directives and duplicate filter numbers are errors. Name collisions offer Keep both, Replace or Cancel. Rename, duplicate and delete operate on user entries; export can serialize either source. Factory profiles remain packaged resources.

Single-band changes update the existing table row and redraw the 512-point response, preserving the active editor widget and keyboard focus. Adding, removing or replacing a band list rebuilds rows. The preview uses the observed graph rate when available and 48 kHz otherwise; Apply requires the observed rate. It is a calculated biquad response, not a measurement of the current audio signal.

Cinema expands stereo across the virtual room by default; disabling its stereo expansion option selects native 7.1 input. Explicit saved input modes remain unchanged. Its peak estimator uses the same source mapping as the renderer; saved Spatial state stores the input mode. Routing preserves native speaker channel names instead of sending every non-FR channel to FL. The bundled KEMAR-style model is synthetic. The additional experimental **HS+ (Experimental)** profile (HoloSpace + Meier) uses a parallel native graph with ten-sample common Meier alignment, preserving HoloSpace interaural/reflection timing. Its estimator matches PipeWire 1.0.5 lowpass Q-port resonance semantics rather than the unused RBJ crossfeed helper. See [hybrid design](spatial-hybrid.md) for matrix math and offline native evidence. External SOFA extraction needs optional `pysofa`. LSP Limiter Stereo is off by default; the control is disabled unless its LV2 descriptor exposes the required stereo ports, true-peak mode, and control symbols and a matching PipeWire LV2 host is available. See [LV2 hosting](lv2-host.md) for the user-local Ubuntu 24.04 helper. Activation verifies actual limiter control values before linking EQ to the staged limiter. EQ edits and packaged preset changes preserve the limiter; full-playback saved profiles apply their explicit setting; EQ-only profiles preserve the current selection. The Mixer banner names every enabled stage and shows Active only after verifying the full path, including EQ-bypassed Spatial or limiter routes. Its ceiling is −1 dBTP, with automatic level regulation and boost off. The UI shows the configured 5 ms lookahead as a lower bound because total latency depends on the installed plugin and oversampling. DeepFilterNet needs its LADSPA plugin and real capture routing. `PipeWireLevelMonitor` reads `wpctl get-volume`, which is a volume value rather than an audio activity meter.

## Failure and shutdown boundaries

The worker reports a short UI error and logs the full exception. Keep its Qt signal source alive until completion, especially during window close. The window defers closing while Apply is in progress. Normal quit switches the session default and every observed active playback link to the selected physical output, verifies that handoff, then unloads owned Spatial, limiter, EQ, and retained staged modules. An unverified handoff blocks close and leaves the module owners alive for another attempt. A default sink, module ID, or button label by itself is insufficient proof of working playback.

For a repeated live `set-param` timeout, capture the complete command and its output, the requested control keys, node ID and `Props` readback after each attempt. Check whether the persistent owner is draining stdout, whether the previous controls were restored, and whether both playback links still point through EQ. An unread owner stdout pipe can stall the native module process, so continuous draining is part of its lifetime contract. See [testing](testing.md) for reproduction and acceptance boundaries.

## Diagnostics

`diagnostics.py` configures terminal and rotating file logs at launch. Persistent logs live under `$XDG_STATE_HOME/eqspace/logs/eqspace.log` (default `~/.local/state/eqspace/logs/`), with a 2 MiB limit and three backups. `--debug` enables verbose records. Playback operations log session/operation identifiers, duration and exceptions; persistent logging failure falls back to terminal logging and does not prevent startup. Help offers Open logs and Copy diagnostic report, including recent log text and current EQ/Spatial/LSP/output/rate/busy state. These records help investigate runtime failures; their presence is not route or audio proof.

## Desktop identity

The application and desktop launcher use `eqspace` as their matching identity, with `EQ-Space` as the display name. The launcher uses the installed themed logo; Qt exports multiple window-icon sizes for desktop shells. The full SVG logo also appears in the app header. The tray uses a simplified matching asset. See [desktop installation](distribution.md#desktop-integration).
