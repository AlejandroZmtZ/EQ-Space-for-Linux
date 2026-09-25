"""Tests for SystemTrayManager."""

import pytest
from PySide6.QtWidgets import QApplication

from eqspace.ui.tray import SystemTrayManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


class TestSystemTrayManager:
    def test_menu_structure_with_profiles(self, qapp):
        emitted_profiles = []
        tray = SystemTrayManager(list_profiles_fn=lambda: ["harman", "bass"])
        tray.profile_selected.connect(emitted_profiles.append)

        # Profiles submenu should have 2 actions
        actions = tray.profiles_menu.actions()
        assert len(actions) == 2
        assert actions[0].text() == "harman"
        assert actions[1].text() == "bass"

        actions[0].trigger()
        assert emitted_profiles == ["harman"]

    def test_mute_and_show_hide_actions(self, qapp):
        mute_events = []
        show_hide_events = []

        tray = SystemTrayManager(list_profiles_fn=lambda: [])
        tray.mute_toggled.connect(mute_events.append)
        tray.show_hide_triggered.connect(lambda: show_hide_events.append(True))

        tray.mute_action.trigger()
        assert len(mute_events) == 1
        assert mute_events[0] is True

        tray.show_action.trigger()
        assert len(show_hide_events) == 1

    def test_empty_profiles_menu(self, qapp):
        tray = SystemTrayManager(list_profiles_fn=lambda: [])
        actions = tray.profiles_menu.actions()
        assert len(actions) == 1
        assert "(No profiles)" in actions[0].text()
        assert not actions[0].isEnabled()
