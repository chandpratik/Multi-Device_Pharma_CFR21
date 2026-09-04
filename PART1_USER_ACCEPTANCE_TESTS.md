# Part 1 user acceptance test checklist

Use a non-production test database and test folders. Record the tester, date/time, application version, result, evidence path/screenshot, and deviation number for every test. Do not use production batch identifiers or credentials.

## Preconditions

- Application is installed with the approved configuration.
- Test users exist: Administrator, Supervisor, Operator, QA, and a disabled/locked user.
- Each tester has a distinct account; do not share credentials.
- A controlled evidence folder is available for exports, backups, and legacy-import copies.
- Cameras/PLC may be simulated where physical equipment is unavailable; record the simulation method.

## Authentication and access control

| ID | Test | Expected result |
| --- | --- | --- |
| UAT-01 | Log in with a valid Operator account. | Login succeeds; user identity and session are shown. |
| UAT-02 | Attempt login with an invalid password until lockout policy applies. | Access is denied and the configured lockout is enforced/audited. |
| UAT-03 | Attempt login with a disabled user. | Access is denied. |
| UAT-04 | Log in as QA and attempt to start logging, change settings, manage users, and import legacy data. | Each restricted action is unavailable or denied. |
| UAT-05 | Log in as Supervisor and start/stop a batch. | Operation succeeds; user cannot access administrator-only user management/import functions. |
| UAT-06 | Log in as Administrator and create/deactivate/reactivate a test user. | Each action succeeds only with administrator authority and appears in the audit trail. |

## Authoritative batch capture

| ID | Test | Expected result |
| --- | --- | --- |
| UAT-07 | Start a batch with valid Batch ID, operator, and product. | Batch starts and audit event is created. |
| UAT-08 | Perform PASS and FAIL scans on Device 1 and Device 2. | Live counters and exported counts match per-device scans. |
| UAT-09 | Stop and close the batch. | Excel/CSV compatibility outputs are generated; integrity hashes are recorded; batch is closed. |
| UAT-10 | Attempt additional scans after batch stop/close. | Scan is rejected; no new record is added. |
| UAT-11 | Attempt to alter a closed/exported scan record through the application. | No edit function is available; audit/history remains unchanged. |

## Recovery and reconciliation

| ID | Test | Expected result |
| --- | --- | --- |
| UAT-12 | Start a batch, scan on both devices, then terminate the test application without normal batch closure. Restart it. | The batch is identified as interrupted and cannot silently resume. |
| UAT-13 | As an Operator, attempt to resume the interrupted batch. | Recovery is denied due to authorization. |
| UAT-14 | As an Administrator or Supervisor, select the interrupted batch and enter a recovery reason. | Reconciliation displays/restores authoritative per-device counts; resumption is audited with approver and reason. |
| UAT-15 | After recovery, perform another scan on each device. | Each device sequence continues without duplication or gap. |
| UAT-16 | Attempt to close a batch while it remains reconciliation-pending. | Closure is blocked. |

## Reports, exports, and integrity

| ID | Test | Expected result |
| --- | --- | --- |
| UAT-17 | Export the batch PDF after scans on both devices. | PDF includes the authoritative scan rows and PASS/FAIL totals. |
| UAT-18 | Compare PDF, Excel, CSV compatibility output, and screen totals to the batch record for each device. | Counts and ordering reconcile to `scan_records`; document any discrepancy as a deviation. |
| UAT-19 | Run file-integrity verification for a closed batch. | Stored and calculated SHA-256 values match. |
| UAT-20 | Make a controlled copy of an exported file and alter the copy; verify the original batch file. | Original remains intact; do not alter the sealed original. If permitted by test protocol, alteration of a sealed test copy is detected as a hash mismatch. |
| UAT-21 | Export an audit-trail report covering the preceding tests. | Report identifies who performed each relevant action, when, what occurred, and recovery/import reasons where applicable. |

## Controlled legacy WAL import

| ID | Test | Expected result |
| --- | --- | --- |
| UAT-22 | As QA or Operator, attempt a legacy WAL import. | Access is denied. |
| UAT-23 | As Administrator, import an approved test CSV WAL to a controlled evidence folder. | Source file is preserved; source hash, user, timestamp, path, row count, limitations, and reconciliation report are recorded. |
| UAT-24 | Review the imported batch and report. | Imported records are clearly labelled legacy/non-retrospectively-compliant. |
| UAT-25 | Attempt to import the identical source again. | Duplicate import is rejected by source hash. |

## Deployment controls

| ID | Test | Expected result |
| --- | --- | --- |
| UAT-26 | Verify the application data directory ACL using the approved deployment checklist. | Only the designated service account has write/modify access; ordinary operators cannot directly edit database/log files. |
| UAT-27 | Run a controlled backup and verify the resulting backup at the approved external location. | Backup succeeds, is accessible only to approved roles, and is recorded. |
| UAT-28 | Perform an approved restore rehearsal using a copy of the test database. | Restore procedure succeeds; integrity/audit checks are documented. |

## Acceptance decision

QA should approve only when every applicable test passes, evidence is attached, deviations are resolved or formally accepted, and the deployment controls in `PART1_DEPLOYMENT_AND_LIMITATIONS.md` are approved.
