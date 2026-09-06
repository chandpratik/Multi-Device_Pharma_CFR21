"""Session-authorized persistence boundary for application settings."""

import os

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

    path = new_config._path()
    previous = None
    if os.path.exists(path):
        try:
            with open(path, "rb") as handle:
                previous = handle.read()
        except OSError as exc:
            return False, f"Could not read existing settings for rollback: {exc}"

    if not new_config.save():
        return False, "Could not write settings file."

    try:
        audit.append_event(audit.event_for_user(
            actor,
            audit.ACTION_SETTINGS_CHANGED,
            "System settings saved via Advanced Settings page.",
            session_id=session_id,
            reason=reason.strip(),
            target_type="settings",
            target_id=os.path.basename(path),
            new_value={"path": path},
        ))
    except Exception as exc:
        try:
            if previous is None:
                os.remove(path)
            else:
                rollback_path = path + ".rollback"
                with open(rollback_path, "wb") as handle:
                    handle.write(previous)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(rollback_path, path)
        except Exception as rollback_exc:
            return False, (
                f"Settings audit failed and rollback failed: {rollback_exc}"
            )
        return False, f"Settings audit failed; previous settings restored: {exc}"
    return True, ""
