from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from jobtracker.markdown import read_markdown_with_frontmatter
from jobtracker.models import JobApplication
from jobtracker.services import ProjectContext, create_job_record
from jobtracker.webapp import app


class JobEditApiTests(unittest.TestCase):
    def _context(self, root: Path) -> ProjectContext:
        return ProjectContext(
            config={"applicant": {"name": "Test User"}},
            input_dir=root / "input",
            jobs_dir=root / "jobs",
            reports_dir=root / "reports",
            profile_dir=root / "profile",
        )

    def _seed_job(self, context: ProjectContext) -> tuple[str, Path]:
        job = JobApplication(
            company="Old Company",
            role="Old Role",
            date_applied="2026-06-25",
            status="applied",
            status_date="2026-06-25",
            attachments=["attachments/original.txt"],
            evidence=["evidence/proof.pdf"],
            cv_file="cv/original.pdf",
        )
        job_dir = context.jobs_dir / job.folder_name()
        create_job_record(job_dir, job, "Original description")
        (job_dir / "attachments" / "original.txt").write_text("keep", encoding="utf-8")
        (job_dir / "evidence" / "proof.pdf").write_bytes(b"proof")
        (job_dir / "cv" / "original.pdf").write_bytes(b"cv")
        return job_dir.name, job_dir

    def test_full_edit_preserves_identity_files_and_body(self) -> None:
        with TemporaryDirectory() as temp:
            context = self._context(Path(temp))
            job_id, job_dir = self._seed_job(context)
            _old_metadata, old_body = read_markdown_with_frontmatter(job_dir / "job.md")
            payload = {
                "company": "New Company",
                "role": "New Role",
                "location": "Berlin",
                "job_url": "https://example.test/job",
                "source": "Direct",
                "application_method": "company website",
                "date_applied": "2026-07-01",
                "status": "interview_invited",
                "status_date": "2026-07-10",
                "contact_person": "Ada",
                "contact_email": "ada@example.test",
                "required_skills": ["Python"],
                "nice_to_have_skills": ["FastAPI"],
                "language_requirements": ["German"],
                "summary": "Updated",
                "next_action": "Prepare",
                "next_action_date": "2026-07-20",
                "relevant_for_arbeitsagentur": False,
                "job_description": "Updated description",
                "note": "Edited in All Jobs",
            }

            with patch("jobtracker.webapp.load_project_context", return_value=context), patch(
                "jobtracker.webapp.write_project_report",
                return_value=context.reports_dir / "report.md",
            ) as report:
                response = TestClient(app).patch(f"/api/jobs/{job_id}", json=payload)

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["id"], job_id)
            self.assertEqual(response.json()["company"], "New Company")
            metadata, body = read_markdown_with_frontmatter(job_dir / "job.md")
            self.assertEqual(metadata["date_applied"], "2026-07-01")
            self.assertEqual(metadata["attachments"], ["attachments/original.txt"])
            self.assertEqual(metadata["evidence"], ["evidence/proof.pdf"])
            self.assertEqual(metadata["cv_file"], "cv/original.pdf")
            self.assertIn(old_body.strip(), body)
            self.assertIn("- Status: interview_invited", body)
            self.assertIn("- Status date: 10-07-2026", body)
            self.assertIn("- Note: Edited in All Jobs", body)
            self.assertEqual(
                (job_dir / "job_description.md").read_text(encoding="utf-8"),
                "# Job description\n\nUpdated description\n",
            )
            self.assertEqual((job_dir / "attachments" / "original.txt").read_text(), "keep")
            self.assertTrue((job_dir / "evidence" / "proof.pdf").exists())
            self.assertTrue((job_dir / "cv" / "original.pdf").exists())
            self.assertFalse((context.jobs_dir / "2026-07-01_new-company_new-role").exists())
            report.assert_called_once_with(context)

    def test_rejects_unknown_bookkeeping_fields_and_invalid_values(self) -> None:
        with TemporaryDirectory() as temp:
            context = self._context(Path(temp))
            job_id, job_dir = self._seed_job(context)
            original = (job_dir / "job.md").read_text(encoding="utf-8")
            client = TestClient(app)

            invalid_payloads = [
                {"attachments": []},
                {"folder": "elsewhere"},
                {"company": " "},
                {"role": ""},
                {"date_applied": "01-07-2026"},
                {"status_date": "2026-02-30"},
                {"status": "maybe"},
                {"required_skills": "Python"},
                {"relevant_for_arbeitsagentur": "false"},
                {"job_description": 42},
                {"note": 42},
            ]
            with patch("jobtracker.webapp.load_project_context", return_value=context), patch(
                "jobtracker.webapp.write_project_report"
            ) as report:
                for payload in invalid_payloads:
                    with self.subTest(payload=payload):
                        response = client.patch(f"/api/jobs/{job_id}", json=payload)
                        self.assertEqual(response.status_code, 422, response.text)

            self.assertEqual((job_dir / "job.md").read_text(encoding="utf-8"), original)
            report.assert_not_called()

    def test_status_and_note_payload_remains_supported(self) -> None:
        with TemporaryDirectory() as temp:
            context = self._context(Path(temp))
            job_id, job_dir = self._seed_job(context)
            with patch("jobtracker.webapp.load_project_context", return_value=context), patch(
                "jobtracker.webapp.write_project_report"
            ):
                response = TestClient(app).patch(
                    f"/api/jobs/{job_id}",
                    json={"status": "rejected", "note": "No fit"},
                )

            self.assertEqual(response.status_code, 200, response.text)
            metadata, body = read_markdown_with_frontmatter(job_dir / "job.md")
            self.assertEqual(metadata["status"], "rejected")
            self.assertRegex(metadata["status_date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertIn("- Note: No fit", body)


if __name__ == "__main__":
    unittest.main()
