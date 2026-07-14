"""Unit tests for configuration loading and secret redaction."""

from pathlib import Path

from webval.config import Settings, load_settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.browser.engine == "chromium"
        assert s.validation.retry_attempts == 3
        assert len(s.devices) == 3

    def test_allowed_hosts_default_from_base_url(self):
        s = Settings(site={"base_url": "https://usim.preprod.sbx.us.pluvicto.com/"})
        assert s.site.allowed_hosts == ["usim.preprod.sbx.us.pluvicto.com"]

    def test_yaml_override(self, tmp_path: Path):
        cfg = tmp_path / "project.yaml"
        cfg.write_text("site:\n  max_pages: 7\nbrowser:\n  headless: false\n")
        s = load_settings(cfg)
        assert s.site.max_pages == 7
        assert s.browser.headless is False

    def test_env_wins_over_yaml(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / "project.yaml"
        cfg.write_text("site:\n  max_pages: 7\n")
        monkeypatch.setenv("WEBVAL_SITE__MAX_PAGES", "99")
        s = load_settings(cfg)
        assert s.site.max_pages == 99

    def test_credentials_from_env(self, monkeypatch):
        monkeypatch.setenv("WEBVAL_AUTH__USERNAME", "qa-user")
        monkeypatch.setenv("WEBVAL_AUTH__PASSWORD", "s3cret")
        s = load_settings()
        assert s.auth.username == "qa-user"
        assert s.auth.password.get_secret_value() == "s3cret"

    def test_redacted_dump_hides_secrets(self, monkeypatch):
        monkeypatch.setenv("WEBVAL_AUTH__USERNAME", "qa-user")
        monkeypatch.setenv("WEBVAL_AUTH__PASSWORD", "s3cret")
        dump = load_settings().redacted_dump()
        flat = str(dump)
        assert "s3cret" not in flat
        assert "qa-user" not in flat

    def test_password_never_in_repr(self):
        s = Settings(auth={"username": "u", "password": "topsecret"})
        assert "topsecret" not in repr(s)
