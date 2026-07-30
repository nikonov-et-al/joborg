# Milestone 10 Plan - Hermetic Test Runtime

Status: active

## Assumptions

- Existing uncommitted Milestone 9 changes are the working baseline and must be
  verified and committed separately before this milestone.
- Tests must never use the live default application-data directory, even when
  `JOBTRACKER_DATA_DIR` or `LOCALAPPDATA` is set outside the test process.
- No runtime behavior or persisted data format changes are required.

## Plan

1. Map runtime-path resolution and test fixtures -> verify: every test service
   receives an explicit temporary root or is protected by a suite-level guard.
2. Add a minimal test-only isolation guard and document one clean full-suite
   command -> verify: tests fail on a live default path and pass with a blank
   temporary `LOCALAPPDATA`.
3. Run focused path tests and the full suite under an isolated environment ->
   verify: no test creates or reads the developer's real application data.

## Files likely touched

- `job-tracker-mvp/tests/`
- `job-tracker-mvp/pyproject.toml`
- `job-tracker-mvp/README.md`
- `docs/MILESTONE_10_PLAN.md`
- `PLAN.md`
- `docs/history/README.md`

## Acceptance criteria

- Normal full-suite command is documented and runs in a clean environment.
- Test runtime paths are explicit or fail before resolving the live default.
- Tests do not depend on real `%LOCALAPPDATA%`, user settings, or workspaces.
- No source runtime behavior changes solely for test isolation.

## Non-goals

- No dependency lockfile, packaged smoke test, diagnostics UI, or performance
  optimization in this first Milestone 10 slice.
- No changes to user data, workspace catalogs, or production default paths.
