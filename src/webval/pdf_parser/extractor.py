"""PDF extraction: text + tables via pdfplumber, images/links/metadata via PyMuPDF.

Two engines are deliberately combined:
  - pdfplumber gives layout-aware text and reliable table geometry
  - PyMuPDF (fitz) gives embedded images, link annotations, doc metadata,
    and (optionally) rasterized pages for OCR fallback

Output is a fully serializable ``PdfDocument`` so extraction is reproducible
and auditable independent of downstream requirement mining.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import fitz  # PyMuPDF
import pdfplumber

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

from webval.config import Settings
from webval.models import PdfDocument, PdfImage, PdfLink, PdfPage, PdfTable
from webval.utils import get_logger, sha256_file
from webval.utils.text import normalize_text

log = get_logger("pdf.extractor")

_URL_RE = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)
_MIN_IMAGE_DIM = 80  # skip decorative slivers / bullets



class PdfExtractor:
    """Parses a specification PDF into a structured PdfDocument."""

    def __init__(self, settings: Settings, image_output_dir: Path | None = None) -> None:
        self._settings = settings
        self._image_dir = image_output_dir

    def extract(self, pdf_path: Path) -> PdfDocument:
        pdf_path = pdf_path.resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError(f"Specification PDF not found: {pdf_path}")
        log.info("Parsing specification PDF: %s", pdf_path.name)

        doc = PdfDocument(
            file_name=pdf_path.name,
            sha256=sha256_file(pdf_path),
            page_count=0,
            metadata={},
        )

        with fitz.open(pdf_path) as fz, pdfplumber.open(pdf_path) as plumber:
            doc.page_count = fz.page_count
            doc.metadata = {k: str(v) for k, v in (fz.metadata or {}).items() if v}
            for page_index in range(fz.page_count):
                page_number = page_index + 1
                fz_page = fz[page_index]
                pl_page = plumber.pages[page_index]
                text, used_ocr = self._extract_text(pl_page, fz_page)
                page = PdfPage(
                    page_number=page_number,
                    text=text,
                    ocr=used_ocr,
                    tables=self._extract_tables(pl_page, page_number),
                    links=self._extract_links(fz_page, page_number),
                )
                if self._settings.pdf.extract_images and self._image_dir is not None:
                    page.images = self._extract_images(fz, fz_page, page_number)
                doc.pages.append(page)

        log.info(
            "Parsed %d pages: %d tables, %d links, %d images",
            doc.page_count,
            sum(len(p.tables) for p in doc.pages),
            sum(len(p.links) for p in doc.pages),
            sum(len(p.images) for p in doc.pages),
        )
        return doc

    # ------------------------------------------------------------------ text

    def _extract_text(self, pl_page: pdfplumber.page.Page, fz_page: fitz.Page) -> tuple[str, bool]:
        """Return (text, used_ocr). Image-only pages fall back to OCR when enabled."""
        text = pl_page.extract_text() or ""
        if not text.strip():
            # image-only page: fall back to PyMuPDF, then OCR if enabled
            text = fz_page.get_text("text") or ""
        if not text.strip() and self._settings.pdf.ocr_enabled:
            ocr_text = self._ocr_page(fz_page)
            if ocr_text.strip():
                log.info("Page %d has no text layer — recovered %d chars via OCR",
                         fz_page.number + 1, len(ocr_text))
                return ocr_text, True
        return text, False

    def _ocr_page(self, fz_page: fitz.Page) -> str:
        """Two-pass OCR for annotated-proof pages.

        Pass 1 reads the page raster (body copy). Pass 2 locates annotation
        keywords (alt / links / clicking), crops each callout region, upscales
        it 3x, and re-OCRs — small callout text is unreadable at page scale
        but legible once cropped and enlarged. Callout lines are appended to
        the page text for the requirement engine's annotation pass.

        Works with either OCR backend (tesseract or the pure-pip RapidOCR
        fallback — see pdf_parser.ocr).
        """
        from PIL import Image, ImageOps

        from webval.pdf_parser.ocr import get_backend

        backend = get_backend(self._settings.pdf.ocr_backend)
        if backend is None:
            log.warning("No OCR backend available — page %d cannot be read", fz_page.number + 1)
            return ""
        try:
            pix = fz_page.get_pixmap(dpi=self._settings.pdf.ocr_dpi)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            gray = ImageOps.grayscale(img)
            body_text = backend.text(gray)
            callouts = self._ocr_callout_regions(gray, backend)
            if callouts:
                return body_text + "\n\n" + "\n\n".join(callouts)
            return body_text
        except Exception as exc:  # corrupt page, engine failure, ...
            log.warning("OCR (%s) failed on page %d: %s", backend.name, fz_page.number + 1, exc)
            return ""

    @staticmethod
    def _ocr_callout_regions(gray: PILImage, backend: object) -> list[str]:
        """Locate annotation keywords and re-OCR each callout crop at 3x."""
        from PIL import Image, ImageOps

        keywords = {"alt", "links", "link", "clicking"}
        hits: list[tuple[int, int]] = []
        for hit in backend.locate(gray, keywords):  # type: ignore[attr-defined]
            # skip near-duplicates (same callout matched via two keywords)
            if all(abs(hit.x - hx) > 60 or abs(hit.y - hy) > 60 for hx, hy in hits):
                hits.append((hit.x, hit.y))

        width, height = gray.size
        out: list[str] = []
        for x, y in hits:
            crop = gray.crop(
                (
                    max(0, x - int(width * 0.06)),
                    max(0, y - int(height * 0.02)),
                    min(width, x + int(width * 0.16)),
                    min(height, y + int(height * 0.08)),
                )
            )
            crop = crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.LANCZOS)
            crop = ImageOps.autocontrast(crop, cutoff=2)
            text = backend.crop_text(crop)  # type: ignore[attr-defined]
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                out.append("\n".join(lines))
        return out

    # ---------------------------------------------------------------- tables

    def _extract_tables(self, pl_page: pdfplumber.page.Page, page_number: int) -> list[PdfTable]:
        tables: list[PdfTable] = []
        for idx, raw in enumerate(pl_page.extract_tables() or []):
            rows = [[normalize_text(cell or "", casefold=False) for cell in row] for row in raw if row]
            rows = [r for r in rows if any(c for c in r)]
            if not rows:
                continue
            headers = rows[0] if len(rows) > 1 else []
            body = rows[1:] if headers else rows
            tables.append(PdfTable(page_number=page_number, index=idx, headers=headers, rows=body))
        return tables

    # ----------------------------------------------------------------- links

    def _extract_links(self, fz_page: fitz.Page, page_number: int) -> list[PdfLink]:
        links: list[PdfLink] = []
        seen: set[str] = set()
        for link in fz_page.get_links():
            uri = link.get("uri")
            if not uri or uri in seen:
                continue
            seen.add(uri)
            anchor_text = ""
            rect = link.get("from")
            if rect is not None:
                anchor_text = normalize_text(fz_page.get_textbox(rect), casefold=False)
            links.append(PdfLink(page_number=page_number, url=uri, anchor_text=anchor_text))
        # plain-text URLs not wrapped in annotations
        for match in _URL_RE.finditer(fz_page.get_text("text") or ""):
            url = match.group(0).rstrip(".,;")
            if url not in seen:
                seen.add(url)
                links.append(PdfLink(page_number=page_number, url=url))
        return links

    # ---------------------------------------------------------------- images

    def _extract_images(self, fz: fitz.Document, fz_page: fitz.Page, page_number: int) -> list[PdfImage]:
        assert self._image_dir is not None
        self._image_dir.mkdir(parents=True, exist_ok=True)
        images: list[PdfImage] = []
        for idx, info in enumerate(fz_page.get_images(full=True)):
            xref = info[0]
            try:
                pix = fitz.Pixmap(fz, xref)
                if pix.width < _MIN_IMAGE_DIM or pix.height < _MIN_IMAGE_DIM:
                    continue
                if pix.n - pix.alpha >= 4:  # CMYK -> RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                out_path = self._image_dir / f"pdf-p{page_number:03d}-img{idx:02d}.png"
                pix.save(out_path)
            except Exception as exc:  # corrupt embedded object: log & continue
                log.warning("Could not extract image xref=%s on page %d: %s", xref, page_number, exc)
                continue
            images.append(
                PdfImage(
                    page_number=page_number,
                    index=idx,
                    path=str(out_path),
                    width=pix.width,
                    height=pix.height,
                    caption=self._caption_near_image(fz_page, info),
                )
            )
        return images

    @staticmethod
    def _caption_near_image(fz_page: fitz.Page, image_info: tuple[int | str, ...]) -> str:
        """Grab text just below the image bbox as a caption candidate."""
        try:
            rects = fz_page.get_image_rects(image_info[0])
            if not rects:
                return ""
            rect = rects[0]
            caption_zone = fitz.Rect(rect.x0, rect.y1, rect.x1, min(rect.y1 + 40, fz_page.rect.y1))
            return normalize_text(fz_page.get_textbox(caption_zone), casefold=False)[:200]
        except Exception:
            return ""
