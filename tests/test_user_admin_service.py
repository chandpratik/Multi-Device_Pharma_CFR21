"""Session-authorized account administration tests."""

import cfr21.db as db
import cfr21.audit_trail as audit
import pytest
import cfr21.user_manager as um
from cfr21.user_admin_service import (
    create_account,
    deactivate_account,
    reactivate_account,
    reset_password,
)
from cfr21.reauthentication_service import issue_grant


def test_authorized_admin_session_can_create_account(admin_user):
    ok, msg = create_account(
        admin_user,
        "s-1",
        "serviceuser1",
        "Service@123",
        um.ROLE_OPERATOR,
    )
    assert ok, msg
    assert um.get_user("serviceuser1") is not None


def test_unknown_admin_session_cannot_create_account(admin_user):
    ok, msg = create_account(
        admin_user,
        "not-issued",
        "blockeduser1",
        "Blocked@123",
        um.ROLE_OPERATOR,
    )
    assert not ok
    assert um.get_user("blockeduser1") is None


def test_operator_session_cannot_administer_accounts(operator_user):
    ok, msg = create_account(
        operator_user,
        "s-12",
        "blockeduser2",
        "Blocked@123",
        um.ROLE_OPERATOR,
    )
    assert not ok
    assert um.get_user("blockeduser2") is None


def test_deactivation_revokes_active_sessions(admin_user, operator_user):
    ok, msg = deactivate_account(admin_user, "s-1", operator_user.username)
    assert ok, msg
    with db.get_conn_ctx() as conn:
        row = conn.execute(
            "SELECT state FROM user_sessions WHERE session_id = 's-12'"
        ).fetchone()
    assert row["state"] == "logged_out"


def test_reactivation_requires_authorized_admin_session(admin_user, operator_user):
    um.deactivate_user(admin_user, operator_user.username)
    ok, msg = reactivate_account(admin_user, "s-1", operator_user.username)
    assert ok, msg
    assert um.get_user(operator_user.username).is_active


def test_password_reset_requires_authorized_admin_session(admin_user, operator_user):
    ok, msg = reset_password(
        admin_user,
        "not-issued",
        operator_user.username,
        "ResetPass@789",
    )
    assert not ok
    grant = issue_grant(
        admin_user, "s-1", "AdminTest@123", "manage_users",
        f"user:{operator_user.username}")
    ok, msg = reset_password(
        admin_user,
        "s-1",
        operator_user.username,
        "ResetPass@789",
        grant,
    )
    assert ok, msg


def test_account_creation_rolls_back_when_audit_fails(admin_user, monkeypatch):
    def fail(*_args, **_kwargs):
        raise audit.AuditWriteError("forced audit failure")

    monkeypatch.setattr(audit.AuditWriter, "append_in_transaction", fail)
    with pytest.raises(audit.AuditWriteError):
        create_account(
            admin_user, "s-1", "auditfailureuser", "Audit@123",
            um.ROLE_OPERATOR)
    assert um.get_user("auditfailureuser") is None
