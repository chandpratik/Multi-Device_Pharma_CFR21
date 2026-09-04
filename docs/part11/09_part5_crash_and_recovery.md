# Part 5 — Crash and Recovery Controls

## Objective

Ensure application crash, power failure, device loss, database failure, and
restart cannot silently lose, duplicate, reorder, or misattribute regulated data.

## Implementation sequence

1. Persist batch state, per-device sequence, configuration/recipe references,
   and last committed scan entirely in the authoritative database.
2. Use database-generated sequence allocation and idempotency keys derived from
   the device event/message where possible.
3. Acknowledge a scan to the UI only after transaction commit. Define the safe
   PLC/reject ordering with the process owner and validate it.
4. Detect active/incomplete batches at startup before normal batch creation.
5. Present controlled choices: resume, stop and reconcile, or quarantine. Require
   authority, reauthentication where appropriate, and a reason.
6. Add `recovery_events` and reconciliation records containing pre/post counts,
   device state, sequence gaps, duplicate checks, decision, actor, and approval.
7. Prevent release/closure until all discrepancies are resolved or documented as
   approved deviations.
8. Make derived export regeneration deterministic from authoritative records.
9. Capture crash events in protected storage and correlate them to active batches.
10. Document safe shutdown, emergency stop, database outage, and restoration SOPs.

## Verification

- Kill the process before, during, and after scan commit.
- Simulate abrupt power loss, disk full, database lock, and corrupt database.
- Restart with one or both devices previously active.
- Replay the same device event and verify idempotent behavior.
- Verify sequences have no unexplained duplicate or gap.
- Confirm an incomplete batch cannot be released without reconciliation.

## Definition of done

Every interruption produces a deterministic, attributable recovery path and a
reconciled authoritative record set with no silent loss or duplication.
