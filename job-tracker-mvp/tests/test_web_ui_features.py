from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from jobtracker.repositories import build_application_service
from jobtracker.services import ProjectContext
from jobtracker.webapp import create_app


STATIC_DIR = Path(__file__).parents[1] / "jobtracker" / "web" / "static"


class WebUiFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        cls.styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        cls.index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    def test_dates_are_formatted_only_for_display(self) -> None:
        self.assertIn("function displayDate", self.app_js)
        self.assertIn("`${day}-${month}-${year}${suffix}`", self.app_js)
        self.assertIn('type="date" value="${esc(job.date_applied)}"', self.app_js)
        self.assertIn("displayDate(job.date_applied)", self.app_js)
        self.assertIn("displayDate(row.date)", self.app_js)

    def test_report_uses_independent_all_month_and_year_selects(self) -> None:
        self.assertIn('<select id="reportMonth"><option value="">All</option>', self.app_js)
        self.assertIn('<select id="reportYear"><option value="">All</option>', self.app_js)
        self.assertIn('query.set("year", year)', self.app_js)
        self.assertIn('query.set("month_number", month)', self.app_js)
        self.assertIn('"January", "February"', self.app_js)
        self.assertIn(".sort((a, b) => Number(b) - Number(a))", self.app_js)

    def test_report_preview_has_reduced_table_and_accessible_css_bars(self) -> None:
        report_source = self.app_js.split("function reportHtml(report)", 1)[1].split("async function renderProfile", 1)[0]
        self.assertIn("<th>Date</th><th>Company</th><th>Role</th><th>Location</th><th>Status</th>", report_source)
        self.assertNotIn("<th>Method</th>", report_source)
        self.assertNotIn("<th>Evidence</th>", report_source)
        self.assertNotIn("<th>Folder</th>", report_source)
        self.assertIn('role="img" aria-label=', report_source)
        self.assertIn("percent.toFixed(1)", report_source)
        self.assertIn("No applications in this period.", report_source)
        self.assertIn(".report-bar", self.styles)
        self.assertIn(".sidebar, .main { min-width: 0; }", self.styles)

    def test_job_edit_form_covers_business_fields_and_preserves_existing_flows(self) -> None:
        self.assertIn('id="editJobForm"', self.app_js)
        for field in (
            "company", "role", "location", "job_url", "source", "application_method",
            "date_applied", "status", "status_date", "contact_person", "contact_email",
            "required_skills", "nice_to_have_skills", "language_requirements", "summary",
            "next_action", "next_action_date", "relevant_for_arbeitsagentur", "job_description",
        ):
            self.assertIn(f'name="{field}"', self.app_js)
        self.assertIn('method: "PATCH"', self.app_js)
        self.assertIn("await selectJob(id)", self.app_js)
        self.assertIn("data-save-status", self.app_js)
        self.assertIn("data-attach", self.app_js)
        self.assertIn("data-generate-cv", self.app_js)

    def test_add_job_sends_description_only_as_multipart_field(self) -> None:
        submit = self.app_js.split('if (event.target.id === "jobForm")', 1)[1]
        self.assertIn('const jobDescription = payload.job_description || "";', submit)
        self.assertIn("delete payload.job_description;", submit)
        self.assertIn('data.append("job_description", jobDescription);', submit)
        self.assertLess(
            submit.index("delete payload.job_description;"),
            submit.index('data.append("payload", JSON.stringify(payload));'),
        )
        self.assertLess(
            submit.index('await request("/jobs", { method: "POST", body: data });'),
            submit.index('/cv-draft`, { method: "POST" }'),
        )

    def test_record_load_errors_are_visible_and_actionable(self) -> None:
        self.assertIn('/jobs?include_errors=true', self.app_js)
        self.assertIn("state.loadErrors", self.app_js)
        self.assertIn("job record(s) failed to load", self.app_js)
        self.assertIn("error.path", self.app_js)
        self.assertIn("error.code", self.app_js)
        self.assertIn("error.message", self.app_js)
        self.assertIn("job doctor", self.app_js)

    def test_profile_uses_required_canonical_name_and_email_fields(self) -> None:
        profile_source = self.app_js.split("async function renderProfile()", 1)[1].split(
            "const desktopApi", 1
        )[0]
        self.assertIn('name="given_name"', profile_source)
        self.assertIn('name="family_name"', profile_source)
        self.assertIn('name="email" type="email"', profile_source)
        self.assertIn("required", profile_source)
        self.assertIn("Previous full name", profile_source)
        self.assertNotIn('name="name"', profile_source)

    def test_settings_uses_desktop_bridge_and_does_not_require_job_loading(self) -> None:
        self.assertIn('data-route="settings"', self.index_html)
        self.assertIn("window.pywebview?.api", self.app_js)
        self.assertIn("Workspace management requires the desktop application", self.app_js)
        self.assertIn("Desktop integration unavailable. Restart Job Tracker.", self.app_js)
        for method in (
            "get_workspaces",
            "choose_workspace_folder",
            "create_workspace",
            "import_workspace",
            "activate_workspace",
            "forget_workspace",
            "rename_workspace",
            "get_storage_status",
            "create_workspace_export",
        ):
            self.assertIn(f'"{method}"', self.app_js)
        navigation = self.app_js.split("async function navigate()", 1)[1].split(
            'app.addEventListener("input"', 1
        )[0]
        self.assertIn('const jobRoutes = new Set(["home", "new-job", "jobs", "report"])', navigation)
        self.assertIn("if (jobRoutes.has(route) && !state.jobs.length) await loadJobs()", navigation)
        self.assertNotIn('request("/workspaces', self.app_js)
        self.assertIn("Job Tracker data folder", self.app_js)
        self.assertIn("stored below this folder", self.app_js)
        self.assertIn("active_id", self.app_js)
        self.assertIn("Create verified export", self.app_js)
        self.assertIn("Migration backups are retained separately", self.app_js)
        self.assertIn("state.storage?.recovery", self.app_js)
        self.assertIn(".workspace-row", self.styles)

    def test_settings_supports_workspace_display_names_and_renaming(self) -> None:
        settings = self.app_js.split("function workspaceListHtml(data)", 1)[1].split(
            "async function navigate()", 1
        )[0]
        submit = self.app_js.split('if (event.target.id === "createWorkspaceForm")', 1)[1]
        self.assertIn("<strong>${esc(name)}</strong>", settings)
        self.assertIn('record.applicant_name || "Unnamed profile"', settings)
        self.assertIn('name="workspace_name"', settings)
        self.assertIn("Workspace name (optional)", settings)
        self.assertIn('data-rename-workspace="${esc(id)}"', settings)
        self.assertIn('"create_workspace", profile, workspaceName', submit)
        self.assertIn('"rename_workspace"', submit)
        self.assertIn("event.target.dataset.renameWorkspace", submit)
        self.assertIn("await renderSettings()", submit)


