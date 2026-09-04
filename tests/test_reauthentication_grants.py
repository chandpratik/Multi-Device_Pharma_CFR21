"""Negative-path tests for session-bound single-use reauthentication grants."""

from datetime import datetime, timedelta, timezone

import cfr21.db as db
from cfr21.reauthentication_service import (
    ReauthenticationError,
    consume_grant,
    issue_grant,
)


def _grant(admin_user: object, target: str = "device:test") -> str:
    return issue_grant(
        admin_user, "s-1", "AdminTest@123", "manage_devices", target)


def test_grant_is_single_use(admin_user):
    grant_id = _grant(admin_user)
    consume_grant(admin_user, "s-1", grant_id, "manage_devices", "device:test")
    try:
        consume_grant(admin_user, "s-1", grant_id, "manage_devices", "device:test")
    except ReauthenticationError:
        pass
    else:
        raise AssertionError("A consumed reauthentication grant was replayed")


def test_grant_is_bound_to_its_target(admin_user):
    grant_id = _grant(admin_user, "device:expected")
    try:
        consume_grant(admin_user, "s-1", grant_id, "manage_devices", "device:other")
    except ReauthenticationError:
        pass
    else:
        raise AssertionError("A grant was accepted for a different target")


def test_expired_grant_is_rejected(admin_user):
    grant_id = _grant(admin_user)
    with db.get_conn_ctx() as conn:
        conn.execute("UPDATE reauthentication_grants SET expires_at = ? WHERE id = ?", (
            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), grant_id))
    try:
        consume_grant(admin_user, "s-1", grant_id, "manage_devices", "device:test")
    except ReauthenticationError:
        pass
    else:
        raise AssertionError("An expired reauthentication grant was accepted")
