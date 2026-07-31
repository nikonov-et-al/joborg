from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


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
        self.assertTrue(privacy.path_violation(".ua/knowledge-graph.json"))
        self.assertTrue(privacy.path_violation("nested/.cache/state.json"))
        self.assertTrue(privacy.path_violation("job-tracker-mvp/report.md"))
        self.assertTrue(privacy.path_violation("report.md"))
        self.assertFalse(privacy.path_violation(".github/workflows/test.yml"))
        self.assertTrue(privacy.path_violation("resume.pdf"))
        self.assertTrue(privacy.path_violation("resume.docx"))
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

    def test_code_assignment_is_not_treated_as_config_secret(self) -> None:
        failures = privacy.source_failures(
            "HEAD", ["module.py"], lambda _: b"api_key = settings.api_key"
        )
        self.assertEqual(failures, [])

    def test_shared_refs_exclude_codex_refs(self) -> None:
        refs = [
            "refs/codex/turn-diffs/checkpoint",
            "refs/heads/main",
            "refs/remotes/origin/main",
            "refs/tags/v1",
        ]
        with patch.object(privacy, "all_ref_names", return_value=refs):
            self.assertEqual(privacy.refs_for_scope("shared-refs"), refs[1:])
            self.assertEqual(privacy.refs_for_scope("all-local-refs"), refs)

    def test_default_fails_for_forbidden_head_with_clean_staged_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "privacy-test@example.invalid")
            self._git(repo, "config", "user.name", "Privacy Test")
            (repo / "safe.txt").write_text("safe\n", encoding="utf-8")
            self._git(repo, "add", "safe.txt")
            self._git(repo, "commit", "-q", "-m", "safe")

            sensitive_value = "must-not-appear-in-check-output"
            (repo / "report.pdf").write_text(sensitive_value, encoding="utf-8")
            self._git(repo, "add", "report.pdf")
            self._git(repo, "commit", "-q", "-m", "forbidden")

            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("HEAD: report.pdf: sensitive/generated file type", result.stderr)
            self.assertNotIn(sensitive_value, result.stderr)
            self.assertEqual(self._git(repo, "diff", "--cached", "--name-only"), "")

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        return subprocess.check_output(
            ("git", *args), cwd=repo, text=True, encoding="utf-8"
        ).strip()


if __name__ == "__main__":
    unittest.main()
