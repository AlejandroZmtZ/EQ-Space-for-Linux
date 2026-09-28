# Privacy and local data

EQ-Space processes audio locally through PipeWire. The application contains no telemetry client, account system or automatic diagnostic upload. It configures native audio modules; Python does not receive or process live audio buffers.

## Files on your device

| Data | Default location | Purpose |
| --- | --- | --- |
| Saved profiles | `~/.config/eqspace/profiles/` | User EQ and optional playback settings |
| Editor restoration state | `~/.config/eqspace/state.json` | Last saved profile selection |
| Spatial cache | `~/.cache/eqspace/hrtf/` | Prepared local impulse-response files |
| User Spatial files | `~/.local/share/eqspace/hrtf/` | Imported SOFA files |
| Diagnostics | `~/.local/state/eqspace/logs/` | Rotating application/error logs |

These locations follow `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME` when set. Exporting a profile writes only to the location you choose. Deleting a library entry deletes that saved user profile; it does not remove exported copies.

## Diagnostics and sharing

Logs rotate at 2 MiB with three backups. `--debug` records more detail. **Copy diagnostic report** copies current EQ/Spatial/LSP state, the selected output, graph rate, operation state and recent logs to your clipboard. Copying does not transmit the report.

Reports can contain profile names, device and application names, PipeWire identifiers, timestamps, local file paths and exception details. Review and redact them before pasting into a public issue. Sharing a profile exposes its name and saved processing configuration.

Installing dependencies or running the optional LV2 host installer makes separate network requests to the relevant package/source services. The main audio application does not need an online account or network audio service.
