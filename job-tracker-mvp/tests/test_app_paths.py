from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import jobtracker
from jobtracker.config import load_config
from jobtracker.paths import (
    AppPaths,
    WorkspaceCatalog,
    WorkspaceCatalogError,
    WorkspaceRecord,
    WorkspaceRegistry,
    WorkspaceRegistryError,
    WorkspaceSettings,
    load_workspace_registry,
    load_workspace_catalog,
    load_workspace_settings,
    resolve_desktop_workspace,
    save_workspace_catalog,
    save_workspace_registry,
    save_workspace_settings,
    validate_workspace_folder,
    validate_workspaces_root,
    workspace_path,
)

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


class AppPathsTests(unittest.TestCase):
    def test_default_paths_do_not_depend_on_working_directory(self) -> None:
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            original_cwd = Path.cwd()
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("JOBTRACKER_DATA_DIR", None)
                paths_before = AppPaths.resolve()
                try:
                    os.chdir(second)
                    paths_after = AppPaths.resolve()
                finally:
                    os.chdir(original_cwd)

            self.assertEqual(paths_before, paths_after)
            self.assertTrue(paths_before.data_dir.is_absolute())

    def test_explicit_overrides_take_precedence_over_environment(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(
                os.environ,
                {
                    "JOBTRACKER_DATA_DIR": str(root / "environment"),
                    "JOBTRACKER_LEGACY_JOBS_DIR": str(root / "environment-legacy"),
                },
            ):
                paths = AppPaths.resolve(root / "explicit", root / "explicit-legacy")

            self.assertEqual(paths.data_dir, (root / "explicit").resolve())
            self.assertEqual(paths.legacy_jobs_dir, (root / "explicit-legacy").resolve())

    def test_environment_overrides_defaults(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(
                os.environ,
                {
                    "JOBTRACKER_DATA_DIR": str(root / "data"),
                    "JOBTRACKER_LEGACY_JOBS_DIR": str(root / "legacy"),
                },
            ):
                paths = AppPaths.resolve()

            self.assertEqual(paths.data_dir, (root / "data").resolve())
            self.assertEqual(paths.legacy_jobs_dir, (root / "legacy").resolve())

    def test_runtime_initialization_does_not_create_empty_legacy_directories(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = AppPaths.resolve(root / "data", root / "missing-legacy")
            paths.ensure_runtime_dirs()

            self.assertTrue(paths.data_dir.is_dir())
            for directory in (
                paths.files_dir,
                paths.backups_dir,
                paths.logs_dir,
                paths.input_dir,
                paths.jobs_dir,
                paths.reports_dir,
                paths.profile_dir,
            ):
                self.assertFalse(directory.exists())
            self.assertFalse(paths.database.exists())
            self.assertFalse(paths.legacy_jobs_dir.exists())

    def test_default_config_uses_app_data_directory(self) -> None:
        with TemporaryDirectory() as temp:
            paths = AppPaths.resolve(Path(temp) / "data")
            config = load_config(app_paths=paths)

            self.assertEqual(Path(config["paths"]["jobs"]), paths.jobs_dir)
            self.assertEqual(Path(config["paths"]["reports"]), paths.reports_dir)

    def test_explicit_config_path_is_preserved_and_relative_values_are_anchored(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "custom.yml"
            config_path.write_text("paths:\n  jobs: legacy-jobs\n", encoding="utf-8")

            config = load_config(config_path, app_paths=AppPaths.resolve(root / "runtime"))

            self.assertEqual(Path(config["paths"]["jobs"]), (root / "legacy-jobs").resolve())
            self.assertEqual(Path(config["paths"]["reports"]), root / "reports")

    def test_pyproject_is_authoritative_version_source(self) -> None:
        with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as project_file:
            declared_version = tomllib.load(project_file)["project"]["version"]

        self.assertEqual(jobtracker.__version__, declared_version)


class WorkspaceRegistryTests(unittest.TestCase):
    def test_registry_round_trip_canonicalizes_and_deduplicates_paths(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "state" / "workspaces.yml"
            workspace = root / "Workspace"
            workspace.mkdir()

            saved = save_workspace_registry(
                WorkspaceRegistry(
                    active=workspace / ".." / "Workspace",
                    workspaces=(workspace, Path(str(workspace).upper())),
                ),
                registry_path,
            )

            self.assertEqual(saved.active, workspace.resolve())
            self.assertEqual(saved.workspaces, (workspace.resolve(),))
            self.assertEqual(load_workspace_registry(registry_path), saved)
            self.assertEqual(list(registry_path.parent.glob("*.tmp")), [])

    def test_invalid_registry_raises_actionable_error(self) -> None:
        with TemporaryDirectory() as temp:
            registry_path = Path(temp) / "workspaces.yml"
            registry_path.write_text("version: 99\nactive: 42\n", encoding="utf-8")

            with self.assertRaisesRegex(WorkspaceRegistryError, "version"):
                load_workspace_registry(registry_path)

            registry_path.write_text("version: 1\nactive: ''\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkspaceRegistryError, "active"):
                load_workspace_registry(registry_path)

    def test_atomic_write_preserves_existing_registry_when_replace_fails(self) -> None:
        with TemporaryDirectory() as temp:
            registry_path = Path(temp) / "workspaces.yml"
            registry_path.write_text("old content\n", encoding="utf-8")

            with patch("jobtracker.paths.os.replace", side_effect=OSError("injected")):
                with self.assertRaisesRegex(OSError, "injected"):
                    save_workspace_registry(WorkspaceRegistry(), registry_path)

            self.assertEqual(registry_path.read_text(encoding="utf-8"), "old content\n")
            self.assertEqual(list(registry_path.parent.glob("*.tmp")), [])

    def test_resolution_honors_explicit_then_environment_override(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {"JOBTRACKER_DATA_DIR": str(root / "environment")}):
                explicit = resolve_desktop_workspace(root / "explicit")
                environment = resolve_desktop_workspace()

            self.assertEqual((explicit.status, explicit.data_dir), ("explicit", (root / "explicit").resolve()))
            self.assertEqual(
                (environment.status, environment.data_dir),
                ("environment", (root / "environment").resolve()),
            )

    def test_resolution_uses_managed_active_workspace(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data_root = root / "data"
            workspace = data_root / "workspaces" / "person--12345678"
            workspace.mkdir(parents=True)
            (workspace / "jobtracker.sqlite3").touch()
            record = WorkspaceRecord(
                "12345678123456781234567812345678",
                "Person",
                Path("workspaces/person--12345678"),
            )
            save_workspace_catalog(
                data_root,
                WorkspaceCatalog(active_id=record.id, workspaces=(record,)),
            )
            settings_path = root / "state" / "settings.yml"
            save_workspace_settings(
                WorkspaceSettings(workspaces_root=data_root),
                settings_path,
            )

            with (
                patch("jobtracker.paths.workspace_settings_file", return_value=settings_path),
                patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("JOBTRACKER_DATA_DIR", None)
                resolution = resolve_desktop_workspace()

            self.assertEqual(resolution.status, "active")
            self.assertEqual(resolution.data_dir, workspace.resolve())

    def test_missing_managed_active_workspace_requires_recovery_without_fallback(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data_root = root / "data"
            data_root.mkdir()
            record = WorkspaceRecord(
                "12345678123456781234567812345678",
                "Missing",
                Path("workspaces/missing--12345678"),
            )
            save_workspace_catalog(
                data_root,
                WorkspaceCatalog(active_id=record.id, workspaces=(record,)),
            )
            settings_path = root / "state" / "settings.yml"
            save_workspace_settings(
                WorkspaceSettings(workspaces_root=data_root),
                settings_path,
            )

            with (
                patch("jobtracker.paths.workspace_settings_file", return_value=settings_path),
                patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("JOBTRACKER_DATA_DIR", None)
                resolution = resolve_desktop_workspace()

            self.assertEqual(resolution.status, "recovery")
            self.assertEqual(resolution.data_dir, (data_root / record.folder).resolve())

    def test_corrupt_registry_requires_recovery(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "workspaces.yml"
            registry_path.write_text("[not a registry]\n", encoding="utf-8")

            with (
                patch("jobtracker.paths.workspace_registry_file", return_value=registry_path),
                patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("JOBTRACKER_DATA_DIR", None)
                resolution = resolve_desktop_workspace()

            self.assertEqual(resolution.status, "recovery")
            self.assertIsNone(resolution.data_dir)

    def test_resolution_requests_setup_or_legacy_migration(self) -> None:
        with TemporaryDirectory() as temp:
            default = Path(temp) / "default"
            default.mkdir()
            registry_path = Path(temp) / "state" / "workspaces.yml"
            with (
                patch("jobtracker.paths.workspace_registry_file", return_value=registry_path),
                patch("jobtracker.paths.default_data_dir", return_value=default),
                patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("JOBTRACKER_DATA_DIR", None)
                setup = resolve_desktop_workspace()
                (default / "jobtracker.sqlite3").touch()
                adopted = resolve_desktop_workspace()

            self.assertEqual(setup.status, "setup")
            self.assertEqual(adopted.status, "migrate")
            self.assertEqual(adopted.legacy_source, default.resolve())

    def test_missing_legacy_active_workspace_requires_recovery(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            missing = root / "missing"
            registry_path = root / "state" / "workspaces.yml"
            save_workspace_registry(
                WorkspaceRegistry(active=missing, workspaces=(missing,)),
                registry_path,
            )
            settings_path = root / "state" / "settings.yml"
            with (
                patch(
                    "jobtracker.paths.workspace_settings_file",
                    return_value=settings_path,
                ),
                patch(
                    "jobtracker.paths.workspace_registry_file",
                    return_value=registry_path,
                ),
                patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("JOBTRACKER_DATA_DIR", None)
                resolution = resolve_desktop_workspace()

            self.assertEqual(resolution.status, "recovery")
            self.assertIn("unavailable", resolution.error)


class WorkspaceFolderValidationTests(unittest.TestCase):
    def test_new_workspace_must_be_empty(self) -> None:
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            self.assertEqual(validate_workspace_folder(folder, "new"), folder.resolve())
            (folder / "unrelated.txt").touch()
            with self.assertRaisesRegex(ValueError, "empty"):
                validate_workspace_folder(folder, "new")

    def test_existing_workspace_requires_database(self) -> None:
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            with self.assertRaisesRegex(ValueError, "database"):
                validate_workspace_folder(folder, "existing")
            (folder / "jobtracker.sqlite3").touch()
            self.assertEqual(validate_workspace_folder(folder, "existing"), folder.resolve())

    def test_default_folder_ignores_registry_control_files(self) -> None:
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "workspaces.yml").touch()
            (folder / ".workspaces.yml.partial.tmp").touch()
            with patch("jobtracker.paths.default_data_dir", return_value=folder):
                self.assertEqual(validate_workspace_folder(folder, "new"), folder.resolve())

    def test_rejects_unc_remote_drive_and_onedrive(self) -> None:
        with self.assertRaisesRegex(ValueError, "Network"):
            validate_workspace_folder(r"\\server\share", "new")

        with TemporaryDirectory() as temp:
            folder = Path(temp)
            with patch("jobtracker.paths._is_remote_drive", return_value=True):
                with self.assertRaisesRegex(ValueError, "Mapped"):
                    validate_workspace_folder(folder, "new")
            with patch.dict(os.environ, {"OneDrive": str(folder)}):
                with self.assertRaisesRegex(ValueError, "OneDrive"):
                    validate_workspace_folder(folder, "new")

    def test_managed_root_must_be_empty_or_have_a_valid_catalog(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(validate_workspaces_root(root, "new"), root.resolve())
            (root / "unrelated.txt").touch()
            with self.assertRaisesRegex(ValueError, "empty"):
                validate_workspaces_root(root, "new")
            (root / "unrelated.txt").unlink()
            save_workspace_catalog(root, WorkspaceCatalog())
            self.assertEqual(validate_workspaces_root(root, "existing"), root.resolve())


class ManagedWorkspaceCatalogTests(unittest.TestCase):
    def test_settings_and_relative_catalog_round_trip(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data_root = root / "data"
            data_root.mkdir()
            settings_path = root / "state" / "settings.yml"
            record = WorkspaceRecord(
                "12345678-1234-5678-1234-567812345678",
                "Jane Example",
                Path("workspaces/jane-example--12345678"),
            )

            settings = save_workspace_settings(
                WorkspaceSettings(workspaces_root=data_root),
                settings_path,
            )
            catalog = save_workspace_catalog(
                data_root,
                WorkspaceCatalog(active_id=record.id, workspaces=(record,)),
            )

            self.assertEqual(load_workspace_settings(settings_path), settings)
            self.assertEqual(load_workspace_catalog(data_root), catalog)
            self.assertEqual(
                workspace_path(data_root, catalog.workspaces[0]),
                (data_root / record.folder).resolve(),
            )
            text = (data_root / "workspaces.yml").read_text(encoding="utf-8")
            self.assertIn("version: 2", text)
            self.assertNotIn(str(data_root), text)

    def test_managed_version_one_catalog_loads_as_version_two(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "workspaces.yml").write_text(
                """
version: 1
active_workspace: "12345678123456781234567812345678"
workspaces:
- id: "12345678123456781234567812345678"
  name: Main search
  folder: workspaces/jane-example--12345678
""".lstrip(),
                encoding="utf-8",
            )

            catalog = load_workspace_catalog(root)

            assert catalog is not None
            self.assertEqual(catalog.version, 2)
            self.assertEqual(catalog.workspaces[0].name, "Main search")

    def test_legacy_version_one_registry_is_not_a_managed_catalog(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "workspaces.yml").write_text(
                """
version: 1
active: C:\\legacy
workspaces:
- C:\\legacy
""".lstrip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(WorkspaceCatalogError, "Legacy"):
                load_workspace_catalog(root)

    def test_default_managed_catalog_without_locator_is_recovered_as_active(self) -> None:
        with TemporaryDirectory() as temp:
            default_root = Path(temp) / "jobtracker"
            workspace = default_root / "workspaces" / "person--12345678"
            workspace.mkdir(parents=True)
            (workspace / "jobtracker.sqlite3").touch()
            record = WorkspaceRecord(
                "12345678123456781234567812345678",
                "Main search",
                workspace.relative_to(default_root),
            )
            save_workspace_catalog(
                default_root,
                WorkspaceCatalog(active_id=record.id, workspaces=(record,)),
            )
            settings_path = Path(temp) / "state" / "settings.yml"

            with (
                patch(
                    "jobtracker.paths.workspace_settings_file",
                    return_value=settings_path,
                ),
                patch(
                    "jobtracker.paths.default_data_dir",
                    return_value=default_root,
                ),
                patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("JOBTRACKER_DATA_DIR", None)
                resolution = resolve_desktop_workspace()

            self.assertEqual(resolution.status, "active")
            self.assertEqual(resolution.data_dir, workspace.resolve())
            self.assertIsNone(resolution.settings)

    def test_catalog_rejects_escaping_absolute_and_duplicate_folders(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            first = WorkspaceRecord(
                "12345678123456781234567812345678",
                "First",
                Path("../escape"),
            )
            with self.assertRaisesRegex(WorkspaceCatalogError, "workspaces"):
                save_workspace_catalog(root, WorkspaceCatalog(workspaces=(first,)))

            duplicate = WorkspaceRecord(
                "87654321876543218765432187654321",
                "Second",
                Path("workspaces/shared"),
            )
            valid = WorkspaceRecord(
                "12345678123456781234567812345678",
                "First",
                Path("workspaces/shared"),
            )
            with self.assertRaisesRegex(WorkspaceCatalogError, "folders"):
                save_workspace_catalog(
                    root,
                    WorkspaceCatalog(workspaces=(valid, duplicate)),
                )

    def test_catalog_atomic_replace_failure_preserves_existing_file(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            catalog_path = root / "workspaces.yml"
            save_workspace_catalog(root, WorkspaceCatalog())
            before = catalog_path.read_bytes()
            record = WorkspaceRecord(
                "12345678123456781234567812345678",
                "Person",
                Path("workspaces/person--12345678"),
            )

            with (
                patch("jobtracker.paths.os.replace", side_effect=OSError("injected")),
                self.assertRaisesRegex(OSError, "injected"),
            ):
                save_workspace_catalog(
                    root,
                    WorkspaceCatalog(active_id=record.id, workspaces=(record,)),
                )

            self.assertEqual(catalog_path.read_bytes(), before)
            self.assertEqual(list(root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
