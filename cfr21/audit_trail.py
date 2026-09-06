"""Structured, append-only audit trail for regulated application events.

The database row is authoritative. AuditWriter serializes writers, creates
the local hash-chain link, and signs each event with a key stored outside the
database. The latest signed event is anchored in a separate file so tail
truncation cannot silently pass verification.
"""

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import secrets
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import cfr21.db as db
from cfr21.db import get_conn_ctx
from cfr21.user_manager import User, get_workstation

audit_log = logging.getLogger("pharma.cfr21.audit")
_AUDIT_WRITE_LOCK = threading.RLock()
_RESULTS = {"success", "failure", "denied", "quarantined", "not_applicable"}

# Authentication
ACTION_LOGIN = "LOGIN"
ACTION_LOGOUT = "LOGOUT"
ACTION_LOGIN_FAILED = "LOGIN_FAILED"
ACTION_ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
ACTION_PASSWORD_CHANGED = "PASSWORD_CHANGED"
ACTION_PASSWORD_RESET = "PASSWORD_RESET"
ACTION_SESSION_TIMEOUT = "SESSION_TIMEOUT"
ACTION_SCREEN_LOCKED = "SCREEN_LOCKED"
ACTION_SCREEN_UNLOCKED = "SCREEN_UNLOCKED"

# User management
ACTION_USER_CREATED = "USER_CREATED"
ACTION_USER_DEACTIVATED = "USER_DEACTIVATED"
ACTION_USER_REACTIVATED = "USER_REACTIVATED"

# Batch / logging lifecycle
ACTION_BATCH_STARTED = "BATCH_STARTED"
ACTION_BATCH_STOPPED = "BATCH_STOPPED"

# Master code
ACTION_MASTER_SET = "MASTER_CODE_SET"
ACTION_MASTER_CLEARED = "MASTER_CODE_CLEARED"

# Camera / PLC
ACTION_CAMERA_CONNECTED = "CAMERA_CONNECTED"
ACTION_CAMERA_DISCONNECTED = "CAMERA_DISCONNECTED"
ACTION_PLC_CONNECTED = "PLC_CONNECTED"
ACTION_PLC_DISCONNECTED = "PLC_DISCONNECTED"
ACTION_CAMERA_LOST = "CAMERA_LOST"

# Alarms / settings / reports
ACTION_CONSEC_FAIL_ALARM = "CONSEC_FAIL_ALARM"
ACTION_SETTINGS_CHANGED = "SETTINGS_CHANGED"
ACTION_SESSION_SETUP = "SESSION_SETUP"
ACTION_COMPANY_CHANGED = "COMPANY_SETTINGS_CHANGED"
ACTION_REPORT_EXPORTED = "REPORT_EXPORTED"
ACTION_AUDIT_VIEWED = "AUDIT_VIEWED"

# Integrity / anomaly
ACTION_CLOCK_ANOMALY = "CLOCK_ANOMALY_DETECTED"
ACTION_WAL_WRITE_FAILED = "WAL_WRITE_FAILED"
ACTION_PLC_WRITE_FAILED = "PLC_WRITE_FAILED"
ACTION_AUDIT_VERIFIED = "AUDIT_CHAIN_VERIFIED"
ACTION_ORPHAN_WAL_SEALED = "ORPHANED_WAL_SEALED"
ACTION_BACKUP_CREATED = "BACKUP_CREATED"
ACTION_DATABASE_RESTORE_COMMITTED = "DATABASE_RESTORE_COMMITTED"
ACTION_DATABASE_RESTORE_FAILED = "DATABASE_RESTORE_FAILED"
ACTION_DEVICE_QUARANTINED = "DEVICE_QUARANTINED"
ACTION_INTEGRITY_FAILURE = "INTEGRITY_FAILURE"

# System
ACTION_APP_STARTED = "APP_STARTED"
ACTION_APP_CLOSED = "APP_CLOSED"
ACTION_CRASH_DETECTED = "CRASH_DETECTED"


class AuditWriteError(RuntimeError):
    """Raised when a required audit event cannot be durably written."""


