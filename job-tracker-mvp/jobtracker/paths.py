from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Literal
from uuid import UUID

import yaml


APP_NAME = "jobtracker"
DATA_DIR_ENV = "JOBTRACKER_DATA_DIR"
LEGACY_JOBS_DIR_ENV = "JOBTRACKER_LEGACY_JOBS_DIR"
WORKSPACE_REGISTRY_VERSION = 1
WORKSPACE_REGISTRY_FILENAME = "workspaces.yml"
WORKSPACE_SETTINGS_VERSION = 1
WORKSPACE_SETTINGS_FILENAME = "settings.yml"
WORKSPACE_CATALOG_VERSION = 2
SUPPORTED_WORKSPACE_CATALOG_VERSIONS = frozenset({1, WORKSPACE_CATALOG_VERSION})
WORKSPACES_DIRECTORY = "workspaces"


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def default_data_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return _absolute(root / APP_NAME)


@dataclass(frozen=True)
class WorkspaceRegistry:
    version: int = WORKSPACE_REGISTRY_VERSION
    active: Path | None = None
    workspaces: tuple[Path, ...] = ()


@dataclass(frozen=True)
class WorkspaceResolution:
    status: Literal["explicit", "environment", "active", "setup", "migrate", "recovery"]
    data_dir: Path | None
    registry: WorkspaceRegistry | None = None
    error: str | None = None
    workspaces_root: Path | None = None
    settings: WorkspaceSettings | None = None
    catalog: WorkspaceCatalog | None = None
    legacy_source: Path | None = None


class WorkspaceRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class WorkspaceSettings:
    version: int = WORKSPACE_SETTINGS_VERSION
    workspaces_root: Path | None = None


@dataclass(frozen=True)
class WorkspaceRecord:
    id: str
    name: str
    folder: Path


@dataclass(frozen=True)
class WorkspaceCatalog:
    version: int = WORKSPACE_CATALOG_VERSION
    active_id: str | None = None
    workspaces: tuple[WorkspaceRecord, ...] = ()


class WorkspaceSettingsError(ValueError):
    pass


class WorkspaceCatalogError(ValueError):
    pass


def workspace_registry_file() -> Path:
    return default_data_dir() / WORKSPACE_REGISTRY_FILENAME


def workspace_settings_file() -> Path:
    return default_data_dir() / WORKSPACE_SETTINGS_FILENAME


def workspace_catalog_file(root: str | Path) -> Path:
    return _absolute(root) / WORKSPACE_REGISTRY_FILENAME


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


def _path_key(path: Path) -> str:
    value = os.path.normcase(str(path))
    return value.casefold() if sys.platform == "win32" else value


