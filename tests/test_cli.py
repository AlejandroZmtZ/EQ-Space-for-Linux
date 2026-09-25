"""The command line only lists profiles; applying requires a running GUI."""

import pytest

from eqspace.app import cmd_list_profiles, main


def test_list_profiles(capsys, monkeypatch):
    monkeypatch.setattr("eqspace.app.storage.list_profiles", lambda: ["alpha", "beta"])
    assert cmd_list_profiles() == 0
    assert capsys.readouterr().out.splitlines() == ["alpha", "beta"]


def test_empty_profiles(capsys, monkeypatch):
    monkeypatch.setattr("eqspace.app.storage.list_profiles", lambda: [])
    assert main(["--list-profiles"]) == 0
    assert "No profiles" in capsys.readouterr().out


def test_help_is_gui_first(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--list-profiles" in out
    assert "--apply-profile" not in out
    assert "--restore-last" not in out


@pytest.mark.parametrize("option", ["--apply-profile", "--restore-last", "--no-spatial", "--no-mic"])
def test_removed_headless_options_rejected(option):
    with pytest.raises(SystemExit) as exc:
        main([option])
    assert exc.value.code == 2
