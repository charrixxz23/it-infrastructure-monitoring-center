"""Install a namespace-scoped, fail-closed egress policy for the Squid proxy."""

from __future__ import annotations

import ipaddress
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path


PROVIDER_HOST = "hc-ping.com"
PROVIDER_PORT = 443
PROXY_UID = 13
PROXY_PORT = 3128
AGENT_IP = "172.31.255.251"
PROXY_IP = "172.31.255.250"
DOCKER_DNS = "127.0.0.11"
LOCAL_DNS = "127.0.0.1"
DNS_TIMEOUT_SECONDS = 2.0
DNS_REFRESH_MAX_SECONDS = 15
MAX_PROVIDER_ADDRESSES = 16
MAX_DNS_TTL_SECONDS = 300
DNSMASQ_RELOAD_SIGNAL = getattr(signal, "SIGHUP", 1)
READY_FILE = Path("/run/heartbeat-egress-policy-ready")
HOSTS_FILE = Path("/run/heartbeat-provider-hosts")
DNSMASQ_CONFIG = Path("/run/heartbeat-provider-dnsmasq.conf")
DNSMASQ_PID = Path("/run/heartbeat-provider-dnsmasq.pid")


class PolicyError(RuntimeError):
    """An error message safe to print without DNS or request payloads."""


def _encode_name(hostname: str) -> bytes:
    labels = hostname.rstrip(".").split(".")
    if not labels or any(not label or len(label) > 63 for label in labels):
        raise PolicyError("invalid fixed DNS name")
    try:
        return b"".join(
            bytes((len(label),)) + label.encode("ascii") for label in labels
        ) + b"\x00"
    except UnicodeEncodeError as error:
        raise PolicyError("invalid fixed DNS name") from error


