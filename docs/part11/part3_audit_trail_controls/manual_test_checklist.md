# Part 3 Manual User Acceptance Test Checklist

## Purpose

Verify that the application creates complete, attributable, tamper-evident,
reviewable, and retention-protected audit evidence for regulated operations.
Execute this checklist in an isolated validation environment before production
deployment. Do not modify production database files during tamper tests.

This checklist is manual validation evidence. It does not replace approved
requirements, risk assessment, IQ/OQ/PQ protocols, SOPs, or QA release
approval.

## Test Record

- Test protocol ID:
- Software version / commit:
- Database schema version:
- Test environment:
- Workstation:
- Test database path:
- Backup destination:
- Tester:
- Independent reviewer:
- Execution start (UTC):
- Execution end (UTC):
- Deviation record(s):

## Prerequisites

1. Use a fresh isolated database, or restore a disposable validation database.
2. Create individual test accounts for Administrator, Supervisor, Operator,
   and QA. Do not share credentials.
3. Issue an active session for each account through the normal login workflow.
4. Prepare two device identities with device number, source identifier, and
   display name. Have one additional unknown or unapproved device identity.
5. Prepare an approved configuration and recipe change package with a written
   change reason.
6. Prepare a separate backup destination outside the application data
   directory.
7. Confirm compliance.db, audit_signing.key, and audit_anchor.json are
   created beside the configured application data.
8. Record the starting audit record count and run the built-in audit
   verification. The result must be PASS.
9. For negative tamper tests, make a byte-for-byte copy of the database and
   its anchor/key files. Perform destructive file tests only on the copy.
10. Keep screenshots, exported reports, database copies, ACL output, and
    expected-versus-actual results with the validation package.

## Acceptance Rules

- PASS requires the observed result to match the expected result and the
  required audit evidence to be present.
- FAIL requires a deviation record; do not continue a dependent test until QA
  decides whether retesting is allowed.
- An audit-write failure must never be reported as a successful regulated
  operation.
- A tamper or integrity failure must block backup, restore, or official export
  as specified below.
- Record timestamps in UTC and retain the exact audit record IDs used as
  evidence.

## A. Audit Trail Initialization And Structure

### P3-A01 Protected artifacts are created

1. Start the application with a new isolated data directory.
2. Complete the first controlled login.
3. Confirm that audit_signing.key and audit_anchor.json exist beside the
   database.
4. Confirm the signing key is not stored in the SQLite database.

Expected result:

- Both files are created outside SQLite.
- The key is not displayed in the application or stored in plaintext in the
  database.
- The application creates an APP_STARTED and successful LOGIN event.

Evidence: directory listing, file properties, database query/export, and event
IDs.

### P3-A02 Structured event fields are complete

1. Perform one controlled configuration or device operation with a reason.
2. Open the audit viewer and locate the resulting event.
3. Verify the event contains event ID, UTC timestamp, actor, role, session,
   workstation, action, target type and ID, reason, result, correlation ID,
   local hash fields, and protected signature.
4. Verify old/new values are present where the operation changes a value.

Expected result:

- The event is computer-generated and attributable to one individual.
- The readable detail is understandable without opening application logs.
- The reason and result match the user action.
- The event is visible through the audit viewer and export.

Evidence: screenshot and exported audit row with its exact event ID.

### P3-A03 Hash chain and external anchor verify

1. Add at least five events through different workflows.
2. Open the audit viewer and select the chain verification action.
3. Record the checked-record count and displayed result.
4. Inspect the external anchor and confirm it identifies the current tail
   event, record hash, and signature.

Expected result:

- Verification reports PASS.
- The checked count includes all hashed events.
- The external anchor matches the final structured audit event.

Evidence: verification result, anchor copy, and audit event ID.

## B. Authorization And Attribution

### P3-B01 Individual session attribution

1. Log in as Administrator and perform one authorized operation.
2. Log out and log in as QA, then perform an authorized review or export.
3. Compare the two events in the audit viewer.

Expected result:

- Each event has the correct username, role, session ID, workstation, and UTC
  timestamp.
- The second user cannot appear as the actor for the first user's event.

### P3-B02 Unauthorized backend actions are denied

