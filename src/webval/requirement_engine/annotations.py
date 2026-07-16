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
# Qualified on purpose ("page title", not bare "title") — page copy often
# contains the bare words and a hallucinated requirement pollutes the matrix.
_TITLE_RE = re.compile(r"(?:page|browser)\s+title(?:\s+tag)?[:;]\s*(?P<rest>.*)", re.IGNORECASE)
_DESCRIPTION_RE = re.compile(r"(?:meta|page)\s+description[:;]\s*(?P<rest>.*)", re.IGNORECASE)
# Pharma approval code + date ("US-PLU-2300123 06/26", "273175 6/23", either
# order). These change with content updates, which is exactly what QA checks.
_CODE_DATE_RES = (
    re.compile(
        r"\b(?P<code>(?:[A-Z]{1,5}(?:-[A-Z0-9]{2,12}){1,5})|\d{6,8})"
        r"\s+(?P<date>(?:0?[1-9]|1[0-2])/(?:\d{4}|\d{2}))\b"
    ),
    re.compile(
        r"\b(?P<date>(?:0?[1-9]|1[0-2])/(?:\d{4}|\d{2}))"
        r"\s+(?P<code>(?:[A-Z]{1,5}(?:-[A-Z0-9]{2,12}){1,5})|\d{6,8})\b"
    ),
)
# Stop words that signal the annotation box ended and page copy resumed.
_ANNOTATION_STARTS = ("links to", "link to", "alt text", "clicking", "global", "page title", "meta description")


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


@dataclass
class MetadataAnnotation:
    field: str  # "title" | "description"
    value: str


@dataclass
class CodeAnnotation:
    text: str  # code + date exactly as annotated, e.g. "US-PLU-2300123 06/26"


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


def _continue_wrapped(lines: list[str], start: int, value: str, max_total: int = 40, max_line: int = 28) -> str:
    """Callout boxes wrap: continue onto short following lines until the box
    plausibly ends or another annotation starts."""
    j = start
    while len(value) < max_total and j < len(lines):
        nxt = _clean(lines[j])
        if not nxt or len(nxt) > max_line:
            break
        if any(nxt.lower().startswith(s) for s in _ANNOTATION_STARTS):
            break
        value = f"{value} {nxt}".strip()
        j += 1
    return value


def extract_alt_text_annotations(text: str) -> list[AltTextAnnotation]:
    out: list[AltTextAnnotation] = []
    seen: set[str] = set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = _ALT_TEXT_RE.search(line)
        if not match:
            continue
        value = _continue_wrapped(lines, i + 1, _clean(match.group("rest")))
        if value and value.lower() not in seen:
            seen.add(value.lower())
            out.append(AltTextAnnotation(alt=value))
    return out


def extract_metadata_annotations(text: str) -> list[MetadataAnnotation]:
    """``Page title: ...`` / ``Meta description: ...`` callouts."""
    out: list[MetadataAnnotation] = []
    seen: set[str] = set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for field, pattern in (("title", _TITLE_RE), ("description", _DESCRIPTION_RE)):
            match = pattern.search(line)
            if not match:
                continue
            # Titles/descriptions run longer than alt text — allow wider wraps.
            value = _continue_wrapped(lines, i + 1, _clean(match.group("rest")), max_total=90, max_line=48)
            key = f"{field}:{value.lower()}"
            if len(value) >= 4 and key not in seen:
                seen.add(key)
                out.append(MetadataAnnotation(field=field, value=value))
    return out


def extract_code_annotations(text: str) -> list[CodeAnnotation]:
    """Approval code + date pairs highlighted on the proof."""
    out: list[CodeAnnotation] = []
    seen: set[str] = set()
    for pattern in _CODE_DATE_RES:
        for match in pattern.finditer(text):
            snippet = _clean(match.group(0))
            if snippet.lower() not in seen:
                seen.add(snippet.lower())
                out.append(CodeAnnotation(text=snippet))
    return out
