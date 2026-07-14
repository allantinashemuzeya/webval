"""Validation result and run models — the traceability output side.

Requirement -> Verification -> Evidence -> Status.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field

from webval.models.evidence import Evidence
from webval.models.requirement import Requirement, RequirementSet


class Status(StrEnum):
    PASS = "Pass"
    FAIL = "Fail"
    WARNING = "Warning"
    NOT_TESTED = "Not Tested"
    ERROR = "Error"


class ValidationResult(BaseModel):
    """Outcome of verifying one requirement (one row of the traceability matrix)."""

    requirement_id: str
    status: Status
    expected: str
    actual: str = Field(description="Observed behaviour, human-readable")
    details: str = Field(default="", description="Extra diagnostic detail (redirect chains, ratios, ...)")
    page_url: str = ""
    validator: str = Field(default="", description="Validator class that produced this result")
    evidence: list[Evidence] = Field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0


class DefectSummary(BaseModel):
    """A failed/warning requirement condensed for the defect section of the report."""

    requirement_id: str
    category: str
    severity: str = Field(description="Major for Fail, Minor for Warning")
    summary: str
    page_url: str = ""
    evidence_paths: list[str] = Field(default_factory=list)


class RunSummary(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    not_tested: int = 0
    errors: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pass_rate(self) -> float:
        executed = self.total - self.not_tested
        return round(self.passed / executed * 100, 1) if executed else 0.0


class RunManifest(BaseModel):
    """Audit header for a validation run (who/what/when/against which inputs)."""

    run_id: str
    started_at: str
    finished_at: str = ""
    base_url: str
    spec_document: str
    spec_sha256: str
    tool_version: str
    operator: str = Field(default="", description="User/service account that executed the run")
    config_snapshot: dict[str, Any] = Field(
        default_factory=dict, description="Effective config (secrets redacted)"
    )


class ValidationRun(BaseModel):
    """Complete result of a validation run — serialized as results.json."""

    manifest: RunManifest
    requirements: RequirementSet
    results: list[ValidationResult] = Field(default_factory=list)

    def result_for(self, requirement_id: str) -> ValidationResult | None:
        return next((r for r in self.results if r.requirement_id == requirement_id), None)

    @property
    def summary(self) -> RunSummary:
        s = RunSummary(total=len(self.requirements))
        counts = {
            Status.PASS: 0,
            Status.FAIL: 0,
            Status.WARNING: 0,
            Status.NOT_TESTED: 0,
            Status.ERROR: 0,
        }
        seen: set[str] = set()
        for r in self.results:
            counts[r.status] += 1
            seen.add(r.requirement_id)
        counts[Status.NOT_TESTED] += sum(1 for req in self.requirements if req.id not in seen)
        s.passed, s.failed = counts[Status.PASS], counts[Status.FAIL]
        s.warnings, s.errors = counts[Status.WARNING], counts[Status.ERROR]
        s.not_tested = counts[Status.NOT_TESTED]
        return s

    @property
    def defects(self) -> list[DefectSummary]:
        out: list[DefectSummary] = []
        for res in self.results:
            if res.status not in (Status.FAIL, Status.WARNING, Status.ERROR):
                continue
            req: Requirement | None = self.requirements.get(res.requirement_id)
            out.append(
                DefectSummary(
                    requirement_id=res.requirement_id,
                    category=req.category.value if req else "General",
                    severity="Major" if res.status in (Status.FAIL, Status.ERROR) else "Minor",
                    summary=f"{res.actual}" if res.actual else res.details,
                    page_url=res.page_url,
                    evidence_paths=[e.path for e in res.evidence],
                )
            )
        return out
