# license_check.py
# Device authorisation check for Cognex DataMan cameras.
# Called once per camera on connect — reuses the already-open socket
# so no second TCP connection is needed.
#
# To authorise additional cameras, add serial numbers to _AUTHORISED.
#
# Flow:
#   1. Drain any Telnet banner bytes (IAC negotiation on connect).
#   2. Send DMCC GET DEVICE.SERIAL-NUMBER.
#   3. Read until we have 2 newlines (status code + serial value).
#   4. Parse and check against _AUTHORISED.

import socket
import time

_AUTHORISED = [
    "1A2549PP720643",
    "1A2606FM011476",
    "1A2606PP092779",
    # Add Device 2 serial here when available — share it with us
]

_DRAIN_TIMEOUT = 0.2
_READ_TIMEOUT  = 0.8


def _fetch_serial_from_socket(sock: socket.socket) -> str:
    """
    Issue a DMCC serial-number query on an already-connected socket.
    Returns the raw response string.
    """
    original_timeout = sock.gettimeout()
    try:
        # Drain Telnet banner
        sock.settimeout(_DRAIN_TIMEOUT)
        try:
            while True:
                banner = sock.recv(4096)
                if not banner:
                    break
        except socket.timeout:
            pass

        # Send DMCC command
        sock.sendall(b"||>GET DEVICE.SERIAL-NUMBER\r\n")

        # Read response
        sock.settimeout(_READ_TIMEOUT)
        buf = bytearray()
        deadline = time.monotonic() + _READ_TIMEOUT
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if buf.count(b"\n") >= 2:
                    break
            except socket.timeout:
                break

    finally:
        sock.settimeout(original_timeout)

    return buf.decode("ascii", errors="ignore").strip()


def verify_socket(sock: socket.socket) -> tuple[bool, str]:
    """
    Check camera serial number using an already-open socket.
    Preferred path — avoids opening a second TCP connection to port 23.
    Returns (True, serial) if authorised, (False, reason) if not.
    """
    try:
        raw    = _fetch_serial_from_socket(sock)
        lines  = [l.strip() for l in raw.replace("\r", "\n").split("\n")
                  if l.strip()]
        serial = lines[-1] if lines else ""

        if serial in _AUTHORISED:
            return True, serial
        else:
            return False, f"Unlicensed device.\nSerial: {serial or 'Unknown'}"

    except Exception as e:
        return False, f"Could not verify device.\n{e}"


def verify(ip: str, port: int = 23) -> tuple[bool, str]:
    """
    Fallback: open a fresh TCP connection and check serial.
    Only used if no open socket is available.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect((ip, port))
        raw    = _fetch_serial_from_socket(s)
        s.close()
        lines  = [l.strip() for l in raw.replace("\r", "\n").split("\n")
                  if l.strip()]
        serial = lines[-1] if lines else ""

        if serial in _AUTHORISED:
            return True, serial
        else:
            return False, f"Unlicensed device.\nSerial: {serial or 'Unknown'}"

    except Exception as e:
        return False, f"Could not verify device.\n{e}"