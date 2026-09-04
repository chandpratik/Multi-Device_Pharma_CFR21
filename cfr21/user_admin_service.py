"""Session-authorized administration boundary for user account changes."""

from cfr21.authorization import AuthorizationError, SessionContext, authorize_session
from cfr21.user_manager import (
    ROLE_DISPLAY,
    User,
    admin_reset_password,
    create_user,
    deactivate_user,
    reactivate_user,
)
from cfr21.reauthentication_service import ReauthenticationError, consume_grant
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

        ok, msg = create_user(actor, username, password, role)
        if ok:
            audit.log(
                user=actor,
                action=audit.ACTION_USER_CREATED,
                detail=(
                    f"Account '{username}' (role: {ROLE_DISPLAY[role]}) "
                    f"created by '{actor.username}'."
                ),
                session_id=session_id,
            )
        return ok, msg

    def deactivate_account(self, admin_user: User, session_id: str,
                           target_username: str) -> tuple[bool, str]:
        try:
            actor = self._authorize_admin(admin_user, session_id, target_username)
        except AuthorizationError:
            return False, "You are not authorized to deactivate user accounts."

        ok, msg = deactivate_user(actor, target_username)
        if ok:
            audit.log(
                user=actor,
                action=audit.ACTION_USER_DEACTIVATED,
                detail=f"Account '{target_username}' deactivated.",
                session_id=session_id,
            )
        return ok, msg

    def reactivate_account(self, admin_user: User, session_id: str,
                           target_username: str) -> tuple[bool, str]:
        try:
            actor = self._authorize_admin(admin_user, session_id, target_username)
        except AuthorizationError:
            return False, "You are not authorized to reactivate user accounts."

        ok, msg = reactivate_user(actor, target_username)
        if ok:
            audit.log(
                user=actor,
                action=audit.ACTION_USER_REACTIVATED,
                detail=f"Account '{target_username}' reactivated.",
                session_id=session_id,
            )
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

        return admin_reset_password(
            actor,
            target_username,
            new_password,
            session_id=session_id,
        )


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
