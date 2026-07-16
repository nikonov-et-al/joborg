from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
import re
from typing import Any


VALID_STATUSES = [
    "draft",
    "applied",
    "confirmation_received",
    "interview_invited",
    "interview_done",
    "task_received",
    "offer_received",
    "rejected",
    "withdrawn",
    "no_response",
    "closed",
]


def today_iso() -> str:
    return date.today().isoformat()


def format_date_display(value: str) -> str:
    """Format stored ISO dates for people without changing persisted values."""
    if not value:
        return value

    try:
        if "T" in value or " " in value:
            parsed = datetime.fromisoformat(value)
            return parsed.strftime("%d-%m-%Y %H:%M")
        return date.fromisoformat(value).strftime("%d-%m-%Y")
    except ValueError:
        return value


def normalize_date_input(value: str) -> str:
    """Accept European or ISO CLI input and return the ISO storage form."""
    value = value.strip()
    if not value:
        return ""

    for pattern in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Invalid date: {value}. Use DD-MM-YYYY.")


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-") or "unknown"


@dataclass
class JobApplication:
    company: str = ""
    role: str = ""
    location: str = ""
    job_url: str = ""
    source: str = ""
    application_method: str = ""
    date_applied: str = field(default_factory=today_iso)
    status: str = "applied"
    status_date: str = field(default_factory=today_iso)
    contact_person: str = ""
    contact_email: str = ""
    required_skills: list[str] = field(default_factory=list)
    nice_to_have_skills: list[str] = field(default_factory=list)
    language_requirements: list[str] = field(default_factory=list)
    cv_file: str = ""
    motivation_letter_file: str = ""
    evidence: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    relevant_for_arbeitsagentur: bool = True
    summary: str = ""
    next_action: str = ""
    next_action_date: str = ""

    def validate(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status: {self.status}. "
                f"Valid statuses: {', '.join(VALID_STATUSES)}"
            )

    def folder_name(self) -> str:
        date_part = self.date_applied or today_iso()
        company_part = slugify(self.company)
        role_part = slugify(self.role)
        return f"{date_part}_{company_part}_{role_part}"

    def to_metadata(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_metadata(cls, data: dict[str, Any]) -> "JobApplication":
        allowed = set(cls.__dataclass_fields__.keys())
        clean = {k: v for k, v in data.items() if k in allowed}
        job = cls(**clean)
        job.validate()
        return job
