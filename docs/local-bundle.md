# Local Linux archive

The local build script creates an experimental Linux archive containing Python and the application dependencies. It is a developer packaging tool, not a Snap, Flatpak or AppImage, and it does not upload anything.

## Build and run

Install the application's dependencies into `.venv`, then install the build tool separately:

```bash
.venv/bin/pip install PyInstaller==6.20.0
tools/build-local-bundle.sh
```

The output is `dist/eqspace-0.1.0-linux-x86_64.tar.gz`, with a SHA-256 file alongside it. Extract the archive and keep the whole `eqspace` folder intact. Run its `eqspace` executable. The archive bundles Python, but the host still needs PipeWire, WirePlumber, their command-line tools and compatible desktop graphics libraries.

Optional LSP processing also requires its compatible plugin and PipeWire LV2 host. See [limiter setup](lv2-host.md).

## Compatibility

The development build targets Ubuntu 24.04 x86_64. Building on one distribution does not establish compatibility with older system libraries. Extraction, profile listing and offscreen startup are limited checks; perform the installed-package [acceptance checks](testing.md) before distributing an archive.

Use a single audio-controlling instance during package testing. Confirm preset changes, Spatial and LSP switching, profile persistence, physical-output selection and verified restoration on normal quit. Current store-format support is described in [distribution](distribution.md).
