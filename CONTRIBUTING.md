# Contributing to EQ-Space

Use Python 3.12 or newer and a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ -q
```

Read [architecture](docs/architecture.md) before changing playback ownership or routing. Python prepares configurations and controls PipeWire; it must never process live audio buffers. Native filter modules perform the DSP.

## Changes and validation

Keep changes focused and describe their user-visible behavior. Add meaningful regressions for routing, profile persistence, DSP math or worker failures. GUI tests run with `QT_QPA_PLATFORM=offscreen`; use fake PipeWire registries and controls unless a check explicitly targets an isolated native session. See [testing](docs/testing.md).

Profile saves must remain atomic and validated. Graph changes must verify controls and stereo links before retiring the previous working path. Preserve manual preamp as an independent user setting. Keep optional plugin failures understandable in the UI.

Build a wheel and check its resources before proposing a distribution change:

```bash
.venv/bin/pip wheel . --no-deps --wheel-dir dist
python3 tools/check-public-tree.py
```

## Public repository content

Commit source, tests, current technical documentation and generic demonstration assets. Keep local workspace files, personal profiles, raw diagnostics, private research, implementation plans and assistant session notes outside the public tree. Ignoring a file does not remove it from Git if it is already tracked.

Check screenshots and fixtures for usernames, hostnames, device identifiers, paths and unrelated desktop content. The screenshot renderer uses neutral data and isolated configuration folders:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render-screenshots.py
```

Review diagnostic reports before attaching them to issues. Redact identifiers while retaining the error text and route/control information needed to reproduce the problem. Do not post credentials or private listening/profile data.

## License

Contributions are distributed under the project's GPL-3.0-or-later license. Include attribution and compatible licensing for any third-party assets or processing data. Synthetic bundled Spatial models must not be presented as measured third-party HRTF recordings.
