"""Transaction coupling for privileged Part 3 service boundaries."""

import pytest

import cfr21.audit_trail as audit
import cfr21.db as db
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
