"""Permission matrix and protected-operation inventory tests."""

import ast
from pathlib import Path

from cfr21.permissions import (
    ALL_ROLES,
    PROTECTED_OPERATIONS,
    ROLE_ADMINISTRATOR,
    ROLE_PERMISSIONS,
    validate_permission_matrix,
)
from cfr21.authorization import AuthorizationError, SessionContext, authorize_session

ROOT = Path(__file__).resolve().parents[1]


def _literal_permissions_from_calls(relative_path: str, function_names: set[str]) -> set[str]:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    permissions: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if func_name in function_names and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str):
                permissions.add(node.args[0].value)
    return permissions


def test_permission_matrix_is_complete():
    ok, problems = validate_permission_matrix()
    assert ok, "\n".join(problems)


def test_administrator_has_every_protected_permission():
    assert ROLE_PERMISSIONS[ROLE_ADMINISTRATOR] == PROTECTED_OPERATIONS


def test_every_role_has_explicit_matrix_entry():
    assert set(ROLE_PERMISSIONS) == set(ALL_ROLES)


def test_backend_permission_checks_are_in_matrix():
    discovered = set()
    discovered |= _literal_permissions_from_calls(
        "core/app_controller.py", {"_require_permission"})
    discovered |= _literal_permissions_from_calls(
        "cfr21/regulated_records.py", {"_require_actor"})
    discovered |= _literal_permissions_from_calls(
        "cfr21/legacy_wal_import.py", {"_require_actor"})

    assert discovered <= PROTECTED_OPERATIONS


def test_authorization_rejects_unknown_permission(admin_user):
    context = SessionContext.from_user(admin_user, "s-1")
    try:
        authorize_session(context, "typo_permission")
    except AuthorizationError as exc:
        assert exc.reason_code == "unknown_permission"
    else:
        raise AssertionError("Unknown permission was accepted")
