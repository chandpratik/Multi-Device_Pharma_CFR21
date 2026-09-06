"""Direct-call authorization tests for export and backup operations."""

import os

import cfr21.audit_trail as audit
import cfr21.db as db
import cfr21.db_backup as db_backup
from cfr21.db_backup import (
    restore_database_authorized,
    run_backup_authorized,
    verify_backup_for_restore,
)


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
    assert os.path.exists(backup_path + ".audit_checkpoint.json")
    assert verify_backup_for_restore(backup_path)[0]


def test_manual_backup_is_deleted_when_audit_fails(admin_user, tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise audit.AuditWriteError("forced audit failure")

    monkeypatch.setattr(audit, "append_event", fail)
    ok, message = run_backup_authorized(admin_user, "s-1", str(tmp_path))

    assert not ok
    assert "audit" in message.lower()
    assert not list(tmp_path.rglob("compliance_backup_*.db"))
    assert not list(tmp_path.rglob("*.audit_checkpoint.json"))


def test_restore_requires_issued_admin_session(admin_user, tmp_path):
    ok, backup_path = run_backup_authorized(admin_user, "s-1", str(tmp_path))
    assert ok, backup_path

    ok, message = restore_database_authorized(
        admin_user, "not-issued", backup_path, "validated restore", "CC-001")

    assert not ok
    assert "authorized" in message.lower()


def test_restore_rejects_non_admin_user(operator_user, tmp_path):
    ok, backup_path = run_backup_authorized(operator_user, "s-12", str(tmp_path))
    assert not ok

    ok, message = restore_database_authorized(
        operator_user, "s-12", str(tmp_path / "candidate.db"),
        "validated restore", "CC-002")

    assert not ok
    assert "authorized" in message.lower()


def test_restore_requires_reason_and_change_control(admin_user, tmp_path):
    ok, backup_path = run_backup_authorized(admin_user, "s-1", str(tmp_path))
    assert ok, backup_path

    ok, message = restore_database_authorized(
        admin_user, "s-1", backup_path, "", "CC-003")
    assert not ok
    assert "reason" in message.lower()

    ok, message = restore_database_authorized(
        admin_user, "s-1", backup_path, "validated restore", "")
    assert not ok
    assert "change-control" in message.lower()


def test_restore_rejects_tampered_candidate(admin_user, tmp_path):
    ok, backup_path = run_backup_authorized(admin_user, "s-1", str(tmp_path))
    assert ok, backup_path
    with open(backup_path, "ab") as handle:
        handle.write(b"tamper")

    ok, message = restore_database_authorized(
        admin_user, "s-1", backup_path, "validated restore", "CC-004")

    assert not ok
    assert "rejected" in message.lower()
    records = audit.get_records(action_filter=audit.ACTION_DATABASE_RESTORE_FAILED)
    assert records


def test_restore_replaces_live_database_and_publishes_anchor(admin_user, tmp_path):
    ok, backup_path = run_backup_authorized(admin_user, "s-1", str(tmp_path))
    assert ok, backup_path
    with db.get_conn_ctx() as conn:
        conn.execute("CREATE TABLE restore_probe (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO restore_probe (id) VALUES (1)")

    ok, message = restore_database_authorized(
        admin_user, "s-1", backup_path, "validated restore", "CC-005")

    assert ok, message
    with db.get_conn_ctx() as conn:
        probe = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'restore_probe'
        """).fetchone()
    assert probe is None
    records = audit.get_records(action_filter=audit.ACTION_DATABASE_RESTORE_COMMITTED)
    assert records and records[0]["reason"] == "validated restore"
    assert audit.verify_chain()[0]


def test_restore_failure_rolls_back_live_database(admin_user, tmp_path, monkeypatch):
    ok, backup_path = run_backup_authorized(admin_user, "s-1", str(tmp_path))
    assert ok, backup_path
    with db.get_conn_ctx() as conn:
        conn.execute("CREATE TABLE restore_probe (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO restore_probe (id) VALUES (1)")

    real_replace = db_backup.os.replace

    def fail_live_replace(src, dst):
        if os.path.basename(src).startswith("restore_stage_"):
            raise OSError("forced replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(db_backup.os, "replace", fail_live_replace)
    ok, message = restore_database_authorized(
        admin_user, "s-1", backup_path, "validated restore", "CC-006")

    assert not ok
    assert "rollback restored" in message
    with db.get_conn_ctx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM restore_probe").fetchone()[0] == 1
    records = audit.get_records(action_filter=audit.ACTION_DATABASE_RESTORE_FAILED)
    assert records and "forced replace failure" in records[0]["detail"]


def test_restore_audit_write_failure_does_not_replace(admin_user, tmp_path, monkeypatch):
    ok, backup_path = run_backup_authorized(admin_user, "s-1", str(tmp_path))
    assert ok, backup_path
    with db.get_conn_ctx() as conn:
        conn.execute("CREATE TABLE restore_probe (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO restore_probe (id) VALUES (1)")

    def fail_append(*_args, **_kwargs):
        raise audit.AuditWriteError("forced audit write failure")

    monkeypatch.setattr(audit.AuditWriter, "append_in_transaction", fail_append)
    ok, message = restore_database_authorized(
        admin_user, "s-1", backup_path, "validated restore", "CC-007")

    assert not ok
    assert "audit" in message.lower()
    with db.get_conn_ctx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM restore_probe").fetchone()[0] == 1
