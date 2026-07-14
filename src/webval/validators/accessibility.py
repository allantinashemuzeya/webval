"""Phase 8 — accessibility validation (alt text, ARIA, roles, labels).

Two modes per requirement:
  - targeted: the requirement names a specific element/text -> that element's
    accessible name/alt/aria attributes are checked.
  - audit: the requirement is page-wide ("all images must have alt text") ->
    every image, button, svg, and labelled control on the resolved page is
    audited and violations are itemized.
"""

from __future__ import annotations

from typing import Any, ClassVar

from webval.models import EvidenceKind, Requirement, RequirementCategory, Status, ValidationResult
from webval.utils.text import contains_normalized
from webval.validators.base import BaseValidator

_AUDIT_JS = """
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const name = (el) =>
    (el.getAttribute('aria-label') || '').trim() ||
    (el.getAttribute('aria-labelledby')
      ? Array.from(el.getAttribute('aria-labelledby').split(/\\s+/))
          .map((id) => (document.getElementById(id)?.innerText || '').trim()).join(' ').trim()
      : '') ||
    (el.innerText || '').trim() ||
    (el.getAttribute('title') || '').trim();

  const images = Array.from(document.querySelectorAll('img')).filter(vis).map((img) => ({
    src: img.currentSrc || img.src, alt: img.getAttribute('alt'),
    role: img.getAttribute('role'), decorative: img.getAttribute('role') === 'presentation',
  }));
  const buttons = Array.from(
    document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]')
  ).filter(vis).map((b) => ({
    tag: b.tagName.toLowerCase(), name: name(b) || (b.value || '').trim(),
    aria_label: b.getAttribute('aria-label'), html: b.outerHTML.slice(0, 300),
  }));
  const svgs = Array.from(document.querySelectorAll('svg')).filter(vis).map((s) => ({
    labelled: !!(s.getAttribute('aria-label') || s.querySelector('title') ||
                 s.getAttribute('aria-labelledby')),
    hidden: s.getAttribute('aria-hidden') === 'true' ||
            !!s.closest('[aria-hidden="true"]') || !!(s.closest('a,button') && name(s.closest('a,button'))),
    html: s.outerHTML.slice(0, 200),
  }));
  const links = Array.from(document.querySelectorAll('a[href]')).filter(vis).map((a) => ({
    href: a.href, name: name(a) || (a.querySelector('img') ? (a.querySelector('img').alt || '') : ''),
  }));
  return { images, buttons, svgs, links };
}
"""


class AccessibilityValidator(BaseValidator):
    name: ClassVar[str] = "accessibility"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset({RequirementCategory.ACCESSIBILITY})

    async def validate(self, requirement: Requirement) -> ValidationResult:
        snapshot = self.ctx.resolve_target_page(requirement)
        if snapshot is None:
            return self.result(requirement, Status.ERROR, "No crawled page available")

        page = await self.ctx.open_page(snapshot.url)
        try:
            audit: dict[str, Any] = await page.evaluate(_AUDIT_JS)
        finally:
            await page.close()

        violations = self._violations(audit)
        evidence = [
            self.ctx.store.add_json(
                EvidenceKind.DOM_SNIPPET,
                f"{requirement.id}-a11y",
                {"url": snapshot.url, "audit": audit, "violations": violations},
                f"Accessibility audit for {requirement.id}",
                snapshot.url,
            )
        ]

        # Targeted check: the spec pinned specific alt/label text.
        if requirement.target_text:
            hit = self._find_labelled(audit, requirement.target_text)
            if hit:
                return self.result(
                    requirement, Status.PASS,
                    f"Element with accessible text “{requirement.target_text}” present on {snapshot.url}",
                    details=hit, page_url=snapshot.url, evidence=evidence,
                )
            # Spec text may be OCR'd from an annotated proof — try fuzzy match
            # before failing, and report it as a Warning for human confirmation.
            fuzzy_hit, ratio = self._find_labelled_fuzzy(audit, requirement.target_text)
            if fuzzy_hit and ratio >= 0.72:
                return self.result(
                    requirement, Status.WARNING,
                    f"Close accessible-text match on {snapshot.url} (similarity {ratio:.2f}): {fuzzy_hit} "
                    f"— expected “{requirement.target_text}” (spec text may contain OCR noise; confirm manually)",
                    page_url=snapshot.url, evidence=evidence,
                )
            return self.result(
                requirement, Status.FAIL,
                f"No element exposes accessible text “{requirement.target_text}” on {snapshot.url}",
                details=f"best fuzzy similarity {ratio:.2f}" if fuzzy_hit else "",
                page_url=snapshot.url, evidence=evidence,
            )

        # Page-wide audit.
        totals = (
            f"{len(audit['images'])} images, {len(audit['buttons'])} buttons, "
            f"{len(audit['svgs'])} svgs, {len(audit['links'])} links audited"
        )
        if not violations:
            return self.result(
                requirement, Status.PASS,
                f"No accessibility violations on {snapshot.url} ({totals})",
                page_url=snapshot.url, evidence=evidence,
            )
        summary = "; ".join(violations[:8]) + ("; ..." if len(violations) > 8 else "")
        return self.result(
            requirement, Status.FAIL,
            f"{len(violations)} accessibility violation(s) on {snapshot.url}: {summary}",
            details=totals, page_url=snapshot.url, evidence=evidence,
        )

    @staticmethod
    def _violations(audit: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for img in audit["images"]:
            if img["alt"] is None and not img["decorative"]:
                out.append(f"img missing alt attribute: {img['src']}")
            elif img["alt"] is not None and not img["alt"].strip() and not img["decorative"]:
                out.append(f"img has empty alt (not marked decorative): {img['src']}")
        for btn in audit["buttons"]:
            if not btn["name"]:
                out.append(f"button without accessible name: {btn['html'][:100]}")
        for svg in audit["svgs"]:
            if not svg["labelled"] and not svg["hidden"]:
                out.append(f"svg neither labelled nor aria-hidden: {svg['html'][:80]}")
        for link in audit["links"]:
            if not link["name"].strip():
                out.append(f"link without accessible name: {link['href']}")
        return out

    @staticmethod
    def _find_labelled_fuzzy(audit: dict[str, Any], text: str) -> tuple[str, float]:
        from webval.utils.text import fuzzy_ratio, normalize_text

        best, best_ratio = "", 0.0
        for kind, items, key in (("img alt", audit["images"], "alt"),
                                 ("button", audit["buttons"], "name"),
                                 ("link", audit["links"], "name")):
            for item in items:
                value = item.get(key) or ""
                ratio = fuzzy_ratio(normalize_text(value), normalize_text(text))
                if ratio > best_ratio:
                    best, best_ratio = f"{kind}={value!r}", ratio
        return best, best_ratio

    @staticmethod
    def _find_labelled(audit: dict[str, Any], text: str) -> str:
        for img in audit["images"]:
            if img["alt"] and contains_normalized(img["alt"], text):
                return f"img alt={img['alt']!r} src={img['src']}"
        for btn in audit["buttons"]:
            if btn["name"] and contains_normalized(btn["name"], text):
                return f"button name={btn['name']!r}"
        for link in audit["links"]:
            if link["name"] and contains_normalized(link["name"], text):
                return f"link name={link['name']!r} href={link['href']}"
        return ""
