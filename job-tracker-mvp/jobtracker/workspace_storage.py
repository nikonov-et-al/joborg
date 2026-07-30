from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
from tempfile import NamedTemporaryFile
import unicodedata
from uuid import uuid4

import yaml

from .database import MIN_SUPPORTED_SCHEMA_VERSION, SCHEMA_VERSION, connect_database
from .file_store import ManagedFileStore
from .paths import (
    WORKSPACES_DIRECTORY,
    WorkspaceRecord,
    validate_workspace_folder,
)
from .services import save_profile


_CONTROL_FILES = {
    "jobtracker.sqlite3",
    "jobtracker.sqlite3-wal",
    "jobtracker.sqlite3-shm",
    "workspaces.yml",
    "settings.yml",
    "workspaces.v1.yml",
    ".layout-migration.yml",
}
_GLOBAL_DIRECTORIES = {"backups", WORKSPACES_DIRECTORY}
LAYOUT_MIGRATION_FILENAME = ".layout-migration.yml"
LEGACY_REGISTRY_BACKUP_FILENAME = "workspaces.v1.yml"
LAYOUT_MIGRATION_VERSION = 1
_ROOT_MANAGEMENT_FILES = {
    "workspaces.yml",
    "settings.yml",
    LEGACY_REGISTRY_BACKUP_FILENAME,
    LAYOUT_MIGRATION_FILENAME,
}


def workspace_slug(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return slug[:48].rstrip("-") or "workspace"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _database_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]
    counts: dict[str, int] = {}
    for table in tables:
        quoted = table.replace('"', '""')
        counts[table] = int(
            connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
        )
    return counts


