# cfr21/__init__.py
# 21 CFR Part 11 compliance package for Pharma Code Datalogger.
#
# Modules:
#   db.py              — SQLite setup, all table definitions, migrations
#   user_manager.py    — Users, roles, account lifecycle, password hashing
#   password_policy.py — Expiry, complexity, lockout, first-login enforcement
#   session_manager.py — Login/logout, active session, session timeout
#   audit_trail.py     — Immutable WHO/WHEN/WHAT/WHY append-only log
#   record_integrity.py— SHA-256 checksums + manifest for log files
#   report_export.py   — PDF export for audit trail and batch records
