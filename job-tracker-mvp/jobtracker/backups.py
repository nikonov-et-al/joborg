from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any
from uuid import uuid4
import zipfile


MANIFEST_FILENAME = "manifest.json"
MIGRATION_BACKUP_DIRECTORY = Path("backups") / "migrations"
EXPORT_DIRECTORY = Path("backups") / "exports"


class DatabaseHealthError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationBackup:
    path: Path
    created_at: str
    source_schema_version: int
    target_schema_version: int
    app_version: str
    size: int
    sha256: str
    verified: bool

    def public_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "created_at": self.created_at,
            "source_schema_version": self.source_schema_version,
            "target_schema_version": self.target_schema_version,
            "app_version": self.app_version,
            "size": self.size,
            "sha256": self.sha256,
            "verified": self.verified,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat().replace("+00:00", "Z")


def _filename_timestamp(moment: datetime | None = None) -> str:
    return (moment or _now()).strftime("%Y%m%dT%H%M%S%fZ")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def assert_database_healthy(connection: sqlite3.Connection) -> None:
    integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise DatabaseHealthError("Database integrity check failed")
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise DatabaseHealthError(
            f"Database foreign-key check failed with {len(foreign_keys)} violation(s)"
        )


def snapshot_database(
    source_connection: sqlite3.Connection,
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    target = sqlite3.connect(temporary)
    try:
        source_connection.backup(target)
    finally:
        target.close()
    try:
        probe = sqlite3.connect(f"{temporary.as_uri()}?mode=ro", uri=True)
        try:
            assert_database_healthy(probe)
        finally:
            probe.close()
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def create_migration_backup(
    connection: sqlite3.Connection,
    database: Path,
    *,
    source_schema_version: int,
    target_schema_version: int,
    app_version: str,
) -> MigrationBackup:
    root = database.parent / MIGRATION_BACKUP_DIRECTORY
    name = (
        f"{_filename_timestamp()}-v{source_schema_version}-to-v"
        f"{target_schema_version}-{uuid4().hex[:8]}"
    )
    final = root / name
    staging = root / f".{name}.tmp"
    backup_database = staging / "jobtracker.sqlite3"
    try:
        staging.mkdir(parents=True)
        snapshot_database(connection, backup_database)
        payload: dict[str, object] = {
            "format_version": 1,
            "kind": "migration",
            "created_at": _timestamp(),
            "app_version": app_version,
            "source_schema_version": source_schema_version,
            "target_schema_version": target_schema_version,
            "database": backup_database.name,
            "size": backup_database.stat().st_size,
            "sha256": _hash_file(backup_database),
            "verified": False,
        }
        _atomic_json(staging / MANIFEST_FILENAME, payload)
        os.replace(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _migration_backup_from_manifest(final)


def _migration_backup_from_manifest(folder: Path) -> MigrationBackup:
    payload = json.loads((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    if payload.get("format_version") != 1 or payload.get("kind") != "migration":
        raise ValueError("Migration backup manifest is unsupported")
    database_name = payload.get("database")
    if database_name != "jobtracker.sqlite3":
        raise ValueError("Migration backup database name is invalid")
    backup_database = folder / database_name
    if not backup_database.is_file():
        raise ValueError("Migration backup database is missing")
    if backup_database.stat().st_size != int(payload["size"]):
        raise ValueError("Migration backup database size does not match")
    if _hash_file(backup_database) != str(payload["sha256"]):
        raise ValueError("Migration backup database checksum does not match")
    probe = sqlite3.connect(f"{backup_database.as_uri()}?mode=ro", uri=True)
    try:
        assert_database_healthy(probe)
        if int(probe.execute("PRAGMA user_version").fetchone()[0]) != int(
            payload["source_schema_version"]
        ):
            raise ValueError("Migration backup schema version does not match")
    finally:
        probe.close()
    return MigrationBackup(
        path=folder,
        created_at=str(payload["created_at"]),
        source_schema_version=int(payload["source_schema_version"]),
        target_schema_version=int(payload["target_schema_version"]),
        app_version=str(payload["app_version"]),
        size=int(payload["size"]),
        sha256=str(payload["sha256"]),
        verified=bool(payload.get("verified")),
    )


def list_migration_backups(database: Path) -> list[MigrationBackup]:
    root = database.parent / MIGRATION_BACKUP_DIRECTORY
    if not root.is_dir():
        return []
    backups: list[MigrationBackup] = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        try:
            backups.append(_migration_backup_from_manifest(folder))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return sorted(backups, key=lambda item: item.created_at, reverse=True)


def mark_migration_backup_verified(backup: MigrationBackup) -> MigrationBackup:
    manifest = backup.path / MANIFEST_FILENAME
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["verified"] = True
    payload["verified_at"] = _timestamp()
    _atomic_json(manifest, payload)
    return _migration_backup_from_manifest(backup.path)


def prune_migration_backups(
    database: Path,
    *,
    keep_count: int = 10,
    max_age_days: int = 365,
    now: datetime | None = None,
) -> list[Path]:
    if keep_count < 1 or max_age_days < 1:
        raise ValueError("Migration backup retention must keep a positive count and age")
    backups = list_migration_backups(database)
    verified = [backup for backup in backups if backup.verified]
    cutoff = (now or _now()) - timedelta(days=max_age_days)
    removed: list[Path] = []
    for index, backup in enumerate(verified):
        created = datetime.fromisoformat(backup.created_at.replace("Z", "+00:00"))
        if index < keep_count or created >= cutoff:
            continue
        shutil.rmtree(backup.path)
        removed.append(backup.path)
    return removed


def _copy_export_source(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise ValueError(f"Workspace symbolic links are not supported: {source}")
    if source.is_dir():
        for item in source.rglob("*"):
            if item.is_symlink():
                raise ValueError(f"Workspace symbolic links are not supported: {item}")
            if item.is_file():
                target = destination / item.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def create_workspace_export(workspace: Path, *, app_version: str) -> Path:
    workspace = workspace.resolve()
    database = workspace / "jobtracker.sqlite3"
    if not database.is_file():
        raise FileNotFoundError("Workspace database is missing")
    export_root = workspace / EXPORT_DIRECTORY
    export_root.mkdir(parents=True, exist_ok=True)
    final = export_root / f"jobtracker-export-{_filename_timestamp()}-{uuid4().hex[:8]}.zip"
    temporary_zip = export_root / f".{final.name}.tmp"
    with TemporaryDirectory(dir=export_root, prefix=".export-") as temp:
        staging = Path(temp)
        source_connection = sqlite3.connect(
            f"{database.as_uri()}?mode=ro",
            uri=True,
        )
        try:
            assert_database_healthy(source_connection)
            schema_version = int(
                source_connection.execute("PRAGMA user_version").fetchone()[0]
            )
            snapshot_database(source_connection, staging / "jobtracker.sqlite3")
        finally:
            source_connection.close()

        for name in ("files", "profile"):
            _copy_export_source(workspace / name, staging / name)

        entries: dict[str, dict[str, object]] = {}
        for path in sorted(item for item in staging.rglob("*") if item.is_file()):
            relative = path.relative_to(staging).as_posix()
            entries[relative] = {
                "size": path.stat().st_size,
                "sha256": _hash_file(path),
            }
        manifest: dict[str, object] = {
            "format_version": 1,
            "kind": "workspace_export",
            "created_at": _timestamp(),
            "app_version": app_version,
            "schema_version": schema_version,
            "files": entries,
        }
        _atomic_json(staging / MANIFEST_FILENAME, manifest)
        try:
            with zipfile.ZipFile(
                temporary_zip,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                for path in sorted(item for item in staging.rglob("*") if item.is_file()):
                    archive.write(path, path.relative_to(staging).as_posix())
            os.replace(temporary_zip, final)
        finally:
            temporary_zip.unlink(missing_ok=True)
    try:
        verify_workspace_export(final)
    except Exception:
        final.unlink(missing_ok=True)
        raise
    return final


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def verify_workspace_export(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if MANIFEST_FILENAME not in names or not all(_safe_archive_name(name) for name in names):
            raise ValueError("Workspace export contains unsafe or missing entries")
        manifest = json.loads(archive.read(MANIFEST_FILENAME))
        if (
            manifest.get("format_version") != 1
            or manifest.get("kind") != "workspace_export"
            or not isinstance(manifest.get("files"), dict)
        ):
            raise ValueError("Workspace export manifest is unsupported")
        entries = manifest["files"]
        if names != set(entries) | {MANIFEST_FILENAME}:
            raise ValueError("Workspace export entries do not match its manifest")
        for name, expected in entries.items():
            if not _safe_archive_name(name) or not isinstance(expected, dict):
                raise ValueError("Workspace export manifest contains an unsafe entry")
            digest = hashlib.sha256()
            size = 0
            with archive.open(name) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            if size != int(expected["size"]) or digest.hexdigest() != str(
                expected["sha256"]
            ):
                raise ValueError(f"Workspace export checksum failed: {name}")
        if "jobtracker.sqlite3" not in entries:
            raise ValueError("Workspace export database is missing")
        with TemporaryDirectory() as temp:
            extracted = Path(temp) / "jobtracker.sqlite3"
            with archive.open("jobtracker.sqlite3") as source, extracted.open("wb") as target:
                shutil.copyfileobj(source, target)
            probe = sqlite3.connect(f"{extracted.as_uri()}?mode=ro", uri=True)
            try:
                assert_database_healthy(probe)
                if int(probe.execute("PRAGMA user_version").fetchone()[0]) != int(
                    manifest["schema_version"]
                ):
                    raise ValueError("Workspace export schema version does not match")
                for stored_path, expected_hash, expected_size in probe.execute(
                    "SELECT stored_path, sha256, size FROM documents"
                ):
                    managed_name = (
                        PurePosixPath("files") / PurePosixPath(str(stored_path))
                    ).as_posix()
                    managed = entries.get(managed_name)
                    if (
                        not isinstance(managed, dict)
                        or str(managed.get("sha256")) != str(expected_hash)
                        or int(managed.get("size", -1)) != int(expected_size)
                    ):
                        raise ValueError(
                            f"Workspace export managed file does not match: {stored_path}"
                        )
            finally:
                probe.close()
    return manifest
