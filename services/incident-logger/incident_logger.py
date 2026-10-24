#!/usr/bin/env python3
"""Small internal Alertmanager webhook receiver with bounded JSONL history."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


LOG_PATH = Path(os.environ.get("INCIDENT_LOG_PATH", "/data/incidents.jsonl"))
MAX_BYTES = max(64 * 1024, int(os.environ.get("INCIDENT_LOG_MAX_BYTES", "5242880")))
BACKUP_COUNT = max(1, min(10, int(os.environ.get("INCIDENT_LOG_BACKUP_COUNT", "3"))))
MAX_REQUEST_BYTES = 1_048_576
USEFUL_LABELS = {
    "alertname",
    "severity",
    "job",
    "instance",
    "mountpoint",
    "device",
    "fstype",
    "source",
}
lock = threading.RLock()
seen_keys: set[str] = set()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def key_for(event: dict) -> str:
    raw = "\0".join((event["fingerprint"], event["status"], event["startsAt"]))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def log_files() -> list[Path]:
    return [LOG_PATH] + [Path(f"{LOG_PATH}.{index}") for index in range(1, BACKUP_COUNT + 1)]


def read_events() -> list[dict]:
    events: list[dict] = []
    for path in reversed(log_files()):
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        events.append(event)
        except OSError:
            continue
    return events


def load_seen_keys() -> None:
    for event in read_events():
        fingerprint = event.get("fingerprint")
        status = event.get("status")
        starts_at = event.get("startsAt")
        if fingerprint and status and starts_at:
            seen_keys.add(key_for({"fingerprint": fingerprint, "status": status, "startsAt": starts_at}))


def rotate_if_needed(next_line_size: int) -> None:
    if not LOG_PATH.exists() or LOG_PATH.stat().st_size + next_line_size <= MAX_BYTES:
        return
    oldest = Path(f"{LOG_PATH}.{BACKUP_COUNT}")
    oldest.unlink(missing_ok=True)
    for index in range(BACKUP_COUNT - 1, 0, -1):
        source = Path(f"{LOG_PATH}.{index}")
        if source.exists():
            source.replace(Path(f"{LOG_PATH}.{index + 1}"))
    LOG_PATH.replace(Path(f"{LOG_PATH}.1"))
    seen_keys.clear()
    load_seen_keys()


def normalized_event(alert: dict, received_at: str) -> dict:
    labels = alert.get("labels")
    if not isinstance(labels, dict):
        raise ValueError("alert.labels must be an object")
    status = str(alert.get("status", "")).upper()
    if status not in {"FIRING", "RESOLVED"}:
        raise ValueError("alert.status must be firing or resolved")
    fingerprint = str(alert.get("fingerprint", ""))
    if not fingerprint:
        identity = json.dumps(labels, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    starts_at = str(alert.get("startsAt") or received_at)
    annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
    return {
        "receivedAt": received_at,
        "fingerprint": fingerprint,
        "alertname": str(labels.get("alertname", "unknown"))[:128],
        "severity": str(labels.get("severity", "unknown"))[:32],
        "status": status,
        "labels": {
            str(key): str(value)[:256]
            for key, value in labels.items()
            if key in USEFUL_LABELS
        },
        "annotations": {
            key: str(annotations[key])[:1024]
            for key in ("summary", "description")
            if key in annotations
        },
        "startsAt": starts_at,
        "endsAt": str(alert.get("endsAt", "")),
    }


def persist_events(events: list[dict]) -> int:
    written = 0
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with lock:
        for event in events:
            dedupe_key = key_for(event)
            if dedupe_key in seen_keys:
                continue
            encoded = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            rotate_if_needed(len(encoded))
            with LOG_PATH.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            seen_keys.add(dedupe_key)
            written += 1
    return written


class Handler(BaseHTTPRequestHandler):
    server_version = "IncidentLogger/1.0"

    def send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path in {"/health", "/-/healthy"}:
            self.send_json(200, {"status": "ok"})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/webhook":
            self.send_json(404, {"error": "not found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "invalid Content-Length"})
            return
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self.send_json(413 if content_length > MAX_REQUEST_BYTES else 400, {"error": "invalid request size"})
            return
        try:
            body = json.loads(self.rfile.read(content_length))
            alerts = body.get("alerts") if isinstance(body, dict) else None
            if not isinstance(alerts, list) or not alerts:
                raise ValueError("payload must contain a non-empty alerts array")
            received_at = utc_now()
            events = [normalized_event(alert, received_at) for alert in alerts if isinstance(alert, dict)]
            if len(events) != len(alerts):
                raise ValueError("each alert must be an object")
            written = persist_events(events)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            self.send_json(400, {"error": str(error)[:200]})
            return
        except OSError:
            self.send_json(500, {"error": "incident log write failed"})
            return
        self.send_json(200, {"status": "accepted", "received": len(events), "written": written, "duplicates": len(events) - written})

    def log_message(self, fmt: str, *args: object) -> None:
        # Keep access logs compact and avoid logging request bodies or headers.
        print(f"{utc_now()} {self.client_address[0]} {fmt % args}", flush=True)


def main() -> int:
    if sys.argv[1:] == ["--healthcheck"]:
        import urllib.request

        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as response:
                return 0 if response.status == 200 else 1
        except Exception:
            return 1
    if sys.argv[1:] == ["--test-events"]:
        selected = [event for event in read_events() if event.get("alertname") == "TestIncident"]
        print(json.dumps(selected, separators=(",", ":")))
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "--tail":
        try:
            count = max(0, min(1000, int(sys.argv[2])))
        except ValueError:
            return 2
        print("\n".join(json.dumps(event, ensure_ascii=False) for event in read_events()[-count:]))
        return 0

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    load_seen_keys()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print(f"incident logger listening on :8080; JSONL retention is {MAX_BYTES} bytes x {BACKUP_COUNT + 1} files", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
