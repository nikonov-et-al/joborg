# Release Guide

Status: current

Last updated: 2026-07-30

## Scope

Build and validate the unsigned Windows development executable. Shared
production artifacts require an approved publisher identity and code-signing
step before distribution.

## Clean environment

From `job-tracker-mvp/`:

```powershell
py -3.14 -m venv .venv-release
.\.venv-release\Scripts\python.exe -m pip install --upgrade pip
.\.venv-release\Scripts\python.exe -m pip install -c constraints-release.txt -e ".[dev,ai,desktop,build]"
.\.venv-release\Scripts\python.exe -m pytest
```

`constraints-release.txt` is the reviewed Windows release input. Update it only
after a clean install and full verification.

## Build and smoke test

```powershell
.\.venv-release\Scripts\python.exe -m PyInstaller --noconfirm --clean jobtracker.spec
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke_package.ps1 -Artifact .\dist\JobTracker.exe
.\.venv-release\Scripts\python.exe scripts\release_manifest.py .\dist\JobTracker.exe
```

The smoke script invokes the executable directly, uses a new temporary
`JOBTRACKER_DATA_DIR`, checks repeat startup preserves a sentinel, then removes
the temporary workspace. It never invokes system Python.

The manifest contains only versions, artifact filename, byte size, SHA-256, and
the `unsigned-development` label. It contains no absolute path or user data.

## Signing

Do not share the current unsigned development artifact as a trusted release.
When distribution requires signing, use an organization-controlled certificate
outside this repository, sign the executable, verify its signature on a clean
Windows machine, then generate the checksum manifest from the signed bytes.

## Upgrade, rollback, and uninstall

- Upgrade: close Job Tracker, create a verified workspace export, then replace
  the executable. Workspace data is external to the executable.
- Rollback: close Job Tracker, preserve the current database, restore the prior
  executable, and restore a verified migration backup only when schema recovery
  is required. Follow [`recovery.md`](recovery.md).
- Uninstall: remove the executable. Keep the configured data folder by default.
  Delete workspace data only as a separate, explicit user action after backup.

No automatic update service, telemetry, or crash upload exists.
