from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from .database import database_connection
from .doctor import inspect_jobs
from .file_store import ManagedFileStore, StoredFile
from .markdown import read_markdown_with_frontmatter
from .models import JobApplication
from .reports import load_jobs
from .sqlite_repository import insert_application, insert_document


_ASSET_DIRS = ("attachments", "cv", "evidence", "motivation_letter", "screenshots")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_manifest(path: Path) -> tuple[str, dict[str, str]]:
    files = {
        item.relative_to(path).as_posix(): hash_file(item)
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), files


def _read_legacy_markdown(path: Path) -> tuple[dict[str, Any], str]:
    metadata, _normalized_body = read_markdown_with_frontmatter(path)
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    closing_index = next(
        index
        for index, line in enumerate(lines[1:], start=1)
        if line.strip() == "---"
    )
    return metadata, "".join(lines[closing_index + 1 :]).lstrip("\r\n")


def legacy_assets(job_dir: Path) -> list[Path]:
    assets: list[Path] = []
    for name in _ASSET_DIRS:
        directory = job_dir / name
        if not directory.is_dir():
            continue
        assets.extend(
            path
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.name not in {".gitkeep", "generated_cv.md"}
        )
    return assets


def _referenced_assets(job: JobApplication) -> set[str]:
    references = set(job.attachments + job.evidence + job.screenshots)
    if job.cv_file:
        references.add(job.cv_file)
    if job.motivation_letter_file:
        references.add(job.motivation_letter_file)
    return references


@dataclass(frozen=True)
class MigrationReport:
    mode: str
    records_discovered: int
    records_importable: int
    attachments: int
    imported: int = 0
    skipped: int = 0
    verified: int = 0
    warnings: tuple[dict[str, Any], ...] = ()
    blocking_errors: tuple[dict[str, Any], ...] = ()
    duplicates: tuple[dict[str, Any], ...] = ()
    unknown_statuses: tuple[dict[str, Any], ...] = ()
    expected_destination_changes: int = 0
    destination_applications: int = 0
    destination_migration_sources: int = 0
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _destination_counts(database: Path) -> tuple[int, int]:
    if not database.is_file():
        return 0, 0
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        application_count = connection.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0]
        source_count = connection.execute(
            "SELECT COUNT(*) FROM migration_sources"
        ).fetchone()[0]
        return int(application_count), int(source_count)
    except sqlite3.Error:
        return 0, 0
    finally:
        if "connection" in locals():
            connection.close()


def preview_migration(legacy_jobs_dir: Path, database: Path) -> MigrationReport:
    doctor = inspect_jobs(legacy_jobs_dir)
    loaded = load_jobs(legacy_jobs_dir)
    warnings = tuple(
        asdict(issue) for issue in doctor.issues if issue.severity == "warning"
    )
    errors = [asdict(issue) for issue in doctor.issues if issue.severity == "error"]
    known_paths = {(error.get("record_path"), error.get("code")) for error in errors}
    for error in loaded.errors:
        key = (error.path, error.code)
        if key not in known_paths:
            errors.append(asdict(error))
            known_paths.add(key)
    applications, sources = _destination_counts(database)
    duplicates = tuple(
        issue for issue in warnings if issue.get("code") == "duplicate_attachment_sha256"
    )
    unknown_statuses = tuple(
        issue for issue in errors if issue.get("code") == "unknown_status"
    )
    return MigrationReport(
        mode="preview",
        records_discovered=doctor.job_directory_count,
        records_importable=loaded.loaded_count,
        attachments=len(doctor.attachments),
        warnings=warnings,
        blocking_errors=tuple(errors),
        duplicates=duplicates,
        unknown_statuses=unknown_statuses,
        expected_destination_changes=max(loaded.loaded_count - sources, 0),
        destination_applications=applications,
        destination_migration_sources=sources,
        ok=not errors,
    )


def _application_id(legacy_folder: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"jobtracker:legacy:{legacy_folder}"))


