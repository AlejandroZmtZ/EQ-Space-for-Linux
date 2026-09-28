#!/usr/bin/env python3
"""Check tracked public files for common private-artifact and data leaks."""

import json
from pathlib import Path
import re
import subprocess


PRIVATE_PREFIXES = (
    ".agents/", ".codex/", ".superpowers/", ".vscode/",
    "private/", "profiles/", "logs/", "docs/internal/",
    "docs/plans/", "docs/research/", "docs/superpowers/",
)
PRIVATE_FILES = {
    "AGENTS.md", "GEMINI.md", "CLAUDE.md",
    "docs/agent-lessons.md", "docs/mixer-review.md", "docs/verification.md",
}
PERSONAL_PATH = re.compile(r"(?:/home/|/Users/)[A-Za-z0-9_.-]+")
SECRET = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
PRIVATE_HEADING = re.compile(
    r"^#{1,6}\s+.*(?:implementation plan|research plan|research roadmap|patent strategy|strategic plan)",
    re.IGNORECASE | re.MULTILINE,
)
DEMO_IDENTITIES = {"demo-user", "demo-host", "test-user", "test-host", "example", "localhost"}


def private_path(name: str) -> bool:
    return (
        name.startswith(PRIVATE_PREFIXES) or name in PRIVATE_FILES
        or name.endswith((".code-workspace", ".log"))
        or Path(name).name == ".env" or Path(name).name.startswith(".env.")
    )


def fixture_identities(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("user-name", "host-name") and child not in DEMO_IDENTITIES:
                yield key
            yield from fixture_identities(child)
    elif isinstance(value, list):
        for child in value:
            yield from fixture_identities(child)


def check(root: Path, names: list[str]) -> list[str]:
    problems = []
    for name in names:
        if private_path(name):
            problems.append(f"{name}: private artifact is tracked")
        file = root / name
        if not file.is_file():
            problems.append(f"{name}: tracked file is missing")
            continue
        try:
            content = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if PERSONAL_PATH.search(content):
            problems.append(f"{name}: contains a personal absolute home path")
        if SECRET.search(content):
            problems.append(f"{name}: contains a credential or private-key pattern")
        if file.suffix == ".md" and PRIVATE_HEADING.search(content):
            problems.append(f"{name}: contains a private planning heading")
        if name.startswith("tests/fixtures/") and file.suffix == ".json":
            for key in fixture_identities(json.loads(content)):
                problems.append(f"{name}: non-demo {key} in fixture")
    return problems


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z"], text=True,
    ).strip("\0").split("\0")
    problems = check(root, names)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"Public-tree check passed for {len(names)} tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
