# Historical Snapshot: Job Tracker Refactoring and Migration Plan

Status: superseded on 2026-07-30

Current planning is maintained locally and is not part of repository history.

This file preserves the original master plan and completion evidence through
Milestone 5. It may contain superseded next steps and is not an active work
queue.

## Purpose

Refactor the current Python job-tracker incrementally while preserving all existing job records, attachments, profile data, and generated documents.

This plan is intentionally ordered by risk. Complete one step at a time. Do not combine storage migration, desktop packaging, UI redesign, and AI/RAG changes in a single milestone.

## Execution Rules

- Existing user data is the source of truth until a migration is verified.
- Never modify legacy records in place during migration development.
- Keep the original `jobs/` directory available and read-only after migration.
- Add a regression test for every confirmed data-loss bug.
- Run targeted tests before the broader relevant test suite.
- GPT-5.6 Sol selects and reviews each step.
- Delegate only small, independently verifiable implementation tasks to `mini_worker`.
- Do not mark a step complete while tests fail or data-safety checks remain unverified.

---

## Immediate Critical Issue

### Frontmatter parsing can corrupt metadata

Current code in `jobtracker/markdown.py` parses frontmatter with:

```python
parts = text.split("---", 2)
```

This treats any `---` sequence inside a YAML value as the closing delimiter.

One existing URL contains:

```text
Ground-Segment---AI-Software-Engineer
```

The parser truncates the URL and loses all metadata fields that follow it, including:

```text
source
application_method
date_applied
status
cv_file
attachments
```

`JobApplication.from_metadata()` then silently supplies defaults. A later edit can rewrite `job.md` with incorrect values and permanently destroy valid metadata.

Do not edit existing records through the application until this parser is fixed and verified.

### Required parser behavior

The parser must recognize frontmatter delimiters only when a complete line, after trimming whitespace, equals `---`.

Reference implementation:

```python
def read_markdown_with_frontmatter(path: Path) -> tuple[dict, str]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    if not lines or lines[0].strip() != "---":
        return {}, "".join(lines)

    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )

    if closing_index is None:
        raise ValueError(f"Unclosed frontmatter: {path}")

    metadata_text = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1:]).lstrip("\r\n")
    metadata = yaml.safe_load(metadata_text) or {}

    if not isinstance(metadata, dict):
        raise ValueError(f"Invalid frontmatter object: {path}")

    return metadata, body
```

Required regression coverage:

- a URL containing `---`;
- an unclosed frontmatter block;
- frontmatter whose YAML root is not a mapping;
- a Markdown file without frontmatter;
- preservation of body content and line endings where relevant.

---

## Verified Legacy Data Baseline

A line-based frontmatter read produced the following baseline:

| Check | Result |
|---|---:|
| Job records found | 27 |
| Records parsed successfully | 27 |
| Status `rejected` | 17 |
| Status `applied` | 9 |
| Status `interview_done` | 1 |
| Physical attachment files | 7 |
| Missing referenced attachments | 0 |

Additional observations:

- 19 of 27 `job_description.md` files contain only a heading and no meaningful description.
- Two groups of PDFs are byte-identical by SHA-256.
- One directory date does not match the record's `date_applied`.
- These observations are not immediate corruption, but migration logic must preserve and report them.

This baseline must be rechecked before and after every persistence migration.

---

## Key Findings to Address

### 1. Read failures are hidden

`jobtracker/reports.py` contains behavior equivalent to:

```python
try:
    ...
except Exception:
    continue
```

A malformed record can silently disappear from the dashboard and reports.

Replace silent skipping with an explicit result type, for example:

```python
@dataclass
class LoadJobsResult:
    jobs: list[tuple[Path, JobApplication]]
    errors: list[JobLoadError]
```

The UI and CLI must report loaded and failed record counts and provide actionable diagnostics.

### 2. CLI and web workflows duplicate business logic

`services.py` already contains operations such as:

```text
create_job_record
apply_job_update
attach_assets_to_job
save_profile
```