def _copy_asset(source: Path, file_store: ManagedFileStore) -> StoredFile:
    source_hash = hash_file(source)
    return file_store.store_file(source, expected_sha256=source_hash)


def execute_migration(
    legacy_jobs_dir: Path,
    database: Path,
    files_dir: Path,
) -> MigrationReport:
    preview = preview_migration(legacy_jobs_dir, database)
    if preview.blocking_errors:
        return MigrationReport(**{**preview.to_dict(), "mode": "execute", "ok": False})

    imported = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    loaded = load_jobs(legacy_jobs_dir)
    file_store = ManagedFileStore(files_dir)
    with database_connection(database) as connection:
        for job_dir, job in loaded.jobs:
            source_hash, _manifest = directory_manifest(job_dir)
            source_key = f"{job_dir.name}/job.md"
            existing = connection.execute(
                "SELECT source_hash FROM migration_sources WHERE source_key = ?",
                (source_key,),
            ).fetchone()
            if existing:
                if existing["source_hash"] == source_hash:
                    skipped += 1
                    continue
                errors.append(
                    {
                        "code": "source_changed_after_import",
                        "record_path": source_key,
                        "message": "Legacy source differs from its imported manifest.",
                    }
                )
                continue

            application_id = _application_id(source_key)
            created_files: list[str] = []
            try:
                metadata, body = _read_legacy_markdown(job_dir / "job.md")
                job = JobApplication.from_metadata(metadata)
                description_path = job_dir / "job_description.md"
                description = (
                    description_path.read_bytes().decode("utf-8")
                    if description_path.is_file()
                    else ""
                )
                with connection:
                    insert_application(
                        connection,
                        application_id,
                        job,
                        job_description=description,
                        body=body,
                        legacy_folder=job_dir.name,
                    )
                    references = _referenced_assets(job)
                    for source in legacy_assets(job_dir):
                        relative = source.relative_to(job_dir)
                        managed = _copy_asset(source, file_store)
                        created_files.append(managed.stored_path)
                        category = relative.parts[0]
                        insert_document(
                            connection,
                            application_id,
                            category=category,
                            original_filename=managed.original_filename,
                            stored_path=managed.stored_path,
                            sha256=managed.sha256,
                            size=managed.size,
                            mime_type=managed.mime_type,
                            source_relative_path=relative.as_posix(),
                            is_referenced=relative.as_posix() in references,
                        )
                    draft = job_dir / "cv" / "generated_cv.md"
                    if draft.is_file():
                        connection.execute(
                            """
                            INSERT INTO document_versions (
                                id, application_id, document_type, version,
                                content, approval_status, created_at
                            ) VALUES (?, ?, 'cv', 1, ?, 'draft', ?)
                            """,
                            (str(uuid4()), application_id, draft.read_bytes().decode("utf-8"), _now()),
                        )
                    connection.execute(
                        """
                        INSERT INTO status_events (
                            application_id, status, event_date, note, category, created_at
                        ) VALUES (?, ?, ?, 'Imported from legacy metadata', 'migration', ?)
                        """,
                        (application_id, job.status, job.status_date or job.date_applied, _now()),
                    )
                    connection.execute(
                        """
                        INSERT INTO migration_sources (
                            source_key, application_id, legacy_folder, source_path,
                            source_hash, source_attachment_count, imported_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_key,
                            application_id,
                            job_dir.name,
                            str(job_dir.resolve()),
                            source_hash,
                            len(legacy_assets(job_dir)),
                            _now(),
                        ),
                    )
                imported += 1
            except Exception as exc:
                for stored_path in created_files:
                    file_store.remove(stored_path)
                errors.append(
                    {
                        "code": "import_failed",
                        "record_path": source_key,
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )

    applications, sources = _destination_counts(database)
    return MigrationReport(
        mode="execute",
        records_discovered=preview.records_discovered,
        records_importable=preview.records_importable,
        attachments=preview.attachments,
        imported=imported,
        skipped=skipped,
        warnings=preview.warnings,
        blocking_errors=tuple(errors),
        duplicates=preview.duplicates,
        unknown_statuses=preview.unknown_statuses,
        expected_destination_changes=preview.expected_destination_changes,
        destination_applications=applications,
        destination_migration_sources=sources,
        ok=not errors,
    )


def verify_migration(
    legacy_jobs_dir: Path,
    database: Path,
    files_dir: Path,
) -> MigrationReport:
    preview = preview_migration(legacy_jobs_dir, database)
    errors: list[dict[str, Any]] = list(preview.blocking_errors)
    verified = 0
    if not database.is_file():
        errors.append(
            {"code": "database_missing", "record_path": "", "message": "Destination database does not exist."}
        )
    else:
        file_store = ManagedFileStore(files_dir)
        with database_connection(database, initialize=False) as connection:
            for job_dir, _job in load_jobs(legacy_jobs_dir).jobs:
                source_key = f"{job_dir.name}/job.md"
                migration = connection.execute(
                    "SELECT * FROM migration_sources WHERE source_key = ?", (source_key,)
                ).fetchone()
                if migration is None:
                    errors.append(
                        {"code": "record_missing", "record_path": source_key, "message": "Record was not imported."}
                    )
                    continue
                actual_source_hash, _manifest = directory_manifest(job_dir)
                if actual_source_hash != migration["source_hash"]:
                    errors.append(
                        {"code": "source_hash_mismatch", "record_path": source_key, "message": "Legacy source hash differs from imported manifest."}
                    )
                documents = connection.execute(
                    "SELECT stored_path, source_relative_path, sha256 FROM documents WHERE application_id = ?",
                    (migration["application_id"],),
                ).fetchall()
                source_assets = {path.relative_to(job_dir).as_posix(): path for path in legacy_assets(job_dir)}
                if len(documents) != len(source_assets):
                    errors.append(
                        {"code": "attachment_count_mismatch", "record_path": source_key, "message": "Imported attachment count differs from legacy source."}
                    )
                for document in documents:
                    source_relative_path = document["source_relative_path"]
                    source = source_assets.get(source_relative_path)
                    try:
                        destination = file_store.resolve(document["stored_path"])
                    except ValueError:
                        errors.append(
                            {"code": "attachment_path_invalid", "record_path": f"{job_dir.name}/{source_relative_path or document['stored_path']}", "message": "Stored attachment path escapes managed file storage."}
                        )
                        continue
                    if source is None:
                        errors.append(
                            {"code": "attachment_source_missing", "record_path": f"{job_dir.name}/{source_relative_path or document['stored_path']}", "message": "Imported attachment has no matching legacy source."}
                        )
                        continue
                    expected = hash_file(source)
                    if document["sha256"] != expected:
                        errors.append(
                            {"code": "attachment_metadata_hash_mismatch", "record_path": f"{job_dir.name}/{source_relative_path}", "message": "Stored attachment hash metadata differs from source."}
                        )
                    if not destination.is_file():
                        errors.append(
                            {"code": "attachment_missing", "record_path": f"{job_dir.name}/{source_relative_path}", "message": "Copied attachment is missing."}
                        )
                    elif hash_file(destination) != expected:
                        errors.append(
                            {"code": "attachment_hash_mismatch", "record_path": f"{job_dir.name}/{source_relative_path}", "message": "Copied attachment differs from source."}
                        )
                if not any(error.get("record_path", "").startswith(job_dir.name) for error in errors):
                    verified += 1

    applications, sources = _destination_counts(database)
    if sources != preview.records_importable:
        errors.append(
            {"code": "record_count_mismatch", "record_path": "", "message": f"Imported source count {sources} differs from importable count {preview.records_importable}."}
        )
    return MigrationReport(
        mode="verify",
        records_discovered=preview.records_discovered,
        records_importable=preview.records_importable,
        attachments=preview.attachments,
        verified=verified,
        warnings=preview.warnings,
        blocking_errors=tuple(errors),
        duplicates=preview.duplicates,
        unknown_statuses=preview.unknown_statuses,
        destination_applications=applications,
        destination_migration_sources=sources,
        ok=not errors,
    )
