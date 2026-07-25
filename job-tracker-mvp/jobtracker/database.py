from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


SCHEMA_VERSION = 1

SCHEMA_SQL = """
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


def connect_database(path: Path, *, initialize: bool = True) -> sqlite3.Connection:
    if initialize:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if initialize:
        initialize_database(connection)
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema {current} is newer than supported schema {SCHEMA_VERSION}"
        )
    if current == 0:
        with connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif current != SCHEMA_VERSION:
        raise RuntimeError(f"Unsupported database schema version: {current}")
    connection.execute("PRAGMA journal_mode = WAL")


@contextmanager
def database_connection(path: Path, *, initialize: bool = True) -> Iterator[sqlite3.Connection]:
    connection = connect_database(path, initialize=initialize)
    try:
        yield connection
    finally:
        connection.close()
