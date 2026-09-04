"""Direct-call authorization tests for export and backup operations."""

import os

import cfr21.audit_trail as audit
from cfr21.db_backup import run_backup_authorized


def test_manual_backup_requires_issued_admin_session(admin_user, tmp_path):
    ok, message = run_backup_authorized(
        admin_user,
        "not-issued",
        str(tmp_path),
    )
    assert not ok
    assert "authorized" in message.lower()


def test_manual_backup_succeeds_for_authorized_admin(admin_user, tmp_path):
    ok, backup_path = run_backup_authorized(
        admin_user,
        "s-1",
        str(tmp_path),
    )
    assert ok, backup_path
    assert os.path.exists(backup_path)
    records = audit.get_records(action_filter=audit.ACTION_BACKUP_CREATED)
    assert records and records[0]["session_id"] == "s-1"
