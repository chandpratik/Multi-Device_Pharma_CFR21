# gui/cfr_dialogs.py
# All 21 CFR Part 11 compliance dialogs.
# New file — original dialogs.py is untouched.
#
# Dialogs:
#   LoginDialog          — username + password login gate
#   ChangePasswordDialog — old / new / confirm password change
#   ReauthDialog         — re-verify identity before sensitive action
#   ReasonDialog         — enter a reason for an audited action
#   _ResetPasswordDialog — admin password reset (used by cfr_tab.py)
#
# Note: UserManagementDialog was removed — user management is embedded
# in gui/cfr_tab.py as UserManagementPage.

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFrame, QWidget,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer

from cfr21.session_manager import SessionManager
from cfr21.user_manager import (
    User, change_password, admin_reset_password,
)
import cfr21.audit_trail as audit

# ── Shared style helpers ───────────────────────────────────────────────────────

_BLUE   = "#0062a3"
_DARK   = "#1a1e24"
_RED    = "#c0392b"
_GREEN  = "#1a7a3a"
_GREY   = "#f4f5f7"
_BORDER = "#d0d4da"
_TEXT2  = "#8a93a0"

_S_TITLE = (
    "font-size: 16px; font-weight: 700; color: #1a1e24; "
    "padding-bottom: 2px;"
)
_S_SUB = "font-size: 11px; color: #8a93a0; padding-bottom: 12px;"
_S_LBL = "font-size: 11px; font-weight: 600; color: #4a5260;"
_S_INPUT = (
    "QLineEdit { border: 1px solid #d0d4da; border-radius: 4px; "
    "padding: 6px 10px; font-size: 12px; background: #fff; } "
    "QLineEdit:focus { border-color: #0062a3; }"
)
_S_BTN_PRIMARY = (
    "QPushButton { background: #0062a3; color: #fff; border: none; "
    "border-radius: 4px; font-size: 13px; font-weight: 600; padding: 8px 0; } "
    "QPushButton:hover { background: #004f87; } "
    "QPushButton:pressed { background: #003d6b; } "
    "QPushButton:disabled { background: #b0c4d8; }"
)
_S_BTN_SECONDARY = (
    "QPushButton { background: #f4f5f7; color: #4a5260; "
    "border: 1px solid #d0d4da; border-radius: 4px; "
    "font-size: 13px; font-weight: 500; padding: 8px 0; } "
    "QPushButton:hover { background: #e8eaed; }"
)
_S_BTN_DANGER = (
    "QPushButton { background: #fef2f2; color: #c0392b; "
    "border: 1px solid #fca5a5; border-radius: 4px; "
    "font-size: 12px; font-weight: 600; padding: 6px 12px; } "
    "QPushButton:hover { background: #fee2e2; }"
)
_S_BTN_SUCCESS = (
    "QPushButton { background: #f0fdf4; color: #1a7a3a; "
    "border: 1px solid #86efac; border-radius: 4px; "
    "font-size: 12px; font-weight: 600; padding: 6px 12px; } "
    "QPushButton:hover { background: #dcfce7; }"
)


def _divider():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("background: #e0e4ea; max-height: 1px; margin: 4px 0;")
    return f


def _lbl(text, style=_S_LBL):
    w = QLabel(text)
    w.setStyleSheet(style)
    return w


def _inp(placeholder="", password=False, width=280):
    w = QLineEdit()
    w.setStyleSheet(_S_INPUT)
    w.setFixedHeight(36)
    w.setFixedWidth(width)
    w.setPlaceholderText(placeholder)
    if password:
        w.setEchoMode(QLineEdit.EchoMode.Password)
    return w


