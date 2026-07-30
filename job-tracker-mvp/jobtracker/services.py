from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any, Sequence

import yaml

from .config import ensure_project_dirs, load_config
from .files import ensure_dir, unique_path
from .markdown import atomic_write_text, append_to_markdown_body, read_markdown_with_frontmatter, update_markdown_frontmatter, write_markdown_with_frontmatter
from .models import JobApplication, format_date_display, today_iso
from .paths import AppPaths
from .reports import LoadJobsResult, load_jobs, write_report


JOB_FILE_CATEGORIES = {
    "cv": "cv",
    "motivation_letter": "motivation_letter",
    "attachments": "attachments",
    "evidence": "evidence",
    "screenshots": "screenshots",
    "job_description": "attachments",
}

EDITABLE_JOB_FIELDS = {
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
    "required_skills",
    "nice_to_have_skills",
    "language_requirements",
    "relevant_for_arbeitsagentur",
    "summary",
    "next_action",
    "next_action_date",
}

_LIST_JOB_FIELDS = {"required_skills", "nice_to_have_skills", "language_requirements"}
_DATE_JOB_FIELDS = {"date_applied", "status_date", "next_action_date"}


@dataclass(frozen=True)
class ProjectContext:
    config: dict[str, Any]
    input_dir: Path
    jobs_dir: Path
    reports_dir: Path
    profile_dir: Path
    database: Path | None = None
    files_dir: Path | None = None
    legacy_jobs_dir: Path | None = None

    @property
    def applicant_name(self) -> str:
        return profile_display_name(
            load_profile(self.profile_dir),
            str(self.config.get("applicant", {}).get("name", "")),
        )


@dataclass(frozen=True)
class UploadedAsset:
    filename: str
    content: bytes
    category: str = "attachments"


class AttachmentCopyError(OSError):
    def __init__(self, filename: str) -> None:
        self.filename = Path(filename).name
        super().__init__(f"Attachment copy failed: {self.filename}")


class JobCreationError(OSError):
    def __init__(self, job_dir: Path, stage: str) -> None:
        self.job_dir = job_dir
        self.stage = stage
        messages = {
            "temporary_directory_creation": "Could not create a temporary job directory",
            "workspace_creation": "Could not create the temporary job workspace",
            "validation": "Completed temporary job directory failed validation",
            "final_rename": "Could not rename the completed temporary job directory",
        }
        super().__init__(f"{messages[stage]}: {job_dir}")


def load_project_context(
    config_path: Path | None = None,
    *,
    data_dir: str | Path | None = None,
    enforce_workspace: bool = False,
) -> ProjectContext:
    paths = AppPaths.resolve(data_dir=data_dir)
    config = load_config(config_path, app_paths=paths)
    if enforce_workspace:
        escaped = [
            key
            for key in ("input", "jobs", "reports", "profile")
            if not Path(config["paths"][key]).resolve().is_relative_to(paths.data_dir)
        ]
        if escaped:
            raise ValueError(
                "Configured paths escape the selected workspace: "
                + ", ".join(sorted(escaped))
            )
    paths.ensure_runtime_dirs()

    return ProjectContext(
        config=config,
        input_dir=Path(config["paths"]["input"]),
        jobs_dir=Path(config["paths"]["jobs"]),
        reports_dir=Path(config["paths"]["reports"]),
        profile_dir=Path(config["paths"]["profile"]),
        database=paths.database,
        files_dir=paths.files_dir,
        legacy_jobs_dir=paths.legacy_jobs_dir,
    )


def ensure_job_workspace(job_dir: Path) -> None:
    ensure_dir(job_dir)
    ensure_dir(job_dir / "evidence")
    ensure_dir(job_dir / "screenshots")
    ensure_dir(job_dir / "attachments")
    ensure_dir(job_dir / "cv")
    ensure_dir(job_dir / "motivation_letter")


def build_job_body(job_description: str) -> str:
    return f"""
# Job description

See `job_description.md`.

# Comments

# Timeline

| Date | Event | Evidence |
|---|---|---|
| {format_date_display(today_iso())} | Application record created | |

# Original pasted job description preview

{job_description[:2000]}
""".strip()


def write_job_description(job_dir: Path, job_description: str) -> None:
    content = job_description.strip()
    if not content.startswith("# Job description"):
        content = f"# Job description\n\n{content}"
    atomic_write_text(job_dir / "job_description.md", content.rstrip() + "\n")


def write_job_record(job_dir: Path, job: JobApplication, job_description: str) -> None:
    write_markdown_with_frontmatter(
        job_dir / "job.md",
        job.to_metadata(),
        build_job_body(job_description),
    )


def _category_folder(category: str) -> str:
    if category not in JOB_FILE_CATEGORIES:
        raise ValueError(f"Invalid file category: {category}")
    return JOB_FILE_CATEGORIES[category]


