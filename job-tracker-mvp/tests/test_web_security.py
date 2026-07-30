from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from jobtracker.repositories import build_application_service
from jobtracker.services import load_project_context
from jobtracker.webapp import TOKEN_HEADER, create_app


class WebSecurityTests(unittest.TestCase):
    def _client(self) -> TestClient:
        self.temp = TemporaryDirectory()
        context = load_project_context(data_dir=Path(self.temp.name))
        return TestClient(
            create_app(build_application_service(context), api_token="test-token"),
            base_url="http://127.0.0.1:8000",
        )

    def tearDown(self) -> None:
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def test_token_origin_and_security_headers_protect_loopback_api(self) -> None:
        with self._client() as client:
            self.assertEqual(client.get("/api/jobs").status_code, 401)
            forbidden = client.get(
                "/api/jobs",
                headers={TOKEN_HEADER: "test-token", "Origin": "https://example.invalid"},
            )
            self.assertEqual(forbidden.status_code, 403)
            response = client.get("/api/jobs", headers={TOKEN_HEADER: "test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])

    def test_public_settings_excludes_secrets_and_paths(self) -> None:
        with self._client() as client:
            response = client.get("/api/config", headers={TOKEN_HEADER: "test-token"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"applicant_name", "llm"})
        self.assertNotIn("api_key", str(payload))
        self.assertNotIn("paths", payload)


if __name__ == "__main__":
    unittest.main()
