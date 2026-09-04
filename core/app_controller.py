# core/app_controller.py
# AppController — the layer between GUI and hardware.
#
# The GUI never touches datalogger internals directly.
# All hardware operations go through AppController methods.
#
# Owns:
#   - Two Datalogger instances (device 1 and 2)
#   - One shared PLCClient
#   - AppConfig (live reference — updated when settings are saved)
#
# Called from: GUI thread only (except callbacks which fire on scan threads).

import os
import logging
from config.settings import AppConfig
from core.models import SessionInfo, ReadRecord
from core.datalogger import Datalogger
from comms.camera import CameraClient
from comms.plc_modbus import PLCClient
from comms.excel_wal import WALExcelLogger
from cfr21.authorization import AuthorizationError, SessionContext, authorize_session
from cfr21.batch_setup_service import BatchSetupService
from cfr21.device_registry_service import DeviceRegistryService
from cfr21.regulated_records import RegulatedRecordService

log = logging.getLogger("pharma.controller")


class AppController:

    def __init__(self, config: AppConfig):
        self.config = config
        self._regulated_records = RegulatedRecordService()
        self._batch_setup = BatchSetupService()
        self._device_registry = DeviceRegistryService()
        self._cfr_user = None
        self._cfr_session_id = ""
        self._regulated_batch_id = ""
        self._stopped_batch_id = ""

        # ── shared PLC ────────────────────────────────────────────────────────
        self.plc_client = PLCClient(
            ip           = config.plc.ip,
            port         = config.plc.port,
            hreg_device1 = config.plc.hreg_device1,
            hreg_device2 = config.plc.hreg_device2,
            pass_val     = config.plc.pass_val,
            fail_val     = config.plc.fail_val,
            # Delay registers
            d1_reject_hreg  = config.plc.d1_reject_hreg,
            d1_reject_val   = config.plc.d1_reject_val,
            d1_trigger_hreg = config.plc.d1_trigger_hreg,
            d1_trigger_val  = config.plc.d1_trigger_val,
            # Timing & status
            cyl_timing_hreg  = config.plc.cyl_timing_hreg,
            cyl_timing_val   = config.plc.cyl_timing_val,
            cam1_status_hreg = config.plc.cam1_status_hreg,
            # Spare
            spare1_hreg = config.plc.spare1_hreg, spare1_val = config.plc.spare1_val,
            spare2_hreg = config.plc.spare2_hreg, spare2_val = config.plc.spare2_val,
            spare3_hreg = config.plc.spare3_hreg, spare3_val = config.plc.spare3_val,
            spare4_hreg = config.plc.spare4_hreg, spare4_val = config.plc.spare4_val,
        )

        # ── per-device loggers ────────────────────────────────────────────────
        self._loggers: dict[int, Datalogger] = {
            1: self._make_logger(1),
            2: self._make_logger(2),
        }

    # ── factory ───────────────────────────────────────────────────────────────

    def _make_logger(self, device_id: int) -> Datalogger:
        cfg = self.config.device1 if device_id == 1 else self.config.device2
        wal = WALExcelLogger(
            log_dir   = self.config.device_log_dir(device_id),
            wal_dir   = self.config.device_wal_dir(device_id),
        )
        wal.device_id = device_id   # CFR21: needed for integrity seal
        dl = Datalogger(
            device_id       = device_id,
            camera          = CameraClient(cfg.camera_ip, cfg.camera_port),
            plc             = self.plc_client,
            wal_logger      = wal,
            poll_interval_s = cfg.poll_interval_s,
        )
        dl.consec_fail_limit = self.config.general.consecutive_fail_limit
        dl.regulated_device_source = f"{cfg.camera_ip}:{cfg.camera_port}"
        return dl

    def set_cfr_user(self, user):
        """CFR21: push the logged-in user into both WAL loggers for audit sealing."""
        for logger in self._loggers.values():
            logger.wal_logger.cfr_user = user
            logger.regulated_actor = user

    def set_cfr_session(self, user, session_id: str):
        """Set the authenticated subject used by authoritative writes."""
        self._cfr_user = user
        self._cfr_session_id = session_id or ""
        self.set_cfr_user(user)
        for logger in self._loggers.values():
            logger.regulated_session_id = self._cfr_session_id

    def logger(self, device_id: int) -> Datalogger:
        return self._loggers[device_id]

    def _require_permission(self, permission: str, target: str = ""):
        context = SessionContext.from_user(self._cfr_user, self._cfr_session_id)
        try:
            return authorize_session(context, permission, target=target)
        except AuthorizationError as exc:
            raise RuntimeError("Protected operation denied.") from exc

    # ── camera ────────────────────────────────────────────────────────────────

    def connect_camera(self, device_id: int):
        """Connect camera. Raises on failure — caller shows error dialog."""
        self._require_permission("connect_camera", target=f"camera:{device_id}")
        cfg    = self.config.device1 if device_id == 1 else self.config.device2
        logger = self._loggers[device_id]
        logger.camera_client.ip   = cfg.camera_ip
        logger.camera_client.port = cfg.camera_port
        logger.camera_client.open()
        log.info("Camera %s connected", device_id)

    def disconnect_camera(self, device_id: int):
        """Disconnect camera. Raises if logging is active."""
        self._require_permission("disconnect_camera", target=f"camera:{device_id}")
        if self._loggers[device_id].is_running():
            raise RuntimeError("Stop logging before disconnecting.")
        self._loggers[device_id].camera_client.close()
        log.info("Camera %s disconnected", device_id)

    def camera_socket(self, device_id: int):
        """Return raw socket for license verification."""
        return self._loggers[device_id].camera_client._sock

    def camera_is_open(self, device_id: int) -> bool:
        return self._loggers[device_id].camera_client.is_open

    # ── PLC ───────────────────────────────────────────────────────────────────

    def connect_plc(self):
        """Connect shared PLC. Raises on failure."""
        self._require_permission("connect_plc", target="plc")
        plc = self.config.plc
        self.plc_client.ip           = plc.ip
        self.plc_client.port         = plc.port
        self.plc_client.hreg_device1 = plc.hreg_device1
        self.plc_client.hreg_device2 = plc.hreg_device2
        self.plc_client.pass_val     = plc.pass_val
        self.plc_client.fail_val     = plc.fail_val
        self.plc_client.open()
        for logger in self._loggers.values():
            logger.plc_enabled = True
        log.info("PLC connected")

    def disconnect_plc(self):
        """Disconnect PLC. Raises if logging is active."""
        self._require_permission("disconnect_plc", target="plc")
        if any(l.is_running() for l in self._loggers.values()):
            raise RuntimeError("Stop logging before disconnecting PLC.")
        self.plc_client.close()
        for logger in self._loggers.values():
            logger.plc_enabled = False
        log.info("PLC disconnected")

    def plc_is_open(self) -> bool:
        return self.plc_client.is_open

    # ── session ───────────────────────────────────────────────────────────────

    def set_session(self, session: SessionInfo):
        for logger in self._loggers.values():
            logger.session = session
        log.info("Session set: batch=%s operator=%s product=%s",
                 session.batch_id, session.operator_id, session.product_name)

    # ── logging ───────────────────────────────────────────────────────────────

    def start_logging(self):
        """Start both devices. Applies current config before starting."""
        actor = self._require_permission("start_logging", target="logging")
        self._apply_config()
        session = self._loggers[1].session
        self._regulated_batch_id = self._regulated_records.start_or_resume_batch(
            actor=actor,
            external_batch_id=session.batch_id,
            operator_id=session.operator_id,
            product_name=session.product_name,
            configuration=self.config,
            session_id=self._cfr_session_id,
        )
        self._start_regulated_loggers()
        log.info("Logging started — both devices")

    def prepare_controlled_batch(self, configuration_version_id: str,
                                 recipe_version_id: str,
                                 device_registry_ids: list[str],
                                 reason: str) -> str:
        """Create/configure a draft batch before any acquisition thread starts."""
        session = self._loggers[1].session
        actor = self._require_permission("start_logging", target=session.batch_id)
        batch_id = self._batch_setup.create_draft(
            actor, self._cfr_session_id, session.batch_id, session.operator_id,
            session.product_name, configuration_version_id, recipe_version_id)
        for device_id in device_registry_ids:
            self._device_registry.assign_device(
                actor, self._cfr_session_id, batch_id, device_id, reason)
        status = self._regulated_records.get_batch_status(batch_id)
        self._batch_setup.configure_batch(
            actor, self._cfr_session_id, batch_id, status["version"], reason)
        return batch_id

    def start_prepared_batch(self, batch_id: str, reason: str = "") -> None:
        """Activate a configured controlled batch and then start hardware IO."""
        actor = self._require_permission("start_logging", target=f"batch:{batch_id}")
        status = self._regulated_records.get_batch_status(batch_id)
        self._batch_setup.activate_batch(
            actor, self._cfr_session_id, batch_id, status["version"], reason)
        self._apply_config()
        self._regulated_batch_id = batch_id
        self._start_regulated_loggers()

    def review_batch(self, batch_id: str, reason: str) -> int:
        """Record an authorized review after stop or reconciliation."""
        status = self._regulated_records.get_batch_status(batch_id)
        return self._regulated_records.transition_batch(
            self._cfr_user, batch_id, "reviewed", status["version"],
            self._cfr_session_id, reason)

    def release_batch(self, batch_id: str, reason: str) -> int:
        """Release a reviewed batch; only released batches can be closed."""
        status = self._regulated_records.get_batch_status(batch_id)
        return self._regulated_records.transition_batch(
            self._cfr_user, batch_id, "released", status["version"],
            self._cfr_session_id, reason)

    def recover_interrupted_batch(self, reason: str):
        """Reconcile the current session batch, then restart acquisition.

        The service rechecks the authenticated user and records the reason and
        per-device count/sequence evidence atomically before this starts IO.
        """
        session = self._loggers[1].session
        actor = self._require_permission("recover_batches", target=session.batch_id)
        self._apply_config()
        self._regulated_batch_id, _ = self._regulated_records.reconcile_and_resume_batch(
            actor, session.batch_id, reason, self._cfr_session_id)
        self._start_regulated_loggers()
        return self._regulated_batch_id

    def pending_reconciliations(self) -> list[dict]:
        return self._regulated_records.get_pending_reconciliations()

    def _start_regulated_loggers(self):
        for logger in self._loggers.values():
            logger.regulated_records = self._regulated_records
            logger.regulated_batch_id = self._regulated_batch_id
            logger.wal_logger.regulated_records = self._regulated_records
            logger.wal_logger.regulated_batch_id = self._regulated_batch_id
            logger.start()

    def stop_logging(self):
        """Stop both devices — scan threads only. Call close_batch() separately."""
        actor = self._require_permission("stop_logging", target="logging")
        for logger in self._loggers.values():
            if logger.is_running():
                logger.stop()
        if self._regulated_batch_id:
            self._regulated_records.stop_batch(
                actor, self._regulated_batch_id, self._cfr_session_id)
            self._stopped_batch_id = self._regulated_batch_id
            self._regulated_batch_id = ""
        log.info("Logging stopped — both devices")

    def close_batch(self, progress_callback=None):
        """
        Build Excel and seal SHA-256 checksums for all active loggers.
        Called from a background QThread so the GUI stays responsive.
        progress_callback(current, total) is forwarded to WALExcelLogger.
        """
        actor = self._require_permission("close_batch", target="batch")
        loggers = list(self._loggers.values())
        total_loggers = len(loggers)
        for idx, logger in enumerate(loggers):
            def _cb(current, total, _idx=idx, _total_loggers=total_loggers):
                if progress_callback and total > 0:
                    # Scale each logger's progress across its share of 100%
                    overall = int(
                        (_idx / _total_loggers + (current / total) / _total_loggers)
                        * 100
                    )
                    progress_callback(overall, 100)
            logger.wal_logger.close_batch(progress_callback=_cb)
        if self._stopped_batch_id:
            self._regulated_records.close_batch(
                actor, self._stopped_batch_id, self._cfr_session_id)
            self._stopped_batch_id = ""
        log.info("close_batch() complete — Excel built and files sealed")

    def is_running(self) -> bool:
        return any(l.is_running() for l in self._loggers.values())

    # ── teach ─────────────────────────────────────────────────────────────────

    def arm_teach(self, device_id: int):
        self._require_permission("set_master_code", target=f"device:{device_id}")
        self._reject_active_batch_change("arm teach mode")
        self._loggers[device_id].arm_teach()

    def clear_master(self, device_id: int):
        self._require_permission("clear_master_code", target=f"device:{device_id}")
        self._reject_active_batch_change("clear a master code")
        self._loggers[device_id].clear_master()



    def apply_new_config(self, new_config: AppConfig):
        """Update live config. Changes apply on next Start Logging."""
        self._require_permission("change_settings", target="configuration")
        self._reject_active_batch_change("apply configuration changes")
        self.config = new_config
        log.info("Config updated — changes apply on next Start Logging")

    def _reject_active_batch_change(self, action: str) -> None:
        """Keep production inputs stable for the lifetime of an active batch."""
        if self._regulated_batch_id or self.is_running():
            raise RuntimeError(f"Cannot {action} while a regulated batch is active.")

    def _apply_config(self):
        """Push current config into loggers before starting."""
        for device_id, logger in self._loggers.items():
            cfg = self.config.device1 if device_id == 1 else self.config.device2
            logger.camera_client.ip      = cfg.camera_ip
            logger.camera_client.port    = cfg.camera_port
            logger.poll_interval_s       = cfg.poll_interval_s
            logger.regulated_device_source = f"{cfg.camera_ip}:{cfg.camera_port}"
            logger.wal_logger.log_dir    = self.config.device_log_dir(device_id)
            logger.wal_logger.wal_dir    = self.config.device_wal_dir(device_id)
            logger.consec_fail_limit     = self.config.general.consecutive_fail_limit
            # CFR21: preserve cfr_user — _apply_config must not reset it to None
            logger.wal_logger.device_id  = device_id   # always re-affirm
            os.makedirs(logger.wal_logger.log_dir, exist_ok=True)
            os.makedirs(logger.wal_logger.wal_dir, exist_ok=True)

        plc = self.config.plc
        self.plc_client.ip           = plc.ip
        self.plc_client.port         = plc.port
        self.plc_client.hreg_device1 = plc.hreg_device1
        self.plc_client.hreg_device2 = plc.hreg_device2
        self.plc_client.pass_val     = plc.pass_val
        self.plc_client.fail_val     = plc.fail_val
        # Delay registers
        self.plc_client.d1_reject_hreg  = plc.d1_reject_hreg
        self.plc_client.d1_reject_val   = plc.d1_reject_val
        self.plc_client.d1_trigger_hreg = plc.d1_trigger_hreg
        self.plc_client.d1_trigger_val  = plc.d1_trigger_val
        # Timing & status
        self.plc_client.cyl_timing_hreg  = plc.cyl_timing_hreg
        self.plc_client.cyl_timing_val   = plc.cyl_timing_val
        self.plc_client.cam1_status_hreg = plc.cam1_status_hreg
        # Spare
        self.plc_client.spare1_hreg = plc.spare1_hreg; self.plc_client.spare1_val = plc.spare1_val
        self.plc_client.spare2_hreg = plc.spare2_hreg; self.plc_client.spare2_val = plc.spare2_val
        self.plc_client.spare3_hreg = plc.spare3_hreg; self.plc_client.spare3_val = plc.spare3_val
        self.plc_client.spare4_hreg = plc.spare4_hreg; self.plc_client.spare4_val = plc.spare4_val

    def set_camera_connected(self, device_id: int, connected: bool):
        """Notify PLC of camera connection state. Writes cam1_status_hreg."""
        if self.plc_client.is_open:
            self.plc_client.set_camera_connected(device_id, connected)

    def get_wal_counts_for_batch(self, batch_id: str) -> dict[int, tuple[int, int]]:
        """
        Search each device's WAL directory for an existing WAL matching batch_id.
        Returns {device_id: (pass_count, fail_count)} for each device.
        A result of (0, 0) means either no WAL found or the WAL is empty.
        Used for counter restore after a crash or emergency shutdown.
        """
        result = self._regulated_records.get_batch_scan_counts(batch_id)
        for device_id in self._loggers:
            result.setdefault(device_id, (0, 0))
        return result