def _read_name(packet: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    cursor = offset
    next_offset: int | None = None
    visited: set[int] = set()
    for _ in range(128):
        if cursor >= len(packet):
            raise PolicyError("truncated DNS name")
        length = packet[cursor]
        if length == 0:
            if next_offset is None:
                next_offset = cursor + 1
            return ".".join(labels).lower() + ".", next_offset
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(packet):
                raise PolicyError("truncated DNS pointer")
            pointer = ((length & 0x3F) << 8) | packet[cursor + 1]
            if pointer >= len(packet) or pointer in visited:
                raise PolicyError("invalid DNS compression pointer")
            visited.add(pointer)
            if next_offset is None:
                next_offset = cursor + 2
            cursor = pointer
            continue
        if length & 0xC0 or length > 63 or cursor + 1 + length > len(packet):
            raise PolicyError("invalid DNS label")
        try:
            labels.append(packet[cursor + 1 : cursor + 1 + length].decode("ascii"))
        except UnicodeDecodeError as error:
            raise PolicyError("invalid DNS label") from error
        cursor += length + 1
    raise PolicyError("DNS name exceeds the supported pointer depth")


def resolve_provider_ipv4(
    server: str = DOCKER_DNS,
    timeout: float = DNS_TIMEOUT_SECONDS,
    port: int = 53,
) -> tuple[tuple[str, ...], int]:
    """Resolve only the fixed provider hostname and return its A records and TTL."""
    transaction_id = int.from_bytes(os.urandom(2), "big")
    question = _encode_name(PROVIDER_HOST) + struct.pack("!HH", 1, 1)
    query = struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as resolver:
        resolver.settimeout(timeout)
        resolver.sendto(query, (server, port))
        response, response_source = resolver.recvfrom(4096)

    if response_source != (server, port):
        raise PolicyError("provider DNS response source does not match the resolver")

    if len(response) < 12:
        raise PolicyError("provider DNS response is truncated")
    response_id, flags, question_count, answer_count, _, _ = struct.unpack(
        "!HHHHHH", response[:12]
    )
    if (
        response_id != transaction_id
        or not flags & 0x8000
        or flags & 0x0200
        or flags & 0x000F
        or question_count != 1
        or answer_count > 64
    ):
        raise PolicyError("provider DNS response is invalid")

    qname, offset = _read_name(response, 12)
    if qname != f"{PROVIDER_HOST}." or offset + 4 > len(response):
        raise PolicyError("provider DNS question does not match the fixed hostname")
    if struct.unpack("!HH", response[offset : offset + 4]) != (1, 1):
        raise PolicyError("provider DNS question type is invalid")
    offset += 4

    cname_records: dict[str, tuple[str, int]] = {}
    a_records: dict[str, list[tuple[str, int]]] = {}
    for _ in range(answer_count):
        owner, offset = _read_name(response, offset)
        if offset + 10 > len(response):
            raise PolicyError("provider DNS answer is truncated")
        record_type, record_class, ttl, data_length = struct.unpack(
            "!HHIH", response[offset : offset + 10]
        )
        offset += 10
        end = offset + data_length
        if end > len(response):
            raise PolicyError("provider DNS answer is truncated")
        if record_class == 1 and record_type == 1 and data_length == 4:
            a_records.setdefault(owner, []).append(
                (str(ipaddress.IPv4Address(response[offset:end])), ttl)
            )
        elif record_class == 1 and record_type == 5:
            target, consumed = _read_name(response, offset)
            if consumed > end:
                raise PolicyError("provider DNS CNAME is malformed")
            cname_records[owner] = (target, ttl)
        offset = end

    addresses: set[str] = set()
    ttls: list[int] = []
    current_name = f"{PROVIDER_HOST}."
    visited_names: set[str] = set()
    for _ in range(9):
        if current_name in visited_names:
            raise PolicyError("provider DNS CNAME loop detected")
        visited_names.add(current_name)
        for address, ttl in a_records.get(current_name, []):
            addresses.add(address)
            ttls.append(ttl)
        cname = cname_records.get(current_name)
        if cname is None:
            break
        current_name, ttl = cname
        ttls.append(ttl)
    else:
        raise PolicyError("provider DNS CNAME chain is too long")

    validated = validate_provider_addresses(tuple(sorted(addresses)))
    if not ttls:
        raise PolicyError("provider DNS response has no address lifetime")
    ttl_seconds = min(max(1, ttl) for ttl in ttls)
    return validated, min(ttl_seconds, MAX_DNS_TTL_SECONDS)


def validate_provider_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    if not addresses or len(addresses) > MAX_PROVIDER_ADDRESSES:
        raise PolicyError("provider DNS address count is outside the supported range")
    normalized: set[str] = set()
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as error:
            raise PolicyError("provider DNS returned an invalid address") from error
        if parsed.version != 4 or not parsed.is_global:
            raise PolicyError("provider DNS returned a non-public IPv4 address")
        normalized.add(str(parsed))
    return tuple(sorted(normalized))


def _firewall_groups(
    provider_addresses: tuple[str, ...] | None,
) -> list[tuple[str, str, list[list[str]]]]:
    # The explicit construction keeps ordering obvious: loopback, narrowly scoped
    # resolver access, proxy responses, dynamic provider destinations, terminal DROP.
    output: list[list[str]] = []
    for protocol in ("udp", "tcp"):
        output.append(
            [
                "-p", protocol, "-d", f"{DOCKER_DNS}/32", "-m", "owner",
                "--uid-owner", "0", "-j", "ACCEPT",
            ]
        )
    output.extend(
        (
            ["-d", f"{DOCKER_DNS}/32", "-j", "DROP"],
            ["-o", "lo", "-j", "ACCEPT"],
        )
    )
    output.append(
        [
            "-m", "owner", "--uid-owner", str(PROXY_UID), "-p", "tcp",
            "--sport", str(PROXY_PORT), "-d", f"{AGENT_IP}/32",
            "-m", "conntrack", "--ctstate", "ESTABLISHED", "-j", "ACCEPT",
        ]
    )
    if provider_addresses:
        for address in provider_addresses:
            output.append(
                [
                    "-m", "owner", "--uid-owner", str(PROXY_UID), "-p", "tcp",
                    "-d", address, "--dport", str(PROVIDER_PORT), "-j", "ACCEPT",
                ]
            )
    output.append(["-j", "DROP"])

    input_rules = [
        ["-i", "lo", "-j", "ACCEPT"],
        [
            "-s", f"{AGENT_IP}/32", "-p", "tcp", "--dport", str(PROXY_PORT),
            "-j", "ACCEPT",
        ],
        ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ["-j", "DROP"],
    ]
    ipv6_output = [["-o", "lo", "-j", "ACCEPT"], ["-j", "DROP"]]
    ipv6_input = [
        ["-i", "lo", "-j", "ACCEPT"],
        ["-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ["-j", "DROP"],
    ]
    return [
        ("iptables", "OUTPUT", output),
        ("iptables", "INPUT", input_rules),
        ("ip6tables", "OUTPUT", ipv6_output),
        ("ip6tables", "INPUT", ipv6_input),
    ]


def build_firewall_commands(
    provider_addresses: tuple[str, ...] | None = None,
) -> list[list[str]]:
    """Build IPv4 allow rules for validated provider IPs and IPv6 deny-all rules."""
    if provider_addresses:
        provider_addresses = validate_provider_addresses(provider_addresses)
    elif provider_addresses is not None:
        provider_addresses = ()
    commands: list[list[str]] = []
    for binary, builtin, rules in _firewall_groups(provider_addresses):
        chain = {
            ("iptables", "OUTPUT"): "ITMC_HE_OUT",
            ("iptables", "INPUT"): "ITMC_HE_IN",
            ("ip6tables", "OUTPUT"): "ITMC_HE6_OUT",
            ("ip6tables", "INPUT"): "ITMC_HE6_IN",
        }[(binary, builtin)]
        commands.append([binary, "-N", chain])
        commands.extend([binary, "-A", chain, *rule] for rule in rules)
        commands.append([binary, "-I", builtin, "1", "-j", chain])
    return commands


def _run_firewall(command: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
        timeout=3,
    )


def apply_firewall(commands: list[list[str]]) -> None:
    """Stage restrictive chains then atomically replace each namespace hook."""
    groups: list[tuple[str, str, list[list[str]]]] = []
    for command in commands:
        if command[1] == "-N":
            groups.append((command[0], command[2], [command]))
        elif not groups:
            raise PolicyError("firewall command has no chain")
        else:
            groups[-1][2].append(command)

    prepared: list[tuple[str, str, str | None, str, list[list[str]]]] = []
    for binary, base, group in groups:
        hooks = [command for command in group if command[1] == "-I"]
        additions = [command for command in group if command[1] == "-A"]
        if len(hooks) != 1 or not additions or additions[-1][-2:] != ["-j", "DROP"]:
            raise PolicyError("firewall chain is incomplete or lacks terminal DROP")
        builtin = hooks[0][2]
        listing = _run_firewall([binary, "-S", builtin], capture=True)
        if listing.returncode != 0:
            raise PolicyError("firewall hook could not be inspected")
        active: list[tuple[int, str]] = []
        position = 0
        for line in listing.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[:2] == ["-A", builtin]:
                position += 1
                if len(parts) == 4 and parts[2] == "-j" and parts[3].startswith(base):
                    active.append((position, parts[3]))
        if len(active) > 1 or active and active[0][0] != 1:
            raise PolicyError("firewall hook is duplicated or not first")
        active_chain = active[0][1] if active else None
        if active_chain not in (None, f"{base}_A", f"{base}_B"):
            raise PolicyError("unknown firewall chain is active")
        candidate = f"{base}_B" if active_chain == f"{base}_A" else f"{base}_A"
        translated = [
            [candidate if value == base else value for value in command]
            for command in group
        ]
        prepared.append((binary, builtin, active_chain, candidate, translated))

    # This namespace is dedicated to the proxy. Default DROP protects startup
    # even if a later chain operation fails before its hook is installed.
    for binary, builtin in (
        ("iptables", "OUTPUT"),
        ("iptables", "INPUT"),
        ("ip6tables", "OUTPUT"),
        ("ip6tables", "INPUT"),
    ):
        if _run_firewall([binary, "-P", builtin, "DROP"]).returncode != 0:
            raise PolicyError("firewall default deny could not be applied")

    for binary, _, _, candidate, group in prepared:
        created = _run_firewall([binary, "-N", candidate])
        if created.returncode != 0:
            existing = _run_firewall([binary, "-S", candidate])
            if existing.returncode != 0:
                raise PolicyError("inactive firewall chain could not be created")
            if _run_firewall([binary, "-F", candidate]).returncode != 0:
                raise PolicyError("inactive firewall chain could not be cleared")
        for command in group:
            if command[1] in ("-N", "-I"):
                continue
            if _run_firewall(command).returncode != 0:
                raise PolicyError("firewall rule could not be applied")

    for binary, builtin, active_chain, candidate, _ in prepared:
        operation = "-R" if active_chain else "-I"
        hook = [binary, operation, builtin, "1", "-j", candidate]
        if _run_firewall(hook).returncode != 0:
            raise PolicyError("firewall hook could not be installed")


def write_dnsmasq_files(addresses: tuple[str, ...]) -> None:
    rows = [f"{address} {PROVIDER_HOST}" for address in addresses]
    temporary = HOSTS_FILE.with_suffix(".next")
    temporary.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="ascii")
    temporary.chmod(0o644)
    os.replace(temporary, HOSTS_FILE)
    DNSMASQ_CONFIG.write_text(
        "\n".join(
            (
                "no-resolv",
                "no-hosts",
                f"pid-file={DNSMASQ_PID}",
                "bind-interfaces",
                f"listen-address={LOCAL_DNS}",
                "port=53",
                f"addn-hosts={HOSTS_FILE}",
                "cache-size=0",
                "local-ttl=5",
                "log-facility=/dev/null",
                "user=dnsproxy",
                "group=dnsproxy",
            )
        )
        + "\n",
        encoding="ascii",
    )
    DNSMASQ_CONFIG.chmod(0o644)


def _query_local_provider(timeout: float = DNS_TIMEOUT_SECONDS) -> tuple[str, ...]:
    addresses, _ = resolve_provider_ipv4(LOCAL_DNS, timeout)
    return addresses


def _write_ready(ready: bool) -> None:
    if ready:
        READY_FILE.write_text("ready\n", encoding="ascii")
    else:
        READY_FILE.unlink(missing_ok=True)


def _install_provider_policy(addresses: tuple[str, ...]) -> None:
    apply_firewall(build_firewall_commands(addresses))


def _emergency_deny_egress() -> bool:
    """Flush only our active namespace OUTPUT hooks and retain built-in DROP."""
    for binary, builtin, base in (
        ("iptables", "OUTPUT", "ITMC_HE_OUT"),
        ("ip6tables", "OUTPUT", "ITMC_HE6_OUT"),
    ):
        listing = _run_firewall([binary, "-S", builtin], capture=True)
        if listing.returncode != 0:
            return False
        active = []
        for line in listing.stdout.splitlines():
            parts = line.split()
            if (
                len(parts) == 4
                and parts[:3] == ["-A", builtin, "-j"]
                and parts[3] in (f"{base}_A", f"{base}_B")
            ):
                active.append(parts[3])
        if len(active) > 1:
            return False
        if active and _run_firewall([binary, "-F", active[0]]).returncode != 0:
            return False
        if _run_firewall([binary, "-P", builtin, "DROP"]).returncode != 0:
            return False
    return True


def _refresh_provider(
    current: tuple[str, ...],
    updated: tuple[str, ...],
    dns_process: subprocess.Popen[bytes],
) -> tuple[str, ...]:
    if current == updated:
        return current
    # Close the egress window before changing the local DNS view. This avoids
    # temporarily allowing both old and new CDN addresses during a DNS rotation.
    _install_provider_policy(())
    write_dnsmasq_files(updated)
    dns_process.send_signal(DNSMASQ_RELOAD_SIGNAL)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            if _query_local_provider() == updated:
                _install_provider_policy(updated)
                return updated
        except (OSError, PolicyError):
            time.sleep(0.1)
    raise PolicyError("local provider DNS did not reload")


def _start_dnsmasq(expected: tuple[str, ...]) -> subprocess.Popen[bytes]:
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
            raise PolicyError("local provider DNS did not start")
        try:
            if _query_local_provider() == expected:
                return process
        except (OSError, PolicyError):
            time.sleep(0.1)
    process.terminate()
    raise PolicyError("local provider DNS did not become ready")


def _refresh_interval(ttl: int) -> int:
    return max(1, min(DNS_REFRESH_MAX_SECONDS, ttl // 2 or 1))


def _stop_dnsmasq(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def run_policy() -> int:
    if os.geteuid() != 0:
        print("ERROR: egress policy must start as container root", file=sys.stderr)
        return 1

    _write_ready(False)
    dns_process: subprocess.Popen[bytes] | None = None
    stage = "bootstrap firewall"
    try:
        apply_firewall(build_firewall_commands())
        stage = "fixed provider DNS resolution"
        addresses, ttl = resolve_provider_ipv4()
        stage = "local allowlisted DNS startup"
        write_dnsmasq_files(addresses)
        dns_process = _start_dnsmasq(addresses)
        stage = "provider egress firewall"
        _install_provider_policy(addresses)
        _write_ready(True)
    except (OSError, PolicyError, subprocess.SubprocessError):
        _stop_dnsmasq(dns_process) if dns_process is not None else None
        print(
            f"ERROR: fail-closed proxy egress setup failed during {stage}",
            file=sys.stderr,
        )
        return 1

    stop = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    previous = {
        signum: signal.signal(signum, request_stop)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    current = addresses
    refresh_at = time.monotonic() + _refresh_interval(ttl)
    try:
        while not stop:
            if time.monotonic() >= refresh_at:
                try:
                    updated, ttl = resolve_provider_ipv4()
                    current = _refresh_provider(current, updated, dns_process)
                    _write_ready(True)
                except (OSError, PolicyError, subprocess.SubprocessError):
                    deny_confirmed = False
                    try:
                        _install_provider_policy(())
                        deny_confirmed = True
                    except (OSError, PolicyError, subprocess.SubprocessError):
                        deny_confirmed = _emergency_deny_egress()
                    try:
                        write_dnsmasq_files(())
                        dns_process.send_signal(DNSMASQ_RELOAD_SIGNAL)
                    except (OSError, PolicyError, subprocess.SubprocessError):
                        pass
                    current = ()
                    _write_ready(False)
                    message = (
                        "WARN: provider DNS refresh failed; proxy egress is denied"
                        if deny_confirmed
                        else "WARN: provider DNS refresh failed; prior firewall state could not be cleared"
                    )
                    print(message, file=sys.stderr)
                refresh_at = time.monotonic() + (
                    _refresh_interval(ttl) if current else DNS_REFRESH_MAX_SECONDS
                )
            time.sleep(0.5)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        _write_ready(False)
        if dns_process is not None:
            _stop_dnsmasq(dns_process)
    return 0


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--healthcheck":
        return 0 if READY_FILE.is_file() else 1
    if len(sys.argv) != 1:
        print("usage: egress_policy.py [--healthcheck]", file=sys.stderr)
        return 2
    return run_policy()


if __name__ == "__main__":
    raise SystemExit(main())