`cli.py` separately implements directory creation, Markdown writes, attachment movement, status updates, timeline updates, and report generation. `build_job_body()` is duplicated.

CLI and FastAPI should be thin interfaces over one application service. A fix must not need to be implemented twice.

### 3. File writes are not atomic

Current creation is a multi-step filesystem workflow. Failure in the middle can leave a partially created record.

Before SQLite:

- write new records into a temporary directory;
- validate all generated files;
- atomically rename the completed directory;
- write Markdown through a temporary file followed by `Path.replace()`;
- copy source attachments first;
- verify hashes before any optional source deletion.

### 4. Runtime data depends on the current working directory

`config.yml`, `jobs/`, `profile/`, and `reports/` are resolved relative to the launch directory.

Introduce a dedicated path object:

```python
@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    database: Path
    files_dir: Path
    backups_dir: Path
    logs_dir: Path
    legacy_jobs_dir: Path | None
```

Desktop runtime data must live in an application-specific user-data directory, not beside source code or relative to an arbitrary working directory.

### 5. Directory names are used as record identifiers

The current identifier is derived from date, company, and role. This can collide, loses some non-ASCII information, and becomes stale after edits.

Use a stable UUID for new records. Preserve the old directory name in `legacy_folder` during migration.

### 6. Validation is incomplete

`JobApplication.validate()` currently validates too little.

Add boundary validation for:

- required company and role;
- dates;
- email addresses;
- URL schemes;
- list and mapping types;
- text and upload size limits;
- allowed status values.

Use Pydantic request and response schemas for API and CLI boundaries without forcing domain models to become ORM models.

### 7. The API exposes internal LLM configuration

Do not return the complete LLM configuration, especially `api_key`, to the browser.

Expose only safe status fields such as:

```json
{
  "enabled": true,
  "provider": "ollama",
  "model": "..."
}
```

Keep secrets in environment variables or an appropriate secret store.

### 8. Uploads are read fully into memory

Replace `await upload.read()` for arbitrary files with:

- explicit upload size limits;
- streaming to a temporary file;
- extension and MIME validation;
- SHA-256 calculation while streaming;
- cleanup after failure.

### 9. Profile CV contents are not actually used by the LLM

The current profile loader does not ingest `profile/master_cv.md` or extract text from uploaded PDF/DOCX files. Passing filenames alone does not give the model their content.

Build a profile ingestion pipeline before adding RAG.

### 10. Generated CVs have no version history

`cv/generated_cv.md` is overwritten.

Add document versioning and AI-run metadata, including:

- provider and model;
- prompt version;
- profile version or input hash;
- generation timestamp;
- approval status;
- source identifiers used for generated claims.

---

# Milestone 0 - Protect Existing Data

## Goal

Remove immediate corruption risks and make legacy data health visible without changing the storage format.

## Steps

### 0.1 Preserve a baseline backup

- Keep the original project archive unchanged.
- Create a second working copy.
- Record counts and SHA-256 hashes for all legacy records and attachments.

Verify:

- 27 job directories are accounted for;
- 7 attachments exist;
- the backup is outside the working migration target;
- no source hash changes during milestone work.

### 0.2 Fix frontmatter parsing

- Replace substring splitting with complete delimiter-line detection.
- Reject unclosed or non-mapping frontmatter explicitly.
- Preserve the Markdown body.
- Add regression tests, especially for URLs containing `---`.

Verify:

- all parser tests pass;
- all 27 existing records parse;
- dates, statuses, attachment references, and URLs match the source files.

### 0.3 Add a `job doctor` command

The command should be read-only by default and report:

- readable and unreadable records;
- malformed frontmatter;
- invalid dates or statuses;
- missing referenced files;
- duplicate attachment hashes;
- directory-date and metadata-date mismatches;
- empty or heading-only descriptions;
- total record and attachment counts.

Support machine-readable output such as JSON for later migration verification.

Verify:

