from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID

import jobtracker.migration as migration_module
from jobtracker.database import SCHEMA_VERSION, connect_database, initialize_database
from jobtracker.file_store import ManagedFileStore
from jobtracker.migration import (
    directory_manifest,
    execute_migration,
    preview_migration,
    verify_migration,
)
from jobtracker.models import JobApplication
from jobtracker.repositories import build_application_service
from jobtracker.services import ProjectContext, UploadedAsset
from jobtracker.sqlite_repository import SQLiteJobRepository


REQUIRED_TABLES = {
    "applications",
    "application_skills",
    "status_events",
    "documents",
    "profiles",
    "profile_items",
    "document_versions",
    "ai_runs",
    "migration_sources",
    "schema_migrations",
}


def write_legacy_job(root: Path, *, malformed: bool = False) -> Path:
    job_dir = root / "2026-07-18_example_engineer"
    job_dir.mkdir(parents=True)
    (job_dir / "job_description.md").write_text(
        "# Job description\n\nRaw description.\n", encoding="utf-8"
    )
    if malformed:
        (job_dir / "job.md").write_text(
            "---\ncompany: Example\nrole: Engineer\n", encoding="utf-8"
        )
        return job_dir

    attachment = job_dir / "attachments" / "proof.bin"
    attachment.parent.mkdir()
    attachment.write_bytes(b"legacy attachment")
    draft = job_dir / "cv" / "generated_cv.md"
    draft.parent.mkdir()
    draft.write_text("# Legacy CV draft\n", encoding="utf-8")
    (job_dir / "job.md").write_text(
        "---\n"
        "company: Example\n"
        "role: Engineer\n"
        "location: Berlin\n"
        "job_url: https://example.test/Ground-Segment---AI-Software-Engineer\n"
        "source: careers\n"
        "application_method: web\n"
        "date_applied: '2026-07-18'\n"
        "status: applied\n"
        "status_date: '2026-07-18'\n"
        "required_skills:\n"
        "- Python\n"
        "attachments:\n"
        "- attachments/proof.bin\n"
        "---\n\n"
        "# Raw body\n\nKeep --- inline.\n",
        encoding="utf-8",
    )
    return job_dir


def exact_body(path: Path) -> str:
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    return "".join(lines[closing + 1 :]).lstrip("\r\n")


class DatabaseSchemaTests(unittest.TestCase):
    def test_schema_is_repeatable_versioned_and_enforces_foreign_keys(self) -> None:
        with TemporaryDirectory() as temp:
            database = Path(temp) / "jobtracker.sqlite3"
            connection = connect_database(database)
            try:
                initialize_database(connection)
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    SCHEMA_VERSION,
                )
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(REQUIRED_TABLES <= tables)
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO application_skills "
                        "(application_id, kind, position, value) VALUES ('missing', 'required', 0, 'Python')"
                    )
            finally:
                connection.close()

            reopened = connect_database(database)
            try:
                self.assertEqual(
                    reopened.execute("PRAGMA user_version").fetchone()[0],
                    SCHEMA_VERSION,
                )
            finally:
                reopened.close()


