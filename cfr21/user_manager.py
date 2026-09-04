# cfr21/user_manager.py
# Multi-user management for 21 CFR Part 11 compliance.
#
# Handles:
#   - Creating, updating, deactivating user accounts
#   - Role-based access control (Administrator/Supervisor/Operator/QA)
#   - Password hashing with bcrypt (never stores plaintext)
#   - Login attempt tracking and account lockout
#   - Password change enforcement
#
# ── Roles and permissions ─────────────────────────────────────────────────────
#
#   administrator — Full access. User management. Settings. All reports.
#                   Can create/deactivate any other account.
#
#   supervisor    — Can start/stop logging. Change master code. View all reports.
#                   Cannot manage users or change system settings.
#
#   operator      — Can start/stop logging. Set master code. View live screen.
#                   Cannot access settings, reports, or user management.
#
#   qa            — Read-only. Can view all reports and audit trail.
#                   Cannot start/stop logging or change any settings.
#
# ── 21 CFR Part 11 sections addressed ────────────────────────────────────────
#   §11.10(d) — System access limited to authorised individuals
#   §11.10(g) — Authority checks ensure role-appropriate access
#   §11.10(j) — Password controls: complexity, expiry, lockout, history
# ─────────────────────────────────────────────────────────────────────────────

import logging
import socket
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt

from cfr21.db import get_conn_ctx
from cfr21.permissions import (
    ALL_ROLES as CENTRAL_ALL_ROLES,
    ROLE_ADMINISTRATOR as CENTRAL_ROLE_ADMINISTRATOR,
    ROLE_DISPLAY as CENTRAL_ROLE_DISPLAY,
    ROLE_OPERATOR as CENTRAL_ROLE_OPERATOR,
    ROLE_PERMISSIONS,
    ROLE_QA as CENTRAL_ROLE_QA,
    ROLE_SUPERVISOR as CENTRAL_ROLE_SUPERVISOR,
    role_has_permission,
)

log = logging.getLogger("pharma.cfr21.user_manager")

# ── Role constants ────────────────────────────────────────────────────────────

ROLE_ADMINISTRATOR = CENTRAL_ROLE_ADMINISTRATOR
ROLE_SUPERVISOR    = CENTRAL_ROLE_SUPERVISOR
ROLE_OPERATOR      = CENTRAL_ROLE_OPERATOR
ROLE_QA            = CENTRAL_ROLE_QA

ALL_ROLES = CENTRAL_ALL_ROLES

# Role display names for UI
ROLE_DISPLAY = {
    ROLE_ADMINISTRATOR: CENTRAL_ROLE_DISPLAY[ROLE_ADMINISTRATOR],
    ROLE_SUPERVISOR:    CENTRAL_ROLE_DISPLAY[ROLE_SUPERVISOR],
    ROLE_OPERATOR:      CENTRAL_ROLE_DISPLAY[ROLE_OPERATOR],
    ROLE_QA:            CENTRAL_ROLE_DISPLAY[ROLE_QA],
}

# ── Permission map ────────────────────────────────────────────────────────────
# Maps role → set of permission strings.
# Check with: can(user, "start_logging")

_PERMISSIONS = ROLE_PERMISSIONS


# ── User dataclass ────────────────────────────────────────────────────────────

@dataclass
class User:
    """
    Represents one user account as returned from the database.
    The password_hash field is included but should never be passed to the GUI.
    """
    id:                   int
    username:             str
    password_hash:        str
    role:                 str
    is_active:            bool
    must_change_pw:       bool
    failed_attempts:      int
    locked_until:         Optional[datetime]   # None = not locked
    password_changed_at:  datetime
    created_at:           datetime
    created_by:           str

    @property
    def role_display(self) -> str:
        return ROLE_DISPLAY.get(self.role, self.role.title())

    def can(self, permission: str) -> bool:
        """Return True if this user's role grants the given permission."""
        return role_has_permission(self.role, permission)

    def is_locked(self) -> bool:
        """Return True if the account is currently locked out."""
        if self.locked_until is None:
            return False
        return datetime.now(timezone.utc) < self.locked_until

    def is_password_expired(self, expiry_days: int) -> bool:
        """Return True if the password has not been changed within expiry_days."""
        if expiry_days <= 0:
            return False  # 0 = never expires
        age = datetime.now(timezone.utc) - self.password_changed_at
        return age.days >= expiry_days


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class AuthResult:
    """Returned by authenticate(). Tells the caller exactly what happened."""
    success:         bool
    user:            Optional[User]   = None
    error_code:      str              = ""
    # error codes:
    #   "invalid_credentials" — wrong username or password
    #   "account_inactive"    — deactivated by admin
    #   "account_locked"      — too many failed attempts
    #   "password_expired"    — must change password (different from must_change_pw)
    #   "must_change_pw"      — first login or admin-forced change
    #   "db_error"            — unexpected database failure


