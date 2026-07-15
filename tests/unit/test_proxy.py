"""System proxy detection (utils/proxy.py)."""

from __future__ import annotations

import pytest

from webval.utils.proxy import _normalize, detect_proxy, parse_proxy_server_value


class TestDetectProxy:
    def test_no_proxy_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        assert detect_proxy() is None

    def test_https_proxy_env_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:8080")
        monkeypatch.setenv("HTTP_PROXY", "http://other:3128")
        assert detect_proxy() == "http://corp-proxy:8080"

    def test_scheme_added_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.setenv("HTTP_PROXY", "corp-proxy:8080")
        assert detect_proxy() == "http://corp-proxy:8080"

    def test_blank_value_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "   ")
        assert detect_proxy() is None


class TestParseProxyServerValue:
    def test_bare_host_port(self) -> None:
        assert parse_proxy_server_value("proxy.corp.com:8080") == "proxy.corp.com:8080"

    def test_per_protocol_list_prefers_https(self) -> None:
        value = "http=proxy:3128;https=sproxy:3129;ftp=fproxy:21"
        assert parse_proxy_server_value(value) == "sproxy:3129"

    def test_per_protocol_list_falls_back_to_http(self) -> None:
        assert parse_proxy_server_value("http=proxy:3128;ftp=fproxy:21") == "proxy:3128"

    def test_no_usable_entry(self) -> None:
        assert parse_proxy_server_value("ftp=fproxy:21") is None
        assert parse_proxy_server_value("") is None


class TestNormalize:
    def test_existing_scheme_preserved(self) -> None:
        assert _normalize("socks5://host:1080") == "socks5://host:1080"

    def test_scheme_prepended(self) -> None:
        assert _normalize("host:8080") == "http://host:8080"
