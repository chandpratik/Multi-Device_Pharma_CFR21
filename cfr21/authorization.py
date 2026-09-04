"""Backend authorization boundary for protected CFR21 operations."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import cfr21.audit_trail as audit
from cfr21.db import get_conn_ctx
from cfr21.permissions import PROTECTED_OPERATIONS
from cfr21.user_manager import User, get_user, get_workstation

SESSION_ACTIVE = "active"
SESSION_LOCKED = "locked"
SESSION_EXPIRED = "expired"
SESSION_LOGGED_OUT = "logged_out"

ACTION_AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"


@dataclass(frozen=True)
class SessionContext:
    """Minimal caller-held session proof for protected backend operations."""

    session_id: str
    user_id: int
    username: str

    @classmethod
    def from_user(cls, user: Optional[User], session_id: str) -> "SessionContext":
        if user is None:
            return cls(session_id=session_id or "", user_id=0, username="")
        return cls(session_id=session_id or "", user_id=user.id, username=user.username)


class AuthorizationError(RuntimeError):
    """Raised when a protected operation is rejected fail-closed."""

    def __init__(self, reason_code: str, message: str = "Protected operation denied."):
        super().__init__(message)
        self.reason_code = reason_code


def authorize_session(context: SessionContext, permission: str,
                      target: str = "") -> User:
    """Validate an issued active session and current role before proceeding."""
    actor: Optional[User] = None
    try:
        if permission not in PROTECTED_OPERATIONS:
            raise AuthorizationError("unknown_permission")
        if not context.session_id or not context.user_id or not context.username:
            raise AuthorizationError("missing_session")

        actor = get_user(context.username)
        if actor is None or actor.id != context.user_id:
            raise AuthorizationError("identity_mismatch")
        if not actor.is_active:
            _revoke_actor_sessions(actor.id, SESSION_LOGGED_OUT, "Account inactive")
            raise AuthorizationError("account_inactive")
        if actor.is_locked():
            _revoke_actor_sessions(actor.id, SESSION_LOCKED, "Account locked")
            raise AuthorizationError("account_locked")

        now = datetime.now(timezone.utc)
        with get_conn_ctx() as conn:
            row = conn.execute("""
                SELECT * FROM user_sessions WHERE session_id = ?
            """, (context.session_id,)).fetchone()

            if row is None:
                raise AuthorizationError("unknown_session")
            if row["user_id"] != actor.id or row["username"].lower() != actor.username.lower():
                raise AuthorizationError("session_identity_mismatch")

            state = row["state"]
            if state != SESSION_ACTIVE:
                raise AuthorizationError(f"session_{state}")

            expiry_time = _parse_dt(row["expiry_time"])
            if expiry_time and now >= expiry_time:
                conn.execute("""
                    UPDATE user_sessions
                    SET state = ?, termination_reason = ?
                    WHERE session_id = ? AND state = ?
                """, (SESSION_EXPIRED, "Session expired", context.session_id, SESSION_ACTIVE))
                raise AuthorizationError("session_expired")

            if not actor.can(permission):
                raise AuthorizationError("permission_denied")

            last_activity = _parse_dt(row["last_activity"])
            if last_activity and expiry_time:
                timeout = expiry_time - last_activity
                next_expiry = now + timeout
            else:
                next_expiry = expiry_time
            conn.execute("""
                UPDATE user_sessions SET last_activity = ?, expiry_time = ?
                WHERE session_id = ? AND state = ?
            """, (
                now.isoformat(timespec="seconds"),
                next_expiry.isoformat(timespec="seconds") if next_expiry else _utc_now(),
                context.session_id,
                SESSION_ACTIVE,
            ))
            return actor

    except AuthorizationError as exc:
        _audit_denial(actor, context, permission, target, exc.reason_code)
        raise


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _revoke_actor_sessions(user_id: int, state: str, reason: str) -> None:
    with get_conn_ctx() as conn:
        conn.execute("""
            UPDATE user_sessions
            SET state = ?, termination_reason = ?
            WHERE user_id = ? AND state = ?
        """, (state, reason, user_id, SESSION_ACTIVE))


def _audit_denial(actor: Optional[User], context: SessionContext,
                  permission: str, target: str, reason_code: str) -> None:
    safe_target = target or ""
    audit.log(
        user=actor,
        action=ACTION_AUTHORIZATION_DENIED,
        detail=(
            f"Authorization denied: operation='{permission}'; "
            f"target='{safe_target}'; reason='{reason_code}'; "
            f"session='{context.session_id or 'none'}'; "
            f"workstation='{get_workstation()}'."
        ),
        session_id=context.session_id or "",
    )
