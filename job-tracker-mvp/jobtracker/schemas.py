from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import JobApplication


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


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
        if not value.strip():
            raise ValueError("value is required")
        return value

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
        value = value.strip()
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


class ProfileUpdateSchema(BoundaryModel):
    personal: PersonalProfileSchema
    skills: list[str]
    experience: str


class CvDraftSchema(BoundaryModel):
    content: str