- the command does not modify files;
- repeated runs produce stable results;
- failures are reported rather than skipped.

### 0.4 Remove silent exception handling

- Return structured load errors.
- Show warnings in CLI and web responses.
- Preserve successfully loaded records while making failures visible.

Verify:

- a deliberately malformed fixture produces a visible diagnostic;
- no record disappears silently.

### 0.5 Make Markdown writes atomic

- write to a sibling temporary file;
- flush and close the temporary file;
- replace the destination atomically;
- clean up temporary files after failure.

For multi-file record creation, use a temporary directory and final rename.

Verify:

- failure injection does not leave a partially valid destination;
- existing destination content remains intact when replacement fails.

## Milestone 0 Acceptance Criteria

```text
27 of 27 legacy records are readable
7 of 7 referenced attachments exist
0 hidden read errors
source SHA-256 values are unchanged
frontmatter URL regression test passes
all relevant tests pass
```

---

# Milestone 1 - Unify Business Logic

## Goal

Make CLI and FastAPI depend on one application layer so storage can change without duplicating behavior.

## Steps

1. Consolidate job, document, profile, timeline, and report workflows behind application services.
2. Move duplicated `build_job_body`, create, update, and attachment logic out of CLI and transport code.
3. Define repository interfaces required by the application layer.
4. Implement the current filesystem behavior as `LegacyMarkdownRepository`.
5. Construct FastAPI through dependency injection, for example `create_app(service)`.
6. Add Pydantic boundary schemas.
7. Keep ReportLab, FastAPI, Typer, and concrete persistence imports outside the domain/application core where practical.

Verify:

- CLI and API tests exercise the same application service;
- existing observable behavior remains compatible;
- duplicated business workflows are removed;
- no storage migration occurs in this milestone.

---

# Milestone 2 - Separate Runtime Data from Source Code

## Goal

Make storage locations deterministic and suitable for a packaged desktop application.

## Steps

1. Introduce `AppPaths`.
2. Use `platformdirs` or equivalent application-specific user directories.
3. Support an explicit legacy-data path for migration and recovery.
4. Add appropriate ignore rules:

```gitignore
.venv/
__pycache__/
*.pyc
*.log
*.egg-info/
dist/
build/
data/
backups/
```

5. Remove generated caches, logs, reports, personal profile data, and personal CVs from version control where applicable.
6. Resolve the version mismatch between `pyproject.toml` and `jobtracker/__init__.py`.
7. Keep one authoritative version source.

Verify:

- launching from different working directories resolves the same user-data path;
- tests can override paths with temporary directories;
- legacy data remains untouched.

---

# Milestone 3 - Introduce SQLite

## Goal

Make SQLite the primary metadata store while retaining a read-only legacy importer.

## Recommended stack

```text
SQLite
SQLAlchemy 2
Alembic
Pydantic at API boundaries
```

## Initial schema

```text
applications
application_skills
status_events
documents
profiles
profile_items
document_versions
ai_runs
migration_sources
```

Company normalization is optional for the first migration.

## Migration commands

```bash
job migrate preview
job migrate execute
job migrate verify
```

### Preview

Must not write data. Report:

- records discovered;
- records importable;
- warnings;
- blocking errors;
- attachments;
- duplicates;
- unknown statuses;
- expected destination changes.

### Execute

- read legacy data only;
- assign stable UUIDs;
- copy attachments;
- verify SHA-256 after copying;
- record source path and source hash;
- write through transactions;
- make repeated execution safe;
- preserve `legacy_folder`;
- never delete the legacy directory.

### Verify

Compare:

- source and imported record counts;
- source and imported attachment counts;
- source and destination hashes;
- failed and skipped records;
- duplicate handling;
- repeat-run behavior.

## Milestone 3 Acceptance Criteria

- preview performs no writes;
- execute is transactional or safely resumable;
- verify detects intentional corruption;
- a second execute run creates no duplicate records;
- original legacy files remain unchanged;
- all migration and repository tests pass.

---

