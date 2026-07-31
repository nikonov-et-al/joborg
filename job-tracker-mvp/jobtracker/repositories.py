from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable, Sequence

from .application import DocumentRef, JobDetails, JobTrackerService, RecordNotFoundError
from .llm import generate_cv_draft_with_llm
from .markdown import atomic_write_text, read_markdown_with_frontmatter
from .models import JobApplication
from .pdf import render_cv_pdf
from .reports import (
    LoadJobsResult,
    ReportData,
    build_report_data,
    build_report_data_from_jobs,
    export_report,
    render_report_pdf,
    write_report_data,
)
from .services import (
    ProjectContext,
    UploadedAsset,
    apply_job_update,
    create_job_record,
    list_job_records,
    load_profile,
    save_profile,
    store_profile_cv,
    write_project_report,
)
from .sqlite_repository import SQLiteJobRepository


class LegacyMarkdownRepository:
    """Read-only adapter for legacy Markdown inspection and migration support."""

    def __init__(self, context: ProjectContext) -> None:
        self.context = context

    def _job_dir(self, job_id: str) -> Path:
        if not job_id or Path(job_id).name != job_id:
            raise RecordNotFoundError(f"Job not found: {job_id}")
        return self.context.jobs_dir / job_id

    def list(self) -> LoadJobsResult:
        loaded = list_job_records(self.context.jobs_dir)
        return type(loaded)([(folder.name, job) for folder, job in loaded.jobs], loaded.errors)

    def get(self, job_id: str) -> JobDetails:
        job_dir = self._job_dir(job_id)
        job_md = job_dir / "job.md"
        if not job_md.is_file():
            raise RecordNotFoundError(f"Job not found: {job_id}")
        metadata, body = read_markdown_with_frontmatter(job_md)
        job = JobApplication.from_metadata(metadata)
        description_path = job_dir / "job_description.md"
        draft_path = job_dir / "cv" / "generated_cv.md"
        return JobDetails(
            application_id=job_id,
            job=job,
            body=body,
            job_description=(
                description_path.read_text(encoding="utf-8") if description_path.exists() else ""
            ),
            cv_draft=draft_path.read_text(encoding="utf-8") if draft_path.exists() else "",
            documents=(),
        )

    def create(
        self,
        job: JobApplication,
        job_description: str,
        assets: Sequence[UploadedAsset] = (),
    ) -> tuple[str, JobApplication, list[DocumentRef]]:
        raise RuntimeError("Legacy Markdown storage is read-only; migrate to SQLite before creating jobs")

    def update(
        self,
        job_id: str,
        assets: Sequence[UploadedAsset] = (),
        *,
        status: str | None = None,
        status_date: str | None = None,
        note: str = "",
        category: str = "",
        updates: dict[str, Any] | None = None,
        job_description: str | None = None,
    ) -> tuple[str, JobApplication, list[DocumentRef]]:
        raise RuntimeError("Legacy Markdown storage is read-only; migrate to SQLite before updating jobs")

    def save_cv_draft(self, job_id: str, content: str) -> str:
        raise RuntimeError("Legacy Markdown storage is read-only; migrate to SQLite before saving drafts")

    def get_profile(self) -> dict[str, Any]:
        return load_profile(self.context.profile_dir)

    def save_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        return save_profile(self.context.profile_dir, payload)

    def store_profile_cv(self, asset: UploadedAsset) -> str:
        return store_profile_cv(self.context.profile_dir, asset)


