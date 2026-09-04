# comms/plc_modbus.py
# Modbus/TCP client for PLC rejection mechanism.
# One shared PLC, two registers — one per device.
#
# Thread ownership:
#   open() / close()    — GUI thread
#   write_result()      — called from _plc_worker threads (one per device)
#   _refresh_loop()     — background daemon thread, runs every 15 seconds
#   _lock protects the shared _client handle across all threads.
#
# Register categories:
#   Pass/Fail   — written on every scan result (hreg_device1, hreg_device2)
#   Static      — written once on connect, then refreshed every 15 seconds
#                 so PLC power cycles / restarts don't lose values
#   Cam status  — written 1 on camera connect, 0 on disconnect

import logging
import threading
import time
from typing import Optional

from core.models import DEFAULT_PULSE_MS

log = logging.getLogger("pharma.plc")

try:
    from pymodbus.client import ModbusTcpClient
    from pymodbus.exceptions import ModbusException
    PYMODBUS_AVAILABLE = True
except ImportError:
    PYMODBUS_AVAILABLE = False
    log.warning("pymodbus not installed — PLC communication disabled. "
                "Run: pip install pymodbus")

_REFRESH_INTERVAL_S = 15   # how often to rewrite static registers


class PLCClient:
    """
    Shared Modbus/TCP client for both devices.

    Static registers (delay, timing, spare, cam status) are written on
    connect and refreshed every 15 seconds via a background thread. This
    ensures PLC power cycles or program restarts don't silently lose the
    configured values, which would disable rejections.
    """

    PULSE_MS = DEFAULT_PULSE_MS

    def __init__(self, ip: str, port: int,
                 # Pass/Fail
                 hreg_device1: int = 0,
                 hreg_device2: int = 1,
                 pass_val: int = 0,
                 fail_val: int = 1,
                 # Delay registers
                 d1_reject_hreg:  int = -1, d1_reject_val:  int = 0,
                 d1_trigger_hreg: int = -1, d1_trigger_val: int = 0,
                 # Timing & status
                 cyl_timing_hreg:  int = -1, cyl_timing_val:  int = 0,
                 cam1_status_hreg: int = -1,
                 # Spare
                 spare1_hreg: int = -1, spare1_val: int = 0,
                 spare2_hreg: int = -1, spare2_val: int = 0,
                 spare3_hreg: int = -1, spare3_val: int = 0,
                 spare4_hreg: int = -1, spare4_val: int = 0):

        self.ip           = ip
        self.port         = port

        # Pass/Fail
        self.hreg_device1 = hreg_device1
        self.hreg_device2 = hreg_device2
        self.pass_val     = pass_val
        self.fail_val     = fail_val

        # Delay
        self.d1_reject_hreg  = d1_reject_hreg
        self.d1_reject_val   = d1_reject_val
        self.d1_trigger_hreg = d1_trigger_hreg
        self.d1_trigger_val  = d1_trigger_val

        # Timing & status
        self.cyl_timing_hreg  = cyl_timing_hreg
        self.cyl_timing_val   = cyl_timing_val
        self.cam1_status_hreg = cam1_status_hreg
        self._cam1_status_val = 0   # tracks current camera connected state

        # Spare
        self.spare1_hreg = spare1_hreg; self.spare1_val = spare1_val
        self.spare2_hreg = spare2_hreg; self.spare2_val = spare2_val
        self.spare3_hreg = spare3_hreg; self.spare3_val = spare3_val
        self.spare4_hreg = spare4_hreg; self.spare4_val = spare4_val

        self._client: Optional[object] = None
        self._lock   = threading.Lock()

        # Refresh thread
        self._refresh_stop = threading.Event()
        self._refresh_thread: Optional[threading.Thread] = None

    # ── connection ────────────────────────────────────────────────────────────

    def open(self):
        if not PYMODBUS_AVAILABLE:
            raise RuntimeError("pymodbus not installed — run: pip install pymodbus")
        with self._lock:
            if self._client is not None:
                return
            client = ModbusTcpClient(host=self.ip, port=self.port, timeout=3)
            if not client.connect():
                raise ConnectionError(
                    f"Modbus/TCP connect failed → {self.ip}:{self.port}")
            self._client = client
        log.info("PLC connected → %s:%s", self.ip, self.port)

        # Write all static registers immediately on connect
        self._write_static_registers()

        # Start background refresh thread
        self._refresh_stop.clear()
        self._refresh_thread = threading.Thread(
            target=self._refresh_loop,
            name="plc-refresh",
            daemon=True,
        )
        self._refresh_thread.start()

    def close(self):
        # Stop refresh thread first
        self._refresh_stop.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=5.0)
            self._refresh_thread = None

        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception as e:
                    log.warning("PLC close error: %s", e)
                self._client = None
        log.info("PLC disconnected")

    @property
    def is_open(self) -> bool:
        return self._client is not None

    # ── camera status ─────────────────────────────────────────────────────────

    def set_camera_connected(self, device_id: int, connected: bool):
        """
        Write camera connected status to cam1_status_hreg.
        Called by AppController when camera connects or disconnects.
        device_id is kept for future multi-camera status registers.
        """
        if self.cam1_status_hreg < 0:
            return   # not configured
        val = 1 if connected else 0
        self._cam1_status_val = val
        self._write_single(self.cam1_status_hreg, val)
        log.info("cam1_status_hreg[%s] = %s (camera %s)",
                 self.cam1_status_hreg, val,
                 "connected" if connected else "disconnected")

    # ── Pass/Fail pulse ───────────────────────────────────────────────────────

    def write_fail(self, device_id: int) -> bool:
        """
        Fire a FAIL reject pulse on the register for the given device.
        Called from the per-device _plc_worker thread.
        """
        hreg = self.hreg_device1 if device_id == 1 else self.hreg_device2
        return self._pulse_register(hreg)

    def _pulse_register(self, hreg: int) -> bool:
        """
        Rising-edge FAIL pulse (FC06).
        Sleep is now OUTSIDE the lock so Device 2 rejects are not
        blocked for 100ms while Device 1's sleep runs.
        """
        # Acquire lock only for the write operations, not the sleep
        with self._lock:
            if self._client is None:
                log.warning("PLC write skipped — not connected")
                return False
            try:
                result = self._client.write_register(hreg, self.fail_val)
                if result.isError():
                    log.error("PLC write FAIL error: %s", result)
                    return False
            except (ModbusException, Exception) as e:
                log.error("PLC write FAIL exception: %s", e)
                self._safe_close()
                return False

        # Sleep OUTSIDE the lock — other threads can write during this time
        time.sleep(self.PULSE_MS / 1000.0)

        with self._lock:
            if self._client is None:
                return False   # disconnected during sleep
            try:
                result = self._client.write_register(hreg, self.pass_val)
                if result.isError():
                    log.error("PLC write RESET error: %s", result)
                    return False
                log.info("PLC reject pulse OK — hreg[%s] %s→%s",
                         hreg, self.fail_val, self.pass_val)
                return True
            except (ModbusException, Exception) as e:
                log.error("PLC write RESET exception: %s", e)
                self._safe_close()
                return False

    # ── Static register refresh ───────────────────────────────────────────────

    def _refresh_loop(self):
        """
        Background thread — rewrites all static registers every 15 seconds.

        PLCs lose holding register values on:
          - Power cycle
          - Program restart
          - Communication fault reset

        Without this refresh, delay and timing values go to zero after a
        PLC restart mid-shift and rejections stop working correctly.
        """
        log.info("PLC refresh loop started (interval: %ss)", _REFRESH_INTERVAL_S)
        while not self._refresh_stop.wait(timeout=_REFRESH_INTERVAL_S):
            if self._client is None:
                continue
            log.debug("PLC refresh — rewriting static registers")
            self._write_static_registers()
        log.info("PLC refresh loop stopped")

    def _write_static_registers(self):
        """Write all non-(-1) static registers in one pass."""
        static = [
            (self.d1_reject_hreg,  self.d1_reject_val,  "d1_reject"),
            (self.d1_trigger_hreg, self.d1_trigger_val, "d1_trigger"),
            (self.cyl_timing_hreg, self.cyl_timing_val, "cyl_timing"),
            (self.cam1_status_hreg, self._cam1_status_val, "cam1_status"),
            (self.spare1_hreg, self.spare1_val, "spare1"),
            (self.spare2_hreg, self.spare2_val, "spare2"),
            (self.spare3_hreg, self.spare3_val, "spare3"),
            (self.spare4_hreg, self.spare4_val, "spare4"),
        ]
        for hreg, val, name in static:
            if hreg >= 0:
                self._write_single(hreg, val, label=name)

    def _write_single(self, hreg: int, val: int, label: str = "") -> bool:
        """Write one holding register. Returns True on success."""
        with self._lock:
            if self._client is None:
                return False
            try:
                result = self._client.write_register(hreg, val)
                if result.isError():
                    log.warning("PLC write_single error [%s] hreg=%s val=%s: %s",
                                label, hreg, val, result)
                    return False
                log.debug("PLC write_single OK [%s] hreg=%s val=%s", label, hreg, val)
                return True
            except (ModbusException, Exception) as e:
                log.error("PLC write_single exception [%s]: %s", label, e)
                self._safe_close()
                return False

    def _safe_close(self):
        """Close without acquiring lock — only call when lock is already held."""
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass
        self._client = None