# ── Row → User ────────────────────────────────────────────────────────────────

def _row_to_user(row) -> User:
    """Convert a sqlite3.Row to a User dataclass."""

    def _parse_dt(val: Optional[str]) -> Optional[datetime]:
        if not val:
            return None
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None

    return User(
        id                  = row["id"],
        username            = row["username"],
        password_hash       = row["password_hash"],
        role                = row["role"],
        is_active           = bool(row["is_active"]),
        must_change_pw      = bool(row["must_change_pw"]),
        failed_attempts     = row["failed_attempts"],
        locked_until        = _parse_dt(row["locked_until"]),
        password_changed_at = _parse_dt(row["password_changed_at"])
                              or datetime.now(timezone.utc),
        created_at          = _parse_dt(row["created_at"])
                              or datetime.now(timezone.utc),
        created_by          = row["created_by"],
    )


# ── Core functions ────────────────────────────────────────────────────────────

def authenticate(username: str, password: str,
                 policy_expiry_days: int = 90,
                 policy_max_attempts: int = 3,
                 policy_lockout_minutes: int = 30) -> AuthResult:
    """
    Attempt to authenticate a user.

    Steps:
      1. Lookup user by username (case-insensitive).
      2. Check account is active.
      3. Check account is not locked.
      4. Verify password against bcrypt hash.
      5. On failure: increment failed_attempts, lock if threshold reached.
      6. On success: reset failed_attempts, check must_change_pw and expiry.

    Returns AuthResult — caller checks .success and .error_code.
    Never raises — all DB errors caught and returned as error_code="db_error".
    """
    try:
        with get_conn_ctx() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username.strip(),)
            ).fetchone()

            if row is None:
                # Username not found — hash a dummy value to prevent timing attacks
                bcrypt.checkpw(b"dummy", bcrypt.hashpw(b"dummy", bcrypt.gensalt()))
                log.warning("Login attempt for unknown user: '%s'", username)
                return AuthResult(success=False, error_code="invalid_credentials")

            user = _row_to_user(row)

            # ── Active check ──────────────────────────────────────────────────
            if not user.is_active:
                log.warning("Login attempt on inactive account: '%s'", username)
                return AuthResult(success=False, error_code="account_inactive")

            # ── Lockout check ─────────────────────────────────────────────────
            if user.is_locked():
                log.warning("Login attempt on locked account: '%s'", username)
                return AuthResult(success=False, error_code="account_locked",
                                  user=user)

            # ── Password verification ──────────────────────────────────────────
            pw_ok = bcrypt.checkpw(
                password.encode("utf-8"),
                user.password_hash.encode("utf-8")
            )

            if not pw_ok:
                new_attempts = user.failed_attempts + 1
                locked_until_iso = None

                if new_attempts >= policy_max_attempts:
                    locked_until = (
                        datetime.now(timezone.utc)
                        + timedelta(minutes=policy_lockout_minutes)
                    )
                    locked_until_iso = locked_until.isoformat()
                    log.warning(
                        "Account '%s' locked until %s after %s failed attempts.",
                        username, locked_until_iso, new_attempts
                    )
                    conn.execute("""
                        UPDATE user_sessions
                        SET state = 'locked',
                            termination_reason = 'Account locked'
                        WHERE user_id = ? AND state = 'active'
                    """, (user.id,))

                conn.execute("""
                    UPDATE users
                    SET failed_attempts = ?, locked_until = ?
                    WHERE id = ?
                """, (new_attempts, locked_until_iso, user.id))

                log.warning("Failed login for '%s' (attempt %s/%s)",
                            username, new_attempts, policy_max_attempts)
                return AuthResult(success=False, error_code="invalid_credentials")

            # ── Success — reset failure counters ──────────────────────────────
            conn.execute("""
                UPDATE users
                SET failed_attempts = 0, locked_until = NULL
                WHERE id = ?
            """, (user.id,))

            # Re-fetch to get updated user state
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user.id,)
            ).fetchone()
            user = _row_to_user(row)

            log.info("Successful login: '%s' (role: %s)", username, user.role)

            # ── Must-change-password flag (admin forced or first login) ────────
            if user.must_change_pw:
                return AuthResult(success=True, user=user,
                                  error_code="must_change_pw")

            # ── Password expiry check ─────────────────────────────────────────
            if user.is_password_expired(policy_expiry_days):
                return AuthResult(success=True, user=user,
                                  error_code="password_expired")

            return AuthResult(success=True, user=user)

    except Exception as e:
        log.error("authenticate() DB error: %s", e)
        return AuthResult(success=False, error_code="db_error")


