from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, Sequence

from .models import JobApplication

if TYPE_CHECKING:
    from .loading import LoadJobsResult
    from .reports import ReportData


class ApplicationContext(Protocol):
    config: dict[str, Any]
    input_dir: Path
    jobs_dir: Path
    reports_dir: Path
    profile_dir: Path


class Asset(Protocol):
    filename: str
    content: bytes
    category: str


class RecordNotFoundError(LookupError):
    """Raised when a requested legacy record does not exist."""


@dataclass(frozen=True)
class DocumentRef:
    id: str
    category: str
    original_filename: str
    size: int
    mime_type: str
    sha256: str
    version: int | None = None


@dataclass(frozen=True)
class JobDetails:
    application_id: str
    job: JobApplication
    body: str
    job_description: str
    cv_draft: str
    documents: tuple[DocumentRef, ...] = ()


@dataclass(frozen=True)
class JobMutationResult:
    application_id: str
    job: JobApplication
    documents: tuple[DocumentRef, ...]
    report_warning: str = ""


@dataclass(frozen=True)
class ReportArtifact:
    artifact_id: str
    content: str
    data: ReportData


class JobRepository(Protocol):
    def list(self) -> LoadJobsResult: ...

    def get(self, job_id: str) -> JobDetails: ...

    def create(
        self,
        job: JobApplication,
        job_description: str,
        assets: Sequence[Asset] = (),
    ) -> tuple[str, JobApplication, list[DocumentRef]]: ...

    def update(
        self,
        job_id: str,
        assets: Sequence[Asset] = (),
        *,
        status: str | None = None,
        status_date: str | None = None,
        note: str = "",
        category: str = "",
        updates: dict[str, Any] | None = None,
        job_description: str | None = None,
    ) -> tuple[str, JobApplication, list[DocumentRef]]: ...

    def save_cv_draft(self, job_id: str, content: str) -> str: ...


