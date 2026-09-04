"""Single-use reauthentication grants for high-risk backend operations."""

import uuid
from datetime import datetime, timedelta, timezone

from cfr21.authorization import SessionContext, authorize_session
from cfr21.db import get_conn_ctx
from cfr21.user_manager import User, authenticate


class ReauthenticationError(RuntimeError):
    """A reauthentication grant is invalid, expired, or already consumed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def issue_grant(actor: User, session_id: str, password: str, action: str,
                target: str, lifetime_seconds: int = 120) -> str:
    """Verify the active user again and issue a narrowly scoped proof."""
    actor = authorize_session(SessionContext.from_user(actor, session_id), action, target)
    if not password or not action or not target or lifetime_seconds < 1:
        raise ReauthenticationError("Action, target, password, and positive lifetime are required.")
    result = authenticate(actor.username, password)
    if not result.success or result.user is None or result.user.id != actor.id:
        raise ReauthenticationError("Reauthentication failed.")
    grant_id = str(uuid.uuid4())
    issued = _now()
    with get_conn_ctx() as conn:
        conn.execute("""
            INSERT INTO reauthentication_grants (
                id, session_id, user_id, action, target, issued_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (grant_id, session_id, actor.id, action, target,
              issued.isoformat(timespec="seconds"),
              (issued + timedelta(seconds=lifetime_seconds)).isoformat(timespec="seconds")))
    return grant_id


def consume_grant(actor: User, session_id: str, grant_id: str, action: str,
                  target: str) -> None:
    """Consume exactly one matching grant after validating current session state."""
    actor = authorize_session(SessionContext.from_user(actor, session_id), action, target)
    with get_conn_ctx() as conn:
        updated = conn.execute("""
            UPDATE reauthentication_grants
            SET consumed_at = ?, consumed_by_action = ?
            WHERE id = ? AND session_id = ? AND user_id = ? AND action = ? AND target = ?
              AND consumed_at IS NULL AND expires_at > ?
        """, (_now().isoformat(timespec="seconds"), action, grant_id, session_id,
              actor.id, action, target, _now().isoformat(timespec="seconds"))).rowcount
        if updated != 1:
            raise ReauthenticationError("Reauthentication grant is invalid, expired, or already used.")
