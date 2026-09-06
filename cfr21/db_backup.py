# cfr21/db_backup.py
# Compliance database backup — 21 CFR Part 11 §11.10(c).
#
# Protects the audit trail and user records against loss or corruption.
# Uses SQLite's built-in online backup API — safe to run while the app
# is open and actively writing (no locks, no data loss).
#
# Two backup modes:
#   1. Manual   — Administrator clicks "Backup Now" in Advanced Settings
#   2. Automatic — scheduled every N hours while app is running
#
# Backup naming: compliance_backup_YYYYMMDD_HHMMSS.db
# Stored in: <log_dir>/db_backups/
#
# The most recent 10 backups are kept — older ones are deleted automatically.

import os
import shutil
import sqlite3
import logging
import tempfile
import uuid
from datetime import datetime
from typing import Optional

import cfr21.audit_trail as audit
import cfr21.db as db
from cfr21.authorization import AuthorizationError, SessionContext, authorize_session
from cfr21.user_manager import User

log = logging.getLogger("pharma.cfr21.backup")

MAX_BACKUPS = 10   # keep last 10 backups


def _backup_dir(log_dir: str, custom_dest: str = "") -> str:
    """
    Return the directory where backups will be written.
    If custom_dest is a non-empty valid path, use it directly.
    Otherwise fall back to <log_dir>/db_backups/.
    """
    if custom_dest and custom_dest.strip():
        return custom_dest.strip()
    return os.path.join(log_dir, "db_backups")


