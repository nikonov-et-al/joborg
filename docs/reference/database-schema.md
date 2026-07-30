# Database Schema Reference

Status: current

Last updated: 2026-07-30

## Purpose

Describe the persisted SQLite contract, table ownership, and upgrade rules.
`job-tracker-mvp/jobtracker/database.py` is the executable source of truth.

## Version contract

- `PRAGMA user_version` is authoritative. Current version: 2.
- Supported existing versions upgrade one step at a time through explicit
  functions such as `migrate_v1_to_v2`.
- Version 1 to 2 adds `schema_migrations`, the audit record used by this
  migration lifecycle. It adds no speculative product data.
- A migration runs in an explicit SQLite transaction. Version changes commit
  with that migration.
- Integrity and foreign-key checks run before the recovery snapshot and after
  the final migration.
- Unknown future versions, unversioned databases containing objects, and gaps
  without a registered migration are rejected without schema writes.
- Downgrade is unsupported.

## Table ownership

| Table | Owner and current use |
|---|---|
| `applications` | SQLite job repository; primary application record |
| `application_skills` | SQLite job repository; ordered skills by application |
| `status_events` | SQLite job repository; append-only status history |
| `documents` | SQLite job repository and managed file store; file metadata |
| `document_versions` | SQLite document gateway; generated CV draft versions |
| `migration_sources` | Read-only legacy importer; import identity and verification |
| `schema_migrations` | Database initializer; non-personal upgrade audit |
| `profiles` | Reserved version-1 table; dormant while profile files remain authoritative |
| `profile_items` | Reserved version-1 table; dormant |
| `ai_runs` | Reserved version-1 table; dormant |

Dormant version-1 tables remain for compatibility. New fields or tables are
added only with the feature that owns them.

## Backup contract

Before the first schema write, SQLite's backup API creates:

```text
<workspace>/backups/migrations/<UTC>-v<source>-to-v<target>-<id>/
|-- jobtracker.sqlite3
`-- manifest.json
```

The manifest contains only artifact type, UTC time, application/schema
versions, byte size, SHA-256, and verification state. It contains no source
absolute path or personal record content.

Verified migration backups use conservative retention: keep at least the ten
newest, keep any less than 365 days old, never remove an unverified backup, and
never inspect or remove user-created exports.

## Non-goals

- No migration framework dependency, downgrade, automatic restore, or backup
  scheduler.
- No schema removal solely because a compatible table is currently dormant.
