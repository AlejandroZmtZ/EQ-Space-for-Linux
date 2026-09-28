"""Persistent, bounded diagnostics for playback operations."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
import logging
import os
from pathlib import Path
import platform
import time
import uuid
import subprocess
from importlib import metadata

_session = uuid.uuid4().hex[:12]
_operation = ContextVar("eqspace_operation", default="-")
_log_path: Path | None = None
_log_error = ""
_versions: dict[str, str] = {}


class _Context(logging.Filter):
    def filter(self, record):
        record.session = _session
        record.operation = _operation.get()
        return True


def configure_logging(*, state_home: Path | None = None, debug: bool = False):
    """Configure once per launch; disk failures never prevent startup."""
    global _log_path, _log_error
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_eqspace", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s session=%(session)s operation=%(operation)s %(name)s: %(message)s")
    handlers = [logging.StreamHandler()]
    _log_error = ""
    _log_path = None
    try:
        base = state_home or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        directory = base / "eqspace/logs"
        directory.mkdir(parents=True, exist_ok=True)
        candidate = directory / "eqspace.log"
        handlers.append(RotatingFileHandler(candidate, maxBytes=2 * 1024 * 1024,
                                            backupCount=3, encoding="utf-8"))
        _log_path = candidate
    except OSError as exc:
        _log_error = f"Persistent logging unavailable: {exc}"
    for handler in handlers:
        handler._eqspace = True
        handler.addFilter(_Context())
        handler.setFormatter(formatter)
        root.addHandler(handler)
    for package in ("eqspace", "PySide6"):
        try:
            _versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            if package == "PySide6":
                try:
                    import PySide6
                    _versions[package] = PySide6.__version__
                except ImportError:
                    _versions[package] = "unavailable"
            else:
                _versions[package] = "unknown"
    for executable in ("pipewire", "wireplumber"):
        try:
            result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=1)
            _versions[executable] = (result.stdout or result.stderr).strip()[:300]
        except (OSError, subprocess.SubprocessError):
            _versions[executable] = "unavailable"
    logging.getLogger(__name__).info("Started Python=%s platform=%s versions=%s", platform.python_version(), platform.platform(), _versions)
    if _log_error:
        logging.getLogger(__name__).warning(_log_error)
    return _log_path, _log_error


@contextmanager
def operation(label: str, **details):
    token = _operation.set(uuid.uuid4().hex[:12])
    logger = logging.getLogger("eqspace.playback")
    started = time.monotonic()
    logger.info("Begin %s %s", label, " ".join(f"{k}={v}" for k, v in details.items()))
    try:
        yield _operation.get()
    except Exception:
        logger.exception("Failed %s duration_ms=%.1f", label, (time.monotonic() - started) * 1000)
        raise
    else:
        logger.info("Completed %s duration_ms=%.1f", label, (time.monotonic() - started) * 1000)
    finally:
        _operation.reset(token)


def log_location():
    return _log_path, _log_error


def diagnostic_report(state: dict | None = None) -> str:
    text = ""
    if _log_path:
        try:
            with _log_path.open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 32000))
                text = stream.read().decode("utf-8", errors="replace")
        except OSError as exc:
            text = str(exc)
    return (f"EQ-Space diagnostics\nSession: {_session}\nPython: {platform.python_version()}\n"
            f"Platform: {platform.platform()}\nVersions: {_versions}\nState: {state or {}}\n"
            f"Log: {_log_path or _log_error or 'not configured'}\n\n{text}")
