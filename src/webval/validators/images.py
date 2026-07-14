"""Phase 9 — image validation (presence, successful load, dimensions, broken assets)."""

from __future__ import annotations

from typing import Any, ClassVar

from webval.models import Evidence, EvidenceKind, Requirement, RequirementCategory, Status, ValidationResult
from webval.utils.text import contains_normalized, normalize_text
from webval.validators.base import BaseValidator

_IMAGE_STATE_JS = """
() => Array.from(document.querySelectorAll('img')).map((img) => {
  const r = img.getBoundingClientRect();
  return {
    src: img.currentSrc || img.src || img.getAttribute('data-src') || '',
    alt: img.getAttribute('alt') || '',
    complete: img.complete,
    naturalWidth: img.naturalWidth,
    naturalHeight: img.naturalHeight,
    renderedWidth: Math.round(r.width),
    renderedHeight: Math.round(r.height),
    broken: img.complete && img.naturalWidth === 0,
  };
})
"""


class ImageValidator(BaseValidator):
    name: ClassVar[str] = "images"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset({RequirementCategory.IMAGE})

    async def validate(self, requirement: Requirement) -> ValidationResult:
        snapshot = self.ctx.resolve_target_page(requirement)
        if snapshot is None:
            return self.result(requirement, Status.ERROR, "No crawled page available")

        page = await self.ctx.open_page(snapshot.url)
        try:
            images: list[dict[str, Any]] = await page.evaluate(_IMAGE_STATE_JS)
            evidence = [
                self.ctx.store.add_json(
                    EvidenceKind.DOM_SNIPPET,
                    f"{requirement.id}-images",
                    {"url": snapshot.url, "images": images},
                    f"Image load states for {requirement.id}",
                    snapshot.url,
                )
            ]

            target = self._match_target(images, requirement)
            if requirement.target_text or requirement.keywords:
                if target is None:
                    # Only fail if the requirement clearly names an image; a
                    # keyword miss on a generic statement audits the page instead.
                    if requirement.target_text:
                        return self.result(
                            requirement, Status.FAIL,
                            f"No image matching “{requirement.target_text}” found on {snapshot.url}",
                            page_url=snapshot.url, evidence=evidence,
                        )
                else:
                    evidence.append(await self._element_shot(page, requirement, target, snapshot.url))
                    if target["broken"] or (target["complete"] and target["naturalWidth"] == 0):
                        return self.result(
                            requirement, Status.FAIL,
                            f"Image {target['src']} is present but failed to load (naturalWidth=0)",
                            page_url=snapshot.url, evidence=evidence,
                        )
                    return self.result(
                        requirement, Status.PASS,
                        f"Image {target['src']} loaded successfully "
                        f"({target['naturalWidth']}x{target['naturalHeight']}, "
                        f"rendered {target['renderedWidth']}x{target['renderedHeight']})",
                        page_url=snapshot.url, evidence=evidence,
                    )

            # Page-wide broken-image audit.
            broken = [i for i in images if i["broken"]]
            if broken:
                return self.result(
                    requirement, Status.FAIL,
                    f"{len(broken)} broken image(s) on {snapshot.url}: "
                    + "; ".join(b["src"] for b in broken[:5]),
                    details=f"{len(images)} images audited",
                    page_url=snapshot.url, evidence=evidence,
                )
            return self.result(
                requirement, Status.PASS,
                f"All {len(images)} images on {snapshot.url} loaded successfully",
                page_url=snapshot.url, evidence=evidence,
            )
        finally:
            await page.close()

    @staticmethod
    def _match_target(images: list[dict[str, Any]], requirement: Requirement) -> dict[str, Any] | None:
        text = requirement.target_text
        if text:
            for img in images:
                if contains_normalized(img["alt"], text) or contains_normalized(img["src"], text):
                    return img
        best, best_hits = None, 0
        for img in images:
            blob = normalize_text(f"{img['alt']} {img['src']}")
            hits = sum(1 for kw in requirement.keywords if kw in blob)
            if hits > best_hits:
                best, best_hits = img, hits
        return best if best_hits >= 2 else None

    async def _element_shot(self, page: Any, requirement: Requirement, target: dict[str, Any], url: str) -> Evidence:
        path = self.ctx.store.new_path(EvidenceKind.ELEMENT_SCREENSHOT, f"{requirement.id}-image", ".png")
        try:
            locator = page.locator(f'img[src*="{target["src"].split("/")[-1].split("?")[0]}"]').first
            await locator.scroll_into_view_if_needed()
            await locator.screenshot(path=str(path))
        except Exception:
            await page.screenshot(path=str(path))  # fall back to viewport
        return self.ctx.store.add_file(
            EvidenceKind.ELEMENT_SCREENSHOT, path, f"{requirement.id}: image element", url
        )
