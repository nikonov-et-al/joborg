# Milestone 5 Implementation Plan

## Assumptions

- The uncommitted Milestones 0 through 4 working tree is the accepted baseline
  and must be preserved.
- Milestone 5 targets Windows first because this repository is currently
  developed and verified on Windows. The desktop launcher remains portable,
  but executable builds are platform-specific.
- A portable one-file executable satisfies the first packaging milestone. A
  signed installer, automatic updater, and OS-specific uninstall UI are later
  distribution work.
- User data remains under `AppPaths.data_dir`; the executable and its temporary
  extraction directory contain application code and bundled read-only assets
  only.
- The existing FastAPI UI remains unchanged. pywebview is only its desktop
  shell, and Uvicorn remains the local ASGI server.

## Plan

1. Add one desktop entry point that builds the existing application service,
   initializes runtime storage, reserves a random loopback port, starts Uvicorn
   on that pre-bound socket, opens pywebview on the local URL, and stops the
   server when the window closes. -> verify: tests assert loopback-only binding,
   non-fixed ports, startup readiness, shutdown, and server-failure propagation.
2. Add clear startup-failure handling plus a packaged `--smoke-test` mode that
   initializes the same runtime paths, SQLite schema, service, and bundled UI
   assets without opening a window. -> verify: failures return nonzero and are
   surfaced; repeated smoke runs against one isolated data directory preserve
   an existing sentinel and database.
3. Add minimal desktop/build dependencies, a GUI script entry point, and one
   PyInstaller spec that bundles existing package data into a one-file windowed
   executable. -> verify: PyInstaller completes and the executable passes the
   isolated smoke test without system Python or a project virtual environment.
4. Add focused desktop tests and documentation for development launch, build,
   data location, upgrade, and uninstall behavior. -> verify: targeted tests,
   full suite, bytecode compilation, and `git diff --check` pass.
5. Re-run legacy doctor and manifest checks, then record accepted Milestone 5
   evidence in `PLAN.md`. -> verify: legacy record/file counts and aggregate
   hash remain unchanged.

## Files likely touched

- `MILESTONE_5_PLAN.md`
- `PLAN.md`
- `job-tracker-mvp/README.md`
- `job-tracker-mvp/pyproject.toml`
- `job-tracker-mvp/jobtracker/desktop.py`
- `job-tracker-mvp/jobtracker.spec`
- `job-tracker-mvp/tests/test_desktop.py`

## Acceptance criteria

- Desktop launch needs no manual virtual-environment, server, or browser step.
- Local HTTP traffic binds only to `127.0.0.1` on an OS-assigned available port.
- Closing the desktop window stops Uvicorn; failed startup does not leave a
  server thread running.
- Startup/storage/schema failures are shown clearly and return a nonzero exit.
- A one-file windowed executable builds with the existing static assets/fonts
  and passes a clean-data smoke test without invoking system Python.
- Repeated executable runs preserve runtime data outside the executable and
  installed-code directories.
- Legacy Markdown data remains unchanged and all relevant tests pass.

## Non-goals

- No Tauri, Node.js, frontend rewrite, installer, code signing, auto-update,
  telemetry, remote binding, TLS, custom IPC, RAG, or LLM-provider change.
- No migration of runtime data into the install directory and no uninstall-time
  deletion of user data.
- No claim that one platform's built artifact runs on another platform; each
  target OS must build its own executable.
