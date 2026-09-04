# Part 2 — Backend Security and Workflow Enforcement

## Objective

The GUI is an interface, not a security boundary. Every protected backend
operation must validate authenticated identity, session, authority, device, and
workflow state before performing the operation.

## Required controls

### Backend authorization

Enforce permission checks for logging, camera/PLC connection, teaching,
master-code changes, settings, user administration, backups/restores, exports,
reconciliation, review, and release.

### Session enforcement

Reject protected actions when no session exists, account is inactive/locked,
session is expired/locked, or database identity differs from session identity.

### Batch workflow

Enforce transitions such as:

`Draft → Configured → Active → Stopped → Reconciliation Pending → Reconciled → Reviewed → Released/Closed`

Scans occur only in Active. Closed/released batches cannot be changed.

### Device authorization

Verify device serial/identity and approved assignment in the backend before it
can generate regulated records.

### Recipe/configuration control

Create immutable versions, preserve old/new values, require authority/reason,
and prevent uncontrolled change during active production.

### Administrator safeguards

Audit privileged actions, require reauthentication/reason where applicable,
prevent impersonation, and consider dual authorization for high-risk actions.

## Definition of done

Direct calls cannot bypass authority or workflow controls; locked/inactive/stale
users are rejected; device approval is backend-verified; and automated tests
demonstrate rejection of unauthorised and invalid-state operations.
