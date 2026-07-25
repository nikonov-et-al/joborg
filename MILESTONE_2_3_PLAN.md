# Milestones 2 and 3 Implementation Plan

## Assumptions

- Milestones 0 and 1 in the current working tree are the accepted baseline and
  must remain compatible while storage changes.
- The checked-in `job-tracker-mvp/jobs/` tree is legacy source data. Migration
  reads it but never edits, renames, or deletes it.
- Python's standard-library `sqlite3` is sufficient for the first schema and
  migration. The plan's SQLAlchemy/Alembic stack is recommended rather than an
  acceptance requirement, so avoiding two new dependencies is the smallest
  reliable implementation.
- Profiles, generated reports, and LLM behavior remain file-backed during this
  step. SQLite becomes primary for job metadata, status history, job
  descriptions, CV drafts, and imported attachment metadata.
- Milestone 4 may later replace the first-pass per-application file layout with
  content-addressed managed storage. Milestone 3 only needs safe copied files
  with verified hashes.

## Plan

1. Add a deterministic `AppPaths` value object and runtime path resolver. Use
   an explicit data-dir override for tests and recovery; otherwise use the
   operating system's application-data convention. Resolve all runtime paths
   independently of the current working directory. -> verify: two different
   working directories produce the same paths, an explicit temporary data dir
   redirects every writable path, and legacy paths are never created or
   modified as a side effect.
2. Make package metadata and repository hygiene deterministic. Keep
   `pyproject.toml` as the version source, expose it through package metadata,
   and add ignore rules for environments, caches, logs, build output, runtime
   data, reports, backups, and personal profile/CV files. Do not delete or
   untrack existing user files in this milestone. -> verify: CLI/package
   versions agree and ignore patterns cover new runtime outputs.
3. Add a versioned SQLite initializer using `PRAGMA user_version`, foreign keys,
   WAL mode, and the initial Milestone 3 tables: `applications`,
   `application_skills`, `status_events`, `documents`, `profiles`,
   `profile_items`, `document_versions`, `ai_runs`, and `migration_sources`.
   -> verify: initialization is repeatable, schema version is explicit, and
   required uniqueness/foreign-key constraints reject invalid duplicates.
4. Implement `SQLiteJobRepository` behind the Milestone 1 repository contract.
   New records receive UUID identifiers; job metadata, description, timeline,
   assets, and CV drafts round-trip transactionally. -> verify: the existing
   repository contract behavior passes with SQLite and failed writes roll back.
5. Implement a read-only legacy migration service with `preview`, `execute`,
   and `verify`. Preview performs no writes. Execute imports each legacy record
   in a transaction, deterministically reuses the prior UUID on repeat runs,
   copies referenced/physical attachments, verifies SHA-256 before commit, and
   records legacy folder and source hashes. Verify independently compares
   counts and hashes and reports corruption, missing files, skips, and extras.
   -> verify: preview leaves destination absent/unchanged, execute imports all
   healthy fixtures, second execute creates no duplicates, verify detects a
   modified copied file, and source snapshots remain unchanged.
6. Add `job migrate preview|execute|verify` and wire normal CLI/API construction
   to SQLite while retaining the legacy repository for explicit migration and
   doctor operations. -> verify: migration JSON is machine-readable, failures
   produce non-zero exit codes, and CLI/API share the SQLite-backed service.
7. Run targeted tests, the full available suite, `job doctor --json`, migration
   against a temporary destination, legacy manifest checks, and
   `git diff --check`. Record results and update `PLAN.md` only after acceptance.

## Files likely touched

- `MILESTONE_2_3_PLAN.md`
- `PLAN.md`
- `job-tracker-mvp/.gitignore`
- `job-tracker-mvp/pyproject.toml`
- `job-tracker-mvp/jobtracker/__init__.py`
- `job-tracker-mvp/jobtracker/config.py`
- `job-tracker-mvp/jobtracker/paths.py`
- `job-tracker-mvp/jobtracker/database.py`
- `job-tracker-mvp/jobtracker/sqlite_repository.py`
- `job-tracker-mvp/jobtracker/migration.py`
- `job-tracker-mvp/jobtracker/repositories.py`
- `job-tracker-mvp/jobtracker/services.py`
- `job-tracker-mvp/jobtracker/cli.py`
- focused Milestones 2 and 3 tests
- `job-tracker-mvp/README.md`

## Acceptance criteria

- Runtime data resolution is independent of the launch directory and fully
  overrideable in tests.
- Legacy records and attachments are unchanged.
- SQLite is the primary job metadata store and new records use stable UUIDs.
- Preview performs no writes.
- Execute is transactional per source record and safely resumable.
- A repeated execute imports no duplicate applications or documents.
- Verify detects record-count, missing-file, and copied-file hash corruption.
- All 27 legacy records and 7 attachments import and verify in an isolated
  temporary destination.
- Targeted and broader relevant tests pass in the project environment.

## Non-goals

- No deletion or untracking of currently committed personal/runtime artifacts.
- No in-place edits to legacy Markdown or attachment files.
- No desktop packaging, frontend rewrite, Node.js, Tauri, RAG, vector database,
  profile redesign, LLM provider change, or content-addressed file store.
- No database backfill for AI provenance beyond creating the initial tables.
- No generalized ORM/repository framework or speculative schema abstraction.
