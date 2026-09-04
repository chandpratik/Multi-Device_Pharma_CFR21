# Part 4 — Electronic Signatures

## Objective

Implement electronic signatures that identify the signer, show signing date/time
and meaning, and remain permanently linked to the exact record version signed.

## Implementation sequence

1. Identify signature-required actions: recipe approval, configuration approval,
   batch reconciliation, batch review/release, deviation acknowledgement, restore
   approval, and other quality decisions.
2. Add a `record_signatures` table containing signature UUID, signer user ID and
   printed name, UTC timestamp, meaning code/text, target type/ID/version, target
   content hash, session ID, workstation, and signature status.
3. Store permitted signature meanings in controlled configuration, such as
   `reviewed`, `approved`, `released`, and `acknowledged`.
4. Require the current user to re-enter username and password at signing time.
   Route verification through normal lockout and failed-attempt controls.
5. Ensure the signer matches the authenticated session and is authorized for the
   requested meaning and workflow transition.
6. Hash/canonicalize the exact record version before signing and store that hash
   in the signature record.
7. Display signer name, signed date/time, and meaning on-screen and on every human-
   readable signed-record export.
8. Prevent signed content from being updated. Corrections create a superseding
   version, invalidate applicability of prior approval, and require a new signature.
9. Prevent signature copying or linking to another record/version.
10. Audit signature success, failure, cancellation, invalidation, and verification.

## Verification

- Try signing with another username, wrong password, inactive user, locked user,
  expired session, and unauthorized role.
- Copy a signature row to another record and confirm verification fails.
- Change signed content and confirm signature verification fails.
- Confirm signature manifestation appears in UI and PDF exports.
- Confirm password re-entry failures increment lockout counters and are audited.

## Definition of done

Each signature is attributable, manifested, meaning-specific, authenticated,
non-transferable, and cryptographically bound to an immutable record version.
