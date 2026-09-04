# EQ-Space for Linux

A parametric equalizer application for Linux (PipeWire), built with PySide6.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e .
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q
```

## Running

```bash
.venv/bin/eqspace
# or
.venv/bin/python -m eqspace
```
