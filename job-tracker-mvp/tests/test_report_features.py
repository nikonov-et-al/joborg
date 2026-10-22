from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from pypdf import PdfReader

from jobtracker.markdown import write_markdown_with_frontmatter
from jobtracker.models import JobApplication
from jobtracker.repositories import build_application_service
from jobtracker.reports import build_report_data, report_data_to_markdown, render_report_pdf
from jobtracker.services import ProjectContext
from jobtracker.webapp import TOKEN_HEADER, create_app


def _client(service) -> TestClient:
    return TestClient(
        create_app(service, api_token="test-token"),
        base_url="http://127.0.0.1:8000",
        headers={TOKEN_HEADER: "test-token"},
    )


def _context(root: Path) -> ProjectContext:
    return ProjectContext(
        config={
            "applicant": {"name": "Jane Example"},
            "paths": {
                "input": str(root / "input"),
                "jobs": str(root / "jobs"),
                "reports": str(root / "reports"),
                "profile": str(root / "profile"),
            },
            "llm": {"model": "fake-model"},
            "export": {},
        },
        input_dir=root / "input",
        jobs_dir=root / "jobs",
        reports_dir=root / "reports",
        profile_dir=root / "profile",
        database=root / "jobtracker.sqlite3",
        files_dir=root / "files",
    )