class ProfileRepository(Protocol):
    def get_profile(self) -> dict[str, Any]: ...

    def save_profile(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def store_profile_cv(self, asset: Asset) -> str: ...


class ReportGateway(Protocol):
    def write_default(self, month: str | None = None, output_path: Path | None = None) -> Path: ...

    def build(
        self,
        *,
        month: str | None = None,
        year: str | None = None,
        month_number: str | None = None,
    ) -> ReportData: ...

    def write_data(self, data: ReportData, output_path: Path) -> None: ...

    def render_pdf(self, data: ReportData) -> bytes: ...

    def export(self, month: str | None = None) -> tuple[list[Path], LoadJobsResult]: ...


class DocumentGateway(Protocol):
    def generate_cv_draft(
        self,
        *,
        profile: dict[str, Any],
        job_description: str,
        job: dict[str, Any],
    ) -> str: ...

    def render_cv_pdf(self, content: str, *, job_title: str) -> bytes: ...


class JobTrackerService:
    """Transport-independent application workflows for the legacy store."""

    def __init__(
        self,
        context: ApplicationContext,
        jobs: JobRepository,
        profiles: ProfileRepository,
        reports: ReportGateway,
        documents: DocumentGateway,
    ) -> None:
        self.context = context
        self.jobs = jobs
        self.profiles = profiles
        self.reports = reports
        self.documents = documents

    def list_jobs(self, *, status: str | None = None, month: str | None = None) -> LoadJobsResult:
        loaded = self.jobs.list()
        jobs = loaded.jobs
        if status:
            jobs = [(job_id, job) for job_id, job in jobs if job.status == status]
        if month:
            jobs = [(job_id, job) for job_id, job in jobs if job.date_applied.startswith(month)]
        return type(loaded)(jobs=jobs, errors=loaded.errors)

    def get_job(self, job_id: str) -> JobDetails:
        return self.jobs.get(job_id)

    def create_job(
        self,
        job: JobApplication,
        job_description: str,
        assets: Sequence[Asset] = (),
    ) -> JobMutationResult:
        application_id, created, documents = self.jobs.create(job, job_description, assets)
        return JobMutationResult(application_id, created, tuple(documents), self._refresh_report())

    def update_job(
        self,
        job_id: str,
        *,
        updates: dict[str, Any] | None = None,
        note: str = "",
        job_description: str | None = None,
    ) -> JobMutationResult:
        application_id, updated, documents = self.jobs.update(
            job_id,
            updates=updates,
            note=note,
            job_description=job_description,
        )
        return JobMutationResult(application_id, updated, tuple(documents), self._refresh_report())

    def attach_files(
        self,
        job_id: str,
        assets: Sequence[Asset],
        *,
        status: str | None = None,
        status_date: str | None = None,
        note: str = "",
        category: str = "",
    ) -> JobMutationResult:
        application_id, updated, documents = self.jobs.update(
            job_id,
            assets,
            status=status,
            status_date=status_date,
            note=note,
            category=category,
        )
        return JobMutationResult(application_id, updated, tuple(documents), self._refresh_report())

    def _refresh_report(self) -> str:
        try:
            self.reports.write_default()
        except OSError as exc:
            return f"Report refresh failed; regenerate it later: {exc}"
        return ""

    def write_report(self, month: str | None = None, output_path: Path | None = None) -> Path:
        return self.reports.write_default(month, output_path)

    def create_report(
        self,
        *,
        month: str | None = None,
        year: str | None = None,
        month_number: str | None = None,
    ) -> ReportArtifact:
        data = self.reports.build(month=month, year=year, month_number=month_number)
        period_key = self.report_period_key(data.year, data.month_number)
        path = (
            self.context.reports_dir / period_key / "report.md"
            if period_key
            else self.context.reports_dir / "report.md"
        )
        self.reports.write_data(data, path)
        return ReportArtifact(period_key or "all", path.read_text(encoding="utf-8"), data)

    def render_report_pdf(
        self,
        *,
        month: str | None = None,
        year: str | None = None,
        month_number: str | None = None,
    ) -> tuple[bytes, str]:
        data = self.reports.build(month=month, year=year, month_number=month_number)
        period_key = self.report_period_key(data.year, data.month_number)
        suffix = f"-{period_key}" if period_key else ""
        return self.reports.render_pdf(data), f"report{suffix}.pdf"

    def export_report(self, month: str | None = None) -> tuple[list[Path], LoadJobsResult]:
        return self.reports.export(month)

    def get_profile(self) -> dict[str, Any]:
        return self.profiles.get_profile()

    def save_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.profiles.save_profile(payload)

    def upload_profile_cvs(self, assets: Sequence[Asset]) -> tuple[list[str], dict[str, Any]]:
        uploaded = [self.profiles.store_profile_cv(asset) for asset in assets]
        return uploaded, self.profiles.get_profile()

    def generate_cv_draft(self, job_id: str) -> str:
        details = self.jobs.get(job_id)
        content = self.documents.generate_cv_draft(
            profile=self.profiles.get_profile(),
            job_description=details.job_description,
            job=details.job.to_metadata(),
        )
        self.jobs.save_cv_draft(job_id, content)
        return content

    def save_cv_draft(self, job_id: str, content: str) -> str:
        return self.jobs.save_cv_draft(job_id, content)

    def render_cv_pdf(self, job_id: str, content: str | None = None) -> bytes:
        details = self.jobs.get(job_id)
        selected = details.cv_draft if content is None else content
        selected = selected.strip()
        if not selected:
            raise ValueError("CV content is empty")
        return self.documents.render_cv_pdf(selected, job_title=details.job.role)

    @staticmethod
    def report_period_key(year: str, month_number: str) -> str:
        if year and month_number:
            return f"{year}-{month_number}"
        if year:
            return year
        if month_number:
            return f"all-years-{month_number}"
        return ""