# ══════════════════════════════════════════════════════════════════════════════
#  LOGIN DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class LoginDialog(QDialog):
    """
    Shown at startup before MainWindow opens.
    Handles: normal login, must-change-pw, password expired.
    Closes with Accepted only when a valid session is established.
    """

    def __init__(self, session_mgr: SessionManager, parent=None):
        super().__init__(parent)
        self._sm = session_mgr
        self.setWindowTitle("Login — Pharma Code Datalogger")
        self.setModal(True)
        self.setFixedWidth(400)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(32, 28, 32, 28)

        # Header
        layout.addWidget(_lbl("Pharma Code Datalogger", _S_TITLE))
        layout.addWidget(_lbl("Sign in to your account to continue.", _S_SUB))
        layout.addWidget(_divider())
        layout.addSpacing(16)

        # Username
        layout.addWidget(_lbl("Username"))
        layout.addSpacing(4)
        self._inp_user = _inp("Enter username", width=336)
        layout.addWidget(self._inp_user)
        layout.addSpacing(12)

        # Password
        layout.addWidget(_lbl("Password"))
        layout.addSpacing(4)
        self._inp_pw = _inp("Enter password", password=True, width=336)
        self._inp_pw.returnPressed.connect(self._attempt_login)
        layout.addWidget(self._inp_pw)
        layout.addSpacing(6)

        # Error label (hidden until needed)
        self._lbl_error = QLabel("")
        self._lbl_error.setStyleSheet(
            "color: #c0392b; font-size: 11px; font-weight: 500;"
        )
        self._lbl_error.setWordWrap(True)
        self._lbl_error.hide()
        layout.addWidget(self._lbl_error)

        layout.addSpacing(16)

        # Login button
        self._btn_login = QPushButton("Sign In")
        self._btn_login.setStyleSheet(_S_BTN_PRIMARY)
        self._btn_login.setFixedHeight(40)
        self._btn_login.clicked.connect(self._attempt_login)
        layout.addWidget(self._btn_login)

        layout.addSpacing(16)
        layout.addWidget(_divider())
        layout.addSpacing(8)

        footer = QLabel("21 CFR Part 11 Compliant · Sun Pharma")
        footer.setStyleSheet(
            "color: #b0b8c4; font-size: 10px; font-weight: 400;"
        )
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(footer)

        # Focus username on open
        QTimer.singleShot(100, self._inp_user.setFocus)

    def _attempt_login(self):
        username = self._inp_user.text().strip()
        password = self._inp_pw.text()

        if not username or not password:
            self._show_error("Please enter both username and password.")
            return

        self._btn_login.setEnabled(False)
        self._btn_login.setText("Signing in…")

        result = self._sm.login(username, password)

        self._btn_login.setEnabled(True)
        self._btn_login.setText("Sign In")
        self._inp_pw.clear()

        if not result.success:
            msgs = {
                "invalid_credentials": "Invalid username or password.",
                "account_inactive":    (
                    "Your account has been deactivated. "
                    "Please contact the Administrator."
                ),
                "account_locked": (
                    "Your account is temporarily locked due to too many "
                    "failed attempts. Please try again later or contact "
                    "the Administrator."
                ),
                "db_error": "A database error occurred. Please restart the application.",
            }
            self._show_error(msgs.get(result.error_code, "Login failed."))
            return

        # ── Success paths ─────────────────────────────────────────────────────

        if result.error_code in ("must_change_pw", "password_expired"):
            reason_text = (
                "You must change your password before continuing."
                if result.error_code == "must_change_pw"
                else "Your password has expired. Please set a new password."
            )
            QMessageBox.information(
                self, "Password Change Required", reason_text
            )
            dlg = ChangePasswordDialog(
                user       = result.user,
                session_mgr= self._sm,
                forced     = True,
                parent     = self,
            )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                # User cancelled forced change — back to login
                self._sm.logout()
                self._show_error(
                    "Password change is required to continue. "
                    "Please log in again."
                )
                return

        # All good — accept the dialog, MainWindow will open
        self.accept()

    def _show_error(self, msg: str):
        self._lbl_error.setText(msg)
        self._lbl_error.show()


