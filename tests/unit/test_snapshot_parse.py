"""Unit tests for browser-independent DOM parsing (crawler.snapshot.parse_html)."""

from webval.crawler.snapshot import parse_html

BASE = "https://example.test/"

HTML = """
<html>
<head>
  <title>  PLUVICTO |  Home </title>
  <meta name="description" content="Official site">
  <meta name="robots" content="noindex">
  <meta property="og:title" content="PLUVICTO">
  <link rel="canonical" href="/home">
  <script type="application/ld+json">{"@type": "Drug", "name": "PLUVICTO"}</script>
  <script type="application/ld+json">not json</script>
</head>
<body>
  <header><nav><a href="/dosing">Dosing</a></nav></header>
  <h1>Now Approved</h1><h2>About mHSPC</h2><h2>About mCRPC</h2>
  <a href="#about-mhspc">About mHSPC</a>
  <a class="btn btn-primary" href="/enroll">Enroll Now</a>
  <a href="https://external.example.com/study">Study</a>
  <a href="mailto:info@example.test">Mail</a>
  <a href="/brochure.pdf">Patient Brochure</a>
  <img src="/hero.png" alt="Hero">
  <video src="/moa.mp4"></video>
  <iframe src="https://player.vimeo.com/video/1"></iframe>
  <div id="about-mhspc">section</div>
  <footer><a href="/privacy">Privacy Policy</a></footer>
</body>
</html>
"""


class TestParseHtml:
    def setup_method(self):
        self.dom = parse_html(HTML, BASE)

    def test_title_normalized(self):
        assert self.dom.title == "PLUVICTO | Home"

    def test_meta_and_canonical(self):
        assert self.dom.meta["description"] == "Official site"
        assert self.dom.meta["robots"] == "noindex"
        assert self.dom.meta["og:title"] == "PLUVICTO"
        assert self.dom.meta["canonical"] == "https://example.test/home"

    def test_headings(self):
        assert self.dom.headings["h1"] == ["Now Approved"]
        assert self.dom.headings["h2"] == ["About mHSPC", "About mCRPC"]

    def test_link_locations(self):
        by_text = {link.text: link for link in self.dom.links}
        assert by_text["Dosing"].location == "nav"
        assert by_text["Privacy Policy"].location == "footer"
        assert by_text["Enroll Now"].location == "cta"
        assert by_text["Study"].location == "body"

    def test_internal_external_and_anchor_flags(self):
        by_text = {link.text: link for link in self.dom.links}
        assert by_text["Dosing"].is_internal
        assert not by_text["Study"].is_internal
        assert by_text["About mHSPC"].is_anchor

    def test_mailto_excluded(self):
        assert all("mailto:" not in link.href for link in self.dom.links)

    def test_assets(self):
        kinds = {(a.kind, a.url) for a in self.dom.assets}
        assert ("image", "https://example.test/hero.png") in kinds
        assert ("video", "https://example.test/moa.mp4") in kinds
        assert ("video", "https://player.vimeo.com/video/1") in kinds
        assert ("download", "https://example.test/brochure.pdf") in kinds

    def test_image_alt_captured(self):
        img = next(a for a in self.dom.assets if a.kind == "image")
        assert img.alt == "Hero"

    def test_anchor_ids(self):
        assert "about-mhspc" in self.dom.anchor_ids

    def test_structured_data_skips_malformed(self):
        assert len(self.dom.structured_data) == 1
        assert self.dom.structured_data[0]["@type"] == "Drug"
