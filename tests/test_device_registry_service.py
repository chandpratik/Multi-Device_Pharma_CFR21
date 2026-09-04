"""Tests for controlled device identity approval and batch assignment."""

import cfr21.db as db
from cfr21.device_registry_service import DeviceRegistryError, DeviceRegistryService
from cfr21.regulated_records import RegulatedRecordError, RegulatedRecordService
from cfr21.reauthentication_service import issue_grant


def _device_grant(user, session_id: str, device_id: str) -> str:
    return issue_grant(user, session_id, "AdminTest@123", "manage_devices", f"device:{device_id}")


def _draft_batch() -> str:
    batch_id = "device-registry-draft"
    with db.get_conn_ctx() as conn:
        conn.execute("""
            INSERT INTO regulated_batches (
                id, external_batch_id, operator_id, product_name, state,
                configuration_json, created_at, created_by, started_at, version
            ) VALUES (?, 'DEVICE-REGISTRY-BATCH', 'admin', 'Tablet', 'draft',
                      '{}', 'test', 'admin', 'test', 1)
        """, (batch_id,))
    return batch_id


def test_approved_assigned_device_is_required_for_scan(admin_user):
    devices = DeviceRegistryService()
    batch_id = _draft_batch()
    device_id = devices.register_device(
        admin_user, "s-1", 1, "camera-serial-1", "Line 1 camera", "new installation")
    devices.approve_device(admin_user, "s-1", device_id, "qualification accepted",
                           _device_grant(admin_user, "s-1", device_id))
    devices.assign_device(admin_user, "s-1", batch_id, device_id, "batch setup")
    with db.get_conn_ctx() as conn:
        conn.execute("UPDATE regulated_batches SET state = 'active' WHERE id = ?", (batch_id,))

    scan_id, _, _ = RegulatedRecordService().record_scan(
        admin_user, batch_id, 1, "A", "A", "PASS", "admin", "Tablet",
        "s-1", device_source="camera-serial-1")
    assert scan_id


def test_unknown_or_unassigned_device_fails_closed(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "DEVICE-REGISTRY-UNKNOWN", "admin", "Tablet", {}, "s-2")
    try:
        service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS",
                            "admin", "Tablet", "s-2", device_source="unknown-camera")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("An unknown device was accepted by scan capture")


def test_assignment_change_after_acquisition_is_rejected(admin_user):
    devices = DeviceRegistryService()
    batch_id = _draft_batch()
    device_id = devices.register_device(
        admin_user, "s-3", 1, "camera-serial-2", "Line 2 camera", "new installation")
    devices.approve_device(admin_user, "s-3", device_id, "qualification accepted",
                           _device_grant(admin_user, "s-3", device_id))
    with db.get_conn_ctx() as conn:
        conn.execute("UPDATE regulated_batches SET state = 'active' WHERE id = ?", (batch_id,))
    try:
        devices.assign_device(admin_user, "s-3", batch_id, device_id, "late assignment")
    except DeviceRegistryError:
        pass
    else:
        raise AssertionError("An active batch accepted a device assignment change")


def test_disabled_or_unapproved_assigned_device_fails_closed(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "DEVICE-REGISTRY-STATUS", "admin", "Tablet", {}, "s-4")
    with db.get_conn_ctx() as conn:
        conn.execute("UPDATE devices SET enabled = 0 WHERE source_identifier = 'device-1'")
    try:
        service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS",
                            "admin", "Tablet", "s-4")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("A disabled device was accepted by scan capture")

    with db.get_conn_ctx() as conn:
        conn.execute("""
            UPDATE devices SET enabled = 1, approval_status = 'pending'
            WHERE source_identifier = 'device-1'
        """)
    try:
        service.record_scan(admin_user, batch_id, 1, "B", "B", "PASS",
                            "admin", "Tablet", "s-4")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("An unapproved device was accepted by scan capture")