# ══════════════════════════════════════════════════════════════════════════════
#  CHANGE PASSWORD DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class ChangePasswordDialog(QDialog):
    """
    Allows user to change their own password.
    When forced=True (first login / expiry), Cancel is disabled.
    """

    def __init__(self, user: User, session_mgr: SessionManager,
                 forced: bool = False, parent=None):
        super().__init__(parent)
        self._user   = user
        self._sm     = session_mgr
        self._forced = forced
        self.setWindowTitle("Change Password")
        self.setModal(True)
        self.setFixedWidth(400)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(28, 24, 28, 24)

        layout.addWidget(_lbl("Change Password", _S_TITLE))
        sub = (
            "Password change required before you can continue."
            if self._forced
            else "Update your account password."
        )
        layout.addWidget(_lbl(sub, _S_SUB))
        layout.addWidget(_divider())
        layout.addSpacing(14)

        for attr, lbl_text, placeholder in [
            ("_old_pw",     "Current Password",  "Enter current password"),
            ("_new_pw",     "New Password",       "Min 8 chars, upper, lower, number, symbol"),
            ("_confirm_pw", "Confirm Password",   "Re-enter new password"),
        ]:
            layout.addWidget(_lbl(lbl_text))
            layout.addSpacing(4)
            inp = _inp(placeholder, password=True, width=344)
            setattr(self, attr, inp)
            layout.addWidget(inp)
            layout.addSpacing(10)

        # Requirements hint
        layout.addWidget(_lbl(
            "Requirements: 8+ characters · uppercase · lowercase · "
            "number · special character (!@#$%^&*…)",
            "font-size: 10px; color: #8a93a0;"
        ))
        layout.addSpacing(4)

        # Error label
        self._lbl_error = QLabel("")
        self._lbl_error.setStyleSheet(
            "color: #c0392b; font-size: 11px;"
        )
        self._lbl_error.setWordWrap(True)
        self._lbl_error.hide()
        layout.addWidget(self._lbl_error)

        layout.addSpacing(14)
        layout.addWidget(_divider())
        layout.addSpacing(10)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if not self._forced:
            btn_cancel = QPushButton("Cancel")
            btn_cancel.setStyleSheet(_S_BTN_SECONDARY)
            btn_cancel.setFixedHeight(36)
            btn_cancel.clicked.connect(self.reject)
            btn_row.addWidget(btn_cancel, 1)

        self._btn_save = QPushButton("Change Password")
        self._btn_save.setStyleSheet(_S_BTN_PRIMARY)
        self._btn_save.setFixedHeight(36)
        self._btn_save.clicked.connect(self._do_change)
        btn_row.addWidget(self._btn_save, 1)

        layout.addLayout(btn_row)

    def _do_change(self):
        old     = self._old_pw.text()
        new     = self._new_pw.text()
        confirm = self._confirm_pw.text()

        if not old or not new or not confirm:
            self._show_error("All fields are required.")
            return

        if new != confirm:
            self._show_error("New password and confirmation do not match.")
            self._confirm_pw.clear()
            return

        # CFR21: pass configured history_count so policy is actually enforced
        history_count = getattr(self._sm, "policy_history_count", 5)
        ok, msg = change_password(
            user_id                  = self._user.id,
            old_password             = old,
            new_password             = new,
            min_days_between_changes = 0 if self._forced else 7,
            history_count            = history_count,
        )

        if not ok:
            self._show_error(msg)
            return

        # Log to audit trail
        audit.log(
            user       = self._user,
            action     = audit.ACTION_PASSWORD_CHANGED,
            detail     = f"User '{self._user.username}' changed their password.",
            session_id = self._sm.session_id,
        )

        QMessageBox.information(
            self, "Password Changed",
            "Your password has been changed successfully."
        )
        self.accept()

    def _show_error(self, msg: str):
        self._lbl_error.setText(msg)
        self._lbl_error.show()


# ══════════════════════════════════════════════════════════════════════════════
#  RE-AUTHENTICATION DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class ReauthDialog(QDialog):
    """
    Shown before sensitive actions (settings change, product deactivation).
    Verifies that the currently logged-in user knows their password.
    §11.200 — electronic signature re-verification.
    """

    def __init__(self, session_mgr: SessionManager,
                 action_description: str = "perform this action",
                 parent=None):
        super().__init__(parent)
        self._sm = session_mgr
        self._action_desc = action_description
        self.setWindowTitle("Confirm Identity")
        self.setModal(True)
        self.setFixedWidth(380)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(28, 24, 28, 24)

        layout.addWidget(_lbl("Confirm Your Identity", _S_TITLE))
        layout.addWidget(_lbl(
            f"Please re-enter your password to {self._action_desc}.",
            _S_SUB
        ))
        layout.addWidget(_divider())
        layout.addSpacing(14)

        # Username (read-only, shows who is logged in)
        layout.addWidget(_lbl("Username"))
        layout.addSpacing(4)
        user_display = QLineEdit(
            self._sm.current_user.username if self._sm.current_user else ""
        )
        user_display.setReadOnly(True)
        user_display.setStyleSheet(
            _S_INPUT +
            "QLineEdit { background: #f4f5f7; color: #8a93a0; }"
        )
        user_display.setFixedHeight(36)
        user_display.setFixedWidth(324)
        layout.addWidget(user_display)
        layout.addSpacing(10)

        layout.addWidget(_lbl("Password"))
        layout.addSpacing(4)
        self._inp_pw = _inp("Enter your password", password=True, width=324)
        self._inp_pw.returnPressed.connect(self._verify)
        layout.addWidget(self._inp_pw)
        layout.addSpacing(6)

        self._lbl_error = QLabel("")
        self._lbl_error.setStyleSheet(
            "color: #c0392b; font-size: 11px;"
        )
        self._lbl_error.hide()
        layout.addWidget(self._lbl_error)

        layout.addSpacing(14)
        layout.addWidget(_divider())
        layout.addSpacing(10)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet(_S_BTN_SECONDARY)
        btn_cancel.setFixedHeight(36)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel, 1)

        btn_confirm = QPushButton("Confirm")
        btn_confirm.setStyleSheet(_S_BTN_PRIMARY)
        btn_confirm.setFixedHeight(36)
        btn_confirm.clicked.connect(self._verify)
        btn_row.addWidget(btn_confirm, 1)

        layout.addLayout(btn_row)
        QTimer.singleShot(100, self._inp_pw.setFocus)

    def _verify(self):
        if not self._sm.current_user:
            self.reject()
            return

        ok, msg = self._sm.reauthenticate(
            self._sm.current_user.username,
            self._inp_pw.text()
        )
        self._inp_pw.clear()

        if ok:
            self.accept()
        else:
            self._lbl_error.setText(msg or "Incorrect password.")
            self._lbl_error.show()