1. As Operator, attempt to manage users, manage devices, change settings,
   approve versions, restore the database, and manage retention.
2. Repeat one action with an unknown session ID.
3. Repeat one action after the account or session has been revoked.

Expected result:

- Each protected operation is denied before the regulated change occurs.
- An AUTHORIZATION_DENIED event identifies the requested operation, target,
  session, and denial reason.
- No protected data or configuration is changed.

### P3-B03 Required reasons and change control

1. As Administrator, attempt a settings change with an empty reason.
2. Attempt a database restore with an empty reason.
3. Attempt a database restore with an empty change-control identifier.
4. Attempt audit review acknowledgement, exception escalation, and retention
   policy change with an empty reason.

Expected result:

- Each operation is rejected.
- No business change is committed.
- Restore and other applicable failure events contain the failure reason.

## C. Transaction-Coupled Regulated Changes

### P3-C01 Account changes are coupled to audit evidence

1. As Administrator, create a named Operator account.
2. Deactivate that account and confirm its active sessions are revoked.
3. Reactivate the account.
4. Reset its password using the required reauthentication.
5. Review the events USER_CREATED, USER_DEACTIVATED, USER_REACTIVATED,
   and PASSWORD_RESET.

Expected result:

- Each account change and its audit event commit together.
- The event identifies the target account and new state.
- A deactivated account cannot use its old active session.

### P3-C02 Device registry changes are coupled to audit evidence

1. Register a device and record its pending status.
2. Approve it with reauthentication.
3. Assign it to a draft or configured batch.
4. Replace it and approve the replacement.
5. Deactivate one device.
6. Review DEVICE_REGISTERED, DEVICE_APPROVED, DEVICE_DEACTIVATED,
   DEVICE_REPLACED, and BATCH_DEVICE_ASSIGNED.

Expected result:

- Every state change has one structured audit event with the target device,
  reason, actor, and session.
- A duplicate or invalid identity is rejected.
- A device assignment cannot be changed after acquisition starts.

### P3-C03 Recipe and configuration version changes are coupled

1. Create a pending configuration version and pending recipe version.
2. Attempt to approve each version as its creator.
3. Approve each version as a different authorized QA user with
   reauthentication.
4. Review the creation and approval events.

Expected result:

- A creator cannot approve their own version.
- Approved content remains immutable.
- Creation and approval events identify version, target, reason, old/new
  status, actor, and session.

### P3-C04 Audit failure rolls back a regulated change

1. In an isolated test environment, make the signing key or anchor
   temporarily unavailable to the application using an approved test
   procedure.
2. Attempt a device registration, configuration creation, or account
   creation.
3. Restore the protected artifact access.
4. Query the database and verify the attempted business row does not exist.

Expected result:

- The operation fails visibly.
- The business mutation is rolled back when audit evidence cannot be written.
- No success event is claimed for the failed operation.
- The environment is returned to a verified state before continuing.

Evidence: failure message, before/after row counts, and deviation record if
the test setup required administrator intervention.

## D. Integrity Verification And Fail-Closed Gates

### P3-D01 Runtime audit rows are append-only

1. From the normal application connection, attempt to update an audit row.
2. Attempt to delete an audit row.
3. Attempt to drop the audit protection trigger.
4. Confirm that normal application writes can still append a valid event.

Expected result:

- Update, delete, and removal of audit protection are denied.
- Valid application audit inserts continue to work.
- No existing audit row changes.

### P3-D02 Tamper detection on an isolated copy

For each test below, restore the untouched copy before proceeding:

1. Change the detail of one audit row.
2. Recalculate its local record hash without the signing key.
3. Delete a middle audit row.
4. Insert an unsigned or forged audit row.
5. Reorder or truncate the audit tail.
6. Change the external anchor to point to a different event.

After each change, start the application against the isolated copy or run
audit verification.

Expected result:

- Verification reports FAIL for every alteration.
- The message identifies a hash, signature, chain, or anchor failure.
- Official export and backup are blocked while the failure remains.
- The original validation database and protected artifacts remain unchanged.

### P3-D03 Startup integrity gate

1. On an isolated copy, corrupt or remove the audit anchor, or modify an audit
   row using a controlled maintenance tool.
