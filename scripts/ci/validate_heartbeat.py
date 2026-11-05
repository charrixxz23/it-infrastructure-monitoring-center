"""Statically validate the opt-in heartbeat proxy and isolation design."""

from __future__ import annotations

import ipaddress
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = ROOT / "compose.yaml"
OVERRIDE_COMPOSE = ROOT / "compose.heartbeat.yaml"
AGENT = ROOT / "services" / "heartbeat-agent" / "heartbeat_agent.py"
ENTRYPOINT = ROOT / "services" / "heartbeat-agent" / "heartbeat_entrypoint.py"
TESTS = ROOT / "services" / "heartbeat-agent" / "test_heartbeat_agent.py"
GUARD_TESTS = ROOT / "services" / "heartbeat-agent" / "test_heartbeat_entrypoint.py"
DOCKERFILE = ROOT / "services" / "heartbeat-agent" / "Dockerfile"
POLICY_SOURCE = ROOT / "services" / "heartbeat-egress-policy" / "egress_policy.py"
POLICY_DOCKERFILE = ROOT / "services" / "heartbeat-egress-policy" / "Dockerfile"
POLICY_TESTS = ROOT / "services" / "heartbeat-egress-policy" / "test_egress_policy.py"
TLS_PROBE = ROOT / "services" / "heartbeat-agent" / "provider_tls_probe.py"
RUNTIME_AUDIT = ROOT / "scripts" / "audit" / "test-heartbeat-proxy-egress.ps1"
SQUID_CONFIG = ROOT / "config" / "heartbeat" / "squid.conf"
RESOLV_CONFIG = ROOT / "config" / "heartbeat" / "resolv.conf"
EXPECTED_CHECKS = {
    "http://prometheus:9090/-/ready",
    "http://alertmanager:9093/-/ready",
    "http://grafana:3000/api/health",
    "http://incident-logger:8080/health",
}
EXPECTED_SERVICE_PORTS = {
    "prometheus": "9090",
    "alertmanager": "9093",
    "grafana": "3000",
    "incident-logger": "8080",
}
PROXY_IP = "172.31.255.250"
AGENT_IP = "172.31.255.251"
PROXY_SUBNET = "172.31.255.248/29"