class AddJobApiRegressionTests(unittest.TestCase):
    def test_description_and_attachment_round_trip_without_weakening_metadata_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("input", "jobs", "reports", "profile", "files"):
                (root / name).mkdir()
            database = root / "jobtracker.sqlite3"
            context = ProjectContext(
                config={"applicant": {"name": ""}, "export": {}},
                input_dir=root / "input",
                jobs_dir=root / "jobs",
                reports_dir=root / "reports",
                profile_dir=root / "profile",
                database=database,
                files_dir=root / "files",
            )
            client = TestClient(create_app(build_application_service(context)))
            description = "First line.\n\nSecond line with umlaut: Gr\u00fc\u00dfe."
            response = client.post(
                "/api/jobs",
                data={
                    "payload": json.dumps(
                        {
                            "company": "Example",
                            "role": "Engineer",
                            "date_applied": "2026-07-21",
                        }
                    ),
                    "job_description": description,
                    "file_categories": json.dumps(
                        [{"name": "proof.txt", "category": "evidence"}]
                    ),
                },
                files={"files": ("proof.txt", b"proof", "text/plain")},
            )

            self.assertEqual(response.status_code, 201, response.text)
            job_id = response.json()["id"]
            detail = client.get(f"/api/jobs/{job_id}")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["job_description"], description)
            self.assertEqual(len(detail.json()["files"]["evidence"]), 1)

            empty = client.post(
                "/api/jobs",
                data={
                    "payload": json.dumps(
                        {
                            "company": "Empty",
                            "role": "Description",
                            "date_applied": "2026-07-21",
                        }
                    )
                },
            )
            self.assertEqual(empty.status_code, 201, empty.text)

            unknown = client.post(
                "/api/jobs",
                data={
                    "payload": json.dumps(
                        {
                            "company": "Bad",
                            "role": "Metadata",
                            "date_applied": "2026-07-21",
                            "unknown": True,
                        }
                    )
                },
            )
            self.assertEqual(unknown.status_code, 422, unknown.text)

            with closing(sqlite3.connect(database)) as connection:
                stored_description = connection.execute(
                    "SELECT job_description FROM applications WHERE id = ?", (job_id,)
                ).fetchone()[0]
                categories = connection.execute(
                    "SELECT category FROM documents WHERE application_id = ?", (job_id,)
                ).fetchall()
                application_count = connection.execute(
                    "SELECT COUNT(*) FROM applications"
                ).fetchone()[0]
            self.assertEqual(stored_description, description)
            self.assertEqual(categories, [("evidence",)])
            self.assertEqual(application_count, 2)


if __name__ == "__main__":
    unittest.main()
