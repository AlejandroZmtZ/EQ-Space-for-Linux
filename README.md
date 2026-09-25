# EQ-Space for Linux

EQ-Space is a **GUI-first prototype** for a PipeWire system-wide parametric equalizer. Python builds filter-chain configurations and controls PipeWire; PipeWire performs the audio processing. The main supported flow is selecting one of eight built-in EQ presets and applying it from the Presets tab.

![EQ-Space main window](docs/screenshots/main-window.png)

## What works

- Parametric EQ with editable bands, a frequency-response graph, and a PipeWire filter chain.
- Eight built-in EQ presets. Select one to see its description, then click **Apply**. Switching between them updates the running filter controls while keeping the EQ sink connected. The UI reports **Active** after the update and routing succeed.
- Per-app volume and mute controls, plus one system EQ route and a physical listening-device selector.
- Local JSON profiles with atomic writes. The GUI loads the last saved profile into the editor at startup; audio changes only after Apply.
- Experimental Spatial and Mic tabs. Spatial offers crossfeed and a **synthetic KEMAR-style model**; it is not a measured KEMAR SOFA recording. External SOFA files need optional `pysofa`. Mic noise reduction needs the DeepFilterNet LADSPA plugin and capture routing setup. These paths are not part of the verified preset flow.

## Requirements

- Linux with PipeWire and WirePlumber (`pw-cli`, `pw-dump`, `pw-link`, `wpctl`, and `pw-metadata` available).
- Python 3.12 and Qt desktop support. Ubuntu 22.04/24.04 are the intended targets.
- A working audio output. The filter chain exists only while EQ-Space is running.

## Install and run

```bash
git clone https://github.com/AlejandroZmtZ/EQ-Space-for-Linux.git
cd EQ-Space-for-Linux
python3.12 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/eqspace
```

Use the Mixer tab to choose a physical listening device. Start audio playback, open **Presets**, select an EQ preset, and click **Apply**. Mixer then shows whether EQ is on. Use **Use direct output** to bypass EQ; the listening device can be changed while EQ is off. If the status says an app is on another device or not using EQ, that app's current audio link did not follow the chosen route. Application rows control volume and mute. Quit from the window or tray to restore direct output. The desktop launcher source is in `data/desktop/eqspace.desktop`; install that and the icon separately if you want an application-menu entry. The wheel includes the icon used by the tray.

![Mixer with a physical listening device and one EQ route](docs/screenshots/mixer.png)

The only command-line operation is read-only:

```bash
.venv/bin/eqspace --list-profiles
```

## Troubleshooting

- **Apply failed / PipeWire unavailable:** run `pw-cli info 0` and `wpctl status` in a terminal. Start or repair your user PipeWire and WirePlumber services before retrying. EQ-Space reports the failure in the Presets tab and keeps the window open.
- **No sound after applying:** check the Mixer status and selected listening device. If it says **Output disconnected**, click **Use direct output**. If an app is **not using EQ**, try bypassing and turning EQ on again while it plays. `wpctl status` shows the current default sink, and `pw-link -l` shows the actual app and filter links. Quit EQ-Space to restore direct routing before retrying.
- **No tray icon:** tray support depends on your desktop. Keep the main window open and quit it normally.
- **Spatial or Mic unavailable:** those tabs are experimental. External SOFA extraction requires `pysofa`; microphone processing requires a separately installed DeepFilterNet LADSPA plugin. The mic level meter is hidden until real capture activity metering is available.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ -q
.venv/bin/pip wheel . --no-deps --wheel-dir dist
```

Tests use fake PipeWire backends where possible. Headless CI checks do not certify live audio routing; verify the GUI and restore path on a PipeWire desktop before distribution.

- [Architecture and runtime design](docs/architecture.md)
- [Engineering lessons and agent guidance](docs/agent-lessons.md)
- [Verification record and known limitations](docs/verification.md)
- [Report a bug](https://github.com/AlejandroZmtZ/EQ-Space-for-Linux/issues)

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