def read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a YAML mapping")
    return value


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def validate_compose() -> list[str]:
    errors: list[str] = []
    try:
        base = read_yaml(BASE_COMPOSE)
        override = read_yaml(OVERRIDE_COMPOSE)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
        return [f"Compose YAML could not be parsed ({type(error).__name__})"]

    base_services = base.get("services", {})
    services = override.get("services", {})
    expected_services = {
        "heartbeat-agent",
        "heartbeat-egress-policy",
        "heartbeat-egress-proxy",
    }
    if "heartbeat-agent" in base_services:
        errors.append("the default Compose stack must not start heartbeat-agent")
    if set(services) != expected_services:
        errors.append("the opt-in override must add only the agent, policy, and proxy")

    agent = services.get("heartbeat-agent", {})
    policy = services.get("heartbeat-egress-policy", {})
    proxy = services.get("heartbeat-egress-proxy", {})
    if (
        agent.get("privileged") is True
        or agent.get("network_mode")
        or agent.get("ports")
    ):
        errors.append(
            "heartbeat-agent must not be privileged, host-networked, or published"
        )
    if agent.get("read_only") is not True:
        errors.append("heartbeat-agent root filesystem must be read-only")
    if "ALL" not in agent.get("cap_drop", []):
        errors.append(
            "heartbeat-agent must drop all capabilities before adding its guard set"
        )
    if set(agent.get("cap_add", [])) != {
        "NET_ADMIN",
        "NET_BIND_SERVICE",
        "SETPCAP",
        "SETUID",
        "SETGID",
        "KILL",
    }:
        errors.append(
            "agent capabilities must be limited to guard setup and privilege dropping"
        )
    if "no-new-privileges:true" not in agent.get("security_opt", []):
        errors.append("heartbeat-agent must set no-new-privileges")
    if agent.get("cpus") != 0.1 or agent.get("mem_limit") != "96m":
        errors.append("heartbeat-agent CPU and memory limits must remain bounded")
    if agent.get("pids_limit") != 32:
        errors.append("heartbeat-agent process count must remain bounded")
    dependencies = agent.get("depends_on", {})
    if dependencies != {
        "heartbeat-egress-proxy": {
            "condition": "service_started",
            "restart": True,
        }
    }:
        errors.append("agent must depend only on the isolated egress proxy")

    if set(agent.get("networks", {})) != {"monitoring", "heartbeat-proxy-private"}:
        errors.append(
            "agent must attach only to internal monitoring and proxy-private networks"
        )
    if set(policy.get("networks", {})) != {
        "heartbeat-proxy-private",
        "heartbeat-proxy-uplink",
    }:
        errors.append("only the policy namespace may attach to the proxy uplink")
    if proxy.get("network_mode") != "service:heartbeat-egress-policy" or proxy.get(
        "networks"
    ):
        errors.append("Squid must share only the policy service network namespace")
    if (
        policy.get("network_mode")
        or policy.get("privileged") is True
        or policy.get("ports")
    ):
        errors.append(
            "egress policy must be an unprivileged-network, unpublished sidecar"
        )
    if policy.get("build", {}).get("context") != "./services/heartbeat-egress-policy":
        errors.append(
            "egress policy must use its dedicated pinned Docker build context"
        )
    if policy.get("read_only") is not True or "ALL" not in policy.get("cap_drop", []):
        errors.append(
            "egress policy must use a read-only rootfs and drop all capabilities first"
        )
    if set(policy.get("cap_add", [])) != {
        "NET_ADMIN",
        "NET_BIND_SERVICE",
        "SETUID",
        "SETGID",
        "KILL",
    }:
        errors.append(
            "egress policy capabilities must be limited to firewall and DNS startup"
        )
    if "no-new-privileges:true" not in policy.get("security_opt", []):
        errors.append("egress policy must set no-new-privileges")
    if (
        policy.get("cpus") != 0.1
        or policy.get("mem_limit") != "64m"
        or policy.get("pids_limit") != 16
    ):
        errors.append(
            "egress policy CPU, memory, and process limits must remain bounded"
        )
    if "--healthcheck" not in policy.get("healthcheck", {}).get("test", []):
        errors.append(
            "policy health must require successful firewall and local DNS setup"
        )
    if proxy.get("depends_on") != {
        "heartbeat-egress-policy": {
            "condition": "service_healthy",
            "restart": True,
        }
    }:
        errors.append(
            "Squid must not start before the independent egress policy is healthy"
        )
    if base.get("networks", {}).get("monitoring", {}).get("internal") is not True:
        errors.append("the existing monitoring network must remain internal")

    networks = override.get("networks", {})
    private = networks.get("heartbeat-proxy-private", {})
    uplink = networks.get("heartbeat-proxy-uplink", {})
    subnets = [
        item.get("subnet")
        for item in private.get("ipam", {}).get("config", [])
        if isinstance(item, dict)
    ]
    if private.get("driver") != "bridge" or private.get("internal") is not True:
        errors.append("the agent-to-proxy network must be a Docker internal bridge")
    if subnets != [PROXY_SUBNET]:
        errors.append(
            "proxy-private network subnet must match the fixed proxy endpoints"
        )
    if uplink.get("driver") != "bridge" or uplink.get("internal") is True:
        errors.append("only the proxy may use its dedicated non-internal uplink bridge")
    try:
        network = ipaddress.ip_network(PROXY_SUBNET)
        if (
            ipaddress.ip_address(PROXY_IP) not in network
            or ipaddress.ip_address(AGENT_IP) not in network
        ):
            errors.append(
                "fixed proxy and agent addresses must belong to the private subnet"
            )
        if (
            agent.get("networks", {})
            .get("heartbeat-proxy-private", {})
            .get("ipv4_address")
            != AGENT_IP
        ):
            errors.append("agent address must match the proxy source ACL")
        if (
            policy.get("networks", {})
            .get("heartbeat-proxy-private", {})
            .get("ipv4_address")
            != PROXY_IP
        ):
            errors.append(
                "policy namespace address must match the agent firewall and proxy URL"
            )
    except ValueError:
        errors.append("proxy-private subnet or fixed endpoint address is invalid")

    volumes = agent.get("volumes", [])
    if len(volumes) != 2:
        errors.append(
            "agent must mount only the Ping URL secret and local resolver file"
        )
    else:
        by_target = {
            item.get("target"): item for item in volumes if isinstance(item, dict)
        }
        secret = by_target.get("/run/secrets/healthchecks_ping_url", {})
        resolver = by_target.get("/etc/resolv.conf", {})
        if (
            secret.get("type") != "bind"
            or secret.get("source") != "./.secrets/healthchecks-ping-url"
            or secret.get("read_only") is not True
            or secret.get("bind", {}).get("create_host_path") is not False
        ):
            errors.append(
                "Ping URL must be a read-only bind mount that fails if absent"
            )
        if (
            resolver.get("type") != "bind"
            or resolver.get("source") != "./config/heartbeat/resolv.conf"
            or resolver.get("read_only") is not True
        ):
            errors.append("agent resolver must be a read-only local-only configuration")

    environment = agent.get("environment", {})
    expected_environment = {
        "HEARTBEAT_PING_URL_FILE": "/run/secrets/healthchecks_ping_url",
        "HEARTBEAT_INTERVAL_SECONDS": "60",
        "HEARTBEAT_COMPONENT_TIMEOUT_SECONDS": "3",
        "HEARTBEAT_PING_TIMEOUT_SECONDS": "5",
        "HEARTBEAT_HEALTH_PORT": "9740",
        "HEARTBEAT_GUARD_STARTUP_TIMEOUT_SECONDS": "120",
    }
    if environment != expected_environment:
        errors.append(
            "environment must contain only the secret-file path and bounded settings"
        )
    if "--healthcheck" not in agent.get("healthcheck", {}).get("test", []):
        errors.append("container healthcheck must use the local read-only endpoint")

    if proxy.get("privileged") is True or proxy.get("ports"):
        errors.append("proxy must not be privileged or published")
    if proxy.get("user") != "13:13" or proxy.get("read_only") is not True:
        errors.append(
            "Squid must run as its image's non-root proxy UID on a read-only rootfs"
        )
    if "ALL" not in proxy.get("cap_drop", []) or proxy.get("cap_add"):
        errors.append("Squid must have no Linux capabilities")
    if "no-new-privileges:true" not in proxy.get("security_opt", []):
        errors.append("Squid must set no-new-privileges")
    if "@sha256:" not in proxy.get("image", ""):
        errors.append("Squid image must be pinned by digest")
    proxy_resolver = next(
        (
            item
            for item in proxy.get("volumes", [])
            if isinstance(item, dict) and item.get("target") == "/etc/resolv.conf"
        ),
        {},
    )
    if (
        proxy_resolver.get("type") != "bind"
        or proxy_resolver.get("source") != "./config/heartbeat/resolv.conf"
        or proxy_resolver.get("read_only") is not True
    ):
        errors.append("Squid must use the local allowlisted DNS resolver read-only")
    if any(
        item.get("target") == "/var/run/docker.sock"
        for item in (
            agent.get("volumes", [])
            + policy.get("volumes", [])
            + proxy.get("volumes", [])
        )
        if isinstance(item, dict)
    ):
        errors.append("the heartbeat stack must never mount Docker Socket")
    return errors


