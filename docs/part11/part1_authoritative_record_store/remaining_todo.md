# Part 1 Remaining TODO

- [ ] Detect interrupted active batches at application startup.
- [ ] Add authorised recovery/reconciliation workflow with reason and audit.
- [ ] Block batch closure until reconciliation is completed.
- [ ] Reconcile device counts, scan sequences, and missing-device state.
- [ ] Record recovery decision and approver.
- [ ] Build controlled legacy WAL import tooling.
- [ ] Preserve/hash legacy source before import.
- [ ] Audit importer, source hash, row count, path, limitations, and outcome.
- [ ] Clearly mark legacy-imported records.
- [ ] Remove dead legacy fallback code after migration support is complete.
- [ ] Verify all Excel/CSV/PDF/recovery paths use `scan_records`.
- [ ] Add database-unavailable, concurrency, duplicate-delivery, restart,
  power-loss, immutable-trigger, stale-user, and export reconciliation tests.
- [ ] Install pytest in the project virtual environment and run the suite.
- [ ] Record approved validation evidence and release decision.
- [ ] Define deployment ACL, service-account, backup, and database protection.
