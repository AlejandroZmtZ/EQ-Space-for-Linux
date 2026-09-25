# Engineering lessons and validation guide

Read [architecture](architecture.md) before changing Apply or routing. This guide separates deterministic tests from live PipeWire evidence and records issues that have already caused user visible failures.

## Invariants to preserve

- Keep live audio sample processing in PipeWire's native graph. Python may design coefficients and inspect control or link metadata; it must not run a real time DSP loop.
- Do not destroy an EQ sink while active playback remains linked to it. Verify both default route and active output links when changing graph shape or falling back to direct output.
- Treat `pw-cli set-param` command status as ambiguous on timeout. Read back `Props`; retry once after resolving the current node ID; verify rollback before calling old controls safe.
- Mark a preset **Active** only after requested controls and EQ routing have been verified. Clear cached good state when restoration is unverified.
- Keep the preamp manual and per profile. Validate actual PipeWire graph rate for Apply; a preview rate fallback cannot authorize a potentially above-Nyquist filter.
- Save profile JSON atomically and continue to read version 1 profiles with a zero dB preamp.

## Reproducible checks

Use the local Python 3.12 environment and offscreen Qt:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ -q
git diff --check
```

Useful focused files are `tests/test_reliable_eq.py`, `tests/test_preset_apply_flow.py`, `tests/test_filterchain_manager.py`, `tests/test_pipewire_control.py`, `tests/test_pipewire_registry.py`, `tests/test_ui_peq.py`, and `tests/test_profiles.py`. They cover control readback and restoration, graph and route failures, profile migration, invalid bands, and editor focus. Add fault injection for failures **before** and **after** a `set-param` request takes effect; both can have the same timeout exception. UI tests should inject managers and registries so they never require the desktop PipeWire session.

For a live desktop check, first record the chosen physical output, default sink, relevant app stream and both FL/FR links. Start a silent playback stream, Apply each of the eight EQ presets, and verify the EQ sink identity, requested `Props`, and both links after each switch. Bypass EQ, verify direct output and both links, then restore the original output route. Use `wpctl status`, `pw-dump`, and `pw-link -l` as independent observations. Record stderr and the process exit code from a real desktop terminal if a GUI exit or stuck Apply occurs. An offscreen Qt process connected to live PipeWire verifies backend behavior but cannot establish audible quality or reproduce every desktop interaction.

Avoid hard coding a previous node ID or test run count. IDs and graph rate can change between sessions. Do not use a temporary `pw-cli load-module` process that exits immediately when validating persistent routing.

## Lessons from prior failures

1. A preset switch once destroyed and recreated the EQ sink while a silent playback stream was active. Its links disappeared although the UI reported the next preset as Active. Compatible ten-band preset layouts now use an in-place control batch; a changed layout needs a direct-output handoff before replacement.
2. `pw-cli` may print its `>>` startup prompt and then live registry events. Parsing only the final output line rejected a successful connection. Likewise, rapid `pw-dump` calls may contain multiple complete JSON documents; snapshot parsing accepts them while validating the trailing content.
3. A control timeout does not establish whether PipeWire applied the new values. The readback and previous-control restoration paths need separate regression tests. A stale node ID must be resolved again before retry.
4. Changing the default sink does not prove that an existing player moved. Inspect both output ports of active streams. If a port is still connected to EQ or disconnected, leave the old chain intact and report the failed handoff.
5. Qt worker lifetime matters. A discarded signal source caused `RuntimeError: Signal source has been deleted` during an earlier failed Apply. Preserve worker ownership and defer window close until its completion callback runs.
6. Rebuilding all table widgets for each spinbox change loses focus and wastes time. Keep widgets for single-band edits; rebuild only for band-list changes. If this path changes, benchmark repeated edits on the same machine and verify focus with offscreen Qt.
7. A fresh desktop run reproduced a live control update failure on the fourth preset. With a silent `pw-cat` stream directed to EQ, three presets held sink ID 64 and stable FL/FR output links; `harman_over_ear_2018` then timed out twice on `pw-cli set-param 64 Props` for ten `Gain` controls. The manager restored the previous controls, the GUI stayed open, and the test harness exited 1 after cleanup. The original headphone route was restored. The persistent `pw-cli` module host was printing about 18–23 KB of registry events per preset to an unread stdout pipe. When the pipe filled, the host blocked. Keep draining that output for the host's entire lifetime; `tests/test_filterchain_manager.py` has a bounded over-capacity regression. A fresh rerun after the output-drainer fix passed all eight presets on sink ID 124 with stable FL/FR links, exit 0, and the original physical default route restored.

The dated [verification record](verification.md) includes run counts and live observations from different revisions. Those numbers describe their checkout at that time, not a current pass. Re-run the checks after implementation changes. The original spontaneous desktop exit was not reproduced in either fresh desktop run; capture its complete terminal trace before claiming its cause. The successful eight-preset rerun verifies the specific control and routing path described above, not audible quality.