# Milestone 4 - Move Attachments to Managed File Storage

## Goal

Store binary files outside SQLite while keeping metadata and integrity information in the database.

## Target layout

```text
app-data/
├── jobtracker.sqlite3
├── files/
│   └── ab/
│       └── ab12...pdf
├── backups/
├── exports/
└── logs/
```

## Document metadata

```text
original_filename
stored_path
sha256
mime_type
size
application_id
category
created_at
```

Use UUID or content-addressed storage names. Prevent path traversal and filename collisions.

Deduplication by SHA-256 is optional during the first import; correctness is more important than optimization.

Verify:

- copied file hashes match their sources;
- duplicate original filenames do not collide;
- records cannot escape the managed file directory;
- failed copies leave no committed database reference.

---

# Milestone 5 - Package as a Desktop Application

## Goal

Launch the application from a normal executable without manually starting a virtual environment, server, or browser.

## Preferred first option

```text
Python application core
FastAPI
pywebview
PyInstaller or Nuitka
```

This preserves the existing frontend and avoids introducing Node.js.

## Alternative

```text
Tauri shell
Python sidecar
FastAPI bound to a random loopback port
```

Use the alternative only when pywebview cannot meet concrete product requirements.

## Requirements

- package only after deterministic user-data paths and SQLite are stable;
- bind any local server to loopback only;
- use a random available port or protected IPC;
- start and stop child services with the desktop lifecycle;
- display startup and migration failures clearly;
- never store user data inside the installed application directory.

Verify:

- application starts from a clean user account;
- no system Python or project virtual environment is required;
- restart preserves data;
- upgrade preserves data;
- uninstall behavior does not unexpectedly delete user data.

---

# Milestone 6 - Refactor AI and Add Retrieval

## Goal

Make AI generation traceable, grounded in profile data, and replaceable across providers.

## Steps

1. Define an `LlmProvider` interface.
2. Add timeouts and safe error handling.
3. Do not expose raw internal exceptions or secrets to the UI.
4. Extract text from Markdown, PDF, and DOCX profile sources.
5. Normalize profile information into structured items.
6. Add SQLite FTS5 lexical search.
7. Add a vector index only after ingestion and FTS are stable.
8. Treat the vector database as a rebuildable index, never the source of truth.
9. Generate validated structured JSON before rendering Markdown, DOCX, or PDF.
10. Store `source_ids` for every generated claim or bullet.
11. Version every generated CV and cover letter.
12. Record AI runs, inputs, model, prompt version, and approval status.

Verify:

- generated content can be traced to stored profile sources;
- changing the embedding model triggers a safe index rebuild;
- deleting the vector index does not lose source data;
- an ungrounded generated claim is rejected or clearly flagged.

---

# Recommended Package Structure

Do not create all directories preemptively. Move toward this structure only as milestones require it.

```text
jobtracker/
├── domain/
│   ├── models.py
│   ├── enums.py
│   ├── errors.py
│   └── events.py
├── application/
│   ├── jobs.py
│   ├── documents.py
│   ├── profiles.py
│   ├── reports.py
│   └── ai.py
├── infrastructure/
│   ├── settings.py
│   ├── database.py
│   ├── repositories/
│   │   ├── base.py
│   │   ├── sqlite_jobs.py
│   │   └── legacy_markdown.py
│   ├── file_store.py
│   ├── llm/
│   │   ├── base.py
│   │   └── openai_compatible.py
│   └── rendering/
│       ├── cv_pdf.py
│       └── report_pdf.py
├── interfaces/
│   ├── api/
│   │   ├── app.py
│   │   ├── dependencies.py
│   │   ├── schemas.py
│   │   └── routers/
│   └── cli/
│       ├── app.py
│       └── commands/
└── web/
    └── static/
```

Dependency direction:

```text
API / CLI
    ↓
Application services
    ↓
Repository interfaces
    ↓
SQLite / legacy Markdown / managed files
```

The application layer should not depend directly on FastAPI, Typer, ReportLab, or a concrete SQLite driver.

