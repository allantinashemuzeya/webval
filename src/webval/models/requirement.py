"""Requirement domain models.

A ``Requirement`` is the atomic unit of traceability: everything downstream
(validation, evidence, reporting) is keyed by ``Requirement.id``.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class RequirementCategory(StrEnum):
    """Validation dimension a requirement belongs to.

    The category selects which validator(s) execute the requirement.
    """

    NAVIGATION = "Navigation"
    CONTENT = "Content"
    LINK = "Link"
    ANCHOR = "Anchor"
    METADATA = "Metadata"
    ACCESSIBILITY = "Accessibility"
    IMAGE = "Image"
    DOWNLOAD = "Download"
    VIDEO = "Video"
    RESPONSIVE = "Responsive"
    UI_BEHAVIOR = "UI Behavior"
    PERFORMANCE = "Performance"
    VISUAL = "Visual"
    GENERAL = "General"


class RequirementSource(BaseModel):
    """Where in the specification a requirement came from (audit trail)."""

    document: str = Field(description="Source file name of the specification PDF")
    page_number: int = Field(ge=1, description="1-based PDF page number")
    extraction_method: str = Field(
        description=(
            "How it was extracted: explicit_id | table | modal_sentence | "
            "link_annotation | image_caption | heading | manual"
        )
    )
    raw_text: str = Field(default="", description="Verbatim source text before normalization")


class Requirement(BaseModel):
    """Normalized, testable requirement extracted from the specification."""

    id: str = Field(pattern=r"^[A-Z]{2,4}-\d{3,4}$", description="Stable requirement ID, e.g. REQ-001")
    category: RequirementCategory = RequirementCategory.GENERAL
    requirement: str = Field(min_length=1, description="Requirement statement (normalized)")
    expected: str = Field(min_length=1, description="Expected observable result")
    source: RequirementSource
    keywords: list[str] = Field(default_factory=list, description="Search terms derived from the statement")
    target_url_hint: str | None = Field(
        default=None, description="URL or path fragment the requirement applies to, if determinable"
    )
    target_text: str | None = Field(
        default=None, description="Exact text/label under test (link text, anchor label, CTA copy...)"
    )
    priority: str = Field(default="Must", description="Must | Should | Could")

    @field_validator("requirement", "expected", mode="before")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(str(v).split())


class RequirementSet(BaseModel):
    """Central store for all requirements of a run — the traceability baseline."""

    source_document: str
    extracted_at: str = Field(description="ISO-8601 UTC timestamp of extraction")
    requirements: list[Requirement] = Field(default_factory=list)

    def __iter__(self) -> Iterator[Requirement]:  # type: ignore[override]
        return iter(self.requirements)

    def __len__(self) -> int:
        return len(self.requirements)

    def by_category(self, category: RequirementCategory) -> list[Requirement]:
        return [r for r in self.requirements if r.category is category]

    def get(self, requirement_id: str) -> Requirement | None:
        return next((r for r in self.requirements if r.id == requirement_id), None)