def change_password(user_id: int, old_password: str,
                    new_password: str,
                    min_length: int = 8,
                    min_days_between_changes: int = 7,
                    history_count: int = 5) -> tuple[bool, str]:
    """
    Change a user's password.

    Validates:
      - Old password is correct
      - New password meets complexity requirements
      - New password is not the same as current password
      - New password not in last N passwords (history_count, default 5)
      - Minimum days between changes not violated (default 7)

    Returns (True, "") on success, (False, error_message) on failure.
    """
    try:
        with get_conn_ctx() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()

            if row is None:
                return False, "User not found."

            user = _row_to_user(row)

            # Verify old password
            if not bcrypt.checkpw(old_password.encode("utf-8"),
                                   user.password_hash.encode("utf-8")):
                return False, "Current password is incorrect."

            # Minimum days between changes
            if min_days_between_changes > 0:
                age = datetime.now(timezone.utc) - user.password_changed_at
                if age.days < min_days_between_changes and not user.must_change_pw:
                    return False, (
                        f"Password was changed {age.days} day(s) ago. "
                        f"You must wait {min_days_between_changes} days "
                        f"between password changes."
                    )

            # Complexity validation
            ok, msg = _check_complexity(new_password, min_length)
            if not ok:
                return False, msg

            # Prevent reuse of current password
            if bcrypt.checkpw(new_password.encode("utf-8"),
                               user.password_hash.encode("utf-8")):
                return False, "New password must be different from your current password."

            # Issue 3: prevent reuse of last N passwords from history
            if history_count > 0:
                history_rows = conn.execute("""
                    SELECT password_hash FROM password_history
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                """, (user_id, history_count)).fetchall()

                for h_row in history_rows:
                    if bcrypt.checkpw(new_password.encode("utf-8"),
                                      h_row["password_hash"].encode("utf-8")):
                        return False, (
                            f"This password has been used recently. "
                            f"Please choose a password not used in your last "
                            f"{history_count} passwords."
                        )

            # Hash and store new password
            new_hash = bcrypt.hashpw(
                new_password.encode("utf-8"), bcrypt.gensalt(rounds=12)
            ).decode("utf-8")

            now_iso = datetime.now().astimezone().isoformat(timespec="seconds")

            # Save old hash to history before overwriting
            conn.execute("""
                INSERT INTO password_history (user_id, password_hash, changed_at)
                VALUES (?, ?, ?)
            """, (user_id, user.password_hash, now_iso))

            # Keep only last N entries in history per user
            conn.execute("""
                DELETE FROM password_history
                WHERE user_id = ?
                AND id NOT IN (
                    SELECT id FROM password_history
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
            """, (user_id, user_id, history_count))

            conn.execute("""
                UPDATE users
                SET password_hash = ?,
                    must_change_pw = 0,
                    failed_attempts = 0,
                    locked_until = NULL,
                    password_changed_at = ?
                WHERE id = ?
            """, (new_hash, now_iso, user_id))

            log.info("Password changed for user id=%s ('%s')",
                     user_id, user.username)
            return True, ""

    except Exception as e:
        log.error("change_password() DB error: %s", e)
        return False, f"Database error: {e}"


