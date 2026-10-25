"""Validate the allowlisted, least-privilege home-network probe configuration."""

from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
PROMETHEUS_CONFIG = ROOT / "config" / "prometheus" / "prometheus.yml"
BLACKBOX_CONFIG = ROOT / "config" / "blackbox" / "blackbox.yml"
COMPOSE_CONFIG = ROOT / "compose.yaml"
ALERTMANAGER_CONFIG = ROOT / "config" / "alertmanager" / "alertmanager.yml"
LOCAL_TARGET = ROOT / "config" / "prometheus" / "targets" / "home-network.yml"
TARGET_TEMPLATE = (
    ROOT / "config" / "prometheus" / "targets" / "home-network.example.yml"
)
EXPECTED_PROBES = {
    "home-router-icmp": {
        "module": "icmp_ipv4",
        "kind": "file",
        "probe_name": "home-router",
    },
    "home-internet-icmp": {
        "module": "icmp_ipv4",
        "kind": "static",
        "targets": ["1.1.1.1"],
        "probe_name": "public-ip",
    },
    "home-dns-a": {
        "module": "dns_a_example",
        "kind": "static",
        "targets": ["1.1.1.1:53"],
        "probe_name": "public-dns-a",
    },
    "home-dns-aaaa": {
        "module": "dns_aaaa_example",
        "kind": "static",
        "targets": ["1.1.1.1:53"],
        "probe_name": "public-dns-aaaa",
    },
    "home-https": {
        "module": "https_ipv4",
        "kind": "static",
        "targets": ["https://example.com"],
        "probe_name": "public-https-example",
    },
}
EXPECTED_MODULES = {probe["module"] for probe in EXPECTED_PROBES.values()}
IPV4_PATTERN = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a YAML mapping")
    return value