@dataclass(frozen=True)
class AuditEvent:
    """Complete structured contract for one audit event."""

    action: str
    detail: str
    actor_id: int = 0
    actor_username: str = "system"
    role: str = "system"
    session_id: str = ""
    workstation: str = ""
    target_type: str = ""
    target_id: str = ""
    target_version: Optional[int] = None
    old_value: Any = None
    new_value: Any = None
    reason: Optional[str] = None
    result: str = "success"
    correlation_id: str = ""
    event_id: str = ""
    timestamp: str = ""

    def with_defaults(self) -> "AuditEvent":
        if not self.action or not self.detail:
            raise ValueError("Audit action and readable detail are required")
        if self.result not in _RESULTS:
            raise ValueError(f"Unknown audit result: {self.result}")
        return AuditEvent(
            action=self.action, detail=self.detail,
            actor_id=int(self.actor_id or 0),
            actor_username=self.actor_username or "system",
            role=self.role or "system", session_id=self.session_id or "",
            workstation=self.workstation or get_workstation(),
            target_type=self.target_type or "", target_id=self.target_id or "",
            target_version=self.target_version, old_value=self.old_value,
            new_value=self.new_value, reason=self.reason, result=self.result,
            correlation_id=self.correlation_id or str(uuid.uuid4()),
            event_id=self.event_id or str(uuid.uuid4()),
            timestamp=self.timestamp or _utc_now(),
        )

    def readable(self) -> str:
        target = f" {self.target_type}={self.target_id}" if self.target_type else ""
        reason = f" Reason: {self.reason}." if self.reason else ""
        return f"{self.action}{target}: {self.detail} Result={self.result}.{reason}"


def _utc_now() -> str:
    # astimezone keeps this helper controllable in clock anomaly tests.
    return datetime.now().astimezone().astimezone(timezone.utc).isoformat(timespec="microseconds")


def _json_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _compute_record_hash(prev_hash: Optional[str], timestamp: str,
                         username: str, role: str, action: str,
                         detail: str, reason: Optional[str], workstation: str,
                         session_id: str, event_id: str = "", actor_id: int = 0,
                         target_type: str = "", target_id: str = "",
                         target_version: Optional[int] = None,
                         old_value_json: Optional[str] = None,
                         new_value_json: Optional[str] = None,
                         result: str = "success", correlation_id: str = "") -> str:
    """Hash the complete event while retaining the old positional API."""
    content = "|".join([
        prev_hash or "GENESIS", timestamp, str(actor_id or 0), username, role,
        session_id, workstation, action, detail, target_type, target_id,
        "" if target_version is None else str(target_version),
        old_value_json or "", new_value_json or "", reason or "", result,
        correlation_id, event_id,
    ])
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _compute_legacy_record_hash(prev_hash: Optional[str], timestamp: str,
                                username: str, role: str, action: str,
                                detail: str, reason: Optional[str],
                                workstation: str, session_id: str) -> str:
    """Verify hashes written before schema 15 without rewriting history."""
    content = "|".join([
        prev_hash or "GENESIS", timestamp, username, role, action, detail,
        reason or "", workstation, session_id,
    ])
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _key_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db._db_path())),
                        "audit_signing.key")


def _anchor_path() -> str:
    return _anchor_path_for_db(db._db_path())


def _anchor_path_for_db(database_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(database_path)),
                        "audit_anchor.json")


def _signing_key(create: bool = True) -> Optional[bytes]:
    path = _key_path()
    try:
        if os.path.exists(path):
            with open(path, "rb") as handle:
                value = handle.read()
            if len(value) < 32:
                raise AuditWriteError("Audit signing key is invalid or too short")
            return value
        if not create:
            return None
        key = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        return key
    except AuditWriteError:
        raise
    except Exception as exc:
        raise AuditWriteError(f"Unable to access protected audit signing key: {exc}") from exc


