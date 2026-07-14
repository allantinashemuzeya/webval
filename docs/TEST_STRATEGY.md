# webval — Test Strategy

## 1. Objectives

Assure that the framework itself is trustworthy enough to generate validation evidence for
regulated review: deterministic extraction, correct status assignment, tamper-evident evidence,
and reports that account for 100% of requirements.

## 2. Test levels

### 2.1 Unit tests (`tests/unit`, no browser, < 5 s)

| Area | What is proven |
|---|---|
| `utils.text` | Normalization across PDF/DOM variants (curly quotes, dashes, NBSP, soft hyphens, ®/™), fuzzy windowing, slug safety |
| `requirement_engine` | Category classification rules; table header synonyms; all 5 extraction passes; ID canonicalization; duplicate suppression (incl. table rows repeated in the text stream); unique IDs |
| `pdf_parser` | Real generated PDFs (PyMuPDF fixtures): text, metadata, link annotations, plain-text URLs, hash, missing-file error |
| `crawler.parse_html` | Meta/canonical/OG, headings, link location classification (nav/footer/cta/body), internal/external/anchor flags, asset detection (image/video/download/iframe), JSON-LD incl. malformed blocks |
| `evidence` | Folder creation, hashing, ledger append, tamper detection (modified + deleted artifacts), unique paths, run-id safety |
| `models` | Status counting, pass-rate excluding Not Tested, defect severity mapping, ID pattern enforcement |
| `reports` | Excel workbook structure + matrix rows + summary counts + evidence index; HTML section completeness + XSS escaping; JSON round-trip |
| `validators.visual` | dHash determinism, scaling invariance, discrimination between layouts |

### 2.2 Integration tests (`tests/integration`, marked `integration`)

Real Chromium against a **local HTTP server enforcing Basic Auth**:
- correct credentials → full site discovered, snapshots + evidence written, ledger verifies
- wrong credentials → HTTP 401 surfaced in the site map (not a crash)

The end-to-end demo (`scripts/generate_sample_run.py`) additionally exercises the complete
pipeline — extraction from a generated spec PDF through all validators to all three reports —
including intentional failures, and doubles as a manual acceptance artifact.

### 2.3 Acceptance criteria for framework releases

1. `pytest` fully green (unit + integration).
2. Sample run produces: expected requirement count, exactly the intentional failures, zero
   Errors, ledger verification clean.
3. Excel opens in Excel/LibreOffice; HTML renders offline with working relative evidence links.
4. `mypy` and `ruff` clean.

## 3. Testing the *website* (how the framework is used)

- **Baseline review**: run `webval extract`, have QA approve `requirements.json` against the spec
  (adds the human control required for heuristic extraction).
- **Dry run** on the preprod URL with `--headed` for spot-checking selector behaviour.
- **Formal run**: clean environment, pinned tool version, archived run directory (reports +
  evidence + ledger + logs) as the UAT record.
- **Regression**: re-run per release; diff `results.json` between runs to detect status changes.

## 4. Non-functional verification

- **Repeatability**: same spec + same site build → same requirement IDs and statuses (verified by
  the deterministic ID allocator and normalized matching).
- **Auditability**: ledger re-verification (`EvidenceStore.verify_ledger`) is part of every run.
- **Robustness**: retry-with-backoff on navigation; validator crashes downgrade to `Error`
  results; run always completes and reports.

## 5. Out of scope

Penetration/security testing, load testing beyond the collected performance metrics, and
medical/regulatory content correctness (the framework verifies presence/fidelity vs the spec,
not the truth of claims).
