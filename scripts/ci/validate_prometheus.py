"""Run Prometheus checks against the exact tool and source config without contacting services."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
PROMETHEUS_CONFIG = ROOT / "config" / "prometheus" / "prometheus.yml"
RULE_FILES = [
    ROOT / "config" / "prometheus" / "rules" / "infrastructure.yml",
    ROOT / "config" / "prometheus" / "test-rules" / "incident-test.yml",
]
RULE_TEST = ROOT / "config" / "prometheus" / "tests" / "infrastructure.test.yml"


def run(command: list[str]) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    subprocess.run(command, check=True, timeout=60)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--promtool", default="promtool", help="path to the pinned promtool binary"
    )
    args = parser.parse_args()

    promtool = shutil.which(args.promtool)
    if promtool is None:
        print(
            "ERROR: promtool was not found; install the project-pinned Prometheus version.",
            file=sys.stderr,
        )
        return 2

    try:
        config = yaml.safe_load(PROMETHEUS_CONFIG.read_text(encoding="utf-8"))
        config["rule_files"] = [path.as_posix() for path in RULE_FILES]
    except (OSError, yaml.YAMLError, TypeError, KeyError) as error:
        print(
            f"ERROR: could not prepare a temporary Prometheus config: {error}",
            file=sys.stderr,
        )
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix="itmc-promtool-ci-") as temporary_root:
            temporary_config = Path(temporary_root) / "prometheus.yml"
            temporary_config.write_text(
                yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
            )
            run([promtool, "check", "config", str(temporary_config)])
        run([promtool, "check", "rules", *(str(path) for path in RULE_FILES)])
        run([promtool, "test", "rules", str(RULE_TEST)])
    except subprocess.TimeoutExpired as error:
        print(f"ERROR: promtool timed out: {error.cmd}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(
            f"ERROR: promtool returned exit code {error.returncode}.", file=sys.stderr
        )
        return error.returncode or 1

    print(
        "PASS: Prometheus config, rules, and rule tests; no service was reloaded or modified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
