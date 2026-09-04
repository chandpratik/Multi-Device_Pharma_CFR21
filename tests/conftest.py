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

import pytest

# Make the project root importable when running `pytest tests/`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cfr21.db as db
import cfr21.user_manager as um
import cfr21.audit_trail as audit


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
    return result.user
