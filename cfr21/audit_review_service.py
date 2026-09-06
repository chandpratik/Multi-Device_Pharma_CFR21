"""Backend controls for audit review evidence, exceptions, and retention."""

import uuid
from datetime import datetime, timezone

import cfr21.audit_trail as audit
from cfr21.authorization import AuthorizationError, SessionContext, authorize_session
from cfr21.db import get_conn_ctx
from cfr21.user_manager import User


class AuditReviewError(RuntimeError):
    """An audit review or retention control could not be committed."""


def _authorize(actor: User, session_id: str, permission: str, target: str) -> User:
    try:
        return authorize_session(
            SessionContext.from_user(actor, session_id), permission, target)
    except AuthorizationError as exc:
        raise AuditReviewError("Protected audit control denied.") from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _validate_range(first_audit_id: int, last_audit_id: int) -> None:
    if first_audit_id < 1 or last_audit_id < first_audit_id:
        raise AuditReviewError("A valid audit record range is required.")


def acknowledge_range(actor: User, session_id: str, first_audit_id: int,
                      last_audit_id: int, reason: str) -> str:
    """Record who reviewed an intact audit-record range and why."""
    actor = _authorize(actor, session_id, "view_audit_trail", "audit-review")
    reason = (reason or "").strip()
    _validate_range(first_audit_id, last_audit_id)
    if not reason:
        raise AuditReviewError("A review reason is required.")
    ok, message, _ = audit.verify_chain()
    if not ok:
        raise AuditReviewError(f"Audit review blocked by integrity failure: {message}")

    review_id = str(uuid.uuid4())
    with get_conn_ctx() as conn:
        rows = conn.execute("""
            SELECT id, record_hash FROM audit_trail
            WHERE id BETWEEN ? AND ? ORDER BY id
        """, (first_audit_id, last_audit_id)).fetchall()
        if len(rows) != last_audit_id - first_audit_id + 1:
            raise AuditReviewError("The requested audit range contains a gap.")
        tail_hash = rows[-1]["record_hash"] or ""
        conn.execute("""
            INSERT INTO audit_review_acknowledgements (
                id, first_audit_id, last_audit_id, reviewed_by, reviewed_at,
                review_reason, chain_tail_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (review_id, first_audit_id, last_audit_id, actor.username,
              _utc_now(), reason, tail_hash))
        audit.append_event_in_transaction(
            conn, actor, audit.ACTION_AUDIT_REVIEW_ACKNOWLEDGED,
            f"Audit records {first_audit_id}-{last_audit_id} reviewed.",
            session_id, reason, target_type="audit_range",
            target_id=f"{first_audit_id}-{last_audit_id}",
            new_value={"review_id": review_id, "chain_tail_hash": tail_hash},
        )
    return review_id


def escalate_exception(actor: User, session_id: str, first_audit_id: int,
                       last_audit_id: int, reason: str) -> str:
    """Open a durable exception against a reviewed audit-record range."""
    actor = _authorize(actor, session_id, "view_audit_trail", "audit-exception")
    reason = (reason or "").strip()
    _validate_range(first_audit_id, last_audit_id)
    if not reason:
        raise AuditReviewError("An exception reason is required.")

    exception_id = str(uuid.uuid4())
    with get_conn_ctx() as conn:
        count = conn.execute("""
            SELECT COUNT(*) FROM audit_trail
            WHERE id BETWEEN ? AND ?
        """, (first_audit_id, last_audit_id)).fetchone()[0]
        if count != last_audit_id - first_audit_id + 1:
            raise AuditReviewError("The requested audit range contains a gap.")
        conn.execute("""
            INSERT INTO audit_review_exceptions (
                id, first_audit_id, last_audit_id, raised_by, raised_at,
                exception_reason, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'open')
        """, (exception_id, first_audit_id, last_audit_id, actor.username,
              _utc_now(), reason))
        audit.append_event_in_transaction(
            conn, actor, audit.ACTION_AUDIT_EXCEPTION_ESCALATED,
            f"Audit exception opened for records {first_audit_id}-{last_audit_id}.",
            session_id, reason, target_type="audit_range",
            target_id=f"{first_audit_id}-{last_audit_id}",
            new_value={"exception_id": exception_id, "status": "open"},
            result="failure",
        )
    return exception_id


def set_retention_policy(actor: User, session_id: str, retention_days: int,
                         reason: str) -> int:
    """Create the next versioned retention policy; historical policies remain."""
    actor = _authorize(actor, session_id, "manage_audit_retention",
                       "audit-retention")
    reason = (reason or "").strip()
    if retention_days < 1 or not reason:
        raise AuditReviewError("A positive retention period and reason are required.")
    now = _utc_now()
    with get_conn_ctx() as conn:
        current = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM audit_retention_policies"
        ).fetchone()[0]
        version = current + 1
        conn.execute("UPDATE audit_retention_policies SET status = 'superseded'")
        conn.execute("""
            INSERT INTO audit_retention_policies (
                id, version, retention_days, reason, approved_by, approved_at,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, 'active')
        """, (str(uuid.uuid4()), version, retention_days, reason,
              actor.username, now))
        audit.append_event_in_transaction(
            conn, actor, "AUDIT_RETENTION_POLICY_SET",
            f"Audit retention policy version {version} set to {retention_days} days.",
            session_id, reason, target_type="audit_retention",
            target_id=str(version), target_version=version,
            new_value={"retention_days": retention_days, "status": "active"},
        )
    return version


def prune_audit_trail(actor: User, session_id: str, before: str,
                      reason: str) -> tuple[bool, str]:
    """Reject physical audit pruning while recording the attempted action."""
    actor = _authorize(actor, session_id, "manage_audit_retention",
                       "audit-retention-prune")
    reason = (reason or "").strip()
    if not reason:
        raise AuditReviewError("A pruning reason is required.")
    with get_conn_ctx() as conn:
        audit.append_event_in_transaction(
            conn, actor, audit.ACTION_AUDIT_PRUNE_BLOCKED,
            f"Attempted audit pruning before '{before}' was blocked.",
            session_id, reason, target_type="audit_retention",
            target_id=before, result="denied",
        )
    return False, "Audit trail pruning is blocked; archive controls are not approved."


def list_open_exceptions(actor: User, session_id: str) -> list[dict]:
    """Return open exception evidence to an authorized audit reviewer."""
    _authorize(actor, session_id, "view_audit_trail", "audit-exception")
    with get_conn_ctx() as conn:
        rows = conn.execute("""
            SELECT * FROM audit_review_exceptions
            WHERE status = 'open' ORDER BY raised_at, id
        """).fetchall()
    return [dict(row) for row in rows]
