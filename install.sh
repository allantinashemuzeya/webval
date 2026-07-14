#!/usr/bin/env bash
# webval — single-command install.
#
#   ./install.sh
#
# Creates .venv, installs webval + Playwright Chromium + the tesseract OCR
# engine (needed for image-only / annotated-proof specification PDFs), and
# scaffolds .env for credentials. Idempotent: safe to re-run.

set -euo pipefail
cd "$(dirname "$0")"

say()  { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31mERROR:\033[0m %s\n" "$*" >&2; exit 1; }

# --- Python 3.12+
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
[ -n "$PYTHON" ] || fail "Python 3.12+ not found. Install it, or set PYTHON=/path/to/python."
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
  || fail "$($PYTHON --version) is too old — webval needs Python 3.12+."
say "Using $($PYTHON --version) ($PYTHON)"

# --- virtualenv + package
if [ ! -d .venv ]; then
  say "Creating virtual environment (.venv)"
  "$PYTHON" -m venv .venv
fi
say "Installing webval and dependencies"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[dev]"

# --- Playwright browser
say "Installing Playwright Chromium (skipped if already present)"
.venv/bin/playwright install chromium

# --- tesseract (OCR for image-only specification PDFs)
if command -v tesseract >/dev/null 2>&1; then
  say "tesseract already installed: $(tesseract --version 2>&1 | head -1)"
else
  case "$(uname -s)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then
        say "Installing tesseract via Homebrew"
        brew install tesseract
      else
        say "WARNING: Homebrew not found — install tesseract manually (https://brew.sh, then: brew install tesseract)."
        say "         webval still works; image-only PDFs will just extract 0 requirements without OCR."
      fi ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        say "Installing tesseract via apt (sudo required)"
        sudo apt-get install -y tesseract-ocr
      else
        say "WARNING: install 'tesseract' with your package manager to enable OCR."
      fi ;;
    *)
      say "WARNING: unknown OS — install tesseract manually to enable OCR." ;;
  esac
fi

# --- credentials scaffold
if [ ! -f .env ]; then
  cp .env.example .env
  say "Created .env — edit it to add the site credentials:"
  say "    WEBVAL_AUTH__USERNAME=..."
  say "    WEBVAL_AUTH__PASSWORD=..."
fi

# --- smoke check
say "Verifying installation"
.venv/bin/webval statuses >/dev/null || fail "CLI smoke check failed"
.venv/bin/pytest -q -m "not integration" >/dev/null 2>&1 && say "Unit tests: PASS" \
  || say "WARNING: unit tests failed — run '.venv/bin/pytest -m \"not integration\"' for details"

cat <<'DONE'

webval is ready. Typical usage:

  source .venv/bin/activate
  webval extract spec1.pdf spec2.pdf        # review the extracted baseline
  webval run spec1.pdf spec2.pdf            # full validation -> runs/<id>/
                                            #   traceability_matrix.xlsx
                                            #   validation_report.html
                                            #   results.json + evidence/

DONE
