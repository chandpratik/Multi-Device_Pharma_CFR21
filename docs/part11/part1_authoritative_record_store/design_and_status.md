# Part 1 — Authoritative Regulated-Record Store

## Objective

The database transaction, rather than a CSV flush, GUI update, or Excel file,
must be the event that makes a scan an official regulated record.

## Implemented foundation

- `regulated_batches` and `scan_records` database tables.
- Transactional authoritative scan write before compatibility output.
- Per-batch/device sequence number and UUID scan ID.
- UTC database-record timestamp.
- Audit record inserted in the same transaction as batch/scan operations.
- Batch start/stop service methods.
- Scan immutability and delete-prevention SQLite triggers.
- Recipe, configuration, and device registry references linked to scans.
- Backend account/role/session-ID validation for batch/scan writes.
- Batch PDF reads authoritative database records.
- Compatibility WAL is generated at batch close from committed records.

## Important limitations

- SQLite triggers do not protect against a person who can replace or administer
  the entire local database file. Deployment access controls remain necessary.
- Recovery/reconciliation and legacy migration are incomplete.
- Recipe versions are currently created from captured master values; controlled
  recipe approval is a later control.
- Complete validation evidence has not been generated.

## Target authoritative entities

`users`, `regulated_batches`, `scan_records`, `recipe_versions`,
`configuration_versions`, `devices`, `audit_events`, `record_signatures`,
`exports`, `backups`, and `restore_events`.

## Required acceptance criteria

- No scan is acknowledged without a committed authoritative record.
- No committed scan is silently overwritten, deleted, or duplicated.
- Each regulated state change has transactional audit evidence.
- Recovery produces a reconciled record set.
- Official exports read authoritative records only.
