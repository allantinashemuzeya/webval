"""Extraction of boxed verification annotations from proof screenshots.

Real-world specification PDFs are often annotated proofs: screenshots of the
site with review callouts drawn on top. After OCR, those callouts follow
recognisable conventions:

    Links to: https://www.us.pluvicto.com/resources/glossary
    GLOBAL Links to: https://us.pluvicto.com/...#mcrpc-hormone-therapy
    Clicking on "About mCRPC" anchor links to mCRPC header below
    Global alt text: mCRPC PSMAfore patient

The parsers here are deliberately tolerant of OCR noise: URLs broken across
lines ("https;// www.us.pluvicto.com/ resources/glossary"), curly/straight
quote confusion, stray box-border characters (|), and interleaved page copy.
They favour precision over recall — a missed annotation surfaces during
baseline review; a hallucinated one pollutes the traceability matrix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tokens that may legitimately appear inside an OCR-fractured URL.
_URL_TOKEN_RE = re.compile(r"^[\w.:/#%?&=;~-]+$")
_URL_START_RE = re.compile(r"(?:https?|www\.)", re.IGNORECASE)

_LINKS_TO_RE = re.compile(r"(?P<global>GLOBAL\s+)?Links?\s+to[:;]\s*", re.IGNORECASE)
_ALT_TEXT_RE = re.compile(r"(?:Global\s+)?alt\s*[- ]?text[:;]\s*(?P<rest>.*)", re.IGNORECASE)
_ANCHOR_RE = re.compile(
    r"Clicking\s+(?:on\s+)?[\"“”'‘’]?(?P<label>[^\"“”'‘’\n|]{2,60}?)[\"“”'‘’]?\s*"
    r"anchor\s+links?\s*\|?\s*to\s+(?P<target>.{3,140}?)(?=\n\s*\n|\|\s*\n|$)",
    re.IGNORECASE | re.DOTALL,
)
# Stop words that signal the annotation box ended and page copy resumed.
_ANNOTATION_STARTS = ("links to", "link to", "alt text", "clicking", "global")


@dataclass
class LinkAnnotation:
    url: str
    is_global: bool


@dataclass
class AnchorAnnotation:
    label: str
    target: str


@dataclass
class AltTextAnnotation:
    alt: str


_STRIP_CHARS = "|(){}[]<>~`'\"“”‘’:;,.!—–-_\\ /"


def _clean(text: str) -> str:
    """Collapse whitespace and drop OCR artifacts of callout-box borders."""
    tokens: list[str] = []
    for token in text.replace("|", " ").split():
        stripped = token.strip(_STRIP_CHARS)
        if not stripped:
            continue  # pure border/punctuation noise
        if len(stripped) == 1 and not stripped.isalnum():
            continue
        tokens.append(token.strip("|()[]{}~`\"“”‘’"))
    return " ".join(tokens).strip(" .;,:")


def _reassemble_url(fragments: str) -> str | None:
    """Join OCR-fractured URL tokens; stop at the first non-URL-ish token."""
    tokens: list[str] = []
    for token in fragments.split():
        stripped = token.strip("|()[]{},")
        if not stripped:
            continue
        if tokens and not _URL_TOKEN_RE.match(stripped):
            break
        if not tokens and not _URL_START_RE.search(stripped):
            break
        tokens.append(stripped)
        # a token ending a sentence closes the URL
        if stripped.endswith((".", ";")) and len(tokens) > 1:
            break
    if not tokens:
        return None
    url = "".join(tokens).rstrip(".,;|")
    url = url.replace(";//", "://").replace(":// ", "://")
    if url.lower().startswith("www."):
        url = "https://" + url
    # OCR sometimes doubles the scheme separator or loses the colon
    url = re.sub(r"^https?[;:]?/{1,2}", lambda m: m.group(0)[:5].rstrip(";:/") + "://", url, flags=re.IGNORECASE)
    if "." not in url.split("://")[-1]:
        return None
    return url


def extract_link_annotations(text: str) -> list[LinkAnnotation]:
    out: list[LinkAnnotation] = []
    seen: set[str] = set()
    for match in _LINKS_TO_RE.finditer(text):
        # URL may continue over the next couple of lines inside the callout box
        window = text[match.end() : match.end() + 200]
        url = _reassemble_url(window)
        if url and url.lower() not in seen:
            seen.add(url.lower())
            out.append(LinkAnnotation(url=url, is_global=bool(match.group("global"))))
    return out


def extract_anchor_annotations(text: str) -> list[AnchorAnnotation]:
    out: list[AnchorAnnotation] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(text):
        label = _clean(match.group("label"))
        target = _clean(match.group("target"))
        # OCR-truncated labels ("About ms") are kept only if plausibly complete
        if len(label) < 3 or label.lower() in seen:
            continue
        seen.add(label.lower())
        out.append(AnchorAnnotation(label=label, target=target))
    return out


def extract_alt_text_annotations(text: str) -> list[AltTextAnnotation]:
    out: list[AltTextAnnotation] = []
    seen: set[str] = set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = _ALT_TEXT_RE.search(line)
        if not match:
            continue
        value = _clean(match.group("rest"))
        # Callout boxes wrap: continue onto short following lines until the
        # box plausibly ends or another annotation starts.
        j = i + 1
        while len(value) < 40 and j < len(lines):
            nxt = _clean(lines[j])
            if not nxt or len(nxt) > 28:
                break
            if any(nxt.lower().startswith(s) for s in _ANNOTATION_STARTS):
                break
            value = f"{value} {nxt}".strip()
            j += 1
        if value and value.lower() not in seen:
            seen.add(value.lower())
            out.append(AltTextAnnotation(alt=value))
    return out
