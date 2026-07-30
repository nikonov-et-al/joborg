# Milestone 7 Plan - Database Evolution and Backup Lifecycle

Status: complete

Completed: 2026-07-30

Started: 2026-07-30

## Assumptions

- Only synthetic databases under test temporary directories may be migrated
  during development.
- `PRAGMA user_version` remains authoritative; a small migration audit table is
  supporting evidence, not a second version source.
- Schema version 2 must serve the migration lifecycle itself. No unrelated
  profile, AI, search, or performance schema belongs in this milestone.
- Migration backups and user-created exports are separate artifacts. Automatic
  retention may affect verified migration backups only.
- Standard-library `sqlite3`, `json`, `hashlib`, and `zipfile` are sufficient.

## Plan

1. Add an ordered version-1-to-version-2 migration, mandatory pre/post database
   checks, future/gap rejection, and transactional failure handling -> verify:
   a synthetic version-1 fixture upgrades once, reopens unchanged, and injected
   failures roll back.
2. Snapshot the live SQLite connection with the backup API before migration;
   write a checksum/version/application manifest and apply conservative
   migration-backup retention after verified startup -> verify: WAL data is in
   the snapshot, corruption blocks writes, and user exports are never pruned.
3. Add verified workspace ZIP export containing the database snapshot, managed
   files, profile, and a self-checking manifest -> verify: export validation
   works without activating the export and detects tampering.
4. Expose schema/backup/export details and recovery guidance in desktop
   Settings, while allowing supported old workspaces through the read-only
   probe -> verify: setup upgrades version 1, Settings can create and display a
   verified export, and future schemas remain blocked.
5. Document table ownership plus backup/restore procedure, run focused and full
   checks, then archive this plan and mark Milestone 7 complete in `PLAN.md` ->
   verify: recorded commands pass and active-plan next step becomes Milestone 8.

## Files likely touched

- `MILESTONE_7_PLAN.md`
- `PLAN.md`
- `docs/history/README.md`
- `docs/reference/database-schema.md`
- `docs/guides/recovery.md`
- `job-tracker-mvp/README.md`
- `job-tracker-mvp/jobtracker/backups.py`
- `job-tracker-mvp/jobtracker/database.py`
- `job-tracker-mvp/jobtracker/desktop.py`
- `job-tracker-mvp/jobtracker/doctor.py`
- `job-tracker-mvp/jobtracker/web/static/app.js`
- `job-tracker-mvp/tests/test_database_evolution.py`
- focused desktop/UI tests when behavior requires them

## Acceptance criteria

- A synthetic version-1 database upgrades automatically to version 2 and the
  process is ordered, idempotent, integrity-checked, and failure-tested.
- Every upgrade creates a verified SQLite backup and manifest before its first
  schema write.
- Unknown future versions, unsupported gaps, corrupt databases, and foreign-key
  violations are rejected without schema writes.
- Conservative retention never removes the newest usable migration backup and
  never touches user-created exports.
- A workspace export can be verified without becoming active and contains the
  database, managed files, profile, checksums, and no absolute source paths.
- Desktop Settings shows schema and backup details, creates explicit exports,
  and gives actionable restore guidance.
- Existing live workspace data is not opened by development tests or commands.

## Non-goals

- No downgrade, automatic restore, cloud backup, scheduler, encryption format,
  SQLAlchemy, Alembic, or speculative product schema.
- No retention of user-created exports.
- No deletion or mutation of live databases, workspaces, or existing backups.

## Completion evidence

- Schema version 2 adds only `schema_migrations`, which records non-personal
  migration audit metadata.
- Version-1, WAL, idempotency, future-version, unsupported-version, corruption,
  foreign-key, injected-failure, retention, export, tamper, and desktop-probe
  regressions pass.
- `python -m unittest discover -s tests` passed 168 tests with
  `LOCALAPPDATA` redirected to a blank temporary directory.
- `python -m compileall -q jobtracker tests`, JavaScript syntax validation, and
  the repository privacy check passed.
- Existing packaged artifacts were not used: they predate this source change
  and generated build outputs are outside the task's allowed inputs.
