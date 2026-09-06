# main.py
# Entry point for Pharma Code Verification v2.
#
# CFR21 additions vs original:
#   1. cfr21.db.initialise()   — creates compliance.db on first run
#   2. SessionManager created  — single instance passed to MainWindow
#   3. LoginDialog shown       — app blocked until valid login
#   4. APP_STARTED / APP_CLOSED logged to audit trail

import sys
import logging

from crash_handler import install as install_crash_handler
install_crash_handler()

# CFR21: configure root logger to WARNING so scan-loop debug noise is
# suppressed in production, while CFR21 ERROR messages still surface.
logging.basicConfig(
    level   = logging.WARNING,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QColor, QPalette

from version import __version__, __app_name__, __company__
from config.settings import AppConfig
from gui.styles import QSS
from gui.widgets import load_bundled_fonts
from gui.main_window import MainWindow

# ── CFR21 ─────────────────────────────────────────────────────────────────────
import cfr21.db as cfr_db
import cfr21.audit_trail as audit
from cfr21.session_manager import SessionManager
from gui.cfr_dialogs import LoginDialog


def _check_db_integrity_on_startup(config):
    """
    Warn if compliance.db appears to have been deleted and recreated.

    Heuristic: audit_trail has 0 records AND backups exist AND the DB file
    is older than 30 seconds (i.e. was not just created by this startup).
    The age check prevents a false alarm on a clean first installation where
    old backup files happen to exist from a previous installation.
    """
    import os
    import time
    from cfr21.db import get_conn_ctx, _db_path
    from cfr21.db_backup import _backup_dir

    try:
        db_file = _db_path()

        # If DB was created within the last 30 seconds it's a fresh install
        if os.path.exists(db_file):
            age_seconds = time.time() - os.path.getctime(db_file)
            if age_seconds < 30:
                return True   # Brand new DB — zero records is expected

        with get_conn_ctx() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM audit_trail"
            ).fetchone()[0]

        chain_ok, chain_message, _ = audit.verify_chain()
        if not chain_ok:
            logging.getLogger("pharma.main").critical(
                "Audit chain verification failed at startup: %s", chain_message)
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(
                    None,
                    "Audit Integrity Failure",
                    "The audit trail failed integrity verification. "
                    "Production use is blocked until the issue is investigated.\n\n"
                    f"{chain_message}",
                )
            except Exception:
                pass
            return False

        if count > 0:
            return True   # Normal — records exist

        # Zero records on an existing DB — check if backups exist
        backup_folder = _backup_dir(
            config.general.log_dir,
            getattr(config.general, "backup_destination", "")
        )
        backups_exist = (
            os.path.isdir(backup_folder) and
            any(f.endswith(".db") for f in os.listdir(backup_folder))
        )

        if backups_exist:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None,
                "⚠️  Compliance Database Warning",
                "The compliance database contains NO audit records,\n"
                "but backup files were found.\n\n"
                "This may mean the database was deleted and recreated —\n"
                "which would constitute a loss of the complete audit trail.\n\n"
                "ACTION REQUIRED:\n"
                "  1. Do NOT use this system for production until investigated.\n"
                "  2. Contact your system administrator immediately.\n"
                "  3. Restore from backup if records are missing.\n\n"
                f"Backup location:\n{backup_folder}"
            )
            logging.getLogger("pharma.main").critical(
                "TAMPER ALERT: audit_trail empty but backups exist at %s",
                backup_folder
            )
            return False

    except Exception as e:
        logging.getLogger("pharma.main").error(
            "Startup DB integrity check failed: %s", e
        )
        return False
    return True


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(f"{__company__} – {__app_name__}")
    app.setApplicationVersion(__version__)

    # Keep the application appearance independent of the operating-system
    # theme. Otherwise Qt 6 follows Windows dark mode for dialogs and for
    # controls that are not fully covered by the stylesheet.
    app.setStyle("Fusion")
    light_palette = QPalette()
    light_palette.setColor(QPalette.ColorRole.Window, QColor("#eceef1"))
    light_palette.setColor(QPalette.ColorRole.WindowText, QColor("#1a1e24"))
    light_palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    light_palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#fafbfc"))
    light_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    light_palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#1a1e24"))
    light_palette.setColor(QPalette.ColorRole.Text, QColor("#1a1e24"))
    light_palette.setColor(QPalette.ColorRole.Button, QColor("#f4f5f7"))
    light_palette.setColor(QPalette.ColorRole.ButtonText, QColor("#1a1e24"))
    light_palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    light_palette.setColor(QPalette.ColorRole.Link, QColor("#0062a3"))
    light_palette.setColor(QPalette.ColorRole.Highlight, QColor("#0062a3"))
    light_palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    light_palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8a93a0"))
    app.setPalette(light_palette)
    app.setStyleSheet(QSS)

    primary_font = load_bundled_fonts()
    app.setFont(QFont(primary_font, 11))

    # ── 1. Initialise compliance DB (idempotent — safe every startup) ─────────
    cfr_db.initialise()
    # Active batches mean the previous process ended before controlled close.
    # They are made non-runnable until an authenticated recovery reconciles
    # authoritative scan_records and supplies a reason.
    from cfr21.regulated_records import RegulatedRecordService
    interrupted = RegulatedRecordService().detect_interrupted_batches()
    if interrupted:
        logging.getLogger("pharma.main").warning(
            "Startup detected %d interrupted authoritative batch(es).", len(interrupted))

    # ── 2. Load app config ────────────────────────────────────────────────────
    config = AppConfig.load()

    # ── 2b. Tamper/deletion detection ─────────────────────────────────────────
    if not _check_db_integrity_on_startup(config):
        raise RuntimeError("Startup audit integrity verification failed.")

    # ── 3. Create the single SessionManager instance ──────────────────────────
    #   Policy values come from settings.json (configurable in Advanced Settings)
    #   Defaults: 30 min lock, 90 day expiry, 3 attempts, 30 min lockout
    session_mgr = SessionManager(
        timeout_minutes        = config.policy.timeout_minutes,
        policy_expiry_days     = config.policy.password_expiry_days,
        policy_max_attempts    = config.policy.max_login_attempts,
        policy_lockout_minutes = config.policy.lockout_minutes,
        policy_history_count   = config.policy.password_history_count,
    )

    # ── 4. Show login dialog — blocks here until successful login ─────────────
    login_dlg = LoginDialog(session_mgr)
    if login_dlg.exec() != LoginDialog.DialogCode.Accepted:
        # User closed the login window — exit cleanly
        sys.exit(0)

    # ── 5. Log application start ──────────────────────────────────────────────
    audit.log(
        user       = session_mgr.current_user,
        action     = audit.ACTION_APP_STARTED,
        detail     = f"{__app_name__} v{__version__} started.",
        session_id = session_mgr.session_id,
    )

    # ── 6. Open main window ───────────────────────────────────────────────────
    win = MainWindow(config, session_mgr)
    win.show()

    # ── 7. Auto-backup compliance.db every 4 hours ────────────────────────────
    from PyQt6.QtCore import QTimer as _QTimer
    from cfr21.db_backup import run_backup as _run_backup

    def _auto_backup():
        ok, result = _run_backup(
            config.general.log_dir,
            config.general.backup_destination,
        )
        if ok:
            logging.getLogger("pharma.main").info(
                "Auto-backup completed: %s", result)
        else:
            logging.getLogger("pharma.main").warning(
                "Auto-backup failed: %s", result)

    _backup_timer = _QTimer()
    _backup_timer.timeout.connect(_auto_backup)
    _backup_timer.start(4 * 60 * 60 * 1000)   # every 4 hours in ms
    _auto_backup()   # run once immediately on startup

    _integrity_timer = _QTimer()

    def _scheduled_integrity_check():
        ok, message, checked = audit.verify_chain()
        if not ok:
            logging.getLogger("pharma.main").critical(
                "Scheduled audit integrity check failed after %d records: %s",
                checked, message)

    _integrity_timer.timeout.connect(_scheduled_integrity_check)
    _integrity_timer.start(15 * 60 * 1000)

    exit_code = app.exec()

    # ── 7. Log application close ──────────────────────────────────────────────
    if session_mgr.is_logged_in:
        audit.log(
            user       = session_mgr.current_user,
            action     = audit.ACTION_APP_CLOSED,
            detail     = f"{__app_name__} closed normally.",
            session_id = session_mgr.session_id,
        )
        session_mgr.logout(reason="Application closed")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
