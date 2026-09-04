# Part 3 — Audit-Trail Controls

## Objective

Create a secure, computer-generated, time-stamped audit trail that records
regulated creation, modification, deletion attempts, workflow transitions,
privileged actions, and signature events without depending on GUI callers.

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
