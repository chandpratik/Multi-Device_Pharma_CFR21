# core/datalogger.py
# Per-device scan loop, teach mode, pass/fail decision, consecutive fail alarm.
# One Datalogger instance per camera device.
#
# ── Thread ownership ──────────────────────────────────────────────────────────
#   GUI thread       — start(), stop(), arm_teach(), clear_master()
#   _scan_thread     — _run_loop(): camera reads, PASS/FAIL decisions, WAL writes
#   _plc_thread      — _plc_worker(): Modbus writes, decoupled from scan loop
#
# All callbacks (on_*) are called from _scan_thread.
# GUI wires these to pyqtSignal.emit — Qt dispatches to GUI thread safely.
# Do NOT touch Qt widgets directly from callbacks.
# ─────────────────────────────────────────────────────────────────────────────

import queue
import threading
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

from core.models import (
    ReadRecord, SessionInfo,
    NO_READ_STRING, VALIDATION_FAIL_PREFIX,
)
from comms.camera   import CameraClient
from comms.plc_modbus import PLCClient
from comms.excel_wal  import WALExcelLogger

log = logging.getLogger("pharma.datalogger")

# CFR21: logging.disable() removed — it was silencing CFR21 compliance
# error output from audit_trail.py, db.py etc. which all use the logging
# module to report failures. Scan loop performance is unaffected because
# SQLite writes are direct function calls, not logging calls.
# To suppress console noise during production, configure the log level
# via the root logger in main.py instead (e.g. logging.basicConfig(level=logging.WARNING))

_DISCONNECT_THRESHOLD = 5   # consecutive None returns before declaring disconnect


# ── pass/fail decision (pure functions — unit testable) ───────────────────────

def _extract_code(line: str) -> str:
    """Strip camera prefix and return bare barcode string."""
    if line.startswith(VALIDATION_FAIL_PREFIX):
        return line[len(VALIDATION_FAIL_PREFIX):].strip()
    return line.strip()


def _parse_line(line: str, master: str) -> tuple[str, bool]:
    """
    Determine PASS or FAIL for one camera line.

    Rules:
      Empty or NO_READ      → FAIL
      No master set         → FAIL  (cannot validate without reference)
      code == master        → PASS
      code != master        → FAIL

    Returns (clean_code, is_pass).
    Pure function — no side effects, safe to unit test directly.
    """
    code = line.strip()
    if not code or code == NO_READ_STRING:
        return NO_READ_STRING, False
    if not master:
        return code, False
    return code, (code == master)


# ── Datalogger ────────────────────────────────────────────────────────────────

