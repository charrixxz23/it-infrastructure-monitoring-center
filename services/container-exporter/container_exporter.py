"""Read-only Prometheus exporter for one Docker Compose project on Windows."""

from __future__ import annotations

import argparse
import http.server
import logging
import math
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence
from urllib.parse import urlsplit


DEFAULT_PROJECT = "it-infrastructure-monitoring-center"
DEFAULT_CONTEXT = "desktop-linux"
DEFAULT_ADDRESS = "127.0.0.1"
DEFAULT_PORT = 9464
DEFAULT_POLL_SECONDS = 15.0
DEFAULT_COMMAND_TIMEOUT_SECONDS = 8.0
MAX_CONTAINERS = 32
MAX_COMMAND_OUTPUT_CHARS = 64 * 1024

_ID_RE = re.compile(r"^[0-9a-fA-F]{12,64}$")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CPU_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*%\s*$")
_SIZE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([A-Za-z]+)\s*$")
_PAIR_RE = re.compile(r"^\s*(.*?)\s*/\s*(.*?)\s*$")

_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
    "eb": 1000**6,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
    "eib": 1024**6,
}
_ERROR_REASONS = (
    "timeout",
    "docker_unavailable",
    "parse_error",
    "too_many_containers",
    "empty_stats",
    "partial",
    "internal_error",
)


class CollectionError(Exception):
    """A safe, bounded error category; never contains raw Docker output."""

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in _ERROR_REASONS else "internal_error"
        super().__init__(self.reason)


@dataclass(frozen=True)
class ContainerStats:
    service: str
    container_id: str
    cpu_percent: float
    memory_usage_bytes: float
    memory_limit_bytes: float
    network_receive_bytes_total: float
    network_transmit_bytes_total: float
    observed_at_seconds: float


@dataclass(frozen=True)
class CollectionSnapshot:
    success: bool
    reason: str
    duration_seconds: float
    collected_at_seconds: float
    containers: tuple[ContainerStats, ...]


@dataclass(frozen=True)
class ExporterSnapshot:
    success: bool
    reason: str
    duration_seconds: float
    last_success_seconds: float
    containers: tuple[ContainerStats, ...]


def _parse_size_bytes(value: str) -> float:
    match = _SIZE_RE.fullmatch(value)
    if not match:
        raise ValueError("invalid size")
    number = float(match.group(1).replace(",", "."))
    multiplier = _UNITS.get(match.group(2).lower())
    if multiplier is None or not math.isfinite(number) or number < 0:
        raise ValueError("invalid size")
    return number * multiplier


def _parse_pair(value: str) -> tuple[str, str]:
    match = _PAIR_RE.fullmatch(value)
    if not match:
        raise ValueError("invalid value pair")
    return match.group(1), match.group(2)


def parse_project_containers(output: str) -> dict[str, str]:
    """Parse only the short ID and Compose service label returned by `docker ps`."""
    containers: dict[str, str] = {}
    for line in output.splitlines():
        if not line:
            continue
        parts = line.split("|", 1)
        if len(parts) != 2:
            raise ValueError("invalid container row")
        container_id, service = (item.strip() for item in parts)
        if not _ID_RE.fullmatch(container_id) or not _SERVICE_RE.fullmatch(service):
            raise ValueError("invalid container identity")
        short_id = container_id[:12].lower()
        if short_id in containers and containers[short_id] != service:
            raise ValueError("ambiguous container identity")
        containers[short_id] = service
    if len(containers) > MAX_CONTAINERS:
        raise OverflowError("container limit exceeded")
    return containers


def parse_stats_output(
    output: str,
    selected_containers: dict[str, str],
    observed_at_seconds: float,
) -> tuple[ContainerStats, ...]:
    """Parse the restricted CLI template and discard every non-project row."""
    samples: dict[str, ContainerStats] = {}
    for line in output.splitlines():
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) != 4:
            raise ValueError("invalid stats row")
        container_id, cpu_value, memory_value, network_value = (item.strip() for item in parts)
        if not _ID_RE.fullmatch(container_id):
            raise ValueError("invalid stats identity")
        short_id = container_id[:12].lower()
        service = selected_containers.get(short_id)
        if service is None:
            continue

        cpu_match = _CPU_RE.fullmatch(cpu_value)
        if not cpu_match:
            raise ValueError("invalid CPU value")
        cpu_percent = float(cpu_match.group(1).replace(",", "."))
        if not math.isfinite(cpu_percent) or cpu_percent < 0:
            raise ValueError("invalid CPU value")

        memory_used, memory_limit = _parse_pair(memory_value)
        receive, transmit = _parse_pair(network_value)
        samples[short_id] = ContainerStats(
            service=service,
            container_id=short_id,
            cpu_percent=cpu_percent,
            memory_usage_bytes=_parse_size_bytes(memory_used),
            memory_limit_bytes=_parse_size_bytes(memory_limit),
            network_receive_bytes_total=_parse_size_bytes(receive),
            network_transmit_bytes_total=_parse_size_bytes(transmit),
            observed_at_seconds=observed_at_seconds,
        )
    return tuple(samples[key] for key in sorted(samples))


