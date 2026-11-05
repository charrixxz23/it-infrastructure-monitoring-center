"""Synthetic tests for backup safety; no real Docker daemon or volumes are used."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import backup_itmc


class BackupItmcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        files = {
            ".gitignore": b".env\n.secrets/\ndata/\n",
            ".env.example": b"GRAFANA_ADMIN_USER=example-only\n",
            ".env": b"FAKE_SECRET_VALUE_DO_NOT_ARCHIVE\n",
            "README.md": b"safe metadata\n",
            "compose.yaml": b"services: {}\n",
            "compose.heartbeat.yaml": b"services: {}\n",
            ".github/workflows/ci.yml": b"name: synthetic\n",
            "config/prometheus/targets/home-network.example.yml": b"targets: [example.invalid]\n",
            "config/prometheus/targets/home-network.yml": b"targets: [192.0.2.91]\n",
            "services/example.py": b"print('source')\n",
            "scripts/example.ps1": b"Write-Output safe\n",
            "docs/backup-recovery.md": b"safe recovery documentation\n",
            "docs/audit-report.md": b"FAKE_PRIVATE_REPORT_DO_NOT_ARCHIVE\n",
            ".secrets/healthchecks-ping-url": b"https://hc-ping.invalid/fake-secret\n",
            "data/incident-log.jsonl": b"FAKE_RUNTIME_DATA_DO_NOT_ARCHIVE\n",
        }
        for relative, content in files.items():
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "-c", "core.autocrlf=false", "add", "-f", "--all"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=ITMC Synthetic Test",
                "-c",
                "user.email=itmc-test@example.invalid",
                "commit",
                "-q",
                "-m",
                "synthetic fixture",
            ],
            cwd=self.repo,
            check=True,
        )
        self.destination = self.base / "encrypted-backup-destination"
        self.destination.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create(self, **kwargs: object) -> Path:
        return backup_itmc.create_config_backup(
            self.repo,
            str(self.destination),
            confirm_encrypted_destination=True,
            now=datetime(2026, 11, 5, 17, 45, tzinfo=timezone.utc),
            disk_usage=lambda _path: SimpleNamespace(free=1024 * 1024 * 1024),
            **kwargs,
        )

    @staticmethod
    def _rewrite_zip(source: Path, destination: Path, replace: object) -> None:
        with (
            zipfile.ZipFile(source, "r") as original,
            zipfile.ZipFile(destination, "w") as changed,
        ):
            for name in original.namelist():
                content = original.read(name)
                if callable(replace):
                    content = replace(name, content)
                changed.writestr(name, content)

    def test_committed_snapshot_excludes_tracked_local_secrets_and_runtime_data(
        self,
    ) -> None:
        snapshot = backup_itmc._read_snapshot(self.repo)
        names = set(snapshot)
        self.assertIn("compose.yaml", names)
        self.assertIn("config/prometheus/targets/home-network.example.yml", names)
        self.assertIn(".env.example", names)
        self.assertNotIn(".env", names)
        self.assertNotIn("config/prometheus/targets/home-network.yml", names)
        self.assertNotIn(".secrets/healthchecks-ping-url", names)
        self.assertNotIn("data/incident-log.jsonl", names)
        self.assertNotIn("docs/audit-report.md", names)
        self.assertNotIn(
            b"FAKE_SECRET_VALUE_DO_NOT_ARCHIVE", b"".join(snapshot.values())
        )

    def test_backup_is_atomic_and_manifest_hashes_verify(self) -> None:
        archive = self._create()
        result = backup_itmc.verify_bundle(archive)
        self.assertEqual(result["git_commit"], backup_itmc._commit(self.repo))
        self.assertGreater(result["file_count"], 0)
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read(backup_itmc.MANIFEST_NAME))
        self.assertEqual(manifest["format"], "zip")
        self.assertEqual(manifest["created_at_utc"], "2026-11-05T17:45:00Z")
        self.assertTrue(
            all(len(file_record["sha256"]) == 64 for file_record in manifest["files"])
        )
        self.assertEqual(list(self.destination.glob("*.tmp")), [])

    def test_existing_backup_is_never_overwritten(self) -> None:
        archive = self._create()
        original = archive.read_bytes()
        with self.assertRaisesRegex(backup_itmc.BackupError, "refusing to overwrite"):
            self._create()
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(list(self.destination.glob("*.tmp")), [])

    def test_archive_created_during_backup_is_not_replaced(self) -> None:
        concurrent_archive = self.destination / (
            f"itmc-config-20261105T174500Z-{backup_itmc._commit(self.repo)[:8]}.zip"
        )

        def create_conflict(
            temporary_path: Path,
            files: dict[str, bytes],
            manifest: dict[str, object],
        ) -> None:
            backup_itmc.write_bundle(temporary_path, files, manifest)
            concurrent_archive.write_bytes(b"concurrent archive")

        with self.assertRaisesRegex(backup_itmc.BackupError, "refusing to overwrite"):
            self._create(writer=create_conflict)
        self.assertEqual(concurrent_archive.read_bytes(), b"concurrent archive")
        self.assertEqual(list(self.destination.glob(".itmc-backup-*.tmp")), [])

    def test_corrupt_manifest_is_rejected(self) -> None:
        archive = self._create()
        corrupted = self.destination / "corrupted-manifest.zip"
        self._rewrite_zip(
            archive,
            corrupted,
            lambda name, content: (
                b"{" if name == backup_itmc.MANIFEST_NAME else content
            ),
        )
        with self.assertRaises(backup_itmc.BackupError):
            backup_itmc.verify_bundle(corrupted)

    def test_tampered_file_and_altered_manifest_hash_are_rejected(self) -> None:
        archive = self._create()
        changed_content = self.destination / "changed-content.zip"

        def alter_content(name: str, content: bytes) -> bytes:
            return content + b"tampered" if name == "README.md" else content

        self._rewrite_zip(archive, changed_content, alter_content)
        with self.assertRaises(backup_itmc.BackupError):
            backup_itmc.verify_bundle(changed_content)

        changed_manifest = self.destination / "changed-manifest-hash.zip"

        def alter_hash(name: str, content: bytes) -> bytes:
            if name != backup_itmc.MANIFEST_NAME:
                return content
            manifest = json.loads(content)
            manifest["files"][0]["sha256"] = "0" * 64
            return json.dumps(manifest).encode("utf-8")

        self._rewrite_zip(archive, changed_manifest, alter_hash)
        with self.assertRaises(backup_itmc.BackupError):
            backup_itmc.verify_bundle(changed_manifest)

    def test_dirty_worktree_refuses_backup_without_creating_files(self) -> None:
        (self.repo / "README.md").write_text("uncommitted edit", encoding="utf-8")
        with self.assertRaises(backup_itmc.BackupError):
            self._create()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_unsafe_destinations_are_rejected(self) -> None:
        inside_repo = self.repo / "backup-output"
        inside_repo.mkdir()
        with self.assertRaises(backup_itmc.BackupError):
            backup_itmc.validate_destination(str(inside_repo), self.repo)
        with self.assertRaises(backup_itmc.BackupError):
            backup_itmc.validate_destination("relative-backup-dir", self.repo)
        with self.assertRaises(backup_itmc.BackupError):
            backup_itmc.validate_destination(r"\\server\share\backup", self.repo)
        sync_dir = self.base / "OneDrive" / "backup"
        sync_dir.mkdir(parents=True)
        with self.assertRaises(backup_itmc.BackupError):
            backup_itmc.validate_destination(str(sync_dir), self.repo)

    def test_archive_paths_reject_windows_drive_and_ads_syntax(self) -> None:
        self.assertFalse(backup_itmc._safe_archive_path("C:/outside.txt"))
        self.assertFalse(backup_itmc._safe_archive_path("README.md:stream"))

    def test_missing_encryption_attestation_is_rejected(self) -> None:
        with self.assertRaises(backup_itmc.BackupError):
            backup_itmc.create_config_backup(
                self.repo, str(self.destination), confirm_encrypted_destination=False
            )
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_insufficient_space_is_rejected(self) -> None:
        with self.assertRaises(backup_itmc.BackupError):
            backup_itmc.create_config_backup(
                self.repo,
                str(self.destination),
                confirm_encrypted_destination=True,
                disk_usage=lambda _path: SimpleNamespace(free=0),
            )
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_copy_failure_and_interruption_remove_only_temporary_file(self) -> None:
        def fail_copy(
            path: Path, _files: dict[str, bytes], _manifest: dict[str, object]
        ) -> None:
            path.write_bytes(b"partial archive")
            raise OSError("synthetic copy failure")

        with self.assertRaises(backup_itmc.BackupError):
            self._create(writer=fail_copy)
        self.assertEqual(list(self.destination.iterdir()), [])

        def interrupt_copy(
            path: Path, _files: dict[str, bytes], _manifest: dict[str, object]
        ) -> None:
            path.write_bytes(b"partial archive")
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self._create(writer=interrupt_copy)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_inventory_handles_missing_docker_cli_without_engine_access(self) -> None:
        with (
            patch.object(backup_itmc.shutil, "which", return_value=None),
            patch.object(backup_itmc.subprocess, "run") as run,
        ):
            inventory = backup_itmc.collect_inventory()
        run.assert_not_called()
        self.assertEqual(inventory["engine"], "cli_unavailable")
        self.assertEqual(len(inventory["volumes"]), 4)
        self.assertTrue(
            all(
                item["state"] == "not_inspected"
                for item in inventory["volumes"].values()
            )
        )

    def test_simulated_inventory_filters_services_and_never_requests_mountpoints(
        self,
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(
            command: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[1] == "info":
                return subprocess.CompletedProcess(
                    command, 0, stdout="test-engine\n", stderr=""
                )
            if command[1:3] == ["volume", "inspect"]:
                volume = command[-1]
                return subprocess.CompletedProcess(
                    command, 0, stdout=f"{volume}|local|local\n", stderr=""
                )
            if command[1] == "ps":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="prometheus|running|Up (healthy)\nprivate-service-name|running|Up\n",
                    stderr="",
                )
            self.fail("Inventory invoked an unexpected Docker operation")

        with (
            patch.object(backup_itmc.shutil, "which", return_value="docker"),
            patch.object(backup_itmc.subprocess, "run", side_effect=fake_run),
        ):
            inventory = backup_itmc.collect_inventory()
        self.assertEqual(inventory["engine"], "available")
        self.assertEqual(
            inventory["volumes"]["itmc-prometheus-data"]["state"], "available"
        )
        self.assertEqual(
            inventory["containers"]["prometheus"],
            {"state": "running", "health": "healthy"},
        )
        self.assertNotIn("private-service-name", json.dumps(inventory))
        command_text = " ".join(" ".join(command) for command in calls)
        self.assertNotIn("Mountpoint", command_text)
        self.assertFalse(
            any(
                any(
                    token in command
                    for token in ("stop", "restart", "rm", "prune", "exec", "run")
                )
                for command in calls
            )
        )

    def test_inventory_reports_engine_unavailable_without_leaking_cli_error(
        self,
    ) -> None:
        def failed_info(
            command: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="private host path and secret"
            )

        with (
            patch.object(backup_itmc.shutil, "which", return_value="docker"),
            patch.object(backup_itmc.subprocess, "run", side_effect=failed_info),
        ):
            inventory = backup_itmc.collect_inventory()
        self.assertEqual(inventory["engine"], "unavailable")
        self.assertNotIn("private host path", json.dumps(inventory))

    def test_container_query_failure_is_not_reported_as_missing_services(self) -> None:
        def fake_run(
            command: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if command[1] == "info":
                return subprocess.CompletedProcess(
                    command, 0, stdout="test-engine\n", stderr=""
                )
            if command[1:3] == ["volume", "inspect"]:
                volume = command[-1]
                return subprocess.CompletedProcess(
                    command, 0, stdout=f"{volume}|local|local\n", stderr=""
                )
            if command[1] == "ps":
                return subprocess.CompletedProcess(
                    command, 1, stdout="", stderr="private engine detail"
                )
            self.fail("Inventory invoked an unexpected Docker operation")

        with (
            patch.object(backup_itmc.shutil, "which", return_value="docker"),
            patch.object(backup_itmc.subprocess, "run", side_effect=fake_run),
        ):
            inventory = backup_itmc.collect_inventory()
        self.assertEqual(
            inventory["containers"]["prometheus"]["state"], "not_inspected"
        )
        self.assertNotIn("private engine detail", json.dumps(inventory))


if __name__ == "__main__":
    unittest.main()