def validate_agent() -> list[str]:
    errors: list[str] = []
    try:
        source = read_text(AGENT)
        guard = read_text(ENTRYPOINT)
        tests = read_text(TESTS)
        guard_tests = read_text(GUARD_TESTS)
        dockerfile = read_text(DOCKERFILE)
        policy_source = read_text(POLICY_SOURCE)
        policy_dockerfile = read_text(POLICY_DOCKERFILE)
        policy_tests = read_text(POLICY_TESTS)
        tls_probe = read_text(TLS_PROBE)
        runtime_audit = read_text(RUNTIME_AUDIT)
        squid = read_text(SQUID_CONFIG)
        resolver = read_text(RESOLV_CONFIG)
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
    if 'EXTERNAL_PROXY_URL = "http://172.31.255.250:3128"' not in source:
        errors.append("external requests must use the dedicated fixed proxy endpoint")
    if "MandatoryProxyHandler" not in source or "return False" not in source:
        errors.append("external proxy use must ignore bypass environment settings")
    if "urllib.request.ProxyHandler({})" not in source:
        errors.append("internal service requests must not inherit an environment proxy")
    if "docker.sock" in source or "subprocess." in source:
        errors.append(
            "heartbeat application must not access Docker or run administration commands"
        )
    if "00000000-0000-0000-0000-000000000001" not in tests:
        errors.append("tests must use only the documented inert synthetic Ping URL")
    if (
        "hc-ping.com" not in tests
        or "test_proxy_failure_does_not_fall_back_to_direct_transport" not in tests
    ):
        errors.append(
            "mock tests must validate host restrictions and no direct fallback"
        )
    if '"127.0.0.1"' not in guard_tests or "1.1.1.1" not in guard_tests:
        errors.append(
            "guard tests must exercise bounded DNS and restricted destinations"
        )
    if (
        "hostname not in DNS_HOSTS" not in guard
        or "test_docker_dns_helper_rejects_non_allowlisted_names_without_querying"
        not in guard_tests
    ):
        errors.append(
            "Docker embedded DNS helper must enforce the fixed internal-name allowlist"
        )

    if 'ENTRYPOINT ["python", "/app/heartbeat_entrypoint.py"]' not in dockerfile:
        errors.append(
            "container entrypoint must install the network guard before the agent"
        )
    if "dnsmasq=" not in dockerfile or "iptables=" not in dockerfile:
        errors.append("network guard tools must have fixed package versions")
    if "sha256:" not in dockerfile:
        errors.append("Python base image must be pinned by digest")
    if "pip install" in dockerfile.lower():
        errors.append(
            "stdlib-only heartbeat agent must not install Python dependencies"
        )

    firewall_install = guard.find("apply_firewall(build_bootstrap_firewall_commands())")
    final_policy = guard.find("apply_firewall(build_final_firewall_commands(services))")
    fork = guard.find("return supervise_agent(services, dns_process)")
    if not (0 <= firewall_install < final_policy < fork):
        errors.append(
            "firewall policies must be applied before the heartbeat child is started"
        )
    for marker in (
        "PR_CAPBSET_DROP",
        "os.setgroups([])",
        "os.setgid(AGENT_GID)",
        "os.setuid(AGENT_UID)",
        '"CapEff", "CapPrm", "CapAmb", "CapBnd"',
        'status.get("NoNewPrivs") != "1"',
    ):
        if marker not in guard:
            errors.append(
                "agent child must verify non-root identity, empty capabilities, and NNP"
            )
            break
    if (
        "def refresh_service_map" not in guard
        or "DNS_REFRESH_SECONDS = 15" not in guard
    ):
        errors.append(
            "private service IPs must be re-resolved after Compose recreations"
        )
    if (
        'f"{base_chain}_A"' not in guard
        or '"ITMC_HB6_OUT"' not in guard
        or '"-R"' not in guard
    ):
        errors.append(
            "firewall refresh must stage complete chains before atomic hook replacement"
        )
    if (
        '"-j", "DROP"' not in guard
        or "network policy could not be applied" not in guard
    ):
        errors.append(
            "firewall setup must retain a terminal deny and abort on rule failures"
        )
    if "server=" in guard or "no-resolv" not in guard or "no-hosts" not in guard:
        errors.append("local DNS must have no external upstream or host-file fallback")
    if "127.0.0.11" not in guard or '"--uid-owner",\n            "0"' not in guard:
        errors.append("only the root supervisor may query Docker's embedded DNS")

    if 'PROVIDER_HOST = "hc-ping.com"' not in policy_source:
        errors.append("proxy firewall must resolve only the fixed provider hostname")
    for marker in (
        'DOCKER_DNS = "127.0.0.11"',
        'LOCAL_DNS = "127.0.0.1"',
        "PROXY_UID = 13",
        "PROVIDER_PORT = 443",
        "def resolve_provider_ipv4(",
        "response_source != (server, port)",
        "def validate_provider_addresses(",
        "parsed.is_global",
        "MAX_PROVIDER_ADDRESSES = 16",
        '"--uid-owner", str(PROXY_UID)',
        '"--uid-owner", "0"',
        'f"{DOCKER_DNS}/32", "-j", "DROP"',
        '"-j", "DROP"',
        '"ip6tables"',
        '"--ctstate", "ESTABLISHED,RELATED"',
        "def _refresh_provider(",
        "_install_provider_policy(())",
        "no-resolv",
        "no-hosts",
        'f"listen-address={LOCAL_DNS}"',
        "local-ttl=5",
    ):
        if marker not in policy_source:
            errors.append(
                "independent proxy namespace policy must keep DNS dynamic and egress fail-closed"
            )
            break
    if (
        "@sha256:" not in policy_dockerfile
        or "iptables=1.8.13-r0" not in policy_dockerfile
        or "dnsmasq=2.92_p2-r0" not in policy_dockerfile
    ):
        errors.append("policy image and firewall/DNS package versions must be pinned")
    if "test_dns_parser_follows_cname_and_uses_shortest_ttl" not in policy_tests:
        errors.append("policy tests must validate DNS CNAME chains and TTL handling")
    if "test_dns_parser_rejects_response_from_unexpected_source" not in policy_tests:
        errors.append(
            "policy DNS tests must reject responses from unconfigured sources"
        )
    if "test_bootstrap_and_empty_policy_have_no_provider_egress" not in policy_tests:
        errors.append(
            "policy tests must prove bootstrap and failure states deny provider egress"
        )
    if "test_failed_rule_install_keeps_default_drop" not in policy_tests:
        errors.append("policy tests must verify fail-closed firewall installation")
    if (
        "def _emergency_deny_egress(" not in policy_source
        or "test_emergency_deny_flushes_only_owned_output_chains" not in policy_tests
    ):
        errors.append(
            "runtime DNS failures must attempt a namespace-scoped emergency deny"
        )
    if "test_dns_refresh_closes_egress_before_swapping_records" not in policy_tests:
        errors.append("policy refresh tests must prevent stale/new IP overlap")
    if 'ENTRYPOINT ["python", "/app/egress_policy.py"]' not in policy_dockerfile:
        errors.append(
            "the policy sidecar must install its filter before reporting healthy"
        )
    if "provider_tls_probe.py" not in dockerfile:
        errors.append(
            "the explicitly authorized TLS probe must be included in the agent image"
        )
    if (
        "--authorize-provider-contact" not in tls_probe
        or 'f"CONNECT {PROVIDER_HOST}:443 HTTP/1.1' not in tls_probe
    ):
        errors.append(
            "the prepared TLS probe must require explicit authorization and fixed CONNECT"
        )
    if "response.read" in tls_probe or "GET " in tls_probe:
        errors.append("the TLS probe must not send a Ping URL or HTTP request")
    if "test_probe_refuses_without_explicit_authorization_flag" not in read_text(
        ROOT / "services" / "heartbeat-agent" / "test_provider_tls_probe.py"
    ):
        errors.append("the TLS probe must have an automated no-contact guard test")
    if (
        "192.0.2.20" not in runtime_audit
        or "198.51.100.99" not in runtime_audit
        or "fd42:192:0:2::20" not in runtime_audit
        or "--cap-add', 'NET_ADMIN" not in runtime_audit
        or "--rm" not in runtime_audit
        or "docker network rm" not in runtime_audit
        or 'dns_probe("approved.invalid", "192.0.2.20")' not in runtime_audit
        or 'dns_probe("blocked.invalid", None)' not in runtime_audit
        or "BLOCKED: non-root proxy UID cannot modify firewall rules"
        not in runtime_audit
        or "hc-ping.com" in runtime_audit
    ):
        errors.append(
            "runtime audit must use synthetic targets, avoid the provider, and clean up"
        )

    required_squid = (
        "http_port 172.31.255.250:3128",
        "acl heartbeat_agent src 172.31.255.251/32",
        "acl CONNECT method CONNECT",
        "acl healthchecks_port port 443",
        "acl healthchecks_host dstdomain -n hc-ping.com",
        "http_access deny !heartbeat_agent",
        "http_access deny !CONNECT",
        "http_access deny !healthchecks_port",
        "http_access deny !healthchecks_host",
        "http_access deny forbidden_destination",
        "http_access allow heartbeat_agent CONNECT healthchecks_host healthchecks_port",
        "http_access deny all",
        "access_log none",
        "cache_log /dev/null",
        "cache_effective_user proxy",
    )
    if any(line not in squid for line in required_squid):
        errors.append(
            "Squid must deny unapproved client, method, port, host, and destination"
        )
    if not (
        squid.index("http_access deny !heartbeat_agent")
        < squid.index("http_access deny !CONNECT")
        < squid.index("http_access deny !healthchecks_port")
        < squid.index("http_access deny !healthchecks_host")
        < squid.index("http_access deny forbidden_destination")
        < squid.index(
            "http_access allow heartbeat_agent CONNECT healthchecks_host healthchecks_port"
        )
        < squid.index("http_access deny all")
    ):
        errors.append(
            "Squid access-control order must deny first and allow only the exact provider"
        )
    for denied_network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "fc00::/7",
        "fe80::/10",
    ):
        if denied_network not in squid:
            errors.append(
                "Squid destination ACL must reject private, local, and reserved IP ranges"
            )
            break
    if "nameserver 127.0.0.1" not in resolver or "nameserver 127.0.0.11" in resolver:
        errors.append(
            "agent resolver must point only at the local fixed-record DNS proxy"
        )
    if any(
        token in (source + guard + squid).lower()
        for token in ("hc-ping.com/", "https://hc-ping.com/")
    ):
        errors.append("source configuration must not embed a provider Ping URL")
    return errors


def main() -> int:
    errors = validate_compose() + validate_agent()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "PASS: opt-in heartbeat has an independently filtered proxy namespace, dynamic DNS allowlisting, "
        "fail-closed startup, non-root Squid/sender checks, and static policy validation. "
        "This validator is not a substitute for Docker Desktop runtime isolation tests."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