2. Start the application.

Expected result:

- Startup displays or records an audit integrity failure.
- Production workflows are not made available until the issue is investigated.
- The failure is not silently treated as a clean startup.

### P3-D04 Scheduled integrity verification

1. Start the application with a verified audit trail.
2. Leave the application running until the configured scheduled integrity
   interval, or use an approved test-time interval override.
3. Record the scheduled verification result.
4. Repeat with a tampered isolated copy.

Expected result:

- The scheduled check records a PASS for an intact chain.
- A tampered chain produces a critical integrity failure and does not report
  PASS.

## E. Backup, Restore, And Export

### P3-E01 Authorized manual backup

1. As Administrator, run a manual backup to the separate backup destination.
2. Confirm the backup opens with SQLite integrity check.
3. Confirm the detached audit checkpoint sidecar exists.
4. Verify the backup through the restore-candidate verification function or
   equivalent application workflow.
5. Review the BACKUP_CREATED event.

Expected result:

- The backup contains a consistent database.
- The sidecar binds the backup digest, audit tail, and protected signature.
- The event identifies the backup filename, actor, session, and destination.

### P3-E02 Backup fails closed on audit integrity failure

1. Use an isolated tampered database copy.
2. Attempt a manual or automatic backup.

Expected result:

- Backup is blocked before a usable backup is reported.
- No unaudited backup artifact remains in the destination.

### P3-E03 Valid authorized restore

1. Create a valid backup containing a known marker row or known audit tail.
2. Make a harmless change to the live validation database after the backup.
3. As Administrator, provide a documented reason and change-control ID.
4. Restore the valid backup.
5. Confirm the known post-backup change is removed.
6. Verify the live chain and inspect the new external anchor.
7. Review DATABASE_RESTORE_COMMITTED.

Expected result:

- The candidate is verified before replacement.
- The restore is authorized only for an active Administrator session.
- The committed event is present in the restored database.
- The new anchor is published only after replacement.
- Post-restore chain verification reports PASS.

### P3-E04 Restore rejects a tampered candidate

1. Copy a valid backup and modify its bytes, audit row, or checkpoint sidecar.
2. Attempt restore with valid Administrator credentials, reason, and
   change-control ID.

Expected result:

- Restore is rejected before live replacement.
- The live database and anchor are unchanged.
- DATABASE_RESTORE_FAILED identifies the rejected candidate and reason.

### P3-E05 Restore replacement failure rolls back

1. In an isolated environment, prepare a valid candidate and a live database
   with a recognizable marker.
2. Use the approved validation fault-injection method to force replacement
   failure.
3. Attempt restore.

Expected result:

- Restore reports failure.
- The rollback snapshot is restored.
- The recognizable live marker remains.
- The prior anchor is republished.
- DATABASE_RESTORE_FAILED records the restore ID, change-control ID,
  failure, and rollback result.

### P3-E06 Official export is integrity-gated

1. As an authorized QA or Administrator user, export the audit trail and a
   completed batch record.
2. Open both files and compare record counts and selected values against the
   authoritative application view.
3. Review REPORT_EXPORTED.
4. Repeat against an isolated database with a failed audit verification.

Expected result:

- Exports contain only authoritative records and open successfully.
- Export events identify report target, output path, actor, session, and time.
- Export is blocked when audit integrity verification fails.
- An unaudited report file is not retained after audit evidence fails.

## F. Review, Exception, And Retention Controls

### P3-F01 Audit review acknowledgement

1. As an authorized audit reviewer, select a contiguous audit record range.
2. Enter a review reason and acknowledge the range.
3. Query or view the acknowledgement record.

Expected result:

- The acknowledgement stores first and last audit IDs, reviewer, UTC time,
  reason, and the verified chain tail hash.
- AUDIT_REVIEW_ACKNOWLEDGED is present and transaction-coupled.
- A range containing a gap or an invalid chain cannot be acknowledged.

### P3-F02 Exception escalation

1. Select an audit record range with an unexpected or unexplained result.
2. Escalate it with a specific exception reason.
3. View open exceptions.

Expected result:

- An open exception stores range, actor, UTC time, reason, and status.
- AUDIT_EXCEPTION_ESCALATED is present with failure result.
- The exception is visible to an authorized reviewer.

