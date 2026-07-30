# Milestone 10 Plan - Reliability, Testing, and Release Discipline

Status: complete

Completed: 2026-07-30

## Assumptions

- Hermetic test runtime work is complete in commit `88ce399`.
- AI and desktop support are optional; the base install must not require them.
- Current packaged executables are unsigned development artifacts.
- No measured performance problem currently justifies query, cache, pagination,
  or FTS changes.
- No runtime behavior or persisted data format change is required.

## Plan

1. Preserve hermetic verification -> verify: the documented full-suite command
   passes with pytest-owned application data and the packaged smoke command
   targets an explicit temporary workspace.
2. Lock release inputs -> verify: optional dependency groups keep AI, desktop,
   and build tooling separate; reviewed constraints pin the release graph.
3. Make release artifacts reviewable -> verify: one standard-library command
   emits an artifact manifest with app/tool versions, size, and SHA-256 without
   local paths or user data.
4. Complete read-only storage health -> verify: settings show SQLite integrity,
   foreign-key state, managed-file counts, schema, backup age, workspace size,
   and free space without returning document names or content.
5. Record a performance baseline -> verify: a temporary synthetic workspace
   measures list/detail query counts and timings without touching live data;
   no optimization is made without evidence.
6. Document distribution operations -> verify: release, isolated smoke test,
   manual upgrade/rollback/uninstall, data retention, and unsigned-artifact
   status have one current guide.
7. Run focused checks, full isolated tests, JavaScript syntax check, release
   manifest check, and packaged smoke test -> verify: all accepted commands
   pass, or any unavailable external signing step is documented.

## Files likely touched

- `job-tracker-mvp/jobtracker/desktop.py`
- `job-tracker-mvp/jobtracker/web/static/app.js`
- `job-tracker-mvp/scripts/`
- `job-tracker-mvp/tests/`
- `job-tracker-mvp/pyproject.toml`
- `job-tracker-mvp/README.md`
- `job-tracker-mvp/constraints-release.txt`
- `docs/guides/release.md`
- `docs/MILESTONE_10_PLAN.md`
- `PLAN.md`
- `docs/history/README.md`

## Acceptance criteria

- Full suite passes with isolated runtime paths.
- Base, AI, desktop, build, and development dependency purposes are explicit;
  release constraints are exact.
- Artifact manifest contains no absolute path, environment value, or user data.
- Storage diagnostics are read-only and expose only counts/status metadata.
- Synthetic measurements use temporary data and justify no speculative
  performance layer.
- Packaged smoke test invokes the executable directly with isolated data.
- Shared releases are signed; local output is labeled unsigned development
  material until signing is explicitly configured.

## Non-goals

- No telemetry, crash upload, automatic updater, cache, background worker,
  pagination framework, FTS, or database index without measured need.
- No code-signing credential or publisher identity committed to Git.
- No changes to user data, persisted schemas, workspace catalogs, or production
  default paths.

## Completion evidence

- Hermetic full suite: `python -m pytest -q` -> 176 passed and 31 subtests
  passed in 38.73 seconds.
- Syntax/dependency checks: JavaScript syntax, Python bytecode compilation,
  `pip check`, and `git diff --check` passed.
- Release build: PyInstaller 6.21.0 on CPython 3.14.4 produced the current
  unsigned Windows development executable.
- Packaged smoke: executable ran twice against a disposable
  `JOBTRACKER_DATA_DIR`, created SQLite storage, and preserved an existing
  sentinel without invoking system Python.
- Artifact manifest: 38,775,362 bytes; SHA-256
  `53ad8846dd1ff6dff161d48f69bb32704404dce1e49177e2e2db5c0debbb4791`;
  no absolute path or user data.
- Synthetic baseline: 50-record list median 2.419 ms/51 SELECTs; 1,000-record
  list median 70.612 ms/1,001 SELECTs. No optimization justified.
- Shared production distribution remains gated on an external code-signing
  identity; current output is documented and manifested as
  `unsigned-development`.
