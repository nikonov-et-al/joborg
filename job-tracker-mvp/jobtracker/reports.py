from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
import subprocess
import shutil

from .markdown import read_markdown_with_frontmatter
from .models import JobApplication


def find_job_files(jobs_dir: Path) -> list[Path]:
    if not jobs_dir.exists():
        return []

    return sorted(jobs_dir.glob("*/job.md"))


def load_jobs(jobs_dir: Path) -> list[tuple[Path, JobApplication]]:
    jobs: list[tuple[Path, JobApplication]] = []

    for path in find_job_files(jobs_dir):
        try:
            metadata, _body = read_markdown_with_frontmatter(path)
            job = JobApplication.from_metadata(metadata)
            jobs.append((path.parent, job))
        except Exception:
            # Keep report generation resilient.
            continue

    return sorted(jobs, key=lambda item: item[1].date_applied or "")


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def generate_report_md(jobs_dir: Path, applicant_name: str = "", month: str | None = None) -> str:
    jobs = load_jobs(jobs_dir)

    if month:
        jobs = [
            (folder, job)
            for folder, job in jobs
            if job.date_applied.startswith(month)
        ]

    status_counts = Counter(job.status for _folder, job in jobs)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    lines.append("# Job Application Report")
    lines.append("")
    if applicant_name:
        lines.append(f"Applicant: {applicant_name}  ")
    if month:
        lines.append(f"Reporting month: {month}  ")
    lines.append(f"Generated: {generated_at}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total applications: {len(jobs)}")
    for status, count in sorted(status_counts.items()):
        lines.append(f"- {status}: {count}")
    lines.append("")
    lines.append("## Applications")
    lines.append("")
    lines.append("| Date | Company | Role | Location | Method | Status | Evidence | Folder |")
    lines.append("|---|---|---|---|---|---|---|---|")

    for folder, job in jobs:
        has_evidence = bool(job.evidence or job.screenshots or job.attachments)
        folder_link = folder.as_posix()
        lines.append(
            "| "
            f"{job.date_applied} | "
            f"{job.company} | "
            f"{job.role} | "
            f"{job.location} | "
            f"{job.application_method} | "
            f"{job.status} | "
            f"{yes_no(has_evidence)} | "
            f"`{folder_link}` |"
        )

    lines.append("")
    lines.append("## Details")
    lines.append("")

    for folder, job in jobs:
        lines.append(f"### {job.date_applied} — {job.company} — {job.role}")
        lines.append("")
        lines.append(f"- Status: {job.status}")
        lines.append(f"- Location: {job.location}")
        lines.append(f"- Source: {job.source}")
        lines.append(f"- Application method: {job.application_method}")
        if job.job_url:
            lines.append(f"- Job URL: {job.job_url}")
        if job.summary:
            lines.append(f"- Summary: {job.summary}")
        if job.required_skills:
            lines.append(f"- Required skills: {', '.join(job.required_skills)}")
        if job.language_requirements:
            lines.append(f"- Languages: {', '.join(job.language_requirements)}")
        if job.cv_file:
            lines.append(f"- CV: `{job.cv_file}`")
        if job.motivation_letter_file:
            lines.append(f"- Motivation letter: `{job.motivation_letter_file}`")
        if job.evidence:
            lines.append(f"- Evidence: {', '.join(f'`{x}`' for x in job.evidence)}")
        if job.screenshots:
            lines.append(f"- Screenshots: {', '.join(f'`{x}`' for x in job.screenshots)}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def write_report(jobs_dir: Path, output_path: Path, applicant_name: str = "", month: str | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        generate_report_md(jobs_dir=jobs_dir, applicant_name=applicant_name, month=month),
        encoding="utf-8",
    )


def export_report(report_md: Path, create_docx: bool = True, create_pdf: bool = True) -> list[Path]:
    if not report_md.exists():
        raise FileNotFoundError(f"Report file not found: {report_md}")

    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise RuntimeError("Pandoc is not installed or not found in PATH.")

    created: list[Path] = []

    if create_docx:
        docx_path = report_md.with_suffix(".docx")
        subprocess.run([pandoc, str(report_md), "-o", str(docx_path)], check=True)
        created.append(docx_path)

    if create_pdf:
        pdf_path = report_md.with_suffix(".pdf")
        subprocess.run([pandoc, str(report_md), "-o", str(pdf_path)], check=True)
        created.append(pdf_path)

    return created