def admin_reset_password(admin_user: User, target_username: str,
                         new_password: str,
                         min_length: int = 8,
                         session_id: str = "") -> tuple[bool, str]:
    """
    Administrator resets another user's password.
    Sets must_change_pw = 1 so the user is forced to change it on next login.
    Admin does NOT need to know the old password.
    """
    if admin_user.role != ROLE_ADMINISTRATOR:
        return False, "Only administrators can reset other users' passwords."

    ok, msg = _check_complexity(new_password, min_length)
    if not ok:
        return False, msg

    try:
        with get_conn_ctx() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                (target_username,)
            ).fetchone()

            if row is None:
                return False, f"User '{target_username}' not found."

            new_hash = bcrypt.hashpw(
                new_password.encode("utf-8"), bcrypt.gensalt(rounds=12)
            ).decode("utf-8")

            now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
            conn.execute("""
                UPDATE users
                SET password_hash = ?,
                    must_change_pw = 1,
                    failed_attempts = 0,
                    locked_until = NULL,
                    password_changed_at = ?
                WHERE id = ?
            """, (new_hash, now_iso, row["id"]))

            log.info("Admin '%s' reset password for '%s'",
                     admin_user.username, target_username)

        # Audit internally — callers must not forget to log this (§11.10e)
        import cfr21.audit_trail as _audit
        _audit.log(
            user       = admin_user,
            action     = _audit.ACTION_PASSWORD_RESET,
            detail     = (
                f"Administrator '{admin_user.username}' reset password for "
                f"'{target_username}'. User forced to change on next login."
            ),
            session_id = session_id,
        )
        return True, ""

    except Exception as e:
        log.error("admin_reset_password() DB error: %s", e)
        return False, f"Database error: {e}"


def create_user(created_by: User, username: str, password: str,
                role: str, min_length: int = 8) -> tuple[bool, str]:
    """
    Create a new user account.
    Only administrators can create accounts.
    New accounts always have must_change_pw = 1.
    """
    if created_by.role != ROLE_ADMINISTRATOR:
        return False, "Only administrators can create user accounts."

    username = username.strip()
    if not username:
        return False, "Username cannot be empty."

    # Issue 9: validate username characters and length
    ok, msg = _check_username(username)
    if not ok:
        return False, msg

    if role not in ALL_ROLES:
        return False, f"Invalid role '{role}'. Must be one of: {ALL_ROLES}"

    ok, msg = _check_complexity(password, min_length)
    if not ok:
        return False, msg

    try:
        with get_conn_ctx() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                (username,)
            ).fetchone()
            if existing:
                return False, f"Username '{username}' already exists."

            pw_hash = bcrypt.hashpw(
                password.encode("utf-8"), bcrypt.gensalt(rounds=12)
            ).decode("utf-8")

            now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
            conn.execute("""
                INSERT INTO users
                    (username, password_hash, role, is_active,
                     must_change_pw, failed_attempts, locked_until,
                     password_changed_at, created_at, created_by)
                VALUES (?, ?, ?, 1, 1, 0, NULL, ?, ?, ?)
            """, (username, pw_hash, role, now_iso, now_iso,
                  created_by.username))

            log.info("User '%s' (role: %s) created by '%s'",
                     username, role, created_by.username)
            return True, ""

    except Exception as e:
        log.error("create_user() DB error: %s", e)
        return False, f"Database error: {e}"


