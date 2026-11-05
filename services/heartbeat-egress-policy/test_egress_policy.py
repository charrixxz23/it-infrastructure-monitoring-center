from __future__ import annotations

import socket
import struct
import threading
import unittest
from unittest.mock import Mock, patch

import egress_policy as policy


def encode_name(name: str) -> bytes:
    return b"".join(
        bytes((len(label),)) + label.encode("ascii")
        for label in name.rstrip(".").split(".")
    ) + b"\x00"


class FakeDnsServer:
    def __init__(self, cname: bool = True, wrong_source: bool = False) -> None:
        self.cname = cname
        self.wrong_source = wrong_source
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(2)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def address(self) -> str:
        return str(self.socket.getsockname()[0])

    @property
    def port(self) -> int:
        return int(self.socket.getsockname()[1])

    def _serve(self) -> None:
        try:
            request, client = self.socket.recvfrom(2048)
            question_end = 12 + len(encode_name(policy.PROVIDER_HOST)) + 4
            question = request[12:question_end]
            answers = bytearray()
            count = 1
            if self.cname:
                target = encode_name("edge.example.net")
                target_offset = question_end + 2 + 10
                answers.extend(b"\xc0\x0c")
                answers.extend(struct.pack("!HHIH", 5, 1, 10, len(target)))
                answers.extend(target)
                answers.extend(struct.pack("!H", 0xC000 | target_offset))
                answers.extend(struct.pack("!HHIH", 1, 1, 20, 4))
                answers.extend(bytes((8, 8, 8, 8)))
                count = 2
            else:
                answers.extend(b"\xc0\x0c")
                answers.extend(struct.pack("!HHIH", 1, 1, 30, 4))
                answers.extend(bytes((8, 8, 8, 8)))
            header = request[:2] + struct.pack("!HHHHH", 0x8180, 1, count, 0, 0)
            response = header + question + answers
            if self.wrong_source:
                spoofed = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                spoofed.bind(("127.0.0.1", 0))
                spoofed.sendto(response, client)
                spoofed.close()
            else:
                self.socket.sendto(response, client)
        except (OSError, TimeoutError):
            return

    def close(self) -> None:
        self.socket.close()
        self.thread.join(timeout=1)


