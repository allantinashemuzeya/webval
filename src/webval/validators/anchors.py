"""Phase 7 — anchor (in-page jump link) validation.

For each anchor requirement: the anchor link exists, is clickable, clicking
scrolls the page, and the destination element is visible afterwards.
Before/after screenshots are captured as evidence.
"""

from __future__ import annotations

from typing import ClassVar

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


class AnchorValidator(BaseValidator):
    name: ClassVar[str] = "anchors"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset({RequirementCategory.ANCHOR})

    async def validate(self, requirement: Requirement) -> ValidationResult:
        located = self._locate_anchor(requirement)
        if located is None:
            return self.result(
                requirement, Status.FAIL,
                "Anchor link not found on any crawled page",
                details=f"target_text={requirement.target_text!r}",
            )
        snapshot, link = located
        fragment = link.href.split("#", 1)[1] if "#" in link.href else ""
        if not fragment:
            return self.result(
                requirement, Status.FAIL,
                f"Link “{link.text}” found on {snapshot.url} but carries no #fragment",
                page_url=snapshot.url,
            )

        page = await self.ctx.open_page(snapshot.url)
        try:
            locator = page.locator(f'a[href*="#{fragment}"]', has_text=link.text or None).first
            if await locator.count() == 0:
                locator = page.locator(f'a[href*="#{fragment}"]').first
            if await locator.count() == 0:
                return self.result(
                    requirement, Status.FAIL,
                    f"Anchor link #{fragment} present in crawl snapshot but not in live DOM",
                    page_url=snapshot.url,
                )

            before_y: float = await page.evaluate("() => window.scrollY")
            before_shot = self.ctx.store.new_path(EvidenceKind.SCREENSHOT, f"{requirement.id}-before", ".png")
            await page.screenshot(path=str(before_shot))
            ev_before = self.ctx.store.add_file(
                EvidenceKind.SCREENSHOT, before_shot, f"{requirement.id}: before anchor click", snapshot.url
            )

            await locator.scroll_into_view_if_needed()
            await locator.click()
            await page.wait_for_timeout(800)  # allow smooth-scroll to settle

            after_y: float = await page.evaluate("() => window.scrollY")
            target_visible = await page.evaluate(
                """(id) => {
                    const el = document.getElementById(id) || document.getElementsByName(id)[0];
                    if (!el) return {exists: false, visible: false};
                    const r = el.getBoundingClientRect();
                    return {exists: true, visible: r.top < window.innerHeight && r.bottom > 0};
                }""",
                fragment,
            )
            after_shot = self.ctx.store.new_path(EvidenceKind.SCREENSHOT, f"{requirement.id}-after", ".png")
            await page.screenshot(path=str(after_shot))
            ev_after = self.ctx.store.add_file(
                EvidenceKind.SCREENSHOT, after_shot, f"{requirement.id}: after anchor click", snapshot.url
            )
            evidence = [ev_before, ev_after]

            if not target_visible["exists"]:
                return self.result(
                    requirement, Status.FAIL,
                    f"Anchor “{link.text}” clicked but destination element #{fragment} does not exist",
                    page_url=snapshot.url, evidence=evidence,
                )
            scrolled = abs(after_y - before_y) > 10
            if target_visible["visible"] and (scrolled or before_y == after_y == 0):
                return self.result(
                    requirement, Status.PASS,
                    f"Anchor “{link.text}” exists, is clickable, and scrolls to visible target #{fragment}",
                    details=f"scrollY {before_y:.0f} -> {after_y:.0f}",
                    page_url=snapshot.url, evidence=evidence,
                )
            if target_visible["visible"]:
                return self.result(
                    requirement, Status.PASS,
                    f"Anchor “{link.text}” target #{fragment} visible without scrolling (already in view)",
                    page_url=snapshot.url, evidence=evidence,
                )
            return self.result(
                requirement, Status.FAIL,
                f"Anchor “{link.text}” clicked (scrollY {before_y:.0f} -> {after_y:.0f}) "
                f"but target #{fragment} is not visible in the viewport",
                page_url=snapshot.url, evidence=evidence,
            )
        finally:
            await page.close()

    def _locate_anchor(self, requirement: Requirement) -> tuple[PageSnapshot, LinkRef] | None:
        text = requirement.target_text or ""
        keywords = requirement.keywords
        best: tuple[PageSnapshot, LinkRef] | None = None
        best_score = 0
        for page in self.ctx.site_map.pages:
            for link in page.links:
                if "#" not in link.href:
                    continue
                score = 0
                if text and contains_normalized(link.text, text):
                    score = 10
                elif text and contains_normalized(text, link.text) and link.text:
                    score = 8
                else:
                    link_text = normalize_text(link.text)
                    score = sum(2 for kw in keywords if kw in link_text)
                if score > best_score:
                    best, best_score = (page, link), score
        min_score = 4 if requirement.target_text else 2
        return best if best_score >= min_score else None
