"""Generate sample report outputs by running the FULL pipeline end-to-end.

Creates a specification PDF (PyMuPDF), serves a demo website behind HTTP
Basic Auth on localhost, then executes ValidationPipeline against it.
Two spec requirements are intentionally not satisfied by the site so the
sample reports demonstrate Fail/Warning rows and the defect summary.

Usage:  python scripts/generate_sample_run.py [output_root]
"""

from __future__ import annotations

import asyncio
import base64
import io
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from webval.config import Settings
from webval.pipeline import ValidationPipeline

USERNAME, PASSWORD = "demo-user", "demo-pass"


# --------------------------------------------------------------------- assets

def _png_bytes(color: str, size: tuple[int, int] = (600, 300), label: str = "") -> bytes:
    img = Image.new("RGB", size, color)
    if label:
        ImageDraw.Draw(img).text((20, 20), label, fill="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


HERO_PNG = _png_bytes("#1f4e79", label="HERO — Now Approved")
BROCHURE_PDF = fitz.open()
_page = BROCHURE_PDF.new_page()
_page.insert_text((72, 100), "Patient Brochure — demo downloadable asset")
BROCHURE_BYTES = BROCHURE_PDF.tobytes()
BROCHURE_PDF.close()

HOME_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<title>DemoRx | Now Approved for mHSPC</title>
<meta name="description" content="Official DemoRx site for healthcare providers.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:title" content="DemoRx">
<link rel="canonical" href="/">
<style>
  body { font-family: sans-serif; margin: 0; }
  img { max-width: 100%; height: auto; }
  nav { display: flex; gap: 16px; padding: 12px; background: #1f4e79; }
  nav a { color: #fff; }
  #menu-btn { display: none; }
  @media (max-width: 820px) {
    nav .links { display: none; }
    nav .links.open { display: block; }
    #menu-btn { display: block; }
  }
  section { min-height: 60vh; padding: 24px; }
</style>
</head><body>
<nav>
  <button id="menu-btn" aria-label="Open menu" aria-expanded="false"
          onclick="this.setAttribute('aria-expanded', this.getAttribute('aria-expanded') !== 'true');
                   document.querySelector('.links').classList.toggle('open')">☰</button>
  <div class="links">
    <a href="#about-mhspc">About mHSPC</a>
    <a href="#about-psma">About PSMA</a>
    <a href="/resources">Resources</a>
  </div>
</nav>
<h1>Now Approved for mHSPC</h1>
<img src="/hero.png" alt="DemoRx hero banner">
<section id="about-mhspc"><h2>About mHSPC</h2>
  <p>Metastatic hormone-sensitive prostate cancer affects many patients.</p></section>
<section id="about-psma"><h2>About PSMA</h2>
  <p>PSMA is a protein expressed on prostate cancer cells.</p></section>
<footer>
  <p>Please see full Important Safety Information.</p>
  <a href="/privacy">Privacy Policy</a>
</footer>
</body></html>"""

RESOURCES_HTML = """<!DOCTYPE html>
<html lang="en"><head><title>DemoRx | Resources</title>
<meta name="description" content="Downloadable resources."></head>
<body><h1>Resources</h1>
<a href="/brochure.pdf">Download the Patient Brochure</a>
<a href="/">Home</a>
</body></html>"""

PRIVACY_HTML = """<!DOCTYPE html>
<html lang="en"><head><title>DemoRx | Privacy</title></head>
<body><h1>Privacy Policy</h1><p>Demo privacy content.</p><a href="/">Home</a></body></html>"""

ROUTES: dict[str, tuple[bytes, str]] = {
    "/": (HOME_HTML.encode(), "text/html"),
    "/resources": (RESOURCES_HTML.encode(), "text/html"),
    "/privacy": (PRIVACY_HTML.encode(), "text/html"),
    "/hero.png": (HERO_PNG, "image/png"),
    "/brochure.pdf": (BROCHURE_BYTES, "application/pdf"),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="demo"')
            self.end_headers()
            return
        entry = ROUTES.get(self.path.split("?")[0])
        if entry is None:
            self.send_response(404)
            self.end_headers()
            return
        body, ctype = entry
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if self.path.endswith(".pdf"):
            self.send_header("Content-Disposition", 'attachment; filename="brochure.pdf"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


# ----------------------------------------------------------------------- spec

def build_spec_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "DemoRx Website Specification v1.0", fontsize=16)
    page.insert_text(
        (72, 120),
        "Requirement table:\n", fontsize=11,
    )
    # A simple drawn table (pdfplumber-friendly: text rows with pipes are NOT
    # parsed as tables, so draw real table lines).
    rows = [
        ["Req ID", "Requirement Description", "Expected Result", "Category"],
        ["REQ-1", 'Homepage displays the headline "Now Approved for mHSPC"', "Headline present verbatim", "Content"],
        ["REQ-2", 'The "About mHSPC" anchor exists and scrolls to its section', "Anchor displayed and functioning", "Anchor"],
        ["REQ-3", 'The "About PSMA" anchor exists and scrolls to its section', "Anchor displayed and functioning", "Anchor"],
        ["REQ-4", "The footer must contain a Privacy Policy link", "Link present and resolves", "Link"],
        ["REQ-5", 'The patient brochure must be downloadable from the Resources page', "File downloads, size > 0", "Download"],
        ["REQ-6", "The hero image must render successfully on the homepage", "Image loads without errors", "Image"],
        ["REQ-7", "All images must include descriptive alt text", "No image lacks alt text", "Accessibility"],
        ["REQ-8", "The page title shall contain \"DemoRx\"", "Title matches", "Metadata"],
        ["REQ-9", "Layout must be responsive with hamburger navigation on mobile", "Responsive on all devices", "Responsive"],
        ["REQ-10", 'Homepage displays the disclaimer "This statement does not exist on the site"', "Disclaimer present verbatim", "Content"],
        ["REQ-11", "The footer must contain a link to Terms of Use", "Link present and resolves", "Link"],
    ]
    y = 150
    col_x = [72, 122, 330, 480, 540]
    for row in rows:
        for i, cell in enumerate(row):
            # wrap long cells crudely
            rect = fitz.Rect(col_x[i], y, col_x[i + 1] - 4, y + 34)
            page.insert_textbox(rect, cell, fontsize=6.5)
        for x in col_x:
            page.draw_line(fitz.Point(x, y - 2), fitz.Point(x, y + 34))
        page.draw_line(fitz.Point(col_x[0], y - 2), fitz.Point(col_x[-1], y - 2))
        y += 36
    page.draw_line(fitz.Point(col_x[0], y - 2), fitz.Point(col_x[-1], y - 2))
    page.insert_text(
        (72, y + 30),
        "Additional notes:\n"
        "The site shall display the text Please see full Important Safety Information "
        "in the footer of every page.",
        fontsize=9,
    )
    doc.set_metadata({"title": "DemoRx Website Specification", "author": "QA Validation"})
    doc.save(path)
    doc.close()


# ------------------------------------------------------------------------ run

async def main(output_root: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Demo site with HTTP Basic Auth: {base_url}")

    spec_path = output_root / "demo_spec.pdf"
    output_root.mkdir(parents=True, exist_ok=True)
    build_spec_pdf(spec_path)
    print(f"Demo specification PDF: {spec_path}")

    settings = Settings(
        site={"base_url": base_url, "max_pages": 10, "max_depth": 3},
        auth={"mode": "http_basic", "username": USERNAME, "password": PASSWORD},
        report={"system_under_test": "DemoRx sample site (localhost)"},
        validation={"visual": {"enabled": False}},  # demo spec has no screenshots
    )
    pipeline = ValidationPipeline(settings, output_root=output_root)
    run = await pipeline.execute(spec_path)

    server.shutdown()
    s = run.summary
    print(
        f"\nSample run complete: total={s.total} pass={s.passed} fail={s.failed} "
        f"warn={s.warnings} error={s.errors} not-tested={s.not_tested}"
    )
    print(f"Outputs in: {pipeline.run_dir}")


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/generated")
    asyncio.run(main(root))
