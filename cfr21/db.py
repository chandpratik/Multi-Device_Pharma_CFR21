# cfr21/db.py
# SQLite database foundation for 21 CFR Part 11 compliance.
#
# This is the ONLY file that knows where the database lives and how
# tables are structured. Every other cfr21 module imports get_conn()
# from here — nothing else opens SQLite directly.
#
# ── Database location ─────────────────────────────────────────────────────────
#   Stored as  compliance.db  in the same folder as settings.json
#   (i.e., next to the executable in a frozen build, or project root in dev).
#
# ── Tables ────────────────────────────────────────────────────────────────────
#
#   users
#     id              INTEGER PK AUTOINCREMENT
#     username        TEXT UNIQUE NOT NULL
#     password_hash   TEXT NOT NULL          — bcrypt hash, never plaintext
#     role            TEXT NOT NULL          — 'administrator'|'supervisor'|'operator'|'qa'
#     is_active       INTEGER NOT NULL       — 1 = active, 0 = deactivated
#     must_change_pw  INTEGER NOT NULL       — 1 = force change on next login
#     failed_attempts INTEGER NOT NULL       — reset to 0 on successful login
#     locked_until    TEXT                   — ISO timestamp or NULL
#     password_changed_at TEXT NOT NULL      — ISO timestamp of last pw change
#     created_at      TEXT NOT NULL          — ISO timestamp
#     created_by      TEXT NOT NULL          — username who created this account
#
#   audit_trail
#     id              INTEGER PK AUTOINCREMENT
#     timestamp       TEXT NOT NULL          — UTC ISO timestamp (microseconds)
#     username        TEXT NOT NULL          — WHO performed the action
#     role            TEXT NOT NULL          — role at time of action
#     action          TEXT NOT NULL          — WHAT was done (short code)
#     detail          TEXT NOT NULL          — human-readable description
#     reason          TEXT                   — WHY (operator-entered, nullable)
#     workstation     TEXT                   — hostname of machine
#     session_id      TEXT                   — links to the login session
#
#   file_integrity
#     id              INTEGER PK AUTOINCREMENT
#     timestamp       TEXT NOT NULL          — when checksum was recorded
#     username        TEXT NOT NULL          — who closed the batch
#     batch_id        TEXT NOT NULL
#     device_id       INTEGER NOT NULL
#     file_type       TEXT NOT NULL          — 'excel' or 'wal'
#     file_path       TEXT NOT NULL
#     sha256          TEXT NOT NULL          — hex digest
#
# ── Design notes ──────────────────────────────────────────────────────────────
#   audit_trail has NO UPDATE or DELETE permissions by design.
#   The only operation ever performed on it is INSERT + SELECT.
#   This enforces immutability at the application level (§11.10(e)).
#
#   WAL mode enabled: readers don't block writers and vice versa.
#   This matters because audit writes happen from scan threads while
#   the GUI thread may be reading for the audit viewer.
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import sqlite3
import logging
from contextlib import contextmanager
from typing import Generator

log = logging.getLogger("pharma.cfr21.db")

# ── Schema version — bump this when adding columns or tables ──────────────────
_SCHEMA_VERSION = 8


# ── Database path ─────────────────────────────────────────────────────────────

def _db_path() -> str:
    """
    Returns the path to compliance.db.
    Mirrors the same logic used by AppConfig._path() so the DB always
    sits next to settings.json.
    """
    root = (
        os.path.dirname(sys.executable)
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        ))
    )
    return os.path.join(root, "compliance.db")


# ── Connection factory ────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    """
    Open and return a sqlite3 connection to compliance.db.

    Settings applied to every connection:
      - WAL journal mode   — concurrent reads/writes without blocking
      - FOREIGN KEYS ON    — enforces referential integrity
      - Row factory        — rows behave like dicts (row["column"])
      - Timeout 10s        — wait up to 10s if DB is locked before raising

    Caller is responsible for closing the connection.
    For automatic close, use the get_conn_ctx() context manager instead.
    """
    path = _db_path()
    conn = sqlite3.connect(path, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row          # access columns by name
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_conn_ctx() -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager version of get_conn().
    Commits on clean exit, rolls back on exception, always closes.

    Usage:
        with get_conn_ctx() as conn:
            conn.execute("INSERT INTO ...")
    """
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema creation and migration ─────────────────────────────────────────────

