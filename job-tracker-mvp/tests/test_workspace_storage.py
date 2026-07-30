from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from jobtracker.database import connect_database
from jobtracker.services import load_profile, save_profile
from jobtracker.workspace_storage import (
    create_managed_workspace,
    finalize_layout_migration,
    import_managed_workspace,
    layout_migration_file,
    mark_layout_migration_activated,
    prepare_same_folder_adoption,
)


PROFILE = {
    "given_name": "Jane",
    "family_name": "Example",
    "email": "jane@example.com",
    "phone": "",
    "city": "",
    "linkedin": "",
    "github": "",
    "website": "",
}


def make_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    connection = connect_database(path / "jobtracker.sqlite3")
    connection.close()
    save_profile(
        path / "profile",
        {
            "personal": PROFILE,
            "skills": ["Python"],
            "experience": "Experience",
        },
    )


class ManagedWorkspaceStorageTests(unittest.TestCase):
    def test_new_workspace_uses_readable_stable_folder_without_empty_legacy_dirs(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            record, workspace = create_managed_workspace(
                root,
                "Jäne Example",
                PROFILE,
            )

            self.assertRegex(workspace.name, r"^jane-example--[0-9a-f]{8}$")
            self.assertEqual(record.folder, workspace.relative_to(root))
            self.assertTrue((workspace / "jobtracker.sqlite3").is_file())
            self.assertEqual(
                load_profile(workspace / "profile")["personal"]["given_name"],
                "Jane",
            )
            for name in ("files", "reports", "jobs", "input", "backups", "logs"):
                self.assertFalse((workspace / name).exists())

    def test_workspace_display_name_is_separate_from_profile_folder_slug(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)

            named_record, named_workspace = create_managed_workspace(
                root,
                "Jane Example",
                PROFILE,
                display_name="Applications 2026",
            )
            fallback_record, fallback_workspace = create_managed_workspace(
                root,
                "Jane Example",
                PROFILE,
                display_name="  ",
            )

            self.assertRegex(named_workspace.name, r"^jane-example--[0-9a-f]{8}$")
            self.assertEqual(named_record.name, "Applications 2026")
            self.assertRegex(fallback_workspace.name, r"^jane-example--[0-9a-f]{8}$")
            self.assertEqual(fallback_record.name, fallback_workspace.name)

    def test_import_copies_and_verifies_without_modifying_source(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            destination_root = root / "managed"
            destination_root.mkdir()
            make_workspace(source)
            sentinel = source / "notes" / "keep.txt"
            sentinel.parent.mkdir()
            sentinel.write_text("keep", encoding="utf-8")
            (source / "jobs").mkdir()
            (source / "config.yml").write_text(
                yaml.safe_dump(
                    {
                        "paths": {
                            "input": str(source / "input"),
                            "jobs": "jobs",
                            "reports": "reports",
                            "profile": "profile",
                        },
                        "export": {"create_pdf": False},
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            before = (source / "config.yml").read_bytes()
            _record, imported = import_managed_workspace(
                destination_root,
                source,
                "Jane Example",
            )

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertEqual((source / "config.yml").read_bytes(), before)
            self.assertEqual(
                (imported / "notes" / "keep.txt").read_text(encoding="utf-8"),
                "keep",
            )
            self.assertFalse((imported / "jobs").exists())
            config = yaml.safe_load(
                (imported / "config.yml").read_text(encoding="utf-8")
            )
            self.assertEqual(config["paths"]["input"], "input")
            self.assertEqual(config["paths"]["jobs"], "jobs")
            self.assertFalse(config["export"]["create_pdf"])

    def test_failed_import_removes_staging_and_preserves_source(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            destination_root = root / "managed"
            destination_root.mkdir()
            make_workspace(source)
            sentinel = source / "keep.txt"
            sentinel.write_text("source", encoding="utf-8")

            with (
                patch(
                    "jobtracker.workspace_storage._verify_database",
                    side_effect=OSError("injected verification failure"),
                ),
                self.assertRaisesRegex(OSError, "injected verification failure"),
            ):
                import_managed_workspace(destination_root, source, "Jane Example")

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "source")
            workspaces = destination_root / "workspaces"
            self.assertEqual(list(workspaces.iterdir()), [])

    def test_same_folder_adoption_prepares_verified_child_without_copying_global_data(
        self,
    ) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)
            sentinel = root / "notes" / "keep.txt"
            sentinel.parent.mkdir()
            sentinel.write_text("source remains", encoding="utf-8")
            backup = root / "backups" / "existing" / "keep.txt"
            backup.parent.mkdir(parents=True)
            backup.write_text("old backup", encoding="utf-8")
            existing_workspace = root / "workspaces" / "existing" / "keep.txt"
            existing_workspace.parent.mkdir(parents=True)
            existing_workspace.write_text("existing workspace", encoding="utf-8")

            record, workspace = prepare_same_folder_adoption(
                root,
                "Jane Example",
                "Primary Applications",
            )

            self.assertEqual(record.name, "Primary Applications")
            self.assertTrue((root / "jobtracker.sqlite3").is_file())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "source remains")
            self.assertEqual(backup.read_text(encoding="utf-8"), "old backup")
            self.assertEqual(
                existing_workspace.read_text(encoding="utf-8"),
                "existing workspace",
            )
            self.assertEqual(
                (workspace / "notes" / "keep.txt").read_text(encoding="utf-8"),
                "source remains",
            )
            self.assertFalse((workspace / "backups").exists())
            self.assertFalse((workspace / "workspaces").exists())
            connection = connect_database(workspace / "jobtracker.sqlite3")
            try:
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
            finally:
                connection.close()
            journal = yaml.safe_load(
                layout_migration_file(root).read_text(encoding="utf-8")
            )
            self.assertEqual(journal["phase"], "prepared")
            self.assertEqual(journal["workspace"]["id"], record.id)
            self.assertEqual(journal["workspace"]["folder"], record.folder.as_posix())

    def test_finalized_layout_migration_moves_direct_data_to_recovery_snapshot(
        self,
    ) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)
            sentinel = root / "notes" / "keep.txt"
            sentinel.parent.mkdir()
            sentinel.write_text("source remains", encoding="utf-8")
            for name in ("jobs", "input", "logs"):
                (root / name).mkdir()
            old_backup = root / "backups" / "legacy-migration-existing" / "keep.txt"
            old_backup.parent.mkdir(parents=True)
            old_backup.write_text("old backup", encoding="utf-8")

            _record, workspace = prepare_same_folder_adoption(root, "Jane Example")
            mark_layout_migration_activated(root)
            snapshot = finalize_layout_migration(root)

            assert snapshot is not None
            self.assertTrue(workspace.is_dir())
            self.assertFalse((root / "jobtracker.sqlite3").exists())
            self.assertFalse((root / "profile").exists())
            self.assertFalse((root / "notes").exists())
            for name in ("jobs", "input", "logs"):
                self.assertFalse((root / name).exists())
                self.assertTrue((snapshot / name).is_dir())
            self.assertTrue((snapshot / "jobtracker.sqlite3").is_file())
            self.assertTrue((snapshot / "profile").is_dir())
            self.assertEqual(
                (snapshot / "notes" / "keep.txt").read_text(encoding="utf-8"),
                "source remains",
            )
            self.assertEqual(old_backup.read_text(encoding="utf-8"), "old backup")
            self.assertFalse(layout_migration_file(root).exists())
            completed = yaml.safe_load(
                (snapshot / "layout-migration.yml").read_text(encoding="utf-8")
            )
            self.assertEqual(completed["phase"], "complete")

    def test_failed_same_folder_adoption_preserves_source_and_publishes_nothing(
        self,
    ) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)
            sentinel = root / "keep.txt"
            sentinel.write_text("source remains", encoding="utf-8")

            with (
                patch(
                    "jobtracker.workspace_storage._verify_database",
                    side_effect=OSError("injected verification failure"),
                ),
                self.assertRaisesRegex(OSError, "injected verification failure"),
            ):
                prepare_same_folder_adoption(root, "Jane Example")

            self.assertTrue((root / "jobtracker.sqlite3").is_file())
            self.assertTrue((root / "profile").is_dir())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "source remains")
            self.assertFalse(layout_migration_file(root).exists())
            self.assertEqual(list((root / "workspaces").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
