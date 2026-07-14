"""Requirement mining: turn a parsed PdfDocument into a normalized RequirementSet.

Extraction passes (each tags its results with ``extraction_method``):

  1. table            — requirement tables (ID / Requirement / Expected columns)
  2. explicit_id      — free-text lines carrying a requirement ID (REQ-012 ...)
  3. modal_sentence   — sentences with shall/must/should/will constructs
  4. link_annotation  — hyperlinks in the spec => link-presence requirements
  5. image_caption    — captioned spec screenshots => visual-comparison requirements

Every requirement keeps a ``RequirementSource`` (document, page, verbatim text)
so auditors can trace any matrix row back into the specification.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from webval.config import Settings
from webval.models import (
    PdfDocument,
    PdfTable,
    Requirement,
    RequirementCategory,
    RequirementSet,
    RequirementSource,
)
from webval.requirement_engine import annotations as proof_annotations
from webval.requirement_engine import rules
from webval.utils import get_logger, normalize_text, utc_now_iso

log = get_logger("requirements.engine")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9•-])")
_QUOTED_RE = re.compile(r"[\"“']([^\"”']{3,80})[\"”']")
_STOPWORDS = frozenset(
    [
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "be",
        "shall", "must", "should", "will", "page", "site", "website", "user", "this", "that",
        "from", "by", "as", "at", "it", "its", "their", "have", "has", "can", "when", "where",
        "each", "all", "any",
    ]
)

# Default expected-result phrasing per category when the spec doesn't state one.
_DEFAULT_EXPECTED: dict[RequirementCategory, str] = {
    RequirementCategory.NAVIGATION: "Navigation element is present and functional",
    RequirementCategory.CONTENT: "Content is present and matches the specification verbatim",
    RequirementCategory.LINK: "Link is present, visible, and resolves without error",
    RequirementCategory.ANCHOR: "Anchor exists, is clickable, and scrolls to a visible target",
    RequirementCategory.METADATA: "Metadata value matches the specification",
    RequirementCategory.ACCESSIBILITY: "Element exposes correct alt text / ARIA attributes",
    RequirementCategory.IMAGE: "Image is present and renders successfully",
    RequirementCategory.DOWNLOAD: "Asset downloads successfully with size > 0 bytes",
    RequirementCategory.VIDEO: "Video renders and playback starts without errors",
    RequirementCategory.RESPONSIVE: "Layout renders correctly on all configured devices",
    RequirementCategory.UI_BEHAVIOR: "Interactive element opens/closes and behaves as specified",
    RequirementCategory.PERFORMANCE: "Page meets the configured performance budgets",
    RequirementCategory.VISUAL: "Rendered page visually matches the specification image",
    RequirementCategory.GENERAL: "Behaviour matches the specification",
}


class RequirementEngine:
    """Extracts and normalizes requirements from a parsed specification."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._id_patterns = [re.compile(p, re.IGNORECASE) for p in settings.pdf.id_patterns]

    def extract(self, doc: PdfDocument) -> RequirementSet:
        return self.extract_many([doc])

    def extract_many(self, docs: list[PdfDocument]) -> RequirementSet:
        """Merge requirements from one or more specification documents.

        IDs are allocated from a shared counter so multi-file runs produce a
        single collision-free traceability matrix; each requirement's source
        records which document it came from.
        """
        req_set = RequirementSet(
            source_document="; ".join(d.file_name for d in docs),
            extracted_at=utc_now_iso(),
        )
        seen_statements: set[str] = set()
        auto_counter = _AutoId(existing=set())
        collected: list[Requirement] = []

        for doc in docs:
            from_tables = self._from_tables(doc, auto_counter)
            for r in from_tables:
                seen_statements.add(normalize_text(r.requirement))
            auto_counter.existing.update(r.id for r in from_tables)
            collected += from_tables

        for doc in docs:
            # Explicit-ID and modal passes only trust real text layers; OCR text
            # is too noisy for sentence mining (mangled words become
            # false-fail content requirements).
            for extractor in (self._from_explicit_ids, self._from_modal_sentences):
                for req in extractor(doc, auto_counter):
                    key = normalize_text(req.requirement)
                    if key in seen_statements:
                        continue
                    seen_statements.add(key)
                    collected.append(req)
                    auto_counter.existing.add(req.id)

            collected += self._from_annotations(doc, auto_counter, seen_statements)
            collected += self._from_links(doc, auto_counter, seen_statements)
            collected += self._from_images(doc, auto_counter, seen_statements)

        req_set.requirements = collected
        log.info(
            "Extracted %d requirements from %d document(s) (%s)",
            len(collected),
            len(docs),
            ", ".join(
                f"{cat.value}: {len(req_set.by_category(cat))}"
                for cat in RequirementCategory
                if req_set.by_category(cat)
            ),
        )
        return req_set

    # ------------------------------------------------------------- pass 1: tables

    def _from_tables(self, doc: PdfDocument, auto: _AutoId) -> list[Requirement]:
        out: list[Requirement] = []
        for page in doc.pages:
            for table in page.tables:
                out.extend(self._parse_requirement_table(doc, table, auto))
        return out

    def _parse_requirement_table(
        self, doc: PdfDocument, table: PdfTable, auto: _AutoId
    ) -> list[Requirement]:
        col_map: dict[str, int] = {}
        for idx, header in enumerate(table.headers):
            canonical = rules.match_header(header)
            if canonical and canonical not in col_map:
                col_map[canonical] = idx
        if "requirement" not in col_map:
            return []  # not a requirement table

        out: list[Requirement] = []
        for row in table.rows:
            def cell(name: str, row: list[str] = row) -> str:
                idx = col_map.get(name)
                return row[idx].strip() if idx is not None and idx < len(row) else ""

            statement = cell("requirement")
            if len(statement) < self._settings.pdf.min_requirement_length:
                continue
            raw_id = cell("id")
            category = self._category_from_cell(cell("category")) or rules.classify(statement)
            req_id = auto.normalize(raw_id) if raw_id else auto.next()
            out.append(
                Requirement(
                    id=req_id,
                    category=category,
                    requirement=statement,
                    expected=cell("expected") or _DEFAULT_EXPECTED[category],
                    priority=cell("priority") or "Must",
                    target_url_hint=cell("url") or None,
                    target_text=_extract_quoted(statement),
                    keywords=_keywords(statement),
                    source=RequirementSource(
                        document=doc.file_name,
                        page_number=table.page_number,
                        extraction_method="table",
                        raw_text=" | ".join(row),
                    ),
                )
            )
        return out

    @staticmethod
    def _category_from_cell(value: str) -> RequirementCategory | None:
        if not value:
            return None
        norm = normalize_text(value)
        for cat in RequirementCategory:
            if normalize_text(cat.value) == norm or normalize_text(cat.name) == norm:
                return cat
        return None

    # -------------------------------------------------------- pass 2: explicit IDs

    def _from_explicit_ids(self, doc: PdfDocument, auto: _AutoId) -> list[Requirement]:
        out: list[Requirement] = []
        for page in doc.pages:
            if page.ocr:
                continue  # OCR text is too noisy for line mining
            for line in page.text.splitlines():
                line = line.strip()
                if len(line) < self._settings.pdf.min_requirement_length:
                    continue
                for pattern in self._id_patterns:
                    match = pattern.search(line)
                    if not match:
                        continue
                    canonical = auto.canonical(match.group(0))
                    if canonical and canonical in auto.existing:
                        # Already captured by the table pass; pdfplumber's text
                        # stream includes table cells, so skip the re-read row.
                        break
                    statement = line[match.end():].lstrip(" :.-—\t") or line
                    if len(statement) < self._settings.pdf.min_requirement_length:
                        continue
                    category = rules.classify(statement)
                    out.append(
                        self._build(
                            doc, page.page_number, "explicit_id", line, statement, category,
                            req_id=auto.normalize(match.group(0)),
                        )
                    )
                    break
        return out

    # ---------------------------------------------------- pass 3: modal sentences

    def _from_modal_sentences(self, doc: PdfDocument, auto: _AutoId) -> list[Requirement]:
        out: list[Requirement] = []
        for page in doc.pages:
            if page.ocr:
                continue  # OCR noise would create false-fail content requirements
            flat = " ".join(page.text.split())
            for sentence in _SENTENCE_SPLIT_RE.split(flat):
                sentence = sentence.strip()
                if len(sentence) < self._settings.pdf.min_requirement_length or len(sentence) > 600:
                    continue
                if not rules.MODAL_RE.search(sentence):
                    continue
                if any(p.search(sentence) for p in self._id_patterns):
                    continue  # already captured by the explicit-ID pass
                category = rules.classify(sentence)
                out.append(
                    self._build(doc, page.page_number, "modal_sentence", sentence, sentence, category)
                )
        # assign IDs after collection for stable ordering by page
        for req in out:
            req.id = auto.next()
        return out

    # ---------------------------------------------- pass 3b: proof annotations

    def _from_annotations(
        self, doc: PdfDocument, auto: _AutoId, seen: set[str]
    ) -> list[Requirement]:
        """Boxed verification callouts on proof screenshots (usually via OCR)."""
        out: list[Requirement] = []

        def add(req: Requirement) -> None:
            key = normalize_text(req.requirement)
            if key in seen:
                return
            seen.add(key)
            out.append(req)

        for page in doc.pages:
            source = RequirementSource(
                document=doc.file_name,
                page_number=page.page_number,
                extraction_method="annotation",
                raw_text="",
            )
            for link in proof_annotations.extract_link_annotations(page.text):
                scope = "on every page (GLOBAL)" if link.is_global else "on the annotated page"
                add(
                    Requirement(
                        id=auto.next(),
                        category=RequirementCategory.LINK,
                        requirement=f"Link to {link.url} is present {scope}",
                        expected="Link is present on the site and resolves with a successful HTTP status",
                        target_url_hint=link.url,
                        keywords=_keywords(link.url),
                        source=source.model_copy(update={"raw_text": f"Links to: {link.url}"}),
                    )
                )
            for anchor in proof_annotations.extract_anchor_annotations(page.text):
                add(
                    Requirement(
                        id=auto.next(),
                        category=RequirementCategory.ANCHOR,
                        requirement=f"“{anchor.label}” anchor links to {anchor.target}",
                        expected="Anchor exists, is clickable, and scrolls to a visible target",
                        target_text=anchor.label,
                        keywords=_keywords(f"{anchor.label} {anchor.target}"),
                        source=source.model_copy(
                            update={"raw_text": f'Clicking "{anchor.label}" anchor links to {anchor.target}'}
                        ),
                    )
                )
            for alt in proof_annotations.extract_alt_text_annotations(page.text):
                add(
                    Requirement(
                        id=auto.next(),
                        category=RequirementCategory.ACCESSIBILITY,
                        requirement=f"Image with alt text “{alt.alt}” is present",
                        expected="An element exposes this exact alt text / accessible name",
                        target_text=alt.alt,
                        keywords=_keywords(alt.alt),
                        source=source.model_copy(update={"raw_text": f"alt text: {alt.alt}"}),
                    )
                )
        return out

    # ------------------------------------------------------------ pass 4: links

    def _from_links(
        self, doc: PdfDocument, auto: _AutoId, seen: set[str]
    ) -> list[Requirement]:
        out: list[Requirement] = []
        site_host = urlparse(self._settings.site.base_url).netloc.removeprefix("www.")
        for page in doc.pages:
            for link in page.links:
                host = urlparse(link.url).netloc.removeprefix("www.")
                if not host:
                    continue
                label = link.anchor_text or link.url
                statement = f"Link to {link.url}" + (f" (“{link.anchor_text}”)" if link.anchor_text else "")
                key = normalize_text(statement)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    Requirement(
                        id=auto.next(),
                        category=RequirementCategory.LINK,
                        requirement=statement,
                        expected="Link is present on the site and resolves with a successful HTTP status",
                        target_url_hint=link.url,
                        target_text=link.anchor_text or None,
                        keywords=_keywords(label),
                        source=RequirementSource(
                            document=doc.file_name,
                            page_number=link.page_number,
                            extraction_method="link_annotation",
                            raw_text=link.url,
                        ),
                    )
                )
                _ = site_host  # host comparison handled by the link validator
        return out

    # ----------------------------------------------------------- pass 5: images

    def _from_images(
        self, doc: PdfDocument, auto: _AutoId, seen: set[str]
    ) -> list[Requirement]:
        if not self._settings.validation.visual.enabled:
            return []
        out: list[Requirement] = []
        for page in doc.pages:
            for image in page.images:
                statement = (
                    f"Rendered page matches specification image on PDF page {image.page_number}"
                    + (f" ({image.caption})" if image.caption else "")
                )
                key = normalize_text(statement)
                if key in seen:
                    continue
                seen.add(key)
                keywords = _keywords(image.caption) if image.caption else []
                # Photos of annotated proofs (OCR pages) can't be pixel-compared
                # against clean browser screenshots; mark them advisory so the
                # visual validator caps the outcome at Warning for human review.
                if page.ocr:
                    keywords = [*keywords, "photo-proof"]
                out.append(
                    Requirement(
                        id=auto.next(),
                        category=RequirementCategory.VISUAL,
                        requirement=statement,
                        expected="Live page screenshot is visually consistent with the specification image",
                        target_url_hint=None,
                        keywords=keywords,
                        source=RequirementSource(
                            document=doc.file_name,
                            page_number=image.page_number,
                            extraction_method="image_caption",
                            raw_text=image.path,
                        ),
                    )
                )
        return out

    # ------------------------------------------------------------------ helpers

    def _build(
        self,
        doc: PdfDocument,
        page_number: int,
        method: str,
        raw: str,
        statement: str,
        category: RequirementCategory,
        req_id: str | None = None,
    ) -> Requirement:
        return Requirement(
            id=req_id or "REQ-000",  # placeholder, replaced by _AutoId before use
            category=category,
            requirement=statement,
            expected=_DEFAULT_EXPECTED[category],
            target_text=_extract_quoted(statement),
            keywords=_keywords(statement),
            source=RequirementSource(
                document=doc.file_name,
                page_number=page_number,
                extraction_method=method,
                raw_text=raw,
            ),
        )


class _AutoId:
    """Stable REQ-NNN allocator that respects IDs already used by the spec."""

    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self._counter = 0

    def next(self) -> str:
        while True:
            self._counter += 1
            candidate = f"REQ-{self._counter:03d}"
            if candidate not in self.existing:
                self.existing.add(candidate)
                return candidate

    @staticmethod
    def canonical(raw_id: str) -> str | None:
        """Canonical form of a spec-authored ID ('req 12' -> 'REQ-012'), no allocation."""
        match = re.match(r"([A-Za-z]{2,4})[-_ ]?(\d{1,4})", raw_id.strip())
        if not match:
            return None
        return f"{match.group(1).upper()}-{int(match.group(2)):03d}"

    def normalize(self, raw_id: str) -> str:
        """Canonicalize and claim a spec-authored ID, falling back to auto-allocation."""
        candidate = self.canonical(raw_id)
        if candidate is None or candidate in self.existing:
            return self.next()
        self.existing.add(candidate)
        return candidate


def _extract_quoted(text: str) -> str | None:
    match = _QUOTED_RE.search(text)
    return match.group(1).strip() if match else None


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", text)
    return [w for w in dict.fromkeys(w.lower() for w in words) if w not in _STOPWORDS][:12]
