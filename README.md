# Job Tracker

Local-first job application tracker for organizing applications, CVs, motivation letters, employer responses, interviews, reports, and supporting evidence.

The project stores data locally in SQLite and managed files. It includes a CLI, authenticated loopback web UI, optional desktop wrapper, read-only legacy Markdown import, reports, and optional local-LLM assistance.

## Features

- Stable UUID-based job and workspace records
- Local SQLite database with managed, atomic file storage
- Multiple applicant workspaces
- CV, motivation letter, job description, screenshot, and employer-response tracking
- Read-only import and diagnostics for legacy Markdown job folders
- CLI, FastAPI web app, and optional pywebview desktop app
- Markdown reports with optional DOCX/PDF export
- Optional OpenAI-compatible local LLM integration
- Recovery exports, migration backups, privacy checks, and release smoke tests

## Project layout

```text
.
├── job-tracker-mvp/   Application source, tests, packaging, and detailed usage guide
├── docs/              Architecture, guides, references, and historical plans
├── PLAN.md            Active development plan
└── AGENTS.md          Repository instructions for coding agents
```

## Quick start

Requirements: Python 3.10 or newer.

```powershell
cd job-tracker-mvp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
job init
```

Run the web application:

```powershell
job web
```

Run the desktop application and build dependencies:

```powershell
pip install -e ".[desktop,build]"
jobtracker-desktop
```

## Common commands

```text
job init       Initialize local storage
job add        Create a job application
job list       List applications
job attach     Attach later employer evidence
job report     Generate Markdown report
job export     Export report to DOCX/PDF when tools are available
job doctor     Inspect legacy data without changing it
job migrate    Preview, execute, or verify legacy imports
job ai-test    Test configured LLM connectivity
```

See [`job-tracker-mvp/README.md`](job-tracker-mvp/README.md) for complete setup, configuration, migration, web, LLM, recovery, and release instructions.

## Data and privacy

Runtime data is kept outside the source checkout by default, under the operating system's per-user application-data directory. The application binds its web server to loopback and keeps its session token in memory.

Do not commit personal job data, CVs, secrets, live databases, generated build output, or virtual environments. Remote LLM endpoints require HTTPS, explicit disclosure consent, and an environment-provided API key.

Legacy imports are read-only: source folders are never modified or deleted automatically. Use an isolated test data directory for migration and recovery checks.

## Tests and checks

From `job-tracker-mvp`:

```powershell
pip install -e ".[dev,desktop]"
python -m pytest
python scripts/check_repo_privacy.py
```

The test suite redirects application-data access to pytest-owned temporary storage and must not use a live Job Tracker workspace.

## Documentation

- [Detailed application README](job-tracker-mvp/README.md)
- [Documentation index](docs/README.md)
- [Recovery guide](docs/guides/recovery.md)
- [Release guide](docs/guides/release.md)
- [Database schema reference](docs/reference/database-schema.md)
- [Performance baseline](docs/reference/performance-baseline.md)

## Development principles

- Local-first storage and explicit recovery
- Stable IDs instead of filesystem identity
- Domain logic separated from CLI, web, desktop, and persistence adapters
- No speculative cloud, sync, telemetry, or framework migration
- Small changes backed by focused and isolated tests

## License

No public license has been declared yet. Treat this repository as all-rights-reserved unless a license is added.
