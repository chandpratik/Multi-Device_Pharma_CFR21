# Part 2 Manual Test Checklist

## Test Setup

- Prepare two enabled user accounts with the required Administrator, Supervisor,
  Reviewer, and/or Quality permissions. Use separate accounts where the test
  calls for creator and approver separation.
- Have two valid device identities available, including their device number,
  source identifier, and display name.
- Start each test from a known state and retain exported audit-trail evidence
  with the test record.

## Device Control

1. Log in as an Administrator and open **CFR Controls > Device Management**.
2. Register two devices using their device numbers, source identifiers, and
   names. Verify both are pending approval.
3. Approve both devices and supply the requested password reauthentication.
   Verify their status becomes approved.
4. Replace one approved device. Verify the existing device becomes inactive and
   the replacement is pending approval.
5. Approve the replacement, then assign approved devices to a draft or
   configured batch.
6. Attempt to use a pending, disabled, inactive, replaced, or unassigned device
   for a scan. Verify the scan is rejected and the audit trail records the
   device quarantine or denial event.

## Controlled Versions And Batch Setup

1. Create a configuration version and a recipe version as one authorized user.
2. Sign in as a different authorized user and approve each version with
   reauthentication. Verify a creator cannot approve their own version.
3. From the main screen, start a controlled batch setup. Select approved
   configuration and recipe versions and provide a setup reason.
4. Verify logging cannot begin until the batch is configured with approved,
   assigned devices.
5. Start the prepared batch and complete a scan using an assigned, approved
   device.

## Active-Batch Safeguards

1. While a batch is active, attempt to teach a product, clear mappings, and
   apply a production configuration change. Verify each action is blocked.
2. Attempt scanning from an unassigned or invalid device during the active
   batch. Verify acquisition is stopped or denied according to the displayed
   error, and review the audit evidence.
3. Lock or log out during acquisition, then attempt another scan. Verify the
   scan is denied and the event is audited.
4. Disable or revoke the current user account, then attempt an authorized
   action. Verify access is denied from the current session.

## Reconciliation, Review, Release, And Closure

1. Stop the batch and complete reconciliation. Confirm assignments and scan
   evidence are shown for the batch.
2. Attempt release before reconciliation and review. Verify release is blocked.
3. Record the required review reason, then release the batch with a release
   reason and complete closure/sealing.
4. Attempt to edit, rescan, reassign a device, or alter linked versions on the
   released/closed batch. Verify the action is rejected.

## Reauthentication Grant Boundaries

1. Attempt password reset, device approval, device replacement, and version
   approval without reauthentication. Verify each operation is rejected.
2. Reauthenticate for one action and verify the grant cannot be reused for a
   different action or target.
3. Establish a grant in one session, then attempt to use it from another
   session. Verify it is rejected.

## Evidence Review

1. Export the batch report and audit trail as an authorized user. Verify the
   exported files open successfully.
2. Confirm exported evidence includes the batch setup, approved configuration
   and recipe versions, device approval/replacement/assignment, rejected scans,
   reauthentication, review, release, closure, and authorization denials.
3. Attach the exports, expected-versus-actual results, tester identity, date,
   and any deviation record to the validation package.
