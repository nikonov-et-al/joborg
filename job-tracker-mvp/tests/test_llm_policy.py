from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

from jobtracker.llm import (
    LLMError,
    classify_llm_endpoint,
    generate_cv_draft_with_llm,
    llm_endpoint_policy,
    test_llm as run_llm_test,
)


class LlmPolicyTests(unittest.TestCase):
    def test_classifies_loopback_and_remote_endpoints(self) -> None:
        self.assertEqual(classify_llm_endpoint("http://localhost:11434/v1"), "loopback")
        self.assertEqual(classify_llm_endpoint("http://127.0.0.1:11434/v1"), "loopback")
        self.assertEqual(classify_llm_endpoint("http://[::1]:11434/v1"), "loopback")
        self.assertEqual(classify_llm_endpoint("https://llm.example.test/v1"), "remote")

    def test_rejects_invalid_endpoint_forms(self) -> None:
        for endpoint in ("", "ftp://localhost", "https://user@example.test", "https:///v1", "https://example.test?x=1"):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(LLMError, "LLM endpoint") as caught:
                classify_llm_endpoint(endpoint)
            self.assertEqual(caught.exception.code, "llm_endpoint_invalid")

    def test_remote_policy_requires_https_consent_confirmation_and_env_credential(self) -> None:
        config = {"llm": {"base_url": "http://llm.example.test/v1"}}
        with self.assertRaises(LLMError) as caught:
            llm_endpoint_policy(config)
        self.assertEqual(caught.exception.code, "llm_remote_https_required")

        config["llm"]["base_url"] = "https://llm.example.test/v1"
        with self.assertRaises(LLMError) as caught:
            llm_endpoint_policy(config)
        self.assertEqual(caught.exception.code, "llm_remote_consent_required")

        config["llm"]["remote_disclosure_consent"] = True
        with self.assertRaises(LLMError) as caught:
            llm_endpoint_policy(config, sends_personal_content=True)
        self.assertEqual(caught.exception.code, "llm_remote_confirmation_required")

        with self.assertRaises(LLMError) as caught, patch.dict(os.environ, {}, clear=True):
            llm_endpoint_policy(config, sends_personal_content=True, remote_confirmation=True)
        self.assertEqual(caught.exception.code, "llm_remote_credential_missing")

        with patch.dict(os.environ, {"JOBTRACKER_LLM_API_KEY": "env-key"}, clear=True):
            self.assertEqual(
                llm_endpoint_policy(config, sends_personal_content=True, remote_confirmation=True),
                ("https://llm.example.test/v1", "env-key"),
            )

    def test_remote_gate_happens_before_provider_construction(self) -> None:
        class FakeOpenAI:
            def __init__(self, **_kwargs: object) -> None:
                raise AssertionError("provider must not be constructed")

        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}):
            with self.assertRaises(LLMError) as caught:
                generate_cv_draft_with_llm(
                    profile={"name": "Jane"}, job_description="Role", job={},
                    config={"llm": {"model": "fake", "base_url": "https://llm.example.test/v1"}},
                )
        self.assertEqual(caught.exception.code, "llm_remote_consent_required")

    def test_provider_errors_are_not_returned_verbatim(self) -> None:
        class FakeOpenAI:
            def __init__(self, **_kwargs: object) -> None:
                self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

            @staticmethod
            def _create(**_kwargs: object) -> object:
                raise RuntimeError("secret provider detail")

        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}):
            ok, message = run_llm_test({"llm": {"model": "fake", "base_url": "http://localhost"}})
        self.assertFalse(ok)
        self.assertEqual(message, "llm_unavailable: LLM provider is unavailable.")


if __name__ == "__main__":
    unittest.main()
