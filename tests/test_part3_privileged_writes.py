"""Transaction coupling for privileged Part 3 service boundaries."""

import pytest

import cfr21.audit_trail as audit
import cfr21.db as db
from cfr21.audit_review_service import (
    AuditReviewError,
    acknowledge_range,
    escalate_exception,
    list_open_exceptions,
    prune_audit_trail,
    set_retention_policy,
)
from cfr21.version_control_service import VersionControlError, VersionControlService


def test_configuration_creation_rolls_back_when_audit_fails(admin_user, monkeypatch):
    def fail(*_args, **_kwargs):
        raise audit.AuditWriteError("forced audit failure")

    monkeypatch.setattr(audit.AuditWriter, "append_in_transaction", fail)
    with pytest.raises(audit.AuditWriteError):
        VersionControlService().create_configuration(
            admin_user, "s-1", {"speed": 12}, "validated change")

    with db.get_conn_ctx() as conn:
        assert conn.execute("""
            SELECT COUNT(*) FROM configuration_versions
            WHERE snapshot_json = ?
        """, ('{"speed":12}',)).fetchone()[0] == 0


def test_configuration_creation_records_structured_target(admin_user):
    version_id = VersionControlService().create_configuration(
        admin_user, "s-1", {"speed": 15}, "validated change")

    with db.get_conn_ctx() as conn:
        row = conn.execute("""
            SELECT action, target_type, target_id, target_version, reason,
                   new_value_json
            FROM audit_trail
            WHERE target_id = ?
        """, (version_id,)).fetchone()
    assert row["action"] == "CONFIGURATION_VERSION_CREATED"
    assert row["target_type"] == "configuration_versions"
    assert row["target_version"] == 1
    assert row["reason"] == "validated change"
    assert '"approval_status":"pending"' in row["new_value_json"]


def test_audit_review_acknowledgement_captures_range_evidence(admin_user):
    audit.log(admin_user, "REVIEW_TARGET", "target", session_id="s-1")
    with db.get_conn_ctx() as conn:
        target = conn.execute("""
            SELECT id FROM audit_trail WHERE action = 'REVIEW_TARGET'
        """).fetchone()["id"]

    review_id = acknowledge_range(
        admin_user, "s-1", target, target, "QA review completed")

    with db.get_conn_ctx() as conn:
        row = conn.execute("""
            SELECT id, first_audit_id, last_audit_id, reviewed_by, review_reason
            FROM audit_review_acknowledgements WHERE id = ?
        """, (review_id,)).fetchone()
    assert row["first_audit_id"] == target
    assert row["last_audit_id"] == target
    assert row["reviewed_by"] == admin_user.username
    assert row["review_reason"] == "QA review completed"


def test_audit_exception_is_open_and_pruning_is_blocked(admin_user):
    audit.log(admin_user, "EXCEPTION_TARGET", "target", session_id="s-1")
    with db.get_conn_ctx() as conn:
        target = conn.execute("""
            SELECT id FROM audit_trail WHERE action = 'EXCEPTION_TARGET'
        """).fetchone()["id"]

    exception_id = escalate_exception(
        admin_user, "s-1", target, target, "Unexpected value requires QA disposition")
    assert any(
        row["id"] == exception_id
        for row in list_open_exceptions(admin_user, "s-1")
    )

    ok, message = prune_audit_trail(
        admin_user, "s-1", "2026-01-01T00:00:00Z", "retention job test")
    assert not ok
    assert "blocked" in message.lower()
    assert audit.get_records(action_filter=audit.ACTION_AUDIT_PRUNE_BLOCKED)


def test_retention_policy_is_versioned_and_admin_only(admin_user, operator_user):
    version = set_retention_policy(
        admin_user, "s-1", 2555, "approved retention schedule")
    assert version == 1
    with db.get_conn_ctx() as conn:
        row = conn.execute("""
            SELECT version, retention_days, approved_by, status
            FROM audit_retention_policies WHERE version = ?
        """, (version,)).fetchone()
    assert row["retention_days"] == 2555
    assert row["approved_by"] == admin_user.username
    assert row["status"] == "active"

    with pytest.raises(AuditReviewError):
        set_retention_policy(operator_user, "s-12", 30, "not authorized")
