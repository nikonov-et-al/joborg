from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .models import VALID_STATUSES

if TYPE_CHECKING:
    from .models import JobApplication


JOB_REQUIRED_FIELDS = ("company", "role", "date_applied", "status")
JOB_STRING_FIELDS = (
    "company",
    "role",
    "location",
    "job_url",
    "source",
    "application_method",
    "date_applied",
    "status",
    "status_date",
    "contact_person",
    "contact_email",
    "cv_file",
    "motivation_letter_file",
    "summary",
    "next_action",
    "next_action_date",
)
JOB_LIST_FIELDS = (
    "required_skills",
    "nice_to_have_skills",
    "language_requirements",
    "evidence",
    "screenshots",
    "attachments",
)
JOB_BOOL_FIELDS = ("relevant_for_arbeitsagentur",)
JOB_DATE_FIELDS = ("date_applied", "status_date", "next_action_date")


class JobMetadataLoadError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LoadJobError:
    path: str
    code: str
    message: str
    exception_type: str | None = None


@dataclass(frozen=True)
class LoadJobsResult:
    jobs: list[tuple[Path, JobApplication]]
    errors: list[LoadJobError]

    @property
    def loaded_count(self) -> int:
        return len(self.jobs)

    @property
    def failed_count(self) -> int:
        return len(self.errors)


def classify_job_load_exception(exc: Exception) -> str:
    if isinstance(exc, JobMetadataLoadError):
        return exc.code
    if isinstance(exc, FileNotFoundError):
        return "missing_file"
    if isinstance(exc, PermissionError):
        return "permission_error"
    if isinstance(exc, UnicodeError):
        return "decode_error"
    if isinstance(exc, yaml.YAMLError):
        return "frontmatter_error"

    message = str(exc).lower()
    if isinstance(exc, ValueError):
        if "frontmatter" in message:
            return "frontmatter_error"
        if message.startswith("invalid status"):
            return "invalid_status"
        if "invalid date" in message:
            return "invalid_date"
        if "metadata" in message:
            return "invalid_metadata"
        return "unexpected_load_error"
    if isinstance(exc, (TypeError, KeyError)) and "metadata" in message:
        return "invalid_metadata"
    return "unexpected_load_error"


def validate_job_load_metadata(metadata: dict[str, object]) -> None:
    if any(
        metadata.get(field) is None
        or isinstance(metadata.get(field), str) and not metadata[field].strip()
        for field in JOB_REQUIRED_FIELDS
    ):
        raise JobMetadataLoadError("invalid_metadata")

    if any(
        field in metadata and not isinstance(metadata[field], str)
        for field in JOB_STRING_FIELDS
    ):
        raise JobMetadataLoadError("invalid_metadata")

    for field in JOB_LIST_FIELDS:
        if field not in metadata:
            continue
        value = metadata[field]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise JobMetadataLoadError("invalid_metadata")

    if any(
        field in metadata and not isinstance(metadata[field], bool)
        for field in JOB_BOOL_FIELDS
    ):
        raise JobMetadataLoadError("invalid_metadata")

    status = metadata.get("status")
    if isinstance(status, str) and status not in VALID_STATUSES:
        raise JobMetadataLoadError("invalid_status")

    for field in JOB_DATE_FIELDS:
        value = metadata.get(field)
        if isinstance(value, str) and value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise JobMetadataLoadError("invalid_date") from exc


def public_job_load_message(code: str) -> str:
    return {
        "frontmatter_error": "Job record frontmatter is malformed or incomplete.",
        "invalid_metadata": "Job record metadata is invalid.",
        "invalid_status": "Job record status is invalid.",
        "invalid_date": "Job record date is invalid.",
        "missing_file": "Job record file is missing.",
        "permission_error": "Permission was denied while reading the job record.",
        "decode_error": "Job record is not valid UTF-8 text.",
        "unexpected_load_error": "An unexpected error occurred while loading the job record.",
    }[code]


def make_job_load_error(path: Path, jobs_dir: Path, exc: Exception) -> LoadJobError:
    try:
        relative_path = path.relative_to(jobs_dir).as_posix()
    except ValueError:
        relative_path = path.name
    code = classify_job_load_exception(exc)
    exception_type = "ValueError" if isinstance(exc, JobMetadataLoadError) else type(exc).__name__
    return LoadJobError(
        path=relative_path,
        code=code,
        message=public_job_load_message(code),
        exception_type=exception_type,
    )
