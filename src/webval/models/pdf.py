"""Models describing the parsed specification PDF."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PdfLink(BaseModel):
    """Hyperlink found in the PDF (annotation or plain-text URL)."""

    page_number: int
    url: str
    anchor_text: str = ""


class PdfImage(BaseModel):
    """Image extracted from the PDF (candidate for visual comparison)."""

    page_number: int
    index: int
    path: str = Field(description="Filesystem path of the extracted image")
    width: int
    height: int
    caption: str = Field(default="", description="Nearby caption text, if detected")


class PdfTable(BaseModel):
    """Table extracted from a PDF page."""

    page_number: int
    index: int
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class PdfPage(BaseModel):
    """One parsed page of the specification."""

    page_number: int
    text: str = ""
    ocr: bool = Field(
        default=False,
        description="True when text came from OCR (image-only page) — downstream passes treat it as noisy",
    )
    tables: list[PdfTable] = Field(default_factory=list)
    images: list[PdfImage] = Field(default_factory=list)
    links: list[PdfLink] = Field(default_factory=list)


class PdfDocument(BaseModel):
    """Full parsed specification document."""

    file_name: str
    sha256: str = Field(description="Hash of the source PDF for audit traceability")
    page_count: int
    metadata: dict[str, str] = Field(default_factory=dict)
    pages: list[PdfPage] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)
