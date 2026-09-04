# comms/camera.py
# Passive TCP listener for Cognex DataMan 280/290 on Telnet port 23.
#
# Camera protocol (confirmed from DataMan Setup Tool):
#   Trigger mode : Hardware (rising edge, Input 0) — NOT software triggered
#   Good read    : "<pharma_code>\r\n"
#   No read      : camera sends NOTHING — silence = no read
#   Our role     : TCP client, connect once, listen forever, never send
#
# listen_once(timeout_s):
#   Blocks up to timeout_s for one complete CRLF-terminated line.
#   Returns (code_string, latency_ms) on data, (None, elapsed_ms) on timeout.
#
# Thread ownership:
#   open() / close() — called from GUI thread or controller
#   listen_once()    — called exclusively from scan loop thread (_run_loop)
#   _lock protects _sock for the is_open check and close() path.
#   The race window between the two lock acquisitions in listen_once() is
#   accepted — consequence is a caught ConnectionError, not a crash.

import time
import socket
import threading
import random
import logging
from typing import Optional

log = logging.getLogger("pharma.camera")


class CameraClient:

    def __init__(self, ip: str, port: int):
        self.ip   = ip
        self.port = port
        self._sock:   Optional[socket.socket] = None
        self._rx_buf  = bytearray()
        self._lock    = threading.Lock()
        self.connect_timeout_s = 5.0

    # ── connection ────────────────────────────────────────────────────────────

    def open(self):
        """Connect to camera Telnet server. Raises on failure."""
        with self._lock:
            if self._sock is not None:
                return
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.connect_timeout_s)
            s.connect((self.ip, self.port))
            s.setblocking(False)
            try:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            self._sock = s
            self._rx_buf.clear()
        log.info("Camera connected → %s:%s", self.ip, self.port)

    def close(self):
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
            self._rx_buf.clear()
        log.info("Camera disconnected → %s:%s", self.ip, self.port)

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    # ── internal receive ──────────────────────────────────────────────────────

    def _recv_chunk(self) -> bool:
        try:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Camera closed connection")
            self._rx_buf.extend(chunk)
            return True
        except BlockingIOError:
            return False
        except ConnectionError:
            raise
        except Exception as e:
            raise ConnectionError(f"Socket error: {e}")

    def _extract_line(self) -> Optional[str]:
        for terminator in (b"\r\n", b"\n", b"\r"):
            idx = self._rx_buf.find(terminator)
            if idx != -1:
                line = bytes(self._rx_buf[:idx])
                del self._rx_buf[:idx + len(terminator)]
                decoded = line.decode("ascii", errors="ignore").strip()
                return decoded if decoded else None
        return None

    # ── public API ────────────────────────────────────────────────────────────

    def listen_once(self, timeout_s: float = 2.0) -> tuple[Optional[str], float]:
        """
        Wait up to timeout_s for one complete line from the camera.
        Returns (code_string, latency_ms) or (None, elapsed_ms) on timeout.
        """
        with self._lock:
            if self._sock is None:
                return None, 0.0

        t0       = time.perf_counter()
        deadline = t0 + timeout_s

        while True:
            now = time.perf_counter()
            if now >= deadline:
                return None, (now - t0) * 1000.0

            line = self._extract_line()
            if line is not None:
                latency_ms = (time.perf_counter() - t0) * 1000.0
                log.debug("Camera RX: %r  latency=%.1f ms", line, latency_ms)
                return line, latency_ms

            try:
                with self._lock:
                    self._recv_chunk()
            except ConnectionError:
                self.close()
                return None, (time.perf_counter() - t0) * 1000.0

            time.sleep(0.005)

    def drain(self):
        """Discard buffered bytes — call before starting a new batch."""
        with self._lock:
            self._rx_buf.clear()
            if self._sock is not None:
                try:
                    while True:
                        chunk = self._sock.recv(4096)
                        if not chunk:
                            break
                except Exception:
                    pass
        log.debug("Camera RX buffer drained → %s:%s", self.ip, self.port)

