"""Install a fail-closed network policy before dropping privileges to the agent."""

from __future__ import annotations

import ipaddress
import ctypes
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path


AGENT_UID = 65534
AGENT_GID = 65534
DNS_SERVER = "127.0.0.11"
LOCAL_DNS = "127.0.0.1"
PROXY_IP = "172.31.255.250"
PROXY_PORT = 3128
DNS_HOSTS = {
    "prometheus": 9090,
    "alertmanager": 9093,
    "grafana": 3000,
    "incident-logger": 8080,
}
PRIVATE_V4_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
STARTUP_TIMEOUT_SECONDS = 120
DNS_TIMEOUT_SECONDS = 2.0
DNS_REFRESH_SECONDS = 15
DNSMASQ_RELOAD_SIGNAL = getattr(signal, "SIGHUP", 1)
HOSTS_FILE = Path("/tmp/heartbeat-hosts")
DNSMASQ_CONFIG = Path("/tmp/heartbeat-dnsmasq.conf")


class GuardError(RuntimeError):
    """Raised without carrying request data or secret values."""


def _skip_dns_name(packet: bytes, offset: int) -> int:
    """Return the first byte after a DNS name, following compressed pointers safely."""
    steps = 0
    while offset < len(packet):
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise GuardError("truncated DNS response")
            return offset + 2
        if length & 0xC0:
            raise GuardError("invalid DNS name encoding")
        offset += 1
        if length == 0:
            return offset
        if offset + length > len(packet):
            raise GuardError("truncated DNS response")
        offset += length
        steps += 1
        if steps > 127:
            raise GuardError("DNS name is too long")
    raise GuardError("truncated DNS response")


def _encode_dns_name(hostname: str) -> bytes:
    labels = hostname.rstrip(".").split(".")
    if not labels or any(not label or len(label) > 63 for label in labels):
        raise GuardError("invalid internal service name")
    return (
        b"".join(bytes((len(label),)) + label.encode("ascii") for label in labels)
        + b"\x00"
    )


