# 21 CFR Part 11 Software Audit

## Executive conclusion

The audited source was not suitable for regulated production use without major
remediation and validation. It had useful foundations (named accounts, bcrypt,
lockout, session lock, audit-event hashes, checksums, and database backups),
but its primary production records were mutable CSV WAL files and electronic
signatures were absent.

## Critical findings

1. **F-01 — Mutable primary records.** `comms/excel_wal.py:80-131` wrote scan
   data to CSV. The file had no access control, immutable versioning, or
   record-level audit trail. A user could alter PASS/FAIL data without old/new
   values, identity, reason, or timestamp.
2. **F-02 — Direct database/audit tampering.** `cfr21/db.py:70-109` used a
   local SQLite database. `cfr21/audit_trail.py:102-263` used a public hash
   algorithm with no protected signing key; a capable local user could modify
   rows and recalculate the chain.
3. **F-03 — Electronic signatures absent.** No implementation supplied
   signature manifestation, meaning, record linking, or signature controls
   required by 21 CFR Part 11 Subpart C.
4. **F-04 — GUI-only authorization.** `core/app_controller.py:90-200` did not
   enforce session/role/workflow checks. Backend calls could bypass disabled
   GUI controls.
5. **F-05 — Audit failures were tolerated.** `cfr21/audit_trail.py:119-195`
   returned failure while callers normally continued. Record/configuration and
   audit writes were not consistently one transaction.
6. **F-06 — Crash recovery split batches.** `core/datalogger.py:164-172,
   293-304` reset read IDs and generated new WAL filenames after restart.

## High findings

- **F-07:** WAL used `flush()` but not durable synchronization; power loss
  could lose acknowledged records (`comms/excel_wal.py:106-132`).
- **F-08:** Account/password-change audit depended on GUI callers rather than
  being guaranteed by the write boundary (`cfr21/user_manager.py:305-592`).
- **F-09:** Concurrent audit writes could fail and be silently lost
  (`cfr21/audit_trail.py:142-195`).
- **F-10:** Backups were automatically pruned to ten and no controlled restore
  or reconciliation workflow existed (`cfr21/db_backup.py:42-149`).
- **F-11:** Backup integrity sealing could fail while backup still reported
  success; `ACTION_BACKUP_CREATED` was unused.
- **F-12:** Master-code changes were in-memory and GUI-audited; previous value
  and controlled version history were incomplete (`core/datalogger.py:149-160`).
- **F-13:** Batch exports trusted mutable WAL data and could be incomplete
  (`cfr21/report_export.py:336-606`).
- **F-14:** Device authorisation occurred only in GUI code
  (`gui/main_window.py:1341-1355`).

## Medium findings

- **F-15:** Local/ambiguous timezone timestamps were used.
- **F-16:** Password-history hashes were silently deleted beyond a limit.
- **F-17:** Screen lock did not revoke backend authority.
- **F-18:** Configuration audit coverage was incomplete and non-atomic.
- **F-19:** JSON session lists could be overwritten without controlled history.
- **F-20:** Initial administrator password was printed and written plaintext.

## Low findings

- **F-21:** No automatic audit-chain verification at startup.
- **F-22:** Crash log was a mutable text file.
- **F-23:** Test execution could not be demonstrated because pytest was absent.

## Controls observed in source

- Case-insensitive unique usernames: `cfr21/db.py:177-200`.
- bcrypt password verification and policy controls: `cfr21/user_manager.py`.
- Soft account deactivation.
- Login/logout/lock events.
- SQLite commit/rollback context manager.
- Database quick-check and online-backup integrity check.
- WAL write failure attempts to stop logging.

## Source-code limits

The audit could not establish OS ACLs, physical access, time synchronization,
SOPs, training, validation evidence, backup restore testing, retention policy,
or controlled deployment/release practices.
