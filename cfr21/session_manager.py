# cfr21/session_manager.py
# Session management — 21 CFR Part 11 §11.10(d) and §11.10(j).
#
# Owns the concept of "who is currently logged in" for the entire app.
# There is exactly ONE active session at a time (single-user terminal).
#
# Responsibilities:
#   - Login: authenticate → create session → write audit entry
#   - Logout: close session → write audit entry
#   - Session timeout: detect inactivity → auto-logout
#   - Re-authentication: verify identity mid-session for sensitive actions
#   - Provide current user to any module that needs it
#
# ── Session ID ────────────────────────────────────────────────────────────────
#   A UUID4 generated at login. Stored in every audit_trail row for that
#   session so the entire session can be reconstructed from the audit trail.
#
# ── Session timeout ───────────────────────────────────────────────────────────
#   Default: 30 minutes of inactivity.
#   Any user interaction (keystroke, mouse click) calls ping() to reset the
#   timer. If the timer expires, lock_screen() is called — the UI locks
#   but the session and any active logging continue.
#   This is wired into MainWindow via a QTimer.
# ─────────────────────────────────────────────────────────────────────────────

import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from cfr21.user_manager import (
    User, AuthResult, authenticate, get_workstation
)
import cfr21.audit_trail as audit
from cfr21.authorization import (
    SESSION_ACTIVE,
    SESSION_LOCKED,
    SESSION_LOGGED_OUT,
)
from cfr21.db import get_conn_ctx

log = logging.getLogger("pharma.cfr21.session")

# Default session timeout — 30 minutes inactivity
DEFAULT_TIMEOUT_MINUTES = 30


