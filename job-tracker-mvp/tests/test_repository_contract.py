from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobtracker.markdown import read_markdown_with_frontmatter
from jobtracker.models import JobApplication
from jobtracker.repositories import LegacyMarkdownRepository
from jobtracker.services import ProjectContext, UploadedAsset


class LegacyMarkdownRepositoryContractTests(unittest.TestCase):
    def test_create_read_update_attach_profile_and_draft_round_trip(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = ProjectContext(
                config={"applicant": {"name": "Test User"}},
                input_dir=root / "input",
                jobs_dir=root / "jobs",
                reports_dir=root / "reports",
                profile_dir=root / "profile",
            )
            repository = LegacyMarkdownRepository(context)
            job = JobApplication(
                company="Example",
                role="Engineer",
                date_applied="2026-07-18",
            )

            folder, created, stored = repository.create(
                job,
                "Original description",
                [UploadedAsset("proof.txt", b"proof", "evidence")],
            )

            self.assertEqual(folder.name, job.folder_name())
            self.assertEqual(created.evidence, ["evidence/proof.txt"])
            self.assertEqual(stored, ["evidence/proof.txt"])
            self.assertEqual(repository.get(folder.name).job_description, "# Job description\n\nOriginal description\n")

            _, updated, attached = repository.update(
                folder.name,
                [UploadedAsset("reply.pdf", b"reply", "attachments")],
                status="interview_invited",
                status_date="2026-07-18",
                category="attachments",
                note="Invite received",
            )

            self.assertEqual(updated.status, "interview_invited")
            self.assertEqual(attached, ["attachments/reply.pdf"])
            metadata, body = read_markdown_with_frontmatter(folder / "job.md")
            self.assertEqual(metadata["attachments"], ["attachments/reply.pdf"])
            self.assertIn("- Category: attachments", body)
            self.assertIn("- Note: Invite received", body)

            profile = repository.save_profile(
                {"personal": {"name": "Test User"}, "skills": ["Python"], "experience": "Built systems"}
            )
            self.assertEqual(profile["skills"], ["Python"])
            self.assertEqual(repository.store_profile_cv(UploadedAsset("../resume.pdf", b"%PDF")), "resume.pdf")
            self.assertEqual(repository.save_cv_draft(folder.name, "# Draft"), "cv/generated_cv.md")
            self.assertEqual(repository.get(folder.name).cv_draft, "# Draft\n")

    def test_record_ids_cannot_escape_jobs_directory(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = ProjectContext({}, root / "input", root / "jobs", root / "reports", root / "profile")
            repository = LegacyMarkdownRepository(context)

            with self.assertRaises(LookupError):
                repository.get("../outside")


if __name__ == "__main__":
    unittest.main()
