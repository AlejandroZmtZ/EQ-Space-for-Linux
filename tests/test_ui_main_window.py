import pytest

from eqspace.core.pipewire.registry import PwSnapshot


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakeRegistry:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot or PwSnapshot()

    def snapshot(self):
        return self._snapshot


def test_main_window_constructs_with_tabs(qapp):
    from eqspace.ui.main_window import MainWindow

    window = MainWindow(registry=FakeRegistry(), poll_interval_ms=0)
    assert window.windowTitle() == "EQ-Space"
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert titles == ["Mixer", "Parametric EQ", "Presets", "Spatial", "Mic"]
    assert window.styleSheet() != ""


def test_main_window_defaults_construct(qapp):
    # Smoke: no-arg construction must not crash even without PipeWire running.
    from eqspace.ui.main_window import MainWindow

    window = MainWindow(poll_interval_ms=0)
    assert window.tabs.count() == 5


def test_peq_tab_present_with_plot(qapp):
    from eqspace.ui.main_window import MainWindow

    window = MainWindow(registry=FakeRegistry(), poll_interval_ms=0)
    assert window.peq.plot is not None
    assert len(window.peq.bands) == 1
