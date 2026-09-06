"""Part 3 controls for structured, coupled, and tamper-evident auditing."""

import json
from concurrent.futures import ThreadPoolExecutor

import cfr21.audit_trail as audit
import cfr21.db as db
import cfr21.regulated_records as records


def test_structured_event_contract_and_readable_rendering(admin_user):
    assert audit.log(
        admin_user,
        "CONFIGURATION_CHANGED",
        "Configuration version proposed",
        session_id="s-1",
        reason="Validated parameter update",
        target_type="configuration",
        target_id="cfg-7",
        target_version=3,
        old_value={"speed": 10},
        new_value={"speed": 12},
        result="success",
        correlation_id="change-7",
    )
    with db.get_conn_ctx() as conn:
        row = conn.execute("""
            SELECT event_id, actor_id, username, role, session_id,
                   target_type, target_id, target_version, old_value_json,
                   new_value_json, reason, result, correlation_id, signature
            FROM audit_trail ORDER BY id DESC LIMIT 1
        """).fetchone()
    assert row["event_id"]
    assert row["actor_id"] == admin_user.id
    assert row["username"] == admin_user.username
    assert row["target_type"] == "configuration"
    assert row["target_version"] == 3
    assert json.loads(row["old_value_json"]) == {"speed": 10}
    assert json.loads(row["new_value_json"]) == {"speed": 12}
    assert row["reason"] == "Validated parameter update"
    assert row["result"] == "success"
    assert row["correlation_id"] == "change-7"
    assert row["signature"]
    assert audit.get_records(limit=1)[0]["readable"].startswith(
        "CONFIGURATION_CHANGED configuration=cfg-7:")


def test_audit_failure_rolls_back_regulated_write(admin_user, monkeypatch):
    def fail(*_args, **_kwargs):
        raise audit.AuditWriteError("forced audit failure")

    monkeypatch.setattr(audit.AuditWriter, "append_in_transaction", fail)
    service = records.RegulatedRecordService()
    try:
        service.start_or_resume_batch(
            admin_user, "AUDIT-ROLLBACK", "admin", "Tablet", {}, "s-1")
    except audit.AuditWriteError:
        pass
    else:
        raise AssertionError("regulated write succeeded without audit evidence")
    with db.get_conn_ctx() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM regulated_batches WHERE external_batch_id = ?",
            ("AUDIT-ROLLBACK",)).fetchone()[0] == 0


def test_protected_signature_detects_local_hash_recalculation(admin_user):
    audit.log(admin_user, "SIGNED_EVENT", "original", session_id="s-1")
    with db.get_conn_ctx() as conn:
        row = conn.execute(
            "SELECT * FROM audit_trail ORDER BY id DESC LIMIT 1").fetchone()
        forged_hash = audit._compute_record_hash(
            row["prev_hash"], row["timestamp"], row["username"], row["role"],
            row["action"], "forged", row["reason"], row["workstation"],
            row["session_id"], row["event_id"], row["actor_id"],
            row["target_type"], row["target_id"], row["target_version"],
            row["old_value_json"], row["new_value_json"], row["result"],
            row["correlation_id"])
        conn.execute(
            "UPDATE audit_trail SET detail = ?, record_hash = ? WHERE id = ?",
            ("forged", forged_hash, row["id"]))
    ok, message, _ = audit.verify_chain()
    assert not ok
    assert "signature" in message.lower()


def test_external_anchor_detects_tail_truncation(admin_user):
    audit.log(admin_user, "ANCHOR_FIRST", "first", session_id="s-1")
    audit.log(admin_user, "ANCHOR_SECOND", "second", session_id="s-1")
    with db.get_conn_ctx() as conn:
        conn.execute("DELETE FROM audit_trail WHERE action = 'ANCHOR_SECOND'")
    ok, message, _ = audit.verify_chain()
    assert not ok
    assert "anchor" in message.lower()


def test_serialized_writers_preserve_all_events(admin_user):
    def write(index):
        return audit.log(admin_user, "CONCURRENT_EVENT", str(index), session_id="s-1")

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(write, range(20)))
    assert all(results)
    with db.get_conn_ctx() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM audit_trail WHERE action = 'CONCURRENT_EVENT'"
        ).fetchone()[0]
    assert count == 20
    assert audit.verify_chain()[0]
