"""Text normalization and hashing helpers.

Normalization makes PDF-sourced copy comparable with DOM-sourced copy:
whitespace runs, line breaks, unicode punctuation variants (curly quotes,
en/em dashes, non-breaking spaces, ellipsis) all collapse to a canonical form.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path

_PUNCT_MAP = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        " ": " ",
        " ": " ",
        " ": " ",
        "…": "...",
        "®": "",  # ®
        "™": "",  # ™
        "©": "",  # ©
    }
)

_WS_RE = re.compile(r"\s+")
_SOFT_HYPHEN = "­"


def normalize_text(text: str, *, casefold: bool = True) -> str:
    """Canonicalize text for comparison across PDF and DOM sources."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(_SOFT_HYPHEN, "").translate(_PUNCT_MAP)
    text = _WS_RE.sub(" ", text).strip()
    return text.casefold() if casefold else text


def fuzzy_ratio(a: str, b: str) -> float:
    """Similarity ratio (0..1) between two already-normalized strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def contains_normalized(haystack: str, needle: str) -> bool:
    """True if normalized needle occurs in normalized haystack."""
    return normalize_text(needle) in normalize_text(haystack)


def best_window_ratio(haystack: str, needle: str) -> float:
    """Best fuzzy match of ``needle`` against sliding windows of ``haystack``.

    Uses word-token windows sized to the needle so a long page can't dilute
    the score of a short required sentence.
    """
    hay = normalize_text(haystack)
    ndl = normalize_text(needle)
    if not ndl:
        return 0.0
    if ndl in hay:
        return 1.0
    hay_words = hay.split(" ")
    ndl_len = len(ndl.split(" "))
    window = max(ndl_len, 3)
    best = 0.0
    step = max(1, window // 3)
    for i in range(0, max(1, len(hay_words) - window + 1), step):
        chunk = " ".join(hay_words[i : i + window])
        best = max(best, fuzzy_ratio(chunk, ndl))
        if best > 0.995:
            break
    return best


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def slugify(value: str, max_length: int = 60) -> str:
    """Filesystem-safe slug for evidence file names."""
    value = normalize_text(value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:max_length] or "item"
