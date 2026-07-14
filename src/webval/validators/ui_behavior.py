"""Phase 13 — UI behaviour validation.

Dispatches on what the requirement describes: back-to-top button, expandable
sections (accordions / aria-expanded), modal windows, cookie consent banners,
and generic open/close menu behaviour. Before/after screenshots for each.
"""

from __future__ import annotations

from typing import Any, ClassVar

from webval.models import Evidence, EvidenceKind, Requirement, RequirementCategory, Status, ValidationResult
from webval.utils.text import normalize_text
from webval.validators.base import BaseValidator

_COOKIE_SELECTORS = (
    "#onetrust-banner-sdk", "#onetrust-accept-btn-handler", "[id*='cookie-banner' i]",
    "[class*='cookie-consent' i]", "[aria-label*='cookie' i]", "#truste-consent-track",
)
_BACK_TO_TOP_SELECTORS = (
    "[class*='back-to-top' i]", "[id*='back-to-top' i]", "[aria-label*='back to top' i]",
    "[class*='scroll-top' i]", "[class*='scrolltop' i]", "a[href='#top']", "a[href='#']",
)
_EXPANDABLE_SELECTORS = (
    "[aria-expanded]", "details summary", "[class*='accordion' i] button", "[data-toggle='collapse']",
)
_MODAL_TRIGGER_SELECTORS = (
    "[data-toggle='modal']", "[data-bs-toggle='modal']", "[aria-haspopup='dialog']",
    "[class*='modal-trigger' i]",
)