# ══════════════════════════════════════════════════════════════════════════════
#  REASON DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class ReasonDialog(QDialog):
    """
    Prompts the operator to enter a reason before an audited action.
    Used for: batch abort, settings change, master code clear, etc.
    The entered reason is stored in the audit_trail.reason column.
    """

    def __init__(self, title: str = "Enter Reason",
                 prompt: str = "Please state the reason for this action:",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(420)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._reason = ""
        self._build_ui(prompt)

    def _build_ui(self, prompt: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(24, 20, 24, 20)

        layout.addWidget(_lbl(self.windowTitle(), _S_TITLE))
        layout.addSpacing(6)
        layout.addWidget(_lbl(prompt, _S_SUB))
        layout.addWidget(_divider())
        layout.addSpacing(12)

        layout.addWidget(_lbl("Reason"))
        layout.addSpacing(4)
        self._inp = _inp("Type your reason here…", width=372)
        self._inp.setFixedHeight(36)
        self._inp.returnPressed.connect(self._confirm)
        layout.addWidget(self._inp)

        self._lbl_error = QLabel("")
        self._lbl_error.setStyleSheet(
            "color: #c0392b; font-size: 11px;"
        )
        self._lbl_error.hide()
        layout.addWidget(self._lbl_error)

        layout.addSpacing(14)
        layout.addWidget(_divider())
        layout.addSpacing(10)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet(_S_BTN_SECONDARY)
        btn_cancel.setFixedHeight(36)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel, 1)

        btn_ok = QPushButton("Confirm")
        btn_ok.setStyleSheet(_S_BTN_PRIMARY)
        btn_ok.setFixedHeight(36)
        btn_ok.clicked.connect(self._confirm)
        btn_row.addWidget(btn_ok, 1)

        layout.addLayout(btn_row)
        QTimer.singleShot(100, self._inp.setFocus)

    def _confirm(self):
        r = self._inp.text().strip()
        if not r:
            self._lbl_error.setText("Reason cannot be empty.")
            self._lbl_error.show()
            return
        self._reason = r
        self.accept()

    @property
    def reason(self) -> str:
        return self._reason


# NOTE: UserManagementDialog was removed — superseded by UserManagementPage
# in gui/cfr_tab.py which is embedded directly in the CFR21 Compliance tab.

class _ResetPasswordDialog(QDialog):
    """Small inline dialog for admin password reset."""

    def __init__(self, target_username: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Reset Password — {target_username}")
        self.setModal(True)
        self.setFixedWidth(360)
        self.new_password = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        layout.addWidget(_lbl(
            f"Set a temporary password for '{target_username}'.", _S_SUB
        ))
        layout.addWidget(_lbl("New Temporary Password"))
        self._inp = _inp("Enter temporary password",
                         password=True, width=312)
        layout.addWidget(self._inp)

        self._err = QLabel("")
        self._err.setStyleSheet("color: #c0392b; font-size: 11px;")
        self._err.hide()
        layout.addWidget(self._err)

        btns = QHBoxLayout()
        c = QPushButton("Cancel")
        c.setStyleSheet(_S_BTN_SECONDARY)
        c.setFixedHeight(34)
        c.clicked.connect(self.reject)

        ok_btn = QPushButton("Set Password")
        ok_btn.setStyleSheet(_S_BTN_PRIMARY)
        ok_btn.setFixedHeight(34)
        ok_btn.clicked.connect(self._ok)

        btns.addWidget(c, 1)
        btns.addWidget(ok_btn, 1)
        layout.addLayout(btns)

    def _ok(self):
        pw = self._inp.text()
        if not pw:
            self._err.setText("Password cannot be empty.")
            self._err.show()
            return
        self.new_password = pw
        self.accept()
