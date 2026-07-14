# webval — CI/CD Recommendations

## 1. Two pipelines, two purposes

**Pipeline A — framework CI** (on every commit/PR to this repo):
lint (`ruff`), types (`mypy`), unit tests, then integration tests with Playwright browsers.
Artifacts: coverage report, sample-run output as a build artifact for reviewer download.

**Pipeline B — validation execution** (scheduled or triggered by site deployments):
runs `webval run spec.pdf` against the target environment and archives the run directory.
This is the pipeline your QA team consumes.

## 2. Reference GitHub Actions workflow (framework CI)

See [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) in this repo. Highlights:
- matrix on Python 3.12/3.13
- Playwright browser cache keyed on the playwright version
- `pytest -m "not integration"` first (fast feedback), then the browser jobs
- sample run executed and uploaded as an artifact

## 3. Validation-execution pipeline (pattern)

```yaml
# e.g. Jenkins/GitLab/Actions — pseudocode stages
stages:
  - name: Extract & approve baseline
    run: webval extract "$SPEC_PDF" --out baseline/requirements.json
    # manual gate: QA reviews/approves the baseline diff before proceeding

  - name: Execute validation
    env:
      WEBVAL_AUTH__USERNAME: ${{ secrets.SITE_USERNAME }}
      WEBVAL_AUTH__PASSWORD: ${{ secrets.SITE_PASSWORD }}
    run: webval run "$SPEC_PDF" --base-url "$TARGET_URL" --output runs/
    # non-zero exit when any requirement Fails/Errors -> pipeline fails

  - name: Archive evidence
    run: zip -r validation-$BUILD_ID.zip runs/
    # push to controlled document storage (S3 + retention policy / Veeva / SharePoint)
```

Operational rules:
- **Secrets** only via the CI secret store → env vars (`WEBVAL_AUTH__*`). Never in YAML/repo.
- **Pin the tool** (tag or container digest) per formal run; the run manifest records the version.
- **Archive the whole run directory** (reports + evidence + ledger + logs) — it is the UAT record.
  Verify the ledger after unarchiving if evidence integrity is questioned.
- **Headless preprod access**: run the agent inside the network segment that can reach the
  preprod host; `site.ignore_https_errors` covers internal CAs.
- **Scheduling**: nightly runs against preprod catch content drift early; gate production
  releases on a green validation run.

## 4. Containerization

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
COPY . .
RUN pip install -e . && playwright install chromium
ENTRYPOINT ["webval"]
```

The Playwright base image ships all browser OS dependencies; pin its tag to the playwright
version in `pyproject.toml`.

## 5. Quality gates summary

| Gate | Tool | Blocking |
|---|---|---|
| Lint / format | ruff | yes |
| Static types | mypy (strict) | yes |
| Unit tests | pytest | yes |
| Integration tests | pytest -m integration | yes |
| Website validation | webval run (exit code) | yes, for site releases |
| Evidence integrity | ledger verification (in-run) | yes |
