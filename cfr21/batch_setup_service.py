"""Controlled draft-batch setup using approved versions and device assignments."""

import threading
import uuid

from cfr21.authorization import SessionContext, authorize_session
from cfr21.db import get_conn
from cfr21.regulated_records import RegulatedRecordError, RegulatedRecordService, _append_audit, _utc_now
from cfr21.user_manager import User


class BatchSetupError(RegulatedRecordError):
    """A batch could not satisfy controlled pre-acquisition prerequisites."""


_WRITE_LOCK = threading.RLock()


class BatchSetupService:
    """Create draft batches and advance them only after all prerequisites exist."""

    def __init__(self):
        self._records = RegulatedRecordService()

    def create_draft(self, actor: User, session_id: str, external_batch_id: str,
                     operator_id: str, product_name: str,
                     configuration_version_id: str, recipe_version_id: str) -> str:
        actor = authorize_session(
            SessionContext.from_user(actor, session_id), "start_logging",
            target=f"batch:{external_batch_id}")
        name = external_batch_id.strip()
        if not name or not operator_id.strip() or not configuration_version_id or not recipe_version_id:
            raise BatchSetupError("Batch, operator, approved configuration, and approved recipe are required.")
        with _WRITE_LOCK:
            conn = get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                configuration = conn.execute("""
                    SELECT snapshot_json FROM configuration_versions
                    WHERE id = ? AND approval_status = 'approved'
                """, (configuration_version_id,)).fetchone()
                recipe = conn.execute("""
                    SELECT id FROM recipe_versions WHERE id = ? AND approval_status = 'approved'
                """, (recipe_version_id,)).fetchone()
                if configuration is None or recipe is None:
                    raise BatchSetupError("Batch setup requires approved configuration and recipe versions.")
                duplicate = conn.execute("""
                    SELECT id FROM regulated_batches
                    WHERE external_batch_id = ? AND state NOT IN ('released', 'closed')
                """, (name,)).fetchone()
                if duplicate:
                    raise BatchSetupError("An unfinished batch already uses this batch ID.")
                batch_id = str(uuid.uuid4())
                now = _utc_now()
                conn.execute("""
                    INSERT INTO regulated_batches (
                        id, external_batch_id, operator_id, product_name, state,
                        configuration_json, configuration_version_id, recipe_version_id,
                        created_at, created_by, started_at, version
                    ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, 1)
                """, (batch_id, name, operator_id.strip(), product_name.strip(),
                      configuration["snapshot_json"], configuration_version_id,
                      recipe_version_id, now, actor.username, now))
                _append_audit(conn, actor, "AUTHORITATIVE_BATCH_DRAFT_CREATED",
                              f"Draft batch '{name}' created with configuration='{configuration_version_id}' "
                              f"and recipe='{recipe_version_id}'.", session_id)
                conn.commit()
                return batch_id
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def configure_batch(self, actor: User, session_id: str, batch_id: str,
                        expected_version: int, reason: str = "") -> int:
        """Confirm approved assigned devices before making a batch acquirable."""
        conn = get_conn()
        try:
            assignments = conn.execute("""
                SELECT COUNT(*) FROM batch_device_assignments a
                JOIN devices d ON d.id = a.device_registry_id
                WHERE a.batch_id = ? AND d.approval_status = 'approved' AND d.enabled = 1
            """, (batch_id,)).fetchone()[0]
        finally:
            conn.close()
        if assignments == 0:
            raise BatchSetupError("At least one enabled approved device must be assigned before configuration.")
        return self._records.transition_batch(actor, batch_id, "configured",
                                              expected_version, session_id, reason)

    def activate_batch(self, actor: User, session_id: str, batch_id: str,
                       expected_version: int, reason: str = "") -> int:
        return self._records.transition_batch(actor, batch_id, "active",
                                              expected_version, session_id, reason)


_SERVICE = BatchSetupService()
