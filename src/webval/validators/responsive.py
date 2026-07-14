"""Phase 12 — responsive validation across the configured device matrix.

Per device: page renders, no horizontal overflow, critical content (H1 or
first heading) visible, hamburger navigation present-and-functional on
mobile-width devices. One screenshot per device is always captured.
"""

from __future__ import annotations

from typing import Any, ClassVar

from webval.models import EvidenceKind, Requirement, RequirementCategory, Status, ValidationResult
from webval.validators.base import BaseValidator

_LAYOUT_JS = """
() => {
  const doc = document.documentElement;
  const overflow = doc.scrollWidth - doc.clientWidth;
  const h1 = document.querySelector('h1, h2, [role="heading"]');
  let headingVisible = false;
  if (h1) {
    const r = h1.getBoundingClientRect();
    headingVisible = r.width > 0 && r.height > 0;
  }
  return {
    viewportWidth: doc.clientWidth,
    scrollWidth: doc.scrollWidth,
    horizontalOverflowPx: Math.max(0, overflow),
    headingText: h1 ? h1.innerText.slice(0, 120) : null,
    headingVisible,
    canScrollVertically: doc.scrollHeight > doc.clientHeight,
  };
}
"""

_HAMBURGER_SELECTORS = (
    'button[aria-label*="menu" i]', 'button[class*="hamburger" i]', '[class*="menu-toggle" i]',
    'button[class*="navbar-toggle" i]', '[data-toggle="collapse"]', 'button[aria-controls*="nav" i]',
    '[class*="mobile-menu" i] button', 'header button[aria-expanded]',
)
_MOBILE_MAX_WIDTH = 820


class ResponsiveValidator(BaseValidator):
    name: ClassVar[str] = "responsive"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset({RequirementCategory.RESPONSIVE})

    async def validate(self, requirement: Requirement) -> ValidationResult:
        snapshot = self.ctx.resolve_target_page(requirement)
        if snapshot is None:
            return self.result(requirement, Status.ERROR, "No crawled page available")

        per_device: list[str] = []
        failures: list[str] = []
        evidence = []

        for profile in self.ctx.session.device_profiles:
            page = await self.ctx.open_page(snapshot.url, profile=profile)
            try:
                layout: dict[str, Any] = await page.evaluate(_LAYOUT_JS)
                shot = self.ctx.store.new_path(
                    EvidenceKind.SCREENSHOT, f"{requirement.id}-{profile}", ".png"
                )
                await page.screenshot(path=str(shot), full_page=False)
                evidence.append(
                    self.ctx.store.add_file(
                        EvidenceKind.SCREENSHOT, shot,
                        f"{requirement.id}: {profile} viewport", snapshot.url,
                    )
                )

                issues: list[str] = []
                if layout["horizontalOverflowPx"] > 5:
                    issues.append(f"horizontal overflow {layout['horizontalOverflowPx']}px")
                if not layout["headingVisible"]:
                    issues.append("critical heading not visible")

                if layout["viewportWidth"] <= _MOBILE_MAX_WIDTH:
                    hamburger_ok = await self._check_hamburger(page)
                    if hamburger_ok is False:
                        issues.append("hamburger menu missing or did not open")
                    elif hamburger_ok is True:
                        per_device.append(f"{profile}: hamburger navigation functional")

                if issues:
                    failures.append(f"{profile}: {', '.join(issues)}")
                else:
                    per_device.append(
                        f"{profile}: OK (viewport {layout['viewportWidth']}px, no overflow, heading visible)"
                    )
            finally:
                await page.close()

        evidence.append(
            self.ctx.store.add_json(
                EvidenceKind.METRIC, f"{requirement.id}-responsive",
                {"page": snapshot.url, "ok": per_device, "failures": failures},
                f"Responsive matrix for {requirement.id}", snapshot.url,
            )
        )
        if failures:
            return self.result(
                requirement, Status.FAIL,
                f"Responsive issues on {snapshot.url}: " + "; ".join(failures),
                details="; ".join(per_device),
                page_url=snapshot.url, evidence=evidence,
            )
        return self.result(
            requirement, Status.PASS,
            f"Layout verified on {len(self.ctx.session.device_profiles)} device(s): "
            + "; ".join(per_device),
            page_url=snapshot.url, evidence=evidence,
        )

    async def _check_hamburger(self, page: Any) -> bool | None:
        """True = present & opens, False = broken/missing, None = page has no collapsed nav."""
        for selector in _HAMBURGER_SELECTORS:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            if not await locator.is_visible():
                continue
            try:
                await locator.click()
                await page.wait_for_timeout(600)
                expanded = await locator.get_attribute("aria-expanded")
                nav_visible = await page.evaluate(
                    """() => {
                        const nav = document.querySelector('nav, [role="navigation"], [class*="nav" i]');
                        if (!nav) return false;
                        const r = nav.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    }"""
                )
                await locator.click()  # close it again
                return bool(nav_visible or expanded == "true")
            except Exception:
                return False
        # No hamburger found: only a failure if nav links are hidden at this width.
        nav_links_visible = await page.evaluate(
            """() => Array.from(document.querySelectorAll('nav a, header a'))
                    .some((a) => { const r = a.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"""
        )
        return None if nav_links_visible else False
