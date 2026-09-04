# tests/test_audit_trail.py
# §11.10(e) — audit trail tests: recording, hash chain, tamper detection,
# clock anomaly.

import cfr21.db as db
import cfr21.audit_trail as audit


class TestBasicLogging:

    def test_log_writes_record(self, admin_user):
        before = _count_records()
        ok = audit.log(admin_user, audit.ACTION_BATCH_STARTED,
                       "Test batch started", session_id="test-session")
        assert ok
        assert _count_records() == before + 1

    def test_system_events_use_system_user(self, fresh_db):
        audit.log(None, audit.ACTION_APP_STARTED, "App boot test")
        with db.get_conn_ctx() as conn:
            row = conn.execute(
                "SELECT username, role FROM audit_trail "
                "ORDER BY id DESC LIMIT 1").fetchone()
        assert row["username"] == "system"
        assert row["role"] == "system"

    def test_reason_stored(self, admin_user):
        audit.log(admin_user, audit.ACTION_CAMERA_DISCONNECTED,
                  "Camera disconnected during batch",
                  session_id="s1", reason="Cable replacement")
        with db.get_conn_ctx() as conn:
            row = conn.execute(
                "SELECT reason FROM audit_trail ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["reason"] == "Cable replacement"


class TestHashChain:

    def test_records_are_chained(self, admin_user):
        audit.log(admin_user, audit.ACTION_LOGIN, "r1", session_id="s")
        audit.log(admin_user, audit.ACTION_LOGOUT, "r2", session_id="s")
        with db.get_conn_ctx() as conn:
            rows = conn.execute(
                "SELECT prev_hash, record_hash FROM audit_trail "
                "ORDER BY id ASC").fetchall()
        hashed = [r for r in rows if r["record_hash"] is not None]
        assert len(hashed) >= 2
        # Each record's prev_hash equals the previous record's record_hash
        for i in range(1, len(hashed)):
            assert hashed[i]["prev_hash"] == hashed[i-1]["record_hash"]

    def test_verify_chain_passes_clean(self, admin_user):
        audit.log(admin_user, audit.ACTION_LOGIN, "clean record",
                  session_id="s")
        ok, message, checked = audit.verify_chain()
        assert ok, message
        assert checked > 0

    def test_verify_detects_modified_content(self, admin_user):
        audit.log(admin_user, audit.ACTION_LOGIN, "original detail",
                  session_id="s")
        # Simulate an attacker editing a record directly in SQLite
        with db.get_conn_ctx() as conn:
            conn.execute(
                "UPDATE audit_trail SET detail = 'FALSIFIED' "
                "WHERE detail = 'original detail'")
        ok, message, _ = audit.verify_chain()
        assert not ok
        assert "modif" in message.lower() or "fail" in message.lower()

    def test_verify_detects_deleted_record(self, admin_user):
        audit.log(admin_user, audit.ACTION_LOGIN, "first", session_id="s")
        audit.log(admin_user, audit.ACTION_LOGOUT, "second", session_id="s")
        audit.log(admin_user, audit.ACTION_LOGIN, "third", session_id="s")
        # Delete the middle record — chain must break
        with db.get_conn_ctx() as conn:
            conn.execute("DELETE FROM audit_trail WHERE detail = 'second'")
        ok, message, _ = audit.verify_chain()
        assert not ok

    def test_verify_detects_inserted_record(self, admin_user):
        audit.log(admin_user, audit.ACTION_LOGIN, "real", session_id="s")
        # Forge a record without valid hashes
        with db.get_conn_ctx() as conn:
            conn.execute("""
                INSERT INTO audit_trail
                    (timestamp, username, role, action, detail,
                     reason, workstation, session_id, prev_hash, record_hash)
                VALUES ('2020-01-01T00:00:00', 'attacker', 'admin',
                        'LOGIN', 'forged', NULL, 'evil-pc', 's',
                        'fakehash', 'fakehash2')
            """)
        ok, message, _ = audit.verify_chain()
        assert not ok


class TestClockAnomaly:

    def test_backward_clock_writes_anomaly(self, admin_user, monkeypatch):
        import cfr21.audit_trail as at
        from datetime import datetime, timedelta, timezone

        # First record at real time
        audit.log(admin_user, audit.ACTION_LOGIN, "now record",
                  session_id="s")

        # Monkeypatch datetime inside audit_trail to return the PAST
        class FakeDT:
            @staticmethod
            def now():
                class _N:
                    @staticmethod
                    def astimezone():
                        past = datetime.now(timezone.utc) - timedelta(hours=2)
                        return past
                return _N()
        monkeypatch.setattr(at, "datetime", FakeDT)

        audit.log(admin_user, audit.ACTION_LOGOUT, "past record",
                  session_id="s")

        with db.get_conn_ctx() as conn:
            rows = conn.execute(
                "SELECT action FROM audit_trail WHERE action = ?",
                (audit.ACTION_CLOCK_ANOMALY,)).fetchall()
        assert len(rows) == 1, "backward clock must write CLOCK_ANOMALY"


def _count_records() -> int:
    with db.get_conn_ctx() as conn:
        return conn.execute("SELECT COUNT(*) c FROM audit_trail"
                            ).fetchone()["c"]
