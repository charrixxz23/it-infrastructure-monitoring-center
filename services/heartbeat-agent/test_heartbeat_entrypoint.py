from __future__ import annotations

import ipaddress
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import heartbeat_entrypoint as guard


SERVICES = {
    "prometheus": ("172.20.0.10",),
    "alertmanager": ("172.20.0.11",),
    "grafana": ("172.20.0.12",),
    "incident-logger": ("172.20.0.13",),
}


class FakeDnsServer:
    def __init__(self, address: str = "172.20.0.10") -> None:
        self.address = ipaddress.IPv4Address(address)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(2)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def address_port(self) -> tuple[str, int]:
        return self.socket.getsockname()

    def _serve(self) -> None:
        try:
            request, client = self.socket.recvfrom(2048)
            question = request[12:]
            response = (
                request[:2]
                + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0)
                + question
                + b"\xc0\x0c"
                + struct.pack("!HHIH", 1, 1, 30, 4)
                + self.address.packed
            )
            self.socket.sendto(response, client)
        except (OSError, TimeoutError):
            return

    def close(self) -> None:
        self.socket.close()
        self.thread.join(timeout=1)


class HeartbeatEntrypointTests(unittest.TestCase):
    def test_bounded_dns_query_reads_only_ipv4_answer(self) -> None:
        server = FakeDnsServer()
        try:
            addresses = guard.dns_query_a(
                "prometheus",
                server.address_port[0],
                timeout=1,
                port=server.address_port[1],
            )
        finally:
            server.close()

        self.assertEqual(addresses, ("172.20.0.10",))

    def test_service_address_validation_rejects_public_and_loopback(self) -> None:
        self.assertEqual(
            guard.validate_service_addresses(("172.20.0.10",)),
            ("172.20.0.10",),
        )
        for invalid in ("1.1.1.1", "127.0.0.1", "169.254.1.2", "::1"):
            with self.subTest(address=invalid):
                with self.assertRaises(guard.GuardError):
                    guard.validate_service_addresses((invalid,))

    def test_docker_dns_helper_rejects_non_allowlisted_names_without_querying(
        self,
    ) -> None:
        for hostname in ("unapproved", "hc-ping.com"):
            with self.subTest(hostname=hostname):
                with patch.object(guard.socket, "socket") as create_socket:
                    with self.assertRaises(guard.GuardError):
                        guard.dns_query_a(hostname, guard.DNS_SERVER)
                create_socket.assert_not_called()

    def test_final_firewall_allows_only_named_service_ports_and_proxy(self) -> None:
        commands = guard.build_final_firewall_commands(SERVICES)
        output_rules = [
            command
            for command in commands
            if command[:3] == ["iptables", "-A", "ITMC_HB_OUT"]
        ]

        self.assertIn(
            [
                "iptables",
                "-A",
                "ITMC_HB_OUT",
                "-d",
                "127.0.0.11/32",
                "-j",
                "DROP",
            ],
            output_rules,
        )
        root_dns_rules = [
            command
            for command in output_rules
            if "--uid-owner" in command and command[command.index("--uid-owner") + 1] == "0"
        ]
        self.assertEqual(
            root_dns_rules[:2],
            [
                [
                    "iptables", "-A", "ITMC_HB_OUT", "-p", "udp", "-d",
                    "127.0.0.11/32", "-m", "owner", "--uid-owner", "0", "-j", "ACCEPT",
                ],
                [
                    "iptables", "-A", "ITMC_HB_OUT", "-p", "tcp", "-d",
                    "127.0.0.11/32", "-m", "owner", "--uid-owner", "0", "-j", "ACCEPT",
                ],
            ],
        )
        self.assertFalse(any("--dport" in command for command in root_dns_rules[:2]))
        self.assertLess(
            output_rules.index(
                [
                    "iptables",
                    "-A",
                    "ITMC_HB_OUT",
                    "-d",
                    "127.0.0.11/32",
                    "-j",
                    "DROP",
                ]
            ),
            output_rules.index(
                ["iptables", "-A", "ITMC_HB_OUT", "-o", "lo", "-j", "ACCEPT"]
            ),
        )
        self.assertIn(
            [
                "iptables",
                "-A",
                "ITMC_HB_OUT",
                "-m",
                "owner",
                "--uid-owner",
                "65534",
                "-p",
                "tcp",
                "-d",
                "172.20.0.10",
                "--dport",
                "9090",
                "-j",
                "ACCEPT",
            ],
            output_rules,
        )
        self.assertIn(
            [
                "iptables",
                "-A",
                "ITMC_HB_OUT",
                "-m",
                "owner",
                "--uid-owner",
                "65534",
                "-p",
                "tcp",
                "-d",
                guard.PROXY_IP,
                "--dport",
                str(guard.PROXY_PORT),
                "-j",
                "ACCEPT",
            ],
            output_rules,
        )
        self.assertEqual(
            output_rules[-1],
            ["iptables", "-A", "ITMC_HB_OUT", "-j", "DROP"],
        )

        ipv6_output_rules = [
            command
            for command in commands
            if command[:3] == ["ip6tables", "-A", "ITMC_HB6_OUT"]
        ]
        self.assertEqual(
            ipv6_output_rules,
            [
                [
                    "ip6tables",
                    "-A",
                    "ITMC_HB6_OUT",
                    "-o",
                    "lo",
                    "-j",
                    "ACCEPT",
                ],
                ["ip6tables", "-A", "ITMC_HB6_OUT", "-j", "DROP"],
            ],
        )

    def test_bootstrap_policy_allows_only_internal_dns_and_proxy_readiness(self) -> None:
        commands = guard.build_bootstrap_firewall_commands()
        output_rules = [
            command
            for command in commands
            if command[:3] == ["iptables", "-A", "ITMC_HB_OUT"]
        ]
        self.assertEqual(
            [command[3:6] for command in output_rules[:2]],
            [["-p", "udp", "-d"], ["-p", "tcp", "-d"]],
        )
        self.assertTrue(all("127.0.0.11/32" in command for command in output_rules[:2]))
        self.assertTrue(all(command[-2:] == ["-j", "ACCEPT"] for command in output_rules[:2]))
        self.assertTrue(all("--uid-owner" in command and "0" in command for command in output_rules[:2]))
        self.assertEqual(output_rules[-1], ["iptables", "-A", "ITMC_HB_OUT", "-j", "DROP"])
        ipv6_output_rules = [
            command
            for command in commands
            if command[:3] == ["ip6tables", "-A", "ITMC_HB6_OUT"]
        ]
        self.assertEqual(
            ipv6_output_rules[-1],
            ["ip6tables", "-A", "ITMC_HB6_OUT", "-j", "DROP"],
        )

    def test_local_dns_configuration_has_no_external_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hosts_file = Path(directory) / "hosts"
            config_file = Path(directory) / "dnsmasq.conf"
            with (
                patch.object(guard, "HOSTS_FILE", hosts_file),
                patch.object(guard, "DNSMASQ_CONFIG", config_file),
            ):
                guard.write_dnsmasq_files(SERVICES)

            configuration = config_file.read_text(encoding="ascii")
            hosts = hosts_file.read_text(encoding="ascii")

        self.assertIn("no-resolv\n", configuration)
        self.assertIn("no-hosts\n", configuration)
        self.assertIn("domain-needed\n", configuration)
        self.assertNotIn("\nserver=", configuration)
        records = {line.split()[1] for line in hosts.splitlines()}
        self.assertEqual(records, set(guard.DNS_HOSTS))
        self.assertEqual(len(hosts.splitlines()), len(guard.DNS_HOSTS))
        self.assertNotIn("hc-ping.com", hosts)

    def test_service_ip_refresh_keeps_temporary_union_then_tightens(self) -> None:
        updated = {
            hostname: tuple(
                address.replace(".10", ".20")
                .replace(".11", ".21")
                .replace(".12", ".22")
                .replace(".13", ".23")
                for address in addresses
            )
            for hostname, addresses in SERVICES.items()
        }
        dns_process = Mock()
        with (
            patch.object(guard, "apply_firewall") as apply,
            patch.object(guard, "_write_dns_hosts") as write_hosts,
            patch.object(
                guard,
                "dns_query_a",
                side_effect=lambda hostname, _server: updated[hostname],
            ),
        ):
            result = guard.refresh_service_map(SERVICES, updated, dns_process)

        self.assertEqual(result, updated)
        write_hosts.assert_called_once_with(updated)
        dns_process.send_signal.assert_called_once_with(guard.DNSMASQ_RELOAD_SIGNAL)
        self.assertEqual(apply.call_count, 2)
        union = apply.call_args_list[0].args[0]
        final = apply.call_args_list[1].args[0]
        self.assertIn("172.20.0.10", str(union))
        self.assertIn("172.20.0.20", str(union))
        self.assertNotIn("172.20.0.10", str(final))
        self.assertIn("172.20.0.20", str(final))

    def test_firewall_rule_failure_aborts_setup(self) -> None:
        with patch.object(
            guard.subprocess,
            "run",
            return_value=Mock(returncode=1),
        ):
            with self.assertRaises(guard.GuardError):
                guard.apply_firewall(guard.build_bootstrap_firewall_commands())

    def test_firewall_stages_drop_terminated_chains_before_hook_switch(self) -> None:
        calls: list[list[str]] = []

        def run(command: list[str], **_kwargs: object) -> Mock:
            calls.append(command)
            return Mock(returncode=0, stdout="")

        with patch.object(guard.subprocess, "run", side_effect=run):
            guard.apply_firewall(guard.build_bootstrap_firewall_commands())

        hook_switches = [
            index
            for index, command in enumerate(calls)
            if command[1] in ("-I", "-R") and command[2] in ("OUTPUT", "INPUT")
        ]
        self.assertEqual(len(hook_switches), 4)
        self.assertTrue(all(command[1] == "-A" for command in calls[: hook_switches[0]] if command[1] == "-A"))
        self.assertTrue(
            all(
                any(
                    command[1:3] == ["-A", chain]
                    and command[-2:] == ["-j", "DROP"]
                    for command in calls[:switch_index]
                )
                for switch_index, chain in zip(
                    hook_switches,
                    ("ITMC_HB_OUT_A", "ITMC_HB_IN_A", "ITMC_HB6_OUT_A", "ITMC_HB6_IN_A"),
                    strict=True,
                )
            )
        )

    def test_failed_staging_keeps_existing_hook_unmodified(self) -> None:
        calls: list[list[str]] = []

        def run(command: list[str], **_kwargs: object) -> Mock:
            calls.append(command)
            if command[1] == "-S" and command[2] in ("OUTPUT", "INPUT"):
                return Mock(returncode=0, stdout="-P OUTPUT ACCEPT\n" if command[2] == "OUTPUT" else "-P INPUT ACCEPT\n")
            if command[1] == "-A" and command[-2:] == ["-j", "DROP"]:
                return Mock(returncode=1, stdout="")
            return Mock(returncode=0, stdout="")

        with patch.object(guard.subprocess, "run", side_effect=run):
            with self.assertRaises(guard.GuardError):
                guard.apply_firewall(guard.build_bootstrap_firewall_commands())

        self.assertFalse(any(command[1] in ("-I", "-R") for command in calls))

    def test_active_firewall_refresh_uses_single_rule_replacement(self) -> None:
        calls: list[list[str]] = []

        def run(command: list[str], **_kwargs: object) -> Mock:
            calls.append(command)
            if command[1] == "-S" and command[2] in ("OUTPUT", "INPUT"):
                builtin = command[2]
                base = {
                    ("iptables", "OUTPUT"): "ITMC_HB_OUT",
                    ("iptables", "INPUT"): "ITMC_HB_IN",
                    ("ip6tables", "OUTPUT"): "ITMC_HB6_OUT",
                    ("ip6tables", "INPUT"): "ITMC_HB6_IN",
                }[(command[0], builtin)]
                return Mock(
                    returncode=0,
                    stdout=f"-P {builtin} ACCEPT\n-A {builtin} -j {base}_A\n",
                )
            return Mock(returncode=0, stdout="")

        with patch.object(guard.subprocess, "run", side_effect=run):
            guard.apply_firewall(guard.build_final_firewall_commands(SERVICES))

        switches = [command for command in calls if command[1] in ("-I", "-R")]
        self.assertEqual(len(switches), 4)
        self.assertTrue(all(command[1:4] == ["-R", command[2], "1"] for command in switches))
        self.assertTrue(all(command[-1].endswith("_B") for command in switches))


if __name__ == "__main__":
    unittest.main()