def dns_query_a(
    hostname: str,
    server: str,
    timeout: float = DNS_TIMEOUT_SECONDS,
    port: int = 53,
) -> tuple[str, ...]:
    """Resolve A records through a specific resolver with a bounded UDP timeout."""
    if server == DNS_SERVER and hostname not in DNS_HOSTS:
        raise GuardError("DNS name is outside the fixed internal service allowlist")
    transaction_id = int.from_bytes(os.urandom(2), "big")
    header = struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
    question = _encode_dns_name(hostname) + struct.pack("!HH", 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(timeout)
        client.sendto(header + question, (server, port))
        response, _ = client.recvfrom(4096)

    if len(response) < 12:
        raise GuardError("truncated DNS response")
    response_id, flags, questions, answers, _, _ = struct.unpack(
        "!HHHHHH", response[:12]
    )
    if response_id != transaction_id or not flags & 0x8000 or flags & 0x0200:
        raise GuardError("invalid DNS response")
    if flags & 0x000F:
        raise GuardError("internal service DNS lookup failed")

    offset = 12
    for _ in range(questions):
        offset = _skip_dns_name(response, offset) + 4
    addresses: set[str] = set()
    for _ in range(answers):
        offset = _skip_dns_name(response, offset)
        if offset + 10 > len(response):
            raise GuardError("truncated DNS answer")
        record_type, record_class, _, record_length = struct.unpack(
            "!HHIH", response[offset : offset + 10]
        )
        offset += 10
        data = response[offset : offset + record_length]
        if len(data) != record_length:
            raise GuardError("truncated DNS answer")
        if record_type == 1 and record_class == 1 and record_length == 4:
            addresses.add(str(ipaddress.IPv4Address(data)))
        offset += record_length
    if not addresses:
        raise GuardError("internal service has no IPv4 address")
    return tuple(sorted(addresses))


def validate_service_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    if not addresses:
        raise GuardError("internal service has no address")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.version != 4 or not any(ip in network for network in PRIVATE_V4_NETWORKS):
            raise GuardError("internal service resolved outside private IPv4 space")
    return tuple(sorted(set(addresses)))


def write_dnsmasq_files(services: dict[str, tuple[str, ...]]) -> None:
    hosts = [
        f"{address} {hostname}"
        for hostname, addresses in sorted(services.items())
        for address in addresses
    ]
    HOSTS_FILE.write_text("\n".join(hosts) + "\n", encoding="ascii")
    HOSTS_FILE.chmod(0o644)
    DNSMASQ_CONFIG.write_text(
        "\n".join(
            (
                "no-resolv",
                "no-hosts",
                "domain-needed",
                "pid-file=/tmp/heartbeat-dnsmasq.pid",
                "bind-interfaces",
                f"listen-address={LOCAL_DNS}",
                "port=53",
                f"addn-hosts={HOSTS_FILE}",
                "cache-size=0",
                "log-facility=/dev/null",
                "user=dnsproxy",
                "group=dnsproxy",
            )
        )
        + "\n",
        encoding="ascii",
    )
    DNSMASQ_CONFIG.chmod(0o644)


def _chain_commands(
    binary: str,
    chain: str,
    builtin_chain: str,
    rules: list[list[str]],
) -> list[list[str]]:
    commands = [
        [binary, "-N", chain],
        [binary, "-F", chain],
    ]
    commands.extend([binary, "-A", chain, *rule] for rule in rules)
    commands.append([binary, "-I", builtin_chain, "1", "-j", chain])
    return commands


def _root_dns_rules() -> list[list[str]]:
    """Allow only supervisor DNS over UDP/TCP to Docker's embedded resolver.

    Docker Desktop DNATs 127.0.0.11:53 to a per-namespace high port before
    OUTPUT filtering, so a destination-port match is not reliable here.
    """
    return [
        [
            "-p",
            protocol,
            "-d",
            f"{DNS_SERVER}/32",
            "-m",
            "owner",
            "--uid-owner",
            "0",
            "-j",
            "ACCEPT",
        ]
        for protocol in ("udp", "tcp")
    ]


def build_bootstrap_firewall_commands() -> list[list[str]]:
    out_rules = _root_dns_rules() + [
        [
            "-d",
            PROXY_IP,
            "-p",
            "tcp",
            "--dport",
            str(PROXY_PORT),
            "-m",
            "owner",
            "--uid-owner",
            "0",
            "-j",
            "ACCEPT",
        ],
        ["-o", "lo", "-j", "ACCEPT"],
        ["-j", "DROP"],
    ]
    in_rules = [
        ["-i", "lo", "-j", "ACCEPT"],
        ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ["-j", "DROP"],
    ]
    commands = _chain_commands("iptables", "ITMC_HB_OUT", "OUTPUT", out_rules)
    commands += _chain_commands("iptables", "ITMC_HB_IN", "INPUT", in_rules)
    commands += _chain_commands(
        "ip6tables",
        "ITMC_HB6_OUT",
        "OUTPUT",
        [["-o", "lo", "-j", "ACCEPT"], ["-j", "DROP"]],
    )
    commands += _chain_commands(
        "ip6tables",
        "ITMC_HB6_IN",
        "INPUT",
        [
            ["-i", "lo", "-j", "ACCEPT"],
            ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
            ["-j", "DROP"],
        ],
    )
    return commands


def build_final_firewall_commands(
    services: dict[str, tuple[str, ...]],
) -> list[list[str]]:
    out_rules = _root_dns_rules() + [
        ["-d", "127.0.0.11/32", "-j", "DROP"],
        ["-o", "lo", "-j", "ACCEPT"],
    ]
    for hostname, addresses in sorted(services.items()):
        port = DNS_HOSTS[hostname]
        for address in addresses:
            out_rules.append(
                [
                    "-m",
                    "owner",
                    "--uid-owner",
                    str(AGENT_UID),
                    "-p",
                    "tcp",
                    "-d",
                    address,
                    "--dport",
                    str(port),
                    "-j",
                    "ACCEPT",
                ]
            )
    out_rules.extend(
        (
            [
                "-m",
                "owner",
                "--uid-owner",
                str(AGENT_UID),
                "-p",
                "tcp",
                "-d",
                PROXY_IP,
                "--dport",
                str(PROXY_PORT),
                "-j",
                "ACCEPT",
            ],
            ["-j", "DROP"],
        )
    )
    in_rules = [
        ["-i", "lo", "-j", "ACCEPT"],
        ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ["-j", "DROP"],
    ]
    commands = _chain_commands("iptables", "ITMC_HB_OUT", "OUTPUT", out_rules)
    commands += _chain_commands("iptables", "ITMC_HB_IN", "INPUT", in_rules)
    commands += _chain_commands(
        "ip6tables",
        "ITMC_HB6_OUT",
        "OUTPUT",
        [["-o", "lo", "-j", "ACCEPT"], ["-j", "DROP"]],
    )
    commands += _chain_commands(
        "ip6tables",
        "ITMC_HB6_IN",
        "INPUT",
        [
            ["-i", "lo", "-j", "ACCEPT"],
            ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
            ["-j", "DROP"],
        ],
    )
    return commands


def apply_firewall(commands: list[list[str]]) -> None:
    """Stage complete DROP-terminated chains, then switch hooks atomically.

    A refresh must never flush the live policy: a partially rebuilt live chain
    could briefly permit traffic before its terminal DROP rule is restored.
    Alternating inactive chains means a failed build leaves the previous hook
    intact. Each builtin hook is replaced with a single iptables operation.
    """
    groups: list[tuple[str, str, list[list[str]]]] = []
    for command in commands:
        if command[1] == "-N":
            groups.append((command[0], command[2], [command]))
        elif not groups:
            raise GuardError("network policy command has no chain")
        else:
            groups[-1][2].append(command)

    policies = []
    for binary, base_chain, group in groups:
        hook_commands = [command for command in group if command[1] == "-I"]
        append_commands = [command for command in group if command[1] == "-A"]
        if len(hook_commands) != 1 or not append_commands:
            raise GuardError("network policy chain is incomplete")
        if append_commands[-1][-2:] != ["-j", "DROP"]:
            raise GuardError("network policy chain is missing its terminal drop")

        _, _, builtin, _, _, _ = hook_commands[0]
        listing = subprocess.run(
            [binary, "-S", builtin],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=3,
        )
        if listing.returncode != 0:
            raise GuardError("network policy hook could not be inspected")

        active: list[tuple[int, str]] = []
        rule_position = 0
        for line in listing.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[0] != "-A" or parts[1] != builtin:
                continue
            rule_position += 1
            if len(parts) == 4 and parts[2] == "-j" and (
                parts[3] == base_chain or parts[3].startswith(f"{base_chain}_")
            ):
                active.append((rule_position, parts[3]))
        if len(active) > 1:
            raise GuardError("duplicate network policy hooks were found")
        active_chain = active[0][1] if active else None
        if active and active[0][0] != 1:
            raise GuardError("network policy hook is not first")
        if active_chain not in (None, base_chain, f"{base_chain}_A", f"{base_chain}_B"):
            raise GuardError("unknown network policy chain is active")

        candidate = f"{base_chain}_B" if active_chain in (
            base_chain,
            f"{base_chain}_A",
        ) else f"{base_chain}_A"
        if candidate == active_chain:
            raise GuardError("active network policy chain cannot be rebuilt")
        translated = [
            [candidate if value == base_chain else value for value in command]
            for command in group
        ]
        policies.append((binary, builtin, active_chain, candidate, translated))

    # Build every candidate while the current policy remains attached.
    for binary, _, _, candidate, group in policies:
        create = subprocess.run(
            [binary, "-N", candidate],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
        )
        if create.returncode != 0:
            existing = subprocess.run(
                [binary, "-S", candidate],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )
            if existing.returncode != 0:
                raise GuardError("network policy chain could not be created")
            flush = subprocess.run(
                [binary, "-F", candidate],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )
            if flush.returncode != 0:
                raise GuardError("inactive network policy chain could not be cleared")

        for command in group:
            if command[1] in ("-N", "-F", "-I"):
                continue
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )
            if result.returncode != 0:
                raise GuardError("network policy could not be applied")

    # Each replacement is atomic; on a later failure, both generations remain
    # restrictive and no partially constructed chain is active.
    for binary, builtin, active_chain, candidate, _ in policies:
        if active_chain:
            switch = [binary, "-R", builtin, "1", "-j", candidate]
        else:
            switch = [binary, "-I", builtin, "1", "-j", candidate]
        result = subprocess.run(
            switch,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
        )
        if result.returncode != 0:
            raise GuardError("network policy hook could not be installed")


