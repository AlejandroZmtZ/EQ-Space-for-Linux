"""Regression checks for the public repository's privacy guard."""

import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "public_tree", Path(__file__).resolve().parents[1] / "tools/check-public-tree.py",
)
public_tree = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public_tree)


def test_demo_data_and_portable_paths_are_public(tmp_path):
    (tmp_path / "README.md").write_text("# Current guide\nProfiles: ~/.config/eqspace/profiles/\n")
    fixture = tmp_path / "tests/fixtures/demo.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('{"info": {"user-name": "demo-user", "host-name": "demo-host"}}')
    assert not public_tree.check(tmp_path, ["README.md", "tests/fixtures/demo.json"])


def test_private_notes_are_rejected_even_when_gitignored(tmp_path):
    file = tmp_path / "docs/internal/listening.md"
    file.parent.mkdir(parents=True)
    file.write_text("# Private listening notes\n")
    (tmp_path / ".gitignore").write_text("docs/internal/\n")
    problems = public_tree.check(tmp_path, ["docs/internal/listening.md"])
    assert any("private artifact is tracked" in problem for problem in problems)


def test_nested_identity_paths_credentials_and_planning_are_rejected(tmp_path):
    fixture = tmp_path / "tests/fixtures/demo.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('{"info": {"host-name": "personal-machine"}}')
    content = "# Research roadmap\n" + "/home/" + "example-person/private.json\n"
    content += "ghp_" + "x" * 36
    (tmp_path / "README.md").write_text(content)
    problems = public_tree.check(tmp_path, ["README.md", "tests/fixtures/demo.json"])
    assert len(problems) == 4
