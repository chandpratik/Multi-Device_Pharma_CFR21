"""Session-authorized settings persistence tests."""

import json

from cfr21.settings_service import save_settings
from config.settings import AppConfig


def test_settings_save_requires_issued_session(admin_user, tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(AppConfig, "_path", staticmethod(lambda: str(target)))

    ok, msg = save_settings(admin_user, "not-issued", AppConfig(), "change test")
    assert not ok
    assert not target.exists()


def test_operator_cannot_save_settings(operator_user, tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(AppConfig, "_path", staticmethod(lambda: str(target)))

    ok, msg = save_settings(operator_user, "s-12", AppConfig(), "change test")
    assert not ok
    assert not target.exists()


def test_authorized_admin_can_save_settings(admin_user, tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(AppConfig, "_path", staticmethod(lambda: str(target)))
    cfg = AppConfig()
    cfg.company.name = "Validation Lab"

    ok, msg = save_settings(admin_user, "s-1", cfg, "validated settings update")
    assert ok, msg
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["company"]["name"] == "Validation Lab"


def test_settings_save_requires_reason(admin_user, tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(AppConfig, "_path", staticmethod(lambda: str(target)))

    ok, msg = save_settings(admin_user, "s-1", AppConfig(), "")
    assert not ok
    assert "reason" in msg.lower()
    assert not target.exists()
