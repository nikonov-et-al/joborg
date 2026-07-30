from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable, Iterator

from . import __version__
from .backups import (
    MigrationBackup,
    assert_database_healthy,
    create_migration_backup,
    mark_migration_backup_verified,
    prune_migration_backups,
)


SCHEMA_VERSION = 2
MIN_SUPPORTED_SCHEMA_VERSION = 1

SCHEMA_V1_SQL = """
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    legacy_folder TEXT UNIQUE,
    company TEXT NOT NULL CHECK (trim(company) <> ''),
    role TEXT NOT NULL CHECK (trim(role) <> ''),
    location TEXT NOT NULL DEFAULT '',
    job_url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    application_method TEXT NOT NULL DEFAULT '',
    date_applied TEXT NOT NULL,
    status TEXT NOT NULL,
    status_date TEXT NOT NULL DEFAULT '',
    contact_person TEXT NOT NULL DEFAULT '',
    contact_email TEXT NOT NULL DEFAULT '',
    relevant_for_arbeitsagentur INTEGER NOT NULL DEFAULT 1 CHECK (relevant_for_arbeitsagentur IN (0, 1)),
    summary TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL DEFAULT '',
    next_action_date TEXT NOT NULL DEFAULT '',
    job_description TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS application_skills (
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('required', 'nice_to_have', 'language')),
    position INTEGER NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (application_id, kind, position)
);

CREATE TABLE IF NOT EXISTS status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    event_date TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0),
    mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    source_relative_path TEXT,
    is_referenced INTEGER NOT NULL DEFAULT 1 CHECK (is_referenced IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (application_id, stored_path)
);

CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    source_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_items (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source_id TEXT,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS document_versions (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    document_type TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    content TEXT NOT NULL,
    approval_status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    UNIQUE (application_id, document_type, version)
);

CREATE TABLE IF NOT EXISTS ai_runs (
    id TEXT PRIMARY KEY,
    application_id TEXT REFERENCES applications(id) ON DELETE SET NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    input_hash TEXT NOT NULL DEFAULT '',
    output_document_version_id TEXT REFERENCES document_versions(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    approval_status TEXT NOT NULL DEFAULT 'draft'
);

CREATE TABLE IF NOT EXISTS migration_sources (
    source_key TEXT PRIMARY KEY,
    application_id TEXT NOT NULL UNIQUE REFERENCES applications(id) ON DELETE CASCADE,
    legacy_folder TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_attachment_count INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_applications_date ON applications(date_applied);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_documents_application ON documents(application_id);
"""

SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    applied_at TEXT NOT NULL,
    app_version TEXT NOT NULL
);
"""

SCHEMA_SQL = SCHEMA_V1_SQL + SCHEMA_V2_SQL


class DatabaseVersionError(RuntimeError):
    pass


class DatabaseMigrationError(RuntimeError):
    def __init__(
        self,
        source_version: int,
        target_version: int,
        backup: MigrationBackup,
    ) -> None:
        self.source_version = source_version
        self.target_version = target_version
        self.backup_path = backup.path
        super().__init__(
            f"Database upgrade from schema {source_version} to {target_version} failed. "
            f"Close Job Tracker and restore jobtracker.sqlite3 from {backup.path} "
            "before retrying; see docs/guides/recovery.md."
        )


def _applied_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_migration(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        """
        INSERT INTO schema_migrations (version, applied_at, app_version)
        VALUES (?, ?, ?)
        """,
        (version, _applied_at(), __version__),
    )


def migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            applied_at TEXT NOT NULL,
            app_version TEXT NOT NULL
        )
        """
    )
    _record_migration(connection, 2)


MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: migrate_v1_to_v2,
}


def connect_database(path: Path, *, initialize: bool = True) -> sqlite3.Connection:
    existed = path.exists()
    if initialize:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        if initialize:
            initialize_database(connection, database=path, existed=existed)
    except Exception:
        connection.close()
        raise
    return connection


def _is_empty_database(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' AND type IN ('table', 'index', 'view', 'trigger')
            LIMIT 1
            """
        ).fetchone()
        is None
    )


def _initialize_new_database(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    with connection:
        _record_migration(connection, SCHEMA_VERSION)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    assert_database_healthy(connection)


def initialize_database(
    connection: sqlite3.Connection,
    *,
    database: Path | None = None,
    existed: bool = False,
) -> None:
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise DatabaseVersionError(
            f"Database schema {current} is newer than supported schema {SCHEMA_VERSION}"
        )
    if current == 0:
        if existed and not _is_empty_database(connection):
            raise DatabaseVersionError(
                "Unversioned database contains unsupported objects; no changes were made"
            )
        _initialize_new_database(connection)
    elif current < MIN_SUPPORTED_SCHEMA_VERSION:
        raise DatabaseVersionError(f"Unsupported database schema version: {current}")
    elif current < SCHEMA_VERSION:
        if database is None:
            raise DatabaseVersionError(
                "Database path is required to create a pre-upgrade recovery backup"
            )
        assert_database_healthy(connection)
        source_version = current
        backup = create_migration_backup(
            connection,
            database,
            source_schema_version=source_version,
            target_schema_version=SCHEMA_VERSION,
            app_version=__version__,
        )
        try:
            while current < SCHEMA_VERSION:
                migration = MIGRATIONS.get(current)
                if migration is None:
                    raise DatabaseVersionError(
                        f"Unsupported database schema gap at version {current}"
                    )
                target = current + 1
                connection.execute("BEGIN IMMEDIATE")
                try:
                    migration(connection)
                    connection.execute(f"PRAGMA user_version = {target}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                current = target
            assert_database_healthy(connection)
        except Exception as exc:
            raise DatabaseMigrationError(
                source_version,
                SCHEMA_VERSION,
                backup,
            ) from exc
        mark_migration_backup_verified(backup)
        prune_migration_backups(database)
    connection.execute("PRAGMA journal_mode = WAL")


@contextmanager
def database_connection(path: Path, *, initialize: bool = True) -> Iterator[sqlite3.Connection]:
    connection = connect_database(path, initialize=initialize)
    try:
        yield connection
    finally:
        connection.close()