class LegacyReportGateway:
    def __init__(
        self,
        context: ProjectContext,
        *,
        write_default_fn: Callable[..., Path] = write_project_report,
        build_fn: Callable[..., ReportData] = build_report_data,
        write_data_fn: Callable[[ReportData, Path], None] = write_report_data,
        render_pdf_fn: Callable[[ReportData], bytes] = render_report_pdf,
        export_fn: Callable[..., list[Path]] = export_report,
    ) -> None:
        self.context = context
        self._write_default = write_default_fn
        self._build = build_fn
        self._write_data = write_data_fn
        self._render_pdf = render_pdf_fn
        self._export = export_fn

    @property
    def applicant_name(self) -> str:
        return self.context.applicant_name

    def write_default(self, month: str | None = None, output_path: Path | None = None) -> Path:
        if month is None and output_path is None:
            return self._write_default(self.context)
        if output_path is None:
            return self._write_default(self.context, month)
        return self._write_default(self.context, month, output_path)

    def build(
        self,
        *,
        month: str | None = None,
        year: str | None = None,
        month_number: str | None = None,
    ) -> ReportData:
        return self._build(
            self.context.jobs_dir,
            applicant_name=self.applicant_name,
            month=month,
            year=year,
            month_number=month_number,
        )

    def write_data(self, data: ReportData, output_path: Path) -> None:
        self._write_data(data, output_path)

    def render_pdf(self, data: ReportData) -> bytes:
        return self._render_pdf(data)

    def export(self, month: str | None = None) -> tuple[list[Path], LoadJobsResult]:
        loaded = list_job_records(self.context.jobs_dir)
        report_path = (
            self.context.reports_dir / month / "report.md" if month else Path("report.md")
        )
        if not report_path.exists():
            self.write_default(month, report_path)
        created = self._export(
            jobs_dir=self.context.jobs_dir,
            report_md=report_path,
            applicant_name=self.applicant_name,
            month=month,
            create_docx=bool(self.context.config.get("export", {}).get("create_docx", True)),
            create_pdf=bool(self.context.config.get("export", {}).get("create_pdf", True)),
        )
        return created, loaded


class LegacyDocumentGateway:
    def __init__(
        self,
        context: ProjectContext,
        *,
        generate_fn: Callable[..., str] = generate_cv_draft_with_llm,
        render_fn: Callable[..., bytes] = render_cv_pdf,
    ) -> None:
        self.context = context
        self._generate = generate_fn
        self._render = render_fn

    def generate_cv_draft(
        self,
        *,
        profile: dict[str, Any],
        job_description: str,
        job: dict[str, Any],
        remote_confirmation: bool = False,
    ) -> str:
        return self._generate(
            profile=profile,
            job_description=job_description,
            job=job,
            config=self.context.config,
            remote_confirmation=remote_confirmation,
        )

    def render_cv_pdf(self, content: str, *, job_title: str) -> bytes:
        return self._render(
            content,
            applicant_name=self.context.applicant_name,
            job_title=job_title,
        )


class SQLiteReportGateway:
    def __init__(self, context: ProjectContext, jobs: SQLiteJobRepository) -> None:
        self.context = context
        self.jobs = jobs

    @property
    def applicant_name(self) -> str:
        return self.context.applicant_name

    def build(
        self,
        *,
        month: str | None = None,
        year: str | None = None,
        month_number: str | None = None,
    ) -> ReportData:
        return build_report_data_from_jobs(
            self.jobs.list(),
            applicant_name=self.applicant_name,
            month=month,
            year=year,
            month_number=month_number,
        )

    def write_data(self, data: ReportData, output_path: Path) -> None:
        write_report_data(data, output_path)

    def write_default(
        self, month: str | None = None, output_path: Path | None = None
    ) -> Path:
        if output_path is None:
            output_path = (
                self.context.reports_dir / month / "report.md"
                if month
                else self.context.reports_dir / "report.md"
            )
        self.write_data(self.build(month=month), output_path)
        return output_path

    def render_pdf(self, data: ReportData) -> bytes:
        return render_report_pdf(data)

    def export(self, month: str | None = None) -> tuple[list[Path], LoadJobsResult]:
        report_path = self.write_default(month)
        created: list[Path] = []
        export_config = self.context.config.get("export", {})
        if bool(export_config.get("create_docx", True)):
            pandoc = shutil.which("pandoc")
            if pandoc:
                docx_path = report_path.with_suffix(".docx")
                subprocess.run([pandoc, str(report_path), "-o", str(docx_path)], check=True)
                created.append(docx_path)
        if bool(export_config.get("create_pdf", True)):
            pdf_path = report_path.with_suffix(".pdf")
            pdf_path.write_bytes(self.render_pdf(self.build(month=month)))
            created.append(pdf_path)
        return created, self.jobs.list()


def build_application_service(context: ProjectContext) -> JobTrackerService:
    profiles = LegacyMarkdownRepository(context)
    if context.database is None or context.files_dir is None:
        raise RuntimeError("SQLite runtime storage is required; legacy Markdown storage is read-only")
    repository = SQLiteJobRepository(context.database, context.files_dir)
    reports = SQLiteReportGateway(context, repository)
    return JobTrackerService(
        context,
        repository,
        profiles,
        reports,
        LegacyDocumentGateway(context),
    )
