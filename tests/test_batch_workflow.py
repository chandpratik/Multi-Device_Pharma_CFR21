"""Tests for the controlled, versioned authoritative batch lifecycle."""

import cfr21.db as db
from cfr21.regulated_records import (
    RegulatedRecordError,
    RegulatedRecordService,
    StaleBatchStateError,
)


def test_review_release_close_are_distinct_versioned_transitions(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "BATCH-WORKFLOW-1", "admin", "Tablet", {}, "s-1")

    service.stop_batch(admin_user, batch_id, "s-1")
    stopped = service.get_batch_status(batch_id)
    assert (stopped["state"], stopped["version"]) == ("stopped", 2)

    reviewed_version = service.transition_batch(
        admin_user, batch_id, "reviewed", stopped["version"], "s-1", "record review complete")
    released_version = service.transition_batch(
        admin_user, batch_id, "released", reviewed_version, "s-1", "QA release")
    closed_version = service.transition_batch(
        admin_user, batch_id, "closed", released_version, "s-1")

    assert closed_version == 5
    assert service.get_batch_status(batch_id)["state"] == "closed"
    with db.get_conn_ctx() as conn:
        transitions = conn.execute("""
            SELECT COUNT(*) FROM audit_trail
            WHERE action = 'AUTHORITATIVE_BATCH_TRANSITION'
        """).fetchone()[0]
    assert transitions == 4


def test_stale_transition_and_close_before_release_are_rejected(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "BATCH-WORKFLOW-2", "admin", "Tablet", {}, "s-2")
    service.stop_batch(admin_user, batch_id, "s-2")
    stopped = service.get_batch_status(batch_id)

    try:
        service.close_batch(admin_user, batch_id, "s-2")
    except RegulatedRecordError:
        pass
    else:
        raise AssertionError("A stopped batch was closed before review and release")

    service.transition_batch(admin_user, batch_id, "reviewed", stopped["version"], "s-2")
    try:
        service.transition_batch(admin_user, batch_id, "released", stopped["version"], "s-2")
    except StaleBatchStateError:
        pass
    else:
        raise AssertionError("A stale batch transition was accepted")


def test_interrupted_recovery_records_reconciliation_before_resuming(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "BATCH-WORKFLOW-3", "admin", "Tablet", {}, "s-3")
    service.detect_interrupted_batches()
    pending = service.get_batch_status(batch_id)
    assert (pending["state"], pending["version"]) == ("reconciliation_pending", 2)

    service.reconcile_and_resume_batch(admin_user, "BATCH-WORKFLOW-3", "restart review", "s-3")
    resumed = service.get_batch_status(batch_id)
    assert (resumed["state"], resumed["version"]) == ("active", 4)


def test_released_batch_is_immutable_at_database_level(admin_user):
    service = RegulatedRecordService()
    batch_id = service.start_or_resume_batch(
        admin_user, "BATCH-WORKFLOW-IMMUTABLE", "admin", "Tablet", {}, "s-4")
    service.stop_batch(admin_user, batch_id, "s-4")
    version = service.get_batch_status(batch_id)["version"]
    version = service.transition_batch(admin_user, batch_id, "reviewed", version, "s-4")
    service.transition_batch(admin_user, batch_id, "released", version, "s-4")
    with db.get_conn_ctx() as conn:
        try:
            conn.execute("UPDATE regulated_batches SET state = 'closed' WHERE id = ?", (batch_id,))
        except Exception:
            pass
        else:
            raise AssertionError("A released batch accepted a direct database update")