def run_backup(log_dir: str, custom_dest: str = "") -> tuple[bool, str]:
    """
    Create a timestamped backup of compliance.db using SQLite online backup API.

    Safe to call while the app is running — SQLite handles concurrent access.
    Returns (True, backup_path) on success, (False, error_message) on failure.

    custom_dest: if set, backups are written to this folder instead of
                 <log_dir>/db_backups/.  Use for USB drives or network shares.
    """
    src_path = db._db_path()

    if not os.path.exists(src_path):
        return False, "compliance.db not found — nothing to back up."

    backup_dir = _backup_dir(log_dir, custom_dest)
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except Exception as e:
        return False, f"Could not create backup directory: {e}"

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir,
                               f"compliance_backup_{timestamp}.db")

    try:
        # SQLite online backup API — consistent copy even under concurrent writes
        src  = sqlite3.connect(src_path)
        dest = sqlite3.connect(backup_path)
        with dest:
            src.backup(dest)
        dest.close()
        src.close()

        # ── Item 5a: verify the backup is a valid, uncorrupted database ──────
        try:
            check_conn = sqlite3.connect(backup_path)
            result = check_conn.execute("PRAGMA integrity_check").fetchone()[0]
            check_conn.close()
            if result != "ok":
                os.remove(backup_path)
                return False, (
                    f"Backup FAILED verification (integrity_check: {result}) "
                    f"— backup file deleted. Retry backup."
                )
        except Exception as e:
            return False, f"Backup written but could not be verified: {e}"

        # ── Item 5b: seal the backup's SHA-256 into the MAIN database ────────
        # Stored in file_integrity so a tampered/replaced backup is detectable
        # by comparing its current hash against the sealed value.
        try:
            import hashlib
            h = hashlib.sha256()
            with open(backup_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            digest = h.hexdigest()

            from cfr21.db import get_conn_ctx
            now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
            with get_conn_ctx() as conn:
                conn.execute("""
                    INSERT INTO backup_integrity
                        (timestamp, backup_file, backup_path, sha256)
                    VALUES (?, ?, ?, ?)
                """, (now_iso, os.path.basename(backup_path),
                      backup_path, digest))
            log.info("Backup verified and sealed: %s (sha256=%s…)",
                     backup_path, digest[:16])
        except Exception as e:
            # Seal failure is logged but does not fail the backup —
            # the verified backup itself is still good.
            log.warning("Backup seal failed (backup still valid): %s", e)

        log.info("Compliance DB backed up to: %s", backup_path)

        try:
            audit.write_detached_checkpoint(backup_path)
        except Exception as e:
            os.remove(backup_path)
            return False, f"Backup FAILED audit checkpoint signing: {e}"

        # Prune old backups — keep only MAX_BACKUPS most recent
        _prune_old_backups(backup_dir)

        return True, backup_path

    except Exception as e:
        log.error("DB backup failed: %s", e)
        # Remove partial backup file if it exists
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except Exception:
                pass
        return False, f"Backup failed: {e}"


def run_backup_authorized(actor: User, session_id: str, log_dir: str,
                          custom_dest: str = "") -> tuple[bool, str]:
    """Manual backup boundary; scheduled backups may still call run_backup()."""
    try:
        actor = authorize_session(
            SessionContext.from_user(actor, session_id),
            "backup_database",
            target=custom_dest or log_dir,
        )
    except AuthorizationError:
        return False, "You are not authorized to create database backups."

    ok, result = run_backup(log_dir, custom_dest)
    if ok:
        try:
            audit.append_event(audit.event_for_user(
                actor,
                audit.ACTION_BACKUP_CREATED,
                f"Manual compliance database backup created: {result}",
                session_id=session_id,
                target_type="database_backup",
                target_id=os.path.basename(result),
                new_value={"backup_path": result},
            ))
        except Exception as exc:
            _remove_backup_artifacts(result)
            return False, (
                "Backup was deleted because its audit evidence could not be "
                f"committed: {exc}"
            )
    return ok, result


def verify_backup_for_restore(backup_path: str) -> tuple[bool, str]:
    """Verify a restore candidate's SQLite integrity and audit checkpoint."""
    if not backup_path or not os.path.exists(backup_path):
        return False, "Restore candidate not found."
    try:
        conn = sqlite3.connect(backup_path)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if result != "ok":
            return False, f"Restore candidate failed SQLite integrity_check: {result}"
    except Exception as exc:
        return False, f"Restore candidate could not be opened: {exc}"

    ok, message, _tail = audit.verify_detached_checkpoint(backup_path)
    if not ok:
        return False, message
    return True, message


def restore_database_authorized(actor: User, session_id: str, backup_path: str,
                                reason: str,
                                change_control_id: str) -> tuple[bool, str]:
    """Restore compliance.db from a verified backup under admin control."""
    try:
        actor = authorize_session(
            SessionContext.from_user(actor, session_id),
            "restore_database",
            target=backup_path,
        )
    except AuthorizationError:
        return False, "You are not authorized to restore the compliance database."

    reason = (reason or "").strip()
    change_control_id = (change_control_id or "").strip()
    if not reason:
        return _restore_preflight_failure(
            actor, session_id, backup_path, reason, change_control_id,
            "Restore reason is required.")
    if not change_control_id:
        return _restore_preflight_failure(
            actor, session_id, backup_path, reason, change_control_id,
            "Change-control identifier is required.")

    ok, message = verify_backup_for_restore(backup_path)
    if not ok:
        return _restore_preflight_failure(
            actor, session_id, backup_path, reason, change_control_id,
            f"Restore candidate rejected: {message}")

    live_path = db._db_path()
    live_dir = os.path.dirname(os.path.abspath(live_path))
    os.makedirs(live_dir, exist_ok=True)
    restore_id = str(uuid.uuid4())
    rollback_path = ""
    rollback_anchor_path = ""
    staged_path = ""

    try:
        _checkpoint_database(live_path)
        rollback_path = _copy_live_database(live_path, "restore_rollback_", live_dir)
        rollback_anchor_path = _copy_live_anchor(live_path, "restore_rollback_anchor_", live_dir)

        staged_path = _stage_restore_candidate(backup_path, live_dir)
        committed_row = _append_restore_event_to_database(
            staged_path, actor, session_id, backup_path, reason,
            change_control_id, restore_id, audit.ACTION_DATABASE_RESTORE_COMMITTED,
            "success",
            "Compliance database restore committed under change control.")

        _checkpoint_database(staged_path)
        os.replace(staged_path, live_path)
        staged_path = ""
        audit._write_anchor(committed_row, audit._anchor_path_for_db(live_path))
        ok, verify_message, _checked = audit.verify_chain()
        if not ok:
            raise RuntimeError(f"Restored database failed audit verification: {verify_message}")
        return True, f"Compliance database restored from backup: {backup_path}"
    except Exception as exc:
        rollback_message = _rollback_restore(
            live_path, rollback_path, rollback_anchor_path)
        try:
            audit.append_event(audit.AuditEvent(
                action=audit.ACTION_DATABASE_RESTORE_FAILED,
                detail=(
                    "Compliance database restore failed and rollback was attempted. "
                    f"restore_id={restore_id}; backup='{backup_path}'; "
                    f"change_control_id='{change_control_id}'; error='{exc}'; "
                    f"rollback='{rollback_message}'."
                ),
                actor_id=actor.id,
                actor_username=actor.username,
                role=actor.role,
                session_id=session_id,
                target_type="database",
                target_id=os.path.basename(backup_path),
                old_value={"live_database": live_path},
                new_value={"backup_path": backup_path,
                           "change_control_id": change_control_id,
                           "restore_id": restore_id,
                           "rollback": rollback_message},
                reason=reason,
                result="failure",
                correlation_id=restore_id,
            ))
        except Exception as audit_exc:
            return False, (
                f"Restore failed and rollback status is: {rollback_message}. "
                f"Failure audit could not be written: {audit_exc}"
            )
        return False, f"Restore failed; rollback status: {rollback_message}. Error: {exc}"
    finally:
        for path in (staged_path, rollback_path, rollback_anchor_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


def _restore_preflight_failure(actor: User, session_id: str, backup_path: str,
                               reason: str, change_control_id: str,
                               detail: str) -> tuple[bool, str]:
    audit.append_event(audit.AuditEvent(
        action=audit.ACTION_DATABASE_RESTORE_FAILED,
        detail=detail,
        actor_id=actor.id,
        actor_username=actor.username,
        role=actor.role,
        session_id=session_id,
        target_type="database",
        target_id=os.path.basename(backup_path or ""),
        new_value={"backup_path": backup_path,
                   "change_control_id": change_control_id},
        reason=reason or None,
        result="failure",
    ))
    return False, detail


def _checkpoint_database(database_path: str) -> None:
    if not os.path.exists(database_path):
        return
    conn = sqlite3.connect(database_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _copy_live_database(live_path: str, prefix: str, directory: str) -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".db", dir=directory)
    os.close(fd)
    shutil.copy2(live_path, path)
    return path


def _copy_live_anchor(live_path: str, prefix: str, directory: str) -> str:
    anchor_path = audit._anchor_path_for_db(live_path)
    if not os.path.exists(anchor_path):
        return ""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=directory)
    os.close(fd)
    shutil.copy2(anchor_path, path)
    return path


def _stage_restore_candidate(backup_path: str, directory: str) -> str:
    fd, path = tempfile.mkstemp(prefix="restore_stage_", suffix=".db", dir=directory)
    os.close(fd)
    shutil.copy2(backup_path, path)
    return path


def _append_restore_event_to_database(database_path: str, actor: User,
                                      session_id: str, backup_path: str,
                                      reason: str, change_control_id: str,
                                      restore_id: str, action: str,
                                      result: str, detail: str) -> dict:
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = audit.AuditWriter.append_in_transaction(
            conn,
            audit.AuditEvent(
                action=action,
                detail=(
                    f"{detail} restore_id={restore_id}; backup='{backup_path}'; "
                    f"change_control_id='{change_control_id}'."
                ),
                actor_id=actor.id,
                actor_username=actor.username,
                role=actor.role,
                session_id=session_id,
                target_type="database",
                target_id=os.path.basename(backup_path),
                old_value={"source_backup": backup_path},
                new_value={"live_database": db._db_path(),
                           "change_control_id": change_control_id,
                           "restore_id": restore_id},
                reason=reason,
                result=result,
                correlation_id=restore_id,
            ),
            publish_anchor=False,
        )
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _rollback_restore(live_path: str, rollback_path: str,
                      rollback_anchor_path: str) -> str:
    try:
        if not rollback_path or not os.path.exists(rollback_path):
            return "rollback snapshot unavailable"
        os.replace(rollback_path, live_path)
        if rollback_anchor_path and os.path.exists(rollback_anchor_path):
            os.replace(rollback_anchor_path, audit._anchor_path_for_db(live_path))
        else:
            try:
                os.remove(audit._anchor_path_for_db(live_path))
            except OSError:
                pass
        return "rollback restored"
    except Exception as exc:
        return f"rollback failed: {exc}"


def _prune_old_backups(backup_dir: str):
    """Delete oldest backups if more than MAX_BACKUPS exist."""
    try:
        files = [
            os.path.join(backup_dir, f)
            for f in os.listdir(backup_dir)
            if f.startswith("compliance_backup_") and f.endswith(".db")
        ]
        files.sort()   # ascending — oldest first
        while len(files) > MAX_BACKUPS:
            oldest = files.pop(0)
            os.remove(oldest)
            log.info("Pruned old backup: %s", oldest)
    except Exception as e:
        log.warning("Could not prune old backups: %s", e)


def _remove_backup_artifacts(backup_path: str) -> None:
    """Remove a backup only when its required manual audit cannot be stored."""
    for path in (backup_path, backup_path + ".audit_checkpoint.json"):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            log.error("Could not remove unaudited backup artifact %s: %s",
                      path, exc)


def list_backups(log_dir: str) -> list[dict]:
    """
    Return a list of existing backups, most recent first.
    Each entry: { "path": str, "filename": str, "size_kb": float,
                  "created": str }
    """
    backup_dir = _backup_dir(log_dir)
    if not os.path.exists(backup_dir):
        return []

    result = []
    try:
        for fname in sorted(os.listdir(backup_dir), reverse=True):
            if not (fname.startswith("compliance_backup_") and
                    fname.endswith(".db")):
                continue
            fpath = os.path.join(backup_dir, fname)
            size_kb = os.path.getsize(fpath) / 1024
            result.append({
                "path":     fpath,
                "filename": fname,
                "size_kb":  round(size_kb, 1),
                "created":  fname.replace("compliance_backup_", "")
                                 .replace(".db", "")
                                 .replace("_", " ", 1),
            })
    except Exception as e:
        log.error("list_backups() error: %s", e)

    return result


def get_last_backup_time(log_dir: str) -> Optional[str]:
    """Return the filename timestamp of the most recent backup, or None."""
    backups = list_backups(log_dir)
    if backups:
        return backups[0]["created"]
    return None
