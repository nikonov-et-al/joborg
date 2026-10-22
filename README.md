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
- Windows desktop application with a packaged `JobTracker.exe` executable

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

For Windows users, the packaged desktop application is available as
`job-tracker-mvp/dist/JobTracker.exe` when a Windows build has been produced.
The executable runs without a separate Python installation or project virtual
environment. Builds are platform-specific and should be created separately
for each target operating system.

## Technology stack

- Python 3.10+
- SQLite with transactional, versioned schema management
- FastAPI and Uvicorn for the authenticated loopback web API
- Pydantic for transport-boundary validation
- Typer, Prompt Toolkit, and Rich for the CLI
- Plain HTML, CSS, and JavaScript for the web UI
- pywebview for the optional Windows desktop wrapper
- ReportLab and Pandoc integration for document/PDF reports
- PyInstaller for Windows executable packaging
- PyYAML for configuration
- pytest and Node.js checks for testing
- Optional OpenAI-compatible local LLM providers, including Ollama and LM Studio

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

## License

This project is licensed under the [MIT License](LICENSE). You may use,
modify, distribute, and sell the software, including in commercial products,
provided that the original copyright and license notice remain included.
