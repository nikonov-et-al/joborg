# Job Tracker MVP

Local job application tracker for keeping applications, CVs, motivation letters, screenshots, and evidence organized.

## What it does

- Stores job metadata in a local SQLite database with stable UUID identifiers
- Stores uploaded files under the application data directory
- Imports the original Markdown job folders through a read-only migration
- Creates job records first
- Moves CV / motivation letter / job description files from `input/` during job creation
- Adds employer responses, screenshots, confirmations, rejections, and interview invites later with `job attach`
- Supports tab completion for selecting files from `input/`
- Optionally uses a local LLM through an OpenAI-compatible endpoint
- Generates `report.md`
- Exports report to DOCX/PDF through Pandoc if installed

## Install

From inside this folder:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

For desktop launch and executable builds:

```powershell
pip install -e ".[desktop,build]"
jobtracker-desktop
```

`jobtracker-desktop` opens the existing FastAPI UI in a native pywebview
window. It reserves an OS-assigned port on `127.0.0.1`, starts Uvicorn in the
same process, and stops the server when the window closes. Startup and database
schema failures are shown in a dialog.

Build the Windows one-file executable from this directory:

```powershell
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean jobtracker.spec
dist\JobTracker.exe
```

The built executable needs neither system Python nor the project virtual
environment. `dist\JobTracker.exe --smoke-test` initializes storage and exits
without opening a window, which is useful in release checks. Builds are
platform-specific; build separately on each target OS.

On Windows, user data defaults to `%LOCALAPPDATA%\jobtracker`, outside the
executable and its temporary extraction directory. Replacing or deleting the
executable therefore preserves the database and managed files. Uninstalling
user data is intentionally a separate explicit action.

## Initialize folders

```bash
job init
```

Runtime data is independent of the launch directory. By default it is stored
under the operating system's per-user application-data location. Override it
for testing or portable recovery with `JOBTRACKER_DATA_DIR`.

The desktop app now treats each data directory as one applicant workspace. On
first start, choose an empty local folder and enter the required given name,
family name, and email. Later starts reopen the last-used workspace
automatically. Use **Settings → Workspaces** to create, open, switch, or forget
workspaces; forgetting removes only the remembered path and never deletes data.

Workspace pointers are stored in `%LOCALAPPDATA%\jobtracker\workspaces.yml`.
Existing data already stored at `%LOCALAPPDATA%\jobtracker` is adopted without
moving it. Network drives, UNC paths, and known OneDrive folders are rejected
because a live SQLite database is unsafe in concurrently synchronized storage.
`JOBTRACKER_DATA_DIR` bypasses workspace selection for testing and recovery.

Uploaded and migrated binaries use managed storage below `files/`. Physical
names are generated UUIDs split into two-character shard directories, for
example `files/ab/ab12…pdf`; user filenames are retained only as document
metadata. SQLite records the original filename, managed relative path,
SHA-256, MIME type, byte size, application, category, and creation time. File
writes are atomic and hash-verified before their metadata transaction commits.

## Import legacy Markdown data

Set `JOBTRACKER_LEGACY_JOBS_DIR` or pass `--legacy-path`, then run the three
read-only/source-preserving phases:

```bash
job migrate preview --legacy-path /path/to/legacy/jobs --json
job migrate execute --legacy-path /path/to/legacy/jobs --json
job migrate verify --legacy-path /path/to/legacy/jobs --json
```

Use `--data-dir` to select an isolated destination. Preview performs no writes.
Execute assigns stable application UUIDs, copies attachments into managed
UUID-sharded storage, verifies SHA-256, and records source manifests
transactionally per record. Repeating execute is safe and does not create
duplicates. Verify compares source/imported counts and hashes; the legacy
directory is never deleted or modified.

## Daily workflow

1. Put files into `input/`:
   - CV PDF/DOCX
   - motivation letter
   - screenshots
   - confirmation emails
   - job description PDF if available

2. Add a job application record:

```bash
job add
```

This creates the job folder and stores the CV / motivation letter if you already have them.

3. Later, when the employer replies, put the response files into `input/` and attach them:

```bash
job attach
```

Examples of later evidence:

- confirmation email PDF
- rejection email PDF
- interview invitation
- employer task
- portal screenshots
- email screenshots

4. Generate report:

```bash
job report
```

5. Export report:

```bash
job export
```

## Web app

```bash
job web
```

Open `http://127.0.0.1:8000`. The web app includes:

