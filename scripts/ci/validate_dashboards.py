"""Check the provisioned Grafana dashboard JSON without requiring Grafana."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = (
    ROOT
    / "config"
    / "grafana"
    / "provisioning"
    / "dashboards"
    / "dashboard-provider.yml"
)
COMPOSE_FILE = ROOT / "compose.yaml"
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
    "home-network-monitoring.json": {
        "title": "Home Network Monitoring",
        "uid": "home-network-monitoring",
        "panels": 13,
    },
}


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate keys so JSON parser behavior cannot hide dashboard errors."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def validate_dashboard(path: Path, expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        dashboard = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
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

    if path.name == "home-network-monitoring.json":
        panels_by_id = {
            panel.get("id"): panel for panel in panels if isinstance(panel, dict)
        }
        for panel_id in (1, 2, 3, 4, 5, 6, 12, 13):
            panel = panels_by_id.get(panel_id, {})
            for target in panel.get("targets", []):
                if target.get("instant") is not True:
                    errors.append(
                        f"{relative_path}: state panel {panel_id} must use an instant query"
                    )
        expected_legends = {
            1: "Router",
            2: "External ICMP",
            3: "DNS A",
            4: "DNS AAAA",
            5: "HTTPS",
            6: "Exporter",
        }
        for panel_id, expected_legend in expected_legends.items():
            panel = panels_by_id.get(panel_id, {})
            targets = panel.get("targets", [])
            if not targets or targets[0].get("legendFormat") != expected_legend:
                errors.append(
                    f"{relative_path}: status panel {panel_id} must use the concise "
                    f"legend {expected_legend!r}"
                )

        availability = panels_by_id.get(11, {})
        availability_defaults = availability.get("fieldConfig", {}).get("defaults", {})
        availability_target = (availability.get("targets") or [{}])[0]
        if (
            availability_defaults.get("unit") != "percentunit"
            or availability_defaults.get("min") != 0
            or availability_defaults.get("max") != 1
        ):
            errors.append(
                f"{relative_path}: Probe Availability History must use a fixed 0-100% axis"
            )
        if availability_target.get("legendFormat") != "{{job}}":
            errors.append(
                f"{relative_path}: Probe Availability History must use job-only labels"
            )

        scrape_errors = panels_by_id.get(13, {})
        scrape_targets = scrape_errors.get("targets", [])
        scrape_defaults = scrape_errors.get("fieldConfig", {}).get("defaults", {})
        expected_scrape_expression = (
            'sum(up{job=~"home-router-icmp|home-internet-icmp|home-dns-a|'
            'home-dns-aaaa|home-https"} == bool 0)'
        )
        if (
            not scrape_targets
            or scrape_targets[0].get("expr") != expected_scrape_expression
            or scrape_targets[0].get("instant") is not True
        ):
            errors.append(
                f"{relative_path}: scrape errors must count failed instant scrapes "
                "with a bool comparison"
            )
        if scrape_defaults.get("noValue") != "NO DATA":
            errors.append(
                f"{relative_path}: scrape errors must display NO DATA when no series match"
            )
        for panel_id in (7, 8):
            expressions = [
                target.get("expr", "")
                for target in panels_by_id.get(panel_id, {}).get("targets", [])
            ]
            if not expressions or any(
                "probe_icmp_duration_seconds" not in expression
                or 'phase="rtt"' not in expression
                or "probe_success" not in expression
                for expression in expressions
            ):
                errors.append(
                    f"{relative_path}: ICMP latency panel {panel_id} must show successful RTT samples only"
                )
        dns_expressions = [
            target.get("expr", "")
            for target in panels_by_id.get(9, {}).get("targets", [])
        ]
        if not dns_expressions or any(
            "probe_dns_duration_seconds" not in expression
            for expression in dns_expressions
        ):
            errors.append(
                f"{relative_path}: DNS timing panel must use Blackbox DNS phase metrics"
            )
    return errors


def validate_provisioning() -> list[str]:
    errors: list[str] = []
    try:
        provider = yaml.safe_load(PROVIDER.read_text(encoding="utf-8"))
        compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        return [
            f"Grafana provisioning or Compose could not be parsed ({type(error).__name__})"
        ]

    providers = provider.get("providers", [])
    if not any(
        item.get("type") == "file"
        and item.get("options", {}).get("path") == "/var/lib/grafana/dashboards"
        for item in providers
    ):
        errors.append(
            "Grafana file provisioning must continue to load the dashboards directory"
        )

    grafana = compose.get("services", {}).get("grafana", {})
    dashboard_mount = "./config/grafana/dashboards:/var/lib/grafana/dashboards:ro"
    if dashboard_mount not in grafana.get("volumes", []):
        errors.append(
            "Compose must keep the dashboard directory mounted read-only into Grafana"
        )
    return errors


def main() -> int:
    dashboard_root = ROOT / "config" / "grafana" / "dashboards"
    errors: list[str] = []
    for filename, expected in DASHBOARDS.items():
        errors.extend(validate_dashboard(dashboard_root / filename, expected))
    errors.extend(validate_provisioning())

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