---

# Explicit Non-Goals During Early Milestones

Do not simultaneously:

- migrate to SQLite;
- add Tauri;
- add a vector database;
- rewrite the frontend;
- redesign the profile format;
- change the LLM provider.

Do not introduce Node.js into the current Python application without a concrete approved requirement.

---

# Current Next Step

Milestones 0 through 5 and the desktop workspace setup extension are
implemented and accepted in the current working tree. The next planned step is
Milestone 6; do not start it until separately requested.

Desktop workspace setup extension completed on 2026-07-25:

- clean desktop starts now request an empty local data folder plus required
  given name, family name, and email before initializing storage;
- `%LOCALAPPDATA%\jobtracker\workspaces.yml` atomically records remembered and
  active workspaces, while explicit `JOBTRACKER_DATA_DIR` remains a recovery
  and test override;
- the last active workspace opens automatically, and Settings supports create,
  open, switch, and forget without moving or deleting user data;
- workspace activation rejects unrelated nonempty folders, UNC and mapped
  network drives, known OneDrive roots, unreadable databases, and unsupported
  schemas;
- desktop-configured runtime paths must remain beneath the selected workspace;
- switching starts and verifies the new backend before updating the active
  pointer, reloads the existing pywebview window, and only then stops the old
  backend;
- canonical split-name profile data is now the source for reports and CV PDFs,
  with legacy profile and config names retained as read compatibility
  fallbacks.

Latest workspace setup verification on 2026-07-26:

- the pywebview bridge now exposes only the nine workspace API methods, after
  window creation and before the event loop starts; internal lifecycle methods
  are not available to JavaScript;
- setup integration initializes immediately, on `pywebviewready`, or through a
  bounded retry, and each action depends only on its required bridge methods;
- folder selection remains usable when setup-state loading or unrelated bridge
  methods fail, repeated selections replace the displayed path, and missing
  integration reports a restart instruction instead of a raw JavaScript error;
- when an existing workspace has an incomplete profile, setup now immediately
  reports the found path and missing required fields, labels the primary action
  `Save and open existing`, and offers `Choose another workspace` without
  letting the displayed folder diverge from the pending workspace;
- a later packaged run exposed that pywebview executes JavaScript API methods
  on an MTA worker thread while the Windows Forms file dialog requires STA
  execution; `WorkspaceBridge` now synchronously marshals the dialog call onto
  the native WinForms UI thread without widening the bridge API;
- all 137 tests passed, including Windows UI-thread marshaling, direct
  non-Windows and UI-thread paths, actionable dialog failures, a real non-GUI
  pywebview `Window` registry check, and Node-executed setup bridge behavior;
- Python bytecode compilation, JavaScript syntax validation, and
  `git diff --check` passed;
- PyInstaller 6.21.0 built
  `job-tracker-mvp/dist/JobTracker.exe` at 2026-07-26 17:21:13 +02:00;
  the executable is 38,716,586 bytes with SHA-256
  `4C78E068C4B0072A3EBBA0AE2A5DB156435F83EFA01D7E19088E61595CA9CF83`;
- two isolated packaged `--smoke-test` starts returned exit code 0 and reopened
  the same valid 118,784-byte SQLite workspace;
- Windows GUI automation during acceptance verified first-run
  readiness, native folder-dialog opening and cancellation, folder A then B
  replacement without selection-time writes, workspace B creation, automatic
  restart into B, and responsive window behavior throughout;
- a final packaged GUI launch against the incomplete default workspace visually
  verified the new warning and corrected alternative-workspace hint, then
  closed without leaving a JobTracker process;
- after a comment/private-name-only rebuild, the final artifact again passed
  native dialog opening, cancellation, and responsiveness checks;
- disabling Uvicorn's default logging configuration fixed a packaged
  windowed-restart failure caused by unavailable `sys.stderr`;
- all disposable GUI and smoke-test workspaces were removed afterward.

Managed parent-folder extension completed on 2026-07-27:

