"""Unit tests for domain models: run summary, defects, requirement validation."""

import pytest
from pydantic import ValidationError

from webval.models import Requirement, RequirementCategory, RequirementSource, Status


class TestRequirementModel:
    def test_id_pattern_enforced(self):
        with pytest.raises(ValidationError):
            Requirement(
                id="bad id", category=RequirementCategory.CONTENT,
                requirement="x" * 20, expected="y",
                source=RequirementSource(document="s.pdf", page_number=1, extraction_method="table"),
            )

    def test_statement_whitespace_normalized(self):
        req = Requirement(
            id="REQ-001", category=RequirementCategory.CONTENT,
            requirement="  spaced   out\nstatement  ", expected="ok",
            source=RequirementSource(document="s.pdf", page_number=1, extraction_method="table"),
        )
        assert req.requirement == "spaced out statement"


class TestRunSummary:
    def test_counts(self, sample_run):
        s = sample_run.summary
        assert s.total == 4
        assert s.passed == 1
        assert s.failed == 1
        assert s.warnings == 1
        assert s.not_tested == 1
        assert s.errors == 0

    def test_pass_rate_excludes_not_tested(self, sample_run):
        # 3 executed, 1 passed -> 33.3%
        assert sample_run.summary.pass_rate == 33.3

    def test_defects_only_fail_warn_error(self, sample_run):
        defects = sample_run.defects
        ids = {d.requirement_id for d in defects}
        assert ids == {"REQ-002", "REQ-003"}

    def test_defect_severity_mapping(self, sample_run):
        by_id = {d.requirement_id: d for d in sample_run.defects}
        assert by_id["REQ-002"].severity == "Major"
        assert by_id["REQ-003"].severity == "Minor"

    def test_status_enum_values(self):
        assert Status.PASS.value == "Pass"
        assert Status.NOT_TESTED.value == "Not Tested"
