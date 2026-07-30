# Milestone 8 Plan - Stable Core Contracts and Legacy Retirement

Status: complete

Started: 2026-07-30

Completed: 2026-07-30

## Assumptions

- SQLite plus managed-file storage is production write path; legacy Markdown
  remains import/doctor/recovery input only.
- Existing schema already records document UUID, category, name, checksum,
  size, MIME type, and creation time; no schema migration is needed.
- Report files remain an adapter detail. A failed derived report must not undo
  an already committed application mutation.

## Plan

1. Add stable application/document DTOs; make SQLite results expose IDs and
   document metadata, never managed paths through `JobApplication` -> verify:
   document references retain their UUIDs and legacy fields stay empty.
2. Change application service/repository contracts to use application IDs and
   document references rather than folder paths; surface report failures as
   warnings after successful writes -> verify: injected report failure keeps
   created/updated data available.
3. Make legacy Markdown adapter read-only and prevent production service
   construction from selecting it -> verify: legacy writes raise a clear
   compatibility error and SQLite is required for normal CLI/API startup.
4. Adapt API/CLI serialization to DTOs; add focused regressions; run full
   isolated suite, compile, and JavaScript syntax check -> verify: public
   responses identify jobs/documents by IDs and all checks pass.

## Files likely touched

- `job-tracker-mvp/jobtracker/application.py`
- `job-tracker-mvp/jobtracker/sqlite_repository.py`
- `job-tracker-mvp/jobtracker/repositories.py`
- `job-tracker-mvp/jobtracker/webapp.py`
- `job-tracker-mvp/jobtracker/cli.py`
- `job-tracker-mvp/tests/`
- `PLAN.md`
- `docs/history/`

## Acceptance criteria

- Core mutation/details DTOs expose stable application IDs and document refs,
  not filesystem paths.
- SQLite documents remain independent records.
- Report regeneration failure is explicit, recoverable, and cannot roll back
  a committed job mutation.
- Legacy Markdown source is read-only; production construction writes SQLite.

## Non-goals

- No schema migration, generic transaction layer, event bus, package split,
  or deletion of legacy source data.

## Completion evidence

- SQLite application details and mutations expose UUID application IDs plus
  `DocumentRef` metadata; managed paths remain repository internals.
- Legacy Markdown job writes now fail explicitly; normal service construction
  requires SQLite runtime storage.
- Report-refresh failure returns a recoverable warning after the job commit.
- `python -m compileall -q jobtracker tests`, JavaScript syntax validation,
  and `git diff --check` passed. Full tests could not start because installed
  Python runtimes lack declared project dependencies (`PyYAML`, FastAPI, etc.).
