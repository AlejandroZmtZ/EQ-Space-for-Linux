"""Tests for packaging, desktop entry, icon, and license files."""

from pathlib import Path


def test_desktop_file_exists_and_valid():
    repo_root = Path(__file__).resolve().parent.parent
    desktop_file = repo_root / "data" / "desktop" / "eqspace.desktop"
    assert desktop_file.is_file()

    content = desktop_file.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in content
    assert "Name=EQ-Space" in content
    assert "Exec=eqspace" in content
    assert "Icon=eqspace" in content
    assert "Categories=" in content


def test_svg_icon_exists_and_valid():
    repo_root = Path(__file__).resolve().parent.parent
    icon_file = repo_root / "data" / "icons" / "eqspace.svg"
    assert icon_file.is_file()

    content = icon_file.read_text(encoding="utf-8")
    assert "<svg" in content
    assert "</svg>" in content


def test_package_icon_and_synthetic_model_present():
    from importlib import resources
    data = resources.files("eqspace.data")
    assert (data / "icons" / "eqspace.svg").is_file()
    assert (data / "hrtf" / "kemar_default.sofa").is_file()


def test_license_and_ci_present():
    repo_root = Path(__file__).resolve().parent.parent
    assert "GNU GENERAL PUBLIC LICENSE" in (repo_root / "LICENSE").read_text()
    assert "3.12" in (repo_root / ".github/workflows/python.yml").read_text()
