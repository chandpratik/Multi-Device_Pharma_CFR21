"""Session-authorized registry and batch assignment service for devices."""

import uuid
from datetime import datetime, timezone

import cfr21.audit_trail as audit
from cfr21.authorization import SessionContext, authorize_session
from cfr21.db import get_conn_ctx
from cfr21.reauthentication_service import ReauthenticationError, consume_grant
from cfr21.user_manager import User


class DeviceRegistryError(RuntimeError):
    """A controlled device registry operation was rejected."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class DeviceRegistryService:
    """Own device identity, approval, and pre-acquisition batch assignment."""

    @staticmethod
    def _authorize(actor: User, session_id: str, permission: str, target: str) -> User:
        return authorize_session(
            SessionContext.from_user(actor, session_id), permission, target=target)

    def list_devices(self, actor: User, session_id: str) -> list[dict]:
        """Return registry status only to a currently authorized administrator."""
        self._authorize(actor, session_id, "manage_devices", "device-registry")
        with get_conn_ctx() as conn:
            rows = conn.execute("""
                SELECT id, device_number, source_identifier, display_name,
                       approval_status, enabled, created_at, created_by,
                       approved_at, approved_by, deactivated_at, deactivated_by
                FROM devices ORDER BY device_number, source_identifier
            """).fetchall()
        return [dict(row) for row in rows]

    def register_device(self, actor: User, session_id: str, device_number: int,
                        source_identifier: str, display_name: str,
                        reason: str) -> str:
        """Register immutable identity in pending state; approval is separate."""
        actor = self._authorize(actor, session_id, "manage_devices", "device:register")
        source = source_identifier.strip()
        if device_number <= 0 or not source or not reason.strip():
            raise DeviceRegistryError("Device number, source identity, and reason are required.")
        with get_conn_ctx() as conn:
            existing = conn.execute("""
                SELECT id FROM devices WHERE device_number = ? AND source_identifier = ?
            """, (device_number, source)).fetchone()
            if existing:
                raise DeviceRegistryError("This immutable device identity is already registered.")
            device_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO devices (
                    id, device_number, source_identifier, display_name, created_at,
                    created_by, approval_status, enabled, approval_reason
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 1, ?)
            """, (device_id, device_number, source, display_name.strip(), _utc_now(),
                  actor.username, reason.strip()))
            audit.append_event_in_transaction(
                conn, actor, "DEVICE_REGISTERED",
                f"Device '{device_id}' registered: number={device_number}; source='{source}'.",
                session_id, reason.strip(), target_type="device", target_id=device_id)
        return device_id

    def approve_device(self, actor: User, session_id: str, device_id: str,
                       reason: str, reauthentication_grant_id: str = "") -> None:
        actor = self._authorize(actor, session_id, "manage_devices", f"device:{device_id}")
        if not reason.strip():
            raise DeviceRegistryError("A device approval reason is required.")
        try:
            consume_grant(actor, session_id, reauthentication_grant_id,
                          "manage_devices", f"device:{device_id}")
        except ReauthenticationError as exc:
            raise DeviceRegistryError("Recent reauthentication is required to approve a device.") from exc
        with get_conn_ctx() as conn:
            updated = conn.execute("""
                UPDATE devices
                SET approval_status = 'approved', approved_at = ?, approved_by = ?,
                    approval_reason = ?, enabled = 1
                WHERE id = ? AND approval_status = 'pending'
            """, (_utc_now(), actor.username, reason.strip(), device_id)).rowcount
            if updated != 1:
                raise DeviceRegistryError("Only a pending registered device can be approved.")
            audit.append_event_in_transaction(
                conn, actor, "DEVICE_APPROVED", f"Device '{device_id}' approved.",
                session_id, reason.strip(), target_type="device", target_id=device_id)

    def deactivate_device(self, actor: User, session_id: str, device_id: str,
                          reason: str, reauthentication_grant_id: str = "") -> None:
        actor = self._authorize(actor, session_id, "manage_devices", f"device:{device_id}")
        if not reason.strip():
            raise DeviceRegistryError("A device deactivation reason is required.")
        try:
            consume_grant(actor, session_id, reauthentication_grant_id,
                          "manage_devices", f"device:{device_id}")
        except ReauthenticationError as exc:
            raise DeviceRegistryError("Recent reauthentication is required to deactivate a device.") from exc
        with get_conn_ctx() as conn:
            updated = conn.execute("""
                UPDATE devices
                SET enabled = 0, deactivated_at = ?, deactivated_by = ?,
                    deactivation_reason = ?
                WHERE id = ? AND enabled = 1
            """, (_utc_now(), actor.username, reason.strip(), device_id)).rowcount
            if updated != 1:
                raise DeviceRegistryError("The device is unknown or already deactivated.")
            audit.append_event_in_transaction(
                conn, actor, "DEVICE_DEACTIVATED",
                f"Device '{device_id}' deactivated.",
                session_id, reason.strip(), target_type="device", target_id=device_id,
                result="success")

    def replace_device(self, actor: User, session_id: str, device_id: str,
                       replacement_number: int, replacement_source: str,
                       replacement_display_name: str, reason: str,
                       reauthentication_grant_id: str = "") -> str:
        """Retire one identity and register its replacement as pending approval."""
        actor = self._authorize(actor, session_id, "manage_devices", f"device:{device_id}")
        if replacement_number <= 0 or not replacement_source.strip() or not reason.strip():
            raise DeviceRegistryError("Replacement identity and reason are required.")
        try:
            consume_grant(actor, session_id, reauthentication_grant_id,
                          "manage_devices", f"device:{device_id}")
        except ReauthenticationError as exc:
            raise DeviceRegistryError("Recent reauthentication is required to replace a device.") from exc
        with get_conn_ctx() as conn:
            old = conn.execute("SELECT id FROM devices WHERE id = ? AND enabled = 1", (device_id,)).fetchone()
            if old is None:
                raise DeviceRegistryError("The device is unknown or already deactivated.")
            duplicate = conn.execute("""
                SELECT id FROM devices WHERE device_number = ? AND source_identifier = ?
            """, (replacement_number, replacement_source.strip())).fetchone()
            if duplicate:
                raise DeviceRegistryError("The replacement identity is already registered.")
            conn.execute("""
                UPDATE devices SET enabled = 0, deactivated_at = ?, deactivated_by = ?,
                    deactivation_reason = ? WHERE id = ?
            """, (_utc_now(), actor.username, f"Replaced: {reason.strip()}", device_id))
            replacement_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO devices (
                    id, device_number, source_identifier, display_name, created_at,
                    created_by, approval_status, enabled, approval_reason
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 1, ?)
            """, (replacement_id, replacement_number, replacement_source.strip(),
                  replacement_display_name.strip(), _utc_now(), actor.username,
                  f"Replacement for {device_id}: {reason.strip()}"))
            audit.append_event_in_transaction(
                conn, actor, "DEVICE_REPLACED",
                f"Device '{device_id}' retired; pending replacement '{replacement_id}' registered.",
                session_id, reason.strip(), target_type="device", target_id=device_id,
                new_value={"replacement_id": replacement_id})
        return replacement_id

    def assign_device(self, actor: User, session_id: str, batch_id: str,
                      device_id: str, reason: str) -> str:
        """Assign an approved device before acquisition; active batches are immutable."""
        actor = self._authorize(actor, session_id, "assign_devices", f"batch:{batch_id}")
        if not reason.strip():
            raise DeviceRegistryError("A batch-device assignment reason is required.")
        with get_conn_ctx() as conn:
            batch = conn.execute("SELECT state FROM regulated_batches WHERE id = ?", (batch_id,)).fetchone()
            if batch is None:
                raise DeviceRegistryError("Authoritative batch was not found.")
            if batch["state"] not in ("draft", "configured"):
                raise DeviceRegistryError("Devices may only be assigned before batch acquisition starts.")
            device = conn.execute("""
                SELECT id FROM devices
                WHERE id = ? AND approval_status = 'approved' AND enabled = 1
            """, (device_id,)).fetchone()
            if device is None:
                raise DeviceRegistryError("Only an enabled approved device may be assigned.")
            assignment_id = str(uuid.uuid4())
            try:
                conn.execute("""
                    INSERT INTO batch_device_assignments (
                        id, batch_id, device_registry_id, assigned_at, assigned_by,
                        assignment_reason
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (assignment_id, batch_id, device_id, _utc_now(), actor.username, reason.strip()))
            except Exception as exc:
                raise DeviceRegistryError("This device is already assigned to the batch.") from exc
            audit.append_event_in_transaction(
                conn, actor, "BATCH_DEVICE_ASSIGNED",
                f"Device '{device_id}' assigned to batch '{batch_id}'.",
                session_id, reason.strip(), target_type="batch", target_id=batch_id,
                new_value={"device_id": device_id, "assignment_id": assignment_id})
        return assignment_id


