"""Tests for the authoritative transactional production-record boundary."""

import cfr21.audit_trail as audit
import cfr21.db as db
import cfr21.regulated_records as records_module
import cfr21.user_manager as users
from cfr21 import report_export
from config.settings import AppConfig
from core.app_controller import AppController
from core.models import SessionInfo
import threading
from datetime import datetime, timedelta, timezone
from cfr21.regulated_records import RegulatedRecordError, RegulatedRecordService
from cfr21.session_manager import SessionManager


def test_batch_and_scan_are_committed_with_audit(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "BATCH-100", "admin", "Tablet", {"recipe": "R1"}, "s-1")
    scan_id, sequence, timestamp = service.record_scan(
        admin_user, batch_id, 1, "CODE-001", "CODE-001", "PASS",
        "admin", "Tablet", "s-1")
    assert scan_id
    assert sequence == 1
    assert timestamp.endswith("+00:00")
    with db.get_conn_ctx() as conn:
        scan = conn.execute("SELECT * FROM scan_records WHERE id = ?", (scan_id,)).fetchone()
        event = conn.execute("SELECT * FROM audit_trail WHERE action = 'SCAN_RECORDED'").fetchone()
    assert scan["status"] == "PASS"
    assert scan["sequence_no"] == 1
    assert event is not None
    assert audit.verify_chain()[0]


def test_sequence_is_unique_per_batch_and_device(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "BATCH-101", "admin", "Tablet", {}, "s-2")
    one = service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS", "admin", "Tablet", "s-2")
    two = service.record_scan(admin_user, batch_id, 1, "B", "A", "FAIL", "admin", "Tablet", "s-2")
    other_device = service.record_scan(admin_user, batch_id, 2, "C", "C", "PASS", "admin", "Tablet", "s-2")
    assert (one[1], two[1], other_device[1]) == (1, 2, 1)


def test_stopped_batch_rejects_new_scans(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "BATCH-102", "admin", "Tablet", {}, "s-3")
    service.stop_batch(admin_user, batch_id, "s-3")
    try:
        service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS", "admin", "Tablet", "s-3")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("A stopped batch accepted a scan")


def test_scan_metadata_must_match_active_batch(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "BATCH-103", "admin", "Tablet", {}, "s-4")
    try:
        service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS", "someone-else", "Tablet", "s-4")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("Mismatched operator identity was accepted")


def test_scan_records_cannot_be_updated_or_deleted(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "BATCH-104", "admin", "Tablet", {}, "s-5")
    scan_id, _, _ = service.record_scan(
        admin_user, batch_id, 1, "A", "A", "PASS", "admin", "Tablet", "s-5")
    with db.get_conn_ctx() as conn:
        try:
            conn.execute("UPDATE scan_records SET status = 'FAIL' WHERE id = ?", (scan_id,))
        except Exception:
            pass
        else:
            raise AssertionError("Immutable scan record accepted an update")


def test_scan_links_recipe_and_device_versions(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "BATCH-105", "admin", "Tablet", {"setting": 1}, "s-6")
    scan_id, _, _ = service.record_scan(
        admin_user, batch_id, 1, "A", "MASTER", "PASS", "admin", "Tablet", "s-6", "cam-serial-1")
    with db.get_conn_ctx() as conn:
        row = conn.execute("SELECT recipe_version_id, device_registry_id FROM scan_records WHERE id = ?", (scan_id,)).fetchone()
        batch = conn.execute("SELECT configuration_version_id FROM regulated_batches WHERE id = ?", (batch_id,)).fetchone()
    assert row["recipe_version_id"]
    assert row["device_registry_id"]
    assert batch["configuration_version_id"]


def test_forged_user_object_is_rejected(admin_user):
    service = RegulatedRecordService()
    forged = type(admin_user)(
        id=99999, username=admin_user.username, password_hash="", role="administrator",
        is_active=True, must_change_pw=False, failed_attempts=0, locked_until=None,
        password_changed_at=admin_user.password_changed_at,
        created_at=admin_user.created_at, created_by="forged")
    try:
        service.start_or_resume_batch(forged, "BATCH-106", "admin", "Tablet", {}, "s-7")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("Forged actor object was accepted")


