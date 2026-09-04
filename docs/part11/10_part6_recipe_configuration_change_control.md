# Part 6 — Recipe and Configuration Change Control

## Objective

Control every production-affecting recipe, master-code, device, PLC, security,
and system configuration change through immutable versions and approved workflow.

## Implementation sequence

1. Classify settings as regulated, security-related, operational, or cosmetic.
2. Define canonical schemas for recipe/master and configuration versions.
3. Store immutable full snapshots with version UUID, version number, creator,
   UTC creation time, reason, status, predecessor ID, and content hash.
4. Implement workflow states: draft, pending approval, approved, effective,
   superseded, and retired.
5. Require backend permission and reason for creation/change. Require appropriate
   electronic signature before approval/effectiveness.
6. Link each batch to the exact effective recipe and configuration version before
   it enters Active state.
7. Block version changes for an active batch; exceptional changes require a
   deviation and controlled batch transition.
8. Preserve field-level old/new differences as structured audit data.
9. Prevent direct `settings.json` edits from becoming effective. Treat files only
   as generated deployment/cache artifacts or controlled imports.
10. Add controlled export/import with schema validation, source hash, authorization,
   approval, and environment compatibility checks.

## Verification

- Modify every regulated field and confirm old/new values and reason are retained.
- Attempt to alter an effective version or active-batch reference and confirm rejection.
- Attempt to activate an unsigned/unapproved version.
- Change `settings.json` externally and confirm it cannot silently affect production.
- Reproduce a historical batch using its stored version snapshots.

## Definition of done

Every production decision can be reconstructed from immutable, approved recipe
and configuration versions linked to the batch and scans that used them.