- `%LOCALAPPDATA%\jobtracker\settings.yml` now stores only the selected
  all-workspaces data-folder location;
- the selected folder owns a portable `workspaces.yml` catalog with a stable
  active workspace ID and root-relative workspace records;
- workspace child folders use readable profile-name slugs plus stable short
  UUID suffixes, and catalog validation rejects absolute, duplicate, or
  escaping paths;
- first-run setup explicitly explains that databases, uploads, profiles, and
  reports for all workspaces are stored below the selected local folder;
- the previous mixed default workspace is copied through a staging folder,
  using SQLite backup and integrity/count checks plus copied-file hashes, and
  the original remains untouched for recovery;
- external workspace opening is now a verified import into the managed parent;
  activation and forgetting use stable IDs, and forgetting never deletes data;
- desktop startup no longer creates empty `jobs/`, `input/`, `backups/`,
  `logs/`, `files/`, or `reports/` folders; managed uploads and reports remain
  lazy while explicit legacy CLI initialization keeps its prior behavior.

Managed parent-folder verification on 2026-07-27:

- all 147 tests passed, including catalog path safety and atomic replacement,
  legacy copy-and-retain behavior, locator rollback, staged-import failure
  cleanup, stable-ID switching, no-delete forgetting, and Node-executed setup
  bridge behavior;
- Python bytecode compilation, JavaScript syntax validation, and
  `git diff --check` passed.
- PyInstaller 6.21.0 rebuilt the one-file Windows executable successfully, and
  the packaged `--smoke-test` created a valid isolated SQLite workspace and
  exited successfully.

Same-folder adoption and workspace naming completed on 2026-07-30:

- legacy path-list registries are now distinguished by shape from managed
  catalogs; managed catalogs write version 2 while previously generated
  managed version-1 catalogs remain readable;
- the mixed default folder can become the managed parent without recursively
  copying its new `workspaces/` child or copying global `backups/`;
- adoption uses a verified staged child and a crash-recovery journal, preserves
  the old registry as `workspaces.v1.yml`, activates the managed workspace
  before archiving direct-layout data, and retains originals under
  `backups/layout-migration-<timestamp>/`;
- startup can recover a managed default-root catalog whose locator write was
  interrupted and can finish a pending post-activation archive;
- workspace creation accepts an optional display name. Blank names fall back
  to the immutable profile-slug plus stable-ID folder name, and Settings can
  rename catalog metadata without moving the folder or changing its ID;
- Settings displays the workspace name first, with applicant and path context,
  while switching, forgetting, and renaming continue to use stable IDs.

Same-folder adoption verification on 2026-07-30:

- all 158 Python tests passed, including exact same-root adoption, global-folder
  exclusion, copy verification failure, locator rollback, legacy-registry
  restoration, version-1 managed catalog compatibility, optional-name fallback,
  rename-without-move, and Node-executed setup bridge behavior;
- Python bytecode compilation, JavaScript syntax validation, and
  `git diff --check` passed;
- PyInstaller 6.21.0 rebuilt `dist/JobTracker.exe` successfully; the
  38,741,894-byte executable has SHA-256
  `1B4546FFDE21670D89B003735BEA788C3E6953E3F89527D51889BF1CF5ACCD56`;
- the packaged `--smoke-test` exited with code 0 against an isolated temporary
  override and created a valid 118,784-byte SQLite database;
- no migration or test was run against the live
  `%LOCALAPPDATA%\jobtracker` folder.

Milestone 0 acceptance completed on 2026-07-17:

- 27 of 27 configured legacy records were readable;
- 7 of 7 referenced attachments were present;
- hidden read failures were removed;
- frontmatter and atomic-write regression coverage passed;
- the 63-file legacy manifest remained unchanged.

Milestone 1 implementation completed on 2026-07-18:

- `JobTrackerService` now owns job creation, updates, timeline entries,
  attachments, listing, profiles, reports, CV drafts, and document rendering
  workflows;
