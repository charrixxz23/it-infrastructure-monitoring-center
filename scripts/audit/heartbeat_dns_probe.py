"""Run DNS A/AAAA and musl getaddrinfo regression checks inside isolated Alpine."""

from __future__ import annotations

import ipaddress
import platform
import socket
import struct
import subprocess
import time
from pathlib import Path

import heartbeat_entrypoint as guard


SERVICES = {
    "prometheus": "192.0.2.10",
    "alertmanager": "192.0.2.11",
    "grafana": "192.0.2.12",
    "incident-logger": "192.0.2.13",
}
DNSMASQ_CONFIG = Path("/tmp/heartbeat-dnsmasq.conf")


def _skip_dns_name(packet: bytes, offset: int) -> int:
    while offset < len(packet):
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            return offset + 2
        if length == 0:
            return offset + 1
        if length & 0xC0 or offset + length + 1 > len(packet):
            raise RuntimeError("malformed synthetic DNS response")
        offset += length + 1
    raise RuntimeError("truncated synthetic DNS response")


def query_dns(hostname: str, record_type: int) -> tuple[int, tuple[str, ...]]:
    labels = hostname.rstrip(".").split(".")
    name = b"".join(bytes((len(label),)) + label.encode("ascii") for label in labels)
    question = name + b"\x00" + struct.pack("!HH", record_type, 1)
    packet = struct.pack("!HHHHHH", 0x4D31, 0x0100, 1, 0, 0, 0) + question

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(2)
        client.sendto(packet, (guard.LOCAL_DNS, 53))
        response, _ = client.recvfrom(4096)

    if len(response) < 12 or struct.unpack("!H", response[:2])[0] != 0x4D31:
        raise RuntimeError("invalid synthetic DNS response")
    _, flags, questions, answers, _, _ = struct.unpack("!HHHHHH", response[:12])
    offset = 12
    for _ in range(questions):
        offset = _skip_dns_name(response, offset) + 4

    records: list[str] = []
    for _ in range(answers):
        offset = _skip_dns_name(response, offset)
        if offset + 10 > len(response):
            raise RuntimeError("truncated synthetic DNS answer")
        answer_type, record_class, _, length = struct.unpack(
            "!HHIH", response[offset : offset + 10]
        )
        offset += 10
        data = response[offset : offset + length]
        if len(data) != length:
            raise RuntimeError("truncated synthetic DNS record")
        if record_class == 1 and answer_type == 1 and length == 4:
            records.append(str(ipaddress.IPv4Address(data)))
        elif record_class == 1 and answer_type == 28 and length == 16:
            records.append(str(ipaddress.IPv6Address(data)))
        offset += length

    return flags & 0x0F, tuple(records)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    libc = subprocess.run(
        ["ldd", "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    require("musl" in (libc.stdout + libc.stderr).lower(), "Alpine musl is required")
    dnsmasq = subprocess.run(
        ["dnsmasq", "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=2,
    )
    require("version 2.92" in dnsmasq.stdout, "pinned dnsmasq 2.92 is required")
    require(platform.system() == "Linux", "the probe must run in isolated Alpine Linux")

    services = {name: (address,) for name, address in SERVICES.items()}
    guard.write_dnsmasq_files(services)
    config = DNSMASQ_CONFIG.read_text(encoding="ascii")
    require("no-resolv\n" in config, "external DNS forwarding must remain disabled")
    require("no-hosts\n" in config, "system hosts fallback must remain disabled")
    require("domain-needed\n" in config, "unqualified names must remain local")
    require("\nserver=" not in config, "no upstream DNS server may be configured")

    resolver = subprocess.Popen(
        ["dnsmasq", "--keep-in-foreground", f"--conf-file={DNSMASQ_CONFIG}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                rcode, addresses = query_dns("prometheus", 1)
                if rcode == 0 and addresses == (SERVICES["prometheus"],):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("local synthetic DNS did not become ready")

        for hostname, expected_address in SERVICES.items():
            a_rcode, a_records = query_dns(hostname, 1)
            aaaa_rcode, aaaa_records = query_dns(hostname, 28)
            require(a_rcode == 0 and expected_address in a_records, "A lookup failed")
            require(
                aaaa_rcode == 0 and not aaaa_records,
                "AAAA must be NOERROR/NODATA for an IPv4-only internal name",
            )

            unspecified = socket.getaddrinfo(
                hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            )
            ipv4_only = socket.getaddrinfo(
                hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM
            )
            require(
                any(result[0] == socket.AF_INET for result in unspecified),
                "AF_UNSPEC did not return the local IPv4 address",
            )
            require(
                all(result[0] == socket.AF_INET for result in unspecified),
                "AF_UNSPEC returned an unauthorized address family",
            )
            require(
                all(result[0] == socket.AF_INET for result in ipv4_only),
                "AF_INET returned a non-IPv4 result",
            )
            require(
                any(result[4][0] == expected_address for result in ipv4_only),
                "AF_INET did not return the expected local record",
            )

        for hostname in ("unapproved", "not-allowlisted.invalid"):
            for record_type in (1, 28):
                rcode, records = query_dns(hostname, record_type)
                require(not records, "a non-allowlisted name returned an address")
                if hostname == "unapproved":
                    require(rcode in (0, 3), "unqualified name was not denied locally")
            try:
                socket.getaddrinfo(
                    hostname,
                    None,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                )
            except socket.gaierror:
                continue
            raise RuntimeError("a non-allowlisted name resolved successfully")

        print(
            "PASS: dnsmasq 2.92 + musl; 4 A records, 4 AAAA NODATA replies, "
            "AF_UNSPEC and AF_INET resolution, and non-allowlisted names denied."
        )
        return 0
    finally:
        resolver.terminate()
        try:
            resolver.wait(timeout=2)
        except subprocess.TimeoutExpired:
            resolver.kill()
            resolver.wait(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
