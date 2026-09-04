import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_app_module_imports():
    import eqspace.app

    assert callable(eqspace.app.main)


def test_main_window_constructs(qapp):
    from eqspace.ui.main_window import MainWindow

    window = MainWindow()
    assert window.windowTitle() == "EQ-Space"
