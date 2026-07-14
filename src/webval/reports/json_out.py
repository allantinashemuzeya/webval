"""JSON results export — the machine-readable system of record for a run."""

from __future__ import annotations

import json
from pathlib import Path

from webval.models import ValidationRun
from webval.utils import get_logger

log = get_logger("reports.json")


def write_json_results(run: ValidationRun, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = run.model_dump(mode="json")
    payload["summary"] = run.summary.model_dump(mode="json")
    payload["defects"] = [d.model_dump(mode="json") for d in run.defects]
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("JSON results written: %s", output_path)
    return output_path


def read_json_results(path: Path) -> ValidationRun:
    """Load a prior run (summary/defects are recomputed properties)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("summary", None)
    data.pop("defects", None)
    return ValidationRun.model_validate(data)
