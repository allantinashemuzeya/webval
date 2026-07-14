"""Pydantic domain models shared across the framework."""

from webval.models.evidence import Evidence, EvidenceKind
from webval.models.page import AssetRef, LinkRef, PageSnapshot, SiteMap
from webval.models.pdf import PdfDocument, PdfImage, PdfLink, PdfPage, PdfTable
from webval.models.requirement import (
    Requirement,
    RequirementCategory,
    RequirementSet,
    RequirementSource,
)
from webval.models.result import (
    DefectSummary,
    RunManifest,
    RunSummary,
    Status,
    ValidationResult,
    ValidationRun,
)

__all__ = [
    "AssetRef",
    "DefectSummary",
    "Evidence",
    "EvidenceKind",
    "LinkRef",
    "PageSnapshot",
    "PdfDocument",
    "PdfImage",
    "PdfLink",
    "PdfPage",
    "PdfTable",
    "Requirement",
    "RequirementCategory",
    "RequirementSet",
    "RequirementSource",
    "RunManifest",
    "RunSummary",
    "SiteMap",
    "Status",
    "ValidationResult",
    "ValidationRun",
]
