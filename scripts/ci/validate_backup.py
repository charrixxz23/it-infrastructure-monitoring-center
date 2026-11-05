"""Static safety checks for the opt-in ITMC backup preparation tools."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ERRORS: list[str] = []


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        ERRORS.append(
            f"Required backup documentation or tooling is missing: {relative}"
        )
        return ""
    return path.read_text(encoding="utf-8")


def main() -> int:
    compose = read("compose.yaml")
    helper = read("scripts/backup/backup_itmc.py")
    backup_ps = read("scripts/backup-itmc.ps1")
    inventory_ps = read("scripts/inventory-itmc.ps1")
    workflow = read(".github/workflows/ci.yml")
    gitignore = read(".gitignore")
    guide = read("docs/backup-recovery.md")
    runbook = read("docs/runbooks/disaster-recovery.md")
    read("scripts/backup/test_backup_itmc.py")

    expected_volumes = (
        "itmc-prometheus-data",
        "itmc-grafana-data",
        "itmc-alertmanager-data",
        "itmc-incident-log-data",
    )
    for volume in expected_volumes:
        if compose.count(volume) != 1 or volume not in helper:
            ERRORS.append(
                f"Expected persistent volume inventory does not match Compose: {volume}"
            )

    if "--web.enable-admin-api" in compose:
        ERRORS.append("Prometheus administrative API must remain disabled.")
    if not re.search(r"\[string\]\$Mode\s*=\s*'Plan'", backup_ps):
        ERRORS.append("PowerShell backup mode must default to read-only Plan.")
    for required in (
        "backup-config",
        "--confirm-encrypted-destination",
        "verify",
        "inventory",
    ):
        if required not in helper:
            ERRORS.append(
                f"Backup helper is missing required explicit operation: {required}"
            )
    for forbidden in (
        '"stop"',
        '"restart"',
        '"rm"',
        '"prune"',
        '"exec"',
        '"run"',
        ".Mountpoint",
        ".Config.Env",
    ):
        if forbidden in helper:
            ERRORS.append(
                "Backup inventory contains a forbidden Docker operation or sensitive field."
            )
            break
    if "docker" not in inventory_ps.casefold() or "inventory" not in inventory_ps:
        ERRORS.append(
            "The PowerShell inventory entrypoint does not call the read-only inventory helper."
        )
    for ignored in (".env", ".secrets/", "data/"):
        if ignored not in gitignore:
            ERRORS.append(f"Expected local-only path is not ignored: {ignored}")
    if (
        "python -m unittest discover -s scripts/backup -p 'test_*.py' -v"
        not in workflow
    ):
        ERRORS.append("CI does not run the synthetic backup tests.")
    if "python scripts/ci/validate_backup.py" not in workflow:
        ERRORS.append("CI does not run the backup static validator.")
    for phrase in (
        "no es un respaldo integral",
        "no archivar tsdb/wal activos",
        "no copiar directamente el archivo sqlite activo",
        "api de snapshot administrativa sigue deshabilitada",
    ):
        if phrase not in f"{guide}\n{runbook}".casefold():
            ERRORS.append(
                "Backup documentation is missing a required consistency or scope limitation."
            )
    if (
        "sha256" not in helper
        or "created_at_utc" not in helper
        or "git_commit" not in helper
    ):
        ERRORS.append(
            "The backup manifest is missing timestamp, commit, or SHA-256 metadata."
        )

    if ERRORS:
        for error in ERRORS:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: fixed volume inventory, read-only Docker inspection, Plan-by-default, secret exclusions, "
        "manifest integrity fields, consistency warnings, and synthetic CI coverage are present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
