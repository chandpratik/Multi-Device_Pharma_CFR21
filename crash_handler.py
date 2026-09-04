# crash_handler.py
# Global unhandled exception hook.
# Writes crash details to crash_log.txt next to the executable,
# then shows the operator a clear message before exiting.
#
# Install ONCE at the top of main.py:
#   from crash_handler import install
#   install()

import os
import sys
import traceback
from datetime import datetime


def _get_log_path() -> str:
    root = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
           else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, "crash_log.txt")


def _handle_exception(exc_type, exc_value, exc_traceback):
    # Ignore KeyboardInterrupt — let Ctrl+C work normally
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    error_text = "".join(
        traceback.format_exception(exc_type, exc_value, exc_traceback))

    # ── Write crash log ───────────────────────────────────────────────────────
    try:
        from version import __version__
        version_str = __version__
    except Exception:
        version_str = "unknown"

    log_path = _get_log_path()
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"CRASH  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Version: {version_str}\n")
            f.write(f"Python:  {sys.version}\n")
            f.write(f"{error_text}\n")
    except Exception:
        pass

    # ── CFR21: write crash to audit trail ─────────────────────────────────────
    # Best-effort only — DB may itself be corrupt if crash was severe enough
    try:
        import cfr21.audit_trail as audit
        audit.log(
            user       = None,   # session may be gone — log as system
            action     = audit.ACTION_CRASH_DETECTED,
            detail     = (
                f"Unhandled exception: {exc_type.__name__}: {exc_value}. "
                f"See crash_log.txt for full traceback."
            ),
            session_id = "",
        )
    except Exception:
        pass   # never let the audit write block the crash dialog

    # ── Show operator dialog ──────────────────────────────────────────────────
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(
            None,
            "Unexpected Error",
            f"An unexpected error occurred and the application must close.\n\n"
            f"Error: {exc_type.__name__}: {exc_value}\n\n"
            f"A crash report has been saved to:\n{log_path}\n\n"
            f"Please share this file with your technician.",
        )
    except Exception:
        print(f"CRASH: {error_text}", file=sys.stderr)

    sys.exit(1)


def install():
    """Install the global exception hook. Call once at application startup."""
    sys.excepthook = _handle_exception
