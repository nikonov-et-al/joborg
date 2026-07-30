# Milestone 1 Implementation Plan

Status: completed and accepted on 2026-07-18. Verification is recorded in
`PLAN.md`.

## Assumptions

- The accepted Milestone 0 working tree is the implementation baseline even
  though it has not been committed.
- Existing Markdown records, folder identifiers, profile files, generated
  documents, CLI commands, and HTTP routes remain compatible.
- `LegacyMarkdownRepository` may reuse the verified Milestone 0 filesystem
  primitives; this milestone changes dependency direction, not persistence.
- Interactive prompting, HTTP multipart parsing, console rendering, PDF
  rendering, and LLM calls remain boundary concerns.

## Plan

1. Define repository and document/report gateway interfaces, then implement the
   current filesystem behavior in `LegacyMarkdownRepository`. -> verify: an
   adapter contract test creates, reads, updates, attaches, and preserves a
   Markdown record.
2. Add a transport-independent `JobTrackerService` that owns job, timeline,
   attachment, profile, report, and generated-document workflows. -> verify:
   service tests cover create/update/list/profile/report operations without
   importing FastAPI or Typer.
3. Add Pydantic request schemas for job creation, job updates, profile updates,
   and CV draft content. -> verify: schema tests reject unknown fields and
   invalid boundary types before storage is called.
4. Construct FastAPI through `create_app(service)` and make route handlers thin
   adapters over `JobTrackerService`. -> verify: an injected fake service is
   exercised by API tests and existing endpoint behavior remains compatible.
5. Refactor CLI add, attach, list, report, and export workflows to call the same
   application service and remove the duplicated job-body/update workflow. ->
   verify: CLI and API integration tests observe calls through the same service
   type.
6. Run targeted tests, the full relevant suite, `job doctor --json`, and a
   before/after legacy manifest comparison; then record Milestone 1 acceptance
   in `PLAN.md`. -> verify: all tests pass, 27 records and 7 referenced
   attachments remain readable, and the 63-file legacy manifest digest is
   unchanged.

## Files likely touched

- `MILESTONE_1_PLAN.md`
- `PLAN.md`
- `job-tracker-mvp/jobtracker/application.py`
- `job-tracker-mvp/jobtracker/repositories.py`
- `job-tracker-mvp/jobtracker/schemas.py`
- `job-tracker-mvp/jobtracker/services.py`
- `job-tracker-mvp/jobtracker/cli.py`
- `job-tracker-mvp/jobtracker/webapp.py`
- `job-tracker-mvp/tests/test_application_service.py`
- `job-tracker-mvp/tests/test_repository_contract.py`
- `job-tracker-mvp/tests/test_boundary_schemas.py`

## Acceptance criteria

- CLI and FastAPI job mutations execute through `JobTrackerService`.
- Application workflows depend on repository/gateway interfaces rather than
  FastAPI, Typer, ReportLab, or concrete Markdown functions.
- `LegacyMarkdownRepository` preserves the current filesystem format and
  stable legacy folder identifiers.
- FastAPI supports explicit service injection through `create_app(service)`.
- Pydantic schemas validate API job/profile/document inputs and forbid unknown
  business fields.
- The CLI no longer defines a duplicate `build_job_body` or performs its own
  Markdown metadata/timeline update workflow.
- Existing observable CLI/API behavior remains compatible and all relevant
  tests pass.
- No legacy job or attachment content changes.

## Non-goals

- SQLite, UUID migration, managed content-addressed files, or data relocation.
- Desktop packaging, frontend redesign, upload streaming, or secret handling.
- LLM provider changes, profile ingestion, RAG, or generated-document
  versioning.
- Renaming existing job directories or changing stored Markdown schemas.
