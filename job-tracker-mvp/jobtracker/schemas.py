from __future__ import annotations

from datetime import date
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import JobApplication


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


MAX_TEXT = 20_000
MAX_LIST_ITEMS = 100
MAX_LIST_ITEM_TEXT = 500


def _safe_text(value: str, *, limit: int = MAX_TEXT) -> str:
    if any(ord(char) < 32 and char not in "\n\t\r" for char in value):
        raise ValueError("control characters are not allowed")
    if len(value) > limit:
        raise ValueError(f"value exceeds {limit} characters")
    return value.strip()


def _validate_iso_date(value: str) -> str:
    value = _safe_text(value, limit=10)
    if value:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("date must use YYYY-MM-DD") from exc
    return value


def _validate_url(value: str) -> str:
    value = _safe_text(value, limit=2_000)
    if value and not re.match(r"https?://[^\s/$.?#][^\s]*$", value, re.IGNORECASE):
        raise ValueError("URL must use http or https")
    return value


def _validate_list(values: list[str]) -> list[str]:
    if len(values) > MAX_LIST_ITEMS:
        raise ValueError(f"list exceeds {MAX_LIST_ITEMS} items")
    return [_safe_text(value, limit=MAX_LIST_ITEM_TEXT) for value in values]


class JobCreateSchema(BoundaryModel):
    company: str
    role: str
    location: str = ""
    job_url: str = ""
    source: str = ""
    application_method: str = "company website"
    date_applied: str = ""
    status: str = "applied"
    status_date: str = ""
    contact_person: str = ""
    contact_email: str = ""
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    language_requirements: list[str] = Field(default_factory=list)
    summary: str = ""
    next_action: str = ""
    next_action_date: str = ""
    relevant_for_arbeitsagentur: bool = True

    @field_validator("company", "role")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = _safe_text(value, limit=500)
        if not value:
            raise ValueError("value is required")
        return value

    @field_validator("location", "source", "application_method", "contact_person", "summary", "next_action")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_text(value)

    @field_validator("job_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        return _validate_url(value)

    @field_validator("contact_email")
    @classmethod
    def safe_email(cls, value: str) -> str:
        return PersonalProfileSchema.valid_email(value) if value else ""

    @field_validator("date_applied", "status_date", "next_action_date")
    @classmethod
    def safe_date(cls, value: str) -> str:
        return _validate_iso_date(value)

    @field_validator("required_skills", "nice_to_have_skills", "language_requirements")
    @classmethod
    def safe_list(cls, value: list[str]) -> list[str]:
        return _validate_list(value)

    @model_validator(mode="after")
    def domain_values_are_valid(self) -> "JobCreateSchema":
        JobApplication.from_metadata(self.model_dump())
        return self

    def to_job(self) -> JobApplication:
        return JobApplication.from_metadata(self.model_dump())


class JobUpdateSchema(BoundaryModel):
    company: str | None = None
    role: str | None = None
    location: str | None = None
    job_url: str | None = None
    source: str | None = None
    application_method: str | None = None
    date_applied: str | None = None
    status: str | None = None
    status_date: str | None = None
    contact_person: str | None = None
    contact_email: str | None = None
    required_skills: list[str] | None = None
    nice_to_have_skills: list[str] | None = None
    language_requirements: list[str] | None = None
    relevant_for_arbeitsagentur: bool | None = None
    summary: str | None = None
    next_action: str | None = None
    next_action_date: str | None = None
    job_description: str | None = None
    note: str | None = None

    @field_validator("company", "role")
    @classmethod
    def safe_required_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = _safe_text(value, limit=500)
        if not value:
            raise ValueError("value is required")
        return value

    @field_validator("location", "source", "application_method", "contact_person", "summary", "next_action", "job_description", "note")
    @classmethod
    def safe_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value)

    @field_validator("job_url")
    @classmethod
    def safe_optional_url(cls, value: str | None) -> str | None:
        return None if value is None else _validate_url(value)

    @field_validator("contact_email")
    @classmethod
    def safe_optional_email(cls, value: str | None) -> str | None:
        return None if value is None or not value else PersonalProfileSchema.valid_email(value)

    @field_validator("date_applied", "status_date", "next_action_date")
    @classmethod
    def safe_optional_date(cls, value: str | None) -> str | None:
        return None if value is None else _validate_iso_date(value)

    @field_validator("required_skills", "nice_to_have_skills", "language_requirements")
    @classmethod
    def safe_optional_list(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_list(value)

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class PersonalProfileSchema(BoundaryModel):
    given_name: str
    family_name: str
    email: str
    phone: str = ""
    city: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""

    @field_validator("given_name", "family_name")
    @classmethod
    def required_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value is required")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = _safe_text(value, limit=320)
        local, separator, domain = value.partition("@")
        if (
            not separator
            or not local
            or not domain
            or "@" in domain
            or any(character.isspace() for character in value)
        ):
            raise ValueError("invalid email")
        return value

    @field_validator("phone", "city")
    @classmethod
    def safe_personal_text(cls, value: str) -> str:
        return _safe_text(value, limit=500)

    @field_validator("linkedin", "github", "website")
    @classmethod
    def safe_personal_url(cls, value: str) -> str:
        return _validate_url(value)


class ProfileUpdateSchema(BoundaryModel):
    personal: PersonalProfileSchema
    skills: list[str]
    experience: str

    @field_validator("skills")
    @classmethod
    def safe_skills(cls, value: list[str]) -> list[str]:
        return _validate_list(value)

    @field_validator("experience")
    @classmethod
    def safe_experience(cls, value: str) -> str:
        return _safe_text(value)


class CvDraftSchema(BoundaryModel):
    content: str

    @field_validator("content")
    @classmethod
    def safe_content(cls, value: str) -> str:
        return _safe_text(value, limit=100_000)
