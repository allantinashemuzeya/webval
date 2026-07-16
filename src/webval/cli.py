"""Command-line interface.

    webval doctor                        # preflight: creds, network, browser, OCR
    webval run spec.pdf                  # full pipeline (phases 1-16)
    webval extract spec.pdf              # phase 1 only: requirements to JSON
    webval crawl                         # phases 2-3 only: discovery smoke test
    webval report runs/<id>/results.json # regenerate reports from stored results
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from webval.config import load_settings
from webval.models import Status, ValidationRun
from webval.utils import setup_logging

app = typer.Typer(name="webval", help="Requirement-traceability website validation.", no_args_is_help=True)
console = Console()

_CONFIG_OPT = typer.Option(None, "--config", "-c", help="Project YAML overriding config/default.yaml")


def _summary_table(run: ValidationRun) -> Table:
    table = Table(title=f"Run {run.manifest.run_id}")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    s = run.summary
    for label, value, style in [
        ("Total requirements", s.total, ""),
        ("Passed", s.passed, "green"),
        ("Failed", s.failed, "red"),
        ("Warnings", s.warnings, "yellow"),
        ("Errors", s.errors, "red"),
        ("Not tested", s.not_tested, "dim"),
        ("Pass rate", f"{s.pass_rate}%", "bold"),
    ]:
        table.add_row(label, str(value), style=style or None)
    return table


@app.command()
def run(
    pdfs: list[Path] = typer.Argument(
        ..., exists=True, readable=True,
        help="One or more specification PDFs (merged into a single traceability matrix)",
    ),
    config: Path | None = _CONFIG_OPT,
    base_url: str | None = typer.Option(None, help="Override site.base_url"),
    depth: int | None = typer.Option(
        None, help="Crawl depth: 0 = only the given page (default), N = follow links N levels"
    ),
    headed: bool = typer.Option(False, help="Run the browser headed (debugging)"),
    output: Path | None = typer.Option(None, help="Output root (default: runs/)"),
) -> None:
    """Execute the full validation pipeline against the configured site."""
    from webval.pipeline import ValidationPipeline

    settings = load_settings(config)
    if base_url:
        settings.site.base_url = base_url
        settings.site.allowed_hosts = []
        settings.site = settings.site.model_validate(settings.site.model_dump())
    if depth is not None:
        settings.site.max_depth = depth
    if headed:
        settings.browser.headless = False

    from webval.crawler.browser import CredentialsMissingError

    pipeline = ValidationPipeline(settings, output_root=output)
    try:
        validation_run = asyncio.run(pipeline.execute(pdfs))
    except CredentialsMissingError as exc:
        console.print(f"[bold red]Cannot start:[/bold red] {exc}")
        raise typer.Exit(code=2) from None

    console.print(_summary_table(validation_run))
    console.print(f"\n[bold]Run directory:[/bold] {pipeline.run_dir}")
    exit_code = 0
    if validation_run.summary.failed or validation_run.summary.errors:
        exit_code = 1  # CI-friendly: failures fail the build
    raise typer.Exit(code=exit_code)


@app.command()
def extract(
    pdfs: list[Path] = typer.Argument(..., exists=True, readable=True, help="One or more specification PDFs"),
    config: Path | None = _CONFIG_OPT,
    out: Path = typer.Option(Path("requirements.json"), help="Output JSON path"),
) -> None:
    """Phase 1 only: extract requirements from the specification PDF(s)."""
    from webval.pdf_parser import PdfExtractor
    from webval.requirement_engine import RequirementEngine

    setup_logging()
    settings = load_settings(config)
    image_dir = out.parent / "pdf_images"
    extractor = PdfExtractor(settings, image_output_dir=image_dir)
    docs = [extractor.extract(pdf) for pdf in pdfs]
    requirements = RequirementEngine(settings).extract_many(docs)
    out.write_text(requirements.model_dump_json(indent=2), encoding="utf-8")

    names = ", ".join(p.name for p in pdfs)
    table = Table(title=f"{len(requirements)} requirements extracted from {names}")
    table.add_column("ID")
    table.add_column("Category")
    table.add_column("Requirement", max_width=60)
    table.add_column("Source", max_width=24)
    for req in requirements:
        table.add_row(
            req.id, req.category.value, req.requirement,
            f"{req.source.document} p.{req.source.page_number}",
        )
    console.print(table)
    console.print(f"Written to [bold]{out}[/bold]")


@app.command()
def crawl(
    config: Path | None = _CONFIG_OPT,
    base_url: str | None = typer.Option(None, help="Override site.base_url"),
    depth: int | None = typer.Option(
        None, help="Crawl depth: 0 = only the given page (default), N = follow links N levels"
    ),
    output: Path | None = typer.Option(None, help="Output root (default: runs/)"),
) -> None:
    """Phases 2-3 only: authenticate, discover pages, capture snapshots."""
    from webval.crawler import BrowserSession, SiteDiscovery
    from webval.evidence import EvidenceStore
    from webval.evidence.store import make_run_id
    from webval.utils import utc_now_iso

    settings = load_settings(config)
    if base_url:
        settings.site.base_url = base_url
        settings.site.allowed_hosts = []
        settings.site = settings.site.model_validate(settings.site.model_dump())
    if depth is not None:
        settings.site.max_depth = depth

    run_dir = (output or Path(settings.evidence.root)) / make_run_id(settings.site.base_url, utc_now_iso())
    store = EvidenceStore(run_dir)
    setup_logging(store.log_file)

    async def _crawl() -> None:
        async with BrowserSession(settings) as session:
            site_map = await SiteDiscovery(settings, session, store).discover()
            (run_dir / "sitemap.json").write_text(site_map.model_dump_json(indent=2), encoding="utf-8")

    from webval.crawler.browser import CredentialsMissingError

    try:
        asyncio.run(_crawl())
    except CredentialsMissingError as exc:
        console.print(f"[bold red]Cannot start:[/bold red] {exc}")
        raise typer.Exit(code=2) from None
    console.print(f"Crawl complete. Snapshots + sitemap.json in [bold]{run_dir}[/bold]")


@app.command()
def report(
    results: Path = typer.Argument(..., exists=True, help="results.json of a prior run"),
    config: Path | None = _CONFIG_OPT,
) -> None:
    """Regenerate Excel + HTML reports from stored JSON results."""
    from webval.reports import write_excel_report, write_html_report
    from webval.reports.json_out import read_json_results

    setup_logging()
    settings = load_settings(config)
    run = read_json_results(results)
    run_dir = results.parent
    write_excel_report(run, run_dir / "traceability_matrix.xlsx", settings.report)
    write_html_report(
        run, run_dir / "validation_report.html",
        title=settings.report.title,
        organization=settings.report.organization,
        system_under_test=settings.report.system_under_test or run.manifest.base_url,
    )
    console.print(_summary_table(run))
    console.print(f"Reports regenerated in [bold]{run_dir}[/bold]")


@app.command()
def doctor(
    config: Path | None = _CONFIG_OPT,
    base_url: str | None = typer.Option(None, help="Override site.base_url"),
) -> None:
    """Preflight diagnostics — run this first on any new machine or network.

    Checks, in order: credential loading, browser availability (bundled
    Chromium, then installed Chrome/Edge), site reachability + authentication
    (direct request, then via the detected system proxy, then a real browser
    navigation that follows system/PAC proxy settings), and OCR backends.
    Every problem prints its exact fix. Exit code 0 = ready to run.
    """
    import platform

    from playwright.async_api import async_playwright

    import webval as _webval

    settings = load_settings(config)
    if base_url:
        settings.site.base_url = base_url

    failures = 0

    def _ok(label: str, detail: str) -> None:
        console.print(f"[green] PASS [/green] [bold]{label}[/bold] — {detail}")

    def _warn(label: str, detail: str) -> None:
        console.print(f"[yellow] WARN [/yellow] [bold]{label}[/bold] — {detail}")

    def _fail(label: str, detail: str) -> None:
        nonlocal failures
        failures += 1
        console.print(f"[red] FAIL [/red] [bold]{label}[/bold] — {detail}")

    console.print(f"[bold]webval {_webval.__version__} preflight[/bold]")
    console.print(
        f"Python {platform.python_version()} on {platform.system()} {platform.release()} — "
        f"running from: {Path.cwd()}\n"
    )

    # 1. Credentials ------------------------------------------------------
    env_file = Path(".env")
    user = settings.auth.username
    password = settings.auth.password.get_secret_value()
    if settings.auth.mode != "http_basic":
        _warn("Credentials", f"auth.mode={settings.auth.mode!r} — no HTTP Basic auth will be sent")
    elif user and password:
        source = ".env in this folder" if env_file.is_file() else "environment variables"
        _ok("Credentials", f"user={user[:2]}***, password=***{len(password)} chars (from {source})")
    elif env_file.is_file():
        _fail(
            "Credentials",
            ".env exists here but WEBVAL_AUTH__USERNAME / WEBVAL_AUTH__PASSWORD did not both parse. "
            "Each line must be exactly KEY=value (no spaces around '='); the password is taken "
            "verbatim, so '#' and quotes inside it are fine.",
        )
    else:
        _fail(
            "Credentials",
            f"no .env file in {Path.cwd()} and no WEBVAL_AUTH__* environment variables set. "
            "webval reads .env from the folder you run it in — cd to the folder containing .env "
            "(or run `webval setup` to scaffold one).",
        )

    # 2 + 3. Browser and site access (async probes) -----------------------
    def _channel_name(channel: str | None) -> str:
        return f"installed browser (channel={channel})" if channel else "bundled Chromium"

    async def _probes() -> None:
        from playwright.async_api import HttpCredentials

        from webval.utils import detect_proxy

        pw = await async_playwright().start()
        try:
            creds: HttpCredentials | None = (
                {"username": user, "password": password} if user and password else None
            )
            target = settings.site.base_url

            # Browser availability: same fallback order the crawler uses.
            configured = settings.browser.channel
            channels: list[str | None] = [configured] if configured else [None, "chrome", "msedge"]
            engine = getattr(pw, settings.browser.engine)
            launchable: list[str | None] = []
            launch_errors: list[str] = []
            for channel in channels:
                try:
                    opts: dict[str, Any] = {"headless": True}
                    if channel:
                        opts["channel"] = channel
                    browser = await engine.launch(**opts)
                    await browser.close()
                    launchable.append(channel)
                except Exception as exc:
                    launch_errors.append(f"{channel or 'bundled chromium'}: {str(exc).splitlines()[0]}")
            if launchable:
                _ok("Browser", f"available: {', '.join(_channel_name(c) for c in launchable)}")
            else:
                _fail(
                    "Browser",
                    "no usable browser. Either run `playwright install chromium` on a network that "
                    "can reach cdn.playwright.dev, or install Google Chrome / Microsoft Edge. "
                    f"({'; '.join(launch_errors)})",
                )

            # Site access, tested the way the crawl will actually connect:
            #   1) direct HTTP request   2) same request via the detected
            #   system proxy   3) real browser navigation, which follows the
            #   machine's system/PAC proxy settings exactly like the user's
            #   desktop browser. Corporate networks commonly block 1 (even
            #   DNS) while 2 or 3 work.
            async def request_probe(proxy: str | None) -> tuple[int | None, str]:
                kwargs: dict[str, Any] = {
                    "http_credentials": creds,
                    "ignore_https_errors": settings.site.ignore_https_errors,
                }
                if proxy:
                    kwargs["proxy"] = {"server": proxy}
                request_ctx = await pw.request.new_context(**kwargs)
                try:
                    response = await request_ctx.get(target, timeout=20_000)
                    return response.status, ""
                except Exception as exc:
                    return None, str(exc).splitlines()[0]
                finally:
                    await request_ctx.dispose()

            via = "direct connection"
            note = ""
            status, first_error = await request_probe(None)
            detected = settings.browser.proxy or detect_proxy()
            if status is None and detected:
                proxy_status, _proxy_error = await request_probe(detected)
                if proxy_status is not None:
                    status = proxy_status
                    via = f"proxy {detected}"
                    note = " The crawler applies this proxy automatically."

            last_error = first_error
            if status is None:
                for channel in launchable:
                    opts = {"headless": True}
                    if channel:
                        opts["channel"] = channel
                    browser = await engine.launch(**opts)
                    try:
                        browser_ctx = await browser.new_context(
                            http_credentials=creds,
                            ignore_https_errors=settings.site.ignore_https_errors,
                        )
                        page = await browser_ctx.new_page()
                        response = await page.goto(target, timeout=30_000)
                        status = response.status if response else 200
                    except Exception as exc:
                        message = str(exc).splitlines()[0]
                        # Chrome/Edge surface an unhandled 401 as a navigation
                        # error — the site was reached, credentials rejected.
                        if "ERR_INVALID_AUTH_CREDENTIALS" in message or "ERR_HTTP_RESPONSE_CODE_FAILURE" in message:
                            status = 401
                        else:
                            last_error = message
                            continue
                    finally:
                        await browser.close()
                    via = f"{_channel_name(channel)} using the system proxy settings"
                    if channel and channel != launchable[0]:
                        note = (
                            f" Pin this browser for the crawl: add WEBVAL_BROWSER__CHANNEL={channel} "
                            "to .env (direct connections are blocked on this network)."
                        )
                    else:
                        note = " The crawler uses this same browser, so runs connect the same way."
                    break

            if status is None:
                _fail(
                    "Site access",
                    f"could not reach {target} by any route (direct request, detected proxy, or browser "
                    f"navigation). Last error: {last_error or 'n/a'}. Since the site opens in your desktop "
                    "browser, this network likely requires an authenticated proxy: ask IT for the proxy "
                    "address and add WEBVAL_BROWSER__PROXY=http://<host>:<port> to .env, then re-run "
                    "webval doctor.",
                )
            elif status < 400:
                _ok("Site access", f"HTTP {status} from {target} via {via} — authentication accepted.{note}")
            elif status == 401:
                _fail(
                    "Site access",
                    f"the site was reached via {via}, but it REJECTED these credentials (HTTP 401). "
                    "Compare the masked user/password length above against the real values "
                    f"(no extra spaces or quotes in .env).{note}",
                )
            elif status == 403:
                _fail(
                    "Site access",
                    f"HTTP 403 from {target} via {via} — reachable and authenticated, but access is "
                    f"forbidden (IP allow-listing / VPN egress?). Ask the site team to allow this network.{note}",
                )
            else:
                _warn(
                    "Site access",
                    f"HTTP {status} from {target} via {via} — reachable, but the URL returned an error.{note}",
                )
        finally:
            await pw.stop()

    asyncio.run(_probes())

    # 4. OCR ---------------------------------------------------------------
    from webval.pdf_parser.ocr import find_tesseract, get_backend

    tesseract = find_tesseract()
    if tesseract:
        _ok("OCR", f"tesseract found: {tesseract}")
    elif get_backend("rapidocr") is not None:
        _warn(
            "OCR",
            "tesseract not found — RapidOCR fallback active (works, but lower accuracy on annotated "
            "proofs). Best quality, no admin needed: winget install UB-Mannheim.TesseractOCR",
        )
    else:
        _fail(
            "OCR",
            "no OCR backend — image-only annotated-proof PDFs will yield 0 requirements. "
            "Fix without admin rights: pip install rapidocr-onnxruntime  (or install tesseract).",
        )

    console.print()
    if failures:
        console.print(
            f"[bold red]{failures} blocking problem(s).[/bold red] "
            "Fix the FAIL lines above, then re-run: webval doctor"
        )
        raise typer.Exit(code=2)
    console.print('[bold green]All checks passed — ready:[/bold green] webval run "path\\to\\spec.pdf"')


@app.command()
def setup() -> None:
    """Post-install setup (cross-platform): browser download, OCR check, .env scaffold.

    Run once after `pip install`: fetches Playwright Chromium and verifies the
    tesseract OCR engine (needed for image-only / annotated-proof PDFs).
    """
    import platform
    import shutil
    import subprocess
    import sys

    console.print("[bold]1/3[/bold] Installing Playwright Chromium (skipped if present)...")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    except subprocess.CalledProcessError:
        console.print(
            "    [yellow]Browser download failed[/yellow] — cdn.playwright.dev is unreachable "
            "(common on corporate networks). Not a problem: webval automatically uses the "
            "machine's installed Chrome or Microsoft Edge instead."
        )

    console.print("[bold]2/3[/bold] Checking tesseract OCR engine...")
    from webval.pdf_parser.ocr import find_tesseract, get_backend

    tesseract = find_tesseract()
    if tesseract:
        console.print(f"    tesseract found: {tesseract}")
    else:
        console.print("    [yellow]tesseract not found[/yellow]")
        hints = {
            "Windows": "winget install UB-Mannheim.TesseractOCR   (or: choco install tesseract)",
            "Darwin": "brew install tesseract",
            "Linux": "sudo apt-get install tesseract-ocr",
        }
        if get_backend("rapidocr") is not None:
            console.print("    Using pure-pip RapidOCR fallback instead — no admin rights needed. "
                          "(tesseract gives the best quality; optional: "
                          f"[bold]{hints.get(platform.system(), 'see tesseract-ocr docs')}[/bold])")
        else:
            console.print("    Enable OCR without admin rights: [bold]pip install rapidocr-onnxruntime[/bold] "
                          f"(or install tesseract: {hints.get(platform.system(), 'see tesseract-ocr docs')})")

    console.print("[bold]3/3[/bold] Credentials scaffold...")
    env_file, example = Path(".env"), Path(".env.example")
    if not env_file.exists():
        if example.exists():
            shutil.copy(example, env_file)
        else:
            env_file.write_text("WEBVAL_AUTH__USERNAME=\nWEBVAL_AUTH__PASSWORD=\n", encoding="utf-8")
        console.print("    Created .env — add WEBVAL_AUTH__USERNAME / WEBVAL_AUTH__PASSWORD")
    else:
        console.print("    .env already exists")
    console.print(
        "\n[bold green]Setup complete.[/bold green] Now verify the environment end-to-end: "
        "[bold]webval doctor[/bold]"
    )


@app.command()
def statuses() -> None:
    """List the status vocabulary used in reports (for SOP documentation)."""
    for status in Status:
        console.print(f"- {status.value}")


if __name__ == "__main__":
    app()
