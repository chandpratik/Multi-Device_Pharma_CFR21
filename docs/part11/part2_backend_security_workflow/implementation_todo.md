# Part 2 Implementation TODO - Backend Security and Workflow

This checklist turns the Part 2 scope into implementable work. Complete the
items in order because later controls depend on the authorization and session
boundary established first.

## 1. Confirm control decisions

- [ ] Approve a role-to-permission matrix for every protected operation:
  camera/PLC connect and disconnect, start/stop logging, teach/clear master,
  settings, user administration, backup/restore, export, reconciliation,
  review, release, and close.
- [ ] Approve the batch states and permitted transitions, including who may
  perform each transition and when a reason or reauthentication is required.
- [ ] Define the authoritative device identity (for example serial number plus
  source identifier), approval status, batch assignment rules, and the process
  for replacing a device.
- [ ] Decide which high-risk actions require a second, distinct authorizer.
- [ ] Record the decisions as controlled requirements and add traceability IDs
  that tests can reference.

## 2. Establish one backend authorization boundary

- [ ] Add a backend authorization service that accepts an authenticated session
  context and a named permission; do not accept a caller-supplied `User` object
  as sufficient proof of identity.
- [ ] Make the service reload the account and role from the database for every
  protected action and reject missing, inactive, locked, expired, or mismatched
  identities.
- [ ] Replace the current non-empty-string session check in
  `cfr21/regulated_records.py` with validation of an issued, active session.
- [ ] Use a single typed authorization error/result so GUI and non-GUI callers
  receive consistent fail-closed behavior without exposing sensitive details.
- [ ] Centralize permission names and validate at startup/test time that every
  protected operation has an entry in the role matrix.
- [ ] Audit authorization denials with actor/session, requested operation,
  target, reason code, workstation, and UTC timestamp without recording secrets.

## 3. Make sessions authoritative and revocable

- [ ] Persist issued sessions with session ID, user ID, login time, last
  activity, state, lock time, expiry time, workstation, and termination reason.
- [ ] Implement session states such as `active`, `locked`, `expired`, and
  `logged_out`; make state changes transactional and audited.
- [ ] Ensure screen lock immediately removes authority for new protected
  actions, while explicitly defining the narrow system identity under which an
  already-running acquisition may continue writing scans.
- [ ] Ensure logout, timeout, account deactivation, role change, and account
  lock revoke the corresponding backend session.
- [ ] On unlock, reauthenticate the same user, re-read account status/role, and
  reactivate the existing session only after successful verification.
- [ ] Add a short-lived reauthentication grant for sensitive operations; bind
  it to the same session, user, operation, and target, and consume it once.
- [ ] Remove or prevent stale identity copies in `AppController`, `Datalogger`,
  and WAL helpers from granting authority after session revocation.

## 4. Protect every backend operation

- [ ] Add backend permission checks to all `AppController` entry points,
  including camera/PLC connection, start/stop/close, recovery, teach/clear
  master, and configuration application.
- [ ] Move settings persistence behind an authorized service; prevent direct
  protected calls to `AppConfig.save()` from bypassing authority checks.
- [ ] Put user creation, activation/deactivation, password reset, and role
  changes behind session validation instead of trusting an administrator-shaped
  `User` object.
- [ ] Put database backup, restore, audit export, batch export, legacy import,
  reconciliation, review, and release behind authorized service methods.
- [ ] Search for and remove GUI-only permission gates as the sole enforcement
  mechanism; retain GUI checks only for usability.
- [ ] Prevent lower-level hardware, logger, and record-writing APIs from being
  called without an authorized controller/service context.
- [ ] Add a protected-operation inventory test so newly added public service
  methods cannot silently omit authorization.

## 5. Implement the batch state machine

- [x] Migrate `regulated_batches.state` from the current limited state set to
  the approved lifecycle (proposed: `draft -> configured -> active -> stopped
  -> reconciliation_pending -> reconciled -> reviewed -> released/closed`).
- [x] Create one transactional transition method that locks/reloads the batch,
  checks the current state, permission, prerequisites, and expected version,
  then records the new state and audit event atomically.
- [ ] Make batch creation produce `draft`; require an approved configuration,
  recipe, operator, and device assignments before transition to `configured`.
- [ ] Allow acquisition to start only from `configured` (or an explicitly
  approved recovery transition) and scans only while the batch is `active`.
- [x] Make stop, reconciliation, review, release, and close distinct operations
  with distinct permissions rather than aliases for `stop_logging`.
- [x] Route interrupted batches to `reconciliation_pending`; block restart,
  review, release, and close until reconciliation is completed.
- [ ] Verify scan count, per-device sequence continuity, duplicate delivery IDs,
  and assigned-device completeness before marking a batch `reconciled`.
- [ ] Make released/closed batches immutable at both service and database level,
  except for separately controlled correction/amendment records.
- [x] Handle two simultaneous transition attempts deterministically so only one
  succeeds and the losing caller receives a stale-state error.

## 6. Enforce device authorization

- [x] Extend the device registry with immutable identity, display name, approval
  status, enabled status, approval/deactivation actor, timestamp, and reason.