def _store_uploaded_asset(job_dir: Path, asset: UploadedAsset) -> tuple[str, str]:
    category = asset.category or "attachments"
    folder = _category_folder(category)
    destination = unique_path(job_dir / folder / Path(asset.filename).name)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(asset.content)
    except OSError as exc:
        raise AttachmentCopyError(asset.filename) from exc
    return category, destination.relative_to(job_dir).as_posix()


def attach_assets_to_job(job_dir: Path, job: JobApplication, assets: Sequence[UploadedAsset]) -> tuple[JobApplication, list[str]]:
    stored_paths: list[str] = []

    for asset in assets:
        category, relative_path = _store_uploaded_asset(job_dir, asset)
        stored_paths.append(relative_path)

        if category == "cv":
            job.cv_file = relative_path
        elif category == "motivation_letter":
            job.motivation_letter_file = relative_path
        elif category == "evidence":
            job.evidence.append(relative_path)
        elif category == "screenshots":
            job.screenshots.append(relative_path)
        else:
            job.attachments.append(relative_path)

    return job, stored_paths


def create_job_record(job_dir: Path, job: JobApplication, job_description: str, assets: Sequence[UploadedAsset] | None = None) -> tuple[JobApplication, list[str]]:
    if job_dir.exists():
        raise FileExistsError(f"Job folder already exists: {job_dir}")

    planned_assets = tuple(assets or ())
    working_job = deepcopy(job)

    try:
        job_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary_directory = TemporaryDirectory(
            prefix=f".{job_dir.name}.",
            dir=job_dir.parent,
        )
    except OSError as exc:
        raise JobCreationError(job_dir, "temporary_directory_creation") from exc

    with temporary_directory as temporary:
        temporary_job_dir = Path(temporary)
        try:
            ensure_job_workspace(temporary_job_dir)
        except OSError as exc:
            raise JobCreationError(job_dir, "workspace_creation") from exc
        write_job_description(temporary_job_dir, job_description)

        stored_paths: list[str] = []
        if planned_assets:
            working_job, stored_paths = attach_assets_to_job(
                temporary_job_dir,
                working_job,
                planned_assets,
            )

        write_job_record(temporary_job_dir, working_job, job_description)

        try:
            required_files = ("job.md", "job_description.md")
            required_directories = tuple(set(JOB_FILE_CATEGORIES.values()))
            if not all((temporary_job_dir / name).is_file() for name in required_files):
                raise ValueError("required file missing")
            if not all((temporary_job_dir / name).is_dir() for name in required_directories):
                raise ValueError("required directory missing")
            metadata, _body = read_markdown_with_frontmatter(temporary_job_dir / "job.md")
            validated_job = JobApplication.from_metadata(metadata)
            if validated_job.to_metadata() != working_job.to_metadata():
                raise ValueError("metadata mismatch")
            for asset, relative_path in zip(planned_assets, stored_paths, strict=True):
                if (temporary_job_dir / relative_path).read_bytes() != asset.content:
                    raise ValueError("attachment mismatch")
        except (OSError, TypeError, ValueError) as exc:
            raise JobCreationError(job_dir, "validation") from exc

        if job_dir.exists():
            raise FileExistsError(f"Job folder already exists: {job_dir}")
        try:
            temporary_job_dir.rename(job_dir)
        except FileExistsError as exc:
            raise FileExistsError(f"Job folder already exists: {job_dir}") from exc
        except OSError as exc:
            raise JobCreationError(job_dir, "final_rename") from exc

    return working_job, stored_paths


def apply_job_update(
    job_dir: Path,
    job: JobApplication,
    assets: Sequence[UploadedAsset] | None = None,
    *,
    status: str | None = None,
    status_date: str | None = None,
    note: str = "",
    category: str = "",
    updates: dict[str, Any] | None = None,
    job_description: str | None = None,
) -> tuple[JobApplication, list[str]]:
    changes = dict(updates or {})
    if status is not None:
        changes["status"] = status
    if status_date is not None:
        changes["status_date"] = status_date

    unknown = set(changes) - EDITABLE_JOB_FIELDS
    if unknown:
        raise ValueError(f"Fields cannot be edited: {', '.join(sorted(unknown))}")
    if not isinstance(note, str):
        raise ValueError("note must be text")
    if not isinstance(category, str):
        raise ValueError("category must be text")
    if job_description is not None and not isinstance(job_description, str):
        raise ValueError("job_description must be text")

    for field, value in changes.items():
        if field in _LIST_JOB_FIELDS:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{field} must be a list of strings")
        elif field == "relevant_for_arbeitsagentur":
            if not isinstance(value, bool):
                raise ValueError(f"{field} must be a boolean")
        elif not isinstance(value, str):
            raise ValueError(f"{field} must be text")

        if field in _DATE_JOB_FIELDS and value:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError(f"{field} must use YYYY-MM-DD")
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{field} must use YYYY-MM-DD") from exc

    metadata = job.to_metadata()
    metadata.update(changes)
    if not str(metadata["company"]).strip():
        raise ValueError("company is required")
    if not str(metadata["role"]).strip():
        raise ValueError("role is required")

    status_changed = "status" in changes and changes["status"] != job.status
    if status_changed and not changes.get("status_date"):
        metadata["status_date"] = today_iso()
    job = JobApplication.from_metadata(metadata)

    stored_paths: list[str] = []

    if assets:
        job, stored_paths = attach_assets_to_job(job_dir, job, assets)

    update_markdown_frontmatter(job_dir / "job.md", job.to_metadata())
    if job_description is not None:
        write_job_description(job_dir, job_description)

    lines: list[str] = []
    lines.append(f"## Update - {format_date_display(today_iso())}")
    lines.append("")

    if status_changed:
        lines.append(f"- Status: {job.status}")
        lines.append(f"- Status date: {format_date_display(job.status_date)}")
    if category:
        lines.append(f"- Category: {category}")
    if note:
        lines.append(f"- Note: {note}")
    if stored_paths:
        lines.append(f"- Files: {', '.join(f'`{path}`' for path in stored_paths)}")

    if len(lines) > 2:
        append_to_markdown_body(job_dir / "job.md", "\n".join(lines))

    return job, stored_paths


