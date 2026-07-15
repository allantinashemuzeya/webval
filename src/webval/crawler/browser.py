"""Playwright session management (Phase 2).

HTTP Basic Authentication is supplied via browser-context ``http_credentials``
so every page, subresource, and API request in the context is authenticated —
no URL-embedded credentials, nothing leaks into evidence artifacts.

``ignore_https_errors`` tolerates the self-signed / internal CA certificates
common on preprod environments. Contexts are cached per device profile so
responsive validation reuses one browser process.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from webval.config import DeviceConfig, Settings
from webval.utils import get_logger

log = get_logger("crawler.browser")

DESKTOP_PROFILE = "Desktop Chrome"


class CredentialsMissingError(RuntimeError):
    """Raised before any crawl work when required auth is not configured."""


class BrowserSession:
    """Owns the Playwright browser and hands out authenticated contexts/pages."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        if self._settings.auth.mode == "http_basic" and (
            not self._settings.auth.username or not self._settings.auth.password.get_secret_value()
        ):
            raise CredentialsMissingError(
                "HTTP Basic Auth credentials are missing. Create a .env file in the folder you run "
                "webval from (or set environment variables) containing:\n"
                "  WEBVAL_AUTH__USERNAME=...\n  WEBVAL_AUTH__PASSWORD=...\n"
                "Without them every page fetch fails and the run produces no site screenshots."
            )
        self._playwright = await async_playwright().start()
        engine = getattr(self._playwright, self._settings.browser.engine)
        launch_opts: dict[str, Any] = {
            "headless": self._settings.browser.headless,
            "slow_mo": self._settings.browser.slow_mo_ms,
        }
        # Restricted networks often block Playwright's browser CDN. When the
        # bundled browser is absent, fall back to the machine's installed
        # Chrome/Edge via launch channels (no download, no admin rights).
        configured = self._settings.browser.channel
        channels: list[str | None] = [configured] if configured else [None, "chrome", "msedge"]
        last_error: Exception | None = None
        for channel in channels:
            try:
                opts = {**launch_opts, "channel": channel} if channel else launch_opts
                self._browser = await engine.launch(**opts)
                break
            except Exception as exc:
                message = str(exc)
                last_error = exc
                if "Executable doesn't exist" in message or "playwright install" in message:
                    log.warning(
                        "Browser channel %s not available — trying next fallback",
                        channel or "bundled chromium",
                    )
                    continue
                raise
        else:
            raise RuntimeError(
                "No usable browser found. Either run `playwright install chromium` on a network "
                "that can reach cdn.playwright.dev, or install/keep Google Chrome or Microsoft Edge "
                f"(webval uses them automatically). Last error: {last_error}"
            )
        user = self._settings.auth.username
        log.info(
            "Browser started (%s%s, headless=%s, auth=%s%s)",
            self._settings.browser.engine,
            f" via channel={channel}" if channel else "",
            self._settings.browser.headless,
            self._settings.auth.mode,
            f", user={user[:2]}***, password=***{len(self._settings.auth.password.get_secret_value())} chars"
            if self._settings.auth.mode == "http_basic" else "",
        )

    async def close(self) -> None:
        for ctx in self._contexts.values():
            await ctx.close()
        self._contexts.clear()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        log.info("Browser closed")

    # ---------------------------------------------------------------- contexts

    def _context_options(self, device: DeviceConfig | None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "ignore_https_errors": self._settings.site.ignore_https_errors,
        }
        if self._settings.auth.mode == "http_basic":
            username = self._settings.auth.username
            password = self._settings.auth.password.get_secret_value()
            if not username or not password:
                raise RuntimeError(
                    "HTTP Basic Auth selected but credentials are missing. "
                    "Set WEBVAL_AUTH__USERNAME and WEBVAL_AUTH__PASSWORD (env or .env)."
                )
            options["http_credentials"] = {"username": username, "password": password}
        if device is not None:
            assert self._playwright is not None
            if device.playwright_device:
                descriptor = self._playwright.devices.get(device.playwright_device)
                if descriptor is None:
                    raise ValueError(f"Unknown Playwright device: {device.playwright_device}")
                options.update(descriptor)
            elif device.viewport:
                options["viewport"] = device.viewport
        return options

    async def context(self, profile: str = DESKTOP_PROFILE) -> BrowserContext:
        """Authenticated context for a named device profile (cached, session reuse)."""
        if profile in self._contexts:
            return self._contexts[profile]
        assert self._browser is not None, "BrowserSession not started"
        device = next((d for d in self._settings.devices if d.name == profile), None)
        if device is None and profile == DESKTOP_PROFILE:
            device = DeviceConfig(name=DESKTOP_PROFILE, viewport={"width": 1440, "height": 900})
        ctx = await self._browser.new_context(**self._context_options(device))
        ctx.set_default_timeout(self._settings.browser.timeout_ms)
        ctx.set_default_navigation_timeout(self._settings.browser.navigation_timeout_ms)
        self._contexts[profile] = ctx
        log.debug("Created browser context for profile %r", profile)
        return ctx

    async def new_page(self, profile: str = DESKTOP_PROFILE) -> Page:
        ctx = await self.context(profile)
        return await ctx.new_page()

    @property
    def device_profiles(self) -> list[str]:
        return [d.name for d in self._settings.devices]
