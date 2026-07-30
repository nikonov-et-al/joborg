from __future__ import annotations

import unittest

from pydantic import ValidationError

from jobtracker.schemas import CvDraftSchema, JobCreateSchema, JobUpdateSchema, ProfileUpdateSchema


class BoundarySchemaTests(unittest.TestCase):
    def test_job_create_builds_domain_model_and_rejects_unknown_fields(self) -> None:
        schema = JobCreateSchema(company="Example", role="Engineer", required_skills=["Python"])
        self.assertEqual(schema.to_job().company, "Example")

        with self.assertRaises(ValidationError):
            JobCreateSchema(company="Example", role="Engineer", attachments=[])

    def test_job_update_forbids_storage_fields_and_strict_type_coercion(self) -> None:
        self.assertEqual(JobUpdateSchema(status="rejected").changes(), {"status": "rejected"})

        for payload in ({"folder": "other"}, {"required_skills": "Python"}, {"relevant_for_arbeitsagentur": "false"}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                JobUpdateSchema.model_validate(payload)

    def test_profile_and_cv_inputs_are_typed(self) -> None:
        self.assertEqual(
            ProfileUpdateSchema(
                personal={
                    "given_name": "Jane",
                    "family_name": "Example",
                    "email": "jane@example.com",
                },
                skills=["Python"],
                experience="Built systems",
            ).skills,
            ["Python"],
        )
        self.assertEqual(CvDraftSchema(content="# CV").content, "# CV")

        for personal in (
            {"given_name": "", "family_name": "Example", "email": "jane@example.com"},
            {"given_name": "Jane", "family_name": "", "email": "jane@example.com"},
            {"given_name": "Jane", "family_name": "Example", "email": "invalid"},
        ):
            with self.assertRaises(ValidationError):
                ProfileUpdateSchema.model_validate(
                    {"personal": personal, "skills": [], "experience": ""}
                )

        with self.assertRaises(ValidationError):
            CvDraftSchema.model_validate({"content": 42})

    def test_rejects_unsafe_urls_dates_controls_and_oversized_lists(self) -> None:
        for payload in (
            {"job_url": "javascript:alert(1)"},
            {"date_applied": "2026-99-01"},
            {"summary": "bad\x00text"},
            {"required_skills": ["Python"] * 101},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                JobUpdateSchema.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
