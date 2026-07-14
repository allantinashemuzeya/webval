"""Phase 6 — link validation (presence, destination, HTTP status, redirect chains).

Also covers Navigation requirements: nav/footer/menu links are located by
their visible text across the crawled site, then their destinations are
requested through Playwright's APIRequestContext (which carries the same
HTTP Basic credentials as the browser context).
"""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlparse

from webval.models import (
    EvidenceKind,
    LinkRef,
    PageSnapshot,
    Requirement,
    RequirementCategory,
    Status,
    ValidationResult,
)
from webval.utils.text import contains_normalized, normalize_text
from webval.validators.base import BaseValidator


class LinkValidator(BaseValidator):
    name: ClassVar[str] = "links"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset(
        {RequirementCategory.LINK, RequirementCategory.NAVIGATION}
    )

    async def validate(self, requirement: Requirement) -> ValidationResult:
        found = self._locate(requirement)
        if found is None:
            return self.result(
                requirement, Status.FAIL,
                "Required link/navigation element not found on any crawled page",
                details=f"target_text={requirement.target_text!r} url_hint={requirement.target_url_hint!r}",
            )
        page, link = found

        # External links can be disabled (e.g. offline verification).
        if not link.is_internal and not self.ctx.settings.validation.external_links:
            return self.result(
                requirement, Status.WARNING,
                f"Link present on {page.url} but external verification is disabled",
                details=f"href={link.href}", page_url=page.url,
            )

        status_code, chain, error = await self._check_http(link.href)
        evidence = [
            self.ctx.store.add_json(
                EvidenceKind.NETWORK,
                f"{requirement.id}-link",
                {
                    "found_on": page.url,
                    "link_text": link.text,
                    "href": link.href,
                    "location": link.location,
                    "status": status_code,
                    "redirect_chain": chain,
                    "error": error,
                },
                f"Link check for {requirement.id}",
                page.url,
            )
        ]

        if error:
            return self.result(
                requirement, Status.FAIL,
                f"Link found on {page.url} but request failed: {error}",
                details=f"href={link.href}", page_url=page.url, evidence=evidence,
            )
        assert status_code is not None
        if status_code >= 400:
            return self.result(
                requirement, Status.FAIL,
                f"Broken link: {link.href} returned HTTP {status_code}",
                details=f"found on {page.url} ({link.location})", page_url=page.url, evidence=evidence,
            )

        # Wrong-destination check when the spec pinned a URL.
        hint = requirement.target_url_hint
        if hint and hint.startswith("http") and not self._same_destination(link.href, chain, hint):
            return self.result(
                requirement, Status.FAIL,
                f"Link resolves to {chain[-1] if chain else link.href}, expected {hint}",
                page_url=page.url, evidence=evidence,
            )

        detail = f"HTTP {status_code}"
        if len(chain) > 1:
            detail += f"; redirect chain: {' -> '.join(chain)}"
        return self.result(
            requirement, Status.PASS,
            f"Link “{link.text or link.href}” present on {page.url} ({link.location}), "
            f"resolves with HTTP {status_code}",
            details=detail, page_url=page.url, evidence=evidence,
        )

    # ------------------------------------------------------------------ locate

    def _locate(self, requirement: Requirement) -> tuple[PageSnapshot, LinkRef] | None:
        """Find the required link across all crawled pages."""
        hint = (requirement.target_url_hint or "").rstrip("/")
        text = requirement.target_text

        def matches(link: LinkRef) -> bool:
            if hint and link.href.rstrip("/").lower() == hint.lower():
                return True
            # OCR'd URL hints may have typo'd #fragments — match without them
            if hint and link.href.split("#")[0].rstrip("/").lower() == hint.split("#")[0].rstrip("/").lower():
                return True
            return bool(
                text and link.text and (
                    normalize_text(link.text) == normalize_text(text)
                    or contains_normalized(link.text, text)
                )
            )

        for page in self.ctx.site_map.pages:
            for link in page.links:
                if matches(link):
                    return page, link

        # Fall back to keyword scoring against link text.
        if requirement.keywords:
            best: tuple[PageSnapshot, LinkRef] | None = None
            best_hits = 0
            for page in self.ctx.site_map.pages:
                for link in page.links:
                    link_text = normalize_text(link.text)
                    if not link_text:
                        continue
                    hits = sum(1 for kw in requirement.keywords if kw in link_text)
                    if hits > best_hits:
                        best, best_hits = (page, link), hits
            if best is not None and best_hits >= 2:
                return best
        return None

    # -------------------------------------------------------------------- http

    async def _check_http(self, url: str) -> tuple[int | None, list[str], str]:
        """Request the URL through the authenticated context; return (status, chain, error)."""
        ctx = await self.ctx.session.context()
        try:
            response = await ctx.request.get(
                url,
                timeout=self.ctx.settings.validation.link_timeout_s * 1000,
                max_redirects=10,
            )
            chain = [url]
            if response.url != url:
                chain.append(response.url)
            return response.status, chain, ""
        except Exception as exc:
            return None, [url], str(exc)

    @staticmethod
    def _same_destination(href: str, chain: list[str], expected: str) -> bool:
        final = chain[-1] if chain else href
        def norm(u: str) -> str:
            p = urlparse(u)
            return f"{p.netloc.removeprefix('www.')}{p.path}".rstrip("/").lower()
        return norm(final) == norm(expected) or norm(href) == norm(expected)
