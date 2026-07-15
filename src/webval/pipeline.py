"""End-to-end validation pipeline.

    PDF -> RequirementSet -> authenticated crawl -> SiteMap
        -> validator dispatch -> results + evidence -> reports

Each stage logs to the run's execution log; the run manifest records the
spec hash, effective (redacted) config, and execution window for audit.
"""

from __future__ import annotations

from pathlib import Path

import webval
from webval.config import Settings
from webval.crawler import BrowserSession, SiteDiscovery
from webval.evidence import EvidenceStore
from webval.evidence.store import make_run_id
from webval.models import PdfDocument, RequirementSet, RunManifest, ValidationRun
from webval.pdf_parser import PdfExtractor
from webval.reports import write_excel_report, write_html_report, write_json_results
from webval.requirement_engine import RequirementEngine
from webval.utils import get_logger, setup_logging, utc_now_iso
from webval.validators import ValidationContext, ValidatorRunner
from webval.validators.registry import default_validators

log = get_logger("pipeline")


class ValidationPipeline:
    """Owns a single run: evidence store, browser session, and reporting."""

    def __init__(self, settings: Settings, output_root: Path | None = None) -> None:
        self.settings = settings
        started = utc_now_iso()
        run_id = make_run_id(settings.site.base_url, started)
        root = output_root or Path(settings.evidence.root)
        self.run_dir = root / run_id
        self.store = EvidenceStore(self.run_dir)
        setup_logging(self.store.log_file)
        self.manifest = RunManifest(
            run_id=run_id,
            started_at=started,
            base_url=settings.site.base_url,
            spec_document="",
            spec_sha256="",
            tool_version=webval.__version__,
            config_snapshot=settings.redacted_dump(),
        )
        log.info("Run %s started (output: %s)", run_id, self.run_dir)

    # ------------------------------------------------------------ phase 1

    def extract_requirements(self, pdf_paths: list[Path]) -> tuple[RequirementSet, list[PdfDocument]]:
        extractor = PdfExtractor(self.settings, image_output_dir=self.store.pdf_images_dir)
        docs = [extractor.extract(path) for path in pdf_paths]
        self.manifest.spec_document = "; ".join(d.file_name for d in docs)
        self.manifest.spec_sha256 = "; ".join(d.sha256 for d in docs)
        engine = RequirementEngine(self.settings)
        requirements = engine.extract_many(docs)
        # Persist the extracted baseline as evidence of what was tested against.
        (self.run_dir / "requirements.json").write_text(
            requirements.model_dump_json(indent=2), encoding="utf-8"
        )
        return requirements, docs

    # -------------------------------------------------------- phases 2-16

    async def execute(self, pdf_paths: list[Path] | Path) -> ValidationRun:
        if isinstance(pdf_paths, Path):
            pdf_paths = [pdf_paths]
        requirements, docs = self.extract_requirements(pdf_paths)
        if not len(requirements):
            log.warning("No requirements extracted from the specification(s) — nothing to validate")

        async with BrowserSession(self.settings) as session:
            discovery = SiteDiscovery(self.settings, session, self.store)
            site_map = await discovery.discover()
            usable = [p for p in site_map.pages if p.status and p.status < 400]
            if not usable:
                log.error(
                    "CRAWL FAILED: no page could be fetched from %s. Every validation below will "
                    "fail as a consequence. Most common causes: wrong/missing WEBVAL_AUTH__USERNAME / "
                    "WEBVAL_AUTH__PASSWORD in .env, or the site requires VPN/proxy access from this machine. "
                    "Test quickly with: webval crawl",
                    self.settings.site.base_url,
                )

            ctx = ValidationContext(
                settings=self.settings,
                session=session,
                site_map=site_map,
                store=self.store,
                pdf_docs=docs,
            )
            runner = ValidatorRunner(ctx, default_validators(ctx))
            results = await runner.run(requirements)

        self.manifest.finished_at = utc_now_iso()
        run = ValidationRun(manifest=self.manifest, requirements=requirements, results=results)
        self._write_reports(run)
        self._final_log(run)
        return run

    # ----------------------------------------------------------- reporting

    def _write_reports(self, run: ValidationRun) -> None:
        report_cfg = self.settings.report
        if "json" in report_cfg.formats:
            write_json_results(run, self.run_dir / "results.json")
        if "excel" in report_cfg.formats:
            write_excel_report(run, self.run_dir / "traceability_matrix.xlsx", report_cfg)
        if "html" in report_cfg.formats:
            write_html_report(
                run,
                self.run_dir / "validation_report.html",
                title=report_cfg.title,
                organization=report_cfg.organization,
                system_under_test=report_cfg.system_under_test or self.settings.site.base_url,
            )
        problems = self.store.verify_ledger()
        if problems:
            for problem in problems:
                log.error("EVIDENCE INTEGRITY: %s", problem)
        else:
            log.info("Evidence ledger verified: all artifact hashes match")

    def _final_log(self, run: ValidationRun) -> None:
        s = run.summary
        log.info(
            "Run %s complete — total=%d pass=%d fail=%d warn=%d error=%d not-tested=%d (pass rate %.1f%%)",
            run.manifest.run_id, s.total, s.passed, s.failed, s.warnings, s.errors, s.not_tested, s.pass_rate,
        )
        log.info("Reports: %s", ", ".join(str(p) for p in sorted(self.run_dir.glob("*.???*")) if p))
