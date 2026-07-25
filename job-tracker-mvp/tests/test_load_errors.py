from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from jobtracker import cli
from jobtracker.markdown import read_markdown_with_frontmatter
from jobtracker.reports import (
    LoadJobError,
    LoadJobsResult,
    build_report_data,
    load_jobs,
    report_data_to_dict,
)
from jobtracker.services import ProjectContext, list_job_records
from jobtracker.webapp import app


def write_job(jobs_dir: Path, folder: str, metadata: dict | None = None) -> Path:
    job_dir = jobs_dir / folder
    job_dir.mkdir(parents=True)
    values = {
        "company": "Good Corp",
        "role": "Engineer",
        "date_applied": "2026-07-16",
        "status": "applied",
    }
    if metadata:
        values.update(metadata)
    frontmatter = yaml.safe_dump(values, allow_unicode=True, sort_keys=False)
    path = job_dir / "job.md"
    path.write_text(f"---\n{frontmatter}---\n\nBody\n", encoding="utf-8")
    return path


def context_for(root: Path) -> ProjectContext:
    return ProjectContext(
        config={
            "applicant": {"name": "Test User"},
            "paths": {
                "input": str(root / "input"),
                "jobs": str(root / "jobs"),
                "reports": str(root / "reports"),
                "profile": str(root / "profile"),
            },
        },
        input_dir=root / "input",
        jobs_dir=root / "jobs",
        reports_dir=root / "reports",
        profile_dir=root / "profile",
    )


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class LoadErrorsTests(unittest.TestCase):
    def test_all_records_load_without_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            write_job(jobs_dir, "2026-07-15_alpha_engineer")
            write_job(jobs_dir, "2026-07-16_beta_engineer")

            result = load_jobs(jobs_dir)

            self.assertIsInstance(result, LoadJobsResult)
            self.assertEqual(len(result.jobs), 2)
            self.assertEqual(result.errors, [])

    def test_frontmatter_failures_are_structured_and_valid_records_remain(self) -> None:
        malformed_values = {
            "malformed_yaml": "---\ncompany: [broken\n---\nBody\n",
            "unclosed": "---\ncompany: Broken\n",
            "non_mapping": "---\n- one\n- two\n---\nBody\n",
        }
        for name, malformed_content in malformed_values.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                jobs_dir = Path(temp_dir) / "jobs"
                malformed_path = write_job(jobs_dir, "2026-07-15_broken_engineer")
                malformed_path.write_text(malformed_content, encoding="utf-8")
                write_job(jobs_dir, "2026-07-16_good-corp_engineer")

                result = load_jobs(jobs_dir)
                repeated = load_jobs(jobs_dir)
                service_result = list_job_records(jobs_dir)

                self.assertIsInstance(result, LoadJobsResult)
                self.assertEqual(len(result.jobs), 1)
                self.assertEqual(result.jobs[0][1].company, "Good Corp")
                self.assertEqual(len(result.errors), 1)
                self.assertEqual(result, repeated)
                self.assertEqual(service_result, result)

                error = result.errors[0]
                self.assertIsInstance(error, LoadJobError)
                self.assertEqual(error.path, malformed_path.relative_to(jobs_dir).as_posix())
                self.assertEqual(error.code, "frontmatter_error")
                self.assertIn(error.exception_type, {"ParserError", "ValueError"})
                self.assertTrue(error.message)
                self.assertNotIn(str(jobs_dir), error.message)
                json.dumps(asdict(error), sort_keys=True)

    def test_invalid_status_is_a_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            invalid_path = write_job(
                jobs_dir,
                "2026-07-16_invalid_engineer",
                metadata={"status": "invented"},
            )

            result = load_jobs(jobs_dir)

            self.assertEqual(result.jobs, [])
            self.assertEqual(len(result.errors), 1)
            error = result.errors[0]
            self.assertEqual(error.path, invalid_path.relative_to(jobs_dir).as_posix())
            self.assertEqual(error.code, "invalid_status")
            self.assertEqual(error.exception_type, "ValueError")
            self.assertIn("invalid", error.message.lower())
            self.assertIn("status", error.message.lower())

    def test_invalid_date_and_metadata_types_are_structured_errors(self) -> None:
        cases = (
            ({"date_applied": "not-a-date"}, "invalid_date"),
            ({"company": []}, "invalid_metadata"),
        )
        for metadata, expected_code in cases:
            with self.subTest(code=expected_code), tempfile.TemporaryDirectory() as temp_dir:
                jobs_dir = Path(temp_dir) / "jobs"
                write_job(jobs_dir, "2026-07-16_invalid_engineer", metadata=metadata)

                result = load_jobs(jobs_dir)

                self.assertEqual(result.jobs, [])
                self.assertEqual(len(result.errors), 1)
                self.assertEqual(result.errors[0].code, expected_code)

    def test_missing_job_file_is_a_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            missing_dir = jobs_dir / "2026-07-15_missing_engineer"
            missing_dir.mkdir(parents=True)
            write_job(jobs_dir, "2026-07-16_good-corp_engineer")

            result = load_jobs(jobs_dir)

            self.assertEqual(len(result.jobs), 1)
            self.assertEqual(len(result.errors), 1)
            self.assertEqual(result.errors[0].path, "2026-07-15_missing_engineer/job.md")
            self.assertEqual(result.errors[0].code, "missing_file")
            self.assertEqual(result.errors[0].exception_type, "FileNotFoundError")

    def test_unicode_decode_failure_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            unreadable = write_job(jobs_dir, "2026-07-15_bad-encoding_engineer")
            unreadable.write_bytes(b"\xff\xfe")

            error = load_jobs(jobs_dir).errors[0]

            self.assertEqual(error.path, unreadable.relative_to(jobs_dir).as_posix())
            self.assertEqual(error.code, "decode_error")
            self.assertEqual(error.exception_type, "UnicodeDecodeError")

    def test_unexpected_parser_exception_is_safe_and_does_not_hide_valid_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            broken_path = write_job(jobs_dir, "2026-07-15_broken_engineer")
            write_job(jobs_dir, "2026-07-16_good-corp_engineer")

            def unexpected_for_broken(path: Path) -> tuple[dict, str]:
                if path == broken_path:
                    raise RuntimeError(r"sensitive detail at C:\outside\secret.txt")
                return read_markdown_with_frontmatter(path)

            with patch(
                "jobtracker.reports.read_markdown_with_frontmatter",
                side_effect=unexpected_for_broken,
            ):
                result = load_jobs(jobs_dir)

            self.assertEqual(len(result.jobs), 1)
            self.assertEqual(result.jobs[0][1].company, "Good Corp")
            self.assertEqual(len(result.errors), 1)
            error = result.errors[0]
            self.assertEqual(error.path, broken_path.relative_to(jobs_dir).as_posix())
            self.assertEqual(error.code, "unexpected_load_error")
            self.assertEqual(error.exception_type, "RuntimeError")
            self.assertNotIn("secret", error.message)
            self.assertNotIn("outside", error.message)

    def test_multiple_failures_are_all_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            unclosed = write_job(jobs_dir, "2026-07-13_unclosed_engineer")
            unclosed.write_text("---\ncompany: Broken\n", encoding="utf-8")
            non_mapping = write_job(jobs_dir, "2026-07-14_list_engineer")
            non_mapping.write_text("---\n- one\n---\n", encoding="utf-8")
            (jobs_dir / "2026-07-15_missing_engineer").mkdir(parents=True)
            write_job(jobs_dir, "2026-07-16_good-corp_engineer")

            result = load_jobs(jobs_dir)

            self.assertEqual(len(result.jobs), 1)
            self.assertEqual(len(result.errors), 3)
            self.assertEqual(
                [error.code for error in result.errors],
                ["frontmatter_error", "frontmatter_error", "missing_file"],
            )

    def test_load_errors_have_deterministic_path_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            for folder in ("2026-07-16_zeta_engineer", "2026-07-15_alpha_engineer"):
                path = write_job(jobs_dir, folder)
                path.write_text("---\ncompany: Broken\n", encoding="utf-8")

            paths = [error.path for error in load_jobs(jobs_dir).errors]

            self.assertEqual(paths, sorted(paths))

    def test_report_data_keeps_valid_rows_and_exposes_load_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            broken_path = write_job(jobs_dir, "2026-07-15_broken_engineer")
            broken_path.write_text("---\ncompany: Broken\n", encoding="utf-8")
            write_job(jobs_dir, "2026-07-16_good-corp_engineer")

            report = build_report_data(jobs_dir)
            data = report_data_to_dict(report)

            self.assertEqual(report.total, 1)
            self.assertEqual(report.rows[0].company, "Good Corp")
            self.assertEqual(len(report.load_errors), 1)
            self.assertEqual(data["loaded_count"], 1)
            self.assertEqual(data["failed_count"], 1)
            self.assertEqual(len(data["load_errors"]), 1)
            self.assertEqual(data["load_errors"][0]["code"], "frontmatter_error")
            self.assertEqual(
                data["load_errors"][0]["path"],
                broken_path.relative_to(jobs_dir).as_posix(),
            )
            json.dumps(data, sort_keys=True)

    def test_jobs_api_preserves_legacy_list_and_optionally_includes_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = context_for(root)
            broken_path = write_job(context.jobs_dir, "2026-07-15_broken_engineer")
            broken_path.write_text("---\ncompany: Broken\n", encoding="utf-8")
            write_job(context.jobs_dir, "2026-07-16_good-corp_engineer")

            with patch("jobtracker.webapp.load_project_context", return_value=context):
                client = TestClient(app)
                legacy = client.get("/api/jobs")
                diagnostic = client.get("/api/jobs?include_errors=true")

            self.assertEqual(legacy.status_code, 200)
            self.assertIsInstance(legacy.json(), list)
            self.assertEqual(len(legacy.json()), 1)
            self.assertEqual(legacy.json()[0]["company"], "Good Corp")
            self.assertEqual(legacy.headers["X-Jobtracker-Loaded-Count"], "1")
            self.assertEqual(legacy.headers["X-Jobtracker-Failed-Count"], "1")

            self.assertEqual(diagnostic.status_code, 200)
            payload = diagnostic.json()
            self.assertEqual(payload["loaded_count"], 1)
            self.assertEqual(payload["failed_count"], 1)
            self.assertEqual(len(payload["jobs"]), 1)
            self.assertEqual(payload["jobs"][0]["company"], "Good Corp")
            self.assertEqual(len(payload["errors"]), 1)
            self.assertEqual(payload["errors"][0]["code"], "frontmatter_error")
            self.assertEqual(
                payload["errors"][0]["path"],
                broken_path.relative_to(context.jobs_dir).as_posix(),
            )
            self.assertEqual(payload["errors"][0]["exception_type"], "ValueError")
            self.assertTrue(payload["errors"][0]["message"])

    def test_cli_list_prints_valid_jobs_and_errors_then_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_dir = Path(temp_dir) / "jobs"
            broken_path = write_job(jobs_dir, "2026-07-15_broken_engineer")
            broken_path.write_text("---\ncompany: Broken\n", encoding="utf-8")
            write_job(jobs_dir, "2026-07-16_good-corp_engineer")
            before = snapshot(jobs_dir)
            config = {"paths": {"jobs": str(jobs_dir)}}

            with patch.object(cli, "load_config", return_value=config):
                result = CliRunner().invoke(cli.app, ["list"])

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("Good Corp", result.output)
            self.assertIn(broken_path.parent.name, result.output)
            self.assertIn("Loaded records: 1", result.output)
            self.assertIn("Failed records: 1", result.output)
            self.assertIn("Run job doctor for details.", result.output)
            self.assertEqual(snapshot(jobs_dir), before)


if __name__ == "__main__":
    unittest.main()
