# Part 7 — Backup, Retention, Restore, and Export

## Objective

Protect records for the required retention period, prove backups are usable, make
restoration controlled, and produce accurate and complete human-readable and
electronic copies.

## Implementation sequence

1. Define retention classes and periods for batches, scans, audit trails,
   signatures, configuration/recipes, exports, backup evidence, and legacy data.
2. Replace count-based deletion with policy-based disposition requiring authority,
   documented eligibility, approval, and audit evidence.
3. Back up the complete record system, including database, keys/checkpoints,
   configuration, schema version, and required attachments.
4. Store encrypted backups off-host with restricted write/delete access and
   protected retention/immutability where available.
5. Calculate and register backup hashes; a seal/audit failure must fail the backup
   operation rather than report success.
6. Implement an authorized restore service with selected-backup verification,
   reauthentication, reason, pre-restore backup, and full audit event.
7. After restore, verify database integrity, audit checkpoints, signatures,
   schema compatibility, batch consistency, and record counts before use.
8. Reconcile records created after the restored point and prevent production until
   QA disposition.
9. Generate CSV/XLSX/PDF only from authoritative records. Register export query,
   record range/count, source checkpoint, generated-by, UTC time, and file hash.
10. Block official exports when the batch is incomplete, integrity fails, or
   required review/signature is absent.

## Verification

- Restore multiple backup ages into an isolated environment.
- Detect altered, truncated, wrong-version, and wrong-system backups.
- Confirm retention jobs cannot remove in-scope records.
- Compare export counts/values/hashes against source queries.
- Verify timezone, signatures, audit history, and full record values in exports.

## Definition of done

Records remain protected and retrievable throughout retention; restoration is
tested and reconciled; exports are accurate, complete, attributable, and verifiable.