def initialise():
    """
    Create all tables if they do not exist.
    Safe to call every time the app starts — uses CREATE IF NOT EXISTS.
    After creating tables, runs _migrate() to apply any pending schema changes.
    Also seeds the default Administrator account if no users exist.
    Raises RuntimeError if the existing database file fails SQLite's
    integrity check (corruption from power loss / bad disk).
    """
    log.info("Initialising compliance database at: %s", _db_path())

    # ── Corruption check on an existing DB before touching it ─────────────
    if os.path.exists(_db_path()):
        try:
            with get_conn_ctx() as conn:
                result = conn.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                log.critical("compliance.db FAILED integrity check: %s", result)
                raise RuntimeError(
                    "compliance.db failed SQLite integrity check — the file "
                    "may be corrupted. Restore from the latest backup before "
                    f"continuing. Details: {result}"
                )
        except RuntimeError:
            raise
        except Exception as e:
            log.error("Could not run integrity check: %s", e)

    with get_conn_ctx() as conn:

        # ── schema_version table ──────────────────────────────────────────────
        # Tracks which migration level the DB is at.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )
        """)

        # ── users table ───────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                username            TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                password_hash       TEXT    NOT NULL,
                role                TEXT    NOT NULL
                                    CHECK(role IN (
                                        'administrator',
                                        'supervisor',
                                        'operator',
                                        'qa'
                                    )),
                is_active           INTEGER NOT NULL DEFAULT 1
                                    CHECK(is_active IN (0, 1)),
                must_change_pw      INTEGER NOT NULL DEFAULT 1
                                    CHECK(must_change_pw IN (0, 1)),
                failed_attempts     INTEGER NOT NULL DEFAULT 0,
                locked_until        TEXT,
                password_changed_at TEXT    NOT NULL,
                created_at          TEXT    NOT NULL,
                created_by          TEXT    NOT NULL
            )
        """)

        # ── audit_trail table ─────────────────────────────────────────────────
        # Append-only — INSERT only, never UPDATE or DELETE.
        # Hash chain (v3): each record stores prev_hash + record_hash so any
        # edit/deletion of a row breaks the chain and is detectable.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_trail (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                username    TEXT    NOT NULL,
                role        TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                detail      TEXT    NOT NULL,
                reason      TEXT,
                workstation TEXT    NOT NULL,
                session_id  TEXT    NOT NULL,
                prev_hash   TEXT,
                record_hash TEXT
            )
        """)

        # ── file_integrity table ──────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_integrity (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                username    TEXT    NOT NULL,
                batch_id    TEXT    NOT NULL,
                device_id   INTEGER NOT NULL,
                file_type   TEXT    NOT NULL CHECK(file_type IN ('excel', 'wal')),
                file_path   TEXT    NOT NULL,
                sha256      TEXT    NOT NULL
            )
        """)

        # ── password_history table ────────────────────────────────────────────
        # Stores hashes of last N passwords per user.
        # Prevents password reuse — checked on every password change.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                password_hash TEXT  NOT NULL,
                changed_at  TEXT    NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_id         TEXT PRIMARY KEY,
                user_id            INTEGER NOT NULL REFERENCES users(id),
                username           TEXT NOT NULL,
                role_at_login      TEXT NOT NULL,
                login_time         TEXT NOT NULL,
                last_activity      TEXT NOT NULL,
                state              TEXT NOT NULL
                                   CHECK(state IN (
                                       'active',
                                       'locked',
                                       'expired',
                                       'logged_out'
                                   )),
                lock_time          TEXT,
                expiry_time        TEXT NOT NULL,
                workstation        TEXT NOT NULL,
                termination_reason TEXT
            )
        """)

        # ── indexes for fast lookups ──────────────────────────────────────────
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp
            ON audit_trail(timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_username
            ON audit_trail(username)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_session
            ON audit_trail(session_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_integrity_batch
            ON file_integrity(batch_id, device_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_sessions_user_state
            ON user_sessions(user_id, state)
        """)

        log.info("Database tables verified/created.")

    # Run migrations for any schema changes added after initial release
    _migrate()

    # Seed default admin if the users table is empty
    _seed_default_admin()