def check_local_target() -> list[str]:
    errors: list[str] = []
    try:
        ignored = subprocess.run(
            [
                "git",
                "check-ignore",
                "-q",
                "--",
                LOCAL_TARGET.relative_to(ROOT).as_posix(),
            ],
            cwd=ROOT,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ["could not verify the local target ignore rule"]
    if ignored.returncode != 0:
        errors.append("the local router target file is not ignored by Git")

    if not LOCAL_TARGET.exists():
        return errors
    try:
        groups = yaml.safe_load(LOCAL_TARGET.read_text(encoding="utf-8"))
        if not isinstance(groups, list) or len(groups) != 1:
            return errors + [
                "the local router file must define exactly one target group"
            ]
        group = groups[0]
        targets = group.get("targets") if isinstance(group, dict) else None
        labels = group.get("labels") if isinstance(group, dict) else None
        if not isinstance(targets, list) or len(targets) != 1:
            errors.append("the local router file must define exactly one address")
        else:
            address = str(targets[0])
            try:
                parsed = ipaddress.ip_address(address)
                if not parsed.is_private:
                    errors.append(
                        "the local router target must be a private IP address"
                    )
            except ValueError:
                errors.append("the local router target must be a literal IP address")
        if labels != {"probe_name": "home-router"}:
            errors.append(
                "the local router target may contain only the generic probe_name label"
            )
    except (OSError, yaml.YAMLError, AttributeError):
        errors.append("the ignored local router target file is not valid YAML")

    try:
        example = yaml.safe_load(TARGET_TEMPLATE.read_text(encoding="utf-8"))
        example_target = example[0]["targets"]
        example_labels = example[0]["labels"]
        documentation_network = ipaddress.ip_network("192.0.2.0/24")
        if (
            len(example_target) != 1
            or ipaddress.ip_address(str(example_target[0])) not in documentation_network
            or example_labels != {"probe_name": "home-router"}
        ):
            errors.append(
                "the tracked router template must use one TEST-NET placeholder and a generic label"
            )
    except (OSError, yaml.YAMLError, KeyError, IndexError, TypeError, ValueError):
        errors.append(
            "the tracked router target template must be valid YAML with a TEST-NET placeholder"
        )
    return errors


def check_prometheus_config() -> list[str]:
    errors: list[str] = []
    try:
        config = load_yaml(PROMETHEUS_CONFIG)
    except (OSError, yaml.YAMLError, ValueError) as error:
        return [f"Prometheus config could not be read ({type(error).__name__})"]

    jobs = {
        item.get("job_name"): item
        for item in config.get("scrape_configs", [])
        if isinstance(item, dict)
    }
    expected_jobs = {"blackbox-exporter", *EXPECTED_PROBES}
    if not expected_jobs.issubset(jobs):
        errors.append("one or more required Blackbox scrape jobs are missing")
    probe_jobs = {
        name
        for name, job in jobs.items()
        if name.startswith("home-") or job.get("metrics_path") == "/probe"
    }
    if probe_jobs != EXPECTED_PROBES.keys():
        errors.append("Prometheus must define only the reviewed home probe jobs")
    exporter_targets = [
        target
        for group in jobs.get("blackbox-exporter", {}).get("static_configs", [])
        for target in group.get("targets", [])
    ]
    if exporter_targets != ["blackbox-exporter:9115"]:
        errors.append(
            "Blackbox exporter health job must target only its internal service name"
        )

    for job_name, expected in EXPECTED_PROBES.items():
        job = jobs.get(job_name)
        if not job:
            continue
        if job.get("metrics_path") != "/probe":
            errors.append(f"{job_name} must scrape the Blackbox /probe endpoint")
        if job.get("params", {}).get("module") != [expected["module"]]:
            errors.append(f"{job_name} must use its expected fixed probe module")
        relabels = job.get("relabel_configs", [])
        if not any(
            item.get("target_label") == "__address__"
            and item.get("replacement") == "blackbox-exporter:9115"
            for item in relabels
        ):
            errors.append(
                f"{job_name} must route through the internal exporter service"
            )
        if not any(item.get("target_label") == "__param_target" for item in relabels):
            errors.append(f"{job_name} must pass its configured target to the probe")
        if not any(
            item.get("source_labels") == ["probe_name"]
            and item.get("target_label") == "instance"
            for item in relabels
        ):
            errors.append(
                f"{job_name} must use a generic probe_name as its instance label"
            )
        if expected["kind"] == "file":
            files = [
                file
                for group in job.get("file_sd_configs", [])
                for file in group.get("files", [])
            ]
            if files != ["/etc/prometheus/targets/home-network.yml"]:
                errors.append(
                    "router probing must use only the ignored local target file"
                )
            if job.get("static_configs"):
                errors.append(
                    "router probing must not embed a local address in tracked Prometheus config"
                )
        else:
            target_groups = job.get("static_configs", [])
            targets = [
                target for group in target_groups for target in group.get("targets", [])
            ]
            labels = [group.get("labels", {}) for group in target_groups]
            if targets != expected["targets"]:
                errors.append(
                    f"{job_name} contains a target outside its explicit allowlist"
                )
            if labels != [{"probe_name": expected["probe_name"]}]:
                errors.append(
                    f"{job_name} must expose only its generic probe_name label"
                )

    for legacy_job in ("prometheus", "node-exporter", "container-exporter"):
        if legacy_job not in jobs:
            errors.append(f"existing scrape job {legacy_job} is missing")
    return errors


def check_blackbox_modules() -> list[str]:
    try:
        config = load_yaml(BLACKBOX_CONFIG)
    except (OSError, yaml.YAMLError, ValueError) as error:
        return [f"Blackbox config could not be read ({type(error).__name__})"]
    modules = config.get("modules", {})
    if set(modules) != EXPECTED_MODULES:
        return ["Blackbox config modules do not match the fixed probe allowlist"]
    expected_probers = {
        "icmp_ipv4": "icmp",
        "dns_a_example": "dns",
        "dns_aaaa_example": "dns",
        "https_ipv4": "http",
    }
    errors = [
        f"module {name} has an unexpected prober"
        for name, prober in expected_probers.items()
        if modules[name].get("prober") != prober
    ]
    if (
        modules["dns_a_example"].get("dns", {}).get("query_name") != "example.com"
        or modules["dns_a_example"].get("dns", {}).get("query_type") != "A"
    ):
        errors.append("DNS A module must query only example.com A records")
    if (
        modules["dns_aaaa_example"].get("dns", {}).get("query_name") != "example.com"
        or modules["dns_aaaa_example"].get("dns", {}).get("query_type") != "AAAA"
    ):
        errors.append("DNS AAAA module must query only example.com AAAA records")
    if modules["https_ipv4"].get("http", {}).get("method") != "GET":
        errors.append("HTTPS module must use a read-only GET request")
    https = modules["https_ipv4"].get("http", {})
    if https.get("follow_redirects") is not False:
        errors.append("HTTPS module must not follow redirects to unapproved hosts")
    if https.get("valid_status_codes") != [200, 301, 302, 303, 307, 308]:
        errors.append(
            "HTTPS module must accept only expected success and redirect status codes"
        )
    return errors


def check_alert_delivery() -> list[str]:
    try:
        prometheus = load_yaml(PROMETHEUS_CONFIG)
        alertmanager = load_yaml(ALERTMANAGER_CONFIG)
        rules = load_yaml(ROOT / "config" / "prometheus" / "rules" / "home-network.yml")
    except (OSError, yaml.YAMLError, ValueError) as error:
        return [
            f"alert delivery configuration could not be read ({type(error).__name__})"
        ]

    errors: list[str] = []
    if "/etc/prometheus/rules/*.yml" not in prometheus.get("rule_files", []):
        errors.append(
            "Prometheus must keep loading rule files from the existing rules directory"
        )
    manager_targets = [
        target
        for group in prometheus.get("alerting", {}).get("alertmanagers", [])
        for static_group in group.get("static_configs", [])
        for target in static_group.get("targets", [])
    ]
    if "alertmanager:9093" not in manager_targets:
        errors.append(
            "Prometheus must continue sending alerts to the existing Alertmanager"
        )

    receivers = {
        receiver.get("name"): receiver
        for receiver in alertmanager.get("receivers", [])
        if isinstance(receiver, dict)
    }
    receiver = receivers.get("incident-logger", {})
    webhook_configs = receiver.get("webhook_configs", [])
    if alertmanager.get("route", {}).get("receiver") != "incident-logger":
        errors.append("Alertmanager must retain its existing Incident Logger route")
    if not any(
        webhook.get("url") == "http://incident-logger:8080/webhook"
        and webhook.get("send_resolved") is True
        for webhook in webhook_configs
    ):
        errors.append(
            "Alertmanager must preserve FIRING and RESOLVED delivery to Incident Logger"
        )

    home_alerts = {
        rule.get("alert")
        for group in rules.get("groups", [])
        for rule in group.get("rules", [])
    }
    expected_alerts = {
        "BlackboxExporterDown",
        "BlackboxExporterTargetMissing",
        "HomeProbeExecutionError",
        "HomeRouterTargetMissing",
        "HomeRouterUnavailable",
        "HomeDNSResolutionUnavailable",
        "HomeInternetHTTPSUnavailable",
    }
    if home_alerts != expected_alerts:
        errors.append("home network rule names must match the reviewed alert set")
    return errors


def check_compose() -> list[str]:
    try:
        config = load_yaml(COMPOSE_CONFIG)
    except (OSError, yaml.YAMLError, ValueError) as error:
        return [f"Compose config could not be read ({type(error).__name__})"]
    services = config.get("services", {})
    exporter = services.get("blackbox-exporter")
    prometheus = services.get("prometheus", {})
    grafana = services.get("grafana", {})
    errors: list[str] = []
    if not isinstance(exporter, dict):
        return ["Blackbox exporter service is missing"]
    if exporter.get("image") != (
        "prom/blackbox-exporter:v0.28.0@sha256:"
        "e753ff9f3fc458d02cca5eddab5a77e1c175eee484a8925ac7d524f04366c2fc"
    ):
        errors.append(
            "Blackbox exporter image must remain pinned to its verified v0.28.0 digest"
        )
    if exporter.get("ports"):
        errors.append("Blackbox exporter must not publish a host port")
    if exporter.get("privileged"):
        errors.append("Blackbox exporter must not run privileged")
    if exporter.get("cap_add"):
        errors.append("Blackbox exporter must not add Linux capabilities")
    if "ALL" not in exporter.get("cap_drop", []):
        errors.append("Blackbox exporter must drop all Linux capabilities")
    if exporter.get("read_only") is not True:
        errors.append("Blackbox exporter root filesystem must be read-only")
    if exporter.get("user") != "65534:65534":
        errors.append("Blackbox exporter must run as the unprivileged nobody identity")
    if set(exporter.get("networks", [])) != {"home-probes"}:
        errors.append(
            "Blackbox exporter must be isolated to the dedicated home-probes network"
        )
    if "home-probes" not in prometheus.get("networks", {}):
        errors.append("Prometheus must join the dedicated home-probes network")
    if "home-probes" in grafana.get("networks", {}):
        errors.append("Grafana must not join the Blackbox probe network")
    members = {
        name
        for name, service in services.items()
        if "home-probes"
        in (
            service.get("networks", {})
            if isinstance(service.get("networks", {}), dict)
            else service.get("networks", [])
        )
    }
    if members != {"prometheus", "blackbox-exporter"}:
        errors.append("only Prometheus and Blackbox may join the home-probes network")
    home_network = config.get("networks", {}).get("home-probes", {})
    if home_network.get("driver") != "bridge" or home_network.get("internal") is True:
        errors.append("home-probes must be a dedicated egress-capable bridge network")
    serialized = yaml.safe_dump(exporter).lower()
    if "docker.sock" in serialized or "containerd.sock" in serialized:
        errors.append("Blackbox exporter must not mount Docker or containerd sockets")
    return errors


def check_candidate_ip_privacy() -> list[str]:
    try:
        candidates = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=20,
        ).stdout.split(b"\0")
    except (OSError, subprocess.SubprocessError):
        return ["could not enumerate Git candidates for address privacy check"]

    errors: list[str] = []
    allowed_public = {ipaddress.ip_address("1.1.1.1")}
    for candidate in candidates:
        if not candidate:
            continue
        path = ROOT / candidate.decode("utf-8", errors="ignore")
        try:
            content = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in IPV4_PATTERN.finditer(content):
            address = ipaddress.ip_address(match.group())
            if (
                address.is_loopback
                or address.is_unspecified
                or address.is_global
                or address in allowed_public
                or any(address in network for network in DOCUMENTATION_NETWORKS)
            ):
                continue
            errors.append(
                f"{path.relative_to(ROOT).as_posix()}: non-public IPv4 literal in a Git candidate"
            )
            break
    return errors


def main() -> int:
    checks = [
        check_prometheus_config,
        check_blackbox_modules,
        check_alert_delivery,
        check_compose,
        check_local_target,
        check_candidate_ip_privacy,
    ]
    errors = [error for check in checks for error in check()]
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: explicit target allowlist, private ignored router file, fixed probe modules, and least-privilege network wiring"
    )
    print(
        "NOTE: this static check does not establish Docker Desktop/WSL2 reachability or real probe success"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
