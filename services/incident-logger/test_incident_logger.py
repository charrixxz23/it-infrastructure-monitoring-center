import http.client
import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("incident_logger.py")
SPEC = importlib.util.spec_from_file_location("incident_logger", MODULE_PATH)
logger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(logger)


class IncidentLoggerWebhookTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path_patch = mock.patch.object(logger, "LOG_PATH", Path(self.temp_dir.name) / "incidents.jsonl")
        self.path_patch.start()
        logger.seen_keys.clear()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), logger.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def post(self, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = json.dumps(payload).encode()
        connection.request("POST", "/webhook", body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    @staticmethod
    def alert(status="firing"):
        return {
            "status": status,
            "fingerprint": "test-fingerprint-001",
            "startsAt": "2026-10-23T12:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "labels": {
                "alertname": "TestIncident",
                "severity": "info",
                "instance": "unit-test:9100",
                "source": "automated-test",
                "private_label": "must-not-be-copied",
            },
            "annotations": {"summary": "Controlled test", "description": "Unit test"},
        }

    def test_deduplicates_retries_but_records_resolved_transition(self):
        payload = {"version": "4", "status": "firing", "alerts": [self.alert("firing")]}
        status, first = self.post(payload)
        self.assertEqual(status, 200)
        self.assertEqual(first["written"], 1)

        status, duplicate = self.post(payload)
        self.assertEqual(status, 200)
        self.assertEqual(duplicate["written"], 0)
        self.assertEqual(duplicate["duplicates"], 1)

        status, resolved = self.post({"version": "4", "status": "resolved", "alerts": [self.alert("resolved")]})
        self.assertEqual(status, 200)
        self.assertEqual(resolved["written"], 1)

        events = logger.read_events()
        self.assertEqual([event["status"] for event in events], ["FIRING", "RESOLVED"])
        self.assertNotIn("private_label", events[0]["labels"])
        self.assertEqual(events[0]["labels"]["instance"], "unit-test:9100")

        logger.seen_keys.clear()
        logger.load_seen_keys()
        status, duplicate_after_restart = self.post(payload)
        self.assertEqual(status, 200)
        self.assertEqual(duplicate_after_restart["written"], 0)

    def test_rejects_invalid_alert_state_without_writing(self):
        status, result = self.post({"alerts": [self.alert("pending")]})
        self.assertEqual(status, 400)
        self.assertIn("alert.status", result["error"])
        self.assertEqual(logger.read_events(), [])

    def test_unknown_route_is_not_a_webhook(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("POST", "/", body=b"{}")
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 404)

    def test_history_rotation_is_bounded(self):
        with mock.patch.object(logger, "MAX_BYTES", 200), mock.patch.object(logger, "BACKUP_COUNT", 1):
            for index in range(5):
                event = {
                    "fingerprint": f"rotation-{index}",
                    "status": "FIRING",
                    "startsAt": f"2026-10-23T12:00:0{index}Z",
                    "alertname": "TestIncident",
                }
                self.assertEqual(logger.persist_events([event]), 1)

            self.assertLessEqual(logger.LOG_PATH.stat().st_size, 200)
            self.assertLessEqual(Path(f"{logger.LOG_PATH}.1").stat().st_size, 200)
            self.assertFalse(Path(f"{logger.LOG_PATH}.2").exists())
            self.assertEqual(len(logger.read_events()), 2)