class EgressPolicyTests(unittest.TestCase):
    def test_dns_parser_follows_cname_and_uses_shortest_ttl(self) -> None:
        server = FakeDnsServer(cname=True)
        try:
            addresses, ttl = policy.resolve_provider_ipv4(
                server=server.address, timeout=1, port=server.port
            )
        finally:
            server.close()
        self.assertEqual(addresses, ("8.8.8.8",))
        self.assertEqual(ttl, 10)

    def test_dns_parser_accepts_only_fixed_hostname_a_query(self) -> None:
        server = FakeDnsServer(cname=False)
        try:
            addresses, ttl = policy.resolve_provider_ipv4(
                server=server.address, timeout=1, port=server.port
            )
        finally:
            server.close()
        self.assertEqual(addresses, ("8.8.8.8",))
        self.assertEqual(ttl, 30)

    def test_dns_parser_rejects_response_from_unexpected_source(self) -> None:
        server = FakeDnsServer(cname=False, wrong_source=True)
        try:
            with self.assertRaises(policy.PolicyError):
                policy.resolve_provider_ipv4(
                    server=server.address, timeout=1, port=server.port
                )
        finally:
            server.close()

    def test_provider_address_validation_rejects_non_global_and_excessive_results(self) -> None:
        self.assertEqual(
            policy.validate_provider_addresses(("8.8.8.8",)), ("8.8.8.8",)
        )
        for address in ("127.0.0.1", "10.0.0.1", "203.0.113.8", "::1"):
            with self.subTest(address=address), self.assertRaises(policy.PolicyError):
                policy.validate_provider_addresses((address,))
        too_many = tuple(f"8.8.8.{index}" for index in range(1, 18))
        with self.assertRaises(policy.PolicyError):
            policy.validate_provider_addresses(too_many)

    def test_firewall_allows_only_proxy_uid_to_current_provider_ip_and_port(self) -> None:
        commands = policy.build_firewall_commands(("8.8.8.8",))
        output_rules = [
            command for command in commands if command[:3] == ["iptables", "-A", "ITMC_HE_OUT"]
        ]
        self.assertIn(
            [
                "iptables", "-A", "ITMC_HE_OUT", "-m", "owner", "--uid-owner",
                "13", "-p", "tcp", "-d", "8.8.8.8", "--dport", "443", "-j", "ACCEPT",
            ],
            output_rules,
        )
        self.assertEqual(output_rules[-1], ["iptables", "-A", "ITMC_HE_OUT", "-j", "DROP"])
        dns_rules = [
            command
            for command in output_rules
            if "127.0.0.11/32" in command and "--uid-owner" in command
        ]
        self.assertEqual(len(dns_rules), 2)
        self.assertTrue(all(command[command.index("--uid-owner") + 1] == "0" for command in dns_rules))
        self.assertTrue(all("--dport" not in command for command in dns_rules))
        resolver_deny = [
            "iptables", "-A", "ITMC_HE_OUT", "-d", "127.0.0.11/32", "-j", "DROP"
        ]
        loopback_allow = ["iptables", "-A", "ITMC_HE_OUT", "-o", "lo", "-j", "ACCEPT"]
        self.assertIn(resolver_deny, output_rules)
        self.assertLess(output_rules.index(resolver_deny), output_rules.index(loopback_allow))

    def test_bootstrap_and_empty_policy_have_no_provider_egress(self) -> None:
        for candidate in (None, ()):
            commands = policy.build_firewall_commands(candidate)
            output_rules = [
                command for command in commands if command[:3] == ["iptables", "-A", "ITMC_HE_OUT"]
            ]
            self.assertFalse(any("--dport" in command and "443" in command for command in output_rules))
            self.assertEqual(output_rules[-1][-2:], ["-j", "DROP"])
            ipv6 = [
                command for command in commands if command[:3] == ["ip6tables", "-A", "ITMC_HE6_OUT"]
            ]
            self.assertEqual(ipv6[-1][-2:], ["-j", "DROP"])

    def test_inbound_proxy_is_restricted_to_the_agent_address_and_listener(self) -> None:
        commands = policy.build_firewall_commands(("8.8.8.8",))
        input_rules = [
            command for command in commands if command[:3] == ["iptables", "-A", "ITMC_HE_IN"]
        ]
        self.assertIn(
            [
                "iptables", "-A", "ITMC_HE_IN", "-s", "172.31.255.251/32",
                "-p", "tcp", "--dport", "3128", "-j", "ACCEPT",
            ],
            input_rules,
        )
        self.assertEqual(input_rules[-1][-2:], ["-j", "DROP"])

    def test_firewall_default_drop_is_required_before_installing_hooks(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], capture: bool = False) -> Mock:
            calls.append(command)
            if command[1] == "-S":
                return Mock(returncode=0, stdout=f"-P {command[2]} ACCEPT\n")
            if command[:3] == ["ip6tables", "-P", "OUTPUT"]:
                return Mock(returncode=1, stdout="")
            return Mock(returncode=0, stdout="")

        with patch.object(policy, "_run_firewall", side_effect=fake_run):
            with self.assertRaises(policy.PolicyError):
                policy.apply_firewall(policy.build_firewall_commands())
        self.assertIn(["ip6tables", "-P", "OUTPUT", "DROP"], calls)
        self.assertFalse(any(command[1] in ("-I", "-R") for command in calls))

    def test_failed_rule_install_keeps_default_drop_and_does_not_switch_hooks(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], capture: bool = False) -> Mock:
            calls.append(command)
            if command[1] == "-S":
                return Mock(returncode=0, stdout=f"-P {command[2]} ACCEPT\n")
            if command[1] == "-A" and command[-2:] == ["-j", "DROP"]:
                return Mock(returncode=1, stdout="")
            return Mock(returncode=0, stdout="")

        with patch.object(policy, "_run_firewall", side_effect=fake_run):
            with self.assertRaises(policy.PolicyError):
                policy.apply_firewall(policy.build_firewall_commands(("8.8.8.8",)))
        self.assertTrue(any(command[1:3] == ["-P", "OUTPUT"] for command in calls))
        self.assertFalse(any(command[1] in ("-I", "-R") for command in calls))

    def test_emergency_deny_flushes_only_owned_output_chains(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], capture: bool = False) -> Mock:
            calls.append(command)
            if command[1] == "-S":
                chain = "ITMC_HE_OUT_A" if command[0] == "iptables" else "ITMC_HE6_OUT_B"
                return Mock(returncode=0, stdout=f"-P OUTPUT DROP\n-A OUTPUT -j {chain}\n")
            return Mock(returncode=0, stdout="")

        with patch.object(policy, "_run_firewall", side_effect=fake_run):
            self.assertTrue(policy._emergency_deny_egress())

        self.assertIn(["iptables", "-F", "ITMC_HE_OUT_A"], calls)
        self.assertIn(["ip6tables", "-F", "ITMC_HE6_OUT_B"], calls)
        self.assertTrue(all(command[1:4] == ["-P", "OUTPUT", "DROP"] for command in calls if command[1] == "-P"))

    def test_dns_refresh_closes_egress_before_swapping_records(self) -> None:
        operations: list[object] = []
        process = Mock()
        process.send_signal.side_effect = lambda signum: operations.append(("signal", signum))

        with (
            patch.object(policy, "_install_provider_policy", side_effect=lambda ips: operations.append(("firewall", ips))),
            patch.object(policy, "write_dnsmasq_files", side_effect=lambda ips: operations.append(("hosts", ips))),
            patch.object(policy, "_query_local_provider", return_value=("9.9.9.9",)),
        ):
            result = policy._refresh_provider(("8.8.8.8",), ("9.9.9.9",), process)

        self.assertEqual(result, ("9.9.9.9",))
        self.assertEqual(
            operations,
            [
                ("firewall", ()),
                ("hosts", ("9.9.9.9",)),
                ("signal", policy.DNSMASQ_RELOAD_SIGNAL),
                ("firewall", ("9.9.9.9",)),
            ],
        )

    def test_failed_local_dns_reload_leaves_provider_egress_closed(self) -> None:
        installed: list[tuple[str, ...]] = []
        process = Mock()
        with (
            patch.object(policy, "_install_provider_policy", side_effect=lambda ips: installed.append(ips)),
            patch.object(policy, "write_dnsmasq_files"),
            patch.object(policy, "_query_local_provider", side_effect=OSError("synthetic timeout")),
            patch.object(policy.time, "monotonic", side_effect=[0, 10]),
        ):
            with self.assertRaises(policy.PolicyError):
                policy._refresh_provider(("8.8.8.8",), ("9.9.9.9",), process)
        self.assertEqual(installed, [()])


if __name__ == "__main__":
    unittest.main()