def _backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _verify_database(source: Path, destination: Path, files_dir: Path) -> None:
    source_connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(
        f"{destination.as_uri()}?mode=ro",
        uri=True,
    )
    try:
        if destination_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise OSError("Copied workspace database failed its integrity check")
        source_version = int(source_connection.execute("PRAGMA user_version").fetchone()[0])
        destination_version = int(
            destination_connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if (
            source_version != destination_version
            or not MIN_SUPPORTED_SCHEMA_VERSION
            <= destination_version
            <= SCHEMA_VERSION
        ):
            raise OSError("Copied workspace database schema does not match")
        if _database_counts(source_connection) != _database_counts(destination_connection):
            raise OSError("Copied workspace database row counts do not match")
        for stored_path, expected_hash, expected_size in destination_connection.execute(
            "SELECT stored_path, sha256, size FROM documents"
        ):
            managed = ManagedFileStore(files_dir).resolve(str(stored_path))
            if not managed.is_file():
                raise OSError(f"Managed workspace file is missing: {stored_path}")
            if managed.stat().st_size != int(expected_size):
                raise OSError(f"Managed workspace file size does not match: {stored_path}")
            if _hash_file(managed) != str(expected_hash):
                raise OSError(f"Managed workspace file hash does not match: {stored_path}")
    finally:
        destination_connection.close()
        source_connection.close()


def _copy_workspace_files(
    source: Path,
    destination: Path,
    *,
    excluded_top_level: set[str] | None = None,
) -> dict[Path, str]:
    hashes: dict[Path, str] = {}
    excluded = excluded_top_level or set()
    for current, directory_names, file_names in os.walk(source):
        current_path = Path(current)
        relative_directory = current_path.relative_to(source)
        if not relative_directory.parts:
            directory_names[:] = [
                name for name in directory_names if name not in excluded
            ]
        for directory_name in directory_names:
            candidate = current_path / directory_name
            if candidate.is_symlink():
                raise ValueError(
                    f"Workspace symbolic links are not supported: {candidate}"
                )
        for file_name in file_names:
            candidate = current_path / file_name
            if candidate.is_symlink():
                raise ValueError(
                    f"Workspace symbolic links are not supported: {candidate}"
                )
            relative = candidate.relative_to(source)
            if relative.parts[0] in excluded:
                continue
            if len(relative.parts) == 1 and relative.name in _CONTROL_FILES:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            hashes[relative] = _hash_file(candidate)
    return hashes


def _normalize_copied_config(source: Path, destination: Path) -> None:
    config_path = destination / "config.yml"
    if not config_path.is_file():
        return
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not migrate workspace config: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("Workspace config must be a mapping")
    configured_paths = loaded.get("paths")
    if configured_paths is None:
        return
    if not isinstance(configured_paths, dict):
        raise ValueError("Workspace config paths must be a mapping")
    normalized_paths: dict[str, str] = {}
    for key in ("input", "jobs", "reports", "profile"):
        value = configured_paths.get(key, key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Workspace config path {key} must be text")
        candidate = Path(value).expanduser()
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (source / candidate).resolve()
        )
        if not resolved.is_relative_to(source):
            raise ValueError(f"Workspace config path escapes its workspace: {key}")
        normalized_paths[key] = resolved.relative_to(source).as_posix()
    loaded["paths"] = {**configured_paths, **normalized_paths}
    config_path.write_text(
        yaml.safe_dump(loaded, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _verify_copied_files(
    destination: Path,
    hashes: dict[Path, str],
) -> None:
    for relative, expected_hash in hashes.items():
        if relative == Path("config.yml"):
            continue
        copied = destination / relative
        if not copied.is_file() or _hash_file(copied) != expected_hash:
            raise OSError(f"Copied workspace file hash does not match: {relative}")


def _paths_for_workspace(
    root: Path,
    folder_name_source: str,
    display_name: str = "",
) -> tuple[WorkspaceRecord, Path, Path]:
    workspace_id = uuid4().hex
    folder_name = f"{workspace_slug(folder_name_source)}--{workspace_id[:8]}"
    relative = Path(WORKSPACES_DIRECTORY) / folder_name
    record = WorkspaceRecord(workspace_id, display_name.strip() or folder_name, relative)
    final = (root / relative).resolve()
    staging = final.with_name(f".{final.name}.{uuid4().hex}.tmp")
    if not final.is_relative_to((root / WORKSPACES_DIRECTORY).resolve()):
        raise ValueError("Workspace destination escapes the data folder")
    return record, staging, final


def create_managed_workspace(
    root: Path,
    folder_name_source: str,
    personal: dict[str, str],
    display_name: str = "",
) -> tuple[WorkspaceRecord, Path]:
    record, staging, final = _paths_for_workspace(
        root.resolve(),
        folder_name_source,
        display_name,
    )
    if final.exists():
        raise FileExistsError(f"Workspace folder already exists: {final}")
    try:
        staging.mkdir(parents=True)
        connection = connect_database(staging / "jobtracker.sqlite3")
        connection.close()
        save_profile(
            staging / "profile",
            {"personal": personal, "skills": [], "experience": ""},
        )
        probe = sqlite3.connect(
            f"{(staging / 'jobtracker.sqlite3').as_uri()}?mode=ro",
            uri=True,
        )
        try:
            if probe.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise OSError("New workspace database failed its integrity check")
        finally:
            probe.close()
        os.replace(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return record, final


def import_managed_workspace(
    root: Path,
    source: Path,
    folder_name_source: str,
    display_name: str = "",
) -> tuple[WorkspaceRecord, Path]:
    canonical_source = validate_workspace_folder(source, "existing")
    record, staging, final = _paths_for_workspace(
        root.resolve(),
        folder_name_source,
        display_name,
    )
    if final.exists():
        raise FileExistsError(f"Workspace folder already exists: {final}")
    hashes: dict[Path, str] = {}
    try:
        staging.mkdir(parents=True)
        hashes = _copy_workspace_files(canonical_source, staging)
        _backup_database(
            canonical_source / "jobtracker.sqlite3",
            staging / "jobtracker.sqlite3",
        )
        _normalize_copied_config(canonical_source, staging)
        _verify_copied_files(staging, hashes)
        _verify_database(
            canonical_source / "jobtracker.sqlite3",
            staging / "jobtracker.sqlite3",
            staging / "files",
        )
        os.replace(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return record, final


def _atomic_write_yaml(path: Path, payload: dict[str, object]) -> None:
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
            yaml.safe_dump(payload, temporary, allow_unicode=True, sort_keys=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def layout_migration_file(root: Path) -> Path:
    return root.resolve() / LAYOUT_MIGRATION_FILENAME


def _load_layout_migration(root: Path) -> dict[str, object] | None:
    journal_path = layout_migration_file(root)
    if not journal_path.is_file():
        return None
    try:
        loaded = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read layout migration journal: {exc}") from exc
    if not isinstance(loaded, dict) or loaded.get("version") != LAYOUT_MIGRATION_VERSION:
        raise ValueError("Layout migration journal is invalid")
    return loaded


def layout_migration_phase(root: Path) -> str:
    journal = _load_layout_migration(root)
    return str(journal.get("phase", "")) if journal is not None else ""


def _journal_record(root: Path, journal: dict[str, object]) -> tuple[WorkspaceRecord, Path]:
    raw_record = journal.get("workspace")
    if not isinstance(raw_record, dict):
        raise ValueError("Layout migration workspace record is missing")
    workspace_id = raw_record.get("id")
    name = raw_record.get("name")
    folder = raw_record.get("folder")
    if not all(isinstance(value, str) and value for value in (workspace_id, name, folder)):
        raise ValueError("Layout migration workspace record is invalid")
    record = WorkspaceRecord(str(workspace_id), str(name), Path(str(folder)))
    workspace = (root.resolve() / record.folder).resolve()
    if workspace.parent != (root.resolve() / WORKSPACES_DIRECTORY).resolve():
        raise ValueError("Layout migration workspace escapes the data folder")
    return record, workspace


def _source_entries(root: Path) -> list[str]:
    excluded = _GLOBAL_DIRECTORIES | _ROOT_MANAGEMENT_FILES
    return sorted(
        item.name
        for item in root.iterdir()
        if item.name not in excluded
    )


def prepare_same_folder_adoption(
    root: Path,
    folder_name_source: str,
    display_name: str = "",
) -> tuple[WorkspaceRecord, Path]:
    canonical_root = validate_workspace_folder(root, "existing")
    journal_path = layout_migration_file(canonical_root)
    existing = _load_layout_migration(canonical_root)
    if existing is not None:
        phase = str(existing.get("phase", ""))
        record, workspace = _journal_record(canonical_root, existing)
        if phase in {"prepared", "activated"} and workspace.is_dir():
            return record, workspace
        if phase == "copying":
            shutil.rmtree(workspace, ignore_errors=True)
            staging_value = existing.get("staging")
            if isinstance(staging_value, str):
                staging = (canonical_root / staging_value).resolve()
                collection = (canonical_root / WORKSPACES_DIRECTORY).resolve()
                if staging.parent == collection:
                    shutil.rmtree(staging, ignore_errors=True)
            journal_path.unlink(missing_ok=True)
        else:
            raise ValueError(f"Unsupported layout migration phase: {phase}")

    record, staging, final = _paths_for_workspace(
        canonical_root,
        folder_name_source,
        display_name,
    )
    snapshot = (
        Path("backups")
        / f"layout-migration-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    journal: dict[str, object] = {
        "version": LAYOUT_MIGRATION_VERSION,
        "phase": "copying",
        "workspace": {
            "id": record.id,
            "name": record.name,
            "folder": record.folder.as_posix(),
        },
        "staging": staging.relative_to(canonical_root).as_posix(),
        "snapshot": snapshot.as_posix(),
        "remaining": _source_entries(canonical_root),
    }
    _atomic_write_yaml(journal_path, journal)
    try:
        staging.mkdir(parents=True)
        hashes = _copy_workspace_files(
            canonical_root,
            staging,
            excluded_top_level=_GLOBAL_DIRECTORIES,
        )
        _backup_database(
            canonical_root / "jobtracker.sqlite3",
            staging / "jobtracker.sqlite3",
        )
        _normalize_copied_config(canonical_root, staging)
        _verify_copied_files(staging, hashes)
        _verify_database(
            canonical_root / "jobtracker.sqlite3",
            staging / "jobtracker.sqlite3",
            staging / "files",
        )
        os.replace(staging, final)
        journal["phase"] = "prepared"
        journal["staging"] = ""
        _atomic_write_yaml(journal_path, journal)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(final, ignore_errors=True)
        journal_path.unlink(missing_ok=True)
        raise
    return record, final


def preserve_legacy_registry(root: Path) -> Path | None:
    canonical_root = root.resolve()
    source = canonical_root / "workspaces.yml"
    destination = canonical_root / LEGACY_REGISTRY_BACKUP_FILENAME
    if not source.is_file():
        return None
    if not destination.exists():
        _atomic_copy(source, destination)
    return destination


def restore_legacy_registry(root: Path) -> None:
    canonical_root = root.resolve()
    backup = canonical_root / LEGACY_REGISTRY_BACKUP_FILENAME
    if backup.is_file():
        _atomic_copy(backup, canonical_root / "workspaces.yml")


def mark_layout_migration_activated(root: Path) -> None:
    journal = _load_layout_migration(root)
    if journal is None:
        return
    phase = str(journal.get("phase", ""))
    if phase == "activated":
        return
    if phase != "prepared":
        raise ValueError(f"Cannot activate layout migration in phase: {phase}")
    journal["phase"] = "activated"
    _atomic_write_yaml(layout_migration_file(root), journal)


def finalize_layout_migration(root: Path) -> Path | None:
    canonical_root = root.resolve()
    journal = _load_layout_migration(canonical_root)
    if journal is None:
        return None
    if str(journal.get("phase", "")) != "activated":
        return None
    snapshot_value = journal.get("snapshot")
    remaining_value = journal.get("remaining", [])
    if not isinstance(snapshot_value, str) or not snapshot_value:
        raise ValueError("Layout migration recovery snapshot is missing")
    if not isinstance(remaining_value, list) or not all(
        isinstance(item, str) and item for item in remaining_value
    ):
        raise ValueError("Layout migration remaining entries are invalid")
    snapshot = (canonical_root / snapshot_value).resolve()
    backups_root = (canonical_root / "backups").resolve()
    if not snapshot.is_relative_to(backups_root):
        raise ValueError("Layout migration snapshot escapes the backups folder")
    snapshot.mkdir(parents=True, exist_ok=True)

    remaining = list(remaining_value)
    for name in list(remaining):
        if Path(name).name != name or name in _GLOBAL_DIRECTORIES | _ROOT_MANAGEMENT_FILES:
            raise ValueError(f"Unsafe layout migration entry: {name}")
        source = canonical_root / name
        destination = snapshot / name
        if source.exists():
            if destination.exists():
                raise FileExistsError(f"Recovery snapshot entry already exists: {destination}")
            os.replace(source, destination)
        elif not destination.exists():
            raise FileNotFoundError(f"Layout migration source is missing: {source}")
        remaining.remove(name)
        journal["remaining"] = remaining
        _atomic_write_yaml(layout_migration_file(canonical_root), journal)

    journal["phase"] = "complete"
    _atomic_write_yaml(layout_migration_file(canonical_root), journal)
    os.replace(
        layout_migration_file(canonical_root),
        snapshot / "layout-migration.yml",
    )
    return snapshot


def discard_managed_workspace(root: Path, path: Path) -> None:
    canonical_root = (root / WORKSPACES_DIRECTORY).resolve()
    canonical_path = path.resolve()
    if canonical_path.parent != canonical_root:
        raise ValueError("Refusing to discard a path outside the workspace collection")
    shutil.rmtree(canonical_path, ignore_errors=True)
