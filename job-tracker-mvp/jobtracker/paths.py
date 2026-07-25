from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Literal

import yaml


APP_NAME = "jobtracker"
DATA_DIR_ENV = "JOBTRACKER_DATA_DIR"
LEGACY_JOBS_DIR_ENV = "JOBTRACKER_LEGACY_JOBS_DIR"
WORKSPACE_REGISTRY_VERSION = 1
WORKSPACE_REGISTRY_FILENAME = "workspaces.yml"


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
    status: Literal["explicit", "environment", "active", "adopt_default", "setup", "recovery"]
    data_dir: Path | None
    registry: WorkspaceRegistry | None = None
    error: str | None = None


class WorkspaceRegistryError(ValueError):
    pass


def workspace_registry_file() -> Path:
    return default_data_dir() / WORKSPACE_REGISTRY_FILENAME


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
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": normalized.version,
        "active": str(normalized.active) if normalized.active is not None else None,
        "workspaces": [str(item) for item in normalized.workspaces],
    }
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=registry_path.parent,
            prefix=f".{registry_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            yaml.safe_dump(payload, temporary, allow_unicode=True, sort_keys=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, registry_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return normalized


def resolve_desktop_workspace(
    explicit_data_dir: str | Path | None = None,
) -> WorkspaceResolution:
    if explicit_data_dir is not None:
        return WorkspaceResolution("explicit", _absolute(explicit_data_dir))
    environment_data_dir = os.environ.get(DATA_DIR_ENV)
    if environment_data_dir:
        return WorkspaceResolution("environment", _absolute(environment_data_dir))

    try:
        registry = load_workspace_registry()
    except WorkspaceRegistryError as exc:
        return WorkspaceResolution("recovery", None, error=str(exc))

    if registry is not None and registry.active is not None:
        active = registry.active
        if not active.is_dir():
            return WorkspaceResolution(
                "recovery",
                active,
                registry,
                f"Active workspace folder is unavailable: {active}",
            )
        if not (active / "jobtracker.sqlite3").is_file():
            return WorkspaceResolution(
                "recovery",
                active,
                registry,
                f"Active workspace database is missing: {active / 'jobtracker.sqlite3'}",
            )
        return WorkspaceResolution("active", active, registry)

    if registry is not None:
        return WorkspaceResolution("setup", None, registry)

    default = default_data_dir()
    if (default / "jobtracker.sqlite3").is_file():
        adopted = WorkspaceRegistry(active=default, workspaces=(default,))
        return WorkspaceResolution("adopt_default", default, adopted)
    return WorkspaceResolution("setup", None)


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
        for path in (
            self.data_dir,
            self.files_dir,
            self.backups_dir,
            self.logs_dir,
            self.input_dir,
            self.jobs_dir,
            self.reports_dir,
            self.profile_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