def _signature(event: AuditEvent, record_hash: str, key: bytes) -> str:
    payload = "|".join((event.event_id, record_hash, str(event.actor_id),
                         event.action, event.target_type, event.target_id,
                         event.result, event.correlation_id))
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _anchor_payload(row: dict) -> dict:
    audit_id = row.get("audit_id", row.get("id"))
    return {"audit_id": audit_id, "event_id": row["event_id"],
            "record_hash": row["record_hash"], "signature": row["signature"]}


def _write_anchor(row: dict, anchor_path: Optional[str] = None) -> None:
    """Atomically update the latest-event anchor outside SQLite."""
    payload = _anchor_payload(row)
    path = anchor_path or _anchor_path()
    directory = os.path.dirname(path)
    fd, temp_path = tempfile.mkstemp(prefix="audit_anchor_", suffix=".tmp",
                                     dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception as exc:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise AuditWriteError(f"Unable to update audit anchor: {path}") from exc


class AuditWriter:
    """The one serialized writer for standalone and coupled audit writes."""

    @staticmethod
    def append_in_transaction(conn, event: AuditEvent,
                              publish_anchor: bool = True) -> dict:
        """Append to an already-open transaction; errors propagate."""
        event = event.with_defaults()
        with _AUDIT_WRITE_LOCK:
            key = _signing_key()
            last = conn.execute("""
                SELECT id, timestamp, record_hash FROM audit_trail
                ORDER BY id DESC LIMIT 1
            """).fetchone()
            prev_hash = last["record_hash"] if last else None
            if last and event.timestamp < last["timestamp"]:
                anomaly = AuditEvent(
                    action=ACTION_CLOCK_ANOMALY,
                    detail=(f"System clock moved backward. Previous record time: "
                            f"{last['timestamp']}; new record time: {event.timestamp}."),
                    actor_id=0, actor_username="system", role="system",
                    session_id="", workstation=event.workstation,
                    target_type="system", target_id="clock", result="failure",
                    correlation_id=event.correlation_id, timestamp=event.timestamp,
                )
                anomaly_row = AuditWriter._insert(conn, anomaly, prev_hash, key)
                if publish_anchor:
                    _write_anchor(anomaly_row)
                prev_hash = anomaly_row["record_hash"]
                audit_log.critical("[AUDIT] CLOCK ANOMALY: %s", anomaly.detail)
            row = AuditWriter._insert(conn, event, prev_hash, key)
            # If this fails, the caller's transaction rolls back.
            if publish_anchor:
                _write_anchor(row)
            audit_log.info("[AUDIT] %s | %s | %s", event.actor_username,
                           event.action, event.readable())
            return row

    @staticmethod
    def _insert(conn, event: AuditEvent, prev_hash: Optional[str], key: bytes) -> dict:
        old_json, new_json = _json_value(event.old_value), _json_value(event.new_value)
        record_hash = _compute_record_hash(
            prev_hash, event.timestamp, event.actor_username, event.role,
            event.action, event.detail, event.reason, event.workstation,
            event.session_id, event.event_id, event.actor_id, event.target_type,
            event.target_id, event.target_version, old_json, new_json,
            event.result, event.correlation_id)
        signature = _signature(event, record_hash, key)
        conn.execute("""
            INSERT INTO audit_trail
                (event_id, timestamp, actor_id, username, role, session_id,
                 workstation, action, target_type, target_id, target_version,
                 old_value_json, new_value_json, reason, result, detail,
                 correlation_id, prev_hash, record_hash, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (event.event_id, event.timestamp, event.actor_id,
               event.actor_username, event.role, event.session_id,
               event.workstation, event.action, event.target_type,
               event.target_id, event.target_version, old_json, new_json,
               event.reason, event.result, event.detail, event.correlation_id,
               prev_hash, record_hash, signature))
        return {"id": conn.execute("SELECT last_insert_rowid()").fetchone()[0],
                "event_id": event.event_id, "record_hash": record_hash,
                "signature": signature}


def event_for_user(user: Optional[User], action: str, detail: str,
                   session_id: str = "", reason: Optional[str] = None,
                   **kwargs) -> AuditEvent:
    """Build a structured event for a service-owned transaction boundary."""
    return AuditEvent(
        action=action,
        detail=detail,
        actor_id=user.id if user else 0,
        actor_username=user.username if user else "system",
        role=user.role if user else "system",
        session_id=session_id,
        workstation=get_workstation(),
        reason=reason,
        **kwargs,
    )


def append_event_in_transaction(conn, user: Optional[User], action: str,
                                detail: str, session_id: str = "",
                                reason: Optional[str] = None,
                                **kwargs) -> dict:
    """Append one structured event to a caller-owned transaction."""
    return AuditWriter.append_in_transaction(
        conn,
        event_for_user(user, action, detail, session_id, reason, **kwargs),
    )


def append_event(event: AuditEvent) -> dict:
    """Append one standalone event and fail visibly if it cannot be stored."""
    with _AUDIT_WRITE_LOCK:
        conn = None
        try:
            conn = db.get_conn()
            conn.execute("BEGIN IMMEDIATE")
            row = AuditWriter.append_in_transaction(conn, event)
            conn.commit()
            return row
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            audit_log.critical("Audit write failed; investigate: %s", exc)
            if isinstance(exc, AuditWriteError):
                raise
            raise AuditWriteError(f"Audit event could not be committed: {exc}") from exc
        finally:
            if conn is not None:
                conn.close()


def log(user: Optional[User], action: str, detail: str,
        session_id: str = "", reason: Optional[str] = None, **kwargs) -> bool:
    """Compatibility wrapper; accepts all structured AuditEvent fields."""
    event = event_for_user(user, action, detail, session_id, reason, **kwargs)
    try:
        append_event(event)
        return True
    except Exception as exc:
        # Existing GUI callers use this boolean. Coupled regulated writers
        # call AuditWriter directly and therefore raise/roll back.
        audit_log.error("audit_trail.log() failed: %s", exc)
        return False


def _verify_signature(row: Any, key: bytes) -> bool:
    event = AuditEvent(
        action=row["action"], detail=row["detail"], actor_id=row["actor_id"] or 0,
        actor_username=row["username"], role=row["role"], session_id=row["session_id"],
        workstation=row["workstation"], target_type=row["target_type"] or "",
        target_id=row["target_id"] or "", target_version=row["target_version"],
        reason=row["reason"], result=row["result"] or "success",
        correlation_id=row["correlation_id"] or "", event_id=row["event_id"],
        timestamp=row["timestamp"])
    expected = _signature(event, row["record_hash"], key)
    return bool(row["signature"]) and hmac.compare_digest(expected, row["signature"])


def _fetch_audit_rows(conn) -> list[Any]:
    return conn.execute("""
                SELECT id, event_id, timestamp, actor_id, username, role,
                       action, detail, reason, workstation, session_id,
                       target_type, target_id, target_version, old_value_json,
                       new_value_json, result, correlation_id, prev_hash,
                       record_hash, signature
                FROM audit_trail ORDER BY id ASC
            """).fetchall()


def _verify_rows(rows: list[Any], anchor: Optional[dict],
                 require_anchor: bool) -> tuple[bool, str, int]:
    key = _signing_key(create=False)
    checked, prev_hash, chain_started = 0, None, False
    for row in rows:
        if row["record_hash"] is None:
            if chain_started:
                return False, f"Record id={row['id']} has no hash after hashed records.", checked
            continue
        if not chain_started:
            chain_started, prev_hash = True, row["prev_hash"]
        if row["prev_hash"] != prev_hash:
            return False, f"Chain broken at record id={row['id']}: previous link mismatch.", checked
        if row["event_id"]:
            expected = _compute_record_hash(
                row["prev_hash"], row["timestamp"], row["username"], row["role"],
                row["action"], row["detail"], row["reason"], row["workstation"],
                row["session_id"], row["event_id"], row["actor_id"] or 0,
                row["target_type"] or "", row["target_id"] or "",
                row["target_version"], row["old_value_json"], row["new_value_json"],
                row["result"] or "success", row["correlation_id"] or "")
        else:
            expected = _compute_legacy_record_hash(
                row["prev_hash"], row["timestamp"], row["username"], row["role"],
                row["action"], row["detail"], row["reason"], row["workstation"],
                row["session_id"])
        if expected != row["record_hash"]:
            return False, f"Record id={row['id']} fails hash verification.", checked
        if row["event_id"] and row["signature"]:
            if key is None or not _verify_signature(row, key):
                return False, f"Record id={row['id']} fails protected signature verification.", checked
        prev_hash, checked = row["record_hash"], checked + 1

    if rows and rows[-1]["event_id"]:
        if key is None:
            return False, "Protected audit signing key is unavailable.", checked
        if anchor is None:
            if require_anchor:
                return False, "Audit anchor is unavailable.", checked
            return True, f"Audit trail verified - {checked} hashed records intact.", checked
        last = rows[-1]
        if anchor.get("audit_id") != last["id"]:
            return False, "Audit anchor does not match last event (id).", checked
        for field in ("event_id", "record_hash", "signature"):
            if anchor.get(field) != last[field]:
                return False, f"Audit anchor does not match last event ({field}).", checked
    return True, f"Audit trail verified - {checked} hashed records intact.", checked


def verify_database_chain(database_path: str,
                          anchor: Optional[dict] = None,
                          require_anchor: bool = False) -> tuple[bool, str, int]:
    """Verify a database file without consulting the live database path."""
    try:
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = _fetch_audit_rows(conn)
        finally:
            conn.close()
        return _verify_rows(rows, anchor, require_anchor)
    except Exception as exc:
        audit_log.error("verify_database_chain() failed: %s", exc)
        return False, f"Verification error: {exc}", 0


def latest_anchor_for_database(database_path: str) -> dict:
    """Return the anchor payload for the last structured audit row in a DB."""
    payload = _latest_anchor_for_database(database_path)
    if payload is None:
        raise AuditWriteError("Database has no structured audit tail to anchor")
    return payload


def _latest_anchor_for_database(database_path: str) -> Optional[dict]:
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("""
            SELECT id, event_id, record_hash, signature
            FROM audit_trail ORDER BY id DESC LIMIT 1
        """).fetchone()
    finally:
        conn.close()
    if row is None or not row["event_id"]:
        return None
    return _anchor_payload(dict(row))


def publish_anchor_for_database(database_path: str) -> dict:
    """Publish the external anchor for the supplied database path."""
    payload = latest_anchor_for_database(database_path)
    _write_anchor(payload, _anchor_path_for_db(database_path))
    return payload


def _checkpoint_signature(payload: dict, key: bytes) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, raw.encode("utf-8"), hashlib.sha256).hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_detached_checkpoint(database_path: str) -> str:
    """Write a signed checkpoint sidecar for a detached database backup."""
    database_path = os.path.abspath(database_path)
    ok, message, _ = verify_database_chain(database_path, require_anchor=False)
    if not ok:
        raise AuditWriteError(f"Cannot checkpoint unverifiable database: {message}")
    body = {
        "version": 1,
        "database_file": os.path.basename(database_path),
        "sha256": sha256_file(database_path),
        "tail": _latest_anchor_for_database(database_path),
    }
    key = _signing_key()
    checkpoint = dict(body)
    checkpoint["signature"] = _checkpoint_signature(body, key)
    path = database_path + ".audit_checkpoint.json"
    fd, temp_path = tempfile.mkstemp(prefix="audit_checkpoint_", suffix=".tmp",
                                     dir=os.path.dirname(database_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(checkpoint, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path
    except Exception as exc:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise AuditWriteError(f"Unable to write detached audit checkpoint: {path}") from exc


def verify_detached_checkpoint(database_path: str) -> tuple[bool, str, Optional[dict]]:
    """Verify a backup file against its signed checkpoint sidecar."""
    checkpoint_path = os.path.abspath(database_path) + ".audit_checkpoint.json"
    try:
        with open(checkpoint_path, "r", encoding="utf-8") as handle:
            checkpoint = json.load(handle)
        signature = checkpoint.pop("signature")
        key = _signing_key(create=False)
        if key is None:
            return False, "Protected audit signing key is unavailable.", None
        expected = _checkpoint_signature(checkpoint, key)
        if not hmac.compare_digest(signature, expected):
            return False, "Detached audit checkpoint signature mismatch.", None
        if checkpoint.get("database_file") != os.path.basename(database_path):
            return False, "Detached audit checkpoint database name mismatch.", None
        if checkpoint.get("sha256") != sha256_file(database_path):
            return False, "Detached audit checkpoint database digest mismatch.", None
        tail = checkpoint.get("tail")
        if tail is not None and not isinstance(tail, dict):
            return False, "Detached audit checkpoint tail is invalid.", None
        ok, message, checked = verify_database_chain(
            database_path, tail, require_anchor=tail is not None)
        if not ok:
            return False, message, None
        return True, f"Detached audit checkpoint verified - {checked} hashed records intact.", tail
    except Exception as exc:
        return False, f"Detached audit checkpoint verification error: {exc}", None


def verify_chain() -> tuple[bool, str, int]:
    """Verify the local chain, protected signatures, and external anchor."""
    try:
        with get_conn_ctx() as conn:
            rows = _fetch_audit_rows(conn)
        key = _signing_key(create=False)
        anchor = None
        if rows and rows[-1]["event_id"]:
            if key is None:
                return False, "Protected audit signing key is unavailable.", 0
            try:
                with open(_anchor_path(), "r", encoding="utf-8") as handle:
                    anchor = json.load(handle)
            except Exception as exc:
                return False, f"Audit anchor is unavailable: {exc}", 0
        return _verify_rows(rows, anchor, require_anchor=True)
    except Exception as exc:
        audit_log.error("verify_chain() failed: %s", exc)
        return False, f"Verification error: {exc}", 0


def _select_records(where: str, params: list[Any], limit: int,
                    order: str = "DESC") -> list[dict]:
    with get_conn_ctx() as conn:
        rows = conn.execute(f"""
            SELECT id, event_id, timestamp, actor_id, username, role,
                   action, detail, reason, workstation, session_id,
                   target_type, target_id, target_version, old_value_json,
                   new_value_json, result, correlation_id, record_hash,
                   signature
            FROM audit_trail {where} ORDER BY id {order} LIMIT ?
        """, list(params) + [limit]).fetchall()
    records = [dict(row) for row in rows]
    for record in records:
        record["readable"] = AuditEvent(
            action=record["action"], detail=record["detail"],
            target_type=record.get("target_type") or "",
            target_id=record.get("target_id") or "", reason=record.get("reason"),
            result=record.get("result") or "success").readable()
    return records


def get_records(limit: int = 500, username_filter: Optional[str] = None,
                action_filter: Optional[str] = None,
                date_from: Optional[datetime] = None,
                date_to: Optional[datetime] = None) -> list[dict]:
    try:
        conditions, params = [], []
        if username_filter:
            conditions.append("username = ? COLLATE NOCASE")
            params.append(username_filter)
        if action_filter:
            conditions.append("action = ?")
            params.append(action_filter)
        if date_from:
            conditions.append("timestamp >= ?")
            params.append(date_from.astimezone(timezone.utc).isoformat(timespec="seconds"))
        if date_to:
            conditions.append("timestamp <= ?")
            params.append(date_to.astimezone(timezone.utc).isoformat(timespec="seconds"))
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        return _select_records(where, params, limit)
    except Exception as exc:
        audit_log.error("get_records() DB error: %s", exc)
        return []


def get_session_records(session_id: str) -> list[dict]:
    try:
        return _select_records("WHERE session_id = ?", [session_id], 100000,
                               order="ASC")
    except Exception as exc:
        audit_log.error("get_session_records() DB error: %s", exc)
        return []


def get_record_count() -> int:
    try:
        with get_conn_ctx() as conn:
            return conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]
    except Exception:
        return 0