- [ ] Add controlled service operations to register, approve, deactivate, and
  replace devices; require the approved permissions and audit each action.
- [x] Add versioned batch-to-device assignments and prohibit assignment changes
  after the batch becomes active.
- [x] Remove automatic device creation from the scan path; an unknown or
  unapproved device must fail closed.
- [x] In the same transaction as each scan insert, verify the device is enabled,
  approved, assigned to that batch, and matches the expected serial/source.
- [ ] Stop or quarantine acquisition safely if a device becomes unauthorized or
  its identity changes during a batch, and create an actionable audit event.

## 7. Control recipes and configuration

- [ ] Replace implicit get-or-create snapshots with explicit immutable recipe
  and configuration version services.
- [ ] Store version number, prior version, old/new values, change reason,
  creator, creation time, approval status, approver, approval time, and effective
  time as applicable.
- [ ] Require appropriate authority and a reason to create or change a version;
  require reauthentication/second authorization where the approved matrix says
  so.
- [ ] Link the exact approved recipe and configuration version to a batch before
  it can become `configured`.
- [ ] Reject edits, version reassignment, teach, clear-master, and protected
  configuration changes while the affected batch is active.
- [ ] Add database constraints/triggers that prevent in-place update or deletion
  of versions referenced by regulated records.
- [ ] Keep the fuller approval/effective-date lifecycle aligned with Part 6 so
  Part 2 provides enforcement without duplicating the later change-control work.

## 8. Add administrator safeguards

- [ ] Require a valid administrator session, not merely an object whose role
  field says `administrator`, for every privileged account action.
- [ ] Require a reason and recent reauthentication for password reset, role
  change, device approval/deactivation, restore, and other approved high-risk
  operations.
- [ ] Ensure an administrator cannot perform an action as another user or cause
  audit records to name the target user as the actor.
- [ ] Prevent self-approval and require a distinct second identity wherever dual
  authorization is configured.
- [ ] Audit successful and rejected privileged actions with actor, target,
  before/after values or version references, reason, and session ID.

## 9. Integrate the GUI without relying on it

- [ ] Pass only the current session context from the GUI into protected service
  calls; remove propagation of long-lived `User` snapshots.
- [ ] Keep buttons/menu items disabled by permission and workflow state for good
  user experience, but display backend rejection safely if state changes between
  rendering and execution.
- [ ] Refresh the visible role, lock state, batch state, and device approval
  state after every protected operation or rejection.
- [ ] Provide clear screens/actions for configuration, device assignment,
  reconciliation, review, and release according to the approved workflow.
- [ ] Ensure background threads do not reuse a revoked interactive session;
  assign and document a narrowly scoped acquisition identity if continuation
  during screen lock is required.

## 10. Verification and acceptance tests

- [ ] Add a permission matrix test covering every role and every protected
  backend operation, including direct calls that bypass the GUI.
- [ ] Test rejection for no session, forged session ID, user/session mismatch,
  locked screen, expired session, logged-out session, inactive account, account
  lock, stale role, and revoked session.
- [ ] Test that a role or account-status change takes effect on the next backend
  call without restarting the application.
- [ ] Test every allowed and forbidden batch transition, prerequisite, terminal
  state, retry, and concurrent transition.
- [ ] Test that scans are accepted only for an active batch and an approved,
  enabled, correctly identified, batch-assigned device.
- [ ] Test unknown, substituted, disabled, unapproved, and unassigned devices,
  including a status change during acquisition.
- [ ] Test that recipe/configuration changes and teach/clear-master operations
  are rejected during active production and cannot mutate referenced versions.
- [ ] Test backup, restore, export, reconciliation, review, release, settings,
  and user administration by both authorized and unauthorized direct callers.
- [ ] Test reauthentication-grant expiry, replay, wrong action/target, wrong
  user/session, and dual-authorization separation where enabled.
- [ ] Test that rejected operations make no business-data or hardware state
  change and create the required denial evidence.
- [ ] Run the full existing test suite and record results, environment, database
  schema version, build identifier, deviations, and approval.

## Definition of done

- [ ] No protected action can be completed by calling below the GUI without a
  valid session, current authority, valid device, and valid workflow state.
- [ ] Locked, inactive, expired, logged-out, mismatched, and stale-role sessions
  are rejected consistently.
- [ ] Batch transitions, device checks, business writes, and their audit records
  are atomic and fail closed.
- [ ] Released/closed records and referenced recipe/configuration versions cannot
  be altered through application services or direct database writes.
- [ ] Automated negative tests demonstrate all required rejection paths, and
  validation evidence is reviewed and approved under change control.

## Scope boundaries and dependencies

- Part 1 must provide the authoritative regulated-record store and recovery
  foundation used here.
- Part 3 owns complete audit-trail hardening; Part 2 still requires transactionally
  recorded authorization, transition, and privileged-action evidence.
- Part 4 owns electronic-signature manifestation and signed-record linkage.
- Part 6 owns the full recipe/configuration change-control lifecycle.
- Part 7 owns retention and complete backup/restore controls.
- Part 8 owns broader credential/bootstrap and administrator hardening.
- This checklist is an engineering plan, not validation evidence or a claim of
  21 CFR Part 11 compliance.