def _migrate():
    """
    Apply schema migrations in order.
    Each migration is idempotent — safe to run more than once.
    New columns/tables added in future versions go here.
    """
    with get_conn_ctx() as conn:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = row["version"] if row else 0

        if current < 1:
            # Version 1 — initial schema (all tables created above).
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (1)"
                )
            else:
                conn.execute("UPDATE schema_version SET version = 1")
            current = 1
            log.info("Schema at version 1")

        # Version 2: UNIQUE seal — prevent re-sealing the same batch file
        if current < 2:
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_integrity_unique_seal
                ON file_integrity(batch_id, device_id, file_type)
            """)
            conn.execute("UPDATE schema_version SET version = 2")
            current = 2
            log.info("Schema migrated to version 2 — file_integrity unique seal enforced")

        # Version 3: audit trail hash chain — tamper-evident records
        if current < 3:
            cols = [r["name"] for r in
                    conn.execute("PRAGMA table_info(audit_trail)").fetchall()]
            if "prev_hash" not in cols:
                conn.execute(
                    "ALTER TABLE audit_trail ADD COLUMN prev_hash TEXT")
            if "record_hash" not in cols:
                conn.execute(
                    "ALTER TABLE audit_trail ADD COLUMN record_hash TEXT")
            # Backup verification seals — separate table because
            # file_integrity has a CHECK constraint on file_type
            conn.execute("""
                CREATE TABLE IF NOT EXISTS backup_integrity (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    backup_file TEXT    NOT NULL,
                    backup_path TEXT    NOT NULL,
                    sha256      TEXT    NOT NULL
                )
            """)
            conn.execute("UPDATE schema_version SET version = 3")
            current = 3
            log.info("Schema migrated to version 3 — audit trail hash chain")


        if current < 4:
            _migrate_v4_authoritative_records(conn)
            conn.execute("UPDATE schema_version SET version = 4")
            current = 4
            log.info("Schema migrated to version 4 - authoritative regulated records")

        if current < 5:
            _migrate_v5_immutable_regulated_records(conn)
            conn.execute("UPDATE schema_version SET version = 5")
            current = 5
            log.info("Schema migrated to version 5 - immutable regulated records")

        if current < 6:
            # Re-run the idempotent v5 DDL so installations created during the
            # first v5 rollout also receive later immutable-table triggers.
            _migrate_v5_immutable_regulated_records(conn)
            conn.execute("UPDATE schema_version SET version = 6")
            current = 6
            log.info("Schema migrated to version 6 - immutable record safeguards")

        if current < 7:
            _migrate_v7_recovery_and_legacy_import(conn)
            conn.execute("UPDATE schema_version SET version = 7")
            current = 7
            log.info("Schema migrated to version 7 - recovery and legacy import controls")

        if current < 8:
            _migrate_v8_authoritative_sessions(conn)
            conn.execute("UPDATE schema_version SET version = 8")
            log.info("Schema migrated to version 8 - authoritative sessions")


def _migrate_v4_authoritative_records(conn):
    """Create the controlled source-of-truth tables for new production data."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS regulated_batches (
            id TEXT PRIMARY KEY,
            external_batch_id TEXT NOT NULL,
            operator_id TEXT NOT NULL,
            product_name TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL CHECK(state IN ('active', 'stopped', 'reconciliation_pending', 'closed')),
            configuration_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            started_at TEXT NOT NULL,
            stopped_at TEXT,
            stopped_by TEXT,
            UNIQUE(external_batch_id, state)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_records (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL REFERENCES regulated_batches(id),
            device_id INTEGER NOT NULL CHECK(device_id > 0),
            sequence_no INTEGER NOT NULL CHECK(sequence_no > 0),
            recorded_at TEXT NOT NULL,
            raw_data TEXT NOT NULL,
            master_data TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PASS', 'FAIL')),
            operator_id TEXT NOT NULL,
            product_name TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            session_id TEXT NOT NULL,
            UNIQUE(batch_id, device_id, sequence_no)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_scan_records_batch_device_sequence
        ON scan_records(batch_id, device_id, sequence_no)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_regulated_batches_external_state
        ON regulated_batches(external_batch_id, state)
    """)


def _migrate_v5_immutable_regulated_records(conn):
    """Prevent in-place scan mutation and add controlled recipe/config snapshots."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recipe_versions (
            id TEXT PRIMARY KEY,
            master_data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            UNIQUE(master_data)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS configuration_versions (
            id TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            UNIQUE(snapshot_json)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            device_number INTEGER NOT NULL,
            source_identifier TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            UNIQUE(device_number, source_identifier)
        )
    """)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(regulated_batches)")}
    if "configuration_version_id" not in cols:
        conn.execute("ALTER TABLE regulated_batches ADD COLUMN configuration_version_id TEXT")
    scan_cols = {r["name"] for r in conn.execute("PRAGMA table_info(scan_records)")}
    if "recipe_version_id" not in scan_cols:
        conn.execute("ALTER TABLE scan_records ADD COLUMN recipe_version_id TEXT")
    if "device_registry_id" not in scan_cols:
        conn.execute("ALTER TABLE scan_records ADD COLUMN device_registry_id TEXT")
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_scan_record_update
        BEFORE UPDATE ON scan_records
        BEGIN SELECT RAISE(ABORT, 'scan_records are immutable'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_scan_record_delete
        BEFORE DELETE ON scan_records
        BEGIN SELECT RAISE(ABORT, 'scan_records are immutable'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_regulated_batch_delete
        BEFORE DELETE ON regulated_batches
        BEGIN SELECT RAISE(ABORT, 'regulated_batches cannot be deleted'); END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_regulated_batch_identity_update
        BEFORE UPDATE ON regulated_batches
        WHEN NEW.id IS NOT OLD.id
          OR NEW.external_batch_id IS NOT OLD.external_batch_id
          OR NEW.operator_id IS NOT OLD.operator_id
          OR NEW.product_name IS NOT OLD.product_name
          OR NEW.configuration_json IS NOT OLD.configuration_json
          OR NEW.configuration_version_id IS NOT OLD.configuration_version_id
          OR NEW.created_at IS NOT OLD.created_at
          OR NEW.created_by IS NOT OLD.created_by
          OR NEW.started_at IS NOT OLD.started_at
        BEGIN SELECT RAISE(ABORT, 'regulated batch identity is immutable'); END
    """)


