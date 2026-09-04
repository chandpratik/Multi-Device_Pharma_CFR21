# tests/test_password_policy.py
# §11.300 — password controls: complexity, history, reset.

import cfr21.db as db
import cfr21.user_manager as um


class TestPasswordChange:

    def test_change_with_correct_old_password(self, operator_user):
        ok, msg = um.change_password(
            operator_user.id, "Operator@123", "NewSecret@456",
            min_days_between_changes=0)
        assert ok, msg
        result = um.authenticate("operator1", "NewSecret@456")
        assert result.success

    def test_change_with_wrong_old_password(self, operator_user):
        ok, msg = um.change_password(
            operator_user.id, "WrongOld@123", "NewSecret@456",
            min_days_between_changes=0)
        assert not ok

    def test_old_password_stops_working(self, operator_user):
        um.change_password(operator_user.id, "Operator@123",
                           "NewSecret@456", min_days_between_changes=0)
        result = um.authenticate("operator1", "Operator@123")
        assert not result.success


class TestComplexity:

    def test_too_short_rejected(self, operator_user):
        ok, _ = um.change_password(operator_user.id, "Operator@123",
                                   "Ab@1", min_days_between_changes=0)
        assert not ok

    def test_no_uppercase_rejected(self, operator_user):
        ok, _ = um.change_password(operator_user.id, "Operator@123",
                                   "lowercase@123", min_days_between_changes=0)
        assert not ok

    def test_no_digit_rejected(self, operator_user):
        ok, _ = um.change_password(operator_user.id, "Operator@123",
                                   "NoDigits@Here", min_days_between_changes=0)
        assert not ok

    def test_no_special_rejected(self, operator_user):
        ok, _ = um.change_password(operator_user.id, "Operator@123",
                                   "NoSpecial123", min_days_between_changes=0)
        assert not ok

    def test_same_as_current_rejected(self, operator_user):
        ok, _ = um.change_password(operator_user.id, "Operator@123",
                                   "Operator@123", min_days_between_changes=0)
        assert not ok


class TestPasswordHistory:

    def test_recent_password_blocked(self, operator_user):
        um.change_password(operator_user.id, "Operator@123",
                           "Second@456", min_days_between_changes=0,
                           history_count=5)
        ok, msg = um.change_password(operator_user.id, "Second@456",
                                     "Operator@123",
                                     min_days_between_changes=0,
                                     history_count=5)
        assert not ok, "reusing a recent password must be blocked"

    def test_history_window_respected(self, operator_user):
        """With history_count=1, only the immediately previous password
        is blocked — older ones become reusable."""
        um.change_password(operator_user.id, "Operator@123",
                           "Second@456", min_days_between_changes=0,
                           history_count=1)
        um.change_password(operator_user.id, "Second@456",
                           "Third@789", min_days_between_changes=0,
                           history_count=1)
        # Operator@123 is now 2 changes old — outside a history of 1
        ok, msg = um.change_password(operator_user.id, "Third@789",
                                     "Operator@123",
                                     min_days_between_changes=0,
                                     history_count=1)
        assert ok, msg


class TestAdminReset:

    def test_admin_can_reset(self, admin_user, operator_user):
        ok, msg = um.admin_reset_password(admin_user, "operator1",
                                          "ResetPass@789")
        assert ok, msg

    def test_reset_forces_change_on_login(self, admin_user, operator_user):
        um.admin_reset_password(admin_user, "operator1", "ResetPass@789")
        result = um.authenticate("operator1", "ResetPass@789")
        assert result.success
        assert result.error_code == "must_change_pw"

    def test_non_admin_cannot_reset(self, operator_user, admin_user):
        ok, msg = um.admin_reset_password(operator_user, "admin",
                                          "Sneaky@123")
        assert not ok

    def test_reset_is_audited(self, admin_user, operator_user):
        um.admin_reset_password(admin_user, "operator1", "ResetPass@789")
        with db.get_conn_ctx() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_trail WHERE action = 'PASSWORD_RESET'"
            ).fetchall()
        assert len(rows) >= 1
        assert "operator1" in rows[-1]["detail"]
