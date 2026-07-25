from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

from .loading import (
    JOB_BOOL_FIELDS,
    JOB_DATE_FIELDS,
    JOB_LIST_FIELDS,
    JOB_REQUIRED_FIELDS,
    JOB_STRING_FIELDS,
    classify_job_load_exception,
    public_job_load_message,
)
from .markdown import read_markdown_with_frontmatter
from .models import VALID_STATUSES


SCHEMA_VERSION = 1

_ASSET_DIRS = ("attachments", "cv", "evidence", "motivation_letter", "screenshots")
_SCALAR_FILE_FIELDS = ("cv_file", "motivation_letter_file")
_LIST_FILE_FIELDS = ("attachments", "evidence", "screenshots")


class DoctorFatalError(RuntimeError):
    def __init__(self, code: str, path: Path, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.path = path.as_posix()

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": str(self)}


@dataclass(frozen=True)
class DoctorIssue:
    code: str
    severity: str
    record_path: str | None
    field: str | None
    message: str


@dataclass(frozen=True)
class DoctorRecord:
    directory_path: str
    record_path: str
    parsed: bool
    company: str | None = None
    role: str | None = None
    date_applied: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class DoctorAttachment:
    path: str
    record_path: str
    sha256: str | None
    size_bytes: int | None
    referenced: bool


@dataclass(frozen=True)
class DoctorReport:
    records: tuple[DoctorRecord, ...]
    attachments: tuple[DoctorAttachment, ...]
    issues: tuple[DoctorIssue, ...]
    referenced_attachment_count: int

    @property
    def job_directory_count(self) -> int:
        return len(self.records)

    @property
    def parsed_count(self) -> int:
        return sum(record.parsed for record in self.records)

    @property
    def failed_count(self) -> int:
        return self.job_directory_count - self.parsed_count

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        issue_counts = Counter(issue.code for issue in self.issues)
        return {
            "schema_version": SCHEMA_VERSION,
            "summary": {
                "ok": self.ok,
                "job_directories": self.job_directory_count,
                "records_parsed": self.parsed_count,
                "records_failed": self.failed_count,
                "physical_attachments": len(self.attachments),
                "referenced_attachments": self.referenced_attachment_count,
                "issues": len(self.issues),
                "errors": sum(issue.severity == "error" for issue in self.issues),
                "warnings": sum(issue.severity == "warning" for issue in self.issues),
                "issues_by_code": dict(sorted(issue_counts.items())),
            },
            "records": [asdict(record) for record in self.records],
            "attachments": [asdict(attachment) for attachment in self.attachments],
            "issues": [asdict(issue) for issue in self.issues],
        }


def _relative(path: Path, jobs_dir: Path) -> str:
    return path.relative_to(jobs_dir).as_posix()


def _issue(
    code: str,
    severity: str,
    record_path: str | None,
    field: str | None,
    message: str,
) -> DoctorIssue:
    return DoctorIssue(code, severity, record_path, field, message)


def _record_error_code(exc: Exception) -> str:
    category = classify_job_load_exception(exc)
    if str(exc).startswith("Unclosed frontmatter"):
        return "unclosed_frontmatter"
    if str(exc).startswith("Invalid frontmatter object"):
        return "invalid_frontmatter_type"
    if category == "frontmatter_error":
        return "malformed_frontmatter"
    if category == "decode_error":
        return "record_encoding_error"
    if category in {"missing_file", "permission_error"} or isinstance(exc, OSError):
        return "record_read_error"
    return category


def _metadata_issues(metadata: dict[str, Any], record_path: str) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []

    for field in JOB_REQUIRED_FIELDS:
        value = metadata.get(field)
        if value is None or isinstance(value, str) and not value.strip():
            issues.append(
                _issue(
                    "missing_required_field",
                    "error",
                    record_path,
                    field,
                    f"Required metadata field {field!r} is missing or empty",
                )
            )

    for field in JOB_STRING_FIELDS:
        if field in metadata and not isinstance(metadata[field], str):
            issues.append(
                _issue(
                    "invalid_metadata_type",
                    "error",
                    record_path,
                    field,
                    f"Metadata field {field!r} must be a string",
                )
            )

    for field in JOB_LIST_FIELDS:
        if field not in metadata:
            continue
        value = metadata[field]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            issues.append(
                _issue(
                    "invalid_metadata_type",
                    "error",
                    record_path,
                    field,
                    f"Metadata field {field!r} must be a list of strings",
                )
            )

    for field in JOB_BOOL_FIELDS:
        if field in metadata and not isinstance(metadata[field], bool):
            issues.append(
                _issue(
                    "invalid_metadata_type",
                    "error",
                    record_path,
                    field,
                    f"Metadata field {field!r} must be a boolean",
                )
            )

    status = metadata.get("status")
    if isinstance(status, str) and status and status not in VALID_STATUSES:
        issues.append(
            _issue(
                "unknown_status",
                "error",
                record_path,
                "status",
                f"Unknown status: {status!r}",
            )
        )

    for field in JOB_DATE_FIELDS:
        value = metadata.get(field)
        if not isinstance(value, str) or not value:
            continue
        try:
            date.fromisoformat(value)
        except ValueError:
            issues.append(
                _issue(
                    "invalid_date",
                    "error",
                    record_path,
                    field,
                    f"Invalid ISO date in {field!r}: {value!r}",
                )
            )

    return issues


def _file_references(metadata: dict[str, Any]) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    for field in _SCALAR_FILE_FIELDS:
        value = metadata.get(field)
        if isinstance(value, str) and value:
            references.append((field, value))
    for field in _LIST_FILE_FIELDS:
        values = metadata.get(field)
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            references.extend((field, value) for value in values if value)
    return references


def _inspect_description(job_dir: Path, jobs_dir: Path, record_path: str) -> list[DoctorIssue]:
    path = job_dir / "job_description.md"
    relative_path = _relative(path, jobs_dir)
    if not path.is_file():
        return [
            _issue(
                "missing_description",
                "warning",
                record_path,
                "job_description",
                f"Job description file is missing: {relative_path}",
            )
        ]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [
            _issue(
                "description_read_error",
                "error",
                record_path,
                "job_description",
                f"Could not read {relative_path}: {type(exc).__name__}: {exc}",
            )
        ]
    if any(line.strip() and not line.lstrip().startswith("#") for line in lines):
        return []
    return [
        _issue(
            "empty_description",
            "warning",
            record_path,
            "job_description",
            f"Job description is empty or contains headings only: {relative_path}",
        )
    ]


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_jobs(jobs_dir: Path) -> DoctorReport:
    if not jobs_dir.exists():
        raise DoctorFatalError(
            "jobs_directory_not_found", jobs_dir, f"Configured jobs directory does not exist: {jobs_dir}"
        )
    if not jobs_dir.is_dir():
        raise DoctorFatalError(
            "jobs_path_not_directory", jobs_dir, f"Configured jobs path is not a directory: {jobs_dir}"
        )

    jobs_dir = jobs_dir.resolve()
    try:
        job_dirs = sorted(
            path
            for path in jobs_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
    except OSError as exc:
        raise DoctorFatalError(
            "jobs_directory_read_error",
            jobs_dir,
            f"Could not scan configured jobs directory: {type(exc).__name__}: {exc}",
        ) from exc

    records: list[DoctorRecord] = []
    attachments: list[DoctorAttachment] = []
    issues: list[DoctorIssue] = []
    references: set[str] = set()
    referenced_attachment_count = 0

    for job_dir in job_dirs:
        record_path = job_dir / "job.md"
        relative_directory = _relative(job_dir, jobs_dir)
        relative_record = _relative(record_path, jobs_dir)
        issues.extend(_inspect_description(job_dir, jobs_dir, relative_record))

        if not record_path.is_file():
            records.append(DoctorRecord(relative_directory, relative_record, False))
            issues.append(
                _issue(
                    "missing_record",
                    "error",
                    relative_record,
                    None,
                    "Job directory does not contain job.md",
                )
            )
            continue

        try:
            metadata, _body = read_markdown_with_frontmatter(record_path)
        except Exception as exc:
            records.append(DoctorRecord(relative_directory, relative_record, False))
            category = classify_job_load_exception(exc)
            issues.append(
                _issue(
                    _record_error_code(exc),
                    "error",
                    relative_record,
                    None,
                    f"{type(exc).__name__}: {public_job_load_message(category)}",
                )
            )
            continue

        records.append(
            DoctorRecord(
                relative_directory,
                relative_record,
                True,
                metadata.get("company") if isinstance(metadata.get("company"), str) else None,
                metadata.get("role") if isinstance(metadata.get("role"), str) else None,
                metadata.get("date_applied") if isinstance(metadata.get("date_applied"), str) else None,
                metadata.get("status") if isinstance(metadata.get("status"), str) else None,
            )
        )
        issues.extend(_metadata_issues(metadata, relative_record))

        applied_date = metadata.get("date_applied")
        if isinstance(applied_date, str) and applied_date and job_dir.name[:10] != applied_date:
            issues.append(
                _issue(
                    "directory_date_mismatch",
                    "warning",
                    relative_record,
                    "date_applied",
                    f"Directory date {job_dir.name[:10]!r} differs from date_applied {applied_date!r}",
                )
            )

        job_root = job_dir.resolve()
        for field, reference in _file_references(metadata):
            referenced_attachment_count += 1
            target = (job_dir / reference).resolve()
            try:
                target.relative_to(job_root)
            except ValueError:
                issues.append(
                    _issue(
                        "invalid_attachment_reference",
                        "error",
                        relative_record,
                        field,
                        f"Attachment reference escapes job directory: {reference}",
                    )
                )
                continue
            relative_target = (
                Path(relative_directory) / target.relative_to(job_root)
            ).as_posix()
            references.add(relative_target)
            if not target.is_file():
                issues.append(
                    _issue(
                        "missing_attachment",
                        "error",
                        relative_record,
                        field,
                        f"Referenced attachment does not exist: {reference}",
                    )
                )

    for job_dir in job_dirs:
        relative_record = _relative(job_dir / "job.md", jobs_dir)
        for directory_name in _ASSET_DIRS:
            asset_dir = job_dir / directory_name
            if not asset_dir.is_dir():
                continue
            try:
                files = sorted(
                    path
                    for path in asset_dir.rglob("*")
                    if path.is_file() and path.name not in {".gitkeep", "generated_cv.md"}
                )
            except OSError as exc:
                issues.append(
                    _issue(
                        "attachment_scan_error",
                        "error",
                        relative_record,
                        directory_name,
                        f"Could not scan {asset_dir}: {type(exc).__name__}: {exc}",
                    )
                )
                continue
            for path in files:
                relative_path = _relative(path, jobs_dir)
                try:
                    size_bytes = path.stat().st_size
                    digest = _hash_file(path)
                except OSError as exc:
                    size_bytes = None
                    digest = None
                    issues.append(
                        _issue(
                            "attachment_read_error",
                            "error",
                            relative_record,
                            directory_name,
                            f"Could not read {relative_path}: {type(exc).__name__}: {exc}",
                        )
                    )
                attachments.append(
                    DoctorAttachment(
                        relative_path,
                        relative_record,
                        digest,
                        size_bytes,
                        relative_path in references,
                    )
                )

    hash_groups: defaultdict[str, list[DoctorAttachment]] = defaultdict(list)
    for attachment in attachments:
        if attachment.sha256 is not None:
            hash_groups[attachment.sha256].append(attachment)
    for digest, duplicates in sorted(hash_groups.items()):
        if len(duplicates) < 2:
            continue
        paths = [attachment.path for attachment in duplicates]
        issues.append(
            _issue(
                "duplicate_attachment_sha256",
                "warning",
                duplicates[0].record_path,
                "attachments",
                f"SHA-256 {digest} is shared by: {', '.join(paths)}",
            )
        )

    issues.sort(
        key=lambda issue: (
            issue.severity,
            issue.code,
            issue.record_path or "",
            issue.field or "",
            issue.message,
        )
    )
    attachments.sort(key=lambda attachment: attachment.path)
    return DoctorReport(
        tuple(records),
        tuple(attachments),
        tuple(issues),
        referenced_attachment_count,
    )
