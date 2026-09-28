import logging
from pathlib import Path

from eqspace.diagnostics import configure_logging, diagnostic_report, operation


def test_log_has_operation_context_and_exception(tmp_path):
    path, error = configure_logging(state_home=tmp_path, debug=True)
    assert not error
    with operation("profile", target="EQ+Spatial"):
        try:
            raise ValueError("readback mismatch")
        except ValueError:
            logging.getLogger("eqspace.test").exception("restoration failed")
    text = path.read_text()
    assert "target=EQ+Spatial" in text
    assert "operation=" in text
    assert "ValueError: readback mismatch" in text
    assert "session=" in text
    assert "readback mismatch" in diagnostic_report({"EQ": True})


def test_unwritable_log_directory_falls_back(tmp_path):
    block = tmp_path / "file"
    block.write_text("occupied")
    path, error = configure_logging(state_home=block)
    assert path is None
    assert error


def test_rotation_is_bounded(tmp_path):
    path, error = configure_logging(state_home=tmp_path)
    handler = next(h for h in logging.getLogger().handlers if hasattr(h, "maxBytes"))
    assert handler.maxBytes == 2 * 1024 * 1024
    assert handler.backupCount == 3