### P3-F03 Retention policy versioning

1. As Administrator, set a positive retention period with an approved reason.
2. Set a second policy with a new reason.
3. Inspect both policy versions.

Expected result:

- Each policy has a unique version, retention days, approver, UTC time, and
  reason.
- Only the newest policy is active; prior policy remains as superseded evidence.
- Non-administrator users cannot set a retention policy.

### P3-F04 Physical pruning is blocked

1. As Administrator, attempt to prune audit records before a stated date.
2. Provide the required reason.
3. Query the audit record count before and after.

Expected result:

- The operation returns a blocked result.
- No audit row is deleted and the count is unchanged.
- AUDIT_PRUNE_BLOCKED records the attempted cutoff, actor, session, reason,
  and denied result.

## G. Deployment ACL Verification

Execute this section only on the final deployment candidate or a disposable
installation with the same service account model.

### P3-G01 Apply the protected-artifact ACL procedure

1. Confirm the final data directory and dedicated non-interactive service
   account through change control.
2. Confirm audit_signing.key and audit_anchor.json exist.
3. From elevated PowerShell, run:

       .\deployment\protect_audit_artifacts.ps1 -DataDirectory "<data-directory>" -ServiceAccount "<service-account>"

4. Capture the command output and ACL listing for both files.

Expected result:

- Inherited ACLs are removed.
- SYSTEM and Administrators have full control.
- The application service account has Modify access.
- Operators have no direct write access.
- No unrelated files are changed.

### P3-G02 Verify service operation after ACL protection

1. Start the application under the dedicated service account.
2. Perform a normal login and one audited operation.
3. Verify the audit chain and external anchor.
4. As an operator or interactive non-service account, attempt to modify the
   key and anchor directly.

Expected result:

- The service can append audit events and update the anchor.
- The operator cannot modify or delete either protected file.
- Chain verification remains PASS.

## H. Required Audit Event Coverage

Use the audit viewer filters and exported audit report to confirm evidence for
the following event groups generated during this checklist:

- LOGIN, LOGIN_FAILED, ACCOUNT_LOCKED, LOGOUT, SCREEN_LOCKED, and
  SCREEN_UNLOCKED.
- USER_CREATED, USER_DEACTIVATED, USER_REACTIVATED, and PASSWORD_RESET.
- DEVICE_REGISTERED, DEVICE_APPROVED, DEVICE_DEACTIVATED, DEVICE_REPLACED,
  and BATCH_DEVICE_ASSIGNED.
- Configuration and recipe version creation and approval.
- Batch start, stop, recovery, reconciliation, review, release, and closure.
- Scan creation, duplicate/input rejection, and authorization denial.
- SETTINGS_CHANGED, REPORT_EXPORTED, and BACKUP_CREATED.
- DATABASE_RESTORE_COMMITTED and DATABASE_RESTORE_FAILED.
- AUDIT_REVIEW_ACKNOWLEDGED, AUDIT_EXCEPTION_ESCALATED, and
  AUDIT_PRUNE_BLOCKED.
- Integrity, clock-anomaly, device, hardware, and application lifecycle events
  applicable to the executed test paths.

For each required group, record at least one exact event ID or mark the group
not executed with a deviation reference.

## Evidence Package

Attach the following to the completed protocol:

1. Test record and account/role matrix.
2. Screenshots of successful and rejected operations.
3. Exported audit trail and batch report.
4. Exported or queried structured fields for representative events.
5. Chain verification results before and after each tamper test.
6. Backup file, checkpoint sidecar, verification result, and restore evidence.
7. Before/after row counts for rollback and blocked-pruning tests.
8. Exception and retention policy records.
9. ACL command output and file security descriptors.
10. Deviations, corrective actions, retest results, and QA approval.

## Sign-Off

| Role | Name | Signature | Date (UTC) | Result |
|---|---|---|---|---|
| Tester |  |  |  |  |
| Independent reviewer |  |  |  |  |
| System owner |  |  |  |  |
| QA approver |  |  |  |  |

Overall result: PASS / FAIL / PASS WITH APPROVED DEVIATIONS

Release decision:

______________________________________________________________________________
