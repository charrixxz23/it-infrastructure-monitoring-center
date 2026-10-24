from __future__ import annotations

import http.client
import subprocess
import threading
import unittest
from dataclasses import replace
from http.server import ThreadingHTTPServer

from container_exporter import (
    CollectionError,
    CollectionSnapshot,
    ContainerStats,
    DockerCliCollector,
    ExporterState,
    MetricsHandler,
    parse_project_containers,
    parse_stats_output,
)


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append((command, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def completed(command, stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class ParserTests(unittest.TestCase):
    def test_parse_sizes_and_stats_units(self):
        containers = {"a" * 12: "prometheus"}
        samples = parse_stats_output(
            "a" * 12 + "|12.5%|1.5GiB / 4GiB|1.25MB / 2MiB\n",
            containers,
            100.0,
        )
        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertEqual(sample.service, "prometheus")
        self.assertEqual(sample.container_id, "a" * 12)
        self.assertEqual(sample.cpu_percent, 12.5)
        self.assertEqual(sample.memory_usage_bytes, 1.5 * 1024**3)
        self.assertEqual(sample.memory_limit_bytes, 4 * 1024**3)
        self.assertEqual(sample.network_receive_bytes_total, 1.25 * 1000**2)
        self.assertEqual(sample.network_transmit_bytes_total, 2 * 1024**2)

    def test_only_selected_project_ids_are_emitted(self):
        selected = {"a" * 12: "prometheus"}
        output = (
            "a" * 12 + "|0.5%|10MiB / 1GiB|1kB / 2kB\n"
            + "b" * 12
            + "|9%|50MiB / 1GiB|8kB / 9kB\n"
        )
        samples = parse_stats_output(output, selected, 100.0)
        self.assertEqual([item.service for item in samples], ["prometheus"])
        self.assertEqual([item.container_id for item in samples], ["a" * 12])

    def test_invalid_identity_and_stats_rows_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_project_containers("not-an-id|prometheus\n")
        with self.assertRaises(ValueError):
            parse_project_containers("a" * 12 + "|service with spaces\n")
        with self.assertRaises(ValueError):
            parse_stats_output("malformed\n", {"a" * 12: "prometheus"}, 100.0)


class CollectorTests(unittest.TestCase):
    def test_collect_uses_exact_compose_project_filter_and_stats_only(self):
        first_id = "a" * 12
        second_id = "b" * 12
        runner = FakeRunner(
            [
                completed("ps", f"{first_id}|prometheus\n{second_id}|grafana\n"),
                completed(
                    "stats",
                    f"{first_id}|1.0%|10MiB / 1GiB|1MB / 2MB\n"
                    f"{second_id}|2.0%|20MiB / 1GiB|3MB / 4MB\n",
                ),
            ]
        )
        result = DockerCliCollector(runner=runner, executable="docker.exe").collect()
        self.assertTrue(result.success)
        self.assertEqual({sample.service for sample in result.containers}, {"prometheus", "grafana"})
        self.assertEqual(len(result.containers), 2)
        ps_command, ps_options = runner.commands[0]
        self.assertEqual(ps_command[:4], ["docker.exe", "--context", "desktop-linux", "ps"])
        self.assertIn(
            "label=com.docker.compose.project=it-infrastructure-monitoring-center",
            ps_command,
        )
        self.assertIn("com.docker.compose.service", ps_command[-1])
        self.assertNotIn("inspect", ps_command)
        self.assertNotIn("logs", ps_command)
        self.assertEqual(ps_options["timeout"], 8.0)
        stats_command, _ = runner.commands[1]
        self.assertEqual(stats_command[3], "stats")
        self.assertIn("--no-stream", stats_command)
        self.assertNotIn("--all", stats_command)

    def test_empty_project_is_a_valid_empty_snapshot_without_stats_call(self):
        runner = FakeRunner([completed("ps", "")])
        result = DockerCliCollector(runner=runner).collect()
        self.assertTrue(result.success)
        self.assertEqual(result.containers, ())
        self.assertEqual(len(runner.commands), 1)

    def test_timeout_is_bounded_and_sanitized(self):
        runner = FakeRunner([subprocess.TimeoutExpired("docker", 8)])
        with self.assertRaises(CollectionError) as caught:
            DockerCliCollector(runner=runner).collect()
        self.assertEqual(caught.exception.reason, "timeout")
        self.assertNotIn("docker", str(caught.exception).lower())

    def test_engine_error_does_not_expose_stderr(self):
        runner = FakeRunner([completed("ps", returncode=1, stderr="token=private-value")])
        with self.assertRaises(CollectionError) as caught:
            DockerCliCollector(runner=runner).collect()
        self.assertEqual(caught.exception.reason, "docker_unavailable")
        self.assertNotIn("private-value", str(caught.exception))

    def test_container_limit_fails_closed_before_stats(self):
        rows = "".join(f"{index:012x}|service{index}\n" for index in range(1, 34))
        runner = FakeRunner([completed("ps", rows)])
        with self.assertRaises(CollectionError) as caught:
            DockerCliCollector(runner=runner).collect()
        self.assertEqual(caught.exception.reason, "too_many_containers")
        self.assertEqual(len(runner.commands), 1)


class ExporterStateTests(unittest.TestCase):
    def test_metrics_have_bounded_safe_labels_and_no_docker_metadata(self):
        state = ExporterState()
        observed_at = 1700000000.0
        sample = ContainerStats(
            service="prometheus",
            container_id="a" * 12,
            cpu_percent=3.5,
            memory_usage_bytes=1024.0,
            memory_limit_bytes=4096.0,
            network_receive_bytes_total=2048.0,
            network_transmit_bytes_total=4096.0,
            observed_at_seconds=observed_at,
        )
        state.publish(CollectionSnapshot(True, "ok", 0.2, observed_at, (sample,)))
        body = state.render_metrics().decode("utf-8")
        self.assertIn('service="prometheus",container_id="' + "a" * 12 + '"', body)
        self.assertIn("itmc_container_network_receive_bytes_total", body)
        for forbidden in ("container_name", "image=", "/var/lib", "secret", "PRIVATE", r"\.env"):
            self.assertNotIn(forbidden, body)
        self.assertEqual(body.count("service=\""), 7)
        self.assertIn("# TYPE itmc_container_network_receive_bytes_total counter", body)

    def test_failure_keeps_last_success_time_and_clears_observed_series(self):
        state = ExporterState()
        sample = ContainerStats("grafana", "b" * 12, 0, 1, 2, 3, 4, 100)
        state.publish(CollectionSnapshot(True, "ok", 0.1, 100, (sample,)))
        state.fail("timeout", 8.0)
        snapshot = state.snapshot()
        self.assertFalse(snapshot.success)
        self.assertEqual(snapshot.last_success_seconds, 100)
        self.assertEqual(snapshot.containers, ())
        self.assertIn('reason="timeout"} 1', state.render_metrics().decode("utf-8"))


class HttpTests(unittest.TestCase):
    def test_health_metrics_and_write_method_rejection(self):
        state = ExporterState()
        server = ThreadingHTTPServer(("127.0.0.1", 0), MetricsHandler)
        server.exporter_state = state
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("GET", "/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"ok\n")

            connection.request("GET", "/metrics")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b"itmc_container_exporter_collection_success", response.read())

            connection.request("POST", "/metrics", body=b"ignored")
            response = connection.getresponse()
            self.assertEqual(response.status, 405)
            self.assertEqual(response.getheader("Allow"), "GET")
            self.assertEqual(response.read(), b"method not allowed\n")
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
