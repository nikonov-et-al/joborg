from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from jobtracker.llm import build_cv_draft_prompt, generate_cv_draft_with_llm


class CvPromptTests(unittest.TestCase):
    def test_cv_prompt_is_constrained_and_single_call(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeOpenAI:
            def __init__(self, **_kwargs: object) -> None:
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create)
                )

            def _create(self, **kwargs: object) -> object:
                calls.append(kwargs)
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            message=types.SimpleNamespace(content="```markdown\n# Draft\n```")
                        )
                    ]
                )

        fake_openai = types.SimpleNamespace(OpenAI=FakeOpenAI)

        prompt = build_cv_draft_prompt(
            profile={"name": "Jane Example", "skills": ["Python"]},
            job_description="Role needs strong Python and FastAPI skills.",
            job={"role": "Backend Engineer", "company": "Example GmbH"},
        )
        self.assertIn("Return Markdown only.", prompt)
        self.assertIn("No tables, icons, or images.", prompt)
        self.assertIn(
            "Use exactly these sections in this order: Summary, Experience, Education, Skills, Projects, Languages.",
            prompt,
        )
        self.assertIn("## Summary, ## Experience, ## Education, ## Skills, ## Projects, ## Languages", prompt)
        self.assertIn("Do not include Certifications, Languages & Certifications, or any other section.", prompt)
        self.assertIn("Summary: max 350 characters.", prompt)
        self.assertIn("Experience: max 3 jobs; max 4 bullets per job", prompt)
        self.assertIn("max 120 characters per bullet", prompt)
        self.assertIn("Bold employer names in Experience", prompt)
        self.assertIn("Education: max 2 entries", prompt)
        self.assertIn("bold university names", prompt)
        self.assertIn("Skills: bullet list grouped by subtype", prompt)
        self.assertIn("**ML/Data:**", prompt)
        self.assertIn("Include all job-required skills truthfully supported by PROFILE facts", prompt)
        self.assertIn("Projects: max 2 bullets", prompt)
        self.assertIn("Languages: one compact line.", prompt)
        self.assertIn("body text at 11pt", prompt)
        self.assertIn("margins of 8-10mm", prompt)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            result = generate_cv_draft_with_llm(
                profile={"name": "Jane Example", "skills": ["Python"]},
                job_description="Role needs strong Python and FastAPI skills.",
                job={"role": "Backend Engineer", "company": "Example GmbH"},
                config={"llm": {"model": "fake-model", "base_url": "http://localhost", "api_key": "x"}},
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "fake-model")
        self.assertEqual(calls[0]["temperature"], 0.2)
        self.assertEqual(calls[0]["messages"][0]["role"], "system")
        self.assertIn("You write truthful CVs.", calls[0]["messages"][0]["content"])
        self.assertIn("Never include Certifications", calls[0]["messages"][0]["content"])
        self.assertIn("Return Markdown only.", calls[0]["messages"][1]["content"])
        self.assertEqual(result, "# Draft\n")


if __name__ == "__main__":
    unittest.main()