class DockerCliCollector:
    """Uses only project-filtered `ps` and one-shot `stats` CLI queries."""

    def __init__(
        self,
        project: str = DEFAULT_PROJECT,
        context: str = DEFAULT_CONTEXT,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        executable: str | None = None,
    ) -> None:
        if not _SERVICE_RE.fullmatch(project):
            raise ValueError("invalid Compose project label")
        if not _SERVICE_RE.fullmatch(context):
            raise ValueError("invalid Docker context")
        if timeout_seconds <= 0:
            raise ValueError("timeout must be positive")
        self.project = project
        self.context = context
        self.timeout_seconds = timeout_seconds
        self.runner = runner
        self.executable = executable or shutil.which("docker") or "docker"

    def _run(self, args: Sequence[str]) -> str:
        command = [self.executable, "--context", self.context, *args]
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise CollectionError("timeout") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise CollectionError("docker_unavailable") from exc
        if result.returncode != 0:
            raise CollectionError("docker_unavailable")
        output = result.stdout or ""
        if len(output) > MAX_COMMAND_OUTPUT_CHARS:
            raise CollectionError("parse_error")
        return output

    def collect(self) -> CollectionSnapshot:
        started = time.perf_counter()
        try:
            container_map = parse_project_containers(
                self._run(
                    [
                        "ps",
                        "--filter",
                        f"label=com.docker.compose.project={self.project}",
                        "--format",
                        '{{.ID}}|{{.Label "com.docker.compose.service"}}',
                    ]
                )
            )
        except OverflowError as exc:
            raise CollectionError("too_many_containers") from exc
        except ValueError as exc:
            raise CollectionError("parse_error") from exc

        collected_at = time.time()
        if not container_map:
            return CollectionSnapshot(
                True, "ok", time.perf_counter() - started, collected_at, ()
            )

        try:
            output = self._run(
                [
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.ID}}|{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}",
                    *sorted(container_map),
                ]
            )
            samples = parse_stats_output(output, container_map, collected_at)
        except ValueError as exc:
            raise CollectionError("parse_error") from exc

        if not samples:
            raise CollectionError("empty_stats")
        complete = len(samples) == len(container_map)
        return CollectionSnapshot(
            complete,
            "ok" if complete else "partial",
            time.perf_counter() - started,
            collected_at,
            samples,
        )


class ExporterState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = ExporterSnapshot(
            success=False,
            reason="internal_error",
            duration_seconds=0.0,
            last_success_seconds=0.0,
            containers=(),
        )

    def publish(self, snapshot: CollectionSnapshot) -> None:
        with self._lock:
            previous = self._snapshot
            last_success = (
                snapshot.collected_at_seconds
                if snapshot.success
                else previous.last_success_seconds
            )
            self._snapshot = ExporterSnapshot(
                success=snapshot.success,
                reason=snapshot.reason,
                duration_seconds=snapshot.duration_seconds,
                last_success_seconds=last_success,
                containers=snapshot.containers,
            )

    def fail(self, reason: str, duration_seconds: float) -> None:
        safe_reason = reason if reason in _ERROR_REASONS else "internal_error"
        with self._lock:
            previous = self._snapshot
            self._snapshot = ExporterSnapshot(
                success=False,
                reason=safe_reason,
                duration_seconds=max(0.0, duration_seconds),
                last_success_seconds=previous.last_success_seconds,
                containers=(),
            )

    def snapshot(self) -> ExporterSnapshot:
        with self._lock:
            return self._snapshot

    def render_metrics(self) -> bytes:
        snapshot = self.snapshot()
        lines = [
            "# HELP itmc_container_exporter_collection_success Whether the latest Docker stats collection was complete.",
            "# TYPE itmc_container_exporter_collection_success gauge",
            f"itmc_container_exporter_collection_success {1 if snapshot.success else 0}",
            "# HELP itmc_container_exporter_collection_duration_seconds Duration of the latest Docker CLI collection.",
            "# TYPE itmc_container_exporter_collection_duration_seconds gauge",
            f"itmc_container_exporter_collection_duration_seconds {_number(snapshot.duration_seconds)}",
            "# HELP itmc_container_exporter_last_success_timestamp_seconds Unix timestamp of the last complete Docker collection.",
            "# TYPE itmc_container_exporter_last_success_timestamp_seconds gauge",
            f"itmc_container_exporter_last_success_timestamp_seconds {_number(snapshot.last_success_seconds)}",
            "# HELP itmc_container_exporter_observed_containers Running project containers present in the latest usable stats snapshot.",
            "# TYPE itmc_container_exporter_observed_containers gauge",
            f"itmc_container_exporter_observed_containers {len(snapshot.containers)}",
            "# HELP itmc_container_exporter_container_limit Maximum project containers accepted to bound metric cardinality.",
            "# TYPE itmc_container_exporter_container_limit gauge",
            f"itmc_container_exporter_container_limit {MAX_CONTAINERS}",
            "# HELP itmc_container_exporter_collection_error Current collection error category (one active category at most).",
            "# TYPE itmc_container_exporter_collection_error gauge",
        ]
        for reason in _ERROR_REASONS:
            lines.append(
                f'itmc_container_exporter_collection_error{{reason="{reason}"}} '
                f"{1 if snapshot.reason == reason else 0}"
            )

        metric_definitions = (
            (
                "itmc_container_info",
                "Identifies a container returned by the filtered Docker stats query.",
                lambda item: "1",
            ),
            (
                "itmc_container_cpu_usage_percent",
                "CPU usage percentage reported by Docker CLI for the last sample.",
                lambda item: _number(item.cpu_percent),
            ),
            (
                "itmc_container_memory_usage_bytes",
                "Memory usage bytes reported by Docker CLI for the last sample.",
                lambda item: _number(item.memory_usage_bytes),
            ),
            (
                "itmc_container_memory_limit_bytes",
                "Memory limit bytes reported by Docker CLI for the last sample.",
                lambda item: _number(item.memory_limit_bytes),
            ),
            (
                "itmc_container_network_receive_bytes_total",
                "Cumulative network bytes received as reported by Docker CLI.",
                lambda item: _number(item.network_receive_bytes_total),
            ),
            (
                "itmc_container_network_transmit_bytes_total",
                "Cumulative network bytes transmitted as reported by Docker CLI.",
                lambda item: _number(item.network_transmit_bytes_total),
            ),
            (
                "itmc_container_last_observed_timestamp_seconds",
                "Unix timestamp when this container was last observed by Docker CLI.",
                lambda item: _number(item.observed_at_seconds),
            ),
        )
        for metric_name, help_text, value_fn in metric_definitions:
            metric_type = "counter" if metric_name.endswith("_bytes_total") else "gauge"
            lines.extend((f"# HELP {metric_name} {help_text}", f"# TYPE {metric_name} {metric_type}"))
            for item in snapshot.containers:
                labels = f'service="{item.service}",container_id="{item.container_id}"'
                lines.append(f"{metric_name}{{{labels}}} {value_fn(item)}")
        return ("\n".join(lines) + "\n").encode("utf-8")


