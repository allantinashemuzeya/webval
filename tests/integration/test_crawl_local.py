"""Integration test: authenticated crawl of a local HTTP Basic Auth site.

Requires Playwright browsers (`playwright install chromium`).
Run with: pytest -m integration
"""

from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from webval.config import Settings
from webval.crawler import BrowserSession, SiteDiscovery
from webval.evidence import EvidenceStore

USERNAME, PASSWORD = "qa-user", "qa-pass"

PAGES = {
    "/": """<html><head><title>Home</title><meta name="description" content="Test home"></head>
            <body><h1>Welcome</h1>
            <nav><a href="/about">About</a></nav>
            <a href="#section">Jump</a><div id="section">Section</div>
            <footer><a href="/privacy">Privacy</a></footer></body></html>""",
    "/about": """<html><head><title>About</title></head>
                 <body><h1>About Us</h1><a href="/">Home</a></body></html>""",
    "/privacy": """<html><head><title>Privacy</title></head><body><h1>Privacy</h1></body></html>""",
}


class _BasicAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="test"')
            self.end_headers()
            return
        body = PAGES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:  # silence test output
        pass


@pytest.fixture()
def auth_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BasicAuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.mark.integration
class TestAuthenticatedCrawl:
    async def test_discovers_all_pages_with_basic_auth(self, auth_server: str, tmp_path: Path):
        settings = Settings(
            site={"base_url": auth_server, "max_pages": 10, "max_depth": 3},
            auth={"mode": "http_basic", "username": USERNAME, "password": PASSWORD},
        )
        store = EvidenceStore(tmp_path / "run")
        async with BrowserSession(settings) as session:
            site_map = await SiteDiscovery(settings, session, store).discover()

        urls = {p.url.rstrip("/") for p in site_map.pages}
        base = auth_server.rstrip("/")
        assert urls == {base, f"{base}/about", f"{base}/privacy"}

        home = next(p for p in site_map.pages if p.url.rstrip("/") == base)
        assert home.status == 200
        assert home.title == "Home"
        assert home.h1 == ["Welcome"]
        assert home.meta["description"] == "Test home"
        assert "section" in home.anchor_ids
        assert any(link.location == "footer" and link.text == "Privacy" for link in home.links)
        # evidence captured
        assert home.screenshot_path and (tmp_path / "run" / home.screenshot_path).is_file()
        assert home.html_path and (tmp_path / "run" / home.html_path).is_file()
        assert store.verify_ledger() == []

    async def test_wrong_credentials_surface_auth_failure(self, auth_server: str, tmp_path: Path):
        settings = Settings(
            site={"base_url": auth_server, "max_pages": 2},
            auth={"mode": "http_basic", "username": "wrong", "password": "wrong"},
        )
        store = EvidenceStore(tmp_path / "run")
        async with BrowserSession(settings) as session:
            site_map = await SiteDiscovery(settings, session, store).discover()
        page = site_map.pages[0]
        # Bundled Chromium renders the 401; Chrome/Edge channels abort navigation
        # with ERR_HTTP_RESPONSE_CODE_FAILURE. Both must surface, never crash.
        assert page.status == 401 or "ERR_HTTP_RESPONSE_CODE_FAILURE" in page.title