- dashboard statistics and pending interviews
- separate add-job, all-jobs, report, and profile views
- right-side job details with status and file updates
- profile text and multiple reference CV uploads
- switchable local applicant workspaces in the desktop app
- local-LLM CV drafts saved inside each job folder
- browser Print / Save as PDF for reports and generated CVs

## Local LLM

Default config uses Ollama:

```yaml
llm:
  base_url: "http://localhost:11434/v1"
  model: "gemma4:12b-it-q4_K_M"
```

Test it:

```bash
job ai-test
```

If you use LM Studio, change `config.yml`:

```yaml
llm:
  base_url: "http://localhost:1234/v1"
  model: "your-loaded-model"
```

## Commands

```bash
job init
job add
job add --no-ai
job list
job report
job export
job attach
job doctor
job doctor --json
job migrate preview --legacy-path PATH
job migrate execute --legacy-path PATH
job migrate verify --legacy-path PATH
job ai-test
```

## Read-only legacy diagnostics

`job doctor` scans `--legacy-path`, `JOBTRACKER_LEGACY_JOBS_DIR`, or the
configured legacy jobs directory without changing any record or attachment.
Data problems are findings, so the command exits with
status 0 even when it reports errors or warnings. A missing, invalid, or
unreadable configured jobs path is fatal and exits with status 2.

`job doctor --json` emits the stable version-1 structure below. Paths are
relative POSIX-style paths and all arrays are deterministically sorted.

```json
{
  "schema_version": 1,
  "summary": {
    "ok": true,
    "job_directories": 0,
    "records_parsed": 0,
    "records_failed": 0,
    "physical_attachments": 0,
    "referenced_attachments": 0,
    "issues": 0,
    "errors": 0,
    "warnings": 0,
    "issues_by_code": {}
  },
  "records": [],
  "attachments": [],
  "issues": []
}
```

Each issue contains `code`, `severity`, `record_path`, `field`, and `message`.
Fatal JSON keeps the four collection keys, sets `summary` to `null`, and adds
`fatal` with `code`, `path`, and `message`. Physical attachments are files in
the legacy attachment, CV, evidence, motivation-letter, and screenshot
directories; `.gitkeep` and generated CV Markdown are excluded.

Version-1 issue codes are:

```text
missing_record
malformed_frontmatter
unclosed_frontmatter
invalid_frontmatter_type
record_encoding_error
record_read_error
unexpected_load_error
missing_required_field
invalid_metadata_type
unknown_status
invalid_date
directory_date_mismatch
invalid_attachment_reference
missing_attachment
missing_description
empty_description
description_read_error
attachment_scan_error
attachment_read_error
duplicate_attachment_sha256
```

Batch record loads keep valid jobs and return one structured diagnostic per
failed record. Diagnostics contain a data-directory-relative `path`, stable
`code`, safe `message`, and `exception_type`. Current load codes are
`frontmatter_error`, `invalid_metadata`, `invalid_status`, `invalid_date`,
`missing_file`, `permission_error`, `decode_error`, and
`unexpected_load_error`.

`GET /api/jobs` keeps the legacy list response and adds
`X-Jobtracker-Loaded-Count` and `X-Jobtracker-Failed-Count` headers. Use
`GET /api/jobs?include_errors=true` for the object response containing `jobs`,
`loaded_count`, `failed_count`, and `errors`. The dashboard uses this diagnostic
response. CLI list, report, and export workflows print loaded and failed counts
and direct users to `job doctor` when failures occur.

## Notes

The local LLM is only used to suggest fields from the job description. You confirm/edit everything before it is saved.

## Application architecture

CLI commands and FastAPI routes are adapters over the same `JobTrackerService`.
The application layer defines repository and report/document gateway contracts;
`SQLiteJobRepository` is the primary runtime adapter. It uses the standard
library's `sqlite3` transactions and a versioned schema initializer. The
`LegacyMarkdownRepository` remains available for explicit read-only import and
recovery. `create_app(service)` supports explicit dependency injection in
tests.

Pydantic schemas validate HTTP job, profile, and generated-document inputs at
the transport boundary. The application core does not import FastAPI, Typer,
ReportLab, or the concrete Markdown repository.

Profiles and generated reports remain file-backed for now. Legacy job folder
names and stored files are unchanged by migration.

## Correct evidence workflow

`job add` is for creating the application record and storing files you already have at the time of applying:

- CV sent
- motivation letter sent
- pasted job description
- optional job description PDF
- optional portfolio/application attachment

`job attach` is for later employer responses:

- confirmation email
- rejection email
- interview invitation
- task assignment
- portal status screenshot
- email screenshots

This is intentional because evidence usually arrives after the application is created.
