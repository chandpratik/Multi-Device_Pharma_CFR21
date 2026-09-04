# cfr21/audit_trail.py
# Immutable audit trail — 21 CFR Part 11 §11.10(e).
#
# Every significant action in the application is recorded here.
# Records are INSERT-only — never updated or deleted.
#
# Each record captures:
#   WHO       — username + role of the person who acted
#   WHEN      — UTC timestamp with microsecond precision
#   WHAT      — action code + human-readable detail
#   WHY       — reason entered by the operator (for sensitive actions)
#   WHERE     — workstation hostname
#   SESSION   — session_id linking all actions in one login session
#
# ── Action codes (WHAT) ───────────────────────────────────────────────────────
#   Use the ACTION_* constants below — never raw strings.
#   This makes querying and filtering reliable.
#
# ── Thread safety ─────────────────────────────────────────────────────────────
#   log() is called from scan threads (datalogger) and the GUI thread.
#   SQLite in WAL mode handles concurrent writes safely.
#   get_conn_ctx() opens a fresh connection per call — no shared handle.
# ─────────────────────────────────────────────────────────────────────────────

import hashlib
import logging
from datetime import datetime
from typing import Optional

from cfr21.db import get_conn_ctx
from cfr21.user_manager import User, get_workstation

audit_log = logging.getLogger("pharma.cfr21.audit")

# ── Action code constants ─────────────────────────────────────────────────────
# Keep these short — they are stored verbatim and used for filtering.

# Authentication
ACTION_LOGIN              = "LOGIN"
ACTION_LOGOUT             = "LOGOUT"
ACTION_LOGIN_FAILED       = "LOGIN_FAILED"
ACTION_ACCOUNT_LOCKED     = "ACCOUNT_LOCKED"
ACTION_PASSWORD_CHANGED   = "PASSWORD_CHANGED"
ACTION_PASSWORD_RESET     = "PASSWORD_RESET"       # admin reset
ACTION_SESSION_TIMEOUT    = "SESSION_TIMEOUT"
ACTION_SCREEN_LOCKED      = "SCREEN_LOCKED"      # inactivity lock — logging continues
ACTION_SCREEN_UNLOCKED    = "SCREEN_UNLOCKED"    # user re-authenticated to unlock

# User management
ACTION_USER_CREATED       = "USER_CREATED"
ACTION_USER_DEACTIVATED   = "USER_DEACTIVATED"
ACTION_USER_REACTIVATED   = "USER_REACTIVATED"
# Note: ACTION_ROLE_CHANGED removed — role changes are handled via
# deactivate + create new account. Add back if in-place role editing is added.

# Batch / logging lifecycle
ACTION_BATCH_STARTED      = "BATCH_STARTED"
ACTION_BATCH_STOPPED      = "BATCH_STOPPED"
# Note: ACTION_BATCH_PAUSED and ACTION_BATCH_RESUMED removed —
# this app does not have a pause state. Add back if pause is implemented.

# Master code
ACTION_MASTER_SET         = "MASTER_CODE_SET"
ACTION_MASTER_CLEARED     = "MASTER_CODE_CLEARED"

# Camera / PLC
ACTION_CAMERA_CONNECTED   = "CAMERA_CONNECTED"
ACTION_CAMERA_DISCONNECTED = "CAMERA_DISCONNECTED"
ACTION_PLC_CONNECTED      = "PLC_CONNECTED"
ACTION_PLC_DISCONNECTED   = "PLC_DISCONNECTED"
ACTION_CAMERA_LOST        = "CAMERA_LOST"          # unexpected disconnect

# Alarms
ACTION_CONSEC_FAIL_ALARM  = "CONSEC_FAIL_ALARM"

# Settings
ACTION_SETTINGS_CHANGED   = "SETTINGS_CHANGED"
ACTION_SESSION_SETUP      = "SESSION_SETUP"
ACTION_COMPANY_CHANGED    = "COMPANY_SETTINGS_CHANGED"


# Reports / export
ACTION_REPORT_EXPORTED    = "REPORT_EXPORTED"
ACTION_AUDIT_VIEWED       = "AUDIT_VIEWED"

