# Milestone 9 Plan - Local API, Input, and Secret Security

Status: complete

Started: 2026-07-30

Completed: 2026-07-30

## Assumptions

- Desktop owns loopback server and can deliver a memory-only token in initial UI URL.
- CLI remains direct service access; HTTP token applies only to web API.
- Existing managed storage is atomic after service receives assets; this milestone
  bounds and validates ingress before assets reach it.
- PDF and DOCX are only accepted profile-CV content types; general job attachments
  retain existing type-neutral behavior pending a product policy.

## Plan

1. Add per-process API token, loopback Host/Origin checks, production docs
   disablement, and UI request-token propagation -> verify: missing/wrong token
   and foreign Origin fail while owned UI succeeds.
2. Replace raw config endpoint with public settings DTO and safe API errors ->
   verify: no secret/path fields are serialized and generation errors are stable.
3. Tighten domain-boundary schemas for strings, URLs, emails, dates, booleans,
   and list items -> verify: create/update reject invalid and oversized values.
4. Stream uploads through bounded temporary files with filename/type checks;
   clean failure paths -> verify: limits and spoofed profile CVs fail without
   committed assets.
5. Add security headers and concise local-data threat model -> verify: headers
   exist on normal responses and documentation covers local/remote boundaries.

## Files likely touched

- `job-tracker-mvp/jobtracker/webapp.py`
- `job-tracker-mvp/jobtracker/desktop.py`
- `job-tracker-mvp/jobtracker/schemas.py`
- `job-tracker-mvp/jobtracker/web/static/app.js`
- `job-tracker-mvp/tests/`
- `docs/privacy.md`
- `PLAN.md`

## Acceptance criteria

- Loopback API trusts current desktop process only.
- Public settings/responses omit secrets and managed paths.
- Invalid boundary data and unsafe uploads are rejected.
- Upload handling is bounded, streamed, and cleans temporary state.
- Local data threat model and browser security headers are documented.

## Non-goals

- No account/login system, custom encryption, cloud service, schema migration,
  or generic validation framework.

## Completion evidence

- Desktop creates a per-process token; API middleware verifies it plus loopback
  Host/Origin before allowing API access.
- Settings response omits secrets and paths; boundary schemas reject unsafe data.
- Uploads stream through bounded temporary files and reject unsafe/suspicious CVs.
- `python -m compileall -q jobtracker tests`, `node --check jobtracker/web/static/app.js`,
  and `git diff --check` pass. Runtime suite blocked: Python lacks project deps.