def _migrate_v7_recovery_and_legacy_import(conn):
    """Add durable recovery evidence and controlled legacy-import provenance."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS batch_reconciliations (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL REFERENCES regulated_batches(id),
            detected_at TEXT NOT NULL,
            detected_by TEXT NOT NULL,
            reconciled_at TEXT,
            reconciled_by TEXT,
            recovery_reason TEXT,
            status TEXT NOT NULL CHECK(status IN ('pending', 'completed')),
            device_summary_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(batch_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS legacy_wal_imports (
            id TEXT PRIMARY KEY,
            imported_at TEXT NOT NULL,
            imported_by TEXT NOT NULL,
            source_path TEXT NOT NULL,
            preserved_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            limitations TEXT NOT NULL,
            report_path TEXT NOT NULL
        )
    """)
    scan_cols = {r["name"] for r in conn.execute("PRAGMA table_info(scan_records)")}
    if "record_classification" not in scan_cols:
        conn.execute("ALTER TABLE scan_records ADD COLUMN record_classification TEXT NOT NULL DEFAULT 'regulated'")
    if "legacy_import_id" not in scan_cols:
        conn.execute("ALTER TABLE scan_records ADD COLUMN legacy_import_id TEXT")
    if "delivery_id" not in scan_cols:
        conn.execute("ALTER TABLE scan_records ADD COLUMN delivery_id TEXT")
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_scan_records_delivery_id
        ON scan_records(batch_id, device_id, delivery_id)
        WHERE delivery_id IS NOT NULL
    """)


def _migrate_v8_authoritative_sessions(conn):
    """Persist issued sessions so backend services can reject revoked authority."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            session_id         TEXT PRIMARY KEY,
            user_id            INTEGER NOT NULL REFERENCES users(id),
            username           TEXT NOT NULL,
            role_at_login      TEXT NOT NULL,
            login_time         TEXT NOT NULL,
            last_activity      TEXT NOT NULL,
            state              TEXT NOT NULL
                               CHECK(state IN (
                                   'active',
                                   'locked',
                                   'expired',
                                   'logged_out'
                               )),
            lock_time          TEXT,
            expiry_time        TEXT NOT NULL,
            workstation        TEXT NOT NULL,
            termination_reason TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_sessions_user_state
        ON user_sessions(user_id, state)
    """)


def _seed_default_admin():
    """
    If no users exist, create a default Administrator account with a
    RANDOMLY GENERATED one-time password.

    The password is:
      - Never stored in source code (CFR21 loophole fix #10)
      - Written once to first_login.txt next to the executable
      - Printed once to stdout/console on first run
      - Stored only as a bcrypt hash in compliance.db
      - Forced to change on first login (must_change_pw = 1)

    Every fresh installation has a unique, unknown-to-anyone initial
    password — eliminating the risk of a known default credential.
    """
    import bcrypt
    import secrets
    import string
    import sys
    from datetime import datetime, timezone
    from cfr21.user_manager import _check_complexity

    with get_conn_ctx() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count > 0:
            return   # Users already exist — do not overwrite

    # Generate a random 14-char password satisfying all complexity rules
    special_chars = "!@#$%^&*()_+-="
    alphabet = string.ascii_letters + string.digits + special_chars
    while True:
        chars = (
            [secrets.choice(string.ascii_uppercase)] +
            [secrets.choice(string.ascii_lowercase)] +
            [secrets.choice(string.digits)] +
            [secrets.choice(special_chars)] +
            [secrets.choice(alphabet) for _ in range(10)]
        )
        secrets.SystemRandom().shuffle(chars)
        default_pw = "".join(chars)
        ok, _ = _check_complexity(default_pw)
        if ok:
            break

    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    pw_hash = bcrypt.hashpw(
        default_pw.encode("utf-8"), bcrypt.gensalt(rounds=12)
    ).decode("utf-8")

    with get_conn_ctx() as conn:
        conn.execute("""
            INSERT INTO users
                (username, password_hash, role, is_active,
                 must_change_pw, failed_attempts, locked_until,
                 password_changed_at, created_at, created_by)
            VALUES (?, ?, ?, 1, 1, 0, NULL, ?, ?, ?)
        """, ("admin", pw_hash, "administrator", now_iso, now_iso, "system"))

    # Write one-time credentials to first_login.txt alongside the exe
    root = (
        os.path.dirname(sys.executable)
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        ))
    )
    first_login_path = os.path.join(root, "first_login.txt")
    msg_lines = [
        "=" * 60,
        "PHARMA CODE DATALOGGER — FIRST INSTALLATION",
        "=" * 60,
        "",
        "A default Administrator account has been created.",
        "",
        f"  Username : admin",
        f"  Password : {default_pw}",
        "",
        "IMPORTANT:",
        "  Change this password immediately on first login.",
        "  Delete this file after first login.",
        "  This password is valid only until first changed.",
        "",
        f"Generated : {now_iso}",
        "=" * 60,
    ]
    try:
        with open(first_login_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(msg_lines) + "\n")
    except Exception as e:
        log.warning("Could not write first_login.txt: %s", e)

    print("\n".join(msg_lines), flush=True)
    log.info(
        "Default admin created. Credentials written to first_login.txt. "
        "User MUST change password on first login."
    )


# ── Utility: check DB is reachable ───────────────────────────────────────────

def health_check() -> bool:
    """
    Returns True if the database file exists and is readable.
    Used at startup to detect a corrupt or missing DB before the app opens.
    """
    try:
        with get_conn_ctx() as conn:
            conn.execute("SELECT 1 FROM schema_version").fetchone()
        return True
    except Exception as e:
        log.error("DB health check failed: %s", e)
        return False
