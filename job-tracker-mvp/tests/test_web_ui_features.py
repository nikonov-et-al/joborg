from pathlib import Path
import unittest


STATIC_DIR = Path(__file__).parents[1] / "jobtracker" / "web" / "static"


class WebUiFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        cls.styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
