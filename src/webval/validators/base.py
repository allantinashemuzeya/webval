"""Validator framework: shared context, base class, dispatch, and runner.

Adding a validator = subclass ``BaseValidator``, declare ``categories``,
implement ``validate``, and list it in ``registry.default_validators()``.
The runner dispatches every requirement to the first validator claiming its
category; anything unclaimed is reported as NOT TESTED (never silently
dropped — the matrix must account for 100% of requirements).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import ClassVar

from playwright.async_api import Page

from webval.config import Settings
from webval.crawler import BrowserSession
from webval.evidence import EvidenceStore
from webval.models import (
    Evidence,
    PageSnapshot,
    PdfDocument,
    Requirement,
    RequirementCategory,
    RequirementSet,
    SiteMap,
    Status,
    ValidationResult,
)
from webval.utils import get_logger, utc_now_iso
from webval.utils.text import contains_normalized, normalize_text

log = get_logger("validators")


class ValidationContext:
    """Everything a validator may need for a run."""

    def __init__(
        self,
        settings: Settings,
        session: BrowserSession,
        site_map: SiteMap,
        store: EvidenceStore,
        pdf_docs: list[PdfDocument] | None = None,
    ) -> None:
        self.settings = settings
        self.session = session
        self.site_map = site_map
        self.store = store
        self.pdf_docs = pdf_docs or []

    # ------------------------------------------------------- target resolution

    def resolve_target_page(self, requirement: Requirement) -> PageSnapshot | None:
        """Best-effort mapping of a requirement onto a crawled page.

        Order: explicit URL hint -> page containing the target text ->
        page whose text hits the most requirement keywords -> homepage.
        """
        if requirement.target_url_hint:
            hint = requirement.target_url_hint.rstrip("/")
            for page in self.site_map.pages:
                if page.url.rstrip("/") == hint or page.final_url.rstrip("/") == hint:
                    return page
            for page in self.site_map.pages:
                if hint.lower() in page.url.lower():
                    return page

        if requirement.target_text:
            for page in self.site_map.pages:
                if contains_normalized(page.visible_text, requirement.target_text):
                    return page
                if any(contains_normalized(link.text, requirement.target_text) for link in page.links):
                    return page

        if requirement.keywords:
            best, best_hits = None, 0
            for page in self.site_map.pages:
                text = normalize_text(page.visible_text)
                hits = sum(1 for kw in requirement.keywords if kw in text)
                if hits > best_hits:
                    best, best_hits = page, hits
            if best is not None and best_hits >= max(2, len(requirement.keywords) // 3):
                return best

        return self.site_map.pages[0] if self.site_map.pages else None

    async def open_page(self, url: str, profile: str = "Desktop Chrome") -> Page:
        """Navigate a fresh authenticated page to ``url``."""
        page = await self.session.new_page(profile)
        await page.goto(url, wait_until="networkidle")
        return page


class BaseValidator(ABC):
    """One validation engine covering one or more requirement categories."""

    name: ClassVar[str] = "base"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset()

    def __init__(self, ctx: ValidationContext) -> None:
        self.ctx = ctx
        self.log = get_logger(f"validators.{self.name}")

    @abstractmethod
    async def validate(self, requirement: Requirement) -> ValidationResult: ...

    def result(
        self,
        requirement: Requirement,
        status: Status,
        actual: str,
        details: str = "",
        page_url: str = "",
        evidence: list[Evidence] | None = None,
    ) -> ValidationResult:
        return ValidationResult(
            requirement_id=requirement.id,
            status=status,
            expected=requirement.expected,
            actual=actual,
            details=details,
            page_url=page_url,
            validator=self.name,
            evidence=evidence or [],
            finished_at=utc_now_iso(),
        )


class ValidatorRunner:
    """Dispatches every requirement to its validator and aggregates results."""

    def __init__(self, ctx: ValidationContext, validators: list[BaseValidator]) -> None:
        self.ctx = ctx
        self.validators = validators

    def _validator_for(self, requirement: Requirement) -> BaseValidator | None:
        return next((v for v in self.validators if requirement.category in v.categories), None)

    async def run(self, requirements: RequirementSet) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        total = len(requirements)
        for index, requirement in enumerate(requirements, 1):
            validator = self._validator_for(requirement)
            started = utc_now_iso()
            t0 = time.monotonic()
            if validator is None:
                result = ValidationResult(
                    requirement_id=requirement.id,
                    status=Status.NOT_TESTED,
                    expected=requirement.expected,
                    actual="No validator registered for category "
                    f"'{requirement.category.value}' — manual verification required",
                    validator="none",
                )
            else:
                log.info("[%d/%d] %s (%s) -> %s", index, total, requirement.id,
                         requirement.category.value, validator.name)
                try:
                    result = await validator.validate(requirement)
                except Exception as exc:
                    log.exception("Validator %s crashed on %s", validator.name, requirement.id)
                    result = validator.result(
                        requirement,
                        Status.ERROR,
                        actual=f"Validator error: {exc}",
                        details="Unhandled exception — see execution log",
                    )
            result.started_at = started
            result.duration_ms = int((time.monotonic() - t0) * 1000)
            if not result.finished_at:
                result.finished_at = utc_now_iso()
            results.append(result)
        return results
