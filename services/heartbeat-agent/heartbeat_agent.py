"""Send a Healthchecks.io success ping only after required ITMC services pass."""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit


LOGGER = logging.getLogger("itmc.heartbeat")
USER_AGENT = "itmc-external-heartbeat/1.0"
DEFAULT_PING_URL_FILE = Path("/run/secrets/healthchecks_ping_url")
PING_URL_HOST = "hc-ping.com"
EXTERNAL_PROXY_URL = "http://172.31.255.250:3128"
HEALTH_BIND_HOST = "127.0.0.1"
PING_UUID_PATH = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


@dataclass(frozen=True)
class HealthCheck:
    name: str
    url: str


REQUIRED_CHECKS = (
    HealthCheck("Prometheus", "http://prometheus:9090/-/ready"),
    HealthCheck("Alertmanager", "http://alertmanager:9093/-/ready"),
    HealthCheck("Grafana", "http://grafana:3000/api/health"),
    HealthCheck("Incident Logger", "http://incident-logger:8080/health"),
)


class ConfigurationError(ValueError):
    """Raised for invalid local agent configuration without echoing its value."""


@dataclass(frozen=True)
class Settings:
    ping_url_file: Path
    interval_seconds: int = 60
    component_timeout_seconds: int = 3
    ping_timeout_seconds: int = 5
    health_port: int = 9740

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            ping_url_file=Path(
                os.environ.get(
                    "HEARTBEAT_PING_URL_FILE", str(DEFAULT_PING_URL_FILE)
                )
            ),
            interval_seconds=_read_int(
                "HEARTBEAT_INTERVAL_SECONDS", default=60, minimum=15, maximum=3600
            ),
            component_timeout_seconds=_read_int(
                "HEARTBEAT_COMPONENT_TIMEOUT_SECONDS",
                default=3,
                minimum=1,
                maximum=10,
            ),
            ping_timeout_seconds=_read_int(
                "HEARTBEAT_PING_TIMEOUT_SECONDS", default=5, minimum=1, maximum=15
            ),
            health_port=_read_int(
                "HEARTBEAT_HEALTH_PORT", default=9740, minimum=1024, maximum=65535
            ),
        )

    @property
    def health_freshness_seconds(self) -> int:
        probe_budget = len(REQUIRED_CHECKS) * self.component_timeout_seconds
        return self.interval_seconds + probe_budget + self.ping_timeout_seconds + 5


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} is outside its supported range")
    return value


