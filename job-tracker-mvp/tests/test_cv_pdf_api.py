from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from jobtracker.models import JobApplication
from jobtracker.repositories import build_application_service
from jobtracker.services import ProjectContext
from jobtracker.webapp import create_app


def _service(root: Path):
    context = ProjectContext(
        config={
            "applicant": {"name": "Jane Example"},
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
    service = build_application_service(context)
    result = service.create_job(
        JobApplication(company="Example", role="Backend Engineer"),
        "Description",
    )
    return service, result.application_id


class CvPdfApiTests(unittest.TestCase):
    def test_cv_pdf_download_uses_current_editor_content(self) -> None:
        with TemporaryDirectory() as temp:
            service, job_id = _service(Path(temp))
            service.save_cv_draft(job_id, "saved draft")
            captured: list[tuple[str, str, str]] = []

            def fake_render(
                markdown_text: str,
                applicant_name: str = "",
                job_title: str = "",
            ) -> bytes:
                captured.append((markdown_text, applicant_name, job_title))
                return b"%PDF-1.4\n%fake\n"

            service.documents._render = fake_render
            response = TestClient(create_app(service)).post(
                f"/api/jobs/{job_id}/cv-pdf",
                json={"content": "# Current draft"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/pdf")
            self.assertIn(
                'attachment; filename="generated_cv.pdf"',
                response.headers["content-disposition"],
            )
            self.assertEqual(
                captured,
                [("# Current draft", "Jane Example", "Backend Engineer")],
            )
            self.assertEqual(service.get_job(job_id).cv_draft, "saved draft\n")

    def test_cv_pdf_download_falls_back_to_saved_draft(self) -> None:
        with TemporaryDirectory() as temp:
            service, job_id = _service(Path(temp))
            service.save_cv_draft(job_id, "# Saved draft")
            captured: list[str] = []

            def fake_render(
                markdown_text: str,
                applicant_name: str = "",
                job_title: str = "",
            ) -> bytes:
                captured.append(markdown_text)
                return b"%PDF-1.4\n%fake\n"

            service.documents._render = fake_render
            response = TestClient(create_app(service)).post(
                f"/api/jobs/{job_id}/cv-pdf"
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(captured, ["# Saved draft"])

    def test_cv_pdf_download_validates_missing_empty_and_overflow(self) -> None:
        with TemporaryDirectory() as temp:
            service, job_id = _service(Path(temp))
            client = TestClient(create_app(service))

            def fail_render(*args, **kwargs):
                raise ValueError("CV content does not fit one page")

            service.documents._render = fail_render
            overflow = client.post(
                f"/api/jobs/{job_id}/cv-pdf",
                json={"content": "# Too much"},
            )
            self.assertEqual(overflow.status_code, 422)
            self.assertIn("CV content does not fit one page", overflow.text)

            empty = client.post(f"/api/jobs/{job_id}/cv-pdf")
            self.assertEqual(empty.status_code, 422)
            self.assertIn("CV content is empty", empty.text)

            missing = client.post(
                "/api/jobs/missing/cv-pdf",
                json={"content": "# Draft"},
            )
            self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
