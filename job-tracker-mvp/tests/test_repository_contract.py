from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobtracker.models import JobApplication
from jobtracker.repositories import LegacyMarkdownRepository
from jobtracker.services import ProjectContext, UploadedAsset


class LegacyMarkdownRepositoryContractTests(unittest.TestCase):
    def test_legacy_jobs_are_read_only_but_profile_helpers_remain_available(self) -> None:
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
            job = JobApplication(company="Example", role="Engineer")
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                repository.create(job, "Original description")
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                repository.update("example")
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                repository.save_cv_draft("example", "# Draft")

            profile = repository.save_profile(
                {"personal": {"name": "Test User"}, "skills": ["Python"], "experience": "Built systems"}
            )
            self.assertEqual(profile["skills"], ["Python"])
            self.assertEqual(repository.store_profile_cv(UploadedAsset("../resume.pdf", b"%PDF")), "resume.pdf")

    def test_record_ids_cannot_escape_jobs_directory(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            context = ProjectContext({}, root / "input", root / "jobs", root / "reports", root / "profile")
            repository = LegacyMarkdownRepository(context)

            with self.assertRaises(LookupError):
                repository.get("../outside")


if __name__ == "__main__":
    unittest.main()
