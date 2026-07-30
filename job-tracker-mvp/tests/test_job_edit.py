from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from jobtracker.models import JobApplication
from jobtracker.repositories import build_application_service
from jobtracker.services import ProjectContext
from jobtracker.webapp import create_app


class JobEditApiTests(unittest.TestCase):
    def _seed_job(self, root: Path):
        context = ProjectContext(
            config={"applicant": {"name": "Test User"}},
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
                company="Old Company",
                role="Old Role",
                date_applied="2026-06-25",
                status="applied",
                status_date="2026-06-25",
            ),
            "Original description",
            [
                SimpleNamespace(
                    filename="original.txt",
                    content=b"keep",
                    category="attachments",
                ),
                SimpleNamespace(
                    filename="proof.pdf",
                    content=b"proof",
                    category="evidence",
                ),
                SimpleNamespace(
                    filename="original.pdf",
                    content=b"cv",
                    category="cv",
                ),
            ],
        )
        return context, service, result.application_id

    def test_full_edit_preserves_identity_files_and_body(self) -> None:
        with TemporaryDirectory() as temp:
            context, service, job_id = self._seed_job(Path(temp))
            original = service.get_job(job_id)
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

            response = TestClient(create_app(service)).patch(
                f"/api/jobs/{job_id}",
                json=payload,
            )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["id"], job_id)
            self.assertEqual(response.json()["company"], "New Company")
            updated = service.get_job(job_id)
            self.assertEqual(updated.job.date_applied, "2026-07-01")
            self.assertEqual(updated.job.required_skills, ["Python"])
            self.assertEqual(updated.job_description, "Updated description")
            self.assertIn(original.body.strip(), updated.body)
            self.assertIn("- Status: interview_invited", updated.body)
            self.assertIn("- Status date: 2026-07-10", updated.body)
            self.assertIn("- Note: Edited in All Jobs", updated.body)
            self.assertEqual(
                {document.category for document in updated.documents},
                {"attachments", "evidence", "cv"},
            )
            with closing(sqlite3.connect(context.database)) as connection:
                paths = [
                    context.files_dir / row[0]
                    for row in connection.execute(
                        "SELECT stored_path FROM documents WHERE application_id = ?",
                        (job_id,),
                    )
                ]
            self.assertEqual(
                sorted(path.read_bytes() for path in paths),
                sorted([b"keep", b"proof", b"cv"]),
            )

    def test_rejects_unknown_bookkeeping_fields_and_invalid_values(self) -> None:
        with TemporaryDirectory() as temp:
            _context, service, job_id = self._seed_job(Path(temp))
            original = service.get_job(job_id)
            client = TestClient(create_app(service))
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
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    response = client.patch(f"/api/jobs/{job_id}", json=payload)
                    self.assertEqual(response.status_code, 422, response.text)

            final = service.get_job(job_id)
            self.assertEqual(final.job.to_metadata(), original.job.to_metadata())
            self.assertEqual(final.body, original.body)
            self.assertEqual(final.job_description, original.job_description)

    def test_status_and_note_payload_remains_supported(self) -> None:
        with TemporaryDirectory() as temp:
            _context, service, job_id = self._seed_job(Path(temp))
            response = TestClient(create_app(service)).patch(
                f"/api/jobs/{job_id}",
                json={"status": "rejected", "note": "No fit"},
            )

            self.assertEqual(response.status_code, 200, response.text)
            details = service.get_job(job_id)
            self.assertEqual(details.job.status, "rejected")
            self.assertRegex(details.job.status_date, r"^\d{4}-\d{2}-\d{2}$")
            self.assertIn("- Note: No fit", details.body)


if __name__ == "__main__":
    unittest.main()