# Integrity / anomaly
ACTION_CLOCK_ANOMALY      = "CLOCK_ANOMALY_DETECTED"
ACTION_WAL_WRITE_FAILED   = "WAL_WRITE_FAILED"
ACTION_PLC_WRITE_FAILED   = "PLC_WRITE_FAILED"
ACTION_AUDIT_VERIFIED     = "AUDIT_CHAIN_VERIFIED"
ACTION_ORPHAN_WAL_SEALED  = "ORPHANED_WAL_SEALED"
ACTION_BACKUP_CREATED     = "BACKUP_CREATED"
ACTION_DEVICE_QUARANTINED = "DEVICE_QUARANTINED"

# System
ACTION_APP_STARTED        = "APP_STARTED"
ACTION_APP_CLOSED         = "APP_CLOSED"
ACTION_CRASH_DETECTED     = "CRASH_DETECTED"


# ── Core log function ─────────────────────────────────────────────────────────

def _compute_record_hash(prev_hash: str, timestamp: str, username: str,
                         role: str, action: str, detail: str,
                         reason: Optional[str], workstation: str,
                         session_id: str) -> str:
    """
    SHA-256 over the previous record's hash plus every field of this record.
    Chaining means editing or deleting ANY historical row invalidates the
    hash of every row after it — tampering becomes mathematically detectable.
    """
    content = "|".join([
        prev_hash or "GENESIS",
        timestamp, username, role, action, detail,
        reason or "", workstation, session_id,
    ])
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def log(user: Optional[User],
        action: str,
        detail: str,
        session_id: str = "",
        reason: Optional[str] = None) -> bool:
    """
    Append one hash-chained record to the audit trail.

    Each record stores:
      prev_hash   — record_hash of the previous row ("GENESIS" for the first)
      record_hash — sha256(prev_hash + all fields of this row)

    Also performs clock anomaly detection: if this record's timestamp is
    EARLIER than the previous record's, a CLOCK_ANOMALY_DETECTED record is
    written first — catching operators who wind the system clock back.

    Returns True on success, False on failure (failure logged, never raised).
    """
    timestamp   = datetime.now().astimezone().isoformat(timespec="seconds")
    username    = user.username if user else "system"
    role        = user.role     if user else "system"
    workstation = get_workstation()

    try:
        with get_conn_ctx() as conn:
            last = conn.execute(
                "SELECT timestamp, record_hash FROM audit_trail "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()

            prev_hash = last["record_hash"] if last else None

            # ── Clock anomaly detection (Item 8) ─────────────────────────
            if last and timestamp < last["timestamp"]:
                anomaly_detail = (
                    f"System clock moved BACKWARD. Previous record time: "
                    f"{last['timestamp']} — new record time: {timestamp}. "
                    f"Possible clock manipulation. Investigate."
                )
                a_hash = _compute_record_hash(
                    prev_hash, timestamp, "system", "system",
                    ACTION_CLOCK_ANOMALY, anomaly_detail,
                    None, workstation, "")
                conn.execute("""
                    INSERT INTO audit_trail
                        (timestamp, username, role, action, detail,
                         reason, workstation, session_id,
                         prev_hash, record_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (timestamp, "system", "system", ACTION_CLOCK_ANOMALY,
                      anomaly_detail, None, workstation, "",
                      prev_hash, a_hash))
                prev_hash = a_hash
                audit_log.critical("[AUDIT] CLOCK ANOMALY: %s", anomaly_detail)

            # ── Chained insert of the actual record ──────────────────────
            record_hash = _compute_record_hash(
                prev_hash, timestamp, username, role, action,
                detail, reason, workstation, session_id)

            conn.execute("""
                INSERT INTO audit_trail
                    (timestamp, username, role, action,
                     detail, reason, workstation, session_id,
                     prev_hash, record_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, username, role, action,
                  detail, reason, workstation, session_id,
                  prev_hash, record_hash))

        audit_log.info("[AUDIT] %s | %s | %s | %s",
                       username, action, detail, reason or "")
        return True

    except Exception as e:
        audit_log.error("audit_trail.log() failed: %s", e)
        return False


def verify_chain() -> tuple[bool, str, int]:
    """
    Walk the entire audit trail and verify the hash chain.

    Returns (ok, message, records_checked):
      ok = True  → every record's hash verifies; no tampering detected
      ok = False → chain broken; message states the first bad record id

    Records written before schema v3 (prev_hash IS NULL and record_hash
    IS NULL) are skipped — the chain starts at the first hashed record.
    """
    try:
        with get_conn_ctx() as conn:
            rows = conn.execute("""
                SELECT id, timestamp, username, role, action, detail,
                       reason, workstation, session_id,
                       prev_hash, record_hash
                FROM audit_trail ORDER BY id ASC
            """).fetchall()

        checked   = 0
        prev_hash = None
        chain_started = False

        for r in rows:
            if r["record_hash"] is None:
                if chain_started:
                    return (False,
                            f"Record id={r['id']} has no hash but appears "
                            f"AFTER hashed records — possible tampering.",
                            checked)
                continue   # pre-v3 legacy record before chain start

            if not chain_started:
                chain_started = True
                prev_hash = r["prev_hash"]   # accept first hashed record's stated prev

            if r["prev_hash"] != prev_hash:
                return (False,
                        f"Chain BROKEN at record id={r['id']}: prev_hash "
                        f"does not match previous record. A record was "
                        f"modified, deleted, or inserted.",
                        checked)

            expected = _compute_record_hash(
                r["prev_hash"], r["timestamp"], r["username"], r["role"],
                r["action"], r["detail"], r["reason"], r["workstation"],
                r["session_id"])

            if expected != r["record_hash"]:
                return (False,
                        f"Record id={r['id']} FAILS hash verification — "
                        f"its content was modified after writing.",
                        checked)

            prev_hash = r["record_hash"]
            checked  += 1

        return (True,
                f"Audit trail verified — {checked} hashed records intact, "
                f"no tampering detected.",
                checked)

    except Exception as e:
        audit_log.error("verify_chain() failed: %s", e)
        return (False, f"Verification error: {e}", 0)


# ── Query functions ───────────────────────────────────────────────────────────

def get_records(limit: int = 500,
                username_filter: Optional[str] = None,
                action_filter:   Optional[str] = None,
                date_from:       Optional[datetime] = None,
                date_to:         Optional[datetime] = None) -> list[dict]:
    """
    Retrieve audit trail records, most recent first.

    Filters are ANDed together — all provided filters must match.
    Returns a list of plain dicts (column → value) for easy use in GUI/reports.
    """
    try:
        conditions = []
        params     = []

        if username_filter:
            conditions.append("username = ? COLLATE NOCASE")
            params.append(username_filter)

        if action_filter:
            conditions.append("action = ?")
            params.append(action_filter)

        if date_from:
            conditions.append("timestamp >= ?")
            params.append(date_from.astimezone().isoformat(timespec="seconds"))

        if date_to:
            conditions.append("timestamp <= ?")
            params.append(date_to.astimezone().isoformat(timespec="seconds"))

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        with get_conn_ctx() as conn:
            rows = conn.execute(f"""
                SELECT id, timestamp, username, role, action,
                       detail, reason, workstation, session_id
                FROM audit_trail
                {where}
                ORDER BY id DESC
                LIMIT ?
            """, params).fetchall()

        return [dict(r) for r in rows]

    except Exception as e:
        audit_log.error("get_records() DB error: %s", e)
        return []


def get_session_records(session_id: str) -> list[dict]:
    """Fetch all audit records for a specific login session."""
    try:
        with get_conn_ctx() as conn:
            rows = conn.execute("""
                SELECT id, timestamp, username, role, action,
                       detail, reason, workstation, session_id
                FROM audit_trail
                WHERE session_id = ?
                ORDER BY id ASC
            """, (session_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        audit_log.error("get_session_records() DB error: %s", e)
        return []


def get_record_count() -> int:
    """Return total number of audit trail records."""
    try:
        with get_conn_ctx() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM audit_trail"
            ).fetchone()[0]
    except Exception:
        return 0
