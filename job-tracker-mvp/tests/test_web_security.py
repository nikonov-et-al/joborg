from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from jobtracker.repositories import build_application_service
from jobtracker.models import JobApplication
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
            missing = client.get("/api/jobs")
            self.assertEqual(missing.status_code, 401)
            forbidden = client.get(
                "/api/jobs",
                headers={TOKEN_HEADER: "test-token", "Origin": "https://example.invalid"},
            )
            self.assertEqual(forbidden.status_code, 403)
            response = client.get("/api/jobs", headers={TOKEN_HEADER: "test-token"})
        self.assertEqual(response.status_code, 200)
        for secured in (missing, forbidden, response):
            self.assertEqual(secured.headers["x-content-type-options"], "nosniff")
            self.assertEqual(secured.headers["x-frame-options"], "DENY")
            self.assertEqual(secured.headers["referrer-policy"], "no-referrer")
            self.assertIn("default-src 'self'", secured.headers["content-security-policy"])

    def test_json_multipart_and_binary_routes_reject_missing_or_wrong_tokens(self) -> None:
        with self._client() as client:
            requests = (
                lambda headers: client.get("/api/jobs", headers=headers),
                lambda headers: client.post(
                    "/api/profile/cvs",
                    files={"files": ("cv.pdf", b"%PDF-1.4", "application/pdf")},
                    headers=headers,
                ),
                lambda headers: client.get("/api/report/pdf", headers=headers),
            )
            for request in requests:
                with self.subTest(request=request):
                    self.assertEqual(request({}).status_code, 401)
                    self.assertEqual(
                        request({TOKEN_HEADER: "wrong-token"}).status_code,
                        401,
                    )

    def test_factory_is_tokenized_and_docs_are_diagnostic_only(self) -> None:
        normal = create_app()
        diagnostic = create_app(diagnostic=True)
        self.assertTrue(normal.state.api_token)
        self.assertNotIn("/docs", {route.path for route in normal.routes})
        self.assertIn("/docs", {route.path for route in diagnostic.routes})

    def test_public_settings_excludes_secrets_and_paths(self) -> None:
        with self._client() as client:
            response = client.get("/api/config", headers={TOKEN_HEADER: "test-token"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"llm"})
        self.assertEqual(
            set(payload["llm"]),
            {
                "enabled",
                "provider",
                "model",
                "endpoint_scope",
                "remote_consent_configured",
            },
        )
        self.assertNotIn("api_key", str(payload))
        self.assertNotIn("paths", payload)

    def test_remote_disclosure_error_is_stable_and_content_safe(self) -> None:
        with TemporaryDirectory() as temp:
            context = load_project_context(data_dir=Path(temp))
            context.config["llm"].update(
                {
                    "base_url": "https://llm.example.test/v1",
                    "remote_disclosure_consent": False,
                }
            )
            service = build_application_service(context)
            created = service.create_job(
                JobApplication(company="Private Employer", role="Secret Role"),
                "private job description",
            )
            client = TestClient(
                create_app(service, api_token="test-token"),
                base_url="http://127.0.0.1:8000",
            )
            with self.assertLogs("jobtracker.llm", level="WARNING") as logs:
                response = client.post(
                    f"/api/jobs/{created.application_id}/cv-draft",
                    json={"remote_disclosure_confirmed": True},
                    headers={TOKEN_HEADER: "test-token"},
                )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "llm_remote_consent_required")
        combined = response.text + " ".join(logs.output)
        self.assertNotIn("Private Employer", combined)
        self.assertNotIn("private job description", combined)


if __name__ == "__main__":
    unittest.main()
