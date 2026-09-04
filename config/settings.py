# config/settings.py
# Typed application configuration using dataclasses.
# Replaces the hand-rolled _inputs dict + JSON approach from App 1.
#
# AppConfig is the single object that holds all settings.
# Load with AppConfig.load(), save with cfg.save().
# All fields are typed — no silent string/int confusion.
#
# Persistence: settings.json next to the executable (same location as App 1).
# Missing keys fall back to dataclass defaults silently.
# Invalid types are logged and the default is kept — never crash on bad config.

import os
import sys
import json
import logging
from dataclasses import dataclass, field, asdict

log = logging.getLogger("pharma.config")

# ── Default values ────────────────────────────────────────────────────────────

_DEFAULT_CAM1_IP    = "192.168.10.10"
_DEFAULT_CAM2_IP    = "192.168.10.11"
_DEFAULT_CAM_PORT   = 23
_DEFAULT_PLC_IP     = "192.168.10.20"
_DEFAULT_PLC_PORT   = 502
_DEFAULT_HREG_D1    = 0
_DEFAULT_HREG_D2    = 1
_DEFAULT_PASS_VAL   = 0
_DEFAULT_FAIL_VAL   = 1
_DEFAULT_POLL_S     = 2.0
# CFR21 Fix 10: _DEFAULT_PASSWORD removed — authentication is now handled
# entirely by compliance.db via cfr21/user_manager.py (bcrypt hashed).
# No password is stored in settings.json.


def _default_log_dir() -> str:
    root = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
           else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, "logs")


# ── Config sections ───────────────────────────────────────────────────────────

@dataclass
class DeviceConfig:
    """Per-camera network settings."""
    camera_ip:        str   = _DEFAULT_CAM1_IP
    camera_port:      int   = _DEFAULT_CAM_PORT
    poll_interval_s:  float = _DEFAULT_POLL_S


@dataclass
class PLCConfig:
    """Shared PLC — one IP, separate registers per device."""
    ip:            str = _DEFAULT_PLC_IP
    port:          int = _DEFAULT_PLC_PORT

    # ── Pass/Fail registers ───────────────────────────────────────────────────
    hreg_device1:  int = _DEFAULT_HREG_D1   # live PASS/FAIL register — device 1
    hreg_device2:  int = _DEFAULT_HREG_D2   # live PASS/FAIL register — device 2
    pass_val:      int = _DEFAULT_PASS_VAL
    fail_val:      int = _DEFAULT_FAIL_VAL

    # ── Delay registers ───────────────────────────────────────────────────────
    # -1 = not configured — refresh loop skips these
    d1_reject_hreg:  int = -1   # rejection delay register
    d1_reject_val:   int = 0
    d1_trigger_hreg: int = -1   # trigger delay register
    d1_trigger_val:  int = 0

    # ── Timing & status registers ─────────────────────────────────────────────
    cyl_timing_hreg:  int = -1   # cylinder timing register
    cyl_timing_val:   int = 0
    cam1_status_hreg: int = -1   # camera connected status (1=connected, 0=disconnected)

    # ── Spare registers ───────────────────────────────────────────────────────
    spare1_hreg: int = -1
    spare1_val:  int = 0
    spare2_hreg: int = -1
    spare2_val:  int = 0
    spare3_hreg: int = -1
    spare3_val:  int = 0
    spare4_hreg: int = -1
    spare4_val:  int = 0


@dataclass
class CompanyConfig:
    """Company identity — printed on all PDF reports."""
    name:    str = "Sun Pharma"
    address: str = ""


@dataclass
class GeneralConfig:
    """Application-wide settings."""
    log_dir:              str = field(default_factory=_default_log_dir)
    backup_destination:   str = ""   # empty = auto (db_backups/ subfolder)
    consecutive_fail_limit: int = 3  # configurable bad-product alarm threshold
    # CFR21 Fix 10: password field removed — authentication is in compliance.db


@dataclass
class PolicyConfig:
    """
    21 CFR Part 11 security policy settings.
    Configurable by Administrator from Advanced Settings.
    These values are passed to SessionManager on startup.
    """
    timeout_minutes:        int = 30    # screen lock inactivity timeout
    password_expiry_days:   int = 90    # 0 = never expires
    max_login_attempts:     int = 3     # failed attempts before lockout
    lockout_minutes:        int = 30    # how long account stays locked
    password_history_count: int = 5     # number of previous passwords to block reuse