class SessionManager:
    """
    Singleton-style class. One instance created at app startup and passed
    wherever the current user is needed.

    Usage:
        sm = SessionManager()

        # Login
        result = sm.login("johndoe", "Password@1")
        if result.success:
            user = sm.current_user

        # Check permission
        if sm.can("start_logging"):
            controller.start_logging()

        # Sensitive action re-auth
        ok, msg = sm.reauthenticate("johndoe", "Password@1")

        # Logout
        sm.logout(reason="End of shift")

        # Activity ping (call from GUI on any user interaction)
        sm.ping()

        # Check timeout (call from QTimer every 60s)
        if sm.is_timed_out():
            sm.lock_screen()
    """

    def __init__(self,
                 timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES,
                 policy_expiry_days: int = 90,
                 policy_max_attempts: int = 3,
                 policy_lockout_minutes: int = 30,
                 policy_history_count: int = 5):
        """
        Parameters
        ----------
        timeout_minutes       : Inactivity timeout before auto-logout.
        policy_expiry_days    : Password expiry in days (0 = never).
        policy_max_attempts   : Failed login attempts before lockout.
        policy_lockout_minutes: How long accounts stay locked.
        """
        self.timeout_minutes        = timeout_minutes
        self.policy_expiry_days     = policy_expiry_days
        self.policy_max_attempts    = policy_max_attempts
        self.policy_lockout_minutes = policy_lockout_minutes
        self.policy_history_count   = policy_history_count
        self.on_screen_lock         = None   # callback → GUI shows lock overlay

        self._current_user:  Optional[User]     = None
        self._session_id:    str                = ""
        self._login_time:    Optional[datetime] = None
        self._last_activity: Optional[datetime] = None
        self._is_locked:     bool               = False   # screen lock state

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def current_user(self) -> Optional[User]:
        """The currently logged-in User, or None."""
        return self._current_user

    @property
    def session_id(self) -> str:
        """UUID of the active session, or empty string."""
        return self._session_id

    @property
    def is_logged_in(self) -> bool:
        return self._current_user is not None

    @property
    def is_locked(self) -> bool:
        """True when the screen lock overlay is active."""
        return self._is_locked

    # ── Login ──────────────────────────────────────────────────────────────────

    def login(self, username: str, password: str) -> AuthResult:
        """
        Attempt login.

        On success:
          - Sets current_user and session_id
          - Writes LOGIN to audit trail
          - Returns AuthResult(success=True, user=...)
            - error_code="" → normal login, proceed to main screen
            - error_code="must_change_pw" → redirect to change-password dialog
            - error_code="password_expired" → redirect to change-password dialog

        On failure:
          - Writes LOGIN_FAILED (or ACCOUNT_LOCKED) to audit trail
          - Returns AuthResult(success=False, error_code=...)
        """
        result = authenticate(
            username,
            password,
            policy_expiry_days     = self.policy_expiry_days,
            policy_max_attempts    = self.policy_max_attempts,
            policy_lockout_minutes = self.policy_lockout_minutes,
        )

        if result.success and result.user:
            # Issue 8: if the same user is already logged in (e.g. relocking
            # after screen lock), just refresh their activity — don't create
            # a new session. If a DIFFERENT user is logging in, the previous
            # session should have been closed via logout() before this call.
            if (self._current_user and
                    self._current_user.username.lower() ==
                    result.user.username.lower() and
                    self._is_locked):
                # Same user unlocking their own locked screen — just ping
                self._is_locked = False
                self.ping()
                return result

            self._current_user  = result.user
            self._session_id    = str(uuid.uuid4())
            self._login_time    = datetime.now(timezone.utc)
            self._last_activity = self._login_time
            self._is_locked     = False
            self._persist_session_start()

            audit.log(
                user       = result.user,
                action     = audit.ACTION_LOGIN,
                detail     = (
                    f"User '{result.user.username}' logged in "
                    f"(role: {result.user.role_display}) "
                    f"from {get_workstation()}"
                ),
                session_id = self._session_id,
            )
            log.info("Session started: %s (%s) — session_id=%s",
                     result.user.username, result.user.role, self._session_id)

        else:
            # Log the failure
            action = (
                audit.ACTION_ACCOUNT_LOCKED
                if result.error_code == "account_locked"
                else audit.ACTION_LOGIN_FAILED
            )
            # Create a minimal placeholder user dict for the audit entry
            # (we don't have a real User object on failure)
            _fail_user = result.user  # may be None or the locked user

            # Log with a system-level entry if user is unknown
            audit.log(
                user       = _fail_user,
                action     = action,
                detail     = (
                    f"Failed login attempt for username '{username}' "
                    f"from {get_workstation()} "
                    f"(reason: {result.error_code})"
                ),
                session_id = "",
            )
            log.warning("Login failed for '%s': %s", username, result.error_code)

        return result

    # ── Logout ────────────────────────────────────────────────────────────────

    def logout(self, reason: str = "User initiated logout"):
        """
        Log out the current user cleanly.
        Writes LOGOUT to audit trail. Clears session state.
        Safe to call even if no session is active.
        """
        if not self._current_user:
            return

        audit.log(
            user       = self._current_user,
            action     = audit.ACTION_LOGOUT,
            detail     = (
                f"User '{self._current_user.username}' logged out. "
                f"Session duration: {self._session_duration_str()}"
            ),
            session_id = self._session_id,
            reason     = reason,
        )
        log.info("Session ended: %s — session_id=%s",
                 self._current_user.username, self._session_id)
        self._set_session_state(SESSION_LOGGED_OUT, reason)

        self._current_user  = None
        self._session_id    = ""
        self._login_time    = None
        self._last_activity = None
        self._is_locked     = False

    def lock_screen(self):
        """
        Called by the inactivity timer when timeout is reached.

        KEY BEHAVIOUR:
          - The session is NOT ended — current_user and session_id are preserved
          - Logging continues uninterrupted on the scan thread
          - Only the UI is locked — on_screen_lock callback fires to show overlay
          - User must re-enter their password to unlock (see unlock_screen)

        This is the correct 21 CFR approach for production line software:
          - Unattended terminal is secured (§11.10(d))
          - Production data is not lost mid-batch
          - The authenticated session is still the active one
        """
        if not self._current_user or self._is_locked:
            return

        self._is_locked = True
        self._set_session_state(SESSION_LOCKED, "Screen locked due to inactivity")

        audit.log(
            user       = self._current_user,
            action     = audit.ACTION_SCREEN_LOCKED,
            detail     = (
                f"Screen locked after {self.timeout_minutes} minutes of "
                f"UI inactivity for user '{self._current_user.username}'. "
                f"Logging continues."
            ),
            session_id = self._session_id,
        )
        log.info("Screen locked: %s — session_id=%s",
                 self._current_user.username, self._session_id)

        if self.on_screen_lock:
            try:
                self.on_screen_lock()
            except Exception as e:
                log.error("on_screen_lock callback error: %s", e)

    def unlock_screen(self, password: str) -> tuple[bool, str]:
        """
        Re-authenticate to dismiss the lock screen overlay.

        CFR21 loophole fix: routes through authenticate() instead of going
        directly to bcrypt. This means:
          - Failed unlock attempts increment the DB lockout counter
          - Account locked after policy_max_attempts failures (same as login)
          - Every failed attempt is written to the audit trail
          - Brute-force on the lock screen is prevented (§11.10d)
        """
        if not self._current_user:
            return False, "No active session."

        username = self._current_user.username

        result = authenticate(
            username,
            password,
            policy_expiry_days     = self.policy_expiry_days,
            policy_max_attempts    = self.policy_max_attempts,
            policy_lockout_minutes = self.policy_lockout_minutes,
        )

        if not result.success:
            msgs = {
                "invalid_credentials": "Incorrect password.",
                "account_locked": (
                    "Account locked — too many failed attempts. "
                    "Contact the Administrator."
                ),
                "account_inactive": "Account has been deactivated.",
                "db_error":         "Database error. Contact the Administrator.",
            }
            audit.log(
                user       = self._current_user,
                action     = audit.ACTION_LOGIN_FAILED,
                detail     = (
                    f"Failed lock screen unlock attempt for '{username}' "
                    f"(reason: {result.error_code})."
                ),
                session_id = self._session_id,
            )
            return False, msgs.get(result.error_code, "Unlock failed.")

        # Success
        self._is_locked = False
        self._set_session_state(SESSION_ACTIVE, None)
        self.ping()
        audit.log(
            user       = self._current_user,
            action     = audit.ACTION_SCREEN_UNLOCKED,
            detail     = f"Screen unlocked by '{username}'.",
            session_id = self._session_id,
        )
        log.info("Screen unlocked: %s", username)
        return True, ""


    # ── Activity tracking ─────────────────────────────────────────────────────

    def ping(self):
        """
        Call this on any user interaction (mouse click, keypress).
        Resets the inactivity timer. Does nothing if screen is locked
        — only unlock_screen() resets the timer on a locked screen.
        """
        if self._current_user and not self._is_locked:
            self._last_activity = datetime.now(timezone.utc)
            self._touch_session()

    def is_timed_out(self) -> bool:
        """
        Returns True if the inactivity window has been exceeded
        and the screen is not already locked.
        """
        if not self._current_user or not self._last_activity:
            return False
        if self._is_locked:
            return False   # already locked — don't fire again
        elapsed = datetime.now(timezone.utc) - self._last_activity
        return elapsed > timedelta(minutes=self.timeout_minutes)

    # ── Permission check ──────────────────────────────────────────────────────

    def can(self, permission: str) -> bool:
        """
        Return True if the current user has the given permission.
        Returns False if nobody is logged in.
        """
        if not self._current_user:
            return False
        return self._current_user.can(permission)

    # ── Re-authentication ─────────────────────────────────────────────────────

    def reauthenticate(self, username: str, password: str) -> tuple[bool, str]:
        """
        Re-verify the current user's identity for sensitive actions
        (Advanced Settings access, destructive operations).

        Routes through authenticate() — NOT direct bcrypt — so that:
          - Failed reauth attempts count toward the account lockout limit
          - Repeated failures lock the account exactly like failed logins
          - Every failed attempt is written to the audit trail
        This closes the brute-force loophole that existed when this method
        checked bcrypt directly with unlimited attempts.
        """
        if not self._current_user:
            return False, "No active session."

        if username.strip().lower() != self._current_user.username.lower():
            return False, "Username does not match the current session."

        result = authenticate(
            username,
            password,
            policy_expiry_days     = self.policy_expiry_days,
            policy_max_attempts    = self.policy_max_attempts,
            policy_lockout_minutes = self.policy_lockout_minutes,
        )

        if not result.success:
            msgs = {
                "invalid_credentials": "Incorrect password.",
                "account_locked": (
                    "Account locked — too many failed attempts. "
                    "Contact the Administrator."
                ),
                "account_inactive": "Account has been deactivated.",
                "db_error":         "Database error. Contact the Administrator.",
            }
            audit.log(
                user       = self._current_user,
                action     = audit.ACTION_LOGIN_FAILED,
                detail     = (
                    f"Failed re-authentication attempt for '{username}' "
                    f"(reason: {result.error_code})."
                ),
                session_id = self._session_id,
            )
            return False, msgs.get(result.error_code, "Verification failed.")

        self.ping()  # successful reauth counts as activity
        return True, ""

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _session_duration_str(self) -> str:
        if not self._login_time:
            return "unknown"
        delta = datetime.now(timezone.utc) - self._login_time
        total_seconds = int(delta.total_seconds())
        h, rem = divmod(total_seconds, 3600)
        m, s   = divmod(rem, 60)
        return f"{h:02d}h {m:02d}m {s:02d}s"

    def _session_expiry_time(self) -> datetime:
        base = self._last_activity or datetime.now(timezone.utc)
        return base + timedelta(minutes=self.timeout_minutes)

    def _persist_session_start(self) -> None:
        if not self._current_user or not self._session_id or not self._login_time:
            return
        now_iso = self._login_time.isoformat(timespec="seconds")
        expiry_iso = self._session_expiry_time().isoformat(timespec="seconds")
        with get_conn_ctx() as conn:
            conn.execute("""
                INSERT INTO user_sessions
                    (session_id, user_id, username, role_at_login, login_time,
                     last_activity, state, lock_time, expiry_time, workstation,
                     termination_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
            """, (
                self._session_id,
                self._current_user.id,
                self._current_user.username,
                self._current_user.role,
                now_iso,
                now_iso,
                SESSION_ACTIVE,
                expiry_iso,
                get_workstation(),
            ))

    def _touch_session(self) -> None:
        if not self._session_id or not self._last_activity:
            return
        last_iso = self._last_activity.isoformat(timespec="seconds")
        expiry_iso = self._session_expiry_time().isoformat(timespec="seconds")
        with get_conn_ctx() as conn:
            conn.execute("""
                UPDATE user_sessions
                SET last_activity = ?, expiry_time = ?
                WHERE session_id = ? AND state = ?
            """, (last_iso, expiry_iso, self._session_id, SESSION_ACTIVE))

    def _set_session_state(self, state: str, reason: Optional[str]) -> None:
        if not self._session_id:
            return
        lock_time = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            if state == SESSION_LOCKED else None
        )
        with get_conn_ctx() as conn:
            conn.execute("""
                UPDATE user_sessions
                SET state = ?,
                    lock_time = CASE WHEN ? IS NULL THEN lock_time ELSE ? END,
                    termination_reason = ?
                WHERE session_id = ?
            """, (state, lock_time, lock_time, reason, self._session_id))

    def session_info_str(self) -> str:
        """Short string for display in the topbar: 'johndoe (Operator)'"""
        if not self._current_user:
            return "Not logged in"
        return (f"{self._current_user.username} "
                f"({self._current_user.role_display})")
