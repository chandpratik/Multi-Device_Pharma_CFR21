# core/models.py
# Shared data structures and application-wide constants.
# No dependencies on GUI, hardware, or config — safe to import anywhere.

from dataclasses import dataclass
from datetime import datetime

# ── Camera protocol ───────────────────────────────────────────────────────────
NO_READ_STRING         = "NO_READ"
VALIDATION_FAIL_PREFIX = "Validation Failure"

# ── Logger ────────────────────────────────────────────────────────────────────
FILE_PREFIX            = "ProductionLog"
WAL_PREFIX             = "WAL"

# ── PLC ───────────────────────────────────────────────────────────────────────
DEFAULT_PULSE_MS       = 100


@dataclass
class ReadRecord:
    """
    One scan result. Produced by Datalogger, consumed by GUI and WALExcelLogger.

    Ownership:
        Creoated on the scan loop thread (_run_lop).
        Passed to GUI via pyqtSignal — Qt handles cross-thread dispatch.
        Written to WAL on the scan loop thread before signal emission.
    """
    read_id:      int
    timestamp:    datetime
    raw_data:     str       # decoded barcode or NO_READ_STRING
    master_data:  str       # master code active at time of scan
    status:       str       # "PASS" or "FAIL"
    batch_id:     str
    operator_id:  str
    product_name: str
    device_id:    int       # 1 or 2 — identifies which camera produced this record


@dataclass
class SessionInfo:
    """
    Operator-entered session data.
    Shared across both devices (App 2 design — same session for both).
    Architecture allows per-device sessions in future by holding two SessionInfo objects.
    """
    batch_id:     str = ""
    operator_id:  str = ""
    product_name: str = ""

    def is_valid(self) -> bool:
        return bool(self.batch_id.strip())
