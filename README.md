# webval — Requirement-Traceability Website Validation

Automated validation of a protected website against a source specification PDF, producing full
requirement traceability for regulated (pharma QA/UAT) review:

> **Requirement → Verification → Evidence → Status**

The framework reads an arbitrary specification PDF, extracts every testable requirement,
authenticates to the site with HTTP Basic Auth, crawls it, executes category-specific validators
(content, links, anchors, metadata, accessibility, images, downloads, video, responsive layout,
UI behaviour, performance, visual comparison), and emits an auditable evidence trail plus
Excel / HTML / JSON reports.

## Quick start (single install)

```bash
./install.sh                  # venv + deps + Playwright Chromium + tesseract OCR + .env scaffold
# edit .env: WEBVAL_AUTH__USERNAME / WEBVAL_AUTH__PASSWORD
source .venv/bin/activate

webval run spec1.pdf spec2.pdf ...   # accepts MULTIPLE PDFs -> one merged traceability matrix
```

### Annotated-proof PDFs (image-only)

Specs that are photos/screenshots of annotated proofs (boxed callouts like
`Links to: <url>`, `Global alt text: ...`, `Clicking on "X" anchor links to ...`) are handled
automatically: pages with no text layer are OCR'd (two-pass: body text, then each callout box is
cropped, enlarged 3x, and re-read), and the annotation pass turns the callouts into Link / Anchor /
Accessibility requirements. Each embedded screenshot also becomes a visual-comparison requirement
(capped at *Warning* for photo sources, since monitor photos can't be pixel-matched).
OCR output is draft quality — review `webval extract` output before a formal run.

The default target (`config/default.yaml`) is `https://usim.preprod.sbx.us.pluvicto.com/`.
Override per run with `--base-url` or a project YAML via `--config`.

## CLI

| Command | Purpose |
|---|---|
| `webval doctor` | Preflight diagnostics: credentials, site reachability + auth (real HTTP request), browser, OCR. Run first on any new machine/network — every problem prints its exact fix. |
| `webval run spec.pdf` | Full pipeline (phases 1–16). Exit code 1 if any requirement fails — CI-friendly. |
| `webval extract spec.pdf` | Phase 1 only: print + save the extracted requirement set. Use this to review the baseline before a run. |
| `webval crawl` | Phases 2–3 only: authentication + discovery smoke test (writes `sitemap.json`). |
| `webval report runs/<id>/results.json` | Regenerate Excel/HTML reports from stored results. |

Useful flags: `--headed` (watch the browser), `--config project.yaml`, `--output <dir>`.

## Reliable setup on locked-down Windows (no admin rights)

Corporate machines fail in predictable ways (blocked CDNs, OneDrive placeholder folders,
Notepad BOM in `.env`, proxies). This sequence avoids all of them:

```powershell
# 1. Work OUTSIDE OneDrive-synced folders
mkdir C:\webval ; cd C:\webval

# 2. Venv with the Store Python (no admin needed)
python -m venv .venv ; .\.venv\Scripts\Activate.ps1

# 3. Install a PINNED release (reproducible — not a moving branch)
pip install git+https://github.com/allantinashemuzeya/webval.git@v1.0.1

# 4. One-time setup (browser download failure is OK — Chrome/Edge is used instead)
webval setup

# 5. Put credentials in C:\webval\.env  (exact format, no spaces around '=';
#    '#' and quotes inside the password are safe)
#      WEBVAL_AUTH__USERNAME=...
#      WEBVAL_AUTH__PASSWORD=...

# 6. Verify EVERYTHING before running — each problem prints its fix
webval doctor

# 7. Only when doctor passes:
webval run "C:\path\to\Verified files.pdf"
```

If `webval doctor` passes, a run cannot fail for environmental reasons.
Always run `webval` from `C:\webval` (the folder containing `.env`).

## Outputs (per run)

```
runs/<timestamp>-<host>/
├── requirements.json            # extracted requirement baseline (audit input)
├── results.json                 # machine-readable system of record
├── traceability_matrix.xlsx     # Executive Summary / Matrix / Defects / Evidence Index
├── validation_report.html       # self-contained dashboard (relative evidence links)
└── evidence/
    ├── screenshots/             # full-page + element + before/after captures
    ├── downloads/               # downloaded artifacts (hashed)
    ├── html/                    # DOM snapshots and match-context snippets
    ├── logs/                    # execution.log + evidence_ledger.jsonl (hash chain)
    ├── pdf_images/              # images extracted from the specification
    └── visual_diffs/            # spec-vs-live composites
```

Every artifact is SHA-256 hashed at write time and recorded in an append-only ledger
(`evidence/logs/evidence_ledger.jsonl`). The ledger is re-verified at the end of each run;
`EvidenceStore.verify_ledger()` re-checks integrity at any later time.

## Configuration

Precedence: **environment (`WEBVAL_*`) > project YAML (`--config`) > `config/default.yaml`**.
Nested keys use `__` in env vars: `WEBVAL_SITE__MAX_PAGES=100`, `WEBVAL_BROWSER__HEADLESS=false`.
Credentials come only from the environment/.env and are redacted from the run manifest.

Key settings: crawl bounds (`site.max_pages`, `site.max_depth`), device matrix (`devices`),
fuzzy-match threshold (`validation.content_fuzzy_threshold`), performance budgets
(`validation.performance`), visual-diff thresholds (`validation.visual`).

## Sample outputs

`python scripts/generate_sample_run.py` builds a demo spec PDF, serves a demo site behind
HTTP Basic Auth on localhost, and executes the *entire* pipeline against it — including two
intentionally failing requirements so the reports demonstrate defect handling. Results land in
`samples/generated/`.

## Testing

```bash
pytest -m "not integration"   # unit suite (no browser needed)
pytest                        # full suite (requires: playwright install chromium)
```

## Documentation

- [docs/DESIGN.md](docs/DESIGN.md) — architecture, data models, execution flow diagrams
- [docs/TEST_STRATEGY.md](docs/TEST_STRATEGY.md) — test levels, coverage, acceptance criteria
- [docs/CICD.md](docs/CICD.md) — pipeline recommendations + reference GitHub Actions workflow

## Regulated-use notes

- The run manifest records the spec SHA-256, tool version, execution window, and the effective
  configuration (secrets redacted) — sufficient to reproduce a run.
- Requirements that no validator can execute are reported **Not Tested**, never dropped:
  the matrix always accounts for 100% of extracted requirements.
- Extraction is heuristic. For formal UAT, review `webval extract` output against the spec
  before executing, and version the approved `requirements.json` alongside the run.
