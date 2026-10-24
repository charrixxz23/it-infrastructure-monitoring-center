"""Render Compose config in an isolated temporary copy with fake CI credentials."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IGNORED_NAMES = {
    ".git",
    "node_modules",
    "dist",
    ".astro",
    "coverage",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    "data",
}


def ignore_runtime_files(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in IGNORED_NAMES}
    ignored.update(name for name in names if name == ".env" or name.startswith(".env."))
    ignored.update(
        name for name in names if name.endswith((".log", ".db", ".wal", ".out"))
    )
    return ignored


def main() -> int:
    docker = shutil.which("docker")
    if docker is None:
        print(
            "ERROR: Docker CLI with Compose is required for config-only validation.",
            file=sys.stderr,
        )
        return 2

    try:
        version = subprocess.run(
            [docker, "compose", "version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print(f"ERROR: Docker Compose CLI is unavailable: {error}", file=sys.stderr)
        return 2
    print(version.stdout.strip())

    with tempfile.TemporaryDirectory(prefix="itmc-compose-ci-") as temporary_root:
        project_copy = Path(temporary_root) / "project"
        shutil.copytree(ROOT, project_copy, ignore=ignore_runtime_files)
        (project_copy / ".env").write_text(
            "GF_SECURITY_ADMIN_USER=ci-check\n"
            "GF_SECURITY_ADMIN_PASSWORD=ci-placeholder-not-a-secret\n",
            encoding="utf-8",
        )

        try:
            subprocess.run(
                [docker, "compose", "config", "--quiet"],
                cwd=project_copy,
                check=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            print(
                "ERROR: docker compose config exceeded its 30-second timeout.",
                file=sys.stderr,
            )
            return 1
        except subprocess.CalledProcessError as error:
            print(
                f"ERROR: docker compose config failed with exit code {error.returncode}.",
                file=sys.stderr,
            )
            return error.returncode or 1

    print(
        "PASS: Compose configuration rendered with a disposable CI-only .env; no services were started."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
