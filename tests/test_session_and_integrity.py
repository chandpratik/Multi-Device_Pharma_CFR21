# tests/test_session_and_integrity.py
# Session lifecycle (§11.10d/j) and record sealing (§11.10a/c) tests.

import os

import cfr21.db as db
import cfr21.user_manager as um
import cfr21.audit_trail as audit
from cfr21.session_manager import SessionManager
from cfr21.record_integrity import (
    seal_batch_files, verify_batch_files,
    find_orphaned_wals, seal_orphaned_wal,
)


class TestSessionManager:

    def test_login_creates_session(self, operator_user):
        sm = SessionManager()
        result = sm.login("operator1", "Operator@123")
        assert result.success
        assert sm.is_logged_in
        assert sm.session_id != ""

    def test_failed_login_no_session(self, operator_user):
        sm = SessionManager()
        result = sm.login("operator1", "wrong")
        assert not result.success
        assert not sm.is_logged_in

    def test_logout_clears_session(self, operator_user):
        sm = SessionManager()
        sm.login("operator1", "Operator@123")
        session_id = sm.session_id
        sm.logout()
        assert not sm.is_logged_in
        assert sm.session_id == ""
        with db.get_conn_ctx() as conn:
            row = conn.execute(
                "SELECT state FROM user_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        assert row["state"] == "logged_out"

    def test_lock_and_unlock(self, operator_user):
        sm = SessionManager()
        sm.login("operator1", "Operator@123")
        session_id = sm.session_id
        sm.lock_screen()
        assert sm.is_locked
        with db.get_conn_ctx() as conn:
            row = conn.execute(
                "SELECT state FROM user_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        assert row["state"] == "locked"
        ok, msg = sm.unlock_screen("Operator@123")
        assert ok, msg
        assert not sm.is_locked
        with db.get_conn_ctx() as conn:
            row = conn.execute(
                "SELECT state FROM user_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        assert row["state"] == "active"

    def test_unlock_wrong_password_fails(self, operator_user):
        sm = SessionManager()
        sm.login("operator1", "Operator@123")
        sm.lock_screen()
        ok, msg = sm.unlock_screen("wrong")
        assert not ok
        assert sm.is_locked

    def test_unlock_bruteforce_locks_account(self, operator_user):
        sm = SessionManager(policy_max_attempts=3)
        sm.login("operator1", "Operator@123")
        sm.lock_screen()
        for _ in range(3):
            sm.unlock_screen("wrong")
        # Correct password now rejected — account locked
        ok, msg = sm.unlock_screen("Operator@123")
        assert not ok
        assert "locked" in msg.lower()

    def test_reauth_wrong_password_counts_toward_lockout(self, operator_user):
        """Item 2 fix: reauthenticate must route through authenticate."""
        sm = SessionManager(policy_max_attempts=3)
        sm.login("operator1", "Operator@123")
        for _ in range(3):
            sm.reauthenticate("operator1", "wrong")
        ok, msg = sm.reauthenticate("operator1", "Operator@123")
        assert not ok, "account must be locked after repeated reauth failures"

    def test_reauth_username_mismatch(self, operator_user, admin_user):
        sm = SessionManager()
        sm.login("operator1", "Operator@123")
        ok, msg = sm.reauthenticate("admin", "AdminTest@123")
        assert not ok

    def test_permission_check(self, operator_user):
        sm = SessionManager()
        sm.login("operator1", "Operator@123")
        assert sm.can("start_logging")
        assert not sm.can("manage_users")


class TestRecordIntegrity:

    def _make_wal(self, tmp_path, batch_id="TESTBATCH"):
        wal = tmp_path / f"WAL_{batch_id}_20260101_000000.csv"
        wal.write_text(
            "read_id,timestamp,batch_id,operator_id,product_name,"
            "raw_data,master_data,status\n"
            f"1,2026-01-01 00:00:01,{batch_id},op1,Aspirin,123,123,PASS\n"
            f"2,2026-01-01 00:00:02,{batch_id},op1,Aspirin,456,123,FAIL\n"
        )
        return str(wal)

    def test_seal_and_verify_pass(self, admin_user, tmp_path):
        wal = self._make_wal(tmp_path)
        results = seal_batch_files(admin_user, "TESTBATCH", 1,
                                   excel_path=None, wal_path=wal)
        assert any(r.get("ok") for r in results)
        verify = verify_batch_files("TESTBATCH", 1)
        assert all(v.get("match") for v in verify)

    def test_tampered_file_fails_verify(self, admin_user, tmp_path):
        wal = self._make_wal(tmp_path)
        seal_batch_files(admin_user, "TESTBATCH", 1,
                         excel_path=None, wal_path=wal)
        # Tamper with the file after sealing
        with open(wal, "a") as f:
            f.write("999,2026-01-01 00:00:03,TESTBATCH,op1,Aspirin,"
                    "FORGED,123,PASS\n")
        verify = verify_batch_files("TESTBATCH", 1)
        assert any(not v.get("match") for v in verify)

    def test_reseal_blocked(self, admin_user, tmp_path):
        wal = self._make_wal(tmp_path)
        seal_batch_files(admin_user, "TESTBATCH", 1,
                         excel_path=None, wal_path=wal)
        # Second seal of same (batch, device, type) must not create a
        # second row — UNIQUE index blocks it
        seal_batch_files(admin_user, "TESTBATCH", 1,
                         excel_path=None, wal_path=wal)
        with db.get_conn_ctx() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) c FROM file_integrity "
                "WHERE batch_id='TESTBATCH' AND device_id=1 "
                "AND LOWER(file_type)='wal'").fetchone()
        assert rows["c"] == 1

    def test_orphan_detection(self, admin_user, tmp_path):
        wal_sealed  = self._make_wal(tmp_path, "SEALED")
        wal_orphan  = self._make_wal(tmp_path, "ORPHAN")
        seal_batch_files(admin_user, "SEALED", 1,
                         excel_path=None, wal_path=wal_sealed)
        orphans = find_orphaned_wals({1: str(tmp_path)})
        paths = [o["wal_path"] for o in orphans]
        assert wal_orphan in paths
        assert wal_sealed not in paths

    def test_orphan_wal_cannot_be_treated_as_authoritative_recovery(self, admin_user, tmp_path):
        wal_orphan = self._make_wal(tmp_path, "CRASHED")
        try:
            seal_orphaned_wal(admin_user, 1, wal_orphan)
        except RuntimeError as exc:
            assert "retired" in str(exc)
        else:
            raise AssertionError("A WAL was accepted as authoritative recovery data")


class TestDatabase:

    def test_schema_version_current(self, fresh_db):
        with db.get_conn_ctx() as conn:
            v = conn.execute("SELECT version FROM schema_version"
                             ).fetchone()["version"]
        assert v == 8

    def test_audit_table_has_hash_columns(self, fresh_db):
        with db.get_conn_ctx() as conn:
            cols = [r["name"] for r in
                    conn.execute("PRAGMA table_info(audit_trail)").fetchall()]
        assert "prev_hash" in cols
        assert "record_hash" in cols

    def test_integrity_unique_index_exists(self, fresh_db):
        with db.get_conn_ctx() as conn:
            idx = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_integrity_unique_seal'").fetchone()
        assert idx is not None

    def test_user_sessions_table_exists(self, fresh_db):
        with db.get_conn_ctx() as conn:
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='user_sessions'").fetchone()
        assert table is not None
