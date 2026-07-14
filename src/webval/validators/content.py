"""Phase 5 — content validation (headings, copy, disclaimers, claims, CTA text).

Matching strategy per requirement:
  1. exact match of the normalized target text anywhere on the resolved page
     (or any page if unresolved) -> PASS
  2. best sliding-window fuzzy ratio >= threshold -> WARNING (altered content)
  3. otherwise -> FAIL (missing content)

Duplicate occurrences of the target text on one page are flagged in details.
"""

from __future__ import annotations

import re
from typing import ClassVar

from webval.models import (
    Evidence,
    EvidenceKind,
    PageSnapshot,
    Requirement,
    RequirementCategory,
    Status,
    ValidationResult,
)
from webval.utils.text import best_window_ratio, normalize_text
from webval.validators.base import BaseValidator

_MODAL_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:page|site|website|section|footer|header)?\s*"
    r"(?:shall|must|should|will)\s+(?:display|show|contain|include|state|present)\s*:?\s*",
    re.IGNORECASE,
)


class ContentValidator(BaseValidator):
    name: ClassVar[str] = "content"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset(
        {RequirementCategory.CONTENT, RequirementCategory.GENERAL}
    )

    async def validate(self, requirement: Requirement) -> ValidationResult:
        needle = self._needle(requirement)
        if not needle:
            return self.result(
                requirement, Status.NOT_TESTED,
                "No verifiable text could be derived from the requirement — manual review required",
            )

        target = self.ctx.resolve_target_page(requirement)
        pages = [target] if target else []
        if not pages:
            pages = self.ctx.site_map.pages
        # Exact match on the resolved page first, then across the whole site.
        search_space = pages + [p for p in self.ctx.site_map.pages if p not in pages]

        exact_page = next(
            (p for p in search_space if normalize_text(needle) in normalize_text(p.visible_text)), None
        )
        if exact_page is not None:
            occurrences = normalize_text(exact_page.visible_text).count(normalize_text(needle))
            evidence = [self._snippet_evidence(requirement, exact_page, needle)]
            details = f"occurrences={occurrences}"
            if occurrences > 1:
                details += " — duplicate content sections detected"
            return self.result(
                requirement,
                Status.PASS if occurrences == 1 else Status.WARNING,
                f"Content found verbatim on {exact_page.url}"
                + ("" if occurrences == 1 else f" ({occurrences} duplicate occurrences)"),
                details=details,
                page_url=exact_page.url,
                evidence=evidence,
            )

        # Fuzzy: content may have been altered.
        best_page, best_ratio = None, 0.0
        for page in search_space:
            ratio = best_window_ratio(page.visible_text, needle)
            if ratio > best_ratio:
                best_page, best_ratio = page, ratio
        threshold = self.ctx.settings.validation.content_fuzzy_threshold
        if best_page is not None and best_ratio >= threshold:
            evidence = [self._snippet_evidence(requirement, best_page, needle)]
            return self.result(
                requirement, Status.WARNING,
                f"Similar but not identical content found on {best_page.url} "
                f"(similarity {best_ratio:.2f}) — content may have been altered",
                details=f"expected text: {needle!r}",
                page_url=best_page.url,
                evidence=evidence,
            )

        checked = target.url if target else f"{len(search_space)} crawled pages"
        return self.result(
            requirement, Status.FAIL,
            f"Required content not found (best similarity {best_ratio:.2f} on "
            f"{best_page.url if best_page else 'n/a'})",
            details=f"expected text: {needle!r}; searched: {checked}",
            page_url=best_page.url if best_page else "",
        )

    def _needle(self, requirement: Requirement) -> str:
        if requirement.target_text:
            return requirement.target_text
        stripped = _MODAL_PREFIX_RE.sub("", requirement.requirement).strip()
        # A bare modal sentence with nothing quotable is only testable if it
        # reads like copy (long enough to be a content fragment).
        return stripped if len(stripped) >= self.ctx.settings.pdf.min_requirement_length else ""

    def _snippet_evidence(self, requirement: Requirement, page: PageSnapshot, needle: str) -> Evidence:
        norm_text = normalize_text(page.visible_text, casefold=False)
        idx = normalize_text(page.visible_text).find(normalize_text(needle))
        window = norm_text[max(0, idx - 200) : idx + len(needle) + 200] if idx >= 0 else norm_text[:400]
        return self.ctx.store.add_text(
            EvidenceKind.DOM_SNIPPET,
            f"{requirement.id}-content",
            f"URL: {page.url}\nSearched: {needle}\n\n--- context ---\n{window}",
            f"Content match context for {requirement.id}",
            page.url,
        )