def start_dnsmasq(expected: tuple[str, ...]) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        ["dnsmasq", "--keep-in-foreground", f"--conf-file={DNSMASQ_CONFIG}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise GuardError("local DNS filter did not start")
        try:
            if validate_service_addresses(dns_query_a("prometheus", LOCAL_DNS)) == expected:
                return process
        except (OSError, GuardError):
            time.sleep(0.1)
    process.terminate()
    raise GuardError("local DNS filter did not become ready")


def _service_map_from_docker_dns() -> dict[str, tuple[str, ...]]:
    return {
        hostname: validate_service_addresses(dns_query_a(hostname, DNS_SERVER))
        for hostname in DNS_HOSTS
    }


def _write_dns_hosts(services: dict[str, tuple[str, ...]]) -> None:
    temporary_hosts = HOSTS_FILE.with_suffix(".next")
    hosts = [
        f"{address} {hostname}"
        for hostname, addresses in sorted(services.items())
        for address in addresses
    ]
    temporary_hosts.write_text("\n".join(hosts) + "\n", encoding="ascii")
    temporary_hosts.chmod(0o644)
    os.replace(temporary_hosts, HOSTS_FILE)


def refresh_service_map(
    current: dict[str, tuple[str, ...]],
    updated: dict[str, tuple[str, ...]],
    dns_process: subprocess.Popen[bytes],
) -> dict[str, tuple[str, ...]]:
    """Refresh DNS and firewall without opening a direct-egress window."""
    if current == updated:
        return current
    union = {
        hostname: tuple(
            sorted(set(current.get(hostname, ())) | set(updated.get(hostname, ())))
        )
        for hostname in DNS_HOSTS
    }
    apply_firewall(build_final_firewall_commands(union))
    _write_dns_hosts(updated)
    dns_process.send_signal(DNSMASQ_RELOAD_SIGNAL)

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            if all(
                validate_service_addresses(dns_query_a(hostname, LOCAL_DNS))
                == addresses
                for hostname, addresses in updated.items()
            ):
                apply_firewall(build_final_firewall_commands(updated))
                return updated
        except (OSError, GuardError):
            time.sleep(0.1)
    # Keep the union of private service IPs if local DNS has not reloaded yet.
    # This can only preserve the narrow service ports; it cannot create Internet egress.
    return union


def _read_startup_timeout() -> int:
    raw = os.environ.get(
        "HEARTBEAT_GUARD_STARTUP_TIMEOUT_SECONDS",
        str(STARTUP_TIMEOUT_SECONDS),
    )
    try:
        value = int(raw)
    except ValueError as error:
        raise GuardError("network guard timeout is invalid") from error
    if not 30 <= value <= 300:
        raise GuardError("network guard timeout is outside its supported range")
    return value


def _drop_privileges() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    cap_last = int(Path("/proc/sys/kernel/cap_last_cap").read_text(encoding="ascii"))
    for capability in range(cap_last + 1):
        if prctl(24, capability, 0, 0, 0) != 0:  # PR_CAPBSET_DROP
            raise GuardError("agent capability bounding-set drop failed")
    os.setgroups([])
    os.setgid(AGENT_GID)
    os.setuid(AGENT_UID)
    if os.geteuid() != AGENT_UID or os.getegid() != AGENT_GID:
        raise GuardError("agent privilege drop failed")
    status = {}
    for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
        if ":" in line:
            key, value = line.split(":", maxsplit=1)
            status[key] = value.strip()
    for key in ("CapEff", "CapPrm", "CapAmb", "CapBnd"):
        if int(status.get(key, "0"), 16) != 0:
            raise GuardError("agent capability set is not empty")
    if status.get("NoNewPrivs") != "1":
        raise GuardError("no-new-privileges is not active")


def supervise_agent(
    services: dict[str, tuple[str, ...]],
    dns_process: subprocess.Popen[bytes],
) -> int:
    stop_requested = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_handlers = {
        signum: signal.signal(signum, request_stop)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    child_pid = os.fork()
    if child_pid == 0:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        try:
            _drop_privileges()
            os.execv(sys.executable, [sys.executable, "/app/heartbeat_agent.py"])
        except (OSError, GuardError):
            print("ERROR: sender privilege drop failed", file=sys.stderr)
            os._exit(1)

    current = services
    next_refresh = time.monotonic() + DNS_REFRESH_SECONDS
    try:
        while True:
            if stop_requested:
                os.kill(child_pid, signal.SIGTERM)
                stop_deadline = time.monotonic() + 10
                while time.monotonic() < stop_deadline:
                    waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
                    if waited_pid == child_pid:
                        return os.waitstatus_to_exitcode(status)
                    time.sleep(0.1)
                os.kill(child_pid, signal.SIGKILL)
                _, status = os.waitpid(child_pid, 0)
                return os.waitstatus_to_exitcode(status)
            waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == child_pid:
                return os.waitstatus_to_exitcode(status)
            if time.monotonic() >= next_refresh:
                try:
                    updated = _service_map_from_docker_dns()
                    current = refresh_service_map(current, updated, dns_process)
                except (OSError, GuardError, subprocess.SubprocessError):
                    print(
                        "WARN: private service DNS refresh failed; egress remains restricted",
                        file=sys.stderr,
                    )
                next_refresh = time.monotonic() + DNS_REFRESH_SECONDS
            time.sleep(1)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if dns_process.poll() is None:
            dns_process.terminate()
            try:
                dns_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                dns_process.kill()


def main() -> int:
    if os.geteuid() != 0:
        print("ERROR: network guard must start as container root", file=sys.stderr)
        return 1

    dns_process: subprocess.Popen[bytes] | None = None
    stage = "bootstrap firewall"
    try:
        timeout = _read_startup_timeout()
        apply_firewall(build_bootstrap_firewall_commands())
        stage = "fixed internal service DNS and proxy readiness"
        started = time.monotonic()
        services: dict[str, tuple[str, ...]] | None = None
        while time.monotonic() - started < timeout:
            try:
                services = _service_map_from_docker_dns()
                with socket.create_connection((PROXY_IP, PROXY_PORT), timeout=1.0):
                    pass
                break
            except (OSError, GuardError):
                time.sleep(2)
        if services is None:
            raise GuardError("required local services or proxy did not become ready")

        stage = "local fixed-record DNS"
        write_dnsmasq_files(services)
        dns_process = start_dnsmasq(services["prometheus"])
        for hostname, addresses in services.items():
            if validate_service_addresses(dns_query_a(hostname, LOCAL_DNS)) != addresses:
                raise GuardError("local DNS filter returned an unexpected address")

        stage = "final fail-closed firewall"
        apply_firewall(build_final_firewall_commands(services))
        stage = "sender privilege handoff"
        return supervise_agent(services, dns_process)
    except (OSError, GuardError, subprocess.SubprocessError):
        if dns_process is not None:
            dns_process.terminate()
            try:
                dns_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                dns_process.kill()
        print(
            f"ERROR: fail-closed heartbeat setup failed during {stage}; agent not started",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
