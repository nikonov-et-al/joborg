from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from jobtracker import cli
from jobtracker.application import JobTrackerService
from jobtracker.loading import LoadJobsResult
from jobtracker.models import JobApplication
from jobtracker.repositories import (
    LegacyDocumentGateway,
    LegacyMarkdownRepository,
    LegacyReportGateway,
    build_application_service,
)
from jobtracker.services import ProjectContext, load_project_context, save_profile
from jobtracker.webapp import create_app


class FakeReports:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.write_count = 0

    def write_default(self, month=None, output_path=None):
        self.write_count += 1
        return output_path or self.path


class FakeDocuments:
    def generate_cv_draft(self, **_kwargs):
        return "# Generated"

    def render_cv_pdf(self, content, *, job_title):
        return f"{job_title}:{content}".encode()


class ApplicationServiceTests(unittest.TestCase):
    def test_context_uses_selected_workspace_and_rejects_escaped_desktop_paths(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            context = load_project_context(data_dir=workspace, enforce_workspace=True)
            self.assertEqual(context.database, workspace / "jobtracker.sqlite3")
            self.assertEqual(context.profile_dir, workspace / "profile")

            (workspace / "config.yml").write_text(
                "paths:\n  profile: ../outside-profile\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "profile"):
                load_project_context(data_dir=workspace, enforce_workspace=True)

    def test_profile_name_precedes_legacy_config_name(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = ProjectContext(
                {"applicant": {"name": "Config Name"}},
                root / "input",
                root / "jobs",
                root / "reports",
                root / "profile",
            )
            self.assertEqual(context.applicant_name, "Config Name")

            save_profile(
                context.profile_dir,
                {
                    "personal": {
                        "given_name": "Jane",
                        "family_name": "Example",
                        "email": "jane@example.com",
                    },
                    "skills": [],
                    "experience": "",
                },
            )
            self.assertEqual(context.applicant_name, "Jane Example")

            context.jobs_dir.mkdir()
            report = LegacyReportGateway(context).build()
            rendered: list[str] = []
            documents = LegacyDocumentGateway(
                context,
                render_fn=lambda _content, *, applicant_name, job_title: (
                    rendered.append(applicant_name) or b"pdf"
                ),
            )
            self.assertEqual(report.applicant_name, "Jane Example")
            self.assertEqual(
                documents.render_cv_pdf("# CV", job_title="Engineer"),
                b"pdf",
            )
            self.assertEqual(rendered, ["Jane Example"])

    def test_service_owns_create_update_profile_and_document_workflows(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = ProjectContext(
                {"applicant": {"name": "Test User"}},
                root / "input",
                root / "jobs",
                root / "reports",
                root / "profile",
            )
            context = ProjectContext(
                {"applicant": {"name": "Test User"}},
                root / "input", root / "jobs", root / "reports", root / "profile",
                database=root / "jobtracker.sqlite3", files_dir=root / "files",
            )
            service = build_application_service(context)

            created = service.create_job(
                JobApplication(company="Example", role="Engineer", date_applied="2026-07-18"),
                "Description",
            )
            updated = service.update_job(created.application_id, updates={"status": "rejected"}, note="Closed")

            self.assertEqual(updated.job.status, "rejected")
            self.assertEqual(service.list_jobs(status="rejected").loaded_count, 1)
            self.assertEqual(
                service.save_profile(
                    {"personal": {}, "skills": ["Python"], "experience": "Experience"}
                )["skills"],
                ["Python"],
            )

    def test_report_failure_does_not_undo_committed_job(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = ProjectContext(
                {}, root / "input", root / "jobs", root / "reports", root / "profile",
                database=root / "jobtracker.sqlite3", files_dir=root / "files",
            )
            service = build_application_service(context)
            with patch.object(service.reports, "write_default", side_effect=OSError("disk full")):
                result = service.create_job(JobApplication(company="Example", role="Engineer"), "Description")

            self.assertIn("Report refresh failed", result.report_warning)
            self.assertEqual(service.get_job(result.application_id).job.company, "Example")

    def test_cli_and_api_use_the_same_injected_application_service(self) -> None:
        class SharedService:
            def __init__(self) -> None:
                self.calls: list[tuple[str | None, str | None]] = []

            def list_jobs(self, *, status=None, month=None):
                self.calls.append((status, month))
                return LoadJobsResult(
                    [("example-id", JobApplication(company="Shared", role="Service"))],
                    [],
                )

        service = SharedService()
        config = {"paths": {"jobs": "jobs"}}
        with patch.object(cli, "load_config", return_value=config), patch.object(
            cli, "_application_service", return_value=service
        ):
            cli_result = CliRunner().invoke(cli.app, ["list", "--status", "applied"])

        api_result = TestClient(create_app(service)).get("/api/jobs?month=2026-07")

        self.assertEqual(cli_result.exit_code, 0, cli_result.output)
        self.assertIn("Shared", cli_result.output)
        self.assertEqual(api_result.status_code, 200)
        self.assertEqual(api_result.json()[0]["company"], "Shared")
        self.assertEqual(service.calls, [("applied", None), (None, "2026-07")])


if __name__ == "__main__":
    unittest.main()
