"""System proxy detection for locked-down corporate networks.

On many corporate machines the desktop browser reaches sites only through the
company proxy (configured via system settings or a PAC file), while direct
connections — including DNS lookups — are blocked. Detection order:

    1. HTTPS_PROXY / HTTP_PROXY / ALL_PROXY environment variables
    2. The Windows per-user registry proxy (ProxyEnable + ProxyServer)

PAC files (AutoConfigURL) cannot be expressed as a single proxy URL; installed
Chrome/Edge resolve PAC themselves, so browser-channel launches are left on
the system configuration and this module is only used for direct HTTP probes
and the bundled headless Chromium (which does not read system settings).
"""

from __future__ import annotations

import os
import sys

_ENV_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


def detect_proxy() -> str | None:
    """Best-effort system proxy URL (``http://host:port``), or None."""
    for var in _ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return _normalize(value)
    if sys.platform == "win32":
        server = _windows_registry_proxy()
        if server:
            return _normalize(server)
    return None


def parse_proxy_server_value(server: str) -> str | None:
    """Parse a Windows ``ProxyServer`` registry value.

    Either a bare ``host:port`` or a per-protocol list like
    ``http=host:3128;https=host:3128;ftp=...`` — prefer the https entry.
    """
    server = server.strip()
    if ";" in server or "=" in server:
        entries: dict[str, str] = {}
        for item in server.split(";"):
            if "=" in item:
                key, _, value = item.partition("=")
                entries[key.strip().lower()] = value.strip()
        server = entries.get("https") or entries.get("http") or ""
    return server or None


def _windows_registry_proxy() -> str | None:
    if sys.platform != "win32":  # pragma: no cover - platform guard
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    return parse_proxy_server_value(str(server))


def _normalize(server: str) -> str:
    if "://" not in server:
        return f"http://{server}"
    return server
