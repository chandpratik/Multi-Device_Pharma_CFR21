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
from datetime import datetime
from typing import Optional

from cfr21.db import _db_path

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
    src_path = _db_path()

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
