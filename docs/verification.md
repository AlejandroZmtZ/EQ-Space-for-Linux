# Prototype verification — 2026-09-25

## Fresh desktop preset runs (2026-09-25)

A fresh GUI launched on desktop `DISPLAY=:1`. With a silent `pw-cat` playback stream directed to EQ, the first three built-in presets applied on EQ sink ID **64** while both FL/FR output links remained stable. Applying the fourth preset, `harman_over_ear_2018`, timed out twice on `pw-cli set-param 64 Props` for ten `Gain` controls. The manager verified restoration of the previous controls. The GUI remained open. The test harness exited with code **1 after cleanup**, and the original headphone route was restored.

This reproduced a live control-update failure, not a desktop process exit. Diagnosis showed that the persistent `pw-cli` process hosting the filter-chain module emitted roughly **18–23 KB of registry output per preset**. Its stdout pipe was not drained after module load. Once that pipe filled, the host process blocked and later `set-param` calls timed out. `FilterChainManager` now starts a daemon output-drainer thread after loading and joins it on unload. A bounded regression writes more than pipe capacity to the fake process's stdout and verifies that draining continues.

After that fix, a fresh desktop run applied **all eight built-in EQ presets** on the same sink ID **124** with stable FL/FR playback links. The GUI test exited with code **0**, and the original physical default output route was restored. This is live routing and control evidence for that run; it is not a human listening assessment or a reproduction of the originally reported spontaneous desktop process exit.

The current full offscreen suite passed **338 tests** after the output-drainer fix; `git diff --check` was clean. The older 322-test result below is retained as a dated snapshot of the earlier revision.

## Apply failure reproduction

- Preset: `bass_boost`. The original offscreen Qt run against a sandbox-restricted PipeWire socket stayed on “Applying…”; the scheduled GUI exit returned code **0**. Stderr showed `BrokenPipeError: [Errno 32] Broken pipe` in `FilterChainManager.load()`, followed by `RuntimeError: Signal source has been deleted` when the discarded worker emitted completion. `pw-cli info 0` in that sandbox reported `failed to connect: Operation not permitted`. This did not reproduce the reported spontaneous process exit.
- With live PipeWire access, the first run also stayed on “Applying…” and exited with code **0** after the scheduled quit. Its fault trace showed `FilterChainError: pw-cli could not connect` while `pw-cli` had already printed `>>` followed by live registry events. The loader incorrectly required `>>` at the end of all accumulated output.

Relevant stderr trace from the restricted run:

```text
async_worker.py:32 in run -> peq_widget.py:323 in _do_apply -> manager.py:239 in load
BrokenPipeError: [Errno 32] Broken pipe
async_worker.py:37 in run -> signals.finished.emit(False, str(exc))
RuntimeError: Signal source has been deleted
```

## Current checks

- `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ -q`: **322 passed**.
- `git diff --check`: clean.
- Built a wheel, installed it into an isolated target, and started its GUI from outside the source tree with Python 3.12. Eight EQ presets, the tray icon, and the packaged synthetic spatial model were present; the Mixer excluded the virtual EQ sink from its listening-device selector.
- Against live PipeWire, `bass_boost` and `gaming_footsteps` both reached **Active** while the GUI remained running. The direct Python process returned code **0** on quit. The selected sink before and after was **Built-in Audio Analog Stereo**.
- A silent four-second `pw-play` stream linked `pw-play:output_FL/FR` to `eqspace.filter-chain:playback_FL/FR`, and `eqspace.filter-chain.playback:output_FL/FR` linked to the physical Built-in Audio sink. The original sink remained selected after quit.
- After the Mixer simplification, a silent stream already playing to Bluetooth moved into EQ on Apply and back to Bluetooth on bypass. A new silent stream also reached EQ and then Bluetooth. Both direct runs exited normally. Linking a new path before removing the old one was also tested live: PipeWire accepted both links briefly, allowing a failed new link to leave the prior route intact.
- With EQ off, the GUI listening-device selector moved a running silent stream from Bluetooth to Built-in Audio and back. The current default sink and both stream channels matched the selector after each change; Bluetooth was restored afterward.
- One earlier silent playback probe found a new stream connected directly to Built-in Audio while EQ was the default. Later probes did not reproduce that route. The Mixer now reports an active app whose actual links bypass EQ or the selected direct device. The exact cause of the earlier route remains unresolved.

## Consecutive preset switch reproduction and repair

- With Bass Boost active, applying Gaming Footsteps destroyed and recreated `eqspace.filter-chain`. The running silent `pw-play` stream lost both output links while the GUI still reported the second preset as Active. On a later user run, PipeWire retained the nonexistent EQ sink as its configured default, matching the reported Brave buffering and loss of sound.
- All eight built-in EQ presets use the same ten peaking filters. A live `pw-cli set-param` probe changed filter gain and frequency in place; `enum-params Props` showed the new values. `FilterChainManager.reload` now sends the changed controls in one command when the graph shape matches, keeping the sink and playback links alive.
- At the normal 1000 ms Mixer poll interval, three consecutive presets (`bass_boost`, `gaming_footsteps`, `harman_in_ear_2019`) reached Active on the same EQ sink ID. Both channels of a running silent stream stayed linked through EQ. The GUI exited normally and Bluetooth was restored.
- A separate rapid-poll run exposed intermittent extra JSON documents from `pw-dump` and one `pw-cli set-param` timeout. Snapshot parsing now accepts complete appended JSON documents. If the control command fails, the loaded sink stays present and Apply reports failure. Browser media recovery after a previously stranded stream still needs a user listening check.

The live checks used Qt offscreen mode against the desktop's real PipeWire session. They verify the backend and GUI event path but are not a human listening assessment or a GitHub Actions run.
