# Prototype verification — 2026-09-26

## Live EQ editing (2026-09-26 local)

- Active EQ edits now use a 180 ms debounce and the verified Apply path. Busy transactions defer the latest edit; bypassed EQ remains bypassed. Invalid responses retain their validation message.
- Full offscreen suite: **464 passed**; whitespace check clean. The new automatic editing interaction still needs a live listening check.

## Audio graph safety review implementation (2026-09-26 local)

- Implemented all seven review findings with three subagents, regression tests and independent integration review. Replacement links are verified before working links are removed. Spatial transitions reserve the greater old/new gain before publication. Spatial-only playback retains a native stereo headroom stage when EQ is bypassed.
- Gain recalculation uses a deep copy of the applied profile and preserves its filter layout; unapplied editor changes remain unapplied. Limiter removal updates cached bypassed EQ controls conservatively. Profile rollback restores conservative controls before reconnecting and restores the previous limiter before returning driven gain. Graph mutation controls are disabled during asynchronous Apply.
- Obsolete neutral gain owners are retired after verified handoff. Failed old EQ/Spatial cleanup retains the owner for shutdown retry.
- An isolated silent live stream exercised native EQ/LSP/Spatial modules, actual gain readback, EQ bypass, limiter removal, cached EQ reactivation, repeated Spatial switching, Spatial Off and shutdown. Only route publication/verification was scoped to that stream in the harness; the real session default and existing Brave links were checked unchanged after every step. This was not a real-default handoff, audible continuity assessment or output true-peak measurement.
- Final offscreen suite: **460 passed**; `git diff --check` clean. Implementation used three subagents and independent integration review. Hosted CI was not rerun.

## Cinema and manual-gain follow-up (2026-09-26 local)

- Automatic headroom previously canceled positive preamp gain even with LSP enabled. With LSP active it now reserves EQ/Spatial gain and leaves manual preamp effective; without LSP it reserves positive manual gain. Negative preamp always remains a cut. LSP removal first applies and verifies conservative EQ gain.
- Native Cinema channel routing now matches FL/FR/FC/LFE/SL/SR/RL/RR inputs instead of collapsing most channels to FL. Cinema defaults to stereo expansion following the user preference, with matching offline peak calibration; disabling expansion selects native multichannel input. Existing saved Spatial state retains native mode when no expansion field exists.
- An isolated silent eight-channel stream verified every native Cinema input and Spatial → EQ → LSP → headphones output. Actual EQ control readback showed +6 dB manual gain produced a **1.995263×** increase with LSP; conservative gain restoration removed that increase. The existing running app default `filter-chain-30122-20` and its playback route were left unchanged. Full offscreen suite: **433 passed**; diff whitespace check clean. This is control/link evidence, not an output true-peak or listening measurement.

## LSP host, combined routing and Mixer indicator (2026-09-26 local)

- `PIPEWIRE_DEBUG=4` confirmed the load failure was a missing `libpipewire-module-filter-chain-lv2.so` in Ubuntu's PipeWire 1.0.5 module directory. LSP itself was installed. The module is hosted inside the persistent `pw-cli`, not the main daemon.
- Built the official matching PipeWire 1.0.5 LV2 helper and installed it under `~/.local/lib/eqspace/pipewire-1.0.5/`, alongside links to existing system modules. The limiter owner receives this module directory; no audio-service restart or system PipeWire upgrade occurred. The reproducible [installer and setup](lv2-host.md) passed on this machine. LSP 1.2.29 is installed under `~/.lv2/lsp-plugins.lv2`.
- Live controller checks verified EQ → LSP → WH-CH720N and Spatial → EQ → LSP → WH-CH720N. Actual Props readback confirmed true-peak mode 21, ceiling 0.891251, lookahead 5 ms, unity input/output gain, and boost/ALR disabled. EQ edits, Spatial replacement, EQ bypass, Spatial Off, limiter removal, re-enabling and shutdown passed full channel/link checks.
- A live offscreen MainWindow check enabled the actual limiter checkbox after Flat Apply. The first tab showed `EQ + LSP Limiter active` and `Spatial + EQ + LSP Limiter active`. Bass Boost and manual EQ Apply preserved both stages. EQ bypass showed `Spatial + LSP Limiter active`; Spatial Off showed `LSP Limiter active`. Quit restored default WH-CH720N and both active Brave FL/FR links to `bluez_output.00_11_22_33_44_55.1` before unloading every owned module.
- Regression coverage includes missing host versus missing plugin, matching host version, control mismatch, failed activation rollback with retained owner, output change while EQ is bypassed, failed output-change restoration, and combined/unverified Mixer labels. Full offscreen suite: **426 passed**; `git diff --check` clean.
- These checks establish live graph and control behavior on the current host, not measured true-peak output, actual end-to-end latency, audible gaplessness, or hosted CI status. Earlier unverified-LSP entries below describe previous snapshots.


