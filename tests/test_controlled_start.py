"""Controller must not bypass the controlled draft-batch setup path."""

from config.settings import AppConfig
from core.app_controller import AppController


def test_start_logging_requires_prepared_controlled_batch(admin_user):
    controller = AppController(AppConfig())
    controller.set_cfr_session(admin_user, "s-1")
    try:
        controller.start_logging()
    except RuntimeError as exc:
        assert "Controlled batch setup" in str(exc)
    else:
        raise AssertionError("Legacy start path created an active batch")