@dataclass
class AppConfig:
    """
    Root configuration object.

    Usage:
        cfg = AppConfig.load()          # load from settings.json
        cfg.device1.camera_ip = "..."   # modify
        cfg.save()                      # persist

    Thread safety: AppConfig is read-only after load() in normal operation.
    All writes happen on the GUI thread via Advanced Settings → Save.
    No locking required.
    """
    device1: DeviceConfig = field(default_factory=DeviceConfig)
    device2: DeviceConfig = field(
        default_factory=lambda: DeviceConfig(camera_ip=_DEFAULT_CAM2_IP))
    plc:     PLCConfig    = field(default_factory=PLCConfig)
    general: GeneralConfig = field(default_factory=GeneralConfig)
    policy:  PolicyConfig  = field(default_factory=PolicyConfig)
    company: CompanyConfig = field(default_factory=CompanyConfig)

    # ── file path ─────────────────────────────────────────────────────────────

    @staticmethod
    def _path() -> str:
        root = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
               else os.path.dirname(os.path.abspath(
                   os.path.join(os.path.dirname(__file__), "..")))
        return os.path.join(root, "settings.json")

    # ── load ──────────────────────────────────────────────────────────────────

    @classmethod
    def load(cls) -> "AppConfig":
        """
        Load settings.json into a new AppConfig.
        Missing keys use dataclass defaults.
        Invalid types are skipped with a log warning.
        Never raises — always returns a valid AppConfig.
        """
        cfg = cls()
        path = cls._path()

        if not os.path.exists(path):
            log.info("No settings.json found at %s — using defaults", path)
            return cfg

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log.warning("Could not read settings.json: %s — using defaults", e)
            return cfg

        # ── device1 ───────────────────────────────────────────────────────────
        _apply(cfg.device1, data.get("device1", {}), {
            "camera_ip":       str,
            "camera_port":     int,
            "poll_interval_s": float,
        })

        # ── device2 ───────────────────────────────────────────────────────────
        _apply(cfg.device2, data.get("device2", {}), {
            "camera_ip":       str,
            "camera_port":     int,
            "poll_interval_s": float,
        })

        # ── plc ───────────────────────────────────────────────────────────────
        _apply(cfg.plc, data.get("plc", {}), {
            "ip":           str,
            "port":         int,
            "hreg_device1": int,
            "hreg_device2": int,
            "pass_val":     int,
            "fail_val":     int,
            # Delay registers
            "d1_reject_hreg":  int,
            "d1_reject_val":   int,
            "d1_trigger_hreg": int,
            "d1_trigger_val":  int,
            # Timing & status
            "cyl_timing_hreg":  int,
            "cyl_timing_val":   int,
            "cam1_status_hreg": int,
            # Spare
            "spare1_hreg": int, "spare1_val": int,
            "spare2_hreg": int, "spare2_val": int,
            "spare3_hreg": int, "spare3_val": int,
            "spare4_hreg": int, "spare4_val": int,
        })

        # ── general ───────────────────────────────────────────────────────────
        _apply(cfg.general, data.get("general", {}), {
            "log_dir":                str,
            "backup_destination":     str,
            "consecutive_fail_limit": int,
            # CFR21 Fix 10: password key ignored if present in old settings.json
        })

        # ── policy ────────────────────────────────────────────────────────────
        _apply(cfg.policy, data.get("policy", {}), {
            "timeout_minutes":        int,
            "password_expiry_days":   int,
            "max_login_attempts":     int,
            "lockout_minutes":        int,
            "password_history_count": int,
        })

        # ── company ───────────────────────────────────────────────────────────
        _apply(cfg.company, data.get("company", {}), {
            "name":    str,
            "address": str,
        })

        log.info("Settings loaded from %s", path)
        return cfg

    # ── save ──────────────────────────────────────────────────────────────────

    def save(self) -> bool:
        """
        Write config to settings.json atomically.
        Returns True on success, False on failure.
        Logs errors — never raises.
        """
        path = cls_path = self._path()
        tmp  = path + ".tmp"
        try:
            data = {
                "device1": asdict(self.device1),
                "device2": asdict(self.device2),
                "plc":     asdict(self.plc),
                "general": asdict(self.general),
                "policy":  asdict(self.policy),
                "company": asdict(self.company),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)   # atomic on all platforms
            log.info("Settings saved to %s", path)
            return True
        except Exception as e:
            log.error("Failed to save settings: %s", e)
            return False

    # ── device log dirs ───────────────────────────────────────────────────────

    def device_log_dir(self, device_id: int) -> str:
        """Returns the log directory for a given device (1 or 2)."""
        return os.path.join(self.general.log_dir, f"Device{device_id}")

    def device_wal_dir(self, device_id: int) -> str:
        """Returns the WAL directory for a given device (1 or 2)."""
        return os.path.join(self.device_log_dir(device_id), "wal")


# ── helpers ───────────────────────────────────────────────────────────────────

def _apply(obj, data: dict, schema: dict):
    """
    Apply values from a dict to a dataclass instance.
    Only keys in schema are applied. Type coercion is attempted.
    Invalid values are skipped with a warning — the default is kept.
    """
    for key, typ in schema.items():
        if key not in data:
            continue
        try:
            setattr(obj, key, typ(data[key]))
        except (ValueError, TypeError) as e:
            log.warning("Config key '%s' invalid value '%s': %s — keeping default",
                        key, data[key], e)
