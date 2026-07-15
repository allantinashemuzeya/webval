"""Command-line interface.

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
    console.print("\n[bold green]Setup complete.[/bold green] Try: webval extract <spec.pdf>")


@app.command()
def statuses() -> None:
    """List the status vocabulary used in reports (for SOP documentation)."""
    for status in Status:
        console.print(f"- {status.value}")


if __name__ == "__main__":
    app()