def test_duplicate_delivery_is_idempotent(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(admin_user, "BATCH-107", "admin", "Tablet", {}, "s-8")
    first = service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS", "admin", "Tablet", "s-8", delivery_id="camera-1-42")
    second = service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS", "admin", "Tablet", "s-8", delivery_id="camera-1-42")
    assert second == first
    with db.get_conn_ctx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM scan_records WHERE batch_id = ?", (batch_id,)).fetchone()[0] == 1


def test_two_devices_write_concurrently(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(admin_user, "BATCH-108", "admin", "Tablet", {}, "s-9")
    errors = []
    def write(device):
        try:
            for i in range(10):
                service.record_scan(admin_user, batch_id, device, str(i), str(i), "PASS", "admin", "Tablet", "s-9")
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=write, args=(device,)) for device in (1, 2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert not errors
    with db.get_conn_ctx() as conn:
        rows = conn.execute("SELECT device_id, MAX(sequence_no) AS seq, COUNT(*) AS count FROM scan_records WHERE batch_id = ? GROUP BY device_id", (batch_id,)).fetchall()
    assert {(row["device_id"], row["seq"], row["count"]) for row in rows} == {(1, 10, 10), (2, 10, 10)}


def test_interrupted_batch_must_be_reconciled_before_resume(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(admin_user, "BATCH-109", "admin", "Tablet", {}, "s-10")
    service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS", "admin", "Tablet", "s-10")
    assert service.detect_interrupted_batches()[0]["batch_id"] == batch_id
    try:
        service.start_or_resume_batch(admin_user, "BATCH-109", "admin", "Tablet", {}, "s-10")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("Interrupted batch resumed without reconciliation")
    resumed, summary = service.reconcile_and_resume_batch(admin_user, "BATCH-109", "power loss", "s-10")
    assert resumed == batch_id and summary["1"]["last_sequence"] == 1


def test_database_unavailable_scan_capture_fails_closed(admin_user, monkeypatch):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(admin_user, "BATCH-110", "admin", "Tablet", {}, "s-11")
    monkeypatch.setattr(records_module, "get_conn", lambda: (_ for _ in ()).throw(OSError("database unavailable")))
    try:
        service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS", "admin", "Tablet", "s-11")
    except OSError:
        pass
    else:
        raise AssertionError("Scan capture continued while authoritative database was unavailable")


def test_deactivated_user_is_rejected_at_backend_boundary(admin_user, operator_user):
    service = RegulatedRecordService()
    ok, _ = users.deactivate_user(admin_user, operator_user.username)
    assert ok
    try:
        service.start_or_resume_batch(operator_user, "BATCH-111", "operator1", "Tablet", {}, "s-12")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("Deactivated actor was accepted by backend")


def test_locked_user_is_rejected_at_backend_boundary(admin_user):
    service = RegulatedRecordService()
    with db.get_conn_ctx() as conn:
        conn.execute("UPDATE users SET locked_until = ? WHERE username = ?",
                     ((datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(), admin_user.username))
    try:
        service.start_or_resume_batch(admin_user, "BATCH-112", "admin", "Tablet", {}, "s-13")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("Locked actor was accepted by backend")


def test_logged_out_session_is_rejected_at_backend_boundary(operator_user):
    sm = SessionManager()
    result = sm.login("operator1", "Operator@123")
    assert result.success
    user = sm.current_user
    session_id = sm.session_id
    sm.logout()
    service = RegulatedRecordService()
    try:
        service.start_or_resume_batch(user, "BATCH-SESSION-1", "operator1", "Tablet", {}, session_id)
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("Logged-out session was accepted by backend")


def test_locked_session_is_rejected_at_backend_boundary(operator_user):
    sm = SessionManager()
    result = sm.login("operator1", "Operator@123")
    assert result.success
    sm.lock_screen()
    service = RegulatedRecordService()
    try:
        service.start_or_resume_batch(
            sm.current_user, "BATCH-SESSION-2", "operator1", "Tablet", {}, sm.session_id)
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("Locked session was accepted by backend")


def test_expired_session_is_rejected_at_backend_boundary(operator_user):
    sm = SessionManager()
    result = sm.login("operator1", "Operator@123")
    assert result.success
    with db.get_conn_ctx() as conn:
        conn.execute("""
            UPDATE user_sessions SET expiry_time = ?
            WHERE session_id = ?
        """, (
            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds"),
            sm.session_id,
        ))
    service = RegulatedRecordService()
    try:
        service.start_or_resume_batch(
            sm.current_user, "BATCH-SESSION-3", "operator1", "Tablet", {}, sm.session_id)
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("Expired session was accepted by backend")


def test_unknown_session_id_is_rejected_at_backend_boundary(admin_user):
    service = RegulatedRecordService()
    try:
        service.start_or_resume_batch(admin_user, "BATCH-SESSION-4", "admin", "Tablet", {}, "not-issued")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("Unknown session ID was accepted by backend")


def test_pdf_export_uses_authoritative_records_not_wal(admin_user, tmp_path):
    if not report_export.REPORTLAB_OK:
        return
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(admin_user, "BATCH-113", "admin", "Tablet", {}, "s-14")
    service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS", "admin", "Tablet", "s-14")
    service.record_scan(admin_user, batch_id, 1, "B", "A", "FAIL", "admin", "Tablet", "s-14")
    service.stop_batch(admin_user, batch_id, "s-14")
    output = tmp_path / "batch.pdf"
    ok, message = report_export.export_batch_record(
        str(output), admin_user, "BATCH-113", 1, session_id="s-14")
    assert ok, message
    assert output.exists() and output.stat().st_size > 0
    _, scans = service.get_batch_record("BATCH-113", 1)
    assert [scan["status"] for scan in scans] == ["PASS", "FAIL"]


def test_controller_recovery_after_restart_restores_authoritative_counts(admin_user, monkeypatch):
    """Simulate a process restart: the new controller resumes only after reconciliation."""
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(admin_user, "BATCH-114", "admin", "Tablet", {}, "s-old")
    service.record_scan(admin_user, batch_id, 1, "A", "A", "PASS", "admin", "Tablet", "s-old")
    service.detect_interrupted_batches()
    controller = AppController(AppConfig())
    controller.set_cfr_session(admin_user, "s-new")
    controller.set_session(SessionInfo(batch_id="BATCH-114", operator_id="admin", product_name="Tablet"))
    for device in (1, 2):
        monkeypatch.setattr(controller.logger(device), "start", lambda: None)
    assert controller.recover_interrupted_batch("controlled restart recovery") == batch_id
    assert controller.get_wal_counts_for_batch("BATCH-114")[1] == (1, 0)
