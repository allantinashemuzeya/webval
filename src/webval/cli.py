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

    Checks, in order: credential loading, site reachability + authentication
    (a real authenticated HTTP request, no browser needed), browser
    availability (bundled Chromium, then installed Chrome/Edge), and OCR
    backends. Every problem prints its exact fix. Exit code 0 = ready to run.
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

    # 2 + 3. Site access and browser (async probes) -----------------------
    async def _probes() -> None:
        pw = await async_playwright().start()
        try:
            # Site reachability + auth: a real authenticated request through
            # Playwright's HTTP client — works even with no browser installed.
            from playwright.async_api import HttpCredentials

            creds: HttpCredentials | None = (
                {"username": user, "password": password} if user and password else None
            )
            request_ctx = await pw.request.new_context(
                http_credentials=creds,
                ignore_https_errors=settings.site.ignore_https_errors,
            )
            target = settings.site.base_url
            try:
                response = await request_ctx.get(target, timeout=20_000)
                status = response.status
                if status < 400:
                    _ok("Site access", f"HTTP {status} from {target} — network OK, authentication accepted")
                elif status == 401:
                    _fail(
                        "Site access",
                        f"HTTP 401 from {target} — the site is reachable but REJECTED these credentials. "
                        "Compare the masked user/password length above against the real values "
                        "(no extra spaces or quotes in .env).",
                    )
                elif status == 403:
                    _fail(
                        "Site access",
                        f"HTTP 403 from {target} — reachable and authenticated, but access is forbidden "
                        "(IP allow-listing / VPN egress?). Ask the site team to allow this network.",
                    )
                else:
                    _warn("Site access", f"HTTP {status} from {target} — reachable, but the URL returned an error")
            except Exception as exc:
                message = str(exc).splitlines()[0]
                if any(s in message for s in ("ENOTFOUND", "getaddrinfo", "ERR_NAME_NOT_RESOLVED")):
                    _fail(
                        "Site access",
                        f"DNS cannot resolve {target} — this machine cannot see the site at all. "
                        "Connect the VPN / correct network, then re-run webval doctor.",
                    )
                elif "Timeout" in message or "ETIMEDOUT" in message:
                    _fail(
                        "Site access",
                        f"connection to {target} timed out — usually VPN not connected or a proxy "
                        "is required on this network.",
                    )
                else:
                    _fail("Site access", f"request to {target} failed: {message}")
            finally:
                await request_ctx.dispose()

            # Browser availability: same fallback order the crawler uses.
            configured = settings.browser.channel
            channels: list[str | None] = [configured] if configured else [None, "chrome", "msedge"]
            engine = getattr(pw, settings.browser.engine)
            tried: list[str] = []
            found: str | None = None
            for channel in channels:
                try:
                    opts = {"headless": True, "channel": channel} if channel else {"headless": True}
                    browser = await engine.launch(**opts)
                    await browser.close()
                    found = f"installed browser (channel={channel})" if channel else "bundled Chromium"
                    break
                except Exception as exc:
                    tried.append(f"{channel or 'bundled chromium'}: {str(exc).splitlines()[0]}")
            if found:
                _ok("Browser", f"{found} launches headless")
            else:
                details = "; ".join(tried)
                _fail(
                    "Browser",
                    "no usable browser. Either run `playwright install chromium` on a network that "
                    f"can reach cdn.playwright.dev, or install Google Chrome / Microsoft Edge. ({details})",
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
