"""Controlled, one-way import of pre-authoritative CSV WAL evidence.

Legacy rows are preserved for review, but are never represented as records
created under the current Part 11 controls.  This module is deliberately not
called by normal acquisition or reporting paths.
"""

import csv
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone

from cfr21.db import get_conn
from cfr21.regulated_records import (_WRITE_LOCK, _append_audit, _require_actor,
                                     _utc_now, RegulatedRecordError,
                                     RegulatedRecordService)
from cfr21.user_manager import User

LIMITATIONS = (
    "Imported from legacy WAL. Source data predates the authoritative database; "
    "identity, signatures, contemporaneous audit trail, and original sequence "
    "controls cannot be retrospectively established."
)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def import_legacy_wal(actor: User, source_path: str, evidence_directory: str,
                      session_id: str, batch_id: str = "") -> dict:
    """Import a legacy CSV with preserved source/hash and reconciliation report.

    Only administrators may invoke this controlled migration boundary.  The
    returned batch is closed, immutable, and classified ``legacy_import``.
    """
    actor = _require_actor(actor, "import_legacy_wal", session_id)
    source_path = os.path.abspath(source_path)
    if not os.path.isfile(source_path):
        raise RegulatedRecordError("Legacy WAL source file was not found.")
    os.makedirs(evidence_directory, exist_ok=True)
    source_hash = _sha256(source_path)
    import_id = str(uuid.uuid4())
    preserved_path = os.path.join(evidence_directory, f"legacy_wal_{source_hash}.csv")
    if not os.path.exists(preserved_path):
        shutil.copy2(source_path, preserved_path)
    if _sha256(preserved_path) != source_hash:
        raise RegulatedRecordError("Preserved legacy source hash verification failed.")
    with open(source_path, "r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise RegulatedRecordError("Legacy WAL contains no data rows.")
    external_batch_id = (batch_id or rows[0].get("batch_id") or "").strip()
    if not external_batch_id:
        raise RegulatedRecordError("A batch ID is required for legacy import.")
    report_path = os.path.join(evidence_directory, f"legacy_import_reconciliation_{import_id}.json")
    service = RegulatedRecordService()
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT id FROM legacy_wal_imports WHERE source_sha256 = ?", (source_hash,)).fetchone()
            if prior:
                raise RegulatedRecordError("This legacy source hash was already imported.")
            regulated_id = str(uuid.uuid4())
            now = _utc_now()
            config_id = service._get_or_create_configuration(conn, actor, json.dumps({"legacy_import": True}))
            operator = rows[0].get("operator_id", "legacy-unknown") or "legacy-unknown"
            product = rows[0].get("product_name", "") or ""
            conn.execute("""INSERT INTO regulated_batches
                (id, external_batch_id, operator_id, product_name, state, configuration_json,
                 configuration_version_id, created_at, created_by, started_at, stopped_at, stopped_by)
                VALUES (?, ?, ?, ?, 'closed', ?, ?, ?, ?, ?, ?, ?)""",
                (regulated_id, external_batch_id, operator, product, json.dumps({"legacy_import": True}),
                 config_id, now, actor.username, now, now, actor.username))
            summary = {}
            for index, row in enumerate(rows, 1):
                device = int(row.get("device_id") or 1)
                status = row.get("status", "FAIL").upper()
                if status not in ("PASS", "FAIL"):
                    status = "FAIL"
                sequence = int(row.get("read_id") or index)
                recipe = service._get_or_create_recipe(conn, actor, row.get("master_data", ""))
                device_ref = service._get_or_create_device(conn, actor, device, "legacy-import")
                conn.execute("""INSERT INTO scan_records
                    (id, batch_id, device_id, sequence_no, recorded_at, raw_data, master_data, status,
                     operator_id, product_name, created_by, session_id, recipe_version_id,
                     device_registry_id, record_classification, legacy_import_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'legacy_import', ?)""",
                    (str(uuid.uuid4()), regulated_id, device, sequence,
                     row.get("timestamp") or now, row.get("raw_data", ""), row.get("master_data", ""), status,
                     row.get("operator_id") or operator, row.get("product_name") or product,
                     actor.username, session_id, recipe, device_ref, import_id))
                item = summary.setdefault(str(device), {"rows": 0, "pass": 0, "fail": 0})
                item["rows"] += 1; item[status.lower()] += 1
            conn.execute("""INSERT INTO legacy_wal_imports
                (id, imported_at, imported_by, source_path, preserved_path, source_sha256, row_count, limitations, report_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (import_id, now, actor.username, source_path, preserved_path, source_hash, len(rows), LIMITATIONS, report_path))
            _append_audit(conn, actor, "LEGACY_WAL_IMPORTED",
                          f"Legacy WAL imported: source_hash={source_hash}; rows={len(rows)}; batch='{external_batch_id}'.", session_id)
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
    report = {"import_id": import_id, "batch_id": external_batch_id, "source_path": source_path,
              "preserved_path": preserved_path, "source_sha256": source_hash, "row_count": len(rows),
              "report_path": report_path,
              "limitations": LIMITATIONS, "device_summary": summary,
              "imported_at": datetime.now(timezone.utc).isoformat()}
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2, sort_keys=True)
    return report
