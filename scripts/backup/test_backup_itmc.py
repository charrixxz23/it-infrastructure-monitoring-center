"""Synthetic tests for backup safety; no real Docker daemon or volumes are used."""

from __future__ import annotations

import json
import struct
import subprocess
import tempfile
import unittest
import warnings
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

    @staticmethod
    def _write_manifest_archive(path: Path, files: dict[str, bytes]) -> None:
        manifest = {
            "schema_version": 1,
            "created_at_utc": "2026-11-05T17:45:00Z",
            "git_commit": "0" * 40,
            "format": "zip",
            "files": [
                {
                    "path": name,
                    "size_bytes": len(content),
                    "sha256": backup_itmc.hashlib.sha256(content).hexdigest(),
                }
                for name, content in sorted(files.items())
            ],
        }
        with zipfile.ZipFile(
            path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name, content in sorted(files.items()):
                archive.writestr(name, content)
            archive.writestr(
                backup_itmc.MANIFEST_NAME,
                json.dumps(manifest, sort_keys=True).encode("utf-8"),
            )

    @staticmethod
    def _forge_declared_size(path: Path, name: str, declared_size: int) -> None:
        with zipfile.ZipFile(path, "r") as archive:
            member = archive.getinfo(name)
            local_offset = member.header_offset
        raw = bytearray(path.read_bytes())
        struct.pack_into("<I", raw, local_offset + 22, declared_size)

        cursor = 0
        while True:
            cursor = raw.find(b"PK\x01\x02", cursor)
            if cursor < 0:
                raise AssertionError("synthetic ZIP central directory was not found")
            name_size = struct.unpack_from("<H", raw, cursor + 28)[0]
            extra_size = struct.unpack_from("<H", raw, cursor + 30)[0]
            comment_size = struct.unpack_from("<H", raw, cursor + 32)[0]
            encoded_name = raw[cursor + 46 : cursor + 46 + name_size]
            flags = struct.unpack_from("<H", raw, cursor + 8)[0]
            encoding = "utf-8" if flags & 0x0800 else "cp437"
            if encoded_name.decode(encoding) == name:
                struct.pack_into("<I", raw, cursor + 24, declared_size)
                break
            cursor += 46 + name_size + extra_size + comment_size
        path.write_bytes(raw)

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

    def test_rejects_excessive_entry_count_before_zipinfo_expansion(self) -> None:
        archive_path = self.destination / "too-many-entries.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for index in range(4):
                archive.writestr(f"config/file-{index}.txt", b"x")
        with patch.object(backup_itmc, "MAX_ARCHIVE_ENTRIES", 3):
            with self.assertRaisesRegex(backup_itmc.BackupError, "too many entries"):
                backup_itmc.verify_bundle(archive_path)

    def test_rejects_duplicate_zip_entry_names(self) -> None:
        archive_path = self.destination / "duplicate-entry.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("README.md", b"first")
                archive.writestr("README.md", b"second")
        with self.assertRaisesRegex(backup_itmc.BackupError, "duplicate"):
            backup_itmc.verify_bundle(archive_path)

    def test_rejects_unsafe_zip_paths(self) -> None:
        archive_path = self.destination / "unsafe-entry.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../outside.txt", b"not extracted")
        with self.assertRaisesRegex(backup_itmc.BackupError, "unsafe"):
            backup_itmc.verify_bundle(archive_path)

    def test_rejects_compressed_archive_and_manifest_limits(self) -> None:
        oversized_archive = self.destination / "oversized-archive.zip"
        oversized_archive.write_bytes(b"x" * 128)
        with patch.object(backup_itmc, "MAX_ARCHIVE_BYTES", 64):
            with self.assertRaisesRegex(backup_itmc.BackupError, "compressed size"):
                backup_itmc.verify_bundle(oversized_archive)

        oversized_manifest = self.destination / "oversized-manifest.zip"
        with zipfile.ZipFile(oversized_manifest, "w") as archive:
            archive.writestr(backup_itmc.MANIFEST_NAME, b"x" * 257)
        with patch.object(backup_itmc, "MAX_MANIFEST_BYTES", 256):
            with self.assertRaisesRegex(backup_itmc.BackupError, "uncompressed size"):
                backup_itmc.verify_bundle(oversized_manifest)

    def test_malformed_deep_manifest_is_reported_as_safe_backup_error(self) -> None:
        archive_path = self.destination / "deep-manifest.zip"
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(
                backup_itmc.MANIFEST_NAME, b"[" * 5000 + b"0" + b"]" * 5000
            )
        with self.assertRaises(backup_itmc.BackupError) as raised:
            backup_itmc.verify_bundle(archive_path)
        # JSON parser behavior differs across supported Python versions: one
        # may report excessive nesting while another rejects the resulting
        # top-level value as an unsupported manifest format. Both outcomes
        # must remain bounded, safe errors that do not include input content.
        self.assertIn(
            str(raised.exception),
            {
                "The archive or its manifest is invalid or corrupt.",
                "The archive manifest has an unsupported format.",
            },
        )

    def test_actual_stream_limit_rejects_forged_small_zipinfo_size(self) -> None:
        archive_path = self.destination / "forged-member-size.zip"
        self._write_manifest_archive(archive_path, {"config/blob.bin": b"A" * 4096})
        self._forge_declared_size(archive_path, "config/blob.bin", 1)
        with patch.object(backup_itmc, "MAX_SOURCE_BYTES", 512):
            with self.assertRaisesRegex(
                backup_itmc.BackupError, "actual uncompressed size"
            ):
                backup_itmc.verify_bundle(archive_path)

    def test_actual_manifest_limit_rejects_forged_small_zipinfo_size(self) -> None:
        archive_path = self.destination / "forged-manifest-size.zip"
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(backup_itmc.MANIFEST_NAME, b"A" * 4096)
        self._forge_declared_size(archive_path, backup_itmc.MANIFEST_NAME, 1)
        with patch.object(backup_itmc, "MAX_MANIFEST_BYTES", 512):
            with self.assertRaisesRegex(
                backup_itmc.BackupError, "actual uncompressed size"
            ):
                backup_itmc.verify_bundle(archive_path)

    def test_actual_total_limit_rejects_forged_small_entry_sizes(self) -> None:
        archive_path = self.destination / "forged-total-size.zip"
        self._write_manifest_archive(
            archive_path,
            {"config/a.bin": b"A" * 300, "config/b.bin": b"B" * 300},
        )
        self._forge_declared_size(archive_path, backup_itmc.MANIFEST_NAME, 1)
        with (
            patch.object(backup_itmc, "MAX_SOURCE_BYTES", 1024),
            patch.object(backup_itmc, "MAX_MANIFEST_BYTES", 1024),
            patch.object(backup_itmc, "MAX_TOTAL_UNCOMPRESSED_BYTES", 700),
        ):
            with self.assertRaisesRegex(
                backup_itmc.BackupError, "actual total uncompressed size"
            ):
                backup_itmc.verify_bundle(archive_path)

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
        self.assertFalse(backup_itmc._safe_archive_path("config//duplicate-path.txt"))
        self.assertFalse(backup_itmc._safe_archive_path("config/./dot-path.txt"))

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
