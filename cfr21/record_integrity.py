# cfr21/record_integrity.py
# File integrity verification — 21 CFR Part 11 §11.10(c).
#
# Every time a batch is closed (stop_logging), the Excel and WAL files
# are SHA-256 hashed and the hash is stored in the file_integrity table.
#
# This means:
#   - If anyone opens the Excel in Windows and edits a cell, the hash
#     will no longer match — tampering is detectable.
#   - Regulators can run verify_batch_files() to confirm record integrity.
#   - The manifest is stored in the DB (not a text file next to the Excel)
#     so it cannot be edited alongside the data file.
#
# ── How it works ──────────────────────────────────────────────────────────────
#   1. Datalogger.stop() calls WALExcelLogger.close_batch()
#   2. close_batch() now also calls record_integrity.seal_batch_files()
#   3. seal_batch_files() hashes both the Excel and WAL, stores in DB
#   4. At any time, verify_batch_files() re-hashes and compares
# ─────────────────────────────────────────────────────────────────────────────

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from cfr21.db import get_conn_ctx
from cfr21.user_manager import User

log = logging.getLogger("pharma.cfr21.integrity")

_CHUNK_SIZE = 65_536   # 64 KB read chunks — memory-efficient for large files


# ── Hash computation ──────────────────────────────────────────────────────────

def sha256_file(filepath: str) -> str:
    """
    Compute SHA-256 hex digest of a file.
    Reads in 64KB chunks — safe for large Excel files.
    Raises FileNotFoundError if the file does not exist.
    """
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ── Seal (write checksum) ─────────────────────────────────────────────────────

