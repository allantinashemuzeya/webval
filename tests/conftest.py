"""Shared fixtures: settings, synthetic documents, and synthetic runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from webval.config import Settings
from webval.models import (
    Evidence,
    EvidenceKind,
    PdfDocument,
    PdfLink,
    PdfPage,
    PdfTable,
    Requirement,
    RequirementCategory,
    RequirementSet,
    RequirementSource,
    RunManifest,
    Status,
    ValidationResult,
    ValidationRun,
)


@pytest.fixture()
def settings() -> Settings:
    return Settings()


def _source(page: int = 1, method: str = "table", raw: str = "") -> RequirementSource:
    return RequirementSource(document="spec.pdf", page_number=page, extraction_method=method, raw_text=raw)


@pytest.fixture()
def sample_pdf_document() -> PdfDocument:
    """Synthetic parsed spec exercising every extraction pass."""
    table = PdfTable(
        page_number=1,
        index=0,
        headers=["Req ID", "Requirement Description", "Expected Result", "Category"],
        rows=[
            ["REQ-1", "About mHSPC anchor exists in the top navigation", "Anchor displayed and functioning", "Anchor"],
            ["REQ-2", 'Homepage displays the headline "Now Approved for mHSPC"', "Headline present verbatim", "Content"],
            ["", "All images must include descriptive alt text", "No image lacks alt text", "Accessibility"],
        ],
    )
    page1 = PdfPage(
        page_number=1,
        text=(
            "Introduction\n"
            "REQ-10: The footer must contain a link to the Privacy Policy.\n"
            # pdfplumber's text stream repeats table cells; the engine must not
            # re-extract rows whose ID the table pass already claimed:
            "REQ-1 About mHSPC anchor exists in the top navigation Anchor displayed and functioning.\n"
            "The site shall display the full Important Safety Information on every page. "
            "Navigation should include a Dosing tab. Short line.\n"
        ),
        tables=[table],
        links=[PdfLink(page_number=1, url="https://www.pluvicto.com/", anchor_text="PLUVICTO Home")],
    )
    page2 = PdfPage(
        page_number=2,
        text="The downloadable patient brochure must be available on the resources page.",
    )
    return PdfDocument(
        file_name="spec.pdf", sha256="0" * 64, page_count=2,
        metadata={"title": "Site Spec"}, pages=[page1, page2],
    )


def make_requirement(
    req_id: str = "REQ-001",
    category: RequirementCategory = RequirementCategory.CONTENT,
    requirement: str = "Homepage displays the approved headline",
    expected: str = "Headline present verbatim",
    **kwargs,
) -> Requirement:
    return Requirement(
        id=req_id, category=category, requirement=requirement,
        expected=expected, source=_source(), **kwargs,
    )


@pytest.fixture()
def sample_run(tmp_path: Path) -> ValidationRun:
    reqs = RequirementSet(
        source_document="spec.pdf",
        extracted_at="2026-07-15T00:00:00+00:00",
        requirements=[
            make_requirement("REQ-001", RequirementCategory.CONTENT),
            make_requirement("REQ-002", RequirementCategory.LINK, "Footer links to Privacy Policy"),
            make_requirement("REQ-003", RequirementCategory.IMAGE, "Hero image renders"),
            make_requirement("REQ-004", RequirementCategory.ANCHOR, "About mHSPC anchor works"),
        ],
    )
    shot = tmp_path / "evidence" / "screenshots" / "0001-fail.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    results = [
        ValidationResult(
            requirement_id="REQ-001", status=Status.PASS,
            expected="Headline present verbatim", actual="Found verbatim on homepage",
            page_url="https://example.test/", finished_at="2026-07-15T00:01:00+00:00",
        ),
        ValidationResult(
            requirement_id="REQ-002", status=Status.FAIL,
            expected="Link resolves", actual="HTTP 404 on /privacy",
            page_url="https://example.test/",
            evidence=[Evidence(kind=EvidenceKind.SCREENSHOT, path="evidence/screenshots/0001-fail.png",
                               description="failure state", sha256="abc")],
            finished_at="2026-07-15T00:02:00+00:00",
        ),
        ValidationResult(
            requirement_id="REQ-003", status=Status.WARNING,
            expected="Image renders", actual="Image renders but is scaled down",
            finished_at="2026-07-15T00:03:00+00:00",
        ),
        # REQ-004 intentionally not executed -> Not Tested
    ]
    manifest = RunManifest(
        run_id="20260715-000000-example.test",
        started_at="2026-07-15T00:00:00+00:00",
        finished_at="2026-07-15T00:05:00+00:00",
        base_url="https://example.test/",
        spec_document="spec.pdf",
        spec_sha256="f" * 64,
        tool_version="1.0.0",
    )
    return ValidationRun(manifest=manifest, requirements=reqs, results=results)