- repository, profile, report, and document gateway contracts define the
  application layer's dependencies;
- `LegacyMarkdownRepository` preserves the existing folder and Markdown format;
- CLI add, attach, list, report, and export commands call the application
  service instead of editing Markdown or running duplicate workflows;
- FastAPI is built through `create_app(service)` and routes use injected
  application services;
- Pydantic request schemas validate job, profile, and CV draft boundaries;
- the application core imports none of FastAPI, Typer, ReportLab, or the
  concrete Markdown adapter;
- repository, application-service, schema, and shared CLI/API injection tests
  were added.

Latest Milestone 1 verification on 2026-07-18:

- all 84 tests passed;
- `job doctor --json` reported 27 directories, 27 parsed records, 0 failed
  records, 7 physical attachments, 7 referenced attachments, and 0 errors;
- the 22 known warnings remain: 19 `empty_description`, 2
  `duplicate_attachment_sha256`, and 1 `directory_date_mismatch`;
- the legacy dataset still contains 63 files and its aggregate manifest SHA-256
  remains `ce4a2d466fe9bc31aedb9750a2e5453d3b5404d17441618fcbf172bed77ae358`;
- `git diff --check` passed; only existing line-ending conversion warnings were
  emitted.

Milestone 1 acceptance criteria are met without a storage migration, folder
rename, SQLite dependency, frontend rewrite, desktop packaging, or RAG change.

Milestones 2 and 3 implementation completed on 2026-07-18:

- `AppPaths` resolves deterministic per-user runtime directories independently
  of the launch directory and supports explicit/environment overrides;
- `pyproject.toml` is the authoritative `0.2.0` version source and runtime,
  cache, log, report, profile, and generated-CV ignore rules were added;
- SQLite is the primary job metadata store for default runtime construction;
- the versioned initial schema includes applications, skills, status events,
  documents, profiles, profile items, document versions, AI runs, and migration
  sources;
- new applications receive UUID identifiers and job metadata, descriptions,
  timeline updates, attachments, reports, and versioned CV drafts round-trip
  through the shared application service;
- `job migrate preview`, `execute`, and `verify` preserve legacy Markdown as
  read-only input, copy and hash attachments, preserve exact legacy body and
  description text, record stable source identities, and block changed sources;
- migration uses standard-library `sqlite3` with explicit schema versioning
  instead of the optional SQLAlchemy/Alembic stack, avoiding new dependencies
  while meeting the transaction, constraint, and repeatability requirements.

Latest Milestones 2 and 3 verification on 2026-07-18:

- all 97 tests passed;
- migration preview against the real legacy tree discovered 27 records and 7
  attachments and created no destination directory;
- isolated execute imported 27 applications and 7 document associations;
- verify accepted 27 of 27 imports, and a second execute imported 0 and skipped
  27 without duplicates;
- imported status counts remained 17 `rejected`, 9 `applied`, and 1
  `interview_done`; `PRAGMA foreign_key_check` returned no rows;
- corruption, malformed-source, rollback/idempotency, UUID, path override,
  exact-text preservation, and repository/report integration tests passed;
- `job doctor --json` still reports 27 parsed records, 0 failures, 7 physical
  and referenced attachments, 0 errors, and the same 22 known warnings;
- the 63-file legacy aggregate manifest SHA-256 remains
  `ce4a2d466fe9bc31aedb9750a2e5453d3b5404d17441618fcbf172bed77ae358`;
- `git diff --check` passed with only existing line-ending conversion warnings.

Milestones 2 and 3 acceptance criteria are met without modifying legacy data,
deleting existing tracked personal artifacts, desktop packaging, a frontend
rewrite, Node.js, RAG, a vector database, or an LLM-provider change.

Milestone 4 implementation completed on 2026-07-18:

- `ManagedFileStore` writes binaries atomically beneath the configured
  `files/` root using collision-resistant UUID names and two-character shards;
- physical names retain only a safe normalized suffix, while original
  filenames remain document metadata;
