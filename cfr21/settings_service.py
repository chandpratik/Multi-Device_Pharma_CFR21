"""Session-authorized persistence boundary for application settings."""

from config.settings import AppConfig
from cfr21.authorization import AuthorizationError, SessionContext, authorize_session
import cfr21.audit_trail as audit
from cfr21.user_manager import User


def save_settings(actor: User, session_id: str, new_config: AppConfig,
                  reason: str = "") -> tuple[bool, str]:
    """Persist settings only after backend authorization and audit evidence."""
    try:
        actor = authorize_session(
            SessionContext.from_user(actor, session_id),
            "change_settings",
            target="settings.json",
        )
    except AuthorizationError:
        return False, "You are not authorized to save system settings."

    if not reason or not reason.strip():
        return False, "A reason is required to save system settings."

    if not new_config.save():
        return False, "Could not write settings file."

    audit.log(
        user=actor,
        action=audit.ACTION_SETTINGS_CHANGED,
        detail="System settings saved via Advanced Settings page.",
        session_id=session_id,
        reason=reason.strip(),
    )
    return True, ""
