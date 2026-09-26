# LSP LV2 hosting

The limiter needs both LSP Limiter Stereo LV2 1.2.24 or newer and PipeWire's
`libpipewire-module-filter-chain-lv2.so`. Finding the LSP descriptor alone does
not establish hosting support. Ubuntu 24.04's PipeWire 1.0.5 package on the
verified machine omitted that helper.

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

LSP itself is separate. This machine uses the official LSP 1.2.29 LV2 bundle
in `~/.lv2/lsp-plugins.lv2`. Ubuntu Noble's older 1.2.14 package lacks the
required true-peak mode.

## Activation and limits

Apply EQ, then enable LSP. The graph becomes EQ → LSP → physical output, or
Spatial → EQ → LSP → physical output. EQ edits and packaged preset changes
preserve the limiter selection. Saved profiles apply their explicit limiter
setting. EQ bypass and Spatial Off preserve the remaining stages.

With automatic headroom enabled, positive manual preamp can drive the limiter
without being canceled by static trim; EQ and Spatial gain retain their
estimated reserve. Start with +3 dB, click Apply, and assess the sound. More
drive can reduce dynamics or create audible limiting. Negative preamp remains
a real cut. Disabling LSP restores conservative gain before removing it.
The displayed peak with LSP is the estimated input peak, not measured output.

Activation verifies the actual true-peak, ceiling, lookahead and gain controls
as well as both downstream channels and active application links. The ceiling
is −1 dBTP; automatic level regulation and boost are off. Configured lookahead
is 5 ms; total latency also depends on oversampling. This is not a measurement
of output true peak, latency, or audible switching quality.
