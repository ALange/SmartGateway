import os

from app import config


def test_without_password_one_is_generated_and_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
    monkeypatch.setattr(config, "ALLOW_OPEN_ADMIN", False)
    config.ensure_admin_password()
    first = config.ADMIN_PASSWORD
    assert len(first) >= 20 and config.GENERATED_ADMIN_PASSWORD
    assert oct(os.stat(tmp_path / "admin_password").st_mode & 0o777) == "0o600"
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
    config.ensure_admin_password()
    assert config.ADMIN_PASSWORD == first            # stable across restarts


def test_explicit_password_and_open_opt_in_are_respected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "mine")
    config.ensure_admin_password()
    assert config.ADMIN_PASSWORD == "mine" and not (tmp_path / "admin_password").exists()
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "")
    monkeypatch.setattr(config, "ALLOW_OPEN_ADMIN", True)
    config.ensure_admin_password()
    assert config.ADMIN_PASSWORD == ""
