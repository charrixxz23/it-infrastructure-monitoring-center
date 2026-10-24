"""Fail closed if the application adds dependency manifests CI does not audit yet."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_NAMES = {
    "requirements.txt",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "uv.lock",
}
EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", ".tox"}


def main() -> int:
    manifests = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.name in MANIFEST_NAMES
        and ".github" not in path.relative_to(ROOT).parts
        and not (set(path.relative_to(ROOT).parts) & EXCLUDED_PARTS)
    ]
    if manifests:
        print(
            "ERROR: application dependency manifests are present but are not included in the CI audit. "
            "Add each supported manifest to pip-audit before merging:",
            file=sys.stderr,
        )
        for manifest in sorted(manifests):
            print(f"- {manifest.relative_to(ROOT).as_posix()}", file=sys.stderr)
        return 1

    print(
        "PASS: no third-party Python application dependencies are declared; CI audits its pinned tooling separately."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
