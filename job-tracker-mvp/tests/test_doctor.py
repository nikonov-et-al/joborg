from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from jobtracker import cli
from jobtracker.doctor import inspect_jobs


ASSET_DIRS = ("cv", "motivation_letter", "attachments", "evidence", "screenshots")
SUCCESS_KEYS = {"schema_version", "summary", "records", "attachments", "issues"}


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def make_record(
    jobs_dir: Path,
    folder: str = "2026-07-16_example_engineer",
    metadata: dict | None = None,
    description: str = "# Job description\n\nBuild useful software.\n",
) -> Path:
    job_dir = jobs_dir / folder
    job_dir.mkdir(parents=True)
    for name in ASSET_DIRS:
        (job_dir / name).mkdir()

    values = {
        "company": "Example",
        "role": "Engineer",
        "job_url": "https://example.test/jobs/engineer",
        "date_applied": "2026-07-16",
        "status": "applied",
        "cv_file": "",
        "motivation_letter_file": "",
        "attachments": [],
        "evidence": [],
        "screenshots": [],
    }
    if metadata:
        values.update(metadata)

    frontmatter = yaml.safe_dump(values, allow_unicode=True, sort_keys=False)
    (job_dir / "job.md").write_text(
        f"---\n{frontmatter}---\n\n# Notes\n\nUnchanged body.\n",
        encoding="utf-8",
    )
    (job_dir / "job_description.md").write_text(description, encoding="utf-8")
    return job_dir


def issue_codes(data: dict) -> set[str]:
    return {issue["code"] for issue in data["issues"]}


