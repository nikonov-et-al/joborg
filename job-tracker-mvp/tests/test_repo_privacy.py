from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_repo_privacy.py"
SPEC = spec_from_file_location("check_repo_privacy", SCRIPT)
assert SPEC and SPEC.loader
privacy = module_from_spec(SPEC)
SPEC.loader.exec_module(privacy)


class RepositoryPrivacyTests(unittest.TestCase):
    def test_forbidden_paths_and_synthetic_fixture_exception(self) -> None:
        self.assertTrue(privacy.path_violation("job-tracker-mvp/jobs/acme/job.md"))
        self.assertTrue(privacy.path_violation("job-tracker-mvp/config.yml"))
        self.assertTrue(privacy.path_violation("package/__pycache__/module.pyc"))
        self.assertTrue(privacy.path_violation("resume.pdf"))
        self.assertTrue(privacy.path_violation("package.egg-info/PKG-INFO"))
        self.assertFalse(
            privacy.path_violation(
                "job-tracker-mvp/tests/fixtures/synthetic-resume.pdf"
            )
        )

    def test_secret_detection_reports_labels_not_values(self) -> None:
        assignment = "api_" + "key: not-a-placeholder"
        token = "sk-" + ("x" * 24)
        self.assertEqual(
            privacy.secret_violations(f"{assignment}\nvalue: {token}"),
            {"non-placeholder api_key", "OpenAI token"},
        )
        self.assertFalse(privacy.secret_violations("api_key: local-not-needed"))


if __name__ == "__main__":
    unittest.main()
