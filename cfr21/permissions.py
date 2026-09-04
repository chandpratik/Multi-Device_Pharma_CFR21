"""Central role-to-permission matrix for protected backend operations."""

ROLE_ADMINISTRATOR = "administrator"
ROLE_SUPERVISOR = "supervisor"
ROLE_OPERATOR = "operator"
ROLE_QA = "qa"

ALL_ROLES = [ROLE_ADMINISTRATOR, ROLE_SUPERVISOR, ROLE_OPERATOR, ROLE_QA]

ROLE_DISPLAY = {
    ROLE_ADMINISTRATOR: "Administrator",
    ROLE_SUPERVISOR: "Supervisor",
    ROLE_OPERATOR: "Operator",
    ROLE_QA: "QA",
}

# Protected operations are backend authority checks, not just GUI affordances.
# Keep this set in sync with service/controller checks so tests can catch drift.
PROTECTED_OPERATIONS = {
    "change_own_password",
    "change_settings",
    "backup_database",
    "clear_master_code",
    "close_batch",
    "connect_camera",
    "connect_plc",
    "deactivate_product",
    "disconnect_camera",
    "disconnect_plc",
    "export_reports",
    "import_legacy_wal",
    "login",
    "manage_users",
    "reconcile_batches",
    "recover_batches",
    "release_batches",
    "review_batches",
    "set_master_code",
    "start_logging",
    "stop_logging",
    "view_audit_trail",
    "view_live",
    "view_reports",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    ROLE_ADMINISTRATOR: set(PROTECTED_OPERATIONS),
    ROLE_SUPERVISOR: {
        "change_own_password",
        "clear_master_code",
        "close_batch",
        "connect_camera",
        "connect_plc",
        "deactivate_product",
        "disconnect_camera",
        "disconnect_plc",
        "export_reports",
        "login",
        "reconcile_batches",
        "release_batches",
        "review_batches",
        "set_master_code",
        "start_logging",
        "stop_logging",
        "view_audit_trail",
        "view_live",
        "view_reports",
    },
    ROLE_OPERATOR: {
        "change_own_password",
        "clear_master_code",
        "close_batch",
        "connect_camera",
        "connect_plc",
        "disconnect_camera",
        "disconnect_plc",
        "login",
        "set_master_code",
        "start_logging",
        "stop_logging",
        "view_live",
    },
    ROLE_QA: {
        "change_own_password",
        "export_reports",
        "login",
        "view_audit_trail",
        "view_live",
        "view_reports",
    },
}


def role_has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def validate_permission_matrix() -> tuple[bool, list[str]]:
    """Return matrix defects so startup/tests can fail closed on drift."""
    problems: list[str] = []
    missing_roles = set(ALL_ROLES) - set(ROLE_PERMISSIONS)
    extra_roles = set(ROLE_PERMISSIONS) - set(ALL_ROLES)
    if missing_roles:
        problems.append(f"Missing permission matrix roles: {sorted(missing_roles)}")
    if extra_roles:
        problems.append(f"Unknown permission matrix roles: {sorted(extra_roles)}")

    for role in ALL_ROLES:
        granted = ROLE_PERMISSIONS.get(role, set())
        unknown = granted - PROTECTED_OPERATIONS
        if unknown:
            problems.append(f"Role '{role}' grants unknown permissions: {sorted(unknown)}")

    for permission in sorted(PROTECTED_OPERATIONS):
        if not any(permission in ROLE_PERMISSIONS.get(role, set()) for role in ALL_ROLES):
            problems.append(f"Permission '{permission}' is not granted to any role")

    return not problems, problems