class SQLiteRepositoryTests(unittest.TestCase):
    def test_create_get_list_update_attach_uuid_and_cv_draft_round_trip(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            repository = SQLiteJobRepository(root / "jobtracker.sqlite3", root / "files")
            job = JobApplication(
                company="Example",
                role="Engineer",
                date_applied="2026-07-18",
                status_date="2026-07-18",
                required_skills=["Python"],
            )

            application_id, created, stored = repository.create(
                job,
                "Raw description",
                [UploadedAsset("../proof.txt", b"proof", "evidence")],
            )
            UUID(application_id)
            self.assertEqual(created.required_skills, ["Python"])
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].original_filename, "proof.txt")

            details = repository.get(application_id)
            self.assertEqual(details.job_description, "Raw description")
            self.assertEqual(details.job.evidence, [])
            self.assertEqual(details.documents, tuple(stored))
            self.assertEqual(repository.list().loaded_count, 1)

            connection = connect_database(root / "jobtracker.sqlite3", initialize=False)
            try:
                document = connection.execute("SELECT * FROM documents").fetchone()
                self.assertEqual(document["application_id"], application_id)
                self.assertEqual(document["category"], "evidence")
                self.assertEqual(document["original_filename"], "proof.txt")
                self.assertEqual(document["sha256"], hashlib.sha256(b"proof").hexdigest())
                self.assertEqual(document["mime_type"], "text/plain")
                self.assertEqual(document["size"], 5)
                self.assertTrue(document["created_at"])
            finally:
                connection.close()

            _, updated, attached = repository.update(
                application_id,
                [UploadedAsset("reply.pdf", b"reply", "attachments")],
                status="interview_invited",
                status_date="2026-07-19",
                note="Invite received",
                category="attachments",
                updates={"company": "Example GmbH", "required_skills": ["Python", "SQL"]},
                job_description="Updated raw description",
            )
            self.assertEqual(updated.company, "Example GmbH")
            self.assertEqual(updated.status, "interview_invited")
            self.assertEqual(updated.required_skills, ["Python", "SQL"])
            self.assertEqual(len(attached), 1)
            self.assertEqual(attached[0].original_filename, "reply.pdf")

            UUID(repository.save_cv_draft(application_id, "# First"))
            repository.save_cv_draft(application_id, "# Latest")
            final = repository.get(application_id)
            self.assertEqual(final.job_description, "Updated raw description")
            self.assertEqual(final.cv_draft, "# Latest\n")
            self.assertIn("Invite received", final.body)

    def test_duplicate_original_names_do_not_collide_and_database_failure_cleans_file(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            repository = SQLiteJobRepository(root / "jobtracker.sqlite3", root / "files")
            job = JobApplication(company="Example", role="Engineer")

            application_id, _, stored = repository.create(
                job,
                "Description",
                [
                    UploadedAsset("same.pdf", b"first", "attachments"),
                    UploadedAsset("same.pdf", b"second", "attachments"),
                ],
            )

            self.assertEqual(len({document.id for document in stored}), 2)

            before = {path for path in (root / "files").rglob("*") if path.is_file()}
            with patch("jobtracker.sqlite_repository.insert_document", side_effect=sqlite3.IntegrityError("injected")):
                with self.assertRaises(sqlite3.IntegrityError):
                    repository.update(
                        application_id,
                        [UploadedAsset("orphan.pdf", b"orphan", "attachments")],
                    )
            after = {path for path in (root / "files").rglob("*") if path.is_file()}
            self.assertEqual(after, before)

            connection = connect_database(root / "jobtracker.sqlite3", initialize=False)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 2)
            finally:
                connection.close()

    def test_application_service_uses_sqlite_jobs_and_reports(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = ProjectContext(
                config={
                    "applicant": {"name": "Test User"},
                    "export": {"create_docx": False, "create_pdf": False},
                },
                input_dir=root / "input",
                jobs_dir=root / "jobs",
                reports_dir=root / "reports",
                profile_dir=root / "profile",
                database=root / "jobtracker.sqlite3",
                files_dir=root / "files",
            )
            service = build_application_service(context)

            result = service.create_job(
                JobApplication(
                    company="SQLite Corp",
                    role="Engineer",
                    date_applied="2026-07-18",
                    status_date="2026-07-18",
                ),
                "Description",
            )

            UUID(result.application_id)
            self.assertEqual(service.list_jobs().loaded_count, 1)
            self.assertFalse(result.report_warning)
            self.assertTrue((root / "reports" / "report.md").is_file())
            self.assertEqual(service.create_report().data.total, 1)


class LegacyMigrationTests(unittest.TestCase):
    def test_preview_execute_verify_repeat_and_corruption_detection(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "legacy"
            legacy.mkdir()
            job_dir = write_legacy_job(legacy)
            database = root / "runtime" / "jobtracker.sqlite3"
            files = root / "runtime" / "files"
            source_before = directory_manifest(legacy)

            preview = preview_migration(legacy, database)
            self.assertTrue(preview.ok)
            self.assertEqual(preview.records_discovered, 1)
            self.assertEqual(preview.records_importable, 1)
            self.assertEqual(preview.attachments, 1)
            self.assertEqual(preview.expected_destination_changes, 1)
            self.assertFalse(database.exists())
            self.assertFalse(files.exists())
            self.assertEqual(directory_manifest(legacy), source_before)

            executed = execute_migration(legacy, database, files)
            self.assertTrue(executed.ok)
            self.assertEqual(executed.imported, 1)
            self.assertEqual(executed.skipped, 0)
            self.assertEqual(directory_manifest(legacy), source_before)

            connection = connect_database(database, initialize=False)
            try:
                application = connection.execute(
                    "SELECT id, legacy_folder, job_url, job_description, body FROM applications"
                ).fetchone()
                self.assertEqual(application["legacy_folder"], job_dir.name)
                self.assertIn("Ground-Segment---AI-Software-Engineer", application["job_url"])
                self.assertEqual(
                    application["job_description"],
                    (job_dir / "job_description.md").read_bytes().decode("utf-8"),
                )
                self.assertEqual(application["body"], exact_body(job_dir / "job.md"))
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1
                )
                self.assertEqual(
                    connection.execute("SELECT content FROM document_versions").fetchone()[0],
                    (job_dir / "cv" / "generated_cv.md").read_bytes().decode("utf-8"),
                )
                document = connection.execute(
                    "SELECT stored_path, source_relative_path FROM documents"
                ).fetchone()
                self.assertEqual(document["source_relative_path"], "attachments/proof.bin")
                stored_path = document["stored_path"]
            finally:
                connection.close()

            verified = verify_migration(legacy, database, files)
            self.assertTrue(verified.ok)
            self.assertEqual(verified.verified, 1)

            repeated = execute_migration(legacy, database, files)
            self.assertTrue(repeated.ok)
            self.assertEqual(repeated.imported, 0)
            self.assertEqual(repeated.skipped, 1)
            self.assertEqual(repeated.destination_applications, 1)
            self.assertEqual(repeated.destination_migration_sources, 1)
            self.assertEqual(directory_manifest(legacy), source_before)

            copied = ManagedFileStore(files).resolve(stored_path)
            copied.write_bytes(b"corrupt destination")
            corrupted = verify_migration(legacy, database, files)
            self.assertFalse(corrupted.ok)
            self.assertIn(
                "attachment_hash_mismatch",
                {error["code"] for error in corrupted.blocking_errors},
            )
            self.assertEqual(directory_manifest(legacy), source_before)

    def test_malformed_fixture_blocks_execute_without_destination_writes(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "legacy"
            legacy.mkdir()
            write_legacy_job(legacy, malformed=True)
            database = root / "runtime" / "jobtracker.sqlite3"
            files = root / "runtime" / "files"
            source_before = directory_manifest(legacy)

            preview = preview_migration(legacy, database)
            self.assertFalse(preview.ok)
            self.assertTrue(preview.blocking_errors)
            self.assertFalse(database.exists())

            executed = execute_migration(legacy, database, files)
            self.assertFalse(executed.ok)
            self.assertEqual(executed.imported, 0)
            self.assertFalse(database.exists())
            self.assertFalse(files.exists())
            self.assertEqual(directory_manifest(legacy), source_before)

    def test_mid_record_copy_failure_rolls_back_and_retry_succeeds(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "legacy"
            legacy.mkdir()
            job_dir = write_legacy_job(legacy)
            second = job_dir / "evidence" / "second.bin"
            second.parent.mkdir()
            second.write_bytes(b"second attachment")
            database = root / "runtime" / "jobtracker.sqlite3"
            files = root / "runtime" / "files"
            source_before = directory_manifest(legacy)
            original_copy = migration_module._copy_asset
            calls = 0

            def fail_second(source: Path, file_store: ManagedFileStore):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected copy failure")
                return original_copy(source, file_store)

            with patch.object(migration_module, "_copy_asset", side_effect=fail_second):
                failed = execute_migration(legacy, database, files)

            self.assertFalse(failed.ok)
            connection = connect_database(database, initialize=False)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM migration_sources").fetchone()[0], 0)
            finally:
                connection.close()
            self.assertEqual(list(files.rglob("*.*")) if files.exists() else [], [])
            self.assertEqual(directory_manifest(legacy), source_before)

            retried = execute_migration(legacy, database, files)
            self.assertTrue(retried.ok)
            self.assertEqual(retried.imported, 1)
            self.assertEqual(directory_manifest(legacy), source_before)


if __name__ == "__main__":
    unittest.main()
