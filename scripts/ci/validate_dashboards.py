"""Check the provisioned Grafana dashboard JSON without requiring Grafana."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = {
    "infrastructure-overview.json": {
        "title": "IT Infrastructure Overview",
        "uid": "it-infra-overview",
        "panels": 14,
    },
    "docker-container-monitoring.json": {
        "title": "Docker Container Monitoring",
        "uid": "docker-container-monitoring",
        "panels": 12,
    },
}


def validate_dashboard(path: Path, expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        dashboard = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"{path.relative_to(ROOT)}: invalid or unreadable JSON ({error})"]

    relative_path = path.relative_to(ROOT)
    if dashboard.get("title") != expected["title"]:
        errors.append(f"{relative_path}: unexpected dashboard title")
    if dashboard.get("uid") != expected["uid"]:
        errors.append(f"{relative_path}: unexpected dashboard UID")

    panels = dashboard.get("panels")
    if not isinstance(panels, list):
        return errors + [f"{relative_path}: panels must be an array"]
    if len(panels) != expected["panels"]:
        errors.append(
            f"{relative_path}: expected {expected['panels']} panels, found {len(panels)}"
        )

    panel_ids: set[int] = set()
    for index, panel in enumerate(panels):
        panel_label = f"{relative_path}: panel #{index + 1}"
        title = panel.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{panel_label} has no readable title")

        panel_id = panel.get("id")
        if not isinstance(panel_id, int) or panel_id <= 0:
            errors.append(f"{panel_label} has no positive integer ID")
        elif panel_id in panel_ids:
            errors.append(f"{panel_label} duplicates panel ID {panel_id}")
        else:
            panel_ids.add(panel_id)

        targets = panel.get("targets")
        if not isinstance(targets, list) or not targets:
            errors.append(f"{panel_label} has no query targets")
            continue
        for target in targets:
            datasource = target.get("datasource") or {}
            if datasource.get("type") != "grafana":
                expression = target.get("expr")
                if not isinstance(expression, str) or not expression.strip():
                    errors.append(f"{panel_label} contains an empty query expression")

    if not isinstance(dashboard.get("schemaVersion"), int):
        errors.append(f"{relative_path}: missing Grafana schemaVersion")
    return errors


def main() -> int:
    dashboard_root = ROOT / "config" / "grafana" / "dashboards"
    errors: list[str] = []
    for filename, expected in DASHBOARDS.items():
        errors.extend(validate_dashboard(dashboard_root / filename, expected))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    for filename, expected in DASHBOARDS.items():
        print(f"PASS: {expected['title']} ({expected['panels']} panels)")
    print("PASS: dashboard JSON structure, IDs, and Prometheus query targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
