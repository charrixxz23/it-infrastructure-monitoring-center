"""Validate the opt-in heartbeat service without starting containers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = ROOT / "compose.yaml"
OVERRIDE_COMPOSE = ROOT / "compose.heartbeat.yaml"
AGENT = ROOT / "services" / "heartbeat-agent" / "heartbeat_agent.py"
TESTS = ROOT / "services" / "heartbeat-agent" / "test_heartbeat_agent.py"
DOCKERFILE = ROOT / "services" / "heartbeat-agent" / "Dockerfile"
EXPECTED_CHECKS = {
    "http://prometheus:9090/-/ready",
    "http://alertmanager:9093/-/ready",
    "http://grafana:3000/api/health",
    "http://incident-logger:8080/health",
}


def read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a YAML mapping")
    return value


def validate_compose() -> list[str]:
    errors: list[str] = []
    try:
        base = read_yaml(BASE_COMPOSE)
        override = read_yaml(OVERRIDE_COMPOSE)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
        return [f"Compose YAML could not be parsed ({type(error).__name__})"]

    base_services = base.get("services", {})
    override_services = override.get("services", {})
    if "heartbeat-agent" in base_services:
        errors.append("the default Compose stack must not start heartbeat-agent")
    if set(override_services) != {"heartbeat-agent"}:
        errors.append("the opt-in override must add only heartbeat-agent")

    service = override_services.get("heartbeat-agent", {})
    if service.get("privileged") is True:
        errors.append("heartbeat-agent must not be privileged")
    if service.get("network_mode") or service.get("ports"):
        errors.append("heartbeat-agent must not use host networking or publish ports")
    if "depends_on" in service:
        errors.append("heartbeat-agent must not implicitly start other services")
    if service.get("user") != "65534:65534":
        errors.append("heartbeat-agent must run as the configured non-root UID")
    if service.get("read_only") is not True:
        errors.append("heartbeat-agent root filesystem must be read-only")
    if "ALL" not in service.get("cap_drop", []):
        errors.append("heartbeat-agent must drop all Linux capabilities")
    if "no-new-privileges:true" not in service.get("security_opt", []):
        errors.append("heartbeat-agent must set no-new-privileges")
    if service.get("cpus") != 0.1 or service.get("mem_limit") != "96m":
        errors.append("heartbeat-agent CPU and memory limits must remain bounded")
    if service.get("pids_limit") != 32:
        errors.append("heartbeat-agent process count must remain bounded")

    networks = service.get("networks", [])
    if set(networks) != {"monitoring", "heartbeat-egress"}:
        errors.append("agent must use only monitoring and its dedicated egress bridge")
    if base.get("networks", {}).get("monitoring", {}).get("internal") is not True:
        errors.append("the existing monitoring network must remain internal")
    egress = override.get("networks", {}).get("heartbeat-egress", {})
    if egress.get("driver") != "bridge" or egress.get("internal") is True:
        errors.append(
            "heartbeat-egress must be a non-internal bridge for outbound HTTPS"
        )

    volumes = service.get("volumes", [])
    if len(volumes) != 1:
        errors.append("agent must receive only its single Ping URL secret file")
    else:
        volume = volumes[0]
        if (
            volume.get("type") != "bind"
            or volume.get("source") != "./.secrets/healthchecks-ping-url"
            or volume.get("target") != "/run/secrets/healthchecks_ping_url"
            or volume.get("read_only") is not True
            or volume.get("bind", {}).get("create_host_path") is not False
        ):
            errors.append(
                "Ping URL must be a read-only bind mount that fails if absent"
            )

    environment = service.get("environment", {})
    expected_environment = {
        "HEARTBEAT_PING_URL_FILE": "/run/secrets/healthchecks_ping_url",
        "HEARTBEAT_INTERVAL_SECONDS": "60",
        "HEARTBEAT_COMPONENT_TIMEOUT_SECONDS": "3",
        "HEARTBEAT_PING_TIMEOUT_SECONDS": "5",
        "HEARTBEAT_HEALTH_PORT": "9740",
    }
    if environment != expected_environment:
        errors.append(
            "environment must contain only the Ping URL file path and bounded non-secret settings"
        )
    healthcheck = service.get("healthcheck", {})
    if "--healthcheck" not in healthcheck.get("test", []):
        errors.append("container healthcheck must use the local read-only endpoint")
    return errors


def validate_agent() -> list[str]:
    errors: list[str] = []
    try:
        source = AGENT.read_text(encoding="utf-8")
        tests = TESTS.read_text(encoding="utf-8")
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return [f"heartbeat implementation could not be read ({type(error).__name__})"]

    for endpoint in EXPECTED_CHECKS:
        if endpoint not in source:
            errors.append("agent is missing a required core service health endpoint")
    if 'PING_URL_HOST = "hc-ping.com"' not in source:
        errors.append("agent must validate the fixed Healthchecks.io Ping host")
    if 'HEALTH_BIND_HOST = "127.0.0.1"' not in source:
        errors.append("agent health endpoint must bind only to container loopback")
    if 'parsed.scheme != "https"' not in source or "NoRedirectHandler" not in source:
        errors.append("Ping URL must require HTTPS and reject redirects")
    if (
        "response.status != 200" not in source
        or 'response.read(3) != b"OK"' not in source
    ):
        errors.append(
            "external success must validate HTTP 200 and the documented OK body"
        )
    if "docker.sock" in source or "subprocess." in source:
        errors.append(
            "agent must not access the Docker API or invoke administration commands"
        )
    if "00000000-0000-0000-0000-000000000001" not in tests:
        errors.append("tests must use only the documented inert synthetic Ping URL")
    if "hc-ping.com" not in tests:
        errors.append("mock tests must exercise Ping URL validation")
    if "USER 65534:65534" not in dockerfile:
        errors.append("container image must run as non-root")
    if "pip install" in dockerfile.lower():
        errors.append("stdlib-only agent image must not install Python packages")
    return errors


def main() -> int:
    errors = validate_compose() + validate_agent()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "PASS: heartbeat remains opt-in, isolated from host Docker access, "
        "bounded, read-only, secret-file based, and validated with fixed checks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
