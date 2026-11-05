from __future__ import annotations

import io
import logging
import signal
import socket
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import heartbeat_agent as agent


PING_URL = "https://hc-ping.com/00000000-0000-0000-0000-000000000001"


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"") -> None:
        self.status = status
        self.body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self.body if limit < 0 else self.body[:limit]


class ScriptedOpener:
    def __init__(self, outcomes: list[FakeResponse | BaseException]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def __call__(
        self, request: urllib.request.Request, timeout: float
    ) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def healthy_responses(count: int = 5) -> list[FakeResponse]:
    return [FakeResponse(200, b"OK") for _ in range(count)]


class HeartbeatAgentTests(unittest.TestCase):
    def test_http_transports_override_environment_proxy_state(self) -> None:
        built_opener = Mock()
        built_opener.open.return_value = FakeResponse()
        request = urllib.request.Request(PING_URL)

        with patch.dict(
            "os.environ",
            {
                "HTTP_PROXY": "http://unexpected-proxy.invalid:8080",
                "HTTPS_PROXY": "http://unexpected-proxy.invalid:8080",
                "NO_PROXY": "*",
                "no_proxy": "*",
            },
        ):
            with patch.object(
                agent.urllib.request,
                "build_opener",
                return_value=built_opener,
            ) as build:
                agent.open_without_redirects(request, timeout=1)
                internal_handlers = build.call_args.args
                internal_proxy = next(
                    item
                    for item in internal_handlers
                    if isinstance(item, urllib.request.ProxyHandler)
                )
                self.assertEqual(internal_proxy.proxies, {})

                agent.open_via_required_proxy(request, timeout=1)
                external_handlers = build.call_args.args
                required_proxy = next(
                    item
                    for item in external_handlers
                    if isinstance(item, agent.MandatoryProxyHandler)
                )
                self.assertEqual(
                    required_proxy.proxies,
                    {
                        "http": agent.EXTERNAL_PROXY_URL,
                        "https": agent.EXTERNAL_PROXY_URL,
                    },
                )
                self.assertFalse(required_proxy.proxy_bypass("hc-ping.com"))

    def test_proxy_failure_does_not_fall_back_to_direct_transport(self) -> None:
        internal = ScriptedOpener(healthy_responses(4))
        external = ScriptedOpener([OSError("synthetic proxy unavailable")])

        result = agent.run_cycle(
            agent.REQUIRED_CHECKS,
            agent.HttpClient(internal, external),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )

        self.assertEqual(result, agent.CycleResult(False, ("Healthchecks.io:network_error",)))
        self.assertEqual(len(internal.requests), 4)
        self.assertEqual(len(external.requests), 1)

    def test_all_required_components_and_external_ping_must_succeed(self) -> None:
        opener = ScriptedOpener(healthy_responses())
        client = agent.HttpClient(opener, opener)

        result = agent.run_cycle(
            agent.REQUIRED_CHECKS, client, PING_URL, component_timeout=3, ping_timeout=5
        )

        self.assertTrue(result.ok)
        self.assertEqual(len(opener.requests), 5)
        self.assertEqual([request.get_method() for request in opener.requests], ["GET"] * 5)
        self.assertEqual(opener.requests[-1].full_url, PING_URL)
        self.assertEqual(opener.timeouts, [3, 3, 3, 3, 5])

    def test_prometheus_failure_suppresses_external_success_ping(self) -> None:
        opener = ScriptedOpener([FakeResponse(503), *healthy_responses(3)])

        result = agent.run_cycle(
            agent.REQUIRED_CHECKS,
            agent.HttpClient(opener, opener),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failures, ("Prometheus:http_503",))
        self.assertEqual(len(opener.requests), 4)

    def test_alertmanager_failure_suppresses_external_success_ping(self) -> None:
        opener = ScriptedOpener([FakeResponse(200), FakeResponse(503), *healthy_responses(2)])

        result = agent.run_cycle(
            agent.REQUIRED_CHECKS,
            agent.HttpClient(opener, opener),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failures, ("Alertmanager:http_503",))
        self.assertEqual(len(opener.requests), 4)

    def test_grafana_and_incident_logger_failures_suppress_external_ping(self) -> None:
        for failed_index, failed_name in ((2, "Grafana"), (3, "Incident Logger")):
            with self.subTest(component=failed_name):
                responses = healthy_responses(4)
                responses[failed_index] = FakeResponse(503)
                opener = ScriptedOpener(responses)
                result = agent.run_cycle(
                    agent.REQUIRED_CHECKS,
                    agent.HttpClient(opener, opener),
                    PING_URL,
                    component_timeout=3,
                    ping_timeout=5,
                )

                self.assertFalse(result.ok)
                self.assertEqual(result.failures, (f"{failed_name}:http_503",))
                self.assertEqual(len(opener.requests), 4)

    def test_component_timeout_is_bounded(self) -> None:
        opener = ScriptedOpener([socket.timeout("slow local endpoint")])
        result = agent.HttpClient(opener, opener).check_component(agent.REQUIRED_CHECKS[0], 2.5)

        self.assertEqual(result, agent.RequestResult(False, "timeout"))
        self.assertEqual(opener.timeouts, [2.5])

    def test_non_success_component_http_status_is_rejected(self) -> None:
        opener = ScriptedOpener([FakeResponse(503)])
        result = agent.HttpClient(opener, opener).check_component(agent.REQUIRED_CHECKS[1], 3)

        self.assertEqual(result, agent.RequestResult(False, "http_503"))

    def test_dns_failure_is_sanitized(self) -> None:
        opener = ScriptedOpener(
            [urllib.error.URLError(socket.gaierror("synthetic DNS failure"))]
        )
        result = agent.HttpClient(opener, opener).check_component(agent.REQUIRED_CHECKS[0], 3)

        self.assertEqual(result, agent.RequestResult(False, "dns_error"))

    def test_internal_failures_have_safe_specific_categories(self) -> None:
        cases = (
            (
                "dns_error",
                socket.gaierror("host 192.0.2.9 private.invalid"),
            ),
            (
                "dns_error",
                urllib.error.URLError(socket.gaierror("host 192.0.2.9 private.invalid")),
            ),
            (
                "connection_refused",
                ConnectionRefusedError("private endpoint refused"),
            ),
            (
                "connection_refused",
                urllib.error.URLError(ConnectionRefusedError("private endpoint refused")),
            ),
            (
                "connection_error",
                OSError("private endpoint reset"),
            ),
            (
                "connection_error",
                urllib.error.URLError(OSError("private endpoint reset")),
            ),
            ("timeout", socket.timeout("private timeout detail")),
            (
                "timeout",
                urllib.error.URLError(socket.timeout("private timeout detail")),
            ),
            (
                "http_503",
                urllib.error.HTTPError(
                    "http://private.invalid/private-path", 503, "private detail", {}, None
                ),
            ),
        )
        for expected_reason, failure in cases:
            with self.subTest(reason=expected_reason):
                opener = ScriptedOpener([failure])
                result = agent.HttpClient(opener, opener).check_component(
                    agent.REQUIRED_CHECKS[0], 3
                )

                self.assertEqual(result, agent.RequestResult(False, expected_reason))
                self.assertNotIn("192.0.2.9", result.reason)
                self.assertNotIn("private", result.reason)

    def test_internal_connection_failure_suppresses_success_ping(self) -> None:
        opener = ScriptedOpener(
            [
                FakeResponse(200),
                urllib.error.URLError(
                    ConnectionRefusedError("private endpoint refused")
                ),
                FakeResponse(200),
                FakeResponse(200),
            ]
        )

        result = agent.run_cycle(
            agent.REQUIRED_CHECKS,
            agent.HttpClient(opener, opener),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )

        self.assertEqual(
            result, agent.CycleResult(False, ("Alertmanager:connection_refused",))
        )
        self.assertEqual(len(opener.requests), 4)

    def test_connection_failure_to_healthchecks_is_sanitized(self) -> None:
        opener = ScriptedOpener([*healthy_responses(4), OSError("synthetic connection failure")])
        result = agent.run_cycle(
            agent.REQUIRED_CHECKS,
            agent.HttpClient(opener, opener),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failures, ("Healthchecks.io:network_error",))

    def test_http_200_without_success_body_is_not_accepted(self) -> None:
        opener = ScriptedOpener([*healthy_responses(4), FakeResponse(200, b"not found")])
        result = agent.run_cycle(
            agent.REQUIRED_CHECKS,
            agent.HttpClient(opener, opener),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )

        self.assertEqual(result, agent.CycleResult(False, ("Healthchecks.io:unexpected_response",)))

    def test_external_http_error_is_rejected(self) -> None:
        error = urllib.error.HTTPError(PING_URL, 429, "rate limited", {}, None)
        opener = ScriptedOpener([*healthy_responses(4), error])
        result = agent.run_cycle(
            agent.REQUIRED_CHECKS,
            agent.HttpClient(opener, opener),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )

        self.assertEqual(result, agent.CycleResult(False, ("Healthchecks.io:http_429",)))

    def test_external_timeout_is_bounded(self) -> None:
        opener = ScriptedOpener([*healthy_responses(4), socket.timeout("slow provider")])
        result = agent.run_cycle(
            agent.REQUIRED_CHECKS,
            agent.HttpClient(opener, opener),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )

        self.assertEqual(result, agent.CycleResult(False, ("Healthchecks.io:timeout",)))
        self.assertEqual(opener.timeouts[-1], 5)

    def test_redirect_response_is_not_accepted(self) -> None:
        opener = ScriptedOpener([*healthy_responses(4), FakeResponse(302, b"OK")])
        result = agent.run_cycle(
            agent.REQUIRED_CHECKS,
            agent.HttpClient(opener, opener),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )

        self.assertEqual(result, agent.CycleResult(False, ("Healthchecks.io:http_302",)))
        redirect = agent.NoRedirectHandler().redirect_request(
            urllib.request.Request(PING_URL),
            FakeResponse(),
            302,
            "Found",
            {},
            "https://example.invalid/redirect",
        )
        self.assertIsNone(redirect)

    def test_ping_url_validation_fails_closed(self) -> None:
        invalid_urls = (
            "http://hc-ping.com/00000000-0000-0000-0000-000000000001",
            "https://example.invalid/00000000-0000-0000-0000-000000000001",
            "https://user@hc-ping.com/00000000-0000-0000-0000-000000000001",
            "https://hc-ping.com:444/00000000-0000-0000-0000-000000000001",
            PING_URL + "?rid=private",
            "https://hc-ping.com/not-a-uuid",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ping-url"
            for invalid_url in invalid_urls:
                path.write_text(invalid_url, encoding="utf-8")
                with self.subTest(invalid_url=invalid_url):
                    with self.assertRaises(agent.ConfigurationError):
                        agent.load_ping_url(path)

    def test_missing_ping_url_does_not_include_path_in_error(self) -> None:
        missing_path = Path("C:/private/path/healthchecks-ping-url")
        with self.assertRaises(agent.ConfigurationError) as raised:
            agent.load_ping_url(missing_path)

        self.assertNotIn(str(missing_path), str(raised.exception))

    def test_valid_ping_url_is_trimmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ping-url"
            path.write_text(f"  {PING_URL}\n", encoding="utf-8")
            self.assertEqual(agent.load_ping_url(path), PING_URL)

    def test_recovery_sends_ping_on_the_next_cycle(self) -> None:
        opener = ScriptedOpener(
            [
                FakeResponse(503),
                *healthy_responses(3),
                *healthy_responses(5),
            ]
        )
        client = agent.HttpClient(opener, opener)

        first = agent.run_cycle(agent.REQUIRED_CHECKS, client, PING_URL, 3, 5)
        second = agent.run_cycle(agent.REQUIRED_CHECKS, client, PING_URL, 3, 5)

        self.assertFalse(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(len(opener.requests), 9)

    def test_error_logs_never_include_ping_url(self) -> None:
        opener = ScriptedOpener(
            [
                *healthy_responses(4),
                urllib.error.URLError(f"failed request to {PING_URL}"),
            ]
        )
        result = agent.run_cycle(
            agent.REQUIRED_CHECKS,
            agent.HttpClient(opener, opener),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("itmc.heartbeat")
        logger.addHandler(handler)
        try:
            agent.log_cycle_result(result)
        finally:
            logger.removeHandler(handler)

        self.assertNotIn(PING_URL, stream.getvalue())
        self.assertNotIn("00000000-0000-0000-0000-000000000001", stream.getvalue())

    def test_component_error_logs_do_not_include_raw_exception_details(self) -> None:
        opener = ScriptedOpener(
            [urllib.error.URLError("synthetic failure containing private-path")]
        )
        result = agent.run_cycle(
            agent.REQUIRED_CHECKS[:1],
            agent.HttpClient(opener, opener),
            PING_URL,
            component_timeout=3,
            ping_timeout=5,
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("itmc.heartbeat")
        logger.addHandler(handler)
        try:
            agent.log_cycle_result(result)
        finally:
            logger.removeHandler(handler)

        self.assertNotIn("private-path", stream.getvalue())
        self.assertNotIn(PING_URL, stream.getvalue())

    def test_interval_limits_prevent_fast_ping_loops(self) -> None:
        with patch.dict("os.environ", {"HEARTBEAT_INTERVAL_SECONDS": "14"}):
            with self.assertRaises(agent.ConfigurationError):
                agent.Settings.from_environment()
        with patch.dict("os.environ", {"HEARTBEAT_INTERVAL_SECONDS": "15"}):
            self.assertEqual(agent.Settings.from_environment().interval_seconds, 15)

    def test_loop_waits_between_cycles_and_stops_cleanly(self) -> None:
        class StopAfterWait:
            stopped = False
            waits: list[float] = []

            def is_set(self) -> bool:
                return self.stopped

            def wait(self, timeout: float | None = None) -> bool:
                self.waits.append(timeout or 0.0)
                self.stopped = True
                return True

        stop_event = StopAfterWait()
        tick = iter((10.0, 12.0))
        state = agent.AgentState(120)
        with patch.object(agent, "run_cycle", return_value=agent.CycleResult(True)) as run:
            agent.run_loop(
                agent.Settings(Path("unused")),
                (),
                agent.HttpClient(ScriptedOpener([])),
                PING_URL,
                state,
                stop_event,
                monotonic=lambda: next(tick),
            )

        run.assert_called_once()
        self.assertEqual(stop_event.waits, [58.0])
        self.assertTrue(state.is_healthy())

    def test_sigterm_handler_requests_shutdown(self) -> None:
        stop_event = threading.Event()
        handlers: dict[int, object] = {}

        def register(signum: int, handler: object) -> None:
            handlers[signum] = handler

        with patch.object(agent.signal, "signal", side_effect=register):
            agent.install_signal_handlers(stop_event)

        handler = handlers[signal.SIGTERM]
        handler(signal.SIGTERM, None)  # type: ignore[operator]
        self.assertTrue(stop_event.is_set())

    def test_health_endpoint_state_requires_a_recent_successful_full_cycle(self) -> None:
        state = agent.AgentState(60)
        self.assertFalse(state.is_healthy())
        state.record_cycle(agent.CycleResult(True))
        self.assertTrue(state.is_healthy())
        state.record_cycle(agent.CycleResult(False, ("Alertmanager:http_503",)))
        self.assertFalse(state.is_healthy())

    def test_health_endpoint_returns_success_only_for_recent_full_cycle(self) -> None:
        def request_status(state: agent.AgentState) -> int:
            server = agent.create_health_server(state, 0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/health"
                )
                try:
                    with urllib.request.urlopen(request, timeout=2) as response:
                        return response.status
                except urllib.error.HTTPError as error:
                    return error.code
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        state = agent.AgentState(60)
        self.assertEqual(request_status(state), 503)
        state.record_cycle(agent.CycleResult(True))
        self.assertEqual(request_status(state), 200)
        state.record_cycle(agent.CycleResult(False, ("Prometheus:connection_refused",)))
        self.assertEqual(request_status(state), 503)

        stale_state = agent.AgentState(-1)
        stale_state.record_cycle(agent.CycleResult(True))
        self.assertEqual(request_status(stale_state), 503)

    def test_health_server_binds_only_to_container_loopback(self) -> None:
        server = agent.create_health_server(agent.AgentState(60), 0)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
