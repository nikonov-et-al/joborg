from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from jobtracker.models import JobApplication
from jobtracker.markdown import write_markdown_with_frontmatter
from jobtracker.services import ProjectContext
from jobtracker.webapp import app


def _make_context(root: Path) -> ProjectContext:
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
    )


class CvPdfApiTests(unittest.TestCase):
    def test_cv_pdf_download_uses_current_editor_content(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = _make_context(root)
            job_dir = context.jobs_dir / "2026-06-25_example_backend-engineer"
            job_dir.mkdir(parents=True)
            write_markdown_with_frontmatter(
                job_dir / "job.md",
                JobApplication(company="Example", role="Backend Engineer").to_metadata(),
                "Body",
            )
            draft_path = job_dir / "cv" / "generated_cv.md"
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text("saved draft", encoding="utf-8")

            captured: list[tuple[str, str, str]] = []

            def fake_render(markdown_text: str, applicant_name: str = "", job_title: str = "") -> bytes:
                captured.append((markdown_text, applicant_name, job_title))
                return b"%PDF-1.4\n%fake\n"

            with patch("jobtracker.webapp.load_project_context", return_value=context), patch(
                "jobtracker.webapp.render_cv_pdf",
                side_effect=fake_render,
            ):
                client = TestClient(app)
                response = client.post(
                    "/api/jobs/2026-06-25_example_backend-engineer/cv-pdf",
                    json={"content": "# Current draft"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/pdf")
            self.assertIn('attachment; filename="generated_cv.pdf"', response.headers["content-disposition"])
            self.assertEqual(captured, [("# Current draft", "Jane Example", "Backend Engineer")])
            self.assertEqual(draft_path.read_text(encoding="utf-8"), "saved draft")

    def test_cv_pdf_download_falls_back_to_saved_draft(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = _make_context(root)
            job_dir = context.jobs_dir / "2026-06-25_example_backend-engineer"
            job_dir.mkdir(parents=True)
            write_markdown_with_frontmatter(
                job_dir / "job.md",
                JobApplication(company="Example", role="Backend Engineer").to_metadata(),
                "Body",
            )
            draft_path = job_dir / "cv" / "generated_cv.md"
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text("# Saved draft", encoding="utf-8")

            captured: list[str] = []

            def fake_render(markdown_text: str, applicant_name: str = "", job_title: str = "") -> bytes:
                captured.append(markdown_text)
                return b"%PDF-1.4\n%fake\n"

            with patch("jobtracker.webapp.load_project_context", return_value=context), patch(
                "jobtracker.webapp.render_cv_pdf",
                side_effect=fake_render,
            ):
                client = TestClient(app)
                response = client.post("/api/jobs/2026-06-25_example_backend-engineer/cv-pdf")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(captured, ["# Saved draft"])

    def test_cv_pdf_download_validates_missing_empty_and_overflow(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = _make_context(root)
            job_dir = context.jobs_dir / "2026-06-25_example_backend-engineer"
            job_dir.mkdir(parents=True)
            write_markdown_with_frontmatter(
                job_dir / "job.md",
                JobApplication(company="Example", role="Backend Engineer").to_metadata(),
                "Body",
            )

            with patch("jobtracker.webapp.load_project_context", return_value=context), patch(
                "jobtracker.webapp.render_cv_pdf",
                side_effect=ValueError("CV content does not fit one page"),
            ):
                client = TestClient(app)
                overflow = client.post(
                    "/api/jobs/2026-06-25_example_backend-engineer/cv-pdf",
                    json={"content": "# Too much"},
                )

            self.assertEqual(overflow.status_code, 422)
            self.assertIn("CV content does not fit one page", overflow.text)

            with patch("jobtracker.webapp.load_project_context", return_value=context):
                client = TestClient(app)
                empty = client.post("/api/jobs/2026-06-25_example_backend-engineer/cv-pdf")

            self.assertEqual(empty.status_code, 422)
            self.assertIn("CV content is empty", empty.text)

            with patch("jobtracker.webapp.load_project_context", return_value=context):
                client = TestClient(app)
                missing = client.post("/api/jobs/missing/cv-pdf", json={"content": "# Draft"})

            self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
