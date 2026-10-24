"""Scan only Git candidate files, excluding ignored local secrets and runtime data."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gitleaks", default="gitleaks", help="path to pinned Gitleaks binary"
    )
    args = parser.parse_args()

    try:
        candidates = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=20,
        ).stdout.split(b"\0")
    except (OSError, subprocess.SubprocessError) as error:
        print(
            f"ERROR: could not enumerate Git candidate files: {error}", file=sys.stderr
        )
        return 2

    with tempfile.TemporaryDirectory(prefix="itmc-gitleaks-") as temporary_root:
        scan_root = Path(temporary_root)
        copied = 0
        for candidate in candidates:
            if not candidate:
                continue
            relative_path = Path(*PurePosixPath(candidate.decode("utf-8")).parts)
            source = ROOT / relative_path
            if not source.is_file() or source.is_symlink():
                continue
            destination = scan_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied += 1

        print(
            f"Scanning {copied} Git candidate files with Gitleaks; findings are fully redacted.",
            flush=True,
        )
        try:
            result = subprocess.run(
                [args.gitleaks, "dir", "--redact=100", "--exit-code=1", "."],
                cwd=scan_root,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            print(
                f"ERROR: Gitleaks could not complete the scan: {error}", file=sys.stderr
            )
            return 2

    if result.returncode:
        print(
            "ERROR: Gitleaks reported a candidate secret or failed to scan.",
            file=sys.stderr,
        )
        return result.returncode

    print("PASS: no Gitleaks findings in Git candidate files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
