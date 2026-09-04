"""Transactional authoritative production-record service for new scans."""

import json
import threading
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from cfr21.authorization import (
    AuthorizationError,
    SessionContext,
    authorize_session,
)
from cfr21.audit_trail import _compute_record_hash
from cfr21.db import get_conn
from cfr21.user_manager import User, get_workstation

STATE_ACTIVE = "active"
STATE_STOPPED = "stopped"
STATE_RECONCILIATION_PENDING = "reconciliation_pending"
STATE_RECONCILED = "reconciled"
STATE_REVIEWED = "reviewed"
STATE_RELEASED = "released"
STATE_CLOSED = "closed"
_WRITE_LOCK = threading.RLock()

# Recovery is deliberately distinct from reconciliation. Evidence may be
# completed without immediately returning a batch to data acquisition.
_TRANSITION_PERMISSIONS = {
    ("draft", "configured"): "start_logging",
    ("configured", STATE_ACTIVE): "start_logging",
    (STATE_RECONCILED, STATE_ACTIVE): "recover_batches",
    (STATE_ACTIVE, STATE_STOPPED): "stop_logging",
    (STATE_RECONCILIATION_PENDING, STATE_RECONCILED): "reconcile_batches",
    (STATE_STOPPED, STATE_REVIEWED): "review_batches",
    (STATE_RECONCILED, STATE_REVIEWED): "review_batches",
    (STATE_REVIEWED, STATE_RELEASED): "release_batches",
    (STATE_RELEASED, STATE_CLOSED): "close_batch",
}


class RegulatedRecordError(RuntimeError):
    """A regulated record could not be committed safely."""


class StaleBatchStateError(RegulatedRecordError):
    """The batch changed after the caller read its expected version."""