- every managed write calculates SHA-256 while streaming, flushes and syncs the
  temporary file, independently verifies the written bytes, and removes
  temporary artifacts on failure;
- guarded path resolution rejects absolute paths, traversal, and any resolved
  destination outside the managed root;
- `SQLiteJobRepository` uses managed storage for new and attached files and
  removes newly written binaries if the surrounding database transaction
  fails;
- legacy migration stores managed paths separately from legacy
  `source_relative_path` values, allowing source-to-copy verification after the
  physical layout change;
- duplicate original filenames receive distinct physical UUID paths; SHA-256
  deduplication remains intentionally optional.

Latest Milestone 4 verification on 2026-07-18:

- all 101 tests passed, including managed-path, collision, hash-failure,
  database-rollback, migration rollback, idempotency, and corruption coverage;
- migration preview against the real legacy tree discovered 27 records and 7
  attachments without writing a destination;
- isolated execute imported 27 applications, 7 document rows, and 7 physical
  managed files with complete metadata and valid shard paths;
- verify accepted 27 of 27 imports, and a second execute imported 0 and skipped
  27 without duplicates;
- `job doctor --json` still reports 27 parsed records, 0 failures, 7 physical
  and referenced attachments, 0 errors, and the same 22 known warnings;
- the legacy tree remained unchanged before and after migration, still contains
  63 files, and has no Git worktree modifications;
- bytecode compilation and `git diff --check` passed; only existing line-ending
  conversion warnings were emitted.

Milestone 4 acceptance criteria are met without modifying legacy data,
introducing content deduplication or a garbage collector, converting existing
Milestone 3 runtime stores in place, desktop packaging, a frontend rewrite,
Node.js, RAG, a vector database, or an LLM-provider change.

Milestone 5 implementation completed on 2026-07-18:

- `jobtracker.desktop` initializes the shared application service and SQLite
  schema before opening the existing FastAPI UI in pywebview;
- the launcher reserves an operating-system-assigned port on `127.0.0.1` and
  passes the pre-bound socket to an in-process Uvicorn server, avoiding remote
  binding, fixed-port collisions, and a child-service process;
- closing the pywebview event loop requests Uvicorn shutdown and waits for the
  server thread, with a bounded forced-exit fallback;
- startup, bundled-asset, storage, and schema failures return a nonzero exit and
  are displayed through a native error dialog where available;
- `--smoke-test` exercises packaged application initialization without opening
  a window;
- `pyproject.toml` exposes minimal desktop/build extras and a GUI entry point;
- `jobtracker.spec` builds a one-file windowed executable and bundles the
  existing static UI, fonts, Uvicorn runtime modules, and pywebview hook data;
- runtime data remains in the per-user `AppPaths.data_dir`, never in the
  executable, installed-code, or PyInstaller extraction directories.

Latest Milestone 5 verification on 2026-07-18:

- all 106 tests passed, including five focused desktop tests for loopback-only
  random-port binding, lifecycle shutdown, startup failure propagation,
  isolated initialization, and data preservation;
- PyInstaller 6.21.0 with pywebview 6.2.1 built the Windows one-file executable
  successfully; `dist/JobTracker.exe` was 38,695,736 bytes;
- the packaged executable ran `--smoke-test` twice against a new isolated data
  directory without invoking system Python, returned exit code 0 both times,
  created SQLite, and preserved an existing sentinel file across restart;
- `job doctor --json` still reports 27 parsed records, 0 failures, 7 physical
  and referenced attachments, 0 errors, and the same 22 known warnings;
- the legacy tree still contains 63 files and has no Git worktree
  modifications;
- `git diff --check` passed with only existing line-ending conversion warnings.

Milestone 5 acceptance criteria are met without adding Tauri, Node.js, a
frontend rewrite, an installer, code signing, automatic updates, remote
binding, RAG, a vector database, or an LLM-provider change. Executable builds
remain platform-specific, and deleting the executable intentionally leaves user
data untouched.
