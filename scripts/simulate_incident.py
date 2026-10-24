#!/usr/bin/env python3
"""Safely exercise one real Prometheus -> Alertmanager -> logger lifecycle."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RULE_PATH = ROOT / "config" / "prometheus" / "test-rules" / "incident-test.yml"
DISABLED = b"expr: vector(0) == 1"
ENABLED = b"expr: vector(1) == 1"
TEST_SOURCE = b"source: automated-test"
PROM = "http://127.0.0.1:9090"
ALERTMANAGER = "http://127.0.0.1:9093"
TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 0.1


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def http(url: str, method: str = "GET", data: bytes | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def json_get(url: str):
    status, body = http(url)
    if status != 200:
        raise RuntimeError(f"GET {url} returned HTTP {status}")
    return json.loads(body)


def reload_prometheus() -> datetime:
    expression = urllib.parse.urlencode({"query": "prometheus_config_last_reload_success_timestamp_seconds"})

    def reload_timestamp() -> float | None:
        response = json_get(PROM + "/api/v1/query?" + expression)
        result = response.get("data", {}).get("result", [])
        if not result:
            return None
        return float(result[0]["value"][1])

    previous_reload = reload_timestamp()
    if previous_reload is None:
        raise RuntimeError("Prometheus reload metric is unavailable")
    run_docker("kill", "-s", "HUP", "prometheus")

    def successful_reload_after_signal() -> float | None:
        current_reload = reload_timestamp()
        return current_reload if current_reload is not None and current_reload > previous_reload else None

    reloaded, reloaded_at = wait_for(
        "Prometheus to reload the changed rule file after SIGHUP",
        successful_reload_after_signal,
    )
    if reloaded <= previous_reload:
        raise RuntimeError("Prometheus did not confirm a successful configuration reload")
    return reloaded_at


def run_docker(*arguments: str) -> str:
    result = subprocess.run(
        ["docker", "compose", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError("docker compose " + " ".join(arguments) + " failed: " + (detail[-1] if detail else "unknown error"))
    return result.stdout


def logger_test_events() -> list[dict]:
    output = run_docker("exec", "-T", "incident-logger", "python", "/app/incident_logger.py", "--test-events")
    try:
        events = json.loads(output)
    except json.JSONDecodeError as error:
        raise RuntimeError("incident logger returned invalid test-event JSON") from error
    if not isinstance(events, list):
        raise RuntimeError("incident logger test events were not a list")
    return events


def prometheus_test_alerts() -> list[dict]:
    response = json_get(PROM + "/api/v1/alerts")
    return [
        alert
        for alert in response.get("data", {}).get("alerts", [])
        if alert.get("labels", {}).get("alertname") == "TestIncident"
        and alert.get("state") in {"pending", "firing"}
    ]


def prometheus_firing_sample(source: str) -> datetime | None:
    expression = f'ALERTS{{alertname="TestIncident",alertstate="firing",source="{source}"}}[5m]'
    query = urllib.parse.urlencode({"query": expression})
    response = json_get(PROM + "/api/v1/query?" + query)
    samples = [
        float(sample[0])
        for series in response.get("data", {}).get("result", [])
        for sample in series.get("values", [])
        if len(sample) >= 2 and sample[1] == "1"
    ]
    if not samples:
        return None
    return datetime.fromtimestamp(min(samples), timezone.utc)


def alertmanager_test_alerts() -> list[dict]:
    query = urllib.parse.urlencode({"active": "true", "silenced": "false", "inhibited": "false"})
    alerts = json_get(ALERTMANAGER + "/api/v2/alerts?" + query)
    return [alert for alert in alerts if alert.get("labels", {}).get("alertname") == "TestIncident"]


def wait_for(description: str, callback, timeout: int = TIMEOUT_SECONDS, interval: float = POLL_INTERVAL_SECONDS):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = callback()
            if result:
                return result, now()
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            last_error = error
        time.sleep(interval)
    detail = f"; last error: {last_error}" if last_error else ""
    raise TimeoutError(f"Timed out waiting for {description}{detail}")


def verify_initial_health() -> dict:
    status, _ = http(PROM + "/-/healthy")
    if status != 200:
        raise RuntimeError("Prometheus is not healthy")
    targets = json_get(PROM + "/api/v1/targets").get("data", {}).get("activeTargets", [])
    for job in ("prometheus", "node-exporter"):
        matching = [target for target in targets if target.get("labels", {}).get("job") == job]
        if not matching or any(target.get("health") != "up" for target in matching):
            raise RuntimeError(f"Prometheus target {job} is missing or not UP")
    status, _ = http(ALERTMANAGER + "/-/healthy")
    if status != 200:
        raise RuntimeError("Alertmanager is not healthy")
    status, _ = http("http://127.0.0.1:3000/api/health")
    if status != 200:
        raise RuntimeError("Grafana is not healthy")
    run_docker("exec", "-T", "incident-logger", "python", "/app/incident_logger.py", "--healthcheck")
    rules = json_get(PROM + "/api/v1/rules?type=alert").get("data", {}).get("groups", [])
    all_rules = [rule for group in rules for rule in group.get("rules", [])]
    test_rule = next((rule for rule in all_rules if rule.get("name") == "TestIncident"), None)
    if not test_rule or test_rule.get("query") != "vector(0) == 1" or test_rule.get("activeAlerts"):
        raise RuntimeError("TestIncident must be loaded and disabled before the test")
    if prometheus_test_alerts() or alertmanager_test_alerts():
        raise RuntimeError("A previous TestIncident is still active; resolve it before starting another test")
    return {"prometheus": "healthy", "grafana": "healthy", "alertmanager": "healthy", "incidentLogger": "healthy", "nodeExporterTarget": "up"}


def latest_event(events: list[dict], fingerprint: str, status: str) -> dict | None:
    matches = [event for event in events if event.get("fingerprint") == fingerprint and event.get("status") == status]
    return matches[-1] if matches else None


def main() -> int:
    report: dict = {"project": "IT Infrastructure Monitoring Center", "test": "TestIncident FIRING -> RESOLVED", "startedAt": iso(), "status": "failed"}
    original_bytes = RULE_PATH.read_bytes()
    test_source_bytes: bytes | None = None
    dynamic_source: str | None = None
    failure: Exception | None = None
    modified = False
    firing_fingerprint: str | None = None

    try:
        report["initialHealth"] = verify_initial_health()
        if original_bytes.count(DISABLED) != 1 or ENABLED in original_bytes:
            raise RuntimeError("Dedicated test rule is not in its expected disabled state")

        report["activationRequestedAt"] = iso()
        test_run = now().strftime("%Y%m%dT%H%M%S%fZ")
        report["testRun"] = test_run
        dynamic_source = "automated-test-" + test_run
        if original_bytes.count(TEST_SOURCE) != 1:
            raise RuntimeError("Dedicated test rule is missing its expected source label")
        test_source_bytes = original_bytes.replace(TEST_SOURCE, ("source: " + dynamic_source).encode("ascii"), 1)
        test_rule_bytes = test_source_bytes.replace(DISABLED, ENABLED, 1)
        modified = True
        RULE_PATH.write_bytes(test_rule_bytes)
        report["activationReloadAcceptedAt"] = iso(reload_prometheus())

        prom_alert, prom_observed = wait_for(
            "Prometheus TestIncident FIRING",
            lambda: next((alert for alert in prometheus_test_alerts() if alert.get("state") == "firing"), None),
        )
        report["prometheusFiringObservedAt"] = iso(prom_observed)
        firing_at, _ = wait_for(
            "the first Prometheus ALERTS firing sample",
            lambda: prometheus_firing_sample(dynamic_source),
        )
        report["prometheusFiringAt"] = iso(firing_at)
        report["prometheusFiringTimestampSource"] = "first raw ALERTS{alertstate=\"firing\"} sample from Prometheus"
        report["prometheusDetectionMethod"] = "first API observation of FIRING; polled every 100 ms"
        report["prometheusActiveAt"] = prom_alert.get("activeAt")
        report["prometheusFiringDetectionSeconds"] = round(
            (firing_at - parse_time(report["activationRequestedAt"])).total_seconds(), 3
        )

        am_alert, am_observed = wait_for(
            "Alertmanager TestIncident active",
            lambda: next(iter(alertmanager_test_alerts()), None),
        )
        firing_fingerprint = am_alert.get("fingerprint")
        if not firing_fingerprint:
            raise RuntimeError("Alertmanager API did not return the alert fingerprint")
        report["alertmanagerFiringObservedAt"] = iso(am_observed)

        firing_event, _ = wait_for(
            "the FIRING webhook event in incident logger",
            lambda: latest_event(logger_test_events(), firing_fingerprint, "FIRING"),
        )
        report["incidentLoggerFiringReceivedAt"] = firing_event.get("receivedAt")
        delivery_delay = (parse_time(firing_event["receivedAt"]) - firing_at).total_seconds()
        if delivery_delay < 0:
            raise RuntimeError("incident logger timestamp preceded the Prometheus FIRING observation; timing was not reliable")
        report["firingNotificationAfterPrometheusSeconds"] = round(delivery_delay, 3)
        report["firingEndToEndSeconds"] = round(
            (parse_time(firing_event["receivedAt"]) - parse_time(report["activationRequestedAt"])).total_seconds(), 3
        )
        report["fingerprint"] = firing_fingerprint

    except Exception as error:
        failure = error

    finally:
        if modified:
            try:
                report["resolutionRequestedAt"] = iso()
                # Keep the firing labels unchanged until Prometheus sends RESOLVED;
                # changing labels first creates a different alert fingerprint.
                RULE_PATH.write_bytes(test_source_bytes or original_bytes)
                report["resolutionReloadAcceptedAt"] = iso(reload_prometheus())
                _, prom_resolved_observed = wait_for(
                    "Prometheus to clear the controlled alert",
                    lambda: not prometheus_test_alerts(),
                )
                report["prometheusResolvedObservedAt"] = iso(prom_resolved_observed)

                if not firing_fingerprint:
                    recent_firings = [
                        event
                        for event in logger_test_events()
                        if event.get("status") == "FIRING"
                        and event.get("labels", {}).get("source") == dynamic_source
                        and event.get("receivedAt", "") >= report.get("activationRequestedAt", "")
                    ]
                    if recent_firings:
                        firing_fingerprint = recent_firings[-1].get("fingerprint")
                        report["fingerprint"] = firing_fingerprint

                _, am_resolved_observed = wait_for(
                    "Alertmanager to clear the controlled alert",
                    lambda: not alertmanager_test_alerts(),
                )
                report["alertmanagerInactiveObservedAt"] = iso(am_resolved_observed)

                if firing_fingerprint:
                    resolved_event, _ = wait_for(
                        "the RESOLVED webhook event in incident logger",
                        lambda: latest_event(logger_test_events(), firing_fingerprint, "RESOLVED"),
                    )
                    report["incidentLoggerResolvedReceivedAt"] = resolved_event.get("receivedAt")
                    if resolved_event.get("endsAt"):
                        report["prometheusResolvedAt"] = resolved_event["endsAt"]
                        report["resolutionNotificationAfterPrometheusSeconds"] = round(
                            (parse_time(resolved_event["receivedAt"]) - parse_time(resolved_event["endsAt"])).total_seconds(), 3
                        )
                    report["resolutionEndToEndSeconds"] = round(
                        (parse_time(resolved_event["receivedAt"]) - parse_time(report["resolutionRequestedAt"])).total_seconds(), 3
                    )
                else:
                    raise RuntimeError("No FIRING fingerprint was available to verify the RESOLVED delivery")
            except Exception as cleanup_error:
                if failure is None:
                    failure = cleanup_error
                else:
                    report["cleanupError"] = str(cleanup_error)
            finally:
                try:
                    if RULE_PATH.read_bytes() != original_bytes:
                        RULE_PATH.write_bytes(original_bytes)
                        report["testRuleRestoreReloadAcceptedAt"] = iso(reload_prometheus())
                    report["testRuleRestored"] = RULE_PATH.read_bytes() == original_bytes
                    if not report["testRuleRestored"]:
                        raise RuntimeError("Dedicated TestIncident rule was not restored byte-for-byte")
                except Exception as restore_error:
                    report["testRuleRestored"] = False
                    if failure is None:
                        failure = restore_error
                    else:
                        report["restoreError"] = str(restore_error)

    report["finishedAt"] = iso()
    if failure is None and report.get("testRuleRestored") and all(key in report for key in ("prometheusFiringObservedAt", "alertmanagerFiringObservedAt", "incidentLoggerFiringReceivedAt", "prometheusResolvedObservedAt", "alertmanagerInactiveObservedAt", "incidentLoggerResolvedReceivedAt")):
        report["status"] = "passed"
    else:
        report["error"] = str(failure or "incident lifecycle was not fully verified")

    reports_dir = ROOT / "data"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / ("incident-test-" + now().strftime("%Y%m%dT%H%M%SZ") + ".json")
    report["reportFile"] = str(report_path.relative_to(ROOT)).replace("\\", "/")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
