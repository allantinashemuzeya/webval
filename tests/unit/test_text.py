"""Unit tests for text normalization utilities."""

from webval.utils.text import (
    best_window_ratio,
    contains_normalized,
    fuzzy_ratio,
    normalize_text,
    slugify,
)


class TestNormalizeText:
    def test_collapses_whitespace_and_linebreaks(self):
        assert normalize_text("Hello\n  world\t !") == "hello world !"

    def test_unifies_curly_quotes_and_dashes(self):
        assert normalize_text("“Now—Approved”") == normalize_text('"Now-Approved"')

    def test_strips_trademark_symbols(self):
        assert normalize_text("PLUVICTO® works") == "pluvicto works"

    def test_removes_soft_hyphen_and_nbsp(self):
        assert normalize_text("cus­tomer care") == "customer care"

    def test_casefold_optional(self):
        assert normalize_text("Hello", casefold=False) == "Hello"

    def test_empty(self):
        assert normalize_text("") == ""


class TestMatching:
    def test_contains_normalized_across_variants(self):
        page = "Talk to your doctor about PLUVICTO® — “Now Approved”."
        assert contains_normalized(page, 'about PLUVICTO - "Now Approved"')

    def test_fuzzy_ratio_bounds(self):
        assert fuzzy_ratio("abc", "abc") == 1.0
        assert fuzzy_ratio("", "abc") == 0.0

    def test_best_window_exact_substring(self):
        hay = "one two three four five six seven"
        assert best_window_ratio(hay, "three four five") == 1.0

    def test_best_window_fuzzy(self):
        hay = "the patient brochure can be downloaded from the resources page today"
        ratio = best_window_ratio(hay, "patient brochure can be downloadd from the resources")
        assert 0.85 < ratio < 1.0

    def test_best_window_no_match(self):
        assert best_window_ratio("alpha beta gamma", "zzz qqq") < 0.5


class TestSlugify:
    def test_basic(self):
        assert slugify("About mHSPC — Anchor!") == "about-mhspc-anchor"

    def test_truncates(self):
        assert len(slugify("x " * 200)) <= 60

    def test_never_empty(self):
        assert slugify("!!!") == "item"
