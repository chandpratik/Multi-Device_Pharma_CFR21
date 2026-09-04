# tests/conftest.py
# Shared pytest fixtures for the CFR21 compliance package tests.
#
# Run from the project root:
#   pip install pytest
#   pytest tests/ -v
#
# Each test gets a FRESH temporary compliance.db — tests never touch the
# real database. The db module's _db_path() is monkeypatched per-test.

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

# Make the project root importable when running `pytest tests/`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cfr21.db as db
import cfr21.user_manager as um
import cfr21.audit_trail as audit


def issue_test_session(user, session_id):
    """Create an issued active backend session for direct service tests."""
    now = datetime.now(timezone.utc)
    with db.get_conn_ctx() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO user_sessions
                (session_id, user_id, username, role_at_login, login_time,
                 last_activity, state, lock_time, expiry_time, workstation,
                 termination_reason)
            VALUES (?, ?, ?, ?, ?, ?, 'active', NULL, ?, 'pytest', NULL)
        """, (
            session_id,
            user.id,
            user.username,
            user.role,
            now.isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"),
            (now + timedelta(minutes=30)).isoformat(timespec="seconds"),
        ))


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    """
    Point cfr21.db at a brand-new temporary database file and initialise it.
    Also silences first_login.txt creation into the temp dir.
    Yields the db path.
    """
    db_file = str(tmp_path / "compliance_test.db")
    monkeypatch.setattr(db, "_db_path", lambda: db_file)

    # _seed_default_admin writes first_login.txt next to the "executable" —
    # redirect anything it writes into tmp_path by patching its file target
    # if the implementation uses a helper; otherwise let it write and ignore.
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        db.initialise()
        # Legacy record tests focus on scan integrity rather than device setup.
        # Provision approved identities/assignments in test setup so those tests
        # continue to exercise their intended record-writing paths.
        from cfr21.regulated_records import RegulatedRecordService

        original_start = RegulatedRecordService.start_or_resume_batch

        def start_with_test_devices(service, *args, **kwargs):
            batch_id = original_start(service, *args, **kwargs)
            with db.get_conn_ctx() as conn:
                for number, source in ((1, "device-1"), (2, "device-2"), (1, "cam-serial-1")):
                    device_id = f"pytest-device-{number}-{source}"
                    conn.execute("""
                        INSERT OR IGNORE INTO devices (
                            id, device_number, source_identifier, display_name,
                            created_at, created_by, approval_status, enabled
                        ) VALUES (?, ?, ?, ?, 'test', 'pytest', 'approved', 1)
                    """, (device_id, number, source, source))
                    conn.execute("""
                        INSERT OR IGNORE INTO batch_device_assignments (
                            id, batch_id, device_registry_id, assigned_at,
                            assigned_by, assignment_reason
                        ) VALUES (?, ?, ?, 'test', 'pytest', 'test fixture')
                    """, (f"pytest-assignment-{batch_id}-{device_id}", batch_id, device_id))
            return batch_id

        monkeypatch.setattr(RegulatedRecordService, "start_or_resume_batch", start_with_test_devices)
        yield db_file
    finally:
        os.chdir(cwd)


@pytest.fixture()
def admin_user(fresh_db):
    """
    Return the seeded admin User object with a KNOWN password by resetting
    the seed password through the DB directly (bypass — test setup only).
    """
    import bcrypt
    pw = "AdminTest@123"
    hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
    with db.get_conn_ctx() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_pw = 0 "
            "WHERE username = 'admin'",
            (hashed,)
        )
    result = um.authenticate("admin", pw)
    assert result.success, f"admin fixture login failed: {result.error_code}"
    for session_id in [
        "s-1", "s-2", "s-3", "s-4", "s-5", "s-6", "s-7",
        "s-8", "s-9", "s-10", "s-11", "s-13", "s-14",
        "s-old", "s-new", "legacy-session",
    ]:
        issue_test_session(result.user, session_id)
    return result.user


@pytest.fixture()
def operator_user(fresh_db, admin_user):
    """Create and return a standard operator account with known password."""
    ok, msg = um.create_user(admin_user, "operator1", "Operator@123",
                             um.ROLE_OPERATOR)
    assert ok, msg
    # Clear must_change_pw so tests can authenticate normally
    with db.get_conn_ctx() as conn:
        conn.execute(
            "UPDATE users SET must_change_pw = 0 WHERE username = 'operator1'")
    result = um.authenticate("operator1", "Operator@123")
    assert result.success
    issue_test_session(result.user, "s-12")
    return result.user
