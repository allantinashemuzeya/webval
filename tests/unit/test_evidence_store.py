"""Unit tests for the evidence store and audit ledger."""

from pathlib import Path

from webval.evidence import EvidenceStore
from webval.evidence.store import make_run_id
from webval.models import EvidenceKind


class TestEvidenceStore:
    def test_folder_structure_created(self, tmp_path: Path):
        EvidenceStore(tmp_path / "run1")
        for sub in ("screenshots", "downloads", "html", "logs"):
            assert (tmp_path / "run1" / "evidence" / sub).is_dir()

    def test_add_text_hashes_and_ledgers(self, tmp_path: Path):
        store = EvidenceStore(tmp_path / "run1")
        ev = store.add_text(EvidenceKind.DOM_SNIPPET, "req-001", "hello", "snippet", "https://x/")
        assert ev.sha256
        assert ev.path.startswith("evidence/html/")
        assert (tmp_path / "run1" / ev.path).read_text() == "hello"
        assert store.verify_ledger() == []

    def test_ledger_detects_tampering(self, tmp_path: Path):
        store = EvidenceStore(tmp_path / "run1")
        ev = store.add_text(EvidenceKind.LOG, "log", "original", "log")
        (tmp_path / "run1" / ev.path).write_text("tampered")
        problems = store.verify_ledger()
        assert len(problems) == 1
        assert "hash mismatch" in problems[0]

    def test_ledger_detects_deletion(self, tmp_path: Path):
        store = EvidenceStore(tmp_path / "run1")
        ev = store.add_text(EvidenceKind.LOG, "log", "original", "log")
        (tmp_path / "run1" / ev.path).unlink()
        problems = store.verify_ledger()
        assert any("missing file" in p for p in problems)

    def test_paths_unique_and_slugged(self, tmp_path: Path):
        store = EvidenceStore(tmp_path / "run1")
        p1 = store.new_path(EvidenceKind.SCREENSHOT, "Same Label!", ".png")
        p2 = store.new_path(EvidenceKind.SCREENSHOT, "Same Label!", ".png")
        assert p1 != p2
        assert "same-label" in p1.name


class TestMakeRunId:
    def test_contains_host_and_timestamp(self):
        run_id = make_run_id("https://usim.preprod.sbx.us.pluvicto.com/", "2026-07-15T10:20:30+00:00")
        assert "usim.preprod.sbx.us.pluvicto.com" in run_id
        assert run_id.startswith("20260715-102030")

    def test_filesystem_safe(self):
        run_id = make_run_id("https://ex ample/@!", "2026-07-15T10:20:30+00:00")
        assert " " not in run_id and "@" not in run_id and "!" not in run_id
