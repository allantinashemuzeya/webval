"""Unit tests for PDF extraction against a real generated PDF (PyMuPDF fixture)."""

from pathlib import Path

import fitz
import pytest

from webval.pdf_parser import PdfExtractor


@pytest.fixture()
def spec_pdf(tmp_path: Path) -> Path:
    """Generate a two-page spec PDF with text and a link annotation."""
    path = tmp_path / "spec.pdf"
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text(
        (72, 100),
        "Website Specification\n"
        "REQ-01: The homepage must display the headline 'Now Approved'.\n"
        "The footer shall include a link to the Privacy Policy.",
    )
    rect = fitz.Rect(72, 200, 220, 215)
    page1.insert_text((72, 212), "Visit https://www.pluvicto.com/ for details")
    page1.insert_link({"kind": fitz.LINK_URI, "from": rect, "uri": "https://www.pluvicto.com/"})
    page2 = doc.new_page()
    page2.insert_text((72, 100), "All images must include alt text.")
    doc.set_metadata({"title": "Spec Doc", "author": "QA"})
    doc.save(path)
    doc.close()
    return path


class TestPdfExtractor:
    def test_extracts_pages_text_metadata(self, settings, spec_pdf: Path, tmp_path: Path):
        doc = PdfExtractor(settings, image_output_dir=tmp_path / "img").extract(spec_pdf)
        assert doc.page_count == 2
        assert doc.file_name == "spec.pdf"
        assert len(doc.sha256) == 64
        assert "Now Approved" in doc.pages[0].text
        assert "alt text" in doc.pages[1].text
        assert doc.metadata.get("title") == "Spec Doc"

    def test_extracts_link_annotation_and_plaintext_url(self, settings, spec_pdf: Path, tmp_path: Path):
        doc = PdfExtractor(settings, image_output_dir=tmp_path / "img").extract(spec_pdf)
        urls = {link.url for link in doc.pages[0].links}
        assert "https://www.pluvicto.com/" in urls

    def test_missing_file_raises(self, settings, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            PdfExtractor(settings).extract(tmp_path / "nope.pdf")
