# Part 8 — Credential and Administrator Controls

## Objective

Protect account credentials and ensure privileged users cannot impersonate,
silently alter, or erase regulated records and controls.

## Implementation sequence

1. Define unique individual-account policy; prohibit shared operational/admin accounts.
2. Replace plaintext bootstrap password files/console output with controlled
   activation: one-time token, restricted delivery, expiry, and forced enrollment.
3. Apply configurable minimum length, complexity, history, expiry where justified,
   failed-attempt lockout, reset, and compromise procedures.
4. Store password hashes with an approved adaptive algorithm and parameters;
   support controlled parameter upgrades.
5. Separate system administration, user administration, quality approval, and
   operational roles. Use least privilege.
6. Validate privileged actors against current database state; never trust a caller-
   constructed user object or GUI state.
7. Require reauthentication and reason for password resets, role changes, account
   activation/deactivation, restore, retention disposition, and security changes.
8. Prevent impersonation. If support access is necessary, use named support accounts,
   explicit authorization, time limits, visible indication, and complete auditing.
9. Prevent the last required administrator from being removed accidentally and
   prevent unsafe self-approval/self-deactivation combinations.
10. Define periodic account review, dormant-account disabling, termination handling,
   credential incident response, and audit review.

## Verification

- Test unknown, inactive, locked, expired, stale, and duplicate accounts.
- Test brute force through login, unlock, reauthentication, and signature screens.
- Attempt forged user objects and direct backend privileged calls.
- Confirm all administrator changes preserve actor, target, old/new values, reason,
  timestamp, session, and result.
- Confirm bootstrap credentials cannot be recovered after activation.

## Definition of done

Every action is attributable to one individual, credentials are securely managed,
and administrator powers are constrained, visible, and independently auditable.
