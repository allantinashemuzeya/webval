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


class TestDotEnvParsing:
    """Windows-notepad .env realities: BOM, quotes, '#' inside passwords."""

    def _write_env(self, tmp_path, content: bytes):
        (tmp_path / ".env").write_bytes(content)

    def test_bom_and_trailing_hash_password(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_env(
            tmp_path,
            b"\xef\xbb\xbfWEBVAL_AUTH__USERNAME=qa-user\nWEBVAL_AUTH__PASSWORD=Secr3t#\n",
        )
        s = load_settings()
        assert s.auth.username == "qa-user"
        assert s.auth.password.get_secret_value() == "Secr3t#"

    def test_quoted_values_unwrapped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_env(tmp_path, b'WEBVAL_AUTH__USERNAME="u"\nWEBVAL_AUTH__PASSWORD="p#w= x"\n')
        s = load_settings()
        assert s.auth.username == "u"
        assert s.auth.password.get_secret_value() == "p#w= x"

    def test_hash_mid_value_kept(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_env(tmp_path, b"WEBVAL_AUTH__USERNAME=u\nWEBVAL_AUTH__PASSWORD=a#b#c\n")
        assert load_settings().auth.password.get_secret_value() == "a#b#c"

    def test_real_env_vars_beat_dotenv(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_env(tmp_path, b"WEBVAL_AUTH__PASSWORD=from-file\n")
        monkeypatch.setenv("WEBVAL_AUTH__PASSWORD", "from-env")
        assert load_settings().auth.password.get_secret_value() == "from-env"
