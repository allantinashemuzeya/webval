"""Models describing crawled website state (Phase 3 output)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LinkRef(BaseModel):
    """A hyperlink as found in a page's DOM."""

    href: str = Field(description="Resolved absolute URL")
    raw_href: str = Field(default="", description="href attribute as authored")
    text: str = ""
    location: str = Field(default="body", description="nav | footer | body | cta")
    is_internal: bool = True
    is_anchor: bool = Field(default=False, description="True for same-page #fragment links")


class AssetRef(BaseModel):
    """A page asset (image, video, downloadable document, script, style)."""

    url: str
    kind: str = Field(description="image | video | download | script | style | iframe")
    alt: str | None = None
    selector: str = Field(default="", description="CSS selector that located the element")


class PageSnapshot(BaseModel):
    """Everything captured for one crawled page."""

    url: str
    final_url: str = Field(default="", description="URL after redirects")
    status: int | None = None
    title: str = ""
    depth: int = 0
    html_path: str | None = Field(default=None, description="Evidence path of the stored DOM")
    screenshot_path: str | None = None
    meta: dict[str, str] = Field(default_factory=dict, description="Meta/OG/robots/canonical values")
    h1: list[str] = Field(default_factory=list)
    headings: dict[str, list[str]] = Field(default_factory=dict, description="h1..h6 -> texts")
    visible_text: str = Field(default="", description="Normalized visible text content")
    links: list[LinkRef] = Field(default_factory=list)
    assets: list[AssetRef] = Field(default_factory=list)
    anchor_ids: list[str] = Field(default_factory=list, description="All element ids/names usable as #targets")
    structured_data: list[dict[str, Any]] = Field(default_factory=list, description="Parsed JSON-LD blocks")
    captured_at: str = ""


class SiteMap(BaseModel):
    """All pages discovered in a run."""

    base_url: str
    pages: list[PageSnapshot] = Field(default_factory=list)

    def get(self, url: str) -> PageSnapshot | None:
        norm = url.rstrip("/")
        for p in self.pages:
            if p.url.rstrip("/") == norm or p.final_url.rstrip("/") == norm:
                return p
        return None

    @property
    def urls(self) -> list[str]:
        return [p.url for p in self.pages]
