"""Session-authorized administration boundary for user account changes."""

from cfr21.authorization import AuthorizationError, SessionContext, authorize_session
from cfr21.user_manager import (
    ROLE_DISPLAY,
    User,
    admin_reset_password_in_transaction,
    create_user_in_transaction,
    deactivate_user_in_transaction,
    reactivate_user_in_transaction,
)
from cfr21.reauthentication_service import ReauthenticationError, consume_grant
from cfr21.db import get_conn_ctx
import cfr21.audit_trail as audit


class UserAdministrationService:
    """Protect privileged account changes with current backend session state."""

    def _authorize_admin(self, admin_user: User, session_id: str,
                         target_username: str) -> User:
        context = SessionContext.from_user(admin_user, session_id)
        return authorize_session(
            context,
            "manage_users",
            target=f"user:{target_username}",
        )

    def create_account(self, admin_user: User, session_id: str, username: str,
                       password: str, role: str) -> tuple[bool, str]:
        try:
            actor = self._authorize_admin(admin_user, session_id, username)
        except AuthorizationError:
            return False, "You are not authorized to create user accounts."

        with get_conn_ctx() as conn:
            ok, msg = create_user_in_transaction(
                conn, actor, username, password, role)
            if ok:
                audit.append_event_in_transaction(
                    conn, actor, audit.ACTION_USER_CREATED,
                    f"Account '{username}' (role: {ROLE_DISPLAY[role]}) "
                    f"created by '{actor.username}'.",
                    session_id, target_type="user", target_id=username,
                    new_value={"role": role, "is_active": True})
            return ok, msg

    def deactivate_account(self, admin_user: User, session_id: str,
                           target_username: str) -> tuple[bool, str]:
        try:
            actor = self._authorize_admin(admin_user, session_id, target_username)
        except AuthorizationError:
            return False, "You are not authorized to deactivate user accounts."

        with get_conn_ctx() as conn:
            ok, msg = deactivate_user_in_transaction(
                conn, actor, target_username)
            if ok:
                audit.append_event_in_transaction(
                    conn, actor, audit.ACTION_USER_DEACTIVATED,
                    f"Account '{target_username}' deactivated.",
                    session_id, target_type="user", target_id=target_username,
                    new_value={"is_active": False})
            return ok, msg

    def reactivate_account(self, admin_user: User, session_id: str,
                           target_username: str) -> tuple[bool, str]:
        try:
            actor = self._authorize_admin(admin_user, session_id, target_username)
        except AuthorizationError:
            return False, "You are not authorized to reactivate user accounts."

        with get_conn_ctx() as conn:
            ok, msg = reactivate_user_in_transaction(
                conn, actor, target_username)
            if ok:
                audit.append_event_in_transaction(
                    conn, actor, audit.ACTION_USER_REACTIVATED,
                    f"Account '{target_username}' reactivated.",
                    session_id, target_type="user", target_id=target_username,
                    new_value={"is_active": True})
            return ok, msg

    def reset_password(self, admin_user: User, session_id: str,
                       target_username: str, new_password: str,
                       reauthentication_grant_id: str = "") -> tuple[bool, str]:
        try:
            actor = self._authorize_admin(admin_user, session_id, target_username)
        except AuthorizationError:
            return False, "You are not authorized to reset passwords."
        try:
            consume_grant(actor, session_id, reauthentication_grant_id,
                          "manage_users", f"user:{target_username}")
        except ReauthenticationError:
            return False, "Recent reauthentication is required to reset passwords."

        with get_conn_ctx() as conn:
            ok, msg = admin_reset_password_in_transaction(
                conn, actor, target_username, new_password)
            if ok:
                audit.append_event_in_transaction(
                    conn, actor, audit.ACTION_PASSWORD_RESET,
                    f"Administrator '{actor.username}' reset password for "
                    f"'{target_username}'. User forced to change on next login.",
                    session_id, target_type="user", target_id=target_username,
                    new_value={"must_change_pw": True})
            return ok, msg


_SERVICE = UserAdministrationService()


def create_account(admin_user: User, session_id: str, username: str,
                   password: str, role: str) -> tuple[bool, str]:
    return _SERVICE.create_account(admin_user, session_id, username, password, role)


def deactivate_account(admin_user: User, session_id: str,
                       target_username: str) -> tuple[bool, str]:
    return _SERVICE.deactivate_account(admin_user, session_id, target_username)


def reactivate_account(admin_user: User, session_id: str,
                       target_username: str) -> tuple[bool, str]:
    return _SERVICE.reactivate_account(admin_user, session_id, target_username)


def reset_password(admin_user: User, session_id: str, target_username: str,
                   new_password: str, reauthentication_grant_id: str = "") -> tuple[bool, str]:
    return _SERVICE.reset_password(
        admin_user,
        session_id,
        target_username,
        new_password,
        reauthentication_grant_id,
    )
