"""Classification rules: map requirement statements to validation categories.

Keyword tables are ordered by specificity — the first matching category wins.
Extend by appending (category, patterns) pairs; no engine change needed.
"""

from __future__ import annotations

import re

from webval.models import RequirementCategory

# Order matters: more specific categories first.
CATEGORY_RULES: list[tuple[RequirementCategory, list[str]]] = [
    (
        RequirementCategory.ANCHOR,
        [r"\banchor\b", r"jump[- ]link", r"scrolls? to", r"in[- ]page (?:link|navigation)", r"#[a-z0-9-]+"],
    ),
    (
        RequirementCategory.DOWNLOAD,
        [r"\bdownload(?:able|s)?\b", r"\bbrochure\b", r"\bpdf (?:file|document|download)\b", r"\bsave the file\b"],
    ),
    (
        RequirementCategory.VIDEO,
        [r"\bvideo\b", r"\bplayback\b", r"\bplayer\b", r"\bplay button\b", r"\bmedia asset\b"],
    ),
    (
        RequirementCategory.ACCESSIBILITY,
        [
            r"\balt[- ]?text\b", r"\baria[- ]", r"\bscreen reader\b", r"\bwcag\b",
            r"\baccessib", r"\brole=", r"\bfocus (?:state|order|visible)\b", r"\bkeyboard\b",
        ],
    ),
    (
        RequirementCategory.METADATA,
        [
            r"\bmeta (?:description|title|tag)", r"\bpage title\b", r"\btitle tag\b", r"\bcanonical\b",
            r"\brobots\b", r"\bopen graph\b", r"\bog:", r"\bstructured data\b", r"\bjson-ld\b", r"\bh1\b",
        ],
    ),
    (
        RequirementCategory.RESPONSIVE,
        [
            r"\bresponsive\b", r"\bmobile\b", r"\btablet\b", r"\bviewport\b", r"\bbreakpoint\b",
            r"\bhamburger\b", r"\biphone\b", r"\bipad\b", r"\bdesktop and mobile\b",
        ],
    ),
    (
        RequirementCategory.UI_BEHAVIOR,
        [
            r"back[- ]to[- ]top", r"\bmodal\b", r"\bpop[- ]?up\b", r"\baccordion\b", r"\bexpand(?:able|s)?\b",
            r"\bcollaps", r"\bcookie (?:banner|consent)\b", r"\bcarousel\b", r"\btoggle\b", r"\bdropdown\b",
            r"\bisi\b", r"\bsticky\b",
        ],
    ),
    (
        RequirementCategory.PERFORMANCE,
        [r"\bload time\b", r"\bperformance\b", r"\blcp\b", r"\bcls\b", r"\bttfb\b", r"\bcore web vitals?\b"],
    ),
    (
        RequirementCategory.IMAGE,
        [r"\bimage\b", r"\bhero (?:image|banner)\b", r"\blogo\b", r"\bicon\b", r"\bgraphic\b", r"\bphoto\b"],
    ),
    (
        RequirementCategory.NAVIGATION,
        [
            r"\bnavigat", r"\bmenu\b", r"\bheader link", r"\bfooter link", r"\bbreadcrumb\b",
            r"\bnav (?:bar|item)\b", r"\bsite ?map\b", r"\btab\b",
        ],
    ),
    (
        RequirementCategory.LINK,
        [
            r"\blinks? to\b", r"\bhyperlink\b", r"\bcta\b", r"\bbutton (?:links?|directs?)\b",
            r"\bexternal (?:link|site)\b", r"\burl\b", r"\bhref\b", r"\bredirect",
            r"\bclick(?:ing)? (?:the |on )?.*(?:link|button)\b",
        ],
    ),
    (
        RequirementCategory.VISUAL,
        [r"\blayout matches\b", r"\bvisual(?:ly)? match", r"\bscreenshot\b", r"\bpixel\b", r"\bdesign comp\b"],
    ),
    (
        RequirementCategory.CONTENT,
        [
            r"\bdisplays?\b", r"\bcopy\b", r"\btext\b", r"\bheadline\b", r"\bheading\b", r"\bparagraph\b",
            r"\bdisclaimer\b", r"\bclaim\b", r"\bfootnote\b", r"\bverbatim\b", r"\bcontent\b", r"\bstates?\b",
            r"\bindication\b", r"\bsafety information\b",
        ],
    ),
]

_COMPILED: list[tuple[RequirementCategory, list[re.Pattern[str]]]] = [
    (cat, [re.compile(p, re.IGNORECASE) for p in patterns]) for cat, patterns in CATEGORY_RULES
]

# Sentences containing these modal constructs are treated as testable statements.
MODAL_RE = re.compile(
    r"\b(?:shall|must|should|will|is required to|needs? to|has to|are required to)\b",
    re.IGNORECASE,
)

# Table header synonyms for structured requirement tables in specs.
TABLE_HEADER_SYNONYMS: dict[str, set[str]] = {
    "id": {"id", "req id", "req. id", "requirement id", "ref", "ref#", "no", "no.", "#"},
    "requirement": {"requirement", "description", "requirement description", "business requirement", "spec"},
    "expected": {"expected", "expected result", "expected behaviour", "expected behavior", "acceptance criteria"},
    "category": {"category", "type", "area", "module", "section"},
    "priority": {"priority", "moscow", "must/should"},
    "url": {"url", "page", "page url", "location"},
}


def classify(text: str) -> RequirementCategory:
    """Return the first matching category for a requirement statement."""
    for category, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            return category
    return RequirementCategory.GENERAL


def match_header(header: str) -> str | None:
    """Map a raw table header cell onto a canonical column name."""
    norm = " ".join(header.lower().split()).strip(" :")
    for canonical, synonyms in TABLE_HEADER_SYNONYMS.items():
        if norm in synonyms:
            return canonical
    return None
