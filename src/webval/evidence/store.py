"""Evidence store: run-scoped, hash-verified, append-only audit trail.

Layout per run:

    runs/<run_id>/
        evidence/
            screenshots/   full-page + element captures
            downloads/     downloaded artifacts
            html/          DOM snapshots and snippets
            logs/          execution log + audit ledger
            pdf_images/    images extracted from the specification
            visual_diffs/  visual comparison outputs
        results.json
        traceability_matrix.xlsx
        validation_report.html

Every artifact is SHA-256 hashed on write and recorded in an append-only
JSONL ledger (`logs/evidence_ledger.jsonl`), giving regulated reviewers a
tamper-evident chain from matrix row to file on disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from webval.models import Evidence, EvidenceKind
from webval.utils import get_logger, sha256_file, utc_now_iso
from webval.utils.text import slugify

log = get_logger("evidence.store")

_SUBDIRS = {
    EvidenceKind.SCREENSHOT: "screenshots",
    EvidenceKind.ELEMENT_SCREENSHOT: "screenshots",
    EvidenceKind.DOM_SNIPPET: "html",
    EvidenceKind.HTML: "html",
    EvidenceKind.DOWNLOAD: "downloads",
    EvidenceKind.LOG: "logs",
    EvidenceKind.NETWORK: "logs",
    EvidenceKind.VISUAL_DIFF: "visual_diffs",
    EvidenceKind.METRIC: "logs",
}


class EvidenceStore:
    """Creates, hashes, and ledgers every evidence artifact for a run."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.evidence_dir = run_dir / "evidence"
        for sub in ("screenshots", "downloads", "html", "logs", "pdf_images", "visual_diffs"):
            (self.evidence_dir / sub).mkdir(parents=True, exist_ok=True)
        self._ledger_path = self.evidence_dir / "logs" / "evidence_ledger.jsonl"
        self._counter = 0

    # ------------------------------------------------------------------ paths

    @property
    def pdf_images_dir(self) -> Path:
        return self.evidence_dir / "pdf_images"

    @property
    def log_file(self) -> Path:
        return self.evidence_dir / "logs" / "execution.log"

    def new_path(self, kind: EvidenceKind, label: str, suffix: str) -> Path:
        """Reserve a unique evidence file path (file not yet written)."""
        self._counter += 1
        name = f"{self._counter:04d}-{slugify(label)}{suffix}"
        return self.evidence_dir / _SUBDIRS[kind] / name

    # ---------------------------------------------------------------- writing

    def add_file(
        self,
        kind: EvidenceKind,
        path: Path,
        description: str,
        page_url: str = "",
    ) -> Evidence:
        """Register an already-written artifact: hash it and append to the ledger."""
        if not path.is_file():
            raise FileNotFoundError(f"Evidence file missing: {path}")
        evidence = Evidence(
            kind=kind,
            path=str(path.relative_to(self.run_dir)),
            description=description,
            sha256=sha256_file(path),
            page_url=page_url,
            captured_at=utc_now_iso(),
        )
        self._append_ledger(evidence)
        return evidence

    def add_text(
        self,
        kind: EvidenceKind,
        label: str,
        content: str,
        description: str,
        page_url: str = "",
        suffix: str = ".txt",
    ) -> Evidence:
        """Write text content (DOM snippet, metric dump, log extract) as evidence."""
        path = self.new_path(kind, label, suffix)
        path.write_text(content, encoding="utf-8")
        return self.add_file(kind, path, description, page_url)

    def add_json(self, kind: EvidenceKind, label: str, data: object, description: str, page_url: str = "") -> Evidence:
        return self.add_text(
            kind, label, json.dumps(data, indent=2, default=str), description, page_url, suffix=".json"
        )

    # ----------------------------------------------------------------- ledger

    def _append_ledger(self, evidence: Evidence) -> None:
        with open(self._ledger_path, "a", encoding="utf-8") as fh:
            fh.write(evidence.model_dump_json() + "\n")

    def verify_ledger(self) -> list[str]:
        """Re-hash every ledgered artifact; return descriptions of mismatches."""
        problems: list[str] = []
        if not self._ledger_path.is_file():
            return problems
        for line_no, line in enumerate(self._ledger_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            entry = Evidence.model_validate_json(line)
            target = self.run_dir / entry.path
            if not target.is_file():
                problems.append(f"ledger line {line_no}: missing file {entry.path}")
            elif sha256_file(target) != entry.sha256:
                problems.append(f"ledger line {line_no}: hash mismatch for {entry.path}")
        if problems:
            log.error("Evidence ledger verification found %d problem(s)", len(problems))
        return problems


def make_run_id(base_url: str, now_iso: str) -> str:
    """Deterministic, filesystem-safe run id: <timestamp>-<host>."""
    host = re.sub(r"[^a-z0-9.-]", "", base_url.split("//")[-1].split("/")[0].lower())
    stamp = now_iso.replace(":", "").replace("-", "").replace("T", "-").split("+")[0]
    return f"{stamp}-{host}"[:80]