## Shutdown handoff and rollback owner retention (2026-09-25 local)

- Added fault-injection tests for a staged Spatial route that becomes default before stream-link verification fails, and for shutdown where direct playback cannot be verified. Failed staged owners remain loaded until a later successful direct handoff; shutdown unloads them only after verifying the selected physical route.
- Live PipeWire MainWindow close check used a neutral temporary EQ chain while Brave had active FL/FR playback. Closing returned the default to WH-CH720N, moved both Brave links to `bluez_output.00_11_22_33_44_55.1`, then unloaded the EQ module; the close was accepted and no temporary EQ sink remained.
- Full offscreen suite after the change: **415 passed**. This validates graph/close handoff behavior, not the separate LSP LV2 host capability; live LSP instantiation remains unverified on this PipeWire 1.0.5 setup.

## Combined EQ and Spatial verification (2026-09-25 local)

- Current dirty checkout: `QT_QPA_PLATFORM=offscreen XDG_CACHE_HOME=/tmp/eqspace-cache .venv/bin/pytest tests/ -q` passed **411 tests**; `git diff --check` was clean. Tests cover Flat, profile v1/v2 migration, four EQ/Spatial states, staged-switch failures and sink discovery retries, limiter UI gating, limiter graph insertion/removal and bypass behavior, missing and incompatible limiter capability, and offline Spatial peak references at 44.1/48/96 kHz.
- Before the repair, live `pw-link -l` showed Brave feeding the existing Spatial sink while the existing EQ sink's FL/FR playback outputs went to the physical sink without receiving Brave playback. The selected physical sink was `alsa_output.pci-0000_00_1f.3.analog-stereo` at 48 kHz.
- A targeted silent WAV stream traversed staged Spatial → EQ → physical links. In-place EQ control updates, changed EQ layout, Spatial replacement, and Spatial Off with EQ retained all had verified FL/FR links and control readback. This probe left the desktop default and Brave links unchanged.
- A second live check changed the session default through EQ only, Spatial → EQ, a changed EQ layout, a new Spatial module, Spatial Off with EQ active, and direct physical output. Each step verified the default and active application output ports. The original `eqspace.spatial` default (`filter-chain-38811-20`) and Brave's FL/FR links were restored after the check. The initial attempts exposed delayed sink registration and a playback stream that disappeared between snapshots; bounded readiness polling and current-link checks resolved those failures.
- A live offscreen GUI check applied Flat with `preamp:Mult = 1.0`, enabled HoloSpace with automatic trim **−2.5 dB**, applied Bass Boost while Spatial remained active with trim **−10.0 dB**, edited a band while Spatial remained active with trim **−9.9 dB**, then turned Spatial Off while EQ remained active with trim **−8.5 dB**. Each displayed estimated combined peak was **−1.0 dB** for the boosted paths. The original default and Brave links were restored. These are calculated gain estimates and graph observations, not LUFS, true-peak, or listening measurements.
- LSP Limiter Stereo LV2 was not installed in the inspected environment. Its optional control was verified disabled with installation guidance; the actual limiter stage and latency could not be measured live.
- Limiter follow-up (2026-09-26): the local LV2 registry has no installed `lsp-plugins-lv2`. Ubuntu Noble's repository candidate is LSP 1.2.14; its downloaded descriptor has stereo ports and a latency output but no True Peak oversampling entry. True Peak modes were added upstream in 1.2.24, so that package cannot satisfy the app's true-peak requirement. The detector also assumed a `lv2:reportsLatency` token, while the actual descriptor marks the output latency port with `pp:reportsLatency`. Detection now accepts the descriptor's namespace and explains missing/incompatible plugin versions in the UI. Full realtime limiter instantiation remains unverified until LSP 1.2.24+ is available to PipeWire.
- Public GitHub Actions run [36189042679](https://github.com/AlejandroZmtZ/EQ-Space-for-Linux/actions/runs/36189042679) failed before any job step started. Its annotation says the account is locked due to a billing issue. Hosted CI has not run the current changes.

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
