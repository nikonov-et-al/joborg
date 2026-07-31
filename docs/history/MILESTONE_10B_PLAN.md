# Milestone 10B Plan - Local API and AI Disclosure Security Closure

Status: complete

Started: 2026-07-31

Completed: 2026-07-31

## Assumptions

- Live workspaces and runtime configuration are user data and remain untouched.
- The existing FastAPI, pywebview, plain-JavaScript, and OpenAI-compatible LLM
  stack remains.
- Shared-history remediation is a separate approval gate.

## Plan

1. Route JSON, multipart, and binary frontend requests through one
   authenticated client -> verify: token/header and download behavior tests.
2. Make every supported web launch tokenized and loopback-only -> verify: CLI,
   desktop, host/origin, docs, and rejected-response tests.
3. Classify LLM endpoints and require remote consent, confirmation, HTTPS, and
   environment credentials -> verify: fake-provider policy and safe-error tests.
4. Run focused/full/package/privacy gates and archive accepted evidence ->
   verify: exact commands and results below.

## Acceptance criteria

- Every supported web runtime is loopback-only and authenticated per process.
- All frontend API workflows use the shared authenticated client.
- OpenAPI/docs are disabled outside disposable diagnostic mode.
- Remote personal-content transmission requires persisted consent and visible
  per-operation confirmation.
- Public settings, responses, and logs expose no credential, path, submitted
  content, or raw provider failure.

## Non-goals

- No user login, remotely reachable server mode, new provider abstraction,
  frontend redesign, data migration, or shared-history rewrite.

## Completion evidence

- Full isolated suite: `.venv\Scripts\python.exe -m pytest -q` -> 190 passed,
  39 subtests passed.
- Frontend behavior: `node --test tests/api_client_test.js` -> 3 passed.
- JavaScript syntax: `node --check jobtracker/web/static/app.js` and
  `node --check jobtracker/web/static/api-client.js` -> passed.
- Bytecode: `.venv\Scripts\python.exe -m compileall -q jobtracker scripts tests`
  -> passed.
- Repository privacy: `.venv\Scripts\python.exe scripts\check_repo_privacy.py`
  -> passed for HEAD, index, and staged state.
- Package: `.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean
  jobtracker.spec` -> passed.
- Disposable packaged smoke: `scripts\smoke_package.ps1 -Artifact
  dist\JobTracker.exe` -> passed without resolving live application data.
- The shared-history remediation proposal remains unresolved and unchanged.
