"""Explicit immutable recipe and configuration version approval workflow."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import cfr21.audit_trail as audit
from cfr21.authorization import SessionContext, authorize_session
from cfr21.db import get_conn_ctx
from cfr21.reauthentication_service import ReauthenticationError, consume_grant
from cfr21.user_manager import User


class VersionControlError(RuntimeError):
    """A controlled version operation was rejected."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class VersionControlService:
    """Create pending immutable values and approve them with a separate actor."""

    @staticmethod
    def _authorize(actor: User, session_id: str, permission: str, target: str) -> User:
        return authorize_session(SessionContext.from_user(actor, session_id), permission, target)

    def create_configuration(self, actor: User, session_id: str, snapshot: Any,
                             reason: str, prior_version_id: str = "") -> str:
        actor = self._authorize(actor, session_id, "manage_configurations", "configuration")
        if not reason.strip():
            raise VersionControlError("A configuration change reason is required.")
        payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
        return self._create(actor, session_id, "configuration_versions", "snapshot_json", payload,
                            reason, prior_version_id, "CONFIGURATION_VERSION_CREATED")

    def create_recipe(self, actor: User, session_id: str, master_data: str,
                      reason: str, prior_version_id: str = "") -> str:
        actor = self._authorize(actor, session_id, "manage_recipes", "recipe")
        if not master_data.strip() or not reason.strip():
            raise VersionControlError("Recipe data and a change reason are required.")
        return self._create(actor, session_id, "recipe_versions", "master_data", master_data,
                            reason, prior_version_id, "RECIPE_VERSION_CREATED")

    def _create(self, actor: User, session_id: str, table: str, value_column: str,
                value: str, reason: str, prior_version_id: str, action: str) -> str:
        with get_conn_ctx() as conn:
            if prior_version_id:
                prior = conn.execute(f"SELECT id FROM {table} WHERE id = ?", (prior_version_id,)).fetchone()
                if prior is None:
                    raise VersionControlError("The prior version was not found.")
            number = conn.execute(f"SELECT COALESCE(MAX(version_number), 0) + 1 FROM {table}").fetchone()[0]
            version_id = str(uuid.uuid4())
            conn.execute(f"""
                INSERT INTO {table} (
                    id, {value_column}, created_at, created_by, version_number,
                    prior_version_id, change_reason, approval_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (version_id, value, _utc_now(), actor.username, number,
                  prior_version_id or None, reason.strip()))
        audit.log(actor, action, f"Pending version '{version_id}' created.",
                  session_id=session_id, reason=reason.strip())
        return version_id

    def approve_configuration(self, actor: User, session_id: str, version_id: str,
                              reason: str, reauthentication_grant_id: str = "") -> None:
        self._approve(actor, session_id, "configuration_versions", "approve_configurations",
                      version_id, reason, "CONFIGURATION_VERSION_APPROVED",
                      reauthentication_grant_id)

    def approve_recipe(self, actor: User, session_id: str, version_id: str,
                       reason: str, reauthentication_grant_id: str = "") -> None:
        self._approve(actor, session_id, "recipe_versions", "approve_recipes",
                      version_id, reason, "RECIPE_VERSION_APPROVED",
                      reauthentication_grant_id)

    def _approve(self, actor: User, session_id: str, table: str, permission: str,
                 version_id: str, reason: str, action: str,
                 reauthentication_grant_id: str) -> None:
        actor = self._authorize(actor, session_id, permission, f"version:{version_id}")
        if not reason.strip():
            raise VersionControlError("An approval reason is required.")
        try:
            consume_grant(actor, session_id, reauthentication_grant_id,
                          permission, f"version:{version_id}")
        except ReauthenticationError as exc:
            raise VersionControlError("Recent reauthentication is required to approve a version.") from exc
        with get_conn_ctx() as conn:
            row = conn.execute(f"SELECT created_by, approval_status FROM {table} WHERE id = ?", (version_id,)).fetchone()
            if row is None or row["approval_status"] != "pending":
                raise VersionControlError("Only a pending version may be approved.")
            if row["created_by"].lower() == actor.username.lower():
                raise VersionControlError("A creator cannot approve their own version.")
            conn.execute(f"""
                UPDATE {table} SET approval_status = 'approved', approved_at = ?,
                    approved_by = ?, effective_at = ? WHERE id = ?
            """, (_utc_now(), actor.username, _utc_now(), version_id))
        audit.log(actor, action, f"Version '{version_id}' approved.",
                  session_id=session_id, reason=reason.strip())


_SERVICE = VersionControlService()