def seal_batch_files(user: Optional[User],
                     batch_id: str,
                     device_id: int,
                     excel_path: Optional[str],
                     wal_path: Optional[str]) -> list[dict]:
    """
    Compute and store SHA-256 checksums for a batch's Excel and WAL files.
    Called automatically when a batch is stopped (close_batch).

    Returns a list of result dicts, one per file:
        [{"file": path, "sha256": hex, "ok": True}, ...]
    Files that don't exist are skipped with a warning.
    """
    results = []
    username = user.username if user else "system"
    now_iso  = datetime.now().astimezone().isoformat(timespec="seconds")

    for file_type, fpath in [("excel", excel_path), ("wal", wal_path)]:
        if not fpath:
            continue

        if not os.path.exists(fpath):
            log.warning("seal_batch_files: file not found — %s", fpath)
            results.append({"file": fpath, "sha256": "", "ok": False,
                             "error": "File not found"})
            continue

        try:
            digest = sha256_file(fpath)
        except Exception as e:
            log.error("seal_batch_files: hash failed for %s — %s", fpath, e)
            results.append({"file": fpath, "sha256": "", "ok": False,
                             "error": str(e)})
            continue

        try:
            with get_conn_ctx() as conn:
                conn.execute("""
                    INSERT INTO file_integrity
                        (timestamp, username, batch_id, device_id,
                         file_type, file_path, sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (now_iso, username, batch_id, device_id,
                      file_type, fpath, digest))

            log.info("Sealed %s file for batch '%s' device %s: %s…",
                     file_type, batch_id, device_id, digest[:16])
            results.append({"file": fpath, "sha256": digest, "ok": True})

        except Exception as e:
            import sqlite3
            if isinstance(e, sqlite3.IntegrityError):
                log.error(
                    "seal_batch_files: RE-SEAL BLOCKED — batch '%s' device %s "
                    "file_type '%s' already sealed. Possible tampering attempt.",
                    batch_id, device_id, file_type
                )
                results.append({"file": fpath, "sha256": digest, "ok": False,
                                 "error": "Re-seal blocked: integrity record already exists."})
            else:
                log.error("seal_batch_files: DB write failed — %s", e)
                results.append({"file": fpath, "sha256": digest, "ok": False,
                                 "error": str(e)})

    return results


# ── Verify (check checksum) ───────────────────────────────────────────────────

def verify_batch_files(batch_id: str,
                       device_id: Optional[int] = None) -> list[dict]:
    """
    Re-hash the files for a batch and compare against stored checksums.

    Returns a list of verification result dicts:
        {
          "file_path":    str,
          "file_type":    "excel" | "wal",
          "stored_hash":  str,
          "actual_hash":  str,
          "match":        bool,
          "sealed_at":    str,
          "sealed_by":    str,
          "error":        str or None,
        }

    "match": True  → file is unmodified since sealing.
    "match": False → file has been altered or is missing.
    """
    results = []

    try:
        where = "WHERE batch_id = ?"
        params: list = [batch_id]
        if device_id is not None:
            where += " AND device_id = ?"
            params.append(device_id)

        with get_conn_ctx() as conn:
            rows = conn.execute(f"""
                SELECT file_type, file_path, sha256, timestamp, username
                FROM file_integrity
                {where}
                ORDER BY id ASC
            """, params).fetchall()

    except Exception as e:
        log.error("verify_batch_files: DB query failed — %s", e)
        return [{"error": str(e), "match": False}]

    for row in rows:
        fpath       = row["file_path"]
        stored_hash = row["sha256"]
        result = {
            "file_path":   fpath,
            "file_type":   row["file_type"],
            "stored_hash": stored_hash,
            "actual_hash": "",
            "match":       False,
            "sealed_at":   row["timestamp"],
            "sealed_by":   row["username"],
            "error":       None,
        }

        if not os.path.exists(fpath):
            result["error"] = "File not found"
            results.append(result)
            continue

        try:
            actual_hash       = sha256_file(fpath)
            result["actual_hash"] = actual_hash
            result["match"]       = (actual_hash == stored_hash)
        except Exception as e:
            result["error"] = str(e)

        results.append(result)

    return results


# ── Query stored checksums ────────────────────────────────────────────────────

def get_integrity_records(batch_id: Optional[str] = None,
                          limit: int = 200) -> list[dict]:
    """
    Retrieve stored file integrity records, most recent first.
    If batch_id is provided, filter to that batch only.
    """
    try:
        if batch_id:
            with get_conn_ctx() as conn:
                rows = conn.execute("""
                    SELECT * FROM file_integrity
                    WHERE batch_id = ?
                    ORDER BY id DESC LIMIT ?
                """, (batch_id, limit)).fetchall()
        else:
            with get_conn_ctx() as conn:
                rows = conn.execute("""
                    SELECT * FROM file_integrity
                    ORDER BY id DESC LIMIT ?
                """, (limit,)).fetchall()

        return [dict(r) for r in rows]

    except Exception as e:
        log.error("get_integrity_records() DB error: %s", e)
        return []


# ── Orphaned WAL detection (Item 7) ──────────────────────────────────────────

def find_orphaned_wals(device_wal_dirs: dict[int, str]) -> list[dict]:
    """
    Scan each device's WAL directory for WAL CSV files that were never
    sealed — i.e. batches interrupted by a crash or power loss before
    close_batch() could run.

    Parameters
    ----------
    device_wal_dirs : {device_id: wal_directory_path}

    Returns a list of dicts:
        [{"device_id": 1, "wal_path": "...", "batch_hint": "WAL_XYZ_..."}]
    """
    import os

    orphans = []
    try:
        with get_conn_ctx() as conn:
            sealed_paths = {
                row["file_path"]
                for row in conn.execute(
                    "SELECT file_path FROM file_integrity "
                    "WHERE LOWER(file_type) = 'wal'"
                ).fetchall()
            }
    except Exception as e:
        log.error("find_orphaned_wals: DB query failed — %s", e)
        return []

    for device_id, wal_dir in device_wal_dirs.items():
        if not os.path.isdir(wal_dir):
            continue
        for fname in os.listdir(wal_dir):
            if not (fname.startswith("WAL_") and fname.endswith(".csv")):
                continue
            fpath = os.path.join(wal_dir, fname)
            if fpath not in sealed_paths:
                orphans.append({
                    "device_id":  device_id,
                    "wal_path":   fpath,
                    "batch_hint": fname,
                })

    if orphans:
        log.warning("find_orphaned_wals: %s unsealed WAL(s) found", len(orphans))
    return orphans


def seal_orphaned_wal(user: Optional[User], device_id: int,
                      wal_path: str) -> tuple[bool, str]:
    """
    Seal a single orphaned WAL discovered by find_orphaned_wals().
    Extracts the batch_id from the WAL's first data row so the seal is
    attributed to the correct batch. Excel is NOT rebuilt (the batch was
    never cleanly closed) — the WAL alone is the complete durable record.

    Returns (True, batch_id) on success, (False, error_message) on failure.
    """
    raise RuntimeError(
        "Orphaned WAL sealing is retired. Use controlled legacy import or "
        "authoritative batch reconciliation; CSV/WAL is not an authoritative record."
    )

    import csv
    import os

    if not os.path.exists(wal_path):
        return False, "WAL file not found."

    batch_id = ""
    try:
        with open(wal_path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                batch_id = row.get("batch_id", "")
                break
    except Exception as e:
        return False, f"Could not read WAL: {e}"

    if not batch_id:
        # Fall back to filename-derived id so the seal is still unique
        batch_id = os.path.basename(wal_path)

    results = seal_batch_files(
        user       = user,
        batch_id   = batch_id,
        device_id  = device_id,
        excel_path = None,       # no Excel — batch never cleanly closed
        wal_path   = wal_path,
    )
    ok = any(r.get("ok") for r in results)
    if ok:
        return True, batch_id
    err = "; ".join(str(r.get("error", "")) for r in results) or "seal failed"
    return False, err
