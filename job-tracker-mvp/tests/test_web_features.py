from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jobtracker.services import UploadedAsset, load_profile, save_profile, store_profile_cv
from jobtracker.webapp import STATIC_DIR, app, root


class ProfileTests(unittest.TestCase):
    def test_profile_round_trip_and_cv_upload(self) -> None:
        with TemporaryDirectory() as temp:
            profile_dir = Path(temp)
            saved = save_profile(
                profile_dir,
                {
                    "personal": {"name": "Test User", "email": "test@example.com"},
                    "skills": ["Python", "FastAPI"],
                    "experience": "Built useful systems.",
                },
            )
            self.assertEqual(saved["personal"]["name"], "Test User")
            self.assertEqual(saved["skills"], ["Python", "FastAPI"])
            self.assertEqual(saved["experience"], "Built useful systems.\n")

            filename = store_profile_cv(
                profile_dir,
                UploadedAsset(filename="../resume.pdf", content=b"%PDF"),
            )
            self.assertEqual(filename, "resume.pdf")
            self.assertEqual(load_profile(profile_dir)["cv_files"], ["resume.pdf"])

    def test_profile_cv_rejects_unsupported_type(self) -> None:
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "PDF or DOCX"):
                store_profile_cv(
                    Path(temp),
                    UploadedAsset(filename="resume.exe", content=b"bad"),
                )


class RouteTests(unittest.TestCase):
    def test_required_api_routes_exist(self) -> None:
        paths = {route.path for route in app.routes}
        self.assertIn("/api/profile", paths)
        self.assertIn("/api/profile/cvs", paths)
        self.assertIn("/api/jobs/{job_id}/cv-draft", paths)
        self.assertIn("/api/report", paths)

    def test_root_serves_uncached_versioned_ui(self) -> None:
        response = root()
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("/static/app.js?v=2", html)
        self.assertIn("/static/styles.css?v=2", html)


if __name__ == "__main__":
    unittest.main()
