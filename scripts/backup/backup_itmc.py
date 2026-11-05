"""Read-only inventory and transactional backups of the committed ITMC config tree."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
PROJECT_LABEL = "it-infrastructure-monitoring-center"
MANIFEST_NAME = "itmc-backup-manifest.json"
MAX_FILES = 5000
MAX_SOURCE_BYTES = 512 * 1024 * 1024
DISK_MARGIN_BYTES = 4 * 1024 * 1024
DISK_MARGIN_RATIO = 0.05

EXPECTED_VOLUMES = {
    "prometheus": "itmc-prometheus-data",
    "grafana": "itmc-grafana-data",
    "alertmanager": "itmc-alertmanager-data",
    "incident_logger": "itmc-incident-log-data",
}
EXPECTED_SERVICES = (
    "prometheus",
    "grafana",
    "alertmanager",
    "node-exporter",
    "incident-logger",
    "blackbox-exporter",
    "heartbeat-agent",
    "heartbeat-egress-policy",
    "heartbeat-egress-proxy",
)

EXACT_PATHS = {
    ".gitignore",
    ".env.example",
    ".github/workflows/ci.yml",
    "README.md",
    "compose.yaml",
    "compose.heartbeat.yaml",
}
PATH_PREFIXES = ("config/", "services/", "scripts/", "docs/")
EXCLUDED_PARTS = {
    ".git",
    ".secrets",
    "data",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".astro",
    "dist",
    "venv",
    ".venv",
}
EXCLUDED_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".wal",
    ".log",
    ".jsonl",
    ".out",
    ".tmp",
    ".temp",
    ".bak",
}
SYNC_DIRECTORY_NAMES = {"onedrive", "dropbox", "google drive", "icloud", "box"}


class BackupError(Exception):
    """A safe-to-report backup or inventory error."""


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            input=input_bytes,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError(
            "A required local command was unavailable or timed out."
        ) from exc
    if result.returncode != 0:
        raise BackupError("A required local command failed; its output was withheld.")
    return result


def _repo_root(root: Path) -> Path:
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=root, timeout=15)
    try:
        return Path(result.stdout.decode("utf-8").strip()).resolve(strict=True)
    except (UnicodeError, OSError, RuntimeError) as exc:
        raise BackupError("Could not resolve the repository root.") from exc


def _commit(root: Path) -> str:
    result = _run(["git", "rev-parse", "HEAD"], cwd=root, timeout=15)
    commit = result.stdout.decode("ascii", errors="ignore").strip()
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise BackupError("The current Git commit could not be identified.")
    return commit


def _working_tree_dirty(root: Path) -> bool:
    result = _run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
        timeout=20,
    )
    return bool(result.stdout)


def _safe_archive_path(name: str) -> bool:
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        return False
    parsed = PurePosixPath(name)
    return not parsed.is_absolute() and all(
        part not in {"", ".", ".."} for part in parsed.parts
    )


def _is_excluded(name: str) -> bool:
    path = PurePosixPath(name)
    parts = {part.casefold() for part in path.parts}
    base = path.name.casefold()
    if parts & EXCLUDED_PARTS:
        return True
    if name == "config/prometheus/targets/home-network.yml":
        return True
    if name == "docs/audit-report.md":
        return True
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        return True
    if path.suffix.casefold() in EXCLUDED_SUFFIXES:
        return True
    return False


def _in_scope(name: str) -> bool:
    return name in EXACT_PATHS or any(
        name.startswith(prefix) for prefix in PATH_PREFIXES
    )


def _tree_entries(root: Path) -> list[tuple[str, str, str, int]]:
    result = _run(["git", "ls-tree", "-r", "-l", "-z", "HEAD"], cwd=root)
    entries: list[tuple[str, str, str, int]] = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id, size_text = metadata.decode("ascii").split()
            name = raw_path.decode("utf-8")
            size = int(size_text)
        except (ValueError, UnicodeError) as exc:
            raise BackupError(
                "The committed Git tree contains an unreadable entry."
            ) from exc
        if not _safe_archive_path(name):
            raise BackupError("The committed Git tree contains an unsafe path.")
        if not _in_scope(name) or _is_excluded(name):
            continue
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise BackupError("A scoped source entry is not a regular file.")
        entries.append((name, mode, object_id, size))
    if not entries:
        raise BackupError("No versioned configuration files were selected.")
    if len(entries) > MAX_FILES:
        raise BackupError(
            "The selected source contains too many files for this backup format."
        )
    if sum(entry[3] for entry in entries) > MAX_SOURCE_BYTES:
        raise BackupError("The selected source exceeds the configured size limit.")
    return sorted(entries)


def _read_snapshot(root: Path) -> dict[str, bytes]:
    entries = _tree_entries(root)
    object_ids = [entry[2] for entry in entries]
    batch_input = b"".join(
        object_id.encode("ascii") + b"\n" for object_id in object_ids
    )
    result = _run(["git", "cat-file", "--batch"], cwd=root, input_bytes=batch_input)
    output = result.stdout
    cursor = 0
    files: dict[str, bytes] = {}
    for name, _mode, object_id, expected_size in entries:
        header_end = output.find(b"\n", cursor)
        if header_end < 0:
            raise BackupError("Git returned an incomplete committed file.")
        header = output[cursor:header_end].split()
        if len(header) != 3 or header[0].decode("ascii", errors="ignore") != object_id:
            raise BackupError(
                "Git returned an unexpected object for the committed snapshot."
            )
        if header[1] != b"blob":
            raise BackupError("A committed source object is not a file.")
        try:
            size = int(header[2])
        except ValueError as exc:
            raise BackupError("Git returned an invalid committed file size.") from exc
        start = header_end + 1
        end = start + size
        if (
            size != expected_size
            or end >= len(output)
            or output[end : end + 1] != b"\n"
        ):
            raise BackupError("Git returned a truncated committed file.")
        files[name] = output[start:end]
        cursor = end + 1
    return files


def _estimate_required_bytes(source_bytes: int) -> int:
    return source_bytes + max(
        DISK_MARGIN_BYTES, math.ceil(source_bytes * DISK_MARGIN_RATIO)
    )


def _is_network_path(path: Path, raw_value: str) -> bool:
    normalized = raw_value.replace("/", "\\")
    if normalized.startswith("\\\\"):
        return True
    if os.name != "nt":
        return False
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        raise BackupError(
            "Could not verify that the backup destination uses a local drive."
        )


def validate_destination(value: str, root: Path) -> Path:
    raw_value = value.strip()
    destination = Path(raw_value).expanduser()
    if not raw_value or not destination.is_absolute():
        raise BackupError(
            "The destination must be an existing absolute local directory."
        )
    if _is_network_path(destination, raw_value):
        raise BackupError(
            "Network destinations are not accepted for this local backup mode."
        )
    try:
        resolved = destination.resolve(strict=True)
        resolved_root = _repo_root(root)
    except (OSError, RuntimeError) as exc:
        raise BackupError(
            "The destination must already exist and be accessible."
        ) from exc
    if not resolved.is_dir():
        raise BackupError("The destination must be an existing directory.")
    if resolved == resolved_root or resolved_root in resolved.parents:
        raise BackupError("The destination must be outside the Git repository.")
    lowered_parts = {part.casefold() for part in resolved.parts}
    if lowered_parts & (EXCLUDED_PARTS | SYNC_DIRECTORY_NAMES):
        raise BackupError(
            "The destination is inside a sensitive or commonly synchronized directory."
        )
    if not os.access(resolved, os.W_OK):
        raise BackupError("The destination is not writable by the current user.")
    return resolved


def _safe_docker_call(
    docker: str, args: list[str], timeout: int = 5
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [docker, *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def collect_inventory(docker_path: str | None = None) -> dict[str, Any]:
    docker = docker_path if docker_path is not None else shutil.which("docker")
    if not docker:
        return {
            "engine": "cli_unavailable",
            "volumes": {
                name: {"state": "not_inspected", "used_bytes": None}
                for name in EXPECTED_VOLUMES.values()
            },
            "containers": {
                name: {"state": "not_inspected", "health": "not_reported"}
                for name in EXPECTED_SERVICES
            },
        }
    info = _safe_docker_call(docker, ["info", "--format", "{{.ServerVersion}}"])
    if info is None or info.returncode != 0 or not info.stdout.strip():
        return {
            "engine": "unavailable",
            "volumes": {
                name: {"state": "not_inspected", "used_bytes": None}
                for name in EXPECTED_VOLUMES.values()
            },
            "containers": {
                name: {"state": "not_inspected", "health": "not_reported"}
                for name in EXPECTED_SERVICES
            },
        }

    volumes: dict[str, dict[str, Any]] = {}
    for volume_name in EXPECTED_VOLUMES.values():
        result = _safe_docker_call(
            docker,
            [
                "volume",
                "inspect",
                "--format",
                "{{.Name}}|{{.Driver}}|{{.Scope}}",
                volume_name,
            ],
        )
        fields = (
            result.stdout.strip().split("|")
            if result and result.returncode == 0
            else []
        )
        if len(fields) == 3 and fields[0] == volume_name:
            volumes[volume_name] = {
                "state": "available",
                "driver": fields[1] if fields[1] in {"local"} else "other",
                "scope": fields[2] if fields[2] in {"local", "global"} else "other",
                "used_bytes": None,
            }
        else:
            volumes[volume_name] = {
                "state": "missing_or_unavailable",
                "used_bytes": None,
            }

    format_value = '{{.Label "com.docker.compose.service"}}|{{.State}}|{{.Status}}'
    containers_result = _safe_docker_call(
        docker,
        [
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={PROJECT_LABEL}",
            "--format",
            format_value,
        ],
    )
    found: dict[str, dict[str, str]] = {}
    containers_inspected = bool(containers_result and containers_result.returncode == 0)
    if containers_inspected:
        for line in containers_result.stdout.splitlines():
            fields = line.split("|", 2)
            if len(fields) != 3 or fields[0] not in EXPECTED_SERVICES:
                continue
            state = (
                fields[1]
                if fields[1]
                in {
                    "created",
                    "running",
                    "paused",
                    "restarting",
                    "exited",
                    "dead",
                    "removing",
                }
                else "unknown"
            )
            status = fields[2].casefold()
            health = (
                "healthy"
                if "(healthy)" in status
                else "unhealthy"
                if "(unhealthy)" in status
                else "starting"
                if "(health: starting)" in status
                else "not_reported"
            )
            found[fields[0]] = {"state": state, "health": health}
    missing_state = "not_found" if containers_inspected else "not_inspected"
    containers = {
        service: found.get(service, {"state": missing_state, "health": "not_reported"})
        for service in EXPECTED_SERVICES
    }
    return {"engine": "available", "volumes": volumes, "containers": containers}


def _manifest_for(
    files: dict[str, bytes], commit: str, created_at: datetime
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at_utc": created_at.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "git_commit": commit,
        "format": "zip",
        "scope": "versioned configuration, source, CI metadata, and selected documentation from committed HEAD",
        "included_resources": [
            ".github/workflows/ci.yml",
            "compose.yaml and compose.heartbeat.yaml",
            "config/",
            "services/",
            "scripts/",
            "selected versioned documentation",
            "README.md",
        ],
        "excluded_classes": [
            "Docker volumes and runtime databases",
            "local environment and secret files",
            "host-specific private monitoring targets",
            "local incident data, logs, caches, and generated website assets",
            "the published security audit report",
        ],
        "files": [
            {
                "path": name,
                "size_bytes": len(files[name]),
                "sha256": hashlib.sha256(files[name]).hexdigest(),
            }
            for name in sorted(files)
        ],
    }


def write_bundle(path: Path, files: dict[str, bytes], manifest: dict[str, Any]) -> None:
    try:
        with zipfile.ZipFile(
            path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as bundle:
            for name in sorted(files):
                bundle.writestr(name, files[name])
            bundle.writestr(
                MANIFEST_NAME,
                json.dumps(
                    manifest, ensure_ascii=False, indent=2, sort_keys=True
                ).encode("utf-8"),
            )
        with path.open("r+b") as archive_file:
            archive_file.flush()
            os.fsync(archive_file.fileno())
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(
            "The temporary backup archive could not be written completely."
        ) from exc


def verify_bundle(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path, mode="r") as bundle:
            members = bundle.infolist()
            names = [member.filename for member in members]
            if len(names) != len(set(names)) or names.count(MANIFEST_NAME) != 1:
                raise BackupError(
                    "The archive has duplicate entries or no unique manifest."
                )
            payload_names = [name for name in names if name != MANIFEST_NAME]
            if any(
                not _safe_archive_path(name)
                or not _in_scope(name)
                or _is_excluded(name)
                for name in payload_names
            ):
                raise BackupError(
                    "The archive contains an unsafe or out-of-scope path."
                )
            for member in members:
                mode = (member.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise BackupError("The archive contains a symbolic link.")
                if bundle.read(member) is None:
                    raise BackupError("The archive contains an unreadable entry.")
            manifest = json.loads(bundle.read(MANIFEST_NAME).decode("utf-8"))
            if (
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != 1
                or manifest.get("format") != "zip"
            ):
                raise BackupError("The archive manifest has an unsupported format.")
            commit = manifest.get("git_commit")
            if (
                not isinstance(commit, str)
                or len(commit) != 40
                or any(ch not in "0123456789abcdef" for ch in commit)
            ):
                raise BackupError("The archive manifest has an invalid Git commit.")
            declared = manifest.get("files")
            if not isinstance(declared, list):
                raise BackupError("The archive manifest file list is invalid.")
            declared_by_name: dict[str, dict[str, Any]] = {}
            for item in declared:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise BackupError(
                        "The archive manifest contains an invalid file record."
                    )
                file_name = item["path"]
                if file_name in declared_by_name or not _safe_archive_path(file_name):
                    raise BackupError(
                        "The archive manifest contains duplicate or unsafe paths."
                    )
                declared_by_name[file_name] = item
            if set(declared_by_name) != set(payload_names):
                raise BackupError("The archive contents do not match the manifest.")
            for file_name, item in declared_by_name.items():
                content = bundle.read(file_name)
                expected = item.get("sha256")
                if (
                    not isinstance(expected, str)
                    or len(expected) != 64
                    or hashlib.sha256(content).hexdigest() != expected
                ):
                    raise BackupError(
                        "A file hash does not match the archive manifest."
                    )
                if item.get("size_bytes") != len(content):
                    raise BackupError(
                        "A file size does not match the archive manifest."
                    )
            if bundle.testzip() is not None:
                raise BackupError("The archive failed its ZIP integrity check.")
    except BackupError:
        raise
    except (
        OSError,
        zipfile.BadZipFile,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
    ) as exc:
        raise BackupError("The archive or its manifest is invalid or corrupt.") from exc
    return {
        "git_commit": commit,
        "file_count": len(payload_names),
        "created_at_utc": manifest.get("created_at_utc"),
    }


def create_config_backup(
    root: Path,
    destination_value: str,
    *,
    confirm_encrypted_destination: bool,
    now: datetime | None = None,
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
    writer: Callable[[Path, dict[str, bytes], dict[str, Any]], None] = write_bundle,
) -> Path:
    if not confirm_encrypted_destination:
        raise BackupError(
            "Explicit confirmation of an encrypted local destination is required."
        )
    repo = _repo_root(root)
    if _working_tree_dirty(repo):
        raise BackupError("The Git working tree is not clean; no backup was created.")
    destination = validate_destination(destination_value, repo)
    files = _read_snapshot(repo)
    source_bytes = sum(len(content) for content in files.values())
    required_bytes = _estimate_required_bytes(source_bytes)
    try:
        free_bytes = disk_usage(destination).free
    except OSError as exc:
        raise BackupError(
            "Free space could not be checked for the destination."
        ) from exc
    if free_bytes < required_bytes:
        raise BackupError(
            "The destination does not have enough free space for the estimated archive."
        )
    commit = _commit(repo)
    created_at = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        raise BackupError("The backup timestamp must include a timezone.")
    manifest = _manifest_for(files, commit, created_at)
    stamp = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = destination / f"itmc-config-{stamp}-{commit[:8]}.zip"
    if final_path.exists():
        raise BackupError(
            "A backup with this timestamp already exists; refusing to overwrite it."
        )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".itmc-backup-", suffix=".tmp", dir=destination, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        writer(temporary_path, files, manifest)
        verify_bundle(temporary_path)
        try:
            # Publish without replacing an archive that may have appeared after
            # the earlier existence check. Both paths share the destination FS.
            os.link(temporary_path, final_path)
        except FileExistsError as exc:
            raise BackupError(
                "A backup appeared at the target path; refusing to overwrite it."
            ) from exc
        try:
            temporary_path.unlink()
        except OSError as exc:
            raise BackupError(
                "The verified archive was published, but its temporary file could not be removed."
            ) from exc
        temporary_path = None
    except BackupError:
        raise
    except OSError as exc:
        raise BackupError(
            "The backup could not be finalized; the previous destination was left unchanged."
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return final_path


def _format_inventory(inventory: dict[str, Any]) -> str:
    lines = [
        f"Docker Engine: {inventory['engine']}",
        "Persistent volumes (metadata only):",
    ]
    for volume_name, details in inventory["volumes"].items():
        driver = details.get("driver")
        scope = details.get("scope")
        suffix = f"; driver={driver}; scope={scope}" if driver and scope else ""
        lines.append(
            f"- {volume_name}: {details['state']}{suffix}; used bytes=not available from portable inspect"
        )
    lines.append("Project containers (service labels and state only):")
    for service, details in inventory["containers"].items():
        lines.append(f"- {service}: {details['state']}; health={details['health']}")
    return "\n".join(lines)


def run_plan(root: Path, destination_value: str | None = None) -> int:
    repo = _repo_root(root)
    files = _tree_entries(repo)
    commit = _commit(repo)
    source_bytes = sum(item[3] for item in files)
    inventory = collect_inventory()
    print("ITMC configuration backup plan (read-only)")
    print(f"Git snapshot: {commit}")
    print(f"Selected committed files: {len(files)}; source bytes: {source_bytes}")
    print(
        f"Estimated free-space requirement: {_estimate_required_bytes(source_bytes)} bytes"
    )
    print(
        f"Working tree changes present: {'yes' if _working_tree_dirty(repo) else 'no'}; uncommitted files are never included"
    )
    if destination_value:
        validate_destination(destination_value, repo)
        try:
            free_bytes = shutil.disk_usage(Path(destination_value).resolve()).free
            print(f"Destination: validated local directory; free bytes: {free_bytes}")
            print(
                f"Estimated capacity check: {'pass' if free_bytes >= _estimate_required_bytes(source_bytes) else 'insufficient'}"
            )
        except OSError:
            print("Destination free-space check: unavailable")
    else:
        print(
            "Destination: not supplied; destination permissions and free space were not checked"
        )
    print(_format_inventory(inventory))
    print("Mode Plan writes no files and does not read or modify volume contents.")
    print("No volume snapshot or database backup is created by this plan.")
    return 0


def run_inventory() -> int:
    print(_format_inventory(collect_inventory()))
    print(
        "Only Docker CLI metadata is queried; volume mountpoints, environment, logs, and volume contents are not read."
    )
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Non-destructive ITMC backup planning and verification"
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    plan_parser = subparsers.add_parser("plan", help="Print a read-only backup plan")
    plan_parser.add_argument("--destination")
    subparsers.add_parser(
        "inventory", help="Inspect expected Docker resource metadata read-only"
    )
    backup_parser = subparsers.add_parser(
        "backup-config", help="Create an explicit config-only backup"
    )
    backup_parser.add_argument("--destination", required=True)
    backup_parser.add_argument("--confirm-encrypted-destination", action="store_true")
    verify_parser = subparsers.add_parser(
        "verify", help="Verify a config backup manifest and all file hashes"
    )
    verify_parser.add_argument("--archive", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        if args.mode == "plan":
            return run_plan(ROOT, args.destination)
        if args.mode == "inventory":
            return run_inventory()
        if args.mode == "backup-config":
            archive = create_config_backup(
                ROOT,
                args.destination,
                confirm_encrypted_destination=args.confirm_encrypted_destination,
            )
            print(f"Configuration backup created and verified: {archive.name}")
            return 0
        if args.mode == "verify":
            result = verify_bundle(Path(args.archive).expanduser())
            print(
                f"PASS: archive integrity and file hashes verified; commit={result['git_commit']}; "
                f"files={result['file_count']}"
            )
            return 0
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
