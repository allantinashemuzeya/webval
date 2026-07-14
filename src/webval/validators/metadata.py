"""Phase 4 — metadata validation (title, description, canonical, robots, OG, H1, JSON-LD)."""

from __future__ import annotations

from typing import ClassVar

from webval.models import (
    EvidenceKind,
    PageSnapshot,
    Requirement,
    RequirementCategory,
    Status,
    ValidationResult,
)
from webval.utils.text import best_window_ratio, contains_normalized, normalize_text
from webval.validators.base import BaseValidator

_FIELD_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("title", ("page title", "title tag", "browser title", "<title>")),
    ("description", ("meta description",)),
    ("canonical", ("canonical",)),
    ("robots", ("robots",)),
    ("og", ("open graph", "og:")),
    ("h1", ("h1", "main heading", "page heading")),
    ("structured_data", ("structured data", "json-ld", "schema.org")),
]


class MetadataValidator(BaseValidator):
    name: ClassVar[str] = "metadata"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset({RequirementCategory.METADATA})

    async def validate(self, requirement: Requirement) -> ValidationResult:
        page = self.ctx.resolve_target_page(requirement)
        if page is None:
            return self.result(requirement, Status.ERROR, "No crawled page available to validate against")

        field = self._detect_field(requirement.requirement)
        actual_value, present = self._actual(page, field)
        evidence = [
            self.ctx.store.add_json(
                EvidenceKind.DOM_SNIPPET,
                f"{requirement.id}-metadata",
                {"url": page.url, "field": field, "title": page.title, "meta": page.meta, "h1": page.h1},
                f"Metadata captured for {requirement.id}",
                page.url,
            )
        ]

        expected_value = requirement.target_text
        if expected_value:
            if contains_normalized(actual_value, expected_value):
                return self.result(
                    requirement, Status.PASS,
                    f"{field or 'metadata'} matches: {actual_value!r}", page_url=page.url, evidence=evidence,
                )
            ratio = best_window_ratio(actual_value, expected_value)
            status = Status.WARNING if ratio >= self.ctx.settings.validation.content_fuzzy_threshold else Status.FAIL
            return self.result(
                requirement, status,
                f"{field or 'metadata'} is {actual_value!r}, expected to contain {expected_value!r}",
                details=f"similarity={ratio:.2f}", page_url=page.url, evidence=evidence,
            )

        # No explicit expected value in the spec: verify presence/non-emptiness.
        if present and normalize_text(actual_value):
            return self.result(
                requirement, Status.PASS,
                f"{field or 'metadata'} present: {actual_value[:180]!r}", page_url=page.url, evidence=evidence,
            )
        return self.result(
            requirement, Status.FAIL,
            f"{field or 'requested metadata'} missing or empty on {page.url}",
            page_url=page.url, evidence=evidence,
        )

    @staticmethod
    def _detect_field(text: str) -> str | None:
        lowered = text.lower()
        for field, hints in _FIELD_HINTS:
            if any(h in lowered for h in hints):
                return field
        return None

    @staticmethod
    def _actual(page: PageSnapshot, field: str | None) -> tuple[str, bool]:
        if field == "title":
            return page.title, bool(page.title)
        if field == "description":
            value = page.meta.get("description", "")
            return value, "description" in page.meta
        if field == "canonical":
            value = page.meta.get("canonical", "")
            return value, "canonical" in page.meta
        if field == "robots":
            value = page.meta.get("robots", "")
            return value, "robots" in page.meta
        if field == "og":
            og = {k: v for k, v in page.meta.items() if k.startswith("og:")}
            return "; ".join(f"{k}={v}" for k, v in og.items()), bool(og)
        if field == "h1":
            return " | ".join(page.h1), bool(page.h1)
        if field == "structured_data":
            kinds = [str(d.get("@type", "?")) for d in page.structured_data]
            return f"JSON-LD blocks: {kinds}" if kinds else "", bool(kinds)
        # Unknown field: aggregate everything searchable.
        blob = " | ".join([page.title, *page.h1, *(f"{k}={v}" for k, v in page.meta.items())])
        return blob, bool(blob)
