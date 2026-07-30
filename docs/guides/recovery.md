# Database Backup and Recovery

Status: current

Last updated: 2026-07-30

## Scope

Recover a workspace after a schema upgrade failure, or validate a user-created
workspace export. These steps do not modify a live database while Job Tracker
is running.

## Migration backup

Job Tracker creates a verified SQLite snapshot before the first upgrade write.
Settings shows its folder, source/target schema versions, creation time, size,
and verification state.

If startup reports an upgrade failure:

1. Close Job Tracker completely.
2. Preserve the failed workspace `jobtracker.sqlite3` under a different name;
   do not overwrite the migration backup.
3. Open the newest matching folder below
   `<workspace>/backups/migrations/`.
4. Confirm `manifest.json` names `jobtracker.sqlite3`, reports the expected
   source schema, and has `"verified": false` or `"verified": true`. An
   unverified upgrade status does not mean the snapshot checksum failed; it
   means the upgraded application did not finish opening.
5. Copy that folder's `jobtracker.sqlite3` over the workspace database.
6. Start the same or an older compatible Job Tracker build. If the upgrade
   fails again, keep both databases and report the public error text.

Managed files and profile files are not changed by a schema restore.

## User-created export

Choose **Settings -> Create verified export**. Job Tracker writes a ZIP below
`<workspace>/backups/exports/` containing:

- a consistent SQLite snapshot;
- managed files below `files/`;
- profile sources below `profile/`;
- `manifest.json` with relative names, sizes, SHA-256 values, schema version,
  application version, and UTC creation time.

The export is verified before success is reported and is never subject to
migration-backup retention. Keep a copy on storage appropriate for the
sensitivity of CV and job data.

An export is not activated or restored automatically. To inspect one, copy it
to a temporary local folder, validate its manifest/checksums, and import the
reconstructed workspace through the desktop workspace flow.

## Safety limits

- Never replace the active database while Job Tracker is running.
- Never put a live SQLite workspace on OneDrive, a network drive, or a
  concurrently synchronized folder.
- Backup integrity cannot protect against loss of every copy or malware running
  as the same operating-system user.
