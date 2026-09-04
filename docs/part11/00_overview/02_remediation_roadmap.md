# 21 CFR Part 11 Remediation Roadmap

## Part 0 — Freeze and baseline

Archive current source, databases, files, reports, settings, and backups under
change control. Define intended use, regulated records, roles, workflows,
retention, and signature-required actions.

## Part 1 — Authoritative regulated-record store

Make controlled database records authoritative for batches, scans, recipes,
configuration, audit events, signatures, exports, backups, and recovery.
CSV/XLSX/PDF become derived outputs only.

## Part 2 — Backend security and workflow

Enforce role, session, lock, device, and state-machine checks below the GUI.

## Part 3 — Audit trail controls

Make audit record creation transactional, protected, verified, and fail closed.

## Part 4 — Electronic signatures

Implement signature manifestation, signer authentication, meaning, immutable
record/version links, and signed-record controls.

## Part 5 — Crash/recovery controls

Implement persisted batch state, controlled resume/reconciliation, and tested
power-loss recovery.

## Part 6 — Recipe/configuration change control

Version recipe/master and configuration values with old/new values, reason,
approval, signature, and effective date.

## Part 7 — Backup, retention, restore, and export

Add controlled retention, protected/off-host backups, audited restore and
reconciliation, and integrity-gated exports.

## Part 8 — Credential and administrator controls

Harden bootstrap credentials, audit all account actions, prevent impersonation,
and define privileged-operation safeguards.

## Part 9 — Time, validation, and release control

Standardize UTC timestamps, test controlled time handling, execute validation,
and establish controlled build/release/deployment evidence.