def deactivate_user(admin_user: User, target_username: str) -> tuple[bool, str]:
    """
    Deactivate a user account (soft delete — row is kept for audit trail).
    Cannot deactivate yourself.
    """
    if admin_user.role != ROLE_ADMINISTRATOR:
        return False, "Only administrators can deactivate accounts."

    if admin_user.username.lower() == target_username.lower():
        return False, "You cannot deactivate your own account."

    try:
        with get_conn_ctx() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                (target_username,)
            ).fetchone()

            if row is None:
                return False, f"User '{target_username}' not found."

            result = conn.execute("""
                UPDATE users SET is_active = 0
                WHERE id = ?
            """, (row["id"],))

            if result.rowcount == 0:
                return False, f"User '{target_username}' not found."

            conn.execute("""
                UPDATE user_sessions
                SET state = 'logged_out',
                    termination_reason = 'Account deactivated'
                WHERE user_id = ? AND state = 'active'
            """, (row["id"],))

            log.info("User '%s' deactivated by '%s'",
                     target_username, admin_user.username)
            return True, ""

    except Exception as e:
        log.error("deactivate_user() DB error: %s", e)
        return False, f"Database error: {e}"


def reactivate_user(admin_user: User, target_username: str) -> tuple[bool, str]:
    """Reactivate a previously deactivated account."""
    if admin_user.role != ROLE_ADMINISTRATOR:
        return False, "Only administrators can reactivate accounts."

    try:
        with get_conn_ctx() as conn:
            result = conn.execute("""
                UPDATE users
                SET is_active = 1, failed_attempts = 0, locked_until = NULL
                WHERE username = ? COLLATE NOCASE
            """, (target_username,))

            if result.rowcount == 0:
                return False, f"User '{target_username}' not found."

            log.info("User '%s' reactivated by '%s'",
                     target_username, admin_user.username)
            return True, ""

    except Exception as e:
        log.error("reactivate_user() DB error: %s", e)
        return False, f"Database error: {e}"


def get_user(username: str) -> Optional[User]:
    """Fetch a single user by username. Returns None if not found."""
    try:
        with get_conn_ctx() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username,)
            ).fetchone()
            return _row_to_user(row) if row else None
    except Exception as e:
        log.error("get_user() DB error: %s", e)
        return None


def get_all_users() -> list[User]:
    """Fetch all user accounts, ordered by username."""
    try:
        with get_conn_ctx() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY username COLLATE NOCASE"
            ).fetchall()
            return [_row_to_user(r) for r in rows]
    except Exception as e:
        log.error("get_all_users() DB error: %s", e)
        return []


def can(user: Optional[User], permission: str) -> bool:
    """
    Convenience function: return True if user has permission.
    Returns False for None user (not logged in).
    """
    if user is None:
        return False
    return user.can(permission)


# ── Password complexity ───────────────────────────────────────────────────────

def _check_complexity(password: str, min_length: int = 8) -> tuple[bool, str]:
    """
    Validate password complexity requirements.

    Rules:
      - Minimum length (default 8)
      - At least one uppercase letter
      - At least one lowercase letter
      - At least one digit
      - At least one special character

    Returns (True, "") on pass, (False, message) on fail.
    """
    if len(password) < min_length:
        return False, f"Password must be at least {min_length} characters long."

    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."

    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter."

    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."

    special = set("!@#$%^&*()_+-=[]{}|;':\",./<>?`~")
    if not any(c in special for c in password):
        return False, (
            "Password must contain at least one special character "
            "(!@#$%^&*()_+-= etc.)."
        )

    return True, ""


def _check_username(username: str,
                    min_length: int = 3,
                    max_length: int = 32) -> tuple[bool, str]:
    """
    Validate username rules.

    Rules:
      - 3 to 32 characters
      - Letters, digits, underscores, hyphens only
      - No spaces or special characters
      - Cannot start with a digit or hyphen
      - Cannot be purely numeric

    Returns (True, "") on pass, (False, message) on fail.
    """
    import re

    if len(username) < min_length:
        return False, (
            f"Username must be at least {min_length} characters long."
        )

    if len(username) > max_length:
        return False, (
            f"Username must be no more than {max_length} characters long."
        )

    if not re.match(r'^[A-Za-z][A-Za-z0-9_\-]*$', username):
        return False, (
            "Username must start with a letter and contain only letters, "
            "numbers, underscores (_) or hyphens (-)."
        )

    if username.isdigit():
        return False, "Username cannot be purely numeric."

    return True, ""


def get_workstation() -> str:
    """Return the machine hostname for audit trail entries."""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"