class DeviceAuthorizationError(RegulatedRecordError):
    """Live acquisition used a device that is no longer valid for its batch."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _actor_name(actor: Optional[User]) -> str:
    if actor is None or not actor.username:
        raise RegulatedRecordError("An authenticated user is required.")
    return actor.username


def _require_actor(actor: Optional[User], permission: str, session_id: str) -> User:
    """Reject forged/stale subjects and unauthorised backend write attempts."""
    context = SessionContext.from_user(actor, session_id)
    try:
        return authorize_session(context, permission)
    except AuthorizationError as exc:
        raise RegulatedRecordError("Protected operation denied.") from exc


def _snapshot(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"), default=str)


def _append_audit(conn, actor: User, action: str, detail: str, session_id: str) -> None:
    """Insert the audit record inside the same transaction as the business record."""
    timestamp = _utc_now()
    username = _actor_name(actor)
    workstation = get_workstation()
    last = conn.execute("SELECT record_hash FROM audit_trail ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = last["record_hash"] if last else None
    record_hash = _compute_record_hash(prev_hash, timestamp, username, actor.role,
                                       action, detail, None, workstation, session_id)
    conn.execute("""
        INSERT INTO audit_trail
            (timestamp, username, role, action, detail, reason, workstation,
             session_id, prev_hash, record_hash)
        VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
    """, (timestamp, username, actor.role, action, detail, workstation,
          session_id, prev_hash, record_hash))


class RegulatedRecordService:
    """Authoritative write boundary for batches and immutable scan records."""

    def transition_batch(self, actor: User, batch_id: str, new_state: str,
                         expected_version: int, session_id: str = "",
                         reason: str = "") -> int:
        """Apply one authorised lifecycle transition with optimistic locking.

        A caller must provide the version it observed. Competing transition
        attempts therefore have one winner and one clear stale-state failure.
        """
        if expected_version < 1:
            raise RegulatedRecordError("A positive expected batch version is required.")
        conn = get_conn()
        try:
            observed = conn.execute("""
                SELECT state FROM regulated_batches WHERE id = ?
            """, (batch_id,)).fetchone()
        finally:
            conn.close()
        if observed is None:
            raise RegulatedRecordError("Authoritative batch was not found.")
        permission = _TRANSITION_PERMISSIONS.get((observed["state"], new_state))
        if permission is None:
            raise RegulatedRecordError(
                f"Batch transition '{observed['state']}' to '{new_state}' is not permitted.")
        # Validate the session before taking the batch write lock. Authorisation
        # refreshes session activity in SQLite, so doing it while holding the
        # batch transaction would self-deadlock on a separate connection.
        actor = _require_actor(actor, permission, session_id)
        with _WRITE_LOCK:
            conn = get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                batch = conn.execute("""
                    SELECT external_batch_id, state, version FROM regulated_batches WHERE id = ?
                """, (batch_id,)).fetchone()
                if batch is None:
                    raise RegulatedRecordError("Authoritative batch was not found.")
                self._transition_locked(conn, actor, batch_id, batch, new_state,
                                        expected_version, session_id, reason)
                conn.commit()
                return expected_version + 1
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @staticmethod
    def _transition_locked(conn, actor: User, batch_id: str, batch: Any,
                           new_state: str, expected_version: int,
                           session_id: str, reason: str = "") -> None:
        if batch["version"] != expected_version:
            raise StaleBatchStateError("Batch state is stale; reload before retrying.")
        if new_state == STATE_STOPPED:
            cursor = conn.execute("""
                UPDATE regulated_batches
                SET state = ?, version = version + 1, stopped_at = ?, stopped_by = ?
                WHERE id = ? AND state = ? AND version = ?
            """, (new_state, _utc_now(), actor.username, batch_id,
                  batch["state"], expected_version))
        else:
            cursor = conn.execute("""
                UPDATE regulated_batches
                SET state = ?, version = version + 1
                WHERE id = ? AND state = ? AND version = ?
            """, (new_state, batch_id, batch["state"], expected_version))
        if cursor.rowcount != 1:
            raise StaleBatchStateError("Batch changed concurrently; reload before retrying.")
        detail = (
            f"Authoritative batch '{batch['external_batch_id']}' transitioned "
            f"from '{batch['state']}' to '{new_state}'; version={expected_version + 1}."
        )
        if reason.strip():
            detail += f" Reason: {reason.strip()}."
        _append_audit(conn, actor, "AUTHORITATIVE_BATCH_TRANSITION", detail, session_id)

    def start_or_resume_batch(self, actor: User, external_batch_id: str,
                              operator_id: str, product_name: str,
                              configuration: Any, session_id: str = "") -> str:
        actor = _require_actor(actor, "start_logging", session_id)
        actor_name = actor.username
        batch_name = external_batch_id.strip()
        if not batch_name or not operator_id.strip():
            raise RegulatedRecordError("Batch ID and operator identity are required.")
        config_json = _snapshot(configuration)
        with _WRITE_LOCK:
            conn = get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("""
                    SELECT id, operator_id, product_name, configuration_json
                    FROM regulated_batches WHERE external_batch_id = ?
                      AND state IN (?, ?)
                    ORDER BY started_at DESC LIMIT 1
                """, (batch_name, STATE_ACTIVE, "reconciliation_pending")).fetchone()
                if row:
                    raise RegulatedRecordError(
                        "An interrupted batch exists and must be reconciled by an authorized user before resuming.")
                batch_id = str(uuid.uuid4())
                now = _utc_now()
                config_version_id = self._get_or_create_configuration(conn, actor, config_json)
                conn.execute("""
                    INSERT INTO regulated_batches
                        (id, external_batch_id, operator_id, product_name, state,
                         configuration_json, configuration_version_id, created_at, created_by, started_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (batch_id, batch_name, operator_id, product_name, STATE_ACTIVE,
                      config_json, config_version_id, now, actor_name, now))
                _append_audit(conn, actor, "AUTHORITATIVE_BATCH_STARTED",
                              f"Authoritative batch '{batch_name}' created: id={batch_id}; "
                              f"operator='{operator_id}'; product='{product_name}'.", session_id)
                conn.commit()
                return batch_id
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def get_batch_status(self, batch_id: str) -> dict:
        """Return lifecycle state and version for a UI/service transition request."""
        conn = get_conn()
        try:
            row = conn.execute("""
                SELECT id, external_batch_id, state, version
                FROM regulated_batches WHERE id = ?
            """, (batch_id,)).fetchone()
            if row is None:
                raise RegulatedRecordError("Authoritative batch was not found.")
            return dict(row)
        finally:
            conn.close()

    def record_scan(self, actor: User, batch_id: str, device_id: int,
                    raw_data: str, master_data: str, status: str,
                    operator_id: str, product_name: str,
                    session_id: str = "", device_source: str = "",
                    delivery_id: str = "") -> tuple[str, int, str]:
        actor = _require_actor(actor, "start_logging", session_id)
        actor_name = actor.username
        if status not in ("PASS", "FAIL") or device_id <= 0:
            raise RegulatedRecordError("Invalid scan status or device ID.")
        with _WRITE_LOCK:
            conn = get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                batch = conn.execute("""
                    SELECT external_batch_id, state, operator_id, product_name,
                           recipe_version_id
                    FROM regulated_batches WHERE id = ?
                """, (batch_id,)).fetchone()
                if batch is None or batch["state"] != STATE_ACTIVE:
                    raise RegulatedRecordError("Scans may only be recorded in an active batch.")
                if batch["operator_id"] != operator_id or batch["product_name"] != product_name:
                    raise RegulatedRecordError("Scan metadata does not match the active batch.")
                if delivery_id:
                    previous = conn.execute("""SELECT id, sequence_no, recorded_at FROM scan_records
                        WHERE batch_id = ? AND device_id = ? AND delivery_id = ?""",
                        (batch_id, device_id, delivery_id)).fetchone()
                    if previous:
                        conn.commit()
                        return previous["id"], previous["sequence_no"], previous["recorded_at"]
                sequence_no = conn.execute("""
                    SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM scan_records
                    WHERE batch_id = ? AND device_id = ?
                """, (batch_id, device_id)).fetchone()[0]
                recipe_version_id = batch["recipe_version_id"]
                if recipe_version_id:
                    recipe = conn.execute("""
                        SELECT id, approval_status FROM recipe_versions WHERE id = ?
                    """, (recipe_version_id,)).fetchone()
                    if recipe is None or recipe["approval_status"] != "approved":
                        raise RegulatedRecordError(
                            "Scan rejected: the batch recipe version is not approved.")
                else:
                    # Compatibility batches predate controlled setup. New
                    # production batches always supply recipe_version_id.
                    recipe_version_id = self._get_or_create_recipe(conn, actor, master_data)
                device_registry_id = self._require_assigned_device(
                    conn, batch_id, device_id,
                    device_source or f"device-{device_id}")
                scan_id, timestamp = str(uuid.uuid4()), _utc_now()
                conn.execute("""
                    INSERT INTO scan_records
                        (id, batch_id, device_id, sequence_no, recorded_at, raw_data,
                         master_data, status, operator_id, product_name, created_by, session_id,
                         recipe_version_id, device_registry_id, delivery_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (scan_id, batch_id, device_id, sequence_no, timestamp, raw_data,
                      master_data, status, operator_id, product_name, actor_name, session_id,
                      recipe_version_id, device_registry_id, delivery_id or None))
                _append_audit(conn, actor, "SCAN_RECORDED",
                              f"Scan id={scan_id}; batch='{batch['external_batch_id']}'; "
                              f"device={device_id}; sequence={sequence_no}; status={status}; "
                              f"raw={raw_data!r}; master={master_data!r}.", session_id)
                conn.commit()
                return scan_id, sequence_no, timestamp
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def stop_batch(self, actor: User, batch_id: str, session_id: str = "") -> None:
        status = self.get_batch_status(batch_id)
        if status["state"] == STATE_STOPPED:
            return
        self.transition_batch(actor, batch_id, STATE_STOPPED, status["version"], session_id)

    def detect_interrupted_batches(self) -> list[dict]:
        """Mark batches left active at a prior process termination as pending.

        This intentionally does not trust a CSV/WAL.  Reconciliation evidence is
        calculated solely from immutable ``scan_records`` after a user logs in.
        """
        with _WRITE_LOCK:
            conn = get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute("SELECT id, external_batch_id FROM regulated_batches WHERE state = ?",
                                    (STATE_ACTIVE,)).fetchall()
                now = _utc_now()
                for row in rows:
                    conn.execute("""UPDATE regulated_batches
                        SET state = ?, version = version + 1
                        WHERE id = ? AND state = ?""",
                                 (STATE_RECONCILIATION_PENDING, row["id"], STATE_ACTIVE))
                    conn.execute("""INSERT OR IGNORE INTO batch_reconciliations
                        (id, batch_id, detected_at, detected_by, status, device_summary_json)
                        VALUES (?, ?, ?, 'system-startup', 'pending', '{}')""",
                                 (str(uuid.uuid4()), row["id"], now))
                conn.commit()
                return [{"batch_id": r["id"], "external_batch_id": r["external_batch_id"]} for r in rows]
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def get_pending_reconciliations(self) -> list[dict]:
        conn = get_conn()
        try:
            rows = conn.execute("""SELECT b.id, b.external_batch_id, b.operator_id, b.product_name,
                    r.detected_at, r.status
                FROM regulated_batches b JOIN batch_reconciliations r ON r.batch_id = b.id
                WHERE b.state = 'reconciliation_pending' AND r.status = 'pending'
                ORDER BY r.detected_at""").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def reconcile_and_resume_batch(self, actor: User, external_batch_id: str,
                                   reason: str, session_id: str = "") -> tuple[str, dict]:
        """Compatibility recovery flow: reconcile evidence, then explicitly resume."""
        if not reason or not reason.strip():
            raise RegulatedRecordError("A recovery reason is required.")
        actor = _require_actor(actor, "reconcile_batches", session_id)
        # Re-check the distinct recovery authority before taking the batch lock.
        actor = _require_actor(actor, "recover_batches", session_id)
        with _WRITE_LOCK:
            conn = get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                batch = conn.execute("SELECT * FROM regulated_batches WHERE external_batch_id = ? AND state = 'reconciliation_pending'",
                                     (external_batch_id.strip(),)).fetchone()
                if batch is None:
                    raise RegulatedRecordError("No interrupted batch is pending reconciliation.")
                counts = conn.execute("""SELECT device_id,
                    SUM(status = 'PASS') AS pass_count, SUM(status = 'FAIL') AS fail_count,
                    COALESCE(MAX(sequence_no), 0) AS last_sequence,
                    COUNT(*) AS record_count
                    FROM scan_records WHERE batch_id = ? GROUP BY device_id""", (batch["id"],)).fetchall()
                summary = {str(r["device_id"]): {"pass": r["pass_count"], "fail": r["fail_count"],
                           "last_sequence": r["last_sequence"], "record_count": r["record_count"]}
                           for r in counts}
                # A gap signals an invalid sequence history; do not reopen it.
                for value in summary.values():
                    if value["last_sequence"] != value["record_count"]:
                        raise RegulatedRecordError("Sequence gap detected; batch cannot be resumed.")
                assignments = conn.execute("""
                    SELECT d.id, d.device_number, d.source_identifier
                    FROM batch_device_assignments a
                    JOIN devices d ON d.id = a.device_registry_id
                    WHERE a.batch_id = ?
                """, (batch["id"],)).fetchall()
                if batch["recipe_version_id"] and not assignments:
                    raise RegulatedRecordError(
                        "Controlled batch has no device assignments for reconciliation.")
                assigned_ids = {row["id"] for row in assignments}
                scanned_ids = {row[0] for row in conn.execute("""
                    SELECT DISTINCT device_registry_id FROM scan_records WHERE batch_id = ?
                """, (batch["id"],)).fetchall() if row[0]}
                if scanned_ids - assigned_ids:
                    raise RegulatedRecordError(
                        "Reconciliation found scan evidence from an unassigned device.")
                # A zero-count assigned device is still explicit reconciliation
                # evidence; it must not disappear merely because it captured no reads.
                for assignment in assignments:
                    summary.setdefault(str(assignment["device_number"]), {
                        "pass": 0,
                        "fail": 0,
                        "last_sequence": 0,
                        "record_count": 0,
                        "source": assignment["source_identifier"],
                    })
                conn.execute("""UPDATE batch_reconciliations SET reconciled_at = ?, reconciled_by = ?,
                    recovery_reason = ?, status = 'completed', device_summary_json = ? WHERE batch_id = ?""",
                             (_utc_now(), actor.username, reason.strip(), json.dumps(summary, sort_keys=True), batch["id"]))
                current = conn.execute("""SELECT external_batch_id, state, version
                    FROM regulated_batches WHERE id = ?""", (batch["id"],)).fetchone()
                self._transition_locked(conn, actor, batch["id"], current,
                                        STATE_RECONCILED, current["version"], session_id, reason)
                reconciled = conn.execute("""SELECT external_batch_id, state, version
                    FROM regulated_batches WHERE id = ?""", (batch["id"],)).fetchone()
                self._transition_locked(conn, actor, batch["id"], reconciled,
                                        STATE_ACTIVE, reconciled["version"], session_id, reason)
                conn.commit()
                return batch["id"], summary
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def close_batch(self, actor: User, batch_id: str, session_id: str = "") -> None:
        """Close only a released batch; review and release remain distinct actions."""
        status = self.get_batch_status(batch_id)
        if status["state"] == STATE_CLOSED:
            return
        self.transition_batch(actor, batch_id, STATE_CLOSED, status["version"], session_id)

    def get_batch_scan_counts(self, external_batch_id: str) -> dict[int, tuple[int, int]]:
        conn = get_conn()
        try:
            rows = conn.execute("""
                SELECT s.device_id,
                    SUM(CASE WHEN s.status = 'PASS' THEN 1 ELSE 0 END) AS pass_count,
                    SUM(CASE WHEN s.status = 'FAIL' THEN 1 ELSE 0 END) AS fail_count
                FROM scan_records s JOIN regulated_batches b ON b.id = s.batch_id
                WHERE b.external_batch_id = ? GROUP BY s.device_id
            """, (external_batch_id,)).fetchall()
            return {r["device_id"]: (r["pass_count"], r["fail_count"]) for r in rows}
        finally:
            conn.close()

    def get_batch_record(self, external_batch_id: str, device_id: int) -> tuple[dict, list[dict]]:
        """Return only authoritative metadata and scans for reporting/export."""
        conn = get_conn()
        try:
            batch = conn.execute("""
                SELECT * FROM regulated_batches WHERE external_batch_id = ?
                ORDER BY started_at DESC LIMIT 1
            """, (external_batch_id,)).fetchone()
            if batch is None:
                raise RegulatedRecordError("No authoritative batch record exists.")
            scans = conn.execute("""
                SELECT sequence_no, recorded_at, raw_data, master_data, status,
                       operator_id, product_name, device_id, id
                FROM scan_records WHERE batch_id = ? AND device_id = ?
                ORDER BY sequence_no ASC
            """, (batch["id"], device_id)).fetchall()
            return dict(batch), [dict(scan) for scan in scans]
        finally:
            conn.close()

    @staticmethod
    def _get_or_create_configuration(conn, actor: User, snapshot_json: str) -> str:
        row = conn.execute("SELECT id FROM configuration_versions WHERE snapshot_json = ?", (snapshot_json,)).fetchone()
        if row:
            return row["id"]
        value_id = str(uuid.uuid4())
        conn.execute("INSERT INTO configuration_versions (id, snapshot_json, created_at, created_by) VALUES (?, ?, ?, ?)",
                     (value_id, snapshot_json, _utc_now(), actor.username))
        return value_id

    @staticmethod
    def _get_or_create_recipe(conn, actor: User, master_data: str) -> str:
        row = conn.execute("SELECT id FROM recipe_versions WHERE master_data = ?", (master_data,)).fetchone()
        if row:
            return row["id"]
        value_id = str(uuid.uuid4())
        conn.execute("INSERT INTO recipe_versions (id, master_data, created_at, created_by) VALUES (?, ?, ?, ?)",
                     (value_id, master_data, _utc_now(), actor.username))
        return value_id

    @staticmethod
    def _require_assigned_device(conn, batch_id: str, number: int, source: str) -> str:
        """Resolve only a pre-approved immutable identity assigned to this batch."""
        row = conn.execute("""
            SELECT d.id
            FROM devices d
            JOIN batch_device_assignments a ON a.device_registry_id = d.id
            WHERE a.batch_id = ?
              AND d.device_number = ?
              AND d.source_identifier = ?
              AND d.approval_status = 'approved'
              AND d.enabled = 1
        """, (batch_id, number, source)).fetchone()
        if row is None:
            raise DeviceAuthorizationError(
                "Scan rejected: device is unknown, unapproved, disabled, or not assigned to this batch.")
        return row["id"]

    @staticmethod
    def _get_or_create_legacy_device(conn, actor: User, number: int, source: str) -> str:
        """Attach imported historical evidence without authorizing live acquisition.

        Legacy imports are classified separately and never pass through
        ``_require_assigned_device``. Their registry identity remains pending,
        so it cannot be reused by a current production scan without review.
        """
        row = conn.execute("""
            SELECT id FROM devices WHERE device_number = ? AND source_identifier = ?
        """, (number, source)).fetchone()
        if row:
            return row["id"]
        value_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO devices (
                id, device_number, source_identifier, display_name, created_at,
                created_by, approval_status, enabled
            ) VALUES (?, ?, ?, 'Legacy import device', ?, ?, 'pending', 0)
        """, (value_id, number, source, _utc_now(), actor.username))
        return value_id
