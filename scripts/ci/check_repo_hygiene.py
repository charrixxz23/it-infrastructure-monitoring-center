"""Check Git ignores expected local-only files and no generated/private artifacts are tracked."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
IGNORE_PROBES = [
    ".env",
    ".env.local",
    ".secrets/placeholder.txt",
    "data/incident-report.json",
    "services/example/__pycache__/module.pyc",
    ".venv/pyvenv.cfg",
    ".ruff_cache/cache",
    "config/prometheus/targets/home-network.yml",
    "example.log",
    "example.db",
    "example.tmp",
    ".coverage",
    "htmlcov/index.html",
]
TRACKED_ARTIFACT_DIRS = {
    "data",
    ".secrets",
    ".venv",
    "venv",
    "__pycache__",
    ".ruff_cache",
}
TRACKED_ARTIFACT_SUFFIXES = {
    ".db",
    ".wal",
    ".log",
    ".out",
    ".tmp",
    ".temp",
    ".bak",
    ".pyc",
}


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
        timeout=20,
    )


def main() -> int:
    errors: list[str] = []
    ignore_file = ROOT / ".gitignore"
    if not ignore_file.is_file():
        print("ERROR: .gitignore is missing.", file=sys.stderr)
        return 1

    for probe in IGNORE_PROBES:
        result = git("check-ignore", "--no-index", "-q", "--", probe)
        if result.returncode != 0:
            errors.append(f".gitignore does not exclude {probe}")

    if (
        git(
            "check-ignore", "--no-index", "-q", "--", ".env.example", check=False
        ).returncode
        == 0
    ):
        errors.append(".gitignore unexpectedly excludes the safe .env.example template")

    candidates = git(
        "ls-files", "--cached", "--others", "--exclude-standard", "-z"
    ).stdout.split("\0")
    candidates = [PurePosixPath(item) for item in candidates if item]
    for path in candidates:
        parts = set(path.parts)
        name = path.name.lower()
        if name == ".env.example":
            continue
        if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
            errors.append(f"tracked environment file: {path.as_posix()}")
        if (
            parts & TRACKED_ARTIFACT_DIRS
            or path.suffix.lower() in TRACKED_ARTIFACT_SUFFIXES
        ):
            errors.append(f"tracked local/runtime artifact: {path.as_posix()}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"PASS: .gitignore excludes {len(IGNORE_PROBES)} secret/runtime/cache probes; "
        f"{len(candidates)} Git candidate files contain no known local-only artifact paths."
    )
    print(
        "Note: secret-content detection is handled separately by the redacted Gitleaks scan."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
