"""Proof-callout parsers (requirement_engine/annotations.py)."""

from webval.requirement_engine.annotations import (
    extract_code_annotations,
    extract_metadata_annotations,
)


class TestMetadataAnnotations:
    def test_page_title(self):
        text = "Page title: PLUVICTO® (lutetium Lu 177 vipivotide tetraxetan)\nbody copy follows"
        out = extract_metadata_annotations(text)
        assert len(out) == 1
        assert out[0].field == "title"
        assert out[0].value.startswith("PLUVICTO")

    def test_meta_description_wraps_to_next_line(self):
        text = "Meta description: Learn about treatment\nand support options\n\nUnrelated copy"
        out = extract_metadata_annotations(text)
        assert out[0].field == "description"
        assert "support options" in out[0].value

    def test_bare_title_word_ignored(self):
        assert extract_metadata_annotations("Title: something\ndescription: other") == []

    def test_deduplicated(self):
        text = "Page title: Home\nPage title: Home"
        assert len(extract_metadata_annotations(text)) == 1


class TestCodeAnnotations:
    def test_hyphenated_code_with_date(self):
        out = extract_code_annotations("footer shows US-PLU-2300123 06/26 in small print")
        assert [c.text for c in out] == ["US-PLU-2300123 06/26"]

    def test_numeric_code_date_first(self):
        out = extract_code_annotations("approved 6/23 273175")
        assert [c.text for c in out] == ["6/23 273175"]

    def test_plain_copy_not_matched(self):
        assert extract_code_annotations("take 100 mg daily, see page 4") == []
        assert extract_code_annotations("visit us at example.com") == []

    def test_deduplicated(self):
        out = extract_code_annotations("US-PLU-2300123 06/26 ... US-PLU-2300123 06/26")
        assert len(out) == 1