class Datalogger:
    """
    One instance per camera device.
    AppController creates and holds two of these (device_id 1 and 2).
    """

    def __init__(self,
                 device_id:   int,
                 camera:      CameraClient,
                 plc:         PLCClient,
                 wal_logger:  WALExcelLogger,
                 poll_interval_s: float = 2.0):

        self.device_id       = device_id
        self.camera_client   = camera
        self.plc_client      = plc
        self.wal_logger      = wal_logger
        self.poll_interval_s = poll_interval_s
        self.plc_enabled     = False

        # ── threading ─────────────────────────────────────────────────────────
        self._scan_thread:  Optional[threading.Thread] = None
        self._plc_thread:   Optional[threading.Thread] = None
        self._stop_event    = threading.Event()
        self._plc_queue:    queue.Queue = queue.Queue(maxsize=8)

        # ── teach state ───────────────────────────────────────────────────────
        self._teach_armed = False
        self.master_code: str = ""

        # ── consecutive fail alarm ────────────────────────────────────────────
        self._consec_fails     = 0
        self.consec_fail_limit = 3

        # ── PLC write failure alarm (Item 4) ─────────────────────────────────
        self._plc_consec_write_fails = 0
        self.plc_write_fail_limit    = 3   # alarm after N consecutive failures

        # ── session metadata — set by AppController before start() ────────────
        self.session: SessionInfo = SessionInfo()

        # ── batch runtime state ───────────────────────────────────────────────
        self.batch_started:  Optional[datetime] = None
        self._read_counter:  int                = 0
        self._excel_path:    Optional[str]      = None
        self._wal_path:      Optional[str]      = None

        # Authoritative regulated-record context. AppController sets these
        # before start(); acquisition fails closed when they are absent.
        self.regulated_records = None
        self.regulated_batch_id: str = ""
        self.regulated_actor = None
        self.regulated_session_id: str = ""
        self.regulated_device_source: str = ""

        # ── callbacks (wired by AppController to pyqtSignals) ─────────────────
        # All called from _scan_thread — must be thread-safe.
        self.on_status:            Optional[Callable[[str], None]]                  = None
        self.on_read_logged:       Optional[Callable[[ReadRecord], None]]           = None
        self.on_live_sample:       Optional[Callable[[str, bool, float], None]]     = None
        self.on_teach_done:        Optional[Callable[[str], None]]                  = None
        self.on_consec_fail:       Optional[Callable[[int], None]]                  = None
        self.on_camera_disconnect: Optional[Callable[[], None]]                     = None
        # WAL write failed — record system cannot record. Batch must stop. (Item 3)
        self.on_wal_error:         Optional[Callable[[str], None]]                  = None
        # PLC reject writes failing repeatedly — ejector may not be firing. (Item 4)
        self.on_plc_error:         Optional[Callable[[int], None]]                  = None

    # ── helpers ───────────────────────────────────────────────────────────────

    def _emit(self, fn, *args):
        """Fire a callback safely — swallows exceptions so loop never dies."""
        if fn:
            try:
                fn(*args)
            except Exception as e:
                log.error("Callback error (device %s): %s", self.device_id, e)

    def is_running(self) -> bool:
        return self._scan_thread is not None and self._scan_thread.is_alive()

    # ── teach control (called from GUI thread via AppController) ──────────────

    def arm_teach(self):
        self._teach_armed = True
        log.info("TEACH ARMED — device %s", self.device_id)
        self._emit(self.on_status, "Teach armed — present a good product…")

    def clear_master(self):
        log.info("MASTER CLEARED — device %s (was '%s')",
                 self.device_id, self.master_code)
        self.master_code   = ""
        self._teach_armed  = False
        self._consec_fails = 0
        self._emit(self.on_status, "Master code cleared.")

    # ── start / stop ──────────────────────────────────────────────────────────

    def start(self):
        """Start scan loop. Session must be set before calling."""
        if self.is_running():
            return
        self.batch_started = datetime.now().astimezone()
        self._read_counter = 0

        self._excel_path, self._wal_path = self.wal_logger.start_batch(
            self.session.batch_id, self.batch_started)

        self._emit(self.on_status,
                   f"Batch started: {self.session.batch_id} | "
                   f"Excel: {self._excel_path} | WAL: {self._wal_path}")

        self._stop_event.clear()
        self._scan_thread = threading.Thread(
            target=self._run_loop,
            name=f"scan-device{self.device_id}",
            daemon=True)
        self._scan_thread.start()

        self._plc_thread = threading.Thread(
            target=self._plc_worker,
            name=f"plc-device{self.device_id}",
            daemon=True)
        self._plc_thread.start()

    def stop(self):
        """Stop scan loop and finalize log files."""
        self._stop_event.set()
        self._plc_queue.put(None)   # sentinel — unblocks _plc_worker

        if self._scan_thread:
            self._scan_thread.join(timeout=3.0)
        if self._plc_thread:
            self._plc_thread.join(timeout=3.0)

        # Do NOT close the camera socket here — user may start logging again.
        # Socket is only closed on explicit disconnect or camera disconnect event.

        # Only close the WAL file here — Excel build and SHA-256 seal happen
        # separately in AppController.close_batch() on a background thread
        # with progress reporting. This keeps the scan thread fast.
        self.wal_logger._close_wal()
        self._emit(self.on_status, "Logging stopped.")

    # ── scan loop (runs on _scan_thread) ──────────────────────────────────────

    def _run_loop(self):
        try:
            if not self.camera_client.is_open:
                self.camera_client.open()
            self.camera_client.drain()
        except Exception as e:
            log.error("Camera connect failed (device %s): %s", self.device_id, e)
            self._emit(self.on_status, f"Camera connect failed: {e}")
            return

        self._emit(self.on_status, "Listening for camera reads…")
        _null_streak = 0

        while not self._stop_event.is_set():

            raw, latency_ms = self.camera_client.listen_once(
                timeout_s=self.poll_interval_s)
            _t_received = time.perf_counter()

            if raw is None:
                if not self.camera_client.is_open:
                    _null_streak += 1
                    if _null_streak >= _DISCONNECT_THRESHOLD:
                        log.error("Camera socket closed — device %s", self.device_id)
                        self._emit(self.on_status,
                                   "CAMERA DISCONNECTED — logging suspended.")
                        self._emit(self.on_camera_disconnect)
                        return
                else:
                    _null_streak = 0
                continue

            _null_streak = 0

            # ── teach capture ─────────────────────────────────────────────────
            if self._teach_armed:
                code = _extract_code(raw)
                if code and code != NO_READ_STRING:
                    self._teach_armed  = False
                    self.master_code   = code
                    self._consec_fails = 0
                    _proc_ms = (time.perf_counter() - _t_received) * 1000.0
                    log.info("TEACH CAPTURED — device %s: '%s'",
                             self.device_id, code)
                    self._emit(self.on_teach_done, code)
                    self._emit(self.on_status, f"Master code set: {code}")
                    self._emit(self.on_live_sample, code, True, _proc_ms)
                else:
                    log.warning("TEACH: NoRead — device %s staying armed",
                                self.device_id)
                    self._emit(self.on_status, "Teach: no read — try again…")
                    _proc_ms = (time.perf_counter() - _t_received) * 1000.0
                    self._emit(self.on_live_sample, NO_READ_STRING, False, _proc_ms)
                continue

            # ── pass / fail decision ──────────────────────────────────────────
            code, is_pass = _parse_line(raw, self.master_code)
            _proc_ms = (time.perf_counter() - _t_received) * 1000.0

            self._emit(self.on_live_sample, code, is_pass, _proc_ms)

            # ── consecutive fail tracking ─────────────────────────────────────
            if is_pass:
                self._consec_fails = 0
            else:
                self._consec_fails += 1
                if self._consec_fails >= self.consec_fail_limit:
                    log.warning("CONSEC FAIL ALARM — device %s: %s fails",
                                self.device_id, self._consec_fails)
                    self._emit(self.on_consec_fail, self._consec_fails)
                    self._consec_fails = 0

            # ── PLC reject — enqueue FAIL only ────────────────────────────────
            if self.plc_enabled and self.plc_client.is_open and not is_pass:
                try:
                    self._plc_queue.put_nowait(True)
                except queue.Full:
                    log.warning("PLC queue full — reject dropped, device %s, "
                                "read_id=%s", self.device_id, self._read_counter + 1)

            # ── log record ────────────────────────────────────────────────────
            self._read_counter += 1
            rec = ReadRecord(
                read_id      = self._read_counter,
                timestamp    = datetime.now().astimezone(),
                raw_data     = code,
                master_data  = self.master_code,
                status       = "PASS" if is_pass else "FAIL",
                batch_id     = self.session.batch_id,
                operator_id  = self.session.operator_id,
                product_name = self.session.product_name,
                device_id    = self.device_id,
            )

            try:
                if (self.regulated_records is None or not self.regulated_batch_id
                        or self.regulated_actor is None):
                    raise RuntimeError("Authoritative record service is not configured.")
                _, sequence_no, recorded_at = self.regulated_records.record_scan(
                    actor       = self.regulated_actor,
                    batch_id    = self.regulated_batch_id,
                    device_id   = self.device_id,
                    raw_data    = rec.raw_data,
                    master_data = rec.master_data,
                    status      = rec.status,
                    operator_id = rec.operator_id,
                    product_name= rec.product_name,
                    session_id  = self.regulated_session_id,
                    device_source = self.regulated_device_source,
                )
                # The database sequence and UTC timestamp are authoritative.
                rec.read_id = sequence_no
                rec.timestamp = datetime.fromisoformat(recorded_at)
                self.wal_logger.append_record(rec)
            except Exception as e:
                # Item 3: a record system that cannot record MUST stop loudly.
                # Silent continuation means the screen shows healthy scans
                # while the electronic record diverges from reality.
                log.critical(
                    "WAL append FAILED (device %s): %s — STOPPING LOGGING",
                    self.device_id, e
                )
                self._emit(self.on_wal_error,
                           f"WAL write failed on Device {self.device_id}: {e}")
                self._stop_event.set()   # halt scan loop immediately
                return

            self._emit(self.on_read_logged, rec)

    # ── PLC worker (runs on _plc_thread) ──────────────────────────────────────

    def _plc_worker(self):
        """
        Dedicated thread for PLC reject writes.
        Blocks on queue, fires Modbus pulse on FAIL signal.
        Sentinel (None) stops the worker on stop().
        """
        while True:
            try:
                item = self._plc_queue.get(timeout=1.0)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue

            if item is None:    # sentinel
                break

            if self.plc_enabled and self.plc_client.is_open:
                ok = self.plc_client.write_fail(self.device_id)
                if not ok:
                    self._plc_consec_write_fails += 1
                    log.warning(
                        "PLC worker: reject write failed — device %s "
                        "(%s consecutive)",
                        self.device_id, self._plc_consec_write_fails
                    )
                    # Item 4: repeated PLC failures = ejector may not be
                    # firing. Failed product could be passing through
                    # un-rejected. Alert the operator loudly.
                    if self._plc_consec_write_fails >= self.plc_write_fail_limit:
                        self._emit(self.on_plc_error,
                                   self._plc_consec_write_fails)
                        self._plc_consec_write_fails = 0   # avoid alarm spam
                else:
                    self._plc_consec_write_fails = 0