_SERVICE = DeviceRegistryService()


def list_devices(actor: User, session_id: str) -> list[dict]:
    return _SERVICE.list_devices(actor, session_id)


def register_device(actor: User, session_id: str, device_number: int,
                    source_identifier: str, display_name: str, reason: str) -> str:
    return _SERVICE.register_device(actor, session_id, device_number, source_identifier,
                                    display_name, reason)


def approve_device(actor: User, session_id: str, device_id: str, reason: str,
                   reauthentication_grant_id: str = "") -> None:
    _SERVICE.approve_device(actor, session_id, device_id, reason, reauthentication_grant_id)


def deactivate_device(actor: User, session_id: str, device_id: str, reason: str,
                      reauthentication_grant_id: str = "") -> None:
    _SERVICE.deactivate_device(actor, session_id, device_id, reason, reauthentication_grant_id)


def replace_device(actor: User, session_id: str, device_id: str,
                   replacement_number: int, replacement_source: str,
                   replacement_display_name: str, reason: str,
                   reauthentication_grant_id: str = "") -> str:
    return _SERVICE.replace_device(
        actor, session_id, device_id, replacement_number, replacement_source,
        replacement_display_name, reason, reauthentication_grant_id)


def assign_device(actor: User, session_id: str, batch_id: str, device_id: str,
                  reason: str) -> str:
    return _SERVICE.assign_device(actor, session_id, batch_id, device_id, reason)
