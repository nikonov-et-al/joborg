# Milestone 4 Implementation Plan

## Assumptions

- The uncommitted Milestones 0 through 3 working tree is the accepted baseline
  for this step and must be preserved.
- The checked-in `job-tracker-mvp/jobs/` tree remains read-only legacy source
  data. Milestone 4 may copy its attachments but never edits, moves, or deletes
  them.
- The existing `documents` table already contains the required metadata fields,
  so Milestone 4 does not require a database schema change.
- UUID-based storage names satisfy the plan's collision and path-safety
  requirements without introducing content deduplication policy. SHA-256 remains
  integrity metadata, not identity, in this milestone.
- Existing application-service return values may continue to identify a job by
  a path whose final component is the application UUID; callers must not assume
  attachments live below that path after this milestone.

## Plan

1. Add a focused managed-file-store adapter rooted at `AppPaths.files_dir`.
   Store each binary at a UUID-sharded relative path, retain only a normalized
   safe suffix in the physical name, write through a sibling temporary file,
   calculate SHA-256 while writing, and expose guarded path resolution and
   cleanup. -> verify: traversal and absolute paths are rejected, stored paths
   stay below the configured root, bytes/hash/size/MIME metadata match, duplicate
   original filenames produce distinct paths, and temporary files are removed
   after failures.
2. Route `SQLiteJobRepository` attachment creation and updates through the
   managed file store. Insert document metadata only after a verified file
   write, and remove newly created files when the surrounding SQLite transaction
   fails. -> verify: create/update round trips preserve categories and original
   filenames, physical paths use the managed layout, and injected database
   failures leave no document row or orphan file.
3. Route legacy migration through the same managed file store. Preserve each
   legacy relative path in `source_relative_path`, store the new managed path in
   `stored_path`, and make verification resolve the source and destination from
   those separate fields. -> verify: all source hashes match copied hashes,
   repeat execution creates no duplicates, corruption is detected, a copy
   failure rolls back metadata and removes copied files, and legacy manifests
   remain unchanged.
4. Add focused managed-storage tests and update existing SQLite/migration tests
   to assert the new contract rather than the Milestone 3 per-application file
   layout. -> verify: targeted file-store, repository, and migration tests pass.
5. Run the full project suite, migration preview/execute/verify against an
   isolated destination, `job doctor --json`, the legacy aggregate manifest
   check, and `git diff --check`. Update `PLAN.md` and README documentation only
   after all Milestone 4 acceptance checks pass.

## Files likely touched

- `MILESTONE_4_PLAN.md`
- `PLAN.md`
- `job-tracker-mvp/README.md`
- `job-tracker-mvp/jobtracker/file_store.py`
- `job-tracker-mvp/jobtracker/sqlite_repository.py`
- `job-tracker-mvp/jobtracker/migration.py`
- `job-tracker-mvp/tests/test_file_store.py`
- `job-tracker-mvp/tests/test_sqlite_migration.py`

## Acceptance criteria

- Binary files are stored outside SQLite below the configured managed
  `files/` root using collision-resistant UUID names and two-character shards.
- Every document row records original filename, managed relative path, SHA-256,
  MIME type, byte size, application ID, category, and creation timestamp.
- User-controlled filenames and stored-path inputs cannot escape the managed
  directory.
- Duplicate original filenames never overwrite or collide.
- A failed file write creates no committed document metadata.
- A later database failure removes files created by the failed transaction and
  leaves no committed document reference.
- Migration copies compare equal to their source SHA-256 values, repeat safely,
  detect destination corruption, and never alter legacy source data.
- All targeted and broader relevant tests pass in the project environment.

## Non-goals

- No content deduplication, garbage collector, attachment deletion workflow, or
  existing Milestone 3 runtime-data in-place conversion command.
- No database schema expansion, desktop packaging, frontend rewrite, Node.js,
  RAG, vector database, profile redesign, or LLM provider change.
- No deletion, renaming, or untracking of existing legacy or personal files.