def write_project_report(context: ProjectContext, month: str | None = None, output_path: Path | None = None) -> Path:
    if output_path is None:
        if month:
            output_path = context.reports_dir / month / "report.md"
        else:
            output_path = Path("report.md")

    write_report(
        jobs_dir=context.jobs_dir,
        output_path=output_path,
        applicant_name=context.applicant_name,
        month=month,
    )
    return output_path


def list_job_records(jobs_dir: Path) -> LoadJobsResult:
    return load_jobs(jobs_dir)


def job_record_to_dict(application_id: str | Path, job: JobApplication) -> dict[str, Any]:
    data = job.to_metadata()
    identifier = application_id.name if isinstance(application_id, Path) else application_id
    data["id"] = identifier
    data["job_id"] = identifier
    return data


def load_profile(profile_dir: Path) -> dict[str, Any]:
    personal_path = profile_dir / "personal_info.yml"
    skills_path = profile_dir / "skills.yml"
    experience_path = profile_dir / "experience.md"
    cv_dir = profile_dir / "cv"

    personal = {}
    skills: list[str] = []
    if personal_path.exists():
        personal = yaml.safe_load(personal_path.read_text(encoding="utf-8")) or {}
    if skills_path.exists():
        loaded = yaml.safe_load(skills_path.read_text(encoding="utf-8")) or {}
        skills = loaded.get("skills", []) if isinstance(loaded, dict) else []

    return {
        "personal": personal if isinstance(personal, dict) else {},
        "skills": skills if isinstance(skills, list) else [],
        "experience": experience_path.read_text(encoding="utf-8") if experience_path.exists() else "",
        "cv_files": sorted(path.name for path in cv_dir.iterdir() if path.is_file()) if cv_dir.exists() else [],
    }


def profile_display_name(profile: dict[str, Any], fallback: str = "") -> str:
    personal = profile.get("personal", {})
    if not isinstance(personal, dict):
        return fallback.strip()
    split_name = " ".join(
        str(personal.get(key, "")).strip()
        for key in ("given_name", "family_name")
        if str(personal.get(key, "")).strip()
    )
    return split_name or str(personal.get("name", "")).strip() or fallback.strip()


def save_profile(profile_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    personal = payload.get("personal", {})
    skills = payload.get("skills", [])
    experience = payload.get("experience", "")

    if not isinstance(personal, dict):
        raise ValueError("personal must be an object")
    if not isinstance(skills, list) or not all(isinstance(item, str) for item in skills):
        raise ValueError("skills must be a list of strings")
    if not isinstance(experience, str):
        raise ValueError("experience must be text")

    existing_personal = load_profile(profile_dir)["personal"]
    merged_personal = dict(existing_personal)
    merged_personal.update(personal)

    profile_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        profile_dir / "personal_info.yml",
        yaml.safe_dump(merged_personal, allow_unicode=True, sort_keys=False),
    )
    atomic_write_text(
        profile_dir / "skills.yml",
        yaml.safe_dump({"skills": [item.strip() for item in skills if item.strip()]}, allow_unicode=True, sort_keys=False),
    )
    atomic_write_text(profile_dir / "experience.md", experience.strip() + "\n")
    return load_profile(profile_dir)


def store_profile_cv(profile_dir: Path, asset: UploadedAsset) -> str:
    suffix = Path(asset.filename).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        raise ValueError("CV files must be PDF or DOCX")

    safe_name = Path(asset.filename).name
    if not safe_name:
        raise ValueError("Invalid filename")

    destination = unique_path(profile_dir / "cv" / safe_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(asset.content)
    return destination.name