def _canonical_workspaces(paths: tuple[Path, ...] | list[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        canonical = _absolute(path)
        key = _path_key(canonical)
        if key not in seen:
            seen.add(key)
            result.append(canonical)
    return tuple(result)


def _normalize_registry(registry: WorkspaceRegistry) -> WorkspaceRegistry:
    if registry.version != WORKSPACE_REGISTRY_VERSION:
        raise WorkspaceRegistryError(f"Unsupported workspace registry version: {registry.version}")
    active = _absolute(registry.active) if registry.active is not None else None
    workspaces = _canonical_workspaces(list(registry.workspaces))
    if active is not None and _path_key(active) not in {_path_key(path) for path in workspaces}:
        workspaces = (*workspaces, active)
    return WorkspaceRegistry(active=active, workspaces=workspaces)


def load_workspace_registry(path: str | Path | None = None) -> WorkspaceRegistry | None:
    registry_path = _absolute(path or workspace_registry_file())
    if not registry_path.exists():
        return None
    try:
        loaded = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise WorkspaceRegistryError(f"Could not read workspace registry: {exc}") from exc
    if not isinstance(loaded, dict):
        raise WorkspaceRegistryError("Workspace registry must be a mapping")
    version = loaded.get("version")
    active = loaded.get("active")
    workspaces = loaded.get("workspaces", [])
    if version != WORKSPACE_REGISTRY_VERSION:
        raise WorkspaceRegistryError(f"Unsupported workspace registry version: {version}")
    if active is not None and (not isinstance(active, str) or not active.strip()):
        raise WorkspaceRegistryError("Workspace registry active path must be text or null")
    if not isinstance(workspaces, list) or not all(
        isinstance(item, str) and item.strip() for item in workspaces
    ):
        raise WorkspaceRegistryError("Workspace registry workspaces must be a list of paths")
    return _normalize_registry(
        WorkspaceRegistry(
            active=_absolute(active) if active else None,
            workspaces=tuple(_absolute(item) for item in workspaces),
        )
    )


def save_workspace_registry(
    registry: WorkspaceRegistry,
    path: str | Path | None = None,
) -> WorkspaceRegistry:
    normalized = _normalize_registry(registry)
    registry_path = _absolute(path or workspace_registry_file())
    payload = {
        "version": normalized.version,
        "active": str(normalized.active) if normalized.active is not None else None,
        "workspaces": [str(item) for item in normalized.workspaces],
    }
    _atomic_write_yaml(registry_path, payload)
    return normalized


def load_workspace_settings(path: str | Path | None = None) -> WorkspaceSettings | None:
    settings_path = _absolute(path or workspace_settings_file())
    if not settings_path.exists():
        return None
    try:
        loaded = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise WorkspaceSettingsError(f"Could not read workspace settings: {exc}") from exc
    if not isinstance(loaded, dict):
        raise WorkspaceSettingsError("Workspace settings must be a mapping")
    if loaded.get("version") != WORKSPACE_SETTINGS_VERSION:
        raise WorkspaceSettingsError(
            f"Unsupported workspace settings version: {loaded.get('version')}"
        )
    root = loaded.get("workspaces_root")
    if not isinstance(root, str) or not root.strip():
        raise WorkspaceSettingsError("Workspace settings root must be a path")
    return WorkspaceSettings(workspaces_root=_absolute(root))


def save_workspace_settings(
    settings: WorkspaceSettings,
    path: str | Path | None = None,
) -> WorkspaceSettings:
    if settings.version != WORKSPACE_SETTINGS_VERSION:
        raise WorkspaceSettingsError(
            f"Unsupported workspace settings version: {settings.version}"
        )
    if settings.workspaces_root is None:
        raise WorkspaceSettingsError("Workspace settings root is required")
    normalized = WorkspaceSettings(workspaces_root=_absolute(settings.workspaces_root))
    _atomic_write_yaml(
        _absolute(path or workspace_settings_file()),
        {
            "version": normalized.version,
            "workspaces_root": str(normalized.workspaces_root),
        },
    )
    return normalized


def _validate_workspace_id(value: object) -> str:
    if not isinstance(value, str):
        raise WorkspaceCatalogError("Workspace ID must be text")
    try:
        return UUID(value).hex
    except (ValueError, AttributeError) as exc:
        raise WorkspaceCatalogError(f"Invalid workspace ID: {value}") from exc


def _normalize_workspace_record(record: WorkspaceRecord, root: Path) -> WorkspaceRecord:
    workspace_id = _validate_workspace_id(record.id)
    name = record.name.strip()
    if not name:
        raise WorkspaceCatalogError("Workspace name is required")
    folder = Path(record.folder)
    if folder.is_absolute() or folder.drive or not folder.parts:
        raise WorkspaceCatalogError("Workspace folder must be relative to the data folder")
    if folder.parts[0] != WORKSPACES_DIRECTORY or any(
        part in {"", ".", ".."} for part in folder.parts
    ):
        raise WorkspaceCatalogError("Workspace folder must stay under workspaces/")
    resolved = (root / folder).resolve()
    if not resolved.is_relative_to((root / WORKSPACES_DIRECTORY).resolve()):
        raise WorkspaceCatalogError("Workspace folder escapes the data folder")
    return WorkspaceRecord(workspace_id, name, Path(*folder.parts))


def _normalize_workspace_catalog(
    catalog: WorkspaceCatalog,
    root: Path,
) -> WorkspaceCatalog:
    if catalog.version not in SUPPORTED_WORKSPACE_CATALOG_VERSIONS:
        raise WorkspaceCatalogError(
            f"Unsupported workspace catalog version: {catalog.version}"
        )
    records = tuple(
        _normalize_workspace_record(record, root) for record in catalog.workspaces
    )
    ids = [record.id for record in records]
    folders = [record.folder.as_posix().casefold() for record in records]
    if len(ids) != len(set(ids)):
        raise WorkspaceCatalogError("Workspace IDs must be unique")
    if len(folders) != len(set(folders)):
        raise WorkspaceCatalogError("Workspace folders must be unique")
    active_id = (
        _validate_workspace_id(catalog.active_id)
        if catalog.active_id is not None
        else None
    )
    if active_id is not None and active_id not in set(ids):
        raise WorkspaceCatalogError("Active workspace is not in the catalog")
    return WorkspaceCatalog(
        version=WORKSPACE_CATALOG_VERSION,
        active_id=active_id,
        workspaces=records,
    )


def load_workspace_catalog(root: str | Path) -> WorkspaceCatalog | None:
    canonical_root = _absolute(root)
    catalog_path = workspace_catalog_file(canonical_root)
    if not catalog_path.exists():
        return None
    try:
        loaded = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise WorkspaceCatalogError(f"Could not read workspace catalog: {exc}") from exc
    if not isinstance(loaded, dict):
        raise WorkspaceCatalogError("Workspace catalog must be a mapping")
    version = loaded.get("version")
    if version not in SUPPORTED_WORKSPACE_CATALOG_VERSIONS:
        raise WorkspaceCatalogError(
            f"Unsupported workspace catalog version: {version}"
        )
    if "active" in loaded and "active_workspace" not in loaded:
        raise WorkspaceCatalogError(
            "Legacy workspace registry is not a managed workspace catalog"
        )
    raw_active = loaded.get("active_workspace")
    if raw_active is not None and not isinstance(raw_active, str):
        raise WorkspaceCatalogError("Active workspace ID must be text or null")
    raw_records = loaded.get("workspaces", [])
    if not isinstance(raw_records, list):
        raise WorkspaceCatalogError("Workspaces must be a list")
    records: list[WorkspaceRecord] = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise WorkspaceCatalogError("Each workspace must be a mapping")
        if not isinstance(raw.get("name"), str) or not isinstance(raw.get("folder"), str):
            raise WorkspaceCatalogError("Workspace name and folder must be text")
        records.append(
            WorkspaceRecord(
                id=raw.get("id"),
                name=raw["name"],
                folder=Path(raw["folder"]),
            )
        )
    return _normalize_workspace_catalog(
        WorkspaceCatalog(
            version=version,
            active_id=raw_active,
            workspaces=tuple(records),
        ),
        canonical_root,
    )


def save_workspace_catalog(
    root: str | Path,
    catalog: WorkspaceCatalog,
) -> WorkspaceCatalog:
    canonical_root = _absolute(root)
    normalized = _normalize_workspace_catalog(catalog, canonical_root)
    _atomic_write_yaml(
        workspace_catalog_file(canonical_root),
        {
            "version": normalized.version,
            "active_workspace": normalized.active_id,
            "workspaces": [
                {
                    "id": record.id,
                    "name": record.name,
                    "folder": record.folder.as_posix(),
                }
                for record in normalized.workspaces
            ],
        },
    )
    return normalized


def workspace_path(root: str | Path, record: WorkspaceRecord) -> Path:
    canonical_root = _absolute(root)
    normalized = _normalize_workspace_record(record, canonical_root)
    return (canonical_root / normalized.folder).resolve()


def _resolve_managed_root(
    root: Path,
    *,
    settings: WorkspaceSettings | None,
) -> WorkspaceResolution:
    if not root.is_dir():
        return WorkspaceResolution(
            "recovery",
            None,
            error=f"Job Tracker data folder is unavailable: {root}",
            workspaces_root=root,
            settings=settings,
        )
    try:
        catalog = load_workspace_catalog(root)
    except WorkspaceCatalogError as exc:
        return WorkspaceResolution(
            "recovery",
            None,
            error=str(exc),
            workspaces_root=root,
            settings=settings,
        )
    if catalog is None:
        return WorkspaceResolution(
            "recovery",
            None,
            error=f"Workspace catalog is missing: {workspace_catalog_file(root)}",
            workspaces_root=root,
            settings=settings,
        )
    if catalog.active_id is None:
        return WorkspaceResolution(
            "setup",
            None,
            workspaces_root=root,
            settings=settings,
            catalog=catalog,
        )
    active = next(
        record for record in catalog.workspaces if record.id == catalog.active_id
    )
    active_path = workspace_path(root, active)
    if not active_path.is_dir():
        return WorkspaceResolution(
            "recovery",
            active_path,
            error=f"Active workspace folder is unavailable: {active_path}",
            workspaces_root=root,
            settings=settings,
            catalog=catalog,
        )
    if not (active_path / "jobtracker.sqlite3").is_file():
        return WorkspaceResolution(
            "recovery",
            active_path,
            error=(
                "Active workspace database is missing: "
                f"{active_path / 'jobtracker.sqlite3'}"
            ),
            workspaces_root=root,
            settings=settings,
            catalog=catalog,
        )
    return WorkspaceResolution(
        "active",
        active_path,
        workspaces_root=root,
        settings=settings,
        catalog=catalog,
    )


def _looks_like_managed_catalog(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return False
    return (
        isinstance(loaded, dict)
        and "active_workspace" in loaded
        and isinstance(loaded.get("workspaces", []), list)
    )


def resolve_desktop_workspace(
    explicit_data_dir: str | Path | None = None,
) -> WorkspaceResolution:
    if explicit_data_dir is not None:
        return WorkspaceResolution("explicit", _absolute(explicit_data_dir))
    environment_data_dir = os.environ.get(DATA_DIR_ENV)
    if environment_data_dir:
        return WorkspaceResolution("environment", _absolute(environment_data_dir))

    try:
        settings = load_workspace_settings()
    except WorkspaceSettingsError as exc:
        return WorkspaceResolution("recovery", None, error=str(exc))

    if settings is not None:
        root = settings.workspaces_root
        assert root is not None
        return _resolve_managed_root(root, settings=settings)

    default_root = default_data_dir()
    if _looks_like_managed_catalog(workspace_catalog_file(default_root)):
        return _resolve_managed_root(default_root, settings=None)

    try:
        legacy = load_workspace_registry()
    except WorkspaceRegistryError as exc:
        return WorkspaceResolution("recovery", None, error=str(exc))
    legacy_source = legacy.active if legacy is not None else None
    if legacy_source is not None:
        if not legacy_source.is_dir():
            return WorkspaceResolution(
                "recovery",
                legacy_source,
                registry=legacy,
                error=f"Legacy workspace folder is unavailable: {legacy_source}",
                legacy_source=legacy_source,
            )
        if not (legacy_source / "jobtracker.sqlite3").is_file():
            return WorkspaceResolution(
                "recovery",
                legacy_source,
                registry=legacy,
                error=(
                    "Legacy workspace database is missing: "
                    f"{legacy_source / 'jobtracker.sqlite3'}"
                ),
                legacy_source=legacy_source,
            )
    elif (default_root / "jobtracker.sqlite3").is_file():
        legacy_source = default_root
    if legacy_source is not None:
        return WorkspaceResolution(
            "migrate",
            None,
            registry=legacy,
            legacy_source=legacy_source,
        )
    return WorkspaceResolution("setup", None, registry=legacy)


def _is_unc(path: Path) -> bool:
    return str(path).startswith(("\\\\", "//"))


def _is_remote_drive(path: Path) -> bool:
    if sys.platform != "win32" or not path.anchor:
        return False
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(path.anchor) == 4
    except (AttributeError, OSError):
        return False


def _is_known_onedrive_path(path: Path) -> bool:
    candidate = _path_key(path).rstrip("\\/")
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(name)
        if not value:
            continue
        root = _path_key(_absolute(value)).rstrip("\\/")
        if candidate == root or candidate.startswith(root + os.sep):
            return True
    return False


def validate_workspaces_root(
    path: str | Path,
    mode: Literal["new", "existing"],
) -> Path:
    expanded = Path(path).expanduser()
    if _is_unc(expanded):
        raise ValueError("Network data folders are not supported")
    root = _absolute(expanded)
    if _is_remote_drive(root):
        raise ValueError("Mapped network data folders are not supported")
    if _is_known_onedrive_path(root):
        raise ValueError("OneDrive data folders are not supported")
    if not root.is_dir():
        raise ValueError(f"Job Tracker data folder does not exist: {root}")
    if mode == "new":
        if any(root.iterdir()):
            raise ValueError("New Job Tracker data folder must be empty")
    elif mode == "existing":
        if not workspace_catalog_file(root).is_file():
            raise ValueError("Workspace catalog is missing")
        load_workspace_catalog(root)
    else:
        raise ValueError(f"Unknown data folder mode: {mode}")
    return root


def _is_registry_control_file(path: Path, folder: Path) -> bool:
    if _path_key(folder) != _path_key(default_data_dir()):
        return False
    name = path.name
    return name == WORKSPACE_REGISTRY_FILENAME or (
        name.startswith(f".{WORKSPACE_REGISTRY_FILENAME}.") and name.endswith(".tmp")
    )


def validate_workspace_folder(
    path: str | Path,
    mode: Literal["new", "existing"],
) -> Path:
    expanded = Path(path).expanduser()
    if _is_unc(expanded):
        raise ValueError("Network workspace folders are not supported")
    folder = _absolute(expanded)
    if _is_remote_drive(folder):
        raise ValueError("Mapped network workspace folders are not supported")
    if _is_known_onedrive_path(folder):
        raise ValueError("OneDrive workspace folders are not supported")
    if not folder.is_dir():
        raise ValueError(f"Workspace folder does not exist: {folder}")

    if mode == "new":
        unrelated = [item for item in folder.iterdir() if not _is_registry_control_file(item, folder)]
        if unrelated:
            raise ValueError("New workspace folder must be empty")
    elif mode == "existing":
        if not (folder / "jobtracker.sqlite3").is_file():
            raise ValueError("Existing workspace database is missing")
    else:
        raise ValueError(f"Unknown workspace folder mode: {mode}")
    return folder


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    database: Path
    files_dir: Path
    backups_dir: Path
    logs_dir: Path
    legacy_jobs_dir: Path | None

    @classmethod
    def resolve(
        cls,
        data_dir: str | Path | None = None,
        legacy_jobs_dir: str | Path | None = None,
    ) -> AppPaths:
        root = _absolute(data_dir or os.environ.get(DATA_DIR_ENV) or default_data_dir())
        legacy = legacy_jobs_dir or os.environ.get(LEGACY_JOBS_DIR_ENV)
        return cls(
            data_dir=root,
            database=root / "jobtracker.sqlite3",
            files_dir=root / "files",
            backups_dir=root / "backups",
            logs_dir=root / "logs",
            legacy_jobs_dir=_absolute(legacy) if legacy else None,
        )

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.yml"

    @property
    def input_dir(self) -> Path:
        return self.data_dir / "input"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def profile_dir(self) -> Path:
        return self.data_dir / "profile"

    def ensure_runtime_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
