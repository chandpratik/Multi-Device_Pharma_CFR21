# Part 9 — Time, Validation, and Release Control

## Objective

Demonstrate through documented evidence that the system consistently meets its
intended use and that only reviewed, approved software/configuration is released.

## Implementation sequence

1. Standardize persisted timestamps to UTC RFC 3339/ISO 8601 with numeric offset
   and sufficient precision. Convert only for display.
2. Use a qualified time source and monitor synchronization status. Audit clock
   changes, backward movement, excessive drift, timezone changes, and NTP failure.
3. Establish bidirectional traceability from intended use and requirements to
   risks, design, code changes, tests, deviations, and release approval.
4. Create reproducible environments with pinned dependency versions and hashes.
5. Add unit, integration, system, security, data-migration, performance,
   concurrency, power-loss, backup/restore, and negative-path testing.
6. Prepare and execute IQ/OQ/PQ or the organization's approved equivalent.
7. Record actual results, objective evidence, reviewer, execution environment,
   deviations, corrections, retests, and approvals.
8. Create controlled build/release automation producing versioned binaries,
   software bill of materials, checksums/signatures, migration package, and
   deployment/rollback instructions.
9. Require independent review and QA approval before release to production.
10. Establish periodic review, vulnerability/dependency review, regression testing,
   incident handling, change impact assessment, and retirement/data-migration plans.

## Minimum validation scenarios

- All role and workflow permission combinations.
- Record and audit transaction rollback.
- Concurrent two-device operation at expected and peak rates.
- Duplicate, malformed, delayed, missing, and out-of-order device input.
- Crash/power loss at transaction boundaries.
- Audit/signature tampering and direct database attempts.
- Backup/restore and post-restore reconciliation.
- Accurate complete exports and long-term readability.
- Time drift, timezone change, and backward-clock behavior.
- Upgrade, schema migration, rollback, and legacy import.

## Definition of done

The approved requirements and risks are fully traced to passing objective evidence;
all deviations are resolved or formally accepted; the release is reproducible,
identified, approved, and deployed under change control.