def _number(value: float) -> str:
    if not math.isfinite(value):
        return "0"
    return format(value, ".15g")


class MetricsHandler(http.server.BaseHTTPRequestHandler):
    server_version = "ITMCContainerExporter"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlsplit(self.path).path
        if path == "/health":
            self._respond(200, b"ok\n", "text/plain; charset=utf-8")
        elif path == "/metrics":
            state: ExporterState = self.server.exporter_state  # type: ignore[attr-defined]
            self._respond(
                200,
                state.render_metrics(),
                "text/plain; version=0.0.4; charset=utf-8",
            )
        else:
            self._respond(404, b"not found\n", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._method_not_allowed()

    def _method_not_allowed(self) -> None:
        self.close_connection = True
        self._respond(405, b"method not allowed\n", "text/plain; charset=utf-8", allow="GET")

    def _respond(
        self, status: int, body: bytes, content_type: str, allow: str | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if allow:
            self.send_header("Allow", allow)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        # Request paths and Docker output are intentionally not logged.
        return


class LoopbackHTTPServer(http.server.HTTPServer):
    allow_reuse_address = True
    request_queue_size = 8
    timeout = 0.5

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(3.0)
        return request, client_address


def _poll_loop(
    collector: DockerCliCollector,
    state: ExporterState,
    stop_event: threading.Event,
    poll_seconds: float,
) -> None:
    while not stop_event.is_set():
        started = time.perf_counter()
        try:
            state.publish(collector.collect())
        except CollectionError as exc:
            state.fail(exc.reason, time.perf_counter() - started)
        except Exception:
            # Keep the server alive without logging arbitrary exception details.
            state.fail("internal_error", time.perf_counter() - started)
        elapsed = time.perf_counter() - started
        stop_event.wait(max(0.0, poll_seconds - elapsed))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--context", default=DEFAULT_CONTEXT)
    parser.add_argument("--address", choices=(DEFAULT_ADDRESS,), default=DEFAULT_ADDRESS)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_COMMAND_TIMEOUT_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be between 1 and 65535")
    if args.poll_seconds < 5:
        raise SystemExit("poll interval must be at least 5 seconds")
    if args.timeout_seconds <= 0 or args.timeout_seconds >= args.poll_seconds:
        raise SystemExit("Docker command timeout must be positive and shorter than the poll interval")

    state = ExporterState()
    stop_event = threading.Event()
    collector = DockerCliCollector(
        project=args.project,
        context=args.context,
        timeout_seconds=args.timeout_seconds,
    )
    server = LoopbackHTTPServer((args.address, args.port), MetricsHandler)
    server.exporter_state = state  # type: ignore[attr-defined]
    poller = threading.Thread(
        target=_poll_loop,
        args=(collector, state, stop_event, args.poll_seconds),
        name="docker-stats-poller",
        daemon=True,
    )
    poller.start()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, request_stop)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.info("Exporter listening on loopback port %d", args.port)
    try:
        while not stop_event.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        stop_event.set()
        server.server_close()
        poller.join(timeout=args.timeout_seconds + 1.0)
        logging.info("Exporter stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
