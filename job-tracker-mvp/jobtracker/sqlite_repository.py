from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import mimetypes
from pathlib import Path
import sqlite3
from typing import Any, Sequence
from uuid import uuid4

from .application import Asset, DocumentRef, JobDetails, RecordNotFoundError
from .database import database_connection
from .file_store import ManagedFileStore
from .loading import LoadJobsResult, validate_job_load_metadata
from .models import JobApplication, today_iso
from .services import EDITABLE_JOB_FIELDS, JOB_FILE_CATEGORIES, build_job_body


_SKILL_FIELDS = {
    "required": "required_skills",
    "nice_to_have": "nice_to_have_skills",
    "language": "language_requirements",
}
_DOCUMENT_FIELDS = {
    "cv": "cv_file",
    "motivation_letter": "motivation_letter_file",
    "evidence": "evidence",
    "screenshots": "screenshots",
    "attachments": "attachments",
    "job_description": "attachments",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_job(job: JobApplication) -> None:
    if not isinstance(job.company, str) or not job.company.strip():
        raise ValueError("company is required")
    if not isinstance(job.role, str) or not job.role.strip():
        raise ValueError("role is required")
    validate_job_load_metadata(job.to_metadata())


def insert_application(
    connection: sqlite3.Connection,
    application_id: str,
    job: JobApplication,
    *,
    job_description: str,
    body: str,
    legacy_folder: str | None = None,
) -> None:
    _validate_job(job)
    timestamp = _now()
    connection.execute(
        """
        INSERT INTO applications (
            id, legacy_folder, company, role, location, job_url, source,
            application_method, date_applied, status, status_date,
            contact_person, contact_email, relevant_for_arbeitsagentur,
            summary, next_action, next_action_date, job_description, body,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            application_id,
            legacy_folder,
            job.company.strip(),
            job.role.strip(),
            job.location,
            job.job_url,
            job.source,
            job.application_method,
            job.date_applied,
            job.status,
            job.status_date,
            job.contact_person,
            job.contact_email,
            int(job.relevant_for_arbeitsagentur),
            job.summary,
            job.next_action,
            job.next_action_date,
            job_description,
            body,
            timestamp,
            timestamp,
        ),
    )
    replace_skills(connection, application_id, job)


def replace_skills(
    connection: sqlite3.Connection,
    application_id: str,
    job: JobApplication,
) -> None:
    connection.execute(
        "DELETE FROM application_skills WHERE application_id = ?", (application_id,)
    )
    for kind, field in _SKILL_FIELDS.items():
        for position, value in enumerate(getattr(job, field)):
            connection.execute(
                "INSERT INTO application_skills (application_id, kind, position, value) VALUES (?, ?, ?, ?)",
                (application_id, kind, position, value),
            )


def insert_document(
    connection: sqlite3.Connection,
    application_id: str,
    *,
    category: str,
    original_filename: str,
    stored_path: str,
    sha256: str,
    size: int,
    mime_type: str | None = None,
    source_relative_path: str | None = None,
    is_referenced: bool = True,
) -> None:
    connection.execute(
        """
        INSERT INTO documents (
            id, application_id, category, original_filename, stored_path,
            sha256, size, mime_type, source_relative_path, is_referenced, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            application_id,
            category,
            original_filename,
            stored_path,
            sha256,
            size,
            mime_type
            or mimetypes.guess_type(original_filename)[0]
            or "application/octet-stream",
            source_relative_path,
            int(is_referenced),
            _now(),
        ),
    )


class SQLiteJobRepository:
    def __init__(self, database: Path, files_dir: Path) -> None:
        self.database = database
        self.files_dir = files_dir
        self.file_store = ManagedFileStore(files_dir)

    def _row(self, connection: sqlite3.Connection, application_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Job not found: {application_id}")
        return row

    def _job(self, connection: sqlite3.Connection, row: sqlite3.Row) -> JobApplication:
        metadata = {
            field: row[field]
            for field in JobApplication.__dataclass_fields__
            if field in row.keys()
        }
        metadata["relevant_for_arbeitsagentur"] = bool(
            row["relevant_for_arbeitsagentur"]
        )
        for field in _SKILL_FIELDS.values():
            metadata[field] = []
        for skill in connection.execute(
            """
            SELECT kind, value FROM application_skills
            WHERE application_id = ? ORDER BY kind, position
            """,
            (row["id"],),
        ):
            metadata[_SKILL_FIELDS[skill["kind"]]].append(skill["value"])
        metadata.update(
            {
                "cv_file": "",
                "motivation_letter_file": "",
                "evidence": [],
                "screenshots": [],
                "attachments": [],
            }
        )
        return JobApplication.from_metadata(metadata)

    def _documents(
        self, connection: sqlite3.Connection, application_id: str
    ) -> tuple[DocumentRef, ...]:
        rows = connection.execute(
            """
            SELECT id, category, original_filename, size, mime_type, sha256
            FROM documents WHERE application_id = ? AND is_referenced = 1
            ORDER BY created_at, id
            """,
            (application_id,),
        )
        return tuple(DocumentRef(**dict(row)) for row in rows)

    def list(self) -> LoadJobsResult:
        with database_connection(self.database) as connection:
            rows = connection.execute(
                "SELECT * FROM applications ORDER BY date_applied, id"
            ).fetchall()
            jobs = [(row["id"], self._job(connection, row)) for row in rows]
        return LoadJobsResult(jobs, [])

    def get(self, job_id: str) -> JobDetails:
        with database_connection(self.database) as connection:
            row = self._row(connection, job_id)
            job = self._job(connection, row)
            draft = connection.execute(
                """
                SELECT content FROM document_versions
                WHERE application_id = ? AND document_type = 'cv'
                ORDER BY version DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            return JobDetails(
                application_id=job_id,
                job=job,
                body=row["body"],
                job_description=row["job_description"],
                cv_draft=draft["content"] if draft else "",
                documents=self._documents(connection, job_id),
            )

    def _store_assets(
        self,
        connection: sqlite3.Connection,
        application_id: str,
        assets: Sequence[Asset],
    ) -> tuple[list[DocumentRef], list[str]]:
        stored: list[DocumentRef] = []
        created: list[str] = []
        try:
            for asset in assets:
                category = asset.category or "attachments"
                if JOB_FILE_CATEGORIES.get(category) is None:
                    raise ValueError(f"Invalid file category: {asset.category}")
                managed = self.file_store.store_bytes(asset.filename, asset.content)
                created.append(managed.stored_path)
                insert_document(
                    connection,
                    application_id,
                    category=category,
                    original_filename=managed.original_filename,
                    stored_path=managed.stored_path,
                    sha256=managed.sha256,
                    size=managed.size,
                    mime_type=managed.mime_type,
                )
                row = connection.execute(
                    """SELECT id, category, original_filename, size, mime_type, sha256
                    FROM documents WHERE application_id = ? AND stored_path = ?""",
                    (application_id, managed.stored_path),
                ).fetchone()
                stored.append(DocumentRef(**dict(row)))
        except Exception:
            for stored_path in created:
                self.file_store.remove(stored_path)
            raise
        return stored, created

    def create(
        self,
        job: JobApplication,
        job_description: str,
        assets: Sequence[Asset] = (),
    ) -> tuple[str, JobApplication, list[DocumentRef]]:
        application_id = str(uuid4())
        created_files: list[str] = []
        try:
            with database_connection(self.database) as connection, connection:
                insert_application(
                    connection,
                    application_id,
                    job,
                    job_description=job_description,
                    body=build_job_body(job_description),
                )
                stored, created_files = self._store_assets(
                    connection, application_id, assets
                )
                connection.execute(
                    """
                    INSERT INTO status_events
                        (application_id, status, event_date, note, category, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (application_id, job.status, job.status_date or job.date_applied, "Application record created", "", _now()),
                )
            return application_id, self.get(application_id).job, stored
        except Exception:
            for stored_path in created_files:
                self.file_store.remove(stored_path)
            raise

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
    ) -> tuple[str, JobApplication, list[DocumentRef]]:
        changes = dict(updates or {})
        if status is not None:
            changes["status"] = status
        if status_date is not None:
            changes["status_date"] = status_date
        unknown = set(changes) - EDITABLE_JOB_FIELDS
        if unknown:
            raise ValueError(f"Fields cannot be edited: {', '.join(sorted(unknown))}")

        created_files: list[str] = []
        try:
            with database_connection(self.database) as connection, connection:
                row = self._row(connection, job_id)
                original = self._job(connection, row)
                metadata = original.to_metadata()
                metadata.update(changes)
                if "status" in changes and changes["status"] != original.status and not changes.get("status_date"):
                    metadata["status_date"] = today_iso()
                updated = JobApplication.from_metadata(metadata)
                _validate_job(updated)
                scalar_columns = [
                    field
                    for field in EDITABLE_JOB_FIELDS
                    if field not in _SKILL_FIELDS.values()
                ]
                assignments = ", ".join(f"{field} = ?" for field in scalar_columns)
                values = [
                    int(getattr(updated, field))
                    if field == "relevant_for_arbeitsagentur"
                    else getattr(updated, field)
                    for field in scalar_columns
                ]
                description = row["job_description"] if job_description is None else job_description
                body = row["body"]
                event_lines = []
                status_changed = updated.status != original.status
                if status_changed:
                    event_lines.extend(
                        [f"- Status: {updated.status}", f"- Status date: {updated.status_date}"]
                    )
                if category:
                    event_lines.append(f"- Category: {category}")
                if note:
                    event_lines.append(f"- Note: {note}")
                if event_lines:
                    body = body.rstrip() + "\n\n## Update - " + today_iso() + "\n\n" + "\n".join(event_lines) + "\n"
                connection.execute(
                    f"UPDATE applications SET {assignments}, job_description = ?, body = ?, updated_at = ? WHERE id = ?",
                    (*values, description, body, _now(), job_id),
                )
                replace_skills(connection, job_id, updated)
                stored, created_files = self._store_assets(connection, job_id, assets)
                if status_changed or note or category or stored:
                    connection.execute(
                        """
                        INSERT INTO status_events
                            (application_id, status, event_date, note, category, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (job_id, updated.status, updated.status_date or today_iso(), note, category, _now()),
                    )
            return job_id, self.get(job_id).job, stored
        except Exception:
            for stored_path in created_files:
                self.file_store.remove(stored_path)
            raise

    def save_cv_draft(self, job_id: str, content: str) -> str:
        with database_connection(self.database) as connection, connection:
            self._row(connection, job_id)
            version = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 FROM document_versions
                WHERE application_id = ? AND document_type = 'cv'
                """,
                (job_id,),
            ).fetchone()[0]
            version_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO document_versions
                    (id, application_id, document_type, version, content, approval_status, created_at)
                VALUES (?, ?, 'cv', ?, ?, 'draft', ?)
                """,
                (version_id, job_id, version, content.rstrip() + "\n", _now()),
            )
        return version_id
