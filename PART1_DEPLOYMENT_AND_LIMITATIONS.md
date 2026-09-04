# Part 1 deployment controls and residual limitations

## Database-file protection

Deploy `compliance.db`, its `-wal` and `-shm` companions on an NTFS volume. Grant the production service account Modify rights only to the application data directory; grant operators read access only through the application, not direct filesystem access. Administrators who maintain the host must not use SQLite tools to alter production records. Protect the executable/configuration directory with read/execute access for operators and controlled change access for the release account.

Run the application under a dedicated, non-interactive Windows service account. Do not use a shared administrator account. The account requires write access to the data/log/backup directories and no general local-administrator privilege.

Backups must be written to a separate, access-controlled network or immutable backup location, encrypted in transit and at rest, with retention and restore testing defined by QA. Do not place the backup destination inside the writable application data directory.

## Recovery and legacy data

At startup, authoritative batches left `active` are marked `reconciliation_pending`. An administrator or supervisor must use the recovery boundary with an authenticated session and documented reason. The system records authoritative per-device PASS/FAIL totals and last sequence numbers before resuming. A sequence gap blocks resumption. A pending batch cannot be closed.

Legacy WAL imports are administrator-only, preserve a hash-verified source copy, create a reconciliation JSON report, and mark imported scan rows `legacy_import`. They do not make old data retrospectively Part 11 compliant.

## QA/change-control approval required

Before production release, QA must approve the intended ACLs, service-account identity, backup location/retention, restore-test evidence, recovery SOP, and this residual-risk statement. Change control must approve the migration to schema version 7 and the validated software/test-results record.

## Current limitations

The application does not itself configure Windows ACLs, provision service accounts, or manage the external backup system; these are deployment controls. The recovery and legacy-import workflows are available in the application, but their use must be governed by approved operating procedures. Legacy source records remain non-retrospectively-compliant by design.
