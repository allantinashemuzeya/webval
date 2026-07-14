"""Unit tests for the requirement extraction engine."""

from webval.models import RequirementCategory
from webval.requirement_engine import RequirementEngine
from webval.requirement_engine.rules import classify, match_header


class TestClassify:
    def test_anchor(self):
        assert classify("About mHSPC anchor exists") is RequirementCategory.ANCHOR

    def test_download(self):
        assert classify("The patient brochure must be downloadable") is RequirementCategory.DOWNLOAD

    def test_accessibility(self):
        assert classify("All images must have alt-text") is RequirementCategory.ACCESSIBILITY

    def test_metadata(self):
        assert classify("The page title shall read PLUVICTO") is RequirementCategory.METADATA

    def test_video(self):
        assert classify("The MOA video must start playback on click") is RequirementCategory.VIDEO

    def test_navigation(self):
        assert classify("The navigation menu should include Dosing") is RequirementCategory.NAVIGATION

    def test_content_fallback(self):
        assert classify("The disclaimer text is shown") is RequirementCategory.CONTENT

    def test_general_fallback(self):
        assert classify("It just works somehow") is RequirementCategory.GENERAL


class TestMatchHeader:
    def test_synonyms(self):
        assert match_header("Req. ID") == "id"
        assert match_header("Expected Behaviour") == "expected"
        assert match_header("REQUIREMENT DESCRIPTION") == "requirement"

    def test_unknown(self):
        assert match_header("Random Column") is None


class TestEngine:
    def test_full_extraction(self, settings, sample_pdf_document):
        engine = RequirementEngine(settings)
        req_set = engine.extract(sample_pdf_document)
        ids = [r.id for r in req_set]

        # table rows with explicit ids are normalized to REQ-00N
        assert "REQ-001" in ids and "REQ-002" in ids
        # explicit-id line REQ-10 -> REQ-010
        assert "REQ-010" in ids
        # every requirement has a source page and method
        assert all(r.source.page_number >= 1 for r in req_set)
        assert all(r.source.extraction_method for r in req_set)

    def test_table_categories_and_expected(self, settings, sample_pdf_document):
        req_set = RequirementEngine(settings).extract(sample_pdf_document)
        anchor = req_set.get("REQ-001")
        assert anchor is not None
        assert anchor.category is RequirementCategory.ANCHOR
        assert anchor.expected == "Anchor displayed and functioning"

    def test_quoted_target_text_extracted(self, settings, sample_pdf_document):
        req_set = RequirementEngine(settings).extract(sample_pdf_document)
        content = req_set.get("REQ-002")
        assert content is not None
        assert content.target_text == "Now Approved for mHSPC"

    def test_modal_sentences_extracted(self, settings, sample_pdf_document):
        req_set = RequirementEngine(settings).extract(sample_pdf_document)
        statements = [r.requirement for r in req_set]
        assert any("Important Safety Information" in s for s in statements)
        assert any("patient brochure" in s for s in statements)

    def test_link_requirements_from_annotations(self, settings, sample_pdf_document):
        req_set = RequirementEngine(settings).extract(sample_pdf_document)
        link_reqs = req_set.by_category(RequirementCategory.LINK)
        assert any("pluvicto.com" in (r.target_url_hint or "") for r in link_reqs)

    def test_no_duplicate_statements(self, settings, sample_pdf_document):
        req_set = RequirementEngine(settings).extract(sample_pdf_document)
        normalized = [" ".join(r.requirement.lower().split()) for r in req_set]
        assert len(normalized) == len(set(normalized))

    def test_unique_ids(self, settings, sample_pdf_document):
        req_set = RequirementEngine(settings).extract(sample_pdf_document)
        ids = [r.id for r in req_set]
        assert len(ids) == len(set(ids))

    def test_table_rows_not_reextracted_from_text_stream(self, settings, sample_pdf_document):
        """pdfplumber page text repeats table cells; those lines must be skipped."""
        req_set = RequirementEngine(settings).extract(sample_pdf_document)
        mhspc_reqs = [r for r in req_set if "mHSPC anchor" in r.requirement]
        assert len(mhspc_reqs) == 1
        assert mhspc_reqs[0].source.extraction_method == "table"
