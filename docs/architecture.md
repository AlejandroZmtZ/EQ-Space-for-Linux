# EQ-Space architecture and runtime design

This document describes the current GUI-first prototype. The authoritative behavior is in `src/eqspace/`; use [engineering lessons](agent-lessons.md) for the development and validation workflow and [verification](verification.md) for dated observations. Python configures and monitors PipeWire. Audio samples are processed by PipeWire filter-chain modules, not by Python.

## Components and ownership

| Area | Main files | Responsibility |
| --- | --- | --- |
| Entry and window | `app.py`, `ui/main_window.py`, `ui/async_worker.py` | Start the GUI or list saved profiles; coordinate Apply, routing, rollback, and Qt worker completion. |
| EQ editor | `ui/peq/peq_widget.py`, `core/dsp/` | Hold editable bands and manual preamp; design a response preview; build PipeWire filter specs. |
| Presets and profiles | `ui/presets/`, `core/profiles/`, `ui/profile_state.py` | Load packaged presets; validate, save, import and export versioned JSON; remember the last saved profile for editor restoration. |
| PipeWire observation | `core/pipewire/registry.py` | Parse `pw-dump` snapshots, poll changes, and read `clock.rate` from `pw-metadata`. |
| PipeWire control | `core/pipewire/control.py`, `ui/mixer/` | Select the physical output; inspect links; move playback streams; set the default sink and per-stream volume or mute. |
| Filter-chain lifetime | `core/filterchain/manager.py` | Render single-line SPA properties; own a persistent interactive `pw-cli`; load or destroy its module; update and verify live controls. |
| Experimental paths | `ui/spatial/`, `ui/mic/`, `core/filterchain/{spatial,mic}.py` | Render or configure spatial and microphone chains. These are outside the verified EQ preset path. |

The playback graph is:

```text
application stream FL/FR
        │
        ▼
eqspace.filter-chain (virtual Audio/Sink)
        │  preamp linear stage → enabled biquads
        ▼
eqspace.filter-chain.playback FL/FR
        │
        ▼
selected physical output FL/FR
```

The direct path connects application streams to the selected physical output. `MixerWidget` exposes a single system EQ route and observes actual app links; default-sink metadata alone does not prove that an already playing stream moved.

## Profile Apply lifecycle

1. `PresetsWidget` converts a packaged preset into an `EQProfile` and emits `preset_apply_requested`. Manual EQ Apply constructs a temporary profile from the editor. `MainWindow.apply_profile` serializes requests with `_profile_apply_busy` and the PEQ worker state.
2. Apply requires a live PipeWire graph rate. `PipeWireRegistry.graph_rate(required=True)` reads `clock.rate`; its 48 kHz fallback is for preview only. `design_filters` rejects enabled bands with nonfinite values or a center frequency at or above Nyquist. An empty enabled-band set is rejected.
3. The editor builds a `linear` preamp node (`Mult = 10 ** (preamp_db / 20)`, `Add = 0`) followed by enabled biquads. The preamp remains a manual profile setting. The graph and estimated peak include its gain; the suggested attenuation is advice, not an automatic change.
4. If the loaded graph has the same node names, types and channel layout, `FilterChainManager.reload` collects changed controls into one `pw-cli set-param <node> Props` request. It resolves the current node ID and reads the node's `Props` from `pw-dump`. A command timeout may have applied the values: readback, rather than command exit alone, decides success. On mismatch it resolves the node and retries once. If the second readback fails, it writes the previous verified controls and verifies them. Failure to prove restoration is reported as unverified.
5. If graph layout changes, `MainWindow` first routes playback to the selected physical output. `set_system_routing(False)` checks formerly EQ-linked stream ports after the move and refuses replacement when an active port remains on EQ or becomes disconnected. The manager then replaces the module. On failure, the window attempts to restore the previous verified graph and route. If restoration cannot be verified, it attempts a direct-output fallback, clears the remembered good graph, and reports failure. A failed fallback itself must not be reported as verified.
6. After a successful load or update, `PeqWidget` verifies controls and the window enables and checks the EQ route before reporting success. The Presets button can show **Active** only while the requested preset and EQ route are both recorded as active. A failed or bypassed route clears that indication.

`FilterChainManager` must keep its interactive `pw-cli` process alive: `load-module` creates a module owned by that process, so exiting it removes the virtual sink. Once the module ID is parsed, a daemon thread continuously drains the owner's stdout until that stream closes; `unload` joins the thread after stopping the process. PipeWire registry events printed to an unread pipe can fill it and block this module host, causing later one-shot `set-param` commands to time out. Its module arguments must be one line of SPA properties. The separately rendered `context.modules` configuration is for file based installation and cannot be passed as the live `load-module` argument. The startup prompt can precede registry events; do not assume it is the final output line.

## Data and UI boundaries

`EQProfile` is schema version 2. It stores `bands`, `preamp_db` (−24 to +12 dB, default 0), optional spatial and mic state, and other profile fields. Loading version 1 supplies a zero dB preamp; profile saves and exports write version 2. Profile JSON is under `$XDG_CONFIG_HOME/eqspace/profiles/` (default `~/.config/eqspace/profiles/`); saves use a temporary file in the same directory followed by `os.replace`. `ui/profile_state.py` stores the name of the last saved profile in `eqspace/state.json`. Startup loads that profile into the editor without applying audio.

Single-band changes update the existing table row and redraw the 512-point response, preserving the active editor widget and keyboard focus. Adding, removing or replacing a band list rebuilds rows. The preview uses the observed graph rate when available and 48 kHz otherwise; Apply requires the observed rate. It is a calculated biquad response, not a measurement of the current audio signal.

Spatial and Mic state may be stored with a profile, but the regular EQ Apply path sets their UI state; it does not certify a combined output and capture graph. The bundled KEMAR-style model is synthetic. External SOFA extraction needs optional `pysofa`; DeepFilterNet needs its LADSPA plugin and real capture routing. `PipeWireLevelMonitor` reads `wpctl get-volume`, which is a volume value rather than an audio activity meter.

## Failure and shutdown boundaries

The worker reports a short UI error and logs the full exception. Keep its Qt signal source alive until completion, especially during window close. The window defers closing while Apply is in progress. Normal quit tries to restore direct routing; process exit then ends the owned `pw-cli` process and its filter-chain module. Failure paths should report what was verified; a default sink, module ID, or button label by itself is insufficient proof of working playback.

For a repeated live `set-param` timeout, capture the complete command and its output, the requested control keys, node ID and `Props` readback after each attempt. Check whether the persistent owner is draining stdout, whether the previous controls were restored, and whether both playback links still point through EQ. A 2026-09-25 desktop run timed out twice on the fourth preset because the owner's unread stdout pipe filled. With the output drainer, a fresh run applied all eight presets with a stable sink and FL/FR links. See the [verification record](verification.md) for the precise scope of both runs.
