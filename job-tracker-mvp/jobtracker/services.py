from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import yaml

from .config import ensure_project_dirs, load_config
from .files import ensure_dir, unique_path
from .markdown import append_to_markdown_body, update_markdown_frontmatter, write_markdown_with_frontmatter
from .models import JobApplication, today_iso
from .reports import load_jobs, write_report


JOB_FILE_CATEGORIES = {
    "cv": "cv",
    "motivation_letter": "motivation_letter",
    "attachments": "attachments",
    "evidence": "evidence",
    "screenshots": "screenshots",
    "job_description": "attachments",
}


@dataclass(frozen=True)
class ProjectContext:
    config: dict[str, Any]
    input_dir: Path
    jobs_dir: Path
    reports_dir: Path
    profile_dir: Path


@dataclass(frozen=True)
class UploadedAsset:
    filename: str
    content: bytes
    category: str = "attachments"


def load_project_context(config_path: Path = Path("config.yml")) -> ProjectContext:
    config = load_config(config_path)
    ensure_project_dirs(config)

    return ProjectContext(
        config=config,
        input_dir=Path(config["paths"]["input"]),
        jobs_dir=Path(config["paths"]["jobs"]),
        reports_dir=Path(config["paths"]["reports"]),
        profile_dir=Path(config["paths"]["profile"]),
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
| {today_iso()} | Application record created | |

# Original pasted job description preview

{job_description[:2000]}
""".strip()


def write_job_description(job_dir: Path, job_description: str) -> None:
    (job_dir / "job_description.md").write_text(
        f"# Job description\n\n{job_description.strip()}\n",
        encoding="utf-8",
    )


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
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(asset.content)
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

    ensure_job_workspace(job_dir)
    write_job_description(job_dir, job_description)

    stored_paths: list[str] = []
    if assets:
        job, stored_paths = attach_assets_to_job(job_dir, job, assets)

    write_job_record(job_dir, job, job_description)
    return job, stored_paths


def apply_job_update(
    job_dir: Path,
    job: JobApplication,
    assets: Sequence[UploadedAsset] | None = None,
    *,
    status: str | None = None,
    status_date: str | None = None,
    note: str = "",
) -> tuple[JobApplication, list[str]]:
    stored_paths: list[str] = []

    if assets:
        job, stored_paths = attach_assets_to_job(job_dir, job, assets)

    if status:
        job.status = status
        job.status_date = status_date or today_iso()
    elif status_date:
        job.status_date = status_date

    update_markdown_frontmatter(job_dir / "job.md", job.to_metadata())

    lines: list[str] = []
    lines.append(f"## Update - {today_iso()}")
    lines.append("")

    if status:
        lines.append(f"- Status: {job.status}")
        lines.append(f"- Status date: {job.status_date}")
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
        applicant_name=context.config["applicant"].get("name", ""),
        month=month,
    )
    return output_path


def list_job_records(jobs_dir: Path) -> list[tuple[Path, JobApplication]]:
    return load_jobs(jobs_dir)


def job_record_to_dict(folder: Path, job: JobApplication) -> dict[str, Any]:
    data = job.to_metadata()
    data["id"] = folder.name
    data["job_id"] = folder.name
    data["folder"] = folder.as_posix()
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

    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "personal_info.yml").write_text(
        yaml.safe_dump(personal, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (profile_dir / "skills.yml").write_text(
        yaml.safe_dump({"skills": [item.strip() for item in skills if item.strip()]}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (profile_dir / "experience.md").write_text(experience.strip() + "\n", encoding="utf-8")
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
