import csv

import cfr21.db as db
from cfr21.legacy_wal_import import import_legacy_wal


def test_legacy_wal_import_preserves_hash_and_marks_records(admin_user, tmp_path):
    source = tmp_path / "legacy.csv"
    with source.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["read_id", "timestamp", "batch_id", "operator_id", "product_name", "raw_data", "master_data", "status"])
        writer.writeheader()
        writer.writerow({"read_id": 1, "timestamp": "2020-01-01T00:00:00+00:00", "batch_id": "LEG-1", "operator_id": "old", "product_name": "Tablet", "raw_data": "A", "master_data": "A", "status": "PASS"})
    result = import_legacy_wal(admin_user, str(source), str(tmp_path / "evidence"), "legacy-session")
    assert result["row_count"] == 1
    with db.get_conn_ctx() as conn:
        scan = conn.execute("SELECT record_classification FROM scan_records").fetchone()
        imported = conn.execute("SELECT source_sha256, row_count FROM legacy_wal_imports").fetchone()
    assert scan["record_classification"] == "legacy_import"
    assert imported["source_sha256"] == result["source_sha256"] and imported["row_count"] == 1
