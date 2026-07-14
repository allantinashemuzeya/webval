"""Evidence models — every validation claim must point at stored proof."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class EvidenceKind(StrEnum):
    SCREENSHOT = "screenshot"
    ELEMENT_SCREENSHOT = "element_screenshot"
    DOM_SNIPPET = "dom_snippet"
    HTML = "html"
    DOWNLOAD = "download"
    LOG = "log"
    NETWORK = "network"
    VISUAL_DIFF = "visual_diff"
    METRIC = "metric"


class Evidence(BaseModel):
    """A single stored artifact backing a validation result.

    ``path`` is relative to the run's evidence root so reports stay portable
    when the run folder is archived.
    """

    kind: EvidenceKind
    path: str = Field(description="Path relative to the run directory")
    description: str = ""
    sha256: str = Field(default="", description="Integrity hash of the artifact")
    page_url: str = ""
    captured_at: str = Field(default="", description="ISO-8601 UTC timestamp")
