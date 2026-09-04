"""Active production must reject changes to regulated acquisition inputs."""

from config.settings import AppConfig
from core.app_controller import AppController


def test_controller_blocks_teach_clear_and_config_during_active_batch(admin_user):
    controller = AppController(AppConfig())
    controller.set_cfr_session(admin_user, "s-1")
    controller._regulated_batch_id = "active-batch"  # Test active workflow state.

    for operation in (
        lambda: controller.arm_teach(1),
        lambda: controller.clear_master(1),
        lambda: controller.apply_new_config(AppConfig()),
    ):
        try:
            operation()
        except RuntimeError:
            pass
        else:
            raise AssertionError("A production input changed during an active batch")