class ReportFeatureTests(unittest.TestCase):
    @staticmethod
    def _write_job(jobs_dir: Path, date_applied: str, company: str, status: str = "applied") -> None:
        job_dir = jobs_dir / f"{date_applied}_{company.lower()}_role"
        job_dir.mkdir(parents=True)
        write_markdown_with_frontmatter(
            job_dir / "job.md",
            JobApplication(
                company=company,
                role="Role",
                location="Berlin",
                application_method="Company site",
                status=status,
                date_applied=date_applied,
            ).to_metadata(),
            "Body",
        )

    def test_empty_report_data_renders_pdf(self) -> None:
        with TemporaryDirectory() as temp:
            data = build_report_data(Path(temp), applicant_name="Jane Example")
            self.assertEqual(data.total, 0)
            markdown = report_data_to_markdown(data)
            self.assertIn("Total applications: **0**", markdown)
            self.assertIn("No applications found.", markdown)

            pdf = render_report_pdf(data)
            text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or ""
            self.assertIn("Total applications: 0", text)
            self.assertIn("No applications found.", text)

    def test_report_filters_support_all_year_month_and_legacy_month(self) -> None:
        with TemporaryDirectory() as temp:
            jobs_dir = Path(temp)
            self._write_job(jobs_dir, "2025-06-01", "Alpha")
            self._write_job(jobs_dir, "2026-06-25", "Beta", "rejected")
            self._write_job(jobs_dir, "2026-07-03", "Gamma")

            all_jobs = build_report_data(jobs_dir)
            june = build_report_data(jobs_dir, month_number=6)
            year = build_report_data(jobs_dir, year=2026)
            june_2026 = build_report_data(jobs_dir, year="2026", month_number="06")
            legacy = build_report_data(jobs_dir, month="2026-06")

            self.assertEqual(all_jobs.total, 3)
            self.assertEqual((all_jobs.year, all_jobs.month_number, all_jobs.period_label), ("", "", "All"))
            self.assertEqual(june.total, 2)
            self.assertEqual(june.period_label, "June (all years)")
            self.assertEqual(year.total, 2)
            self.assertEqual(year.period_label, "2026")
            self.assertEqual(june_2026.total, 1)
            self.assertEqual(june_2026.period_label, "June 2026")
            self.assertEqual(legacy.rows, june_2026.rows)
            self.assertEqual(legacy.month, "2026-06")
            self.assertEqual(legacy.rows[0].date, "25-06-2026")

    def test_markdown_and_pdf_use_slim_table_and_percentage_summary(self) -> None:
        with TemporaryDirectory() as temp:
            jobs_dir = Path(temp)
            self._write_job(jobs_dir, "2026-06-01", "Alpha")
            self._write_job(jobs_dir, "2026-06-02", "Beta", "rejected")
            self._write_job(jobs_dir, "2026-06-03", "Gamma", "rejected")
            data = build_report_data(jobs_dir)

            markdown = report_data_to_markdown(data)
            self.assertIn("| Date | Company | Role | Location | Status |", markdown)
            self.assertNotIn("| Method |", markdown)
            self.assertNotIn("| Evidence |", markdown)
            self.assertNotIn("| Folder |", markdown)
            self.assertIn("applied: **1** (33.3%)", markdown)
            self.assertIn("rejected: **2** (66.7%)", markdown)
            bars = [line.split("`")[1] for line in markdown.splitlines() if "`" in line]
            self.assertEqual([len(bar) for bar in bars], [20, 20])

            pdf = render_report_pdf(data)
            text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
            self.assertIn("applied: 1 (33.3%)", text)
            self.assertIn("rejected: 2 (66.7%)", text)
            self.assertNotIn("Method", text)
            self.assertNotIn("Evidence", text)
            self.assertNotIn(str(jobs_dir), text)

    def test_report_api_returns_data_and_pdf_download(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = _context(root)
            service = build_application_service(context)
            service.create_job(
                JobApplication(
                    company="Example Corp",
                    role="Backend Engineer",
                    location="Berlin",
                    application_method="Company site",
                    status="applied",
                    date_applied="2026-06-25",
                ),
                "Description",
            )
            client = _client(service)
            report = client.post("/api/report?month=2026-06")
            pdf = client.get("/api/report/pdf?month=2026-06")
            saved = client.post("/api/report/pdf/save?month=2026-06")

            self.assertEqual(report.status_code, 200)
            payload = report.json()
            self.assertEqual(payload["artifact_id"], "2026-06")
            self.assertEqual(payload["data"]["total"], 1)
            self.assertEqual(payload["data"]["rows"][0]["company"], "Example Corp")
            self.assertIn("Example Corp", payload["content"])

            self.assertEqual(pdf.status_code, 200)
            self.assertEqual(pdf.headers["content-type"], "application/pdf")
            self.assertIn('attachment; filename="report-2026-06.pdf"', pdf.headers["content-disposition"])
            text = PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text() or ""
            self.assertIn("Example Corp", text)
            saved_path = Path(saved.json()["path"])
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved_path, context.reports_dir / "report-2026-06.pdf")
            saved_text = PdfReader(str(saved_path)).pages[0].extract_text() or ""
            self.assertIn("Example Corp", saved_text)

    def test_report_api_supports_independent_period_filters_and_paths(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = _context(root)
            service = build_application_service(context)
            for date_applied, company in (
                ("2025-06-01", "Alpha"),
                ("2026-06-25", "Beta"),
                ("2026-07-03", "Gamma"),
            ):
                service.create_job(
                    JobApplication(
                        company=company,
                        role="Role",
                        date_applied=date_applied,
                    ),
                    "Description",
                )
            client = _client(service)
            all_jobs = client.post("/api/report")
            june = client.post("/api/report?month_number=06")
            year = client.post("/api/report?year=2026")
            june_2026 = client.post("/api/report?year=2026&month_number=06")
            legacy = client.post("/api/report?month=2026-06")
            pdf = client.get("/api/report/pdf?month_number=06")
            saved_pdf = client.post("/api/report/pdf/save?year=2026&month_number=06")
            invalid = client.post("/api/report?month_number=13")

            self.assertEqual(all_jobs.json()["data"]["total"], 3)
            self.assertEqual(all_jobs.json()["artifact_id"], "all")
            self.assertEqual(june.json()["data"]["total"], 2)
            self.assertEqual(
                june.json()["artifact_id"],
                "all-years-06",
            )
            self.assertEqual(year.json()["data"]["total"], 2)
            self.assertEqual(year.json()["artifact_id"], "2026")
            self.assertEqual(june_2026.json()["data"]["total"], 1)
            self.assertEqual(
                june_2026.json()["artifact_id"],
                "2026-06",
            )
            self.assertEqual(legacy.json()["data"]["total"], 1)
            self.assertIn('filename="report-all-years-06.pdf"', pdf.headers["content-disposition"])
            self.assertEqual(
                Path(saved_pdf.json()["path"]),
                context.reports_dir / "report-2026-06.pdf",
            )
            saved_text = "\n".join(
                page.extract_text() or "" for page in PdfReader(saved_pdf.json()["path"]).pages
            )
            self.assertIn("Beta", saved_text)
            self.assertNotIn("Alpha", saved_text)
            self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
