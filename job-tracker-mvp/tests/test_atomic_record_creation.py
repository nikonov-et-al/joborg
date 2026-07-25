from __future__ import annotations

from dataclasses import asdict
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobtracker import cli
from jobtracker.doctor import inspect_jobs
from jobtracker.markdown import AtomicWriteError, read_markdown_with_frontmatter, write_markdown_with_frontmatter
from jobtracker.models import JobApplication
from jobtracker.reports import load_jobs
from jobtracker.services import (
    AttachmentCopyError,
    JobCreationError,
    UploadedAsset,
    create_job_record,
    write_job_record,
)


WORKSPACE_DIRS = {"attachments", "cv", "evidence", "motivation_letter", "screenshots"}


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def job() -> JobApplication:
    return JobApplication(
        company="Example",
        role="Engineer",
        date_applied="2026-07-16",
        status="applied",
    )


class AtomicRecordCreationTests(unittest.TestCase):
    def test_success_renames_complete_hidden_sibling_and_preserves_asset_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            destination = jobs_dir / "2026-07-16_example_engineer"
            assets = [
                UploadedAsset("resume.pdf", b"cv bytes", "cv"),
                UploadedAsset("letter.pdf", b"letter bytes", "motivation_letter"),
                UploadedAsset("portfolio.zip", b"portfolio bytes", "attachments"),
                UploadedAsset("reply.eml", b"evidence bytes", "evidence"),
                UploadedAsset("screen.png", b"screenshot bytes", "screenshots"),
            ]
            original_rename = Path.rename
            rename_sources: list[Path] = []
            events: list[str] = []

            def record_validation(path: Path) -> tuple[dict, str]:
                events.append("validate")
                return read_markdown_with_frontmatter(path)

            def record_rename(source: Path, target: Path) -> Path:
                events.append("rename")
                rename_sources.append(source)
                self.assertEqual(source.parent, destination.parent)
                self.assertTrue(source.name.startswith("."))
                self.assertEqual(Path(target), destination)
                self.assertTrue((source / "job.md").is_file())
                self.assertTrue((source / "job_description.md").is_file())
                self.assertTrue(WORKSPACE_DIRS <= {path.name for path in source.iterdir() if path.is_dir()})
                return original_rename(source, target)

            with (
                patch(
                    "jobtracker.services.read_markdown_with_frontmatter",
                    side_effect=record_validation,
                ),
                patch.object(Path, "rename", autospec=True, side_effect=record_rename),
            ):
                created_job, stored_paths = create_job_record(
                    destination,
                    job(),
                    "Build reliable systems.",
                    assets,
                )

            self.assertEqual(len(rename_sources), 1)
            self.assertEqual(events, ["validate", "rename"])
            self.assertEqual(list(jobs_dir.iterdir()), [destination])
            self.assertEqual(
                {path.name for path in destination.iterdir() if path.is_dir()},
                WORKSPACE_DIRS,
            )
            self.assertEqual(
                stored_paths,
                [
                    "cv/resume.pdf",
                    "motivation_letter/letter.pdf",
                    "attachments/portfolio.zip",
                    "evidence/reply.eml",
                    "screenshots/screen.png",
                ],
            )
            self.assertEqual((destination / "cv" / "resume.pdf").read_bytes(), b"cv bytes")
            self.assertEqual(
                (destination / "motivation_letter" / "letter.pdf").read_bytes(),
                b"letter bytes",
            )
            self.assertEqual(
                (destination / "attachments" / "portfolio.zip").read_bytes(),
                b"portfolio bytes",
            )
            self.assertEqual((destination / "evidence" / "reply.eml").read_bytes(), b"evidence bytes")
            self.assertEqual(
                (destination / "screenshots" / "screen.png").read_bytes(),
                b"screenshot bytes",
            )

            metadata, body = read_markdown_with_frontmatter(destination / "job.md")
            self.assertEqual(metadata, created_job.to_metadata())
            self.assertEqual(metadata["cv_file"], "cv/resume.pdf")
            self.assertEqual(metadata["motivation_letter_file"], "motivation_letter/letter.pdf")
            self.assertEqual(metadata["attachments"], ["attachments/portfolio.zip"])
            self.assertEqual(metadata["evidence"], ["evidence/reply.eml"])
            self.assertEqual(metadata["screenshots"], ["screenshots/screen.png"])
            self.assertIn("Build reliable systems.", body)
            self.assertIn(
                "Build reliable systems.",
                (destination / "job_description.md").read_text(encoding="utf-8"),
            )

    def test_existing_destination_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            destination = jobs_dir / "2026-07-16_existing_engineer"
            destination.mkdir(parents=True)
            (destination / "job.md").write_bytes(b"existing job bytes\r\n")
            (destination / "keep.bin").write_bytes(b"keep bytes")
            before = snapshot(jobs_dir)

            with self.assertRaises(FileExistsError):
                create_job_record(
                    destination,
                    job(),
                    "Replacement description",
                    [UploadedAsset("new.pdf", b"new bytes", "cv")],
                )

            self.assertEqual(snapshot(jobs_dir), before)
            self.assertEqual(list(jobs_dir.iterdir()), [destination])

    def test_temporary_directory_creation_failure_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            destination = jobs_dir / "2026-07-16_example_engineer"

            with patch(
                "jobtracker.services.TemporaryDirectory",
                side_effect=OSError("injected temporary directory failure"),
            ):
                with self.assertRaises(JobCreationError) as raised:
                    create_job_record(destination, job(), "Description")

            self.assertEqual(raised.exception.stage, "temporary_directory_creation")
            self.assertIn(str(destination), str(raised.exception))
            self.assertFalse(destination.exists())
            self.assertTrue(jobs_dir.is_dir())
            self.assertEqual(list(jobs_dir.iterdir()), [])

    def test_later_markdown_write_failure_cleans_temp_and_preserves_caller(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            destination = jobs_dir / "2026-07-16_example_engineer"
            caller = job()
            caller_before = asdict(caller)
            assets = [
                UploadedAsset("resume.pdf", b"cv bytes", "cv"),
                UploadedAsset("reply.eml", b"evidence bytes", "evidence"),
            ]

            with patch(
                "jobtracker.services.write_job_record",
                side_effect=AtomicWriteError(destination / "job.md", "write"),
            ):
                with self.assertRaises(AtomicWriteError) as raised:
                    create_job_record(destination, caller, "Description", assets)

            self.assertEqual(raised.exception.stage, "write")
            self.assertEqual(asdict(caller), caller_before)
            self.assertFalse(destination.exists())
            self.assertTrue(jobs_dir.is_dir())
            self.assertEqual(list(jobs_dir.iterdir()), [])

    def test_attachment_copy_failure_cleans_temp_and_leaves_no_final_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            destination = jobs_dir / "2026-07-16_example_engineer"
            assets = [
                UploadedAsset("first.pdf", b"first bytes", "attachments"),
                UploadedAsset("broken.pdf", b"second bytes", "attachments"),
            ]
            original_write_bytes = Path.write_bytes

            def fail_second_attachment(path: Path, content: bytes) -> int:
                if path.name == "broken.pdf":
                    raise OSError("injected attachment write failure")
                return original_write_bytes(path, content)

            with patch.object(Path, "write_bytes", autospec=True, side_effect=fail_second_attachment):
                with self.assertRaises(AttachmentCopyError) as raised:
                    create_job_record(destination, job(), "Description", assets)

            self.assertEqual(raised.exception.filename, "broken.pdf")
            self.assertFalse(destination.exists())
            self.assertTrue(jobs_dir.is_dir())
            self.assertEqual(list(jobs_dir.iterdir()), [])

    def test_final_rename_failure_cleans_temp_and_preserves_caller(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            destination = jobs_dir / "2026-07-16_example_engineer"
            caller = job()
            caller_before = asdict(caller)
            original_rename = Path.rename

            def fail_final_rename(source: Path, target: Path) -> Path:
                target = Path(target)
                if source.parent == destination.parent and source != destination and target == destination:
                    raise OSError("injected final rename failure")
                return original_rename(source, target)

            with patch.object(Path, "rename", autospec=True, side_effect=fail_final_rename):
                with self.assertRaises(JobCreationError) as raised:
                    create_job_record(
                        destination,
                        caller,
                        "Description",
                        [UploadedAsset("portfolio.pdf", b"asset bytes", "attachments")],
                    )

            self.assertEqual(raised.exception.stage, "final_rename")
            self.assertEqual(asdict(caller), caller_before)
            self.assertFalse(destination.exists())
            self.assertTrue(jobs_dir.is_dir())
            self.assertEqual(list(jobs_dir.iterdir()), [])

    def test_retry_after_failed_attempt_succeeds_without_stale_temp_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            destination = jobs_dir / "2026-07-16_example_engineer"
            caller = job()
            attempts = 0

            def fail_once(job_dir: Path, value: JobApplication, description: str) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise AtomicWriteError(job_dir / "job.md", "write")
                write_job_record(job_dir, value, description)

            with patch("jobtracker.services.write_job_record", side_effect=fail_once):
                with self.assertRaises(AtomicWriteError):
                    create_job_record(destination, caller, "Description")
                self.assertEqual(list(jobs_dir.iterdir()), [])

                created, stored = create_job_record(destination, caller, "Description")

            self.assertEqual(created.to_metadata(), caller.to_metadata())
            self.assertEqual(stored, [])
            self.assertEqual(list(jobs_dir.iterdir()), [destination])
            self.assertTrue((destination / "job.md").is_file())
            self.assertTrue((destination / "job_description.md").is_file())

    def test_cli_creation_sources_are_copied_and_remain_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            jobs_dir = root / "jobs"
            destination = jobs_dir / "2026-07-16_example_engineer"
            input_dir.mkdir()
            (input_dir / "resume.pdf").write_bytes(b"resume bytes")
            (input_dir / "portfolio.txt").write_bytes(b"portfolio bytes")
            before = snapshot(input_dir)

            with (
                patch.object(cli, "choose_file_from_input", side_effect=["resume.pdf", None]),
                patch.object(cli, "choose_many_files_from_input", return_value=["portfolio.txt"]),
                patch.object(cli, "ask_bool", return_value=True),
            ):
                assets = cli.collect_application_documents(destination, input_dir)

            created, stored = create_job_record(destination, job(), "Description", assets)

            self.assertEqual(snapshot(input_dir), before)
            self.assertEqual(stored, ["cv/resume.pdf", "attachments/portfolio.txt"])
            self.assertEqual(created.cv_file, "cv/resume.pdf")
            self.assertEqual(created.attachments, ["attachments/portfolio.txt"])
            self.assertEqual((destination / stored[0]).read_bytes(), b"resume bytes")
            self.assertEqual((destination / stored[1]).read_bytes(), b"portfolio bytes")

    def test_reports_and_doctor_ignore_hidden_in_progress_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            visible = jobs_dir / "2026-07-16_visible_engineer"
            hidden = jobs_dir / ".2026-07-17_hidden_engineer.in-progress"
            visible.mkdir(parents=True)
            hidden.mkdir(parents=True)
            write_markdown_with_frontmatter(visible / "job.md", job().to_metadata(), "Body")
            write_markdown_with_frontmatter(
                hidden / "job.md",
                JobApplication(
                    company="Hidden",
                    role="Engineer",
                    date_applied="2026-07-17",
                    status="applied",
                ).to_metadata(),
                "Body",
            )

            loaded = load_jobs(jobs_dir)
            doctor = inspect_jobs(jobs_dir).to_dict()

            self.assertEqual(len(loaded.jobs), 1)
            self.assertEqual(loaded.jobs[0][1].company, "Example")
            self.assertEqual(doctor["summary"]["job_directories"], 1)
            self.assertEqual(doctor["summary"]["records_parsed"], 1)


if __name__ == "__main__":
    unittest.main()