class UiBehaviorValidator(BaseValidator):
    name: ClassVar[str] = "ui_behavior"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset({RequirementCategory.UI_BEHAVIOR})

    async def validate(self, requirement: Requirement) -> ValidationResult:
        snapshot = self.ctx.resolve_target_page(requirement)
        if snapshot is None:
            return self.result(requirement, Status.ERROR, "No crawled page available")

        text = normalize_text(requirement.requirement)
        page = await self.ctx.open_page(snapshot.url)
        try:
            if "back to top" in text or "back-to-top" in text:
                return await self._back_to_top(page, requirement, snapshot.url)
            if "cookie" in text:
                return await self._cookie_banner(page, requirement, snapshot.url)
            if any(k in text for k in ("modal", "pop-up", "popup", "lightbox")):
                return await self._modal(page, requirement, snapshot.url)
            if any(k in text for k in ("accordion", "expand", "collaps", "toggle", "isi")):
                return await self._expandable(page, requirement, snapshot.url)
            # generic interactive element: fall back to expandable behaviour
            return await self._expandable(page, requirement, snapshot.url)
        finally:
            await page.close()

    # ------------------------------------------------------------ back to top

    async def _back_to_top(self, page: Any, requirement: Requirement, url: str) -> ValidationResult:
        await page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        await page.wait_for_timeout(700)
        scrolled_y: float = await page.evaluate("() => window.scrollY")
        if scrolled_y < 50:
            return self.result(
                requirement, Status.WARNING,
                "Page too short to exercise back-to-top behaviour (no scroll possible)",
                page_url=url,
            )
        button = await self._first_visible(page, _BACK_TO_TOP_SELECTORS)
        ev_before = await self._shot(page, requirement, "scrolled", url)
        if button is None:
            return self.result(
                requirement, Status.FAIL,
                f"Back-to-top control not visible after scrolling to bottom (scrollY={scrolled_y:.0f})",
                page_url=url, evidence=[ev_before],
            )
        await button.click()
        await page.wait_for_timeout(1000)
        final_y: float = await page.evaluate("() => window.scrollY")
        ev_after = await self._shot(page, requirement, "after-click", url)
        if final_y <= 50:
            return self.result(
                requirement, Status.PASS,
                f"Back-to-top button visible and functional (scrollY {scrolled_y:.0f} -> {final_y:.0f})",
                page_url=url, evidence=[ev_before, ev_after],
            )
        return self.result(
            requirement, Status.FAIL,
            f"Back-to-top clicked but page did not return to top (scrollY={final_y:.0f})",
            page_url=url, evidence=[ev_before, ev_after],
        )

    # ---------------------------------------------------------- cookie banner

    async def _cookie_banner(self, page: Any, requirement: Requirement, url: str) -> ValidationResult:
        banner = await self._first_visible(page, _COOKIE_SELECTORS)
        ev = await self._shot(page, requirement, "cookie-banner", url)
        if banner is None:
            return self.result(
                requirement, Status.FAIL,
                "Cookie consent banner not visible on page load",
                page_url=url, evidence=[ev],
            )
        accept = await self._first_visible(
            page, ("#onetrust-accept-btn-handler", "button:has-text('Accept')", "[class*='accept' i]")
        )
        if accept is not None:
            await accept.click()
            await page.wait_for_timeout(800)
            still = await self._first_visible(page, _COOKIE_SELECTORS)
            ev_after = await self._shot(page, requirement, "cookie-accepted", url)
            if still is None:
                return self.result(
                    requirement, Status.PASS,
                    "Cookie banner displayed on load and dismissed after Accept",
                    page_url=url, evidence=[ev, ev_after],
                )
            return self.result(
                requirement, Status.FAIL,
                "Cookie banner did not close after clicking Accept",
                page_url=url, evidence=[ev, ev_after],
            )
        return self.result(
            requirement, Status.WARNING,
            "Cookie banner visible but no Accept control found to exercise close behaviour",
            page_url=url, evidence=[ev],
        )

    # ------------------------------------------------------------------ modal

    async def _modal(self, page: Any, requirement: Requirement, url: str) -> ValidationResult:
        trigger = await self._first_visible(page, _MODAL_TRIGGER_SELECTORS)
        if trigger is None:
            # external-link interstitials are the common pharma modal: try an external link
            trigger = await self._first_visible(page, ('a[target="_blank"]',))
        if trigger is None:
            return self.result(
                requirement, Status.WARNING,
                "No modal trigger found on the page — manual verification recommended",
                page_url=url,
            )
        await trigger.click()
        await page.wait_for_timeout(800)
        modal = await self._first_visible(
            page, ('[role="dialog"]', ".modal.show", "[class*='modal' i][style*='block']", "dialog[open]")
        )
        ev_open = await self._shot(page, requirement, "modal-open", url)
        if modal is None:
            return self.result(
                requirement, Status.FAIL,
                "Modal trigger clicked but no dialog became visible",
                page_url=url, evidence=[ev_open],
            )
        close = await self._first_visible(
            page, ('[role="dialog"] [aria-label*="close" i]', ".modal .close", "[class*='modal-close' i]",
                   "dialog [autofocus]", '[role="dialog"] button')
        )
        if close is not None:
            await close.click()
            await page.wait_for_timeout(600)
        else:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(600)
        still_open = await self._first_visible(page, ('[role="dialog"]', ".modal.show", "dialog[open]"))
        ev_closed = await self._shot(page, requirement, "modal-closed", url)
        if still_open is None:
            return self.result(
                requirement, Status.PASS,
                "Modal opens on trigger and closes via close control/Escape",
                page_url=url, evidence=[ev_open, ev_closed],
            )
        return self.result(
            requirement, Status.FAIL,
            "Modal opened but could not be closed",
            page_url=url, evidence=[ev_open, ev_closed],
        )

    # ------------------------------------------------------------- expandable

    async def _expandable(self, page: Any, requirement: Requirement, url: str) -> ValidationResult:
        element = await self._first_visible(page, _EXPANDABLE_SELECTORS)
        if element is None:
            return self.result(
                requirement, Status.WARNING,
                "No expandable/interactive element matching the requirement found — manual verification recommended",
                page_url=url,
            )
        before_state = await element.get_attribute("aria-expanded")
        ev_before = await self._shot(page, requirement, "collapsed", url)
        await element.click()
        await page.wait_for_timeout(600)
        after_state = await element.get_attribute("aria-expanded")
        ev_after = await self._shot(page, requirement, "expanded", url)
        changed = before_state != after_state and after_state is not None
        if changed:
            # toggle back to verify close
            await element.click()
            await page.wait_for_timeout(400)
            final_state = await element.get_attribute("aria-expanded")
            closes = final_state == before_state
            return self.result(
                requirement, Status.PASS if closes else Status.WARNING,
                f"Expandable section toggles (aria-expanded {before_state} -> {after_state}"
                + (f" -> {final_state})" if closes else ") but did not toggle back"),
                page_url=url, evidence=[ev_before, ev_after],
            )
        return self.result(
            requirement, Status.WARNING,
            "Interactive element clicked but no aria-expanded state change observed — verify manually",
            page_url=url, evidence=[ev_before, ev_after],
        )

    # ---------------------------------------------------------------- helpers

    @staticmethod
    async def _first_visible(page: Any, selectors: tuple[str, ...]) -> Any:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    return locator
            except Exception:
                continue
        return None

    async def _shot(self, page: Any, requirement: Requirement, label: str, url: str) -> Evidence:
        path = self.ctx.store.new_path(EvidenceKind.SCREENSHOT, f"{requirement.id}-{label}", ".png")
        await page.screenshot(path=str(path))
        return self.ctx.store.add_file(EvidenceKind.SCREENSHOT, path, f"{requirement.id}: {label}", url)
