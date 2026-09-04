# Part 1 test-results record

Date: 2026-09-04  
Environment: project `.venv`, Python 3.9.4, pytest 8.3.2

| Test group | Result |
| --- | --- |
| `tests/test_audit_trail.py` | 9 passed |
| `tests/test_session_and_integrity.py` | 17 passed |
| `tests/test_authentication.py` | 19 passed |
| `tests/test_password_policy.py` | 14 passed |
| `tests/test_regulated_records.py` and `tests/test_legacy_wal_import.py` | 16 passed |
| **Total** | **75 passed** |

The suite was executed in groups because the terminal reporting window is shorter than the complete bcrypt-heavy run. All collected tests passed with no failures. Focused additions cover database-unavailable fail-closed capture, concurrent two-device writes, duplicate-delivery idempotency, controller-level restart/interrupted-batch reconciliation, immutable records, stale/deactivated/locked backend actor rejection, controlled legacy WAL import provenance, and PDF export from authoritative records without a WAL input.

Approval status: pending QA/change-control review. See `PART1_DEPLOYMENT_AND_LIMITATIONS.md` for deployment controls and residual limitations.