class DoctorTests(unittest.TestCase):
    def assert_issue_shape(self, data: dict) -> None:
        for issue in data["issues"]:
            self.assertEqual(
                set(issue), {"code", "severity", "record_path", "field", "message"}
            )

    def test_healthy_record_and_url_delimiter_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            job_dir = make_record(
                jobs_dir,
                metadata={
                    "job_url": "https://example.test/Ground-Segment---AI-Engineer",
                    "cv_file": "cv/resume.pdf",
                },
            )
            (job_dir / "cv" / "resume.pdf").write_bytes(b"resume")
            before = snapshot(jobs_dir)

            first = inspect_jobs(jobs_dir).to_dict()
            second = inspect_jobs(jobs_dir).to_dict()

            self.assertEqual(first, second)
            self.assertEqual(set(first), SUCCESS_KEYS)
            self.assertEqual(first["schema_version"], 1)
            self.assertEqual(
                first["summary"],
                {
                    "ok": True,
                    "job_directories": 1,
                    "records_parsed": 1,
                    "records_failed": 0,
                    "physical_attachments": 1,
                    "referenced_attachments": 1,
                    "issues": 0,
                    "errors": 0,
                    "warnings": 0,
                    "issues_by_code": {},
                },
            )
            self.assertEqual(first["records"][0]["status"], "applied")
            self.assertEqual(snapshot(jobs_dir), before)
            self.assert_issue_shape(first)

    def test_all_frontmatter_failures_and_missing_record_are_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            make_record(jobs_dir, "2026-07-12_healthy_engineer")
            malformed = make_record(jobs_dir, "2026-07-13_malformed_engineer")
            unclosed = make_record(jobs_dir, "2026-07-14_unclosed_engineer")
            non_mapping = make_record(jobs_dir, "2026-07-15_list_engineer")
            missing = make_record(jobs_dir, "2026-07-16_missing_engineer")
            (malformed / "job.md").write_text(
                "---\ncompany: [broken\n---\nBody\n", encoding="utf-8"
            )
            (unclosed / "job.md").write_text("---\ncompany: Example\n", encoding="utf-8")
            (non_mapping / "job.md").write_text(
                "---\n- one\n- two\n---\nBody\n", encoding="utf-8"
            )
            (missing / "job.md").unlink()

            data = inspect_jobs(jobs_dir).to_dict()

            self.assertEqual(data["summary"]["job_directories"], 5)
            self.assertEqual(data["summary"]["records_parsed"], 1)
            self.assertEqual(data["summary"]["records_failed"], 4)
            self.assertTrue(
                {
                    "malformed_frontmatter",
                    "unclosed_frontmatter",
                    "invalid_frontmatter_type",
                    "missing_record",
                }
                <= issue_codes(data)
            )
            self.assertEqual(sum(record["parsed"] for record in data["records"]), 1)

    def test_metadata_diagnostics_have_stable_codes_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            job_dir = make_record(
                jobs_dir,
                metadata={
                    "company": [],
                    "role": "",
                    "date_applied": "2026-99-40",
                    "status": "invented",
                    "relevant_for_arbeitsagentur": "yes",
                    "attachments": ["attachments/missing.pdf"],
                },
            )
            before = snapshot(jobs_dir)

            data = inspect_jobs(jobs_dir).to_dict()

            self.assertTrue(
                {
                    "missing_required_field",
                    "invalid_metadata_type",
                    "invalid_date",
                    "unknown_status",
                    "missing_attachment",
                }
                <= issue_codes(data)
            )
            missing = next(
                issue for issue in data["issues"] if issue["code"] == "missing_attachment"
            )
            self.assertEqual(missing["record_path"], f"{job_dir.name}/job.md")
            self.assertEqual(missing["field"], "attachments")
            self.assertEqual(snapshot(jobs_dir), before)
            self.assert_issue_shape(data)

    def test_duplicate_date_mismatch_and_empty_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            first = make_record(
                jobs_dir,
                folder="2026-07-14_example_engineer",
                metadata={
                    "date_applied": "2026-07-16",
                    "cv_file": "cv/resume.pdf",
                },
                description="",
            )
            second = make_record(
                jobs_dir,
                folder="2026-07-16_other_engineer",
                metadata={"attachments": ["attachments/resume-copy.pdf"]},
                description="# Job description\n\n## Responsibilities\n",
            )
            (first / "cv" / "resume.pdf").write_bytes(b"same bytes")
            (second / "attachments" / "resume-copy.pdf").write_bytes(b"same bytes")
            (second / "screenshots" / ".gitkeep").write_bytes(b"")

            data = inspect_jobs(jobs_dir).to_dict()

            self.assertTrue(
                {
                    "duplicate_attachment_sha256",
                    "directory_date_mismatch",
                    "empty_description",
                }
                <= issue_codes(data)
            )
            self.assertEqual(
                sum(issue["code"] == "empty_description" for issue in data["issues"]), 2
            )
            self.assertEqual(data["summary"]["physical_attachments"], 2)
            self.assertEqual(data["summary"]["referenced_attachments"], 2)

    def test_json_cli_schema_is_stable_and_issues_exit_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            make_record(jobs_dir, metadata={"status": "invented"})
            before = snapshot(jobs_dir)

            with patch.object(cli, "load_config", return_value={"paths": {"jobs": str(jobs_dir)}}):
                first = CliRunner().invoke(cli.app, ["doctor", "--json"])
                second = CliRunner().invoke(cli.app, ["doctor", "--json"])

            self.assertEqual(first.exit_code, 0, first.output)
            self.assertEqual(second.exit_code, 0, second.output)
            self.assertEqual(first.stdout, second.stdout)
            data = json.loads(first.stdout)
            self.assertEqual(set(data), SUCCESS_KEYS)
            self.assertEqual(data["schema_version"], 1)
            self.assertNotIn("fatal", data)
            self.assertIn("unknown_status", issue_codes(data))
            self.assertEqual(snapshot(jobs_dir), before)

    def test_nonexistent_jobs_root_is_fatal_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "absent"

            with patch.object(cli, "load_config", return_value={"paths": {"jobs": str(jobs_dir)}}):
                result = CliRunner().invoke(cli.app, ["doctor", "--json"])

            self.assertNotEqual(result.exit_code, 0)
            data = json.loads(result.stdout)
            self.assertEqual(data["schema_version"], 1)
            self.assertIsNone(data["summary"])
            self.assertEqual(data["records"], [])
            self.assertEqual(data["attachments"], [])
            self.assertEqual(data["issues"], [])
            self.assertEqual(data["fatal"]["code"], "jobs_directory_not_found")

    def test_invalid_jobs_configuration_is_fatal_json(self) -> None:
        with patch.object(cli, "load_config", return_value={"paths": {"jobs": ""}}):
            result = CliRunner().invoke(cli.app, ["doctor", "--json"])

        self.assertNotEqual(result.exit_code, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["fatal"]["code"], "configuration_error")

    def test_human_output_includes_core_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            make_record(jobs_dir)

            with patch.object(cli, "load_config", return_value={"paths": {"jobs": str(jobs_dir)}}):
                result = CliRunner().invoke(cli.app, ["doctor"])

            self.assertEqual(result.exit_code, 0, result.output)
            output = " ".join(result.stdout.split())
            self.assertIn("Job directories: 1", output)
            self.assertIn("records parsed: 1", output)
            self.assertIn("records failed: 0", output)
            self.assertIn("physical attachments: 0", output)


if __name__ == "__main__":
    unittest.main()