def load_ping_url(path: Path) -> str:
    """Read and validate a UUID Ping URL; never include the value in errors."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise ConfigurationError("Healthchecks Ping URL file is unavailable") from error

    try:
        parsed = urlsplit(value)
        valid_port = parsed.port in (None, 443)
    except ValueError as error:
        raise ConfigurationError("Healthchecks Ping URL is invalid") from error

    if (
        parsed.scheme != "https"
        or parsed.hostname != PING_URL_HOST
        or not valid_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not PING_UUID_PATH.fullmatch(parsed.path)
    ):
        raise ConfigurationError("Healthchecks Ping URL is invalid")
    return value


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects so the credential-bearing URL cannot be forwarded."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OpenResponse = Callable[[urllib.request.Request, float], object]


def open_without_redirects(
    request: urllib.request.Request, timeout: float
) -> object:
    # An explicit empty proxy map prevents environment-proxy inheritance for
    # internal Compose service checks.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirectHandler()
    )
    return opener.open(request, timeout=timeout)


class MandatoryProxyHandler(urllib.request.ProxyHandler):
    """Use the configured proxy even if a no_proxy environment value exists."""

    def proxy_bypass(self, host: str) -> bool:
        return False


def open_via_required_proxy(
    request: urllib.request.Request, timeout: float
) -> object:
    proxy = MandatoryProxyHandler(
        {"http": EXTERNAL_PROXY_URL, "https": EXTERNAL_PROXY_URL}
    )
    opener = urllib.request.build_opener(proxy, NoRedirectHandler())
    return opener.open(request, timeout=timeout)


@dataclass(frozen=True)
class RequestResult:
    ok: bool
    reason: str


def _classify_internal_error(error: BaseException) -> str:
    """Return a stable, non-sensitive category for an internal HTTP failure."""
    if isinstance(error, urllib.error.HTTPError):
        return f"http_{error.code}"

    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(reason, socket.gaierror):
        return "dns_error"
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused"
    return "connection_error"


class HttpClient:
    def __init__(
        self,
        internal_open_response: OpenResponse = open_without_redirects,
        external_open_response: OpenResponse = open_via_required_proxy,
    ) -> None:
        self._internal_open_response = internal_open_response
        self._external_open_response = external_open_response

    @staticmethod
    def _request(url: str, method: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            method=method,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain;q=0.9",
                "Cache-Control": "no-cache",
            },
        )

    def check_component(
        self, check: HealthCheck, timeout: float
    ) -> RequestResult:
        request = self._request(check.url, "GET")
        try:
            with self._internal_open_response(request, timeout) as response:
                if response.status != 200:
                    return RequestResult(False, f"http_{response.status}")
            return RequestResult(True, "ok")
        except urllib.error.HTTPError as error:
            error.close()
            return RequestResult(False, f"http_{error.code}")
        except (urllib.error.URLError, OSError) as error:
            return RequestResult(False, _classify_internal_error(error))

    def send_success_ping(self, ping_url: str, timeout: float) -> RequestResult:
        request = self._request(ping_url, "GET")
        try:
            with self._external_open_response(request, timeout) as response:
                if response.status != 200:
                    return RequestResult(False, f"http_{response.status}")
                # Healthchecks.io can use HTTP 200 for not-found/rate-limit replies.
                # Accept only its documented success body, and read at most 3 bytes.
                if response.read(3) != b"OK":
                    return RequestResult(False, "unexpected_response")
            return RequestResult(True, "ok")
        except urllib.error.HTTPError as error:
            error.close()
            return RequestResult(False, f"http_{error.code}")
        except (TimeoutError, socket.timeout):
            return RequestResult(False, "timeout")
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                return RequestResult(False, "timeout")
            return RequestResult(False, "network_error")
        except OSError:
            return RequestResult(False, "network_error")


@dataclass(frozen=True)
class CycleResult:
    ok: bool
    failures: tuple[str, ...] = ()


def run_cycle(
    checks: tuple[HealthCheck, ...],
    client: HttpClient,
    ping_url: str,
    component_timeout: float,
    ping_timeout: float,
) -> CycleResult:
    failures: list[str] = []
    for check in checks:
        result = client.check_component(check, component_timeout)
        if not result.ok:
            failures.append(f"{check.name}:{result.reason}")

    if failures:
        return CycleResult(False, tuple(failures))

    ping_result = client.send_success_ping(ping_url, ping_timeout)
    if not ping_result.ok:
        return CycleResult(False, (f"Healthchecks.io:{ping_result.reason}",))
    return CycleResult(True)


class AgentState:
    def __init__(self, freshness_seconds: int) -> None:
        self._freshness_seconds = freshness_seconds
        self._lock = threading.Lock()
        self._last_cycle_ok = False
        self._last_success_at: float | None = None

    def record_cycle(self, result: CycleResult) -> None:
        now = time.monotonic()
        with self._lock:
            self._last_cycle_ok = result.ok
            if result.ok:
                self._last_success_at = now

    def is_healthy(self) -> bool:
        with self._lock:
            return (
                self._last_cycle_ok
                and self._last_success_at is not None
                and time.monotonic() - self._last_success_at
                <= self._freshness_seconds
            )


def make_health_handler(state: AgentState) -> type[BaseHTTPRequestHandler]:
    class HealthHandler(BaseHTTPRequestHandler):
        def _respond(self, include_body: bool) -> None:
            if urlsplit(self.path).path not in {"/health", "/-/healthy"}:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 200 if state.is_healthy() else 503
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "2" if include_body else "0")
            self.end_headers()
            if include_body:
                self.wfile.write(b"ok")

        def do_GET(self) -> None:  # noqa: N802
            self._respond(include_body=True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._respond(include_body=False)

        def do_POST(self) -> None:  # noqa: N802
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.close_connection = True

        def log_message(self, format_string: str, *args: object) -> None:
            # Avoid access logs; no request details are needed for this local check.
            return

    return HealthHandler


def _healthcheck_local(port: int) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health", method="GET"
    )
    try:
        with open_without_redirects(request, timeout=2) as response:
            return 0 if response.status == 200 else 1
    except (OSError, urllib.error.URLError, TimeoutError, socket.timeout):
        return 1


def create_health_server(state: AgentState, port: int) -> ThreadingHTTPServer:
    """Expose health only to the agent process, not attached network peers."""
    return ThreadingHTTPServer(
        (HEALTH_BIND_HOST, port), make_health_handler(state)
    )


class StopEvent(Protocol):
    """Protocol-shaped alias for threading.Event, useful for type readability."""

    def is_set(self) -> bool:
        raise NotImplementedError

    def wait(self, timeout: float | None = None) -> bool:
        raise NotImplementedError


def run_loop(
    settings: Settings,
    checks: tuple[HealthCheck, ...],
    client: HttpClient,
    ping_url: str,
    state: AgentState,
    stop_event: StopEvent,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    while not stop_event.is_set():
        started_at = monotonic()
        result = run_cycle(
            checks,
            client,
            ping_url,
            settings.component_timeout_seconds,
            settings.ping_timeout_seconds,
        )
        state.record_cycle(result)
        log_cycle_result(result)

        if stop_event.is_set():
            break
        elapsed = max(0.0, monotonic() - started_at)
        stop_event.wait(max(0.0, settings.interval_seconds - elapsed))


def log_cycle_result(result: CycleResult) -> None:
    if result.ok:
        LOGGER.info("cycle healthy; success ping accepted")
    else:
        LOGGER.warning(
            "cycle unhealthy; no success ping sent (%s)", ", ".join(result.failures)
        )


def install_signal_handlers(stop_event: threading.Event) -> None:
    def request_shutdown(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


def run_agent(settings: Settings) -> None:
    ping_url = load_ping_url(settings.ping_url_file)
    state = AgentState(settings.health_freshness_seconds)
    stop_event = threading.Event()
    install_signal_handlers(stop_event)

    server = create_health_server(state, settings.health_port)
    server.daemon_threads = True
    server_thread = threading.Thread(
        target=server.serve_forever, name="heartbeat-health", daemon=True
    )
    server_thread.start()

    try:
        LOGGER.info("agent started; checks=%d interval_seconds=%d", len(REQUIRED_CHECKS), settings.interval_seconds)
        run_loop(
            settings,
            REQUIRED_CHECKS,
            HttpClient(),
            ping_url,
            state,
            stop_event,
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
        LOGGER.info("agent stopped cleanly")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args(argv)

    if args.healthcheck:
        try:
            port = _read_int(
                "HEARTBEAT_HEALTH_PORT", default=9740, minimum=1024, maximum=65535
            )
        except ConfigurationError:
            return 1
        return _healthcheck_local(port)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        run_agent(Settings.from_environment())
    except ConfigurationError as error:
        LOGGER.error("startup configuration invalid: %s", error)
        return 2
    except OSError:
        # Do not forward exception text: it could include local paths or URLs.
        LOGGER.error("agent could not start its local health endpoint")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
