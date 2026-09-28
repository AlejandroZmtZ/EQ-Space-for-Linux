# LSP LV2 hosting

The limiter needs both LSP Limiter Stereo LV2 1.2.24 or newer and PipeWire's
`libpipewire-module-filter-chain-lv2.so`. Finding the LSP descriptor alone does
not establish hosting support. Some PipeWire distributions omit that helper; EQ-Space checks
hosting capability separately from the plugin descriptor.

EQ-Space checks the installed host and a user-local directory matching the
running `pw-cli` library version: `~/.local/lib/eqspace/pipewire-<version>/`.
Only the limiter owner receives `PIPEWIRE_MODULE_DIR`; the system daemon is
unchanged. A PipeWire upgrade requires a matching helper. Missing hosting
support disables the control and gives installation guidance.

## Ubuntu 24.04 with PipeWire 1.0.5

The matching upstream host can be built without upgrading the audio service:

```bash
bash tools/install-lv2-host-noble.sh
```

This explicit installation downloads official PipeWire source and Ubuntu
header packages, builds a native helper with `cc`, and installs it in your
user directory. It requires `curl`, `cc`, `apt`, `dpkg-deb`, `tar`, and
`sha256sum`. It does not use sudo or restart services. The script supports
PipeWire 1.0.5 only; use your distribution's matching host package on other
versions. The source is [PipeWire 1.0.5](https://github.com/PipeWire/pipewire/tree/1.0.5/src/modules/module-filter-chain).

LSP itself is separate. Install a compatible LV2 bundle in a standard
plugin location such as `~/.lv2/`. Older plugin versions can lack the
required true-peak mode; an installed package alone does not establish
compatibility.

## Activation and limits

LSP is an independent playback stage: enable it with EQ and Spatial both off,
or add it after either or both. All eight EQ/Spatial/LSP selections are
represented by the controller; the order is Spatial → identical stereo EQ →
LSP → physical output, omitting disabled stages. A native gain stage retains
positive Spatial reserve when EQ is bypassed. EQ edits and EQ-only factory/user
presets preserve LSP. Full-playback profiles restore their explicit limiter
selection. EQ bypass and Spatial Off preserve the remaining stages. Stage
preparation, control readback and route transactions run in background workers.

With automatic headroom enabled, positive manual preamp can drive the limiter
without being canceled by static trim; EQ and Spatial gain retain their
estimated reserve. A modest manual increase can be assessed with familiar material after the route is verified. More
drive can reduce dynamics or create audible limiting. Negative preamp remains
a real cut. Disabling LSP restores conservative gain before removing it.
The displayed peak with LSP is the estimated input peak, not measured output.

Activation verifies the actual true-peak, ceiling, lookahead and gain controls
as well as both downstream channels and active application links. The ceiling
is −1 dBTP; automatic level regulation and boost are off. Configured lookahead
is 5 ms; total latency also depends on oversampling. This is not a measurement
of output true peak, latency, or audible switching quality.

The experimental [HS+ (Experimental, HoloSpace + Meier) hybrid](spatial-hybrid.md) sums its two
branches before EQ and LSP. Its static reserve uses the actual combined complex
transfer, not a sum of standalone gains. Identical linear stereo EQ commutes
with that matrix; nonlinear LSP stays last. Offline native kernel checks do not
establish measured output true peak, desktop audio quality or frozen-build
readiness.
