from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from jobtracker import __version__
from jobtracker.backups import (
    DatabaseHealthError,
    MANIFEST_FILENAME,
    create_migration_backup,
    create_workspace_export,
    list_migration_backups,
    mark_migration_backup_verified,
    prune_migration_backups,
    verify_workspace_export,
)
from jobtracker.database import (
    DatabaseMigrationError,
    DatabaseVersionError,
    SCHEMA_V1_SQL,
    SCHEMA_VERSION,
    connect_database,
)
from jobtracker.desktop import _probe_workspace_database


def create_version_one_database(path: Path, *, wal: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_V1_SQL)
    connection.execute("PRAGMA user_version = 1")
    if wal:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute(
        """
        INSERT INTO profiles (id, name, created_at, updated_at)
        VALUES ('profile-1', 'Synthetic User', '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z')
        """
    )
    connection.commit()
    return connection


class DatabaseEvolutionTests(unittest.TestCase):
    def test_version_one_upgrades_once_with_verified_backup(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "jobtracker.sqlite3"
            create_version_one_database(database).close()

            connection = connect_database(database)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    SCHEMA_VERSION,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT app_version FROM schema_migrations WHERE version = 2"
                    ).fetchone()[0],
                    __version__,
                )
                self.assertEqual(
                    connection.execute("SELECT name FROM profiles").fetchone()[0],
                    "Synthetic User",
                )
            finally:
                connection.close()

            backups = list_migration_backups(database)
            self.assertEqual(len(backups), 1)
            self.assertTrue(backups[0].verified)
            manifest = (backups[0].path / MANIFEST_FILENAME).read_text(encoding="utf-8")
            self.assertNotIn(str(root), manifest)

            reopened = connect_database(database)
            reopened.close()
            self.assertEqual(len(list_migration_backups(database)), 1)

    def test_wal_snapshot_contains_committed_rows(self) -> None:
        with TemporaryDirectory() as temp:
            database = Path(temp) / "jobtracker.sqlite3"
            writer = create_version_one_database(database, wal=True)
            try:
                upgraded = connect_database(database)
                upgraded.close()
                backup = list_migration_backups(database)[0]
                snapshot = sqlite3.connect(backup.path / "jobtracker.sqlite3")
                try:
                    self.assertEqual(
                        snapshot.execute("SELECT name FROM profiles").fetchone()[0],
                        "Synthetic User",
                    )
                finally:
                    snapshot.close()
            finally:
                writer.close()

    def test_future_unversioned_and_foreign_key_invalid_databases_are_read_only_failures(
        self,
    ) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)

            future = root / "future.sqlite3"
            create_version_one_database(future).close()
            connection = sqlite3.connect(future)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            connection.close()
            before = hashlib.sha256(future.read_bytes()).hexdigest()
            with self.assertRaises(DatabaseVersionError):
                connect_database(future)
            self.assertEqual(hashlib.sha256(future.read_bytes()).hexdigest(), before)
            self.assertFalse((root / "backups").exists())

            unversioned = root / "unversioned.sqlite3"
            connection = sqlite3.connect(unversioned)
            connection.execute("CREATE TABLE foreign_data (value TEXT)")
            connection.close()
            with self.assertRaises(DatabaseVersionError):
                connect_database(unversioned)
            probe = sqlite3.connect(unversioned)
            try:
                self.assertEqual(probe.execute("PRAGMA user_version").fetchone()[0], 0)
            finally:
                probe.close()

            invalid = root / "invalid.sqlite3"
            create_version_one_database(invalid).close()
            connection = sqlite3.connect(invalid)
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                """
                INSERT INTO application_skills
                    (application_id, kind, position, value)
                VALUES ('missing', 'required', 0, 'Python')
                """
            )
            connection.commit()
            connection.close()
            with self.assertRaises(DatabaseHealthError):
                connect_database(invalid)
            probe = sqlite3.connect(invalid)
            try:
                self.assertEqual(probe.execute("PRAGMA user_version").fetchone()[0], 1)
            finally:
                probe.close()

    def test_migration_failure_rolls_back_and_keeps_recoverable_backup(self) -> None:
        with TemporaryDirectory() as temp:
            database = Path(temp) / "jobtracker.sqlite3"
            create_version_one_database(database).close()

            def fail(connection: sqlite3.Connection) -> None:
                connection.execute("CREATE TABLE should_roll_back (id INTEGER)")
                raise sqlite3.OperationalError("injected migration failure")

            with patch("jobtracker.database.MIGRATIONS", {1: fail}):
                with self.assertRaises(DatabaseMigrationError) as raised:
                    connect_database(database)

            self.assertTrue(raised.exception.backup_path.is_dir())
            probe = sqlite3.connect(database)
            try:
                self.assertEqual(probe.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertIsNone(
                    probe.execute(
                        "SELECT 1 FROM sqlite_master WHERE name = 'should_roll_back'"
                    ).fetchone()
                )
            finally:
                probe.close()
            backups = list_migration_backups(database)
            self.assertEqual(len(backups), 1)
            self.assertFalse(backups[0].verified)

    def test_corruption_blocks_migration_before_backup_or_schema_write(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "jobtracker.sqlite3"
            create_version_one_database(database).close()
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA writable_schema = ON")
            connection.execute(
                "UPDATE sqlite_schema SET rootpage = 999999 WHERE name = 'profiles'"
            )
            connection.commit()
            connection.close()

            with self.assertRaises((DatabaseHealthError, sqlite3.DatabaseError)):
                connect_database(database)
            self.assertFalse((root / "backups").exists())
            probe = sqlite3.connect(database)
            try:
                self.assertEqual(probe.execute("PRAGMA user_version").fetchone()[0], 1)
            finally:
                probe.close()

    def test_read_only_desktop_probe_accepts_supported_old_schema(self) -> None:
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            create_version_one_database(workspace / "jobtracker.sqlite3").close()
            _probe_workspace_database(workspace)


class BackupLifecycleTests(unittest.TestCase):
    def test_retention_removes_only_old_excess_verified_migration_backups(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "jobtracker.sqlite3"
            source = create_version_one_database(database)
            try:
                backups = [
                    mark_migration_backup_verified(
                        create_migration_backup(
                            source,
                            database,
                            source_schema_version=1,
                            target_schema_version=2,
                            app_version=__version__,
                        )
                    )
                    for _ in range(4)
                ]
            finally:
                source.close()
            for backup in backups:
                manifest_path = backup.path / MANIFEST_FILENAME
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["created_at"] = "2020-01-01T00:00:00Z"
                manifest_path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

            export = root / "backups" / "exports" / "keep.zip"
            export.parent.mkdir(parents=True)
            export.write_bytes(b"user export")
            removed = prune_migration_backups(
                database,
                keep_count=2,
                max_age_days=30,
                now=datetime(2026, 7, 30, tzinfo=timezone.utc),
            )
            self.assertEqual(len(removed), 2)
            self.assertEqual(len(list_migration_backups(database)), 2)
            self.assertEqual(export.read_bytes(), b"user export")

    def test_workspace_export_verifies_and_tampering_is_detected(self) -> None:
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            connection = connect_database(workspace / "jobtracker.sqlite3")
            profile = workspace / "profile" / "profile.txt"
            profile.parent.mkdir()
            profile.write_text("synthetic profile", encoding="utf-8")
            managed = workspace / "files" / "aa" / "asset.bin"
            managed.parent.mkdir(parents=True)
            managed.write_bytes(b"synthetic asset")
            connection.execute(
                """
                INSERT INTO applications
                    (id, company, role, date_applied, status, created_at, updated_at)
                VALUES
                    ('application-1', 'Synthetic', 'Tester', '', 'draft',
                     '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z')
                """
            )
            connection.execute(
                """
                INSERT INTO documents
                    (id, application_id, category, original_filename, stored_path,
                     sha256, size, created_at)
                VALUES
                    ('document-1', 'application-1', 'evidence', 'asset.bin',
                     'aa/asset.bin', ?, ?, '2026-07-30T00:00:00Z')
                """,
                (hashlib.sha256(managed.read_bytes()).hexdigest(), managed.stat().st_size),
            )
            connection.commit()
            connection.close()

            export = create_workspace_export(workspace, app_version=__version__)
            manifest = verify_workspace_export(export)
            self.assertEqual(manifest["schema_version"], SCHEMA_VERSION)
            self.assertIn("jobtracker.sqlite3", manifest["files"])
            self.assertIn("profile/profile.txt", manifest["files"])
            self.assertIn("files/aa/asset.bin", manifest["files"])
            self.assertNotIn(str(workspace), json.dumps(manifest))

            tampered = export.with_name("tampered.zip")
            with zipfile.ZipFile(export) as source, zipfile.ZipFile(
                tampered, "w"
            ) as destination:
                for entry in source.infolist():
                    content = source.read(entry.filename)
                    if entry.filename == "profile/profile.txt":
                        content = b"tampered"
                    destination.writestr(entry, content)
            with self.assertRaises(ValueError):
                verify_workspace_export(tampered)


if __name__ == "__main__":
    unittest.main()
