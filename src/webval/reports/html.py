"""HTML validation report (Jinja2 dashboard).

The report is written into the run directory so that evidence hyperlinks
(relative paths like ``evidence/screenshots/...``) resolve when the folder is
zipped and shared with reviewers.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from webval.models import EvidenceKind, Status, ValidationRun
from webval.utils import get_logger, utc_now_iso

log = get_logger("reports.html")

_STATUS_KEY = {
    Status.PASS: "Pass",
    Status.FAIL: "Fail",
    Status.WARNING: "Warning",
    Status.NOT_TESTED: "NotTested",
    Status.ERROR: "Error",
}


def write_html_report(run: ValidationRun, output_path: Path, *, title: str,
                      organization: str, system_under_test: str) -> Path:
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("report.html.j2")

    rows = []
    failed_shots = []
    for req in run.requirements:
        res = run.result_for(req.id)
        status = res.status if res else Status.NOT_TESTED
        evidence = [
            {
                "href": ev.path,
                "label": f"{ev.kind.value}: {Path(ev.path).name}",
                "sha256": ev.sha256,
            }
            for ev in (res.evidence if res else [])
        ]
        rows.append(
            {
                "id": req.id,
                "category": req.category.value,
                "requirement": req.requirement,
                "expected": req.expected,
                "actual": res.actual if res else "Not executed",
                "details": res.details if res else "",
                "status": status.value,
                "status_key": _STATUS_KEY[status],
                "page_url": res.page_url if res else "",
                "evidence": evidence,
                "timestamp": res.finished_at if res else "",
                "source_page": req.source.page_number,
                "source_doc": req.source.document,
                "method": req.source.extraction_method,
            }
        )
        if res and status in (Status.FAIL, Status.ERROR, Status.WARNING):
            for ev in res.evidence:
                if ev.kind in (EvidenceKind.SCREENSHOT, EvidenceKind.ELEMENT_SCREENSHOT,
                               EvidenceKind.VISUAL_DIFF):
                    failed_shots.append(
                        {
                            "requirement_id": req.id,
                            "status": status.value,
                            "status_key": _STATUS_KEY[status],
                            "description": ev.description,
                            "href": ev.path,
                        }
                    )
                    break  # one representative screenshot per failed requirement

    html = template.render(
        title=title,
        organization=organization,
        system_under_test=system_under_test,
        manifest=run.manifest,
        summary=run.summary,
        rows=rows,
        defects=run.defects,
        failed_shots=failed_shots,
        generated_at=utc_now_iso(),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info("HTML validation report written: %s", output_path)
    return output_path
