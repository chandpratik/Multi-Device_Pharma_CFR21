# Part 3 — Audit-Trail Controls

Create a secure, computer-generated, time-stamped audit trail that records
regulated creation, modification, deletion attempts, workflow transitions,
privileged actions, and signature events without depending on GUI callers.

## Incremental implementation status (2026-09-06)

The first Part 3 increment is implemented in the current codebase:

- `audit_trail` now has structured event fields for event UUID, actor ID,
  target, version, old/new JSON values, result, and correlation ID.
- `AuditEvent` provides the event contract and readable rendering, while the
  compatibility `audit.log()` wrapper remains available to existing callers.
- `AuditWriter` is the single serialized writer. Authoritative batch, scan,
  draft, and legacy-import transactions use its in-transaction API, so a
  required audit failure rolls back those business writes.
- New events are signed with a generated protected-key file outside SQLite,
  and the latest event is atomically recorded in an external anchor file.
  Verification checks the structured hash chain, signatures, and anchor.
- Database backups receive signed detached audit checkpoint sidecars. Restore
  candidates must pass SQLite integrity checks, file-digest verification,
  checkpoint-signature verification, protected audit-signature verification,
  and detached-tail verification before live replacement is attempted.
- Authorized database restore now requires an issued administrator session,
  a non-empty reason, and a change-control identifier. Restore evidence is
  staged in the candidate database without moving the live anchor; after atomic
  replacement, the new anchor is published and the live chain is re-verified.
  Replacement/post-replacement failures restore a rollback snapshot and record
  failure evidence where audit integrity permits.
- Regression coverage was added for structured fields, rollback on audit
  failure, concurrent writers, local-hash recalculation, tail truncation,
  backup checkpoint verification, authorized restore, tampered candidate
  rejection, anchor publication, replacement rollback, and restore audit-write
  failure.
- Runtime connections cannot update or delete audit rows or remove the
  append-only protection triggers. SQLite deployment limitations are recorded
  in runtime_schema_controls.
- Startup, export, backup, and scheduled integrity verification fail closed
  when the signed chain or external anchor is invalid.
- Remaining compatibility calls are limited to authentication denials and
  session/UI lifecycle notifications, where there is no regulated business
  row to couple. Regulated account, device, version, settings, backup, export,
  restore, review, and retention boundaries use the shared writer directly.
- The execution checklist is documented in manual_test_checklist.md for QA/UAT
  evidence, including negative-path, tamper, restore, and deployment tests.

This is an engineering increment, not validation evidence or a claim of full
Part 11 compliance. The remaining production responsibility is execution and
approval of the deployment ACL procedure and validation evidence for the target
Windows service account. Physical audit pruning remains intentionally
unavailable until an approved archive system exists.

## Implementation sequence

1. Define an audit-event schema containing event UUID, UTC timestamp, actor ID,
   role, authenticated session ID, workstation, action, target type and ID,
   target version, old value, new value, reason, result, and correlation ID.
2. Replace free-form-only audit messages with structured fields plus a readable
   rendering.
3. Move audit creation into the same database transaction as every regulated
   business write.
4. Centralize all audit writes through one serialized writer/transaction service.
5. Make audit failure fail the associated regulated operation; raise a visible
   alarm and record the recovery outcome.
6. Prohibit application roles from updating or deleting audit records. Separate
   schema ownership from the runtime write identity where the database supports it.
7. Add tamper evidence backed by a protected signing key or externally anchored
   checkpoints. Do not rely solely on a recalculable local hash chain.
8. Verify the chain/checkpoint automatically at startup, before official export,
   after restore, and on a scheduled basis.
9. Add audit review filters, review acknowledgement, exception escalation, and
   evidence of who reviewed which record range.
10. Define audit retention and prohibit pruning before the approved retention end.

## Events that must be covered

- Login success/failure, lockout, unlock, logout, timeout, and session handover.
- Batch creation, start, stop, recovery, reconciliation, review, and release.
- Scan creation and rejected duplicate/input attempts.
- Recipe/master and configuration version changes.
- Account and role changes, password resets, backup/restore, export, device
  authorization, clock anomaly, integrity failure, and application crash.
- Attempts to update/delete immutable or signed records.

## Verification

- Force audit insertion failure and confirm the regulated write rolls back.
- Run simultaneous writers and confirm no lost events or broken chain.
- Modify, insert, delete, reorder, and truncate audit data and confirm detection.
- Recalculate local hashes without the protected key and confirm verification fails.
- Verify old/new values and reasons for each regulated change type.

## Definition of done

Every regulated write and privileged action has complete, immutable, reviewable,
transactionally coupled audit evidence, with tested tamper detection and retention.
