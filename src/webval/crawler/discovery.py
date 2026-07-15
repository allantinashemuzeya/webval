"""Site discovery (Phase 3): breadth-first crawl of internal pages.

Bounded by ``site.max_pages`` and ``site.max_depth``; restricted to
``site.allowed_hosts``; concurrent up to ``browser.concurrency`` pages.
Every discovered page is captured as a PageSnapshot with DOM + screenshot
evidence, producing the SiteMap all validators run against.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urldefrag, urlparse

from playwright.async_api import Error as PlaywrightError

from webval.config import Settings
from webval.crawler.browser import BrowserSession
from webval.crawler.snapshot import PageCapture
from webval.evidence import EvidenceStore
from webval.models import PageSnapshot, SiteMap
from webval.utils import get_logger, retry_async

log = get_logger("crawler.discovery")

_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:")
_SKIP_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".webp", ".mp4", ".webm", ".css", ".js", ".ico",
)


def _normalize_url(url: str) -> str:
    no_frag, _ = urldefrag(url)
    return no_frag.rstrip("/") or no_frag


class SiteDiscovery:
    """BFS crawler producing the run's SiteMap."""

    def __init__(self, settings: Settings, session: BrowserSession, store: EvidenceStore) -> None:
        self._settings = settings
        self._session = session
        self._capture = PageCapture(settings, store)
        self._seen: set[str] = set()
        self._lock = asyncio.Lock()

    def _crawlable(self, url: str) -> bool:
        if url.startswith(_SKIP_SCHEMES):
            return False
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.netloc.removeprefix("www.")
        if host not in {h.removeprefix("www.") for h in self._settings.site.allowed_hosts}:
            return False
        if parsed.path.lower().endswith(_SKIP_EXTENSIONS):
            return False
        lowered = url.lower()
        return not any(pattern in lowered for pattern in self._settings.site.exclude_patterns)

    async def discover(self) -> SiteMap:
        base = _normalize_url(self._settings.site.base_url)
        site_map = SiteMap(base_url=self._settings.site.base_url)
        queue: list[tuple[str, int]] = [(base, 0)]
        self._seen = {base}
        semaphore = asyncio.Semaphore(self._settings.browser.concurrency)

        while queue and len(site_map.pages) < self._settings.site.max_pages:
            capacity = self._settings.site.max_pages - len(site_map.pages)
            batch, queue = queue[:capacity], queue[capacity:]

            async def visit(url: str, depth: int) -> PageSnapshot | None:
                async with semaphore:
                    return await self._visit_safe(url, depth)

            snapshots = await asyncio.gather(*(visit(u, d) for u, d in batch))
            for snapshot in snapshots:
                if snapshot is None:
                    continue
                site_map.pages.append(snapshot)
                if snapshot.depth >= self._settings.site.max_depth:
                    continue
                for link in snapshot.links:
                    if link.is_anchor or not link.is_internal:
                        continue
                    normalized = _normalize_url(link.href)
                    async with self._lock:
                        if normalized in self._seen or not self._crawlable(normalized):
                            continue
                        self._seen.add(normalized)
                    queue.append((normalized, snapshot.depth + 1))

        log.info("Discovery complete: %d page(s) captured", len(site_map.pages))
        for page in site_map.pages:
            log.info("  [depth %d] %s — %s", page.depth, page.url, page.title or "(no title)")
        return site_map

    async def _visit_safe(self, url: str, depth: int) -> PageSnapshot | None:
        try:
            return await self._visit(url, depth)
        except PlaywrightError as exc:
            message = str(exc)
            if "ERR_HTTP_RESPONSE_CODE_FAILURE" in message or "ERR_INVALID_AUTH_CREDENTIALS" in message:
                # Chrome/Edge channels fail navigation outright on auth errors
                # instead of rendering the 401 page.
                log.error(
                    "Navigation to %s failed with an HTTP error response — this usually means the "
                    "HTTP Basic credentials are wrong or missing (check WEBVAL_AUTH__* in .env)",
                    url,
                )
            else:
                log.error("Failed to capture %s after retries: %s", url, exc)
            return PageSnapshot(url=url, depth=depth, status=None, title=f"[capture failed: {exc}"[:200] + "]")
        except Exception:
            log.exception("Unexpected error capturing %s", url)
            return None

    @retry_async(attempts=3, backoff_s=1.5, exceptions=(PlaywrightError,))
    async def _visit(self, url: str, depth: int) -> PageSnapshot:
        page = await self._session.new_page()
        try:
            response = await page.goto(url, wait_until="networkidle")
            snapshot = await self._capture.capture(page, url, depth)
            snapshot.status = response.status if response else None
            if response and response.status >= 400:
                log.warning("HTTP %d for %s", response.status, url)
            return snapshot
        finally:
            await page.close()
