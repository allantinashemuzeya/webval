"""Page capture: turn a live page into a fully-evidenced PageSnapshot.

DOM parsing is split into a pure function (``parse_html``) so link/meta/asset
extraction is unit-testable without a browser; the async ``PageCapture`` adds
the parts that require rendering (visible text, screenshots).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import Page

from webval.config import Settings
from webval.evidence import EvidenceStore
from webval.models import AssetRef, EvidenceKind, LinkRef, PageSnapshot
from webval.utils import get_logger, normalize_text, utc_now_iso

log = get_logger("crawler.snapshot")

DOWNLOAD_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".csv", ".txt", ".rtf",
)
_CTA_HINTS = ("btn", "button", "cta")
_META_NAMES = ("description", "robots", "viewport", "keywords")


@dataclass
class ParsedDom:
    """Browser-independent DOM extraction result."""

    title: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    headings: dict[str, list[str]] = field(default_factory=dict)
    links: list[LinkRef] = field(default_factory=list)
    assets: list[AssetRef] = field(default_factory=list)
    anchor_ids: list[str] = field(default_factory=list)
    structured_data: list[dict[str, Any]] = field(default_factory=list)
    text_fallback: str = ""


def parse_html(html: str, base_url: str, allowed_hosts: list[str] | None = None) -> ParsedDom:
    """Extract metadata, links, assets, anchors, and structured data from HTML."""
    soup = BeautifulSoup(html, "lxml")
    parsed = ParsedDom()
    parsed.title = normalize_text(soup.title.get_text(), casefold=False) if soup.title else ""

    # --- meta / canonical / open graph
    for meta in soup.find_all("meta"):
        name = str(meta.get("name") or meta.get("property") or "").lower()
        content = str(meta.get("content") or "")
        if name in _META_NAMES or name.startswith(("og:", "twitter:")):
            parsed.meta[name] = content
    canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
    if canonical and canonical.get("href"):
        parsed.meta["canonical"] = urljoin(base_url, str(canonical["href"]))

    # --- headings
    for level in range(1, 7):
        tag = f"h{level}"
        values = [normalize_text(h.get_text(), casefold=False) for h in soup.find_all(tag)]
        values = [v for v in values if v]
        if values:
            parsed.headings[tag] = values

    # --- links with location classification
    base_host = urlparse(base_url).netloc.removeprefix("www.")
    hosts = {h.removeprefix("www.") for h in (allowed_hosts or [base_host])}
    for a in soup.find_all("a"):
        raw_href = str(a.get("href") or "")
        if not raw_href or raw_href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, raw_href)
        no_frag, frag = urldefrag(absolute)
        host = urlparse(absolute).netloc.removeprefix("www.")
        is_anchor = bool(frag) and (
            no_frag.rstrip("/") == base_url.split("#")[0].rstrip("/") or raw_href.startswith("#")
        )
        location = "body"
        if a.find_parent(["nav", "header"]) is not None:
            location = "nav"
        elif a.find_parent("footer") is not None:
            location = "footer"
        else:
            classes = " ".join(a.get("class") or []).lower()
            if any(hint in classes for hint in _CTA_HINTS) or a.get("role") == "button":
                location = "cta"
        parsed.links.append(
            LinkRef(
                href=absolute,
                raw_href=raw_href,
                text=normalize_text(a.get_text(), casefold=False),
                location=location,
                is_internal=host in hosts,
                is_anchor=is_anchor,
            )
        )

    # --- assets
    for img in soup.find_all("img"):
        src = str(img.get("src") or img.get("data-src") or "")
        if src:
            parsed.assets.append(
                AssetRef(
                    url=urljoin(base_url, src), kind="image",
                    alt=str(img["alt"]) if img.has_attr("alt") else None, selector="img",
                )
            )
    for video in soup.find_all(["video", "source"]):
        src = str(video.get("src") or "")
        if not src:
            continue
        if video.name == "video" or video.find_parent("video") is not None:
            parsed.assets.append(AssetRef(url=urljoin(base_url, src), kind="video", selector="video"))
    for iframe in soup.find_all("iframe"):
        src = str(iframe.get("src") or "")
        if src:
            kind = "video" if any(p in src for p in ("youtube", "vimeo", "brightcove", "player")) else "iframe"
            parsed.assets.append(AssetRef(url=urljoin(base_url, src), kind=kind, selector="iframe"))
    for a in soup.find_all("a"):
        href = str(a.get("href") or "")
        absolute = urljoin(base_url, href)
        path = urlparse(absolute).path.lower()
        if a.has_attr("download") or path.endswith(DOWNLOAD_EXTENSIONS):
            parsed.assets.append(AssetRef(url=absolute, kind="download", selector="a[download]"))

    # --- anchor targets
    parsed.anchor_ids = sorted(
        {str(el["id"]) for el in soup.find_all(id=True)}
        | {str(el["name"]) for el in soup.find_all("a", attrs={"name": True})}
    )

    # --- structured data (JSON-LD)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            parsed.structured_data.extend(data if isinstance(data, list) else [data])
        except (json.JSONDecodeError, TypeError):
            log.debug("Skipping malformed JSON-LD block")

    parsed.text_fallback = normalize_text(soup.get_text(" "), casefold=False)
    return parsed


class PageCapture:
    """Captures a navigated Playwright page into a PageSnapshot with evidence."""

    def __init__(self, settings: Settings, store: EvidenceStore) -> None:
        self._settings = settings
        self._store = store

    async def capture(self, page: Page, requested_url: str, depth: int) -> PageSnapshot:
        html = await page.content()
        parsed = parse_html(html, page.url, self._settings.site.allowed_hosts)
        visible_text: str = await page.evaluate("() => document.body ? document.body.innerText : ''")

        snapshot = PageSnapshot(
            url=requested_url,
            final_url=page.url,
            title=parsed.title or await page.title(),
            depth=depth,
            meta=parsed.meta,
            h1=parsed.headings.get("h1", []),
            headings=parsed.headings,
            visible_text=normalize_text(visible_text or parsed.text_fallback, casefold=False),
            links=parsed.links,
            assets=parsed.assets,
            anchor_ids=parsed.anchor_ids,
            structured_data=parsed.structured_data,
            captured_at=utc_now_iso(),
        )

        label = urlparse(page.url).path or "home"
        if self._settings.evidence.keep_dom_snapshots:
            html_path = self._store.new_path(EvidenceKind.HTML, f"dom-{label}", ".html")
            html_path.write_text(html, encoding="utf-8")
            ev = self._store.add_file(EvidenceKind.HTML, html_path, f"DOM snapshot of {page.url}", page.url)
            snapshot.html_path = ev.path
        if self._settings.evidence.full_page_screenshots:
            shot_path = self._store.new_path(EvidenceKind.SCREENSHOT, f"page-{label}", ".png")
            try:
                await page.screenshot(path=str(shot_path), full_page=True)
            except Exception as exc:
                log.warning("Full-page screenshot failed for %s (%s); using viewport", page.url, exc)
                await page.screenshot(path=str(shot_path), full_page=False)
            ev = self._store.add_file(
                EvidenceKind.SCREENSHOT, shot_path, f"Full-page screenshot of {page.url}", page.url
            )
            snapshot.screenshot_path = ev.path
        return snapshot
