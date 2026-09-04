# tests/test_authentication.py
# §11.10(d) — access control tests: login, lockout, deactivation.

import cfr21.db as db
import cfr21.user_manager as um


class TestLogin:

    def test_correct_password_succeeds(self, operator_user):
        result = um.authenticate("operator1", "Operator@123")
        assert result.success
        assert result.user.username == "operator1"

    def test_wrong_password_rejected(self, operator_user):
        result = um.authenticate("operator1", "WrongPassword@1")
        assert not result.success
        assert result.error_code == "invalid_credentials"

    def test_unknown_username_rejected(self, fresh_db):
        result = um.authenticate("ghost", "Whatever@123")
        assert not result.success

    def test_username_case_insensitive(self, operator_user):
        result = um.authenticate("OPERATOR1", "Operator@123")
        assert result.success

    def test_deactivated_account_rejected(self, operator_user, admin_user):
        ok, msg = um.deactivate_user(admin_user, "operator1")
        assert ok, msg
        result = um.authenticate("operator1", "Operator@123")
        assert not result.success
        assert result.error_code == "account_inactive"

    def test_reactivated_account_works(self, operator_user, admin_user):
        um.deactivate_user(admin_user, "operator1")
        ok, msg = um.reactivate_user(admin_user, "operator1")
        assert ok, msg
        result = um.authenticate("operator1", "Operator@123")
        assert result.success


class TestLockout:

    def test_lockout_after_max_attempts(self, operator_user):
        for _ in range(3):
            um.authenticate("operator1", "wrong", policy_max_attempts=3)
        # Correct password now also rejected
        result = um.authenticate("operator1", "Operator@123",
                                 policy_max_attempts=3)
        assert not result.success
        assert result.error_code == "account_locked"

    def test_no_lockout_below_threshold(self, operator_user):
        for _ in range(2):
            um.authenticate("operator1", "wrong", policy_max_attempts=3)
        result = um.authenticate("operator1", "Operator@123",
                                 policy_max_attempts=3)
        assert result.success

    def test_success_resets_failed_counter(self, operator_user):
        um.authenticate("operator1", "wrong", policy_max_attempts=3)
        um.authenticate("operator1", "wrong", policy_max_attempts=3)
        um.authenticate("operator1", "Operator@123", policy_max_attempts=3)
        # Two more wrong attempts — counter restarted, so still not locked
        um.authenticate("operator1", "wrong", policy_max_attempts=3)
        um.authenticate("operator1", "wrong", policy_max_attempts=3)
        result = um.authenticate("operator1", "Operator@123",
                                 policy_max_attempts=3)
        assert result.success


class TestUserCreation:

    def test_admin_can_create_user(self, admin_user):
        ok, msg = um.create_user(admin_user, "newuser1", "NewUser@123",
                                 um.ROLE_OPERATOR)
        assert ok, msg

    def test_non_admin_cannot_create_user(self, operator_user):
        ok, msg = um.create_user(operator_user, "hacker", "Hack@1234",
                                 um.ROLE_ADMINISTRATOR)
        assert not ok

    def test_duplicate_username_rejected(self, admin_user):
        um.create_user(admin_user, "dupe", "Dupe@1234", um.ROLE_OPERATOR)
        ok, msg = um.create_user(admin_user, "dupe", "Dupe@1234",
                                 um.ROLE_OPERATOR)
        assert not ok

    def test_short_username_rejected(self, admin_user):
        ok, msg = um.create_user(admin_user, "ab", "Valid@1234",
                                 um.ROLE_OPERATOR)
        assert not ok

    def test_username_with_space_rejected(self, admin_user):
        ok, msg = um.create_user(admin_user, "john doe", "Valid@1234",
                                 um.ROLE_OPERATOR)
        assert not ok

    def test_new_user_must_change_password(self, admin_user):
        um.create_user(admin_user, "fresh1", "Fresh@1234", um.ROLE_OPERATOR)
        result = um.authenticate("fresh1", "Fresh@1234")
        assert result.success
        assert result.error_code == "must_change_pw"

    def test_weak_password_rejected_on_create(self, admin_user):
        ok, msg = um.create_user(admin_user, "weakpw1", "abc",
                                 um.ROLE_OPERATOR)
        assert not ok


class TestRolePermissions:

    def test_admin_permissions(self, admin_user):
        assert admin_user.can("manage_users")
        assert admin_user.can("change_settings")
        assert admin_user.can("start_logging")

    def test_operator_permissions(self, operator_user):
        assert operator_user.can("start_logging")
        assert not operator_user.can("manage_users")
        assert not operator_user.can("change_settings")

    def test_unknown_permission_denied(self, operator_user):
        assert not operator_user.can("nonexistent_permission")
