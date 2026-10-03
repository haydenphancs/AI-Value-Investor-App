"""Shared guard: no real investor's name, catchphrase or trading directive in REPORT persona text.

Imported by `test_persona_no_real_names.py` (the report persona prompts, their structured
fields and every Stage-A / Stage-B prompt built from them) and meant to be imported by the
report-chat voice tests, so the two surfaces share ONE baseline list. A surface may add
stricter items locally on top of it; it never drops one. Precedent for a shared, non-test
helper module in this folder: `tests/_price_fakes.py`.

WRIGHT'S LAW is deliberately NOT a catchphrase here (decided 2026-10-02). It is T. P. Wright's
1936 learning-curve observation — an aeronautical engineer, not an investor — and it is the
Disruption Seeker's method SUBSTANCE (cost declines per cumulative doubling), used in its
report prompt and structured fields. The report-chat voices are short and tone-only, so their
own test may ban it as a voice-local addition (plan-backend-voice §7); that does not make it
a person's saying for the report prompts.

SCOPE — report personas and report voices only. The Learn book voices
(`agents/book_voice_prompt.py`) name their book's author BY DESIGN — there the book is the
subject — so they must never be scanned with this guard.

Why each list exists:

* NAMES / FIRMS — the five research personas were renamed to style names (migration 103) to
  remove right-of-publicity / false-endorsement exposure and App Review 5.2.1. Their system
  prompts still opened "…associated with Warren Buffett" and quoted him, so every report was
  written by a model told it was channelling a named living investor. Superset of
  `test_learn_titles_name_no_real_investor._REAL_INVESTORS` plus the firms a model would reach
  for next.
* CATCHPHRASES — a famous saying is the person's voice even without the name ("know what you
  own", "tenbagger", "a wonderful company at a fair price"). Substring match, case-insensitive.
* DIRECTIVES — persona text that tells the model to time trades or size positions contradicts
  ADVICE_BOUNDARY, which is appended after it. Checked only on the text BEFORE that boundary
  (the boundary itself legitimately says "buy, sell, or hold").
* HOLDER FRAMING — "your purchase price", "you take large positions": the model cast as an
  investor with holdings of its own. Same scope as DIRECTIVES.
"""

from __future__ import annotations

import re
from typing import List, Tuple

# Word-boundary, case-insensitive. Bare "wood" is deliberately absent: it is an ordinary
# noun ("Wood here is a commodity"); "cathie" carries that persona's name.
REAL_INVESTOR_NAMES: Tuple[str, ...] = (
    "warren buffett", "buffett",
    "charlie munger", "munger",
    "benjamin graham", "ben graham", "graham",
    "peter lynch", "lynch",
    "cathie wood", "cathie",
    "bill ackman", "ackman",
    "michael burry", "burry",
    "ray dalio", "dalio",
    "soros", "icahn", "druckenmiller", "klarman",
    "oracle of omaha",
    # The rest of the App Store "Do not use" list (marketing/compliance.APP_STORE_NAMES) and
    # its distinctive surnames, plus the commonest misspelling. Bare "buffet" is an ordinary
    # word, but persona text is code-authored: a false positive costs one rewording, a missed
    # "Buffet" costs a name in every report. "\b" keeps it from matching inside "buffett".
    "warren buffet", "buffet",
    "joel greenblatt", "greenblatt",
    "howard marks",
    "morgan housel", "housel",
    "pabrai", "einhorn", "tepper",
    "philip fisher", "john templeton", "templeton", "john bogle", "bogle",
)

INVESTMENT_FIRMS: Tuple[str, ...] = (
    "berkshire", "ark invest", "ark investment", "pershing square", "scion",
    "magellan", "bridgewater",
)

# Substring, case-insensitive. Each one is a specific investor's saying or a branded framework.
CATCHPHRASES: Tuple[str, ...] = (
    "know what you own", "invest in what you know",
    "tenbagger", "ten-bagger", "ten bagger", "10-bagger",
    "diworsification",
    "two minutes", "two-minute",
    "boring name",
    "smart money",
    "beat wall street", "one up on wall street",
    "wonderful company", "wonderful business", "wonderful price",
    "holding period is forever",
    "circle of competence",
    "mr. market",
    "rule no. 1", "never lose money",
    "fearful when others", "greedy when others", "the market is fearful",
    "price is what you pay", "tide goes out",
    "folksy",
    "innovation is key to growth", "five innovation platforms", "multiomic",
    "free-cash-flow-generative",
    "investor day",
    "the big short",
    "buy for one reason", "for one reason only", "for only one reason",
)

# Regexes, case-insensitive, checked only BEFORE "ADVICE BOUNDARY".
DIRECTIVE_PATTERNS: Tuple[str, ...] = (
    r"\b(?:buy|sell) when\b",
    r"\bsell signals?\b",
    r"\bright time\b",
    r"\bportfolio weight",
    r"\bposition siz",
    r"\bavoid (?:if|when)\b",
)

HOLDER_PATTERNS: Tuple[str, ...] = (
    r"\byour (?:purchase price|best ideas|positions?|holdings?|portfolio)\b",
    r"\byou (?:buy|sell|own|invest|hold)\b",
    r"\byou (?:size|take)\b[^.\n]{0,40}\bpositions?\b",
    r"\bwon't invest\b",
    r"\bideal holding period\b",
    # The model cast as an investor with tastes of its own ("You would rather miss … than
    # overpay", "You distrust narratives", a "YOUR INVESTMENT PHILOSOPHY" heading): the
    # method holds the preference, never the model.
    r"\byou would rather\b",
    r"\byou (?:distrust|respect)\b",
    r"\byour investment (?:philosophy|style|approach|process)\b",
)

_NAME_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(n) for n in REAL_INVESTOR_NAMES + INVESTMENT_FIRMS) + r")\b",
    re.IGNORECASE,
)
_CATCHPHRASE_RE = re.compile("|".join(re.escape(c) for c in CATCHPHRASES), re.IGNORECASE)
_DIRECTIVE_RE = re.compile("|".join(DIRECTIVE_PATTERNS), re.IGNORECASE)
_HOLDER_RE = re.compile("|".join(HOLDER_PATTERNS), re.IGNORECASE)

ADVICE_MARKER = "ADVICE BOUNDARY"


def name_violations(text: str) -> List[str]:
    return [m.group(0) for m in _NAME_RE.finditer(text or "")]


def catchphrase_violations(text: str) -> List[str]:
    return [m.group(0) for m in _CATCHPHRASE_RE.finditer(text or "")]


def violations(text: str) -> List[str]:
    """Every real name, firm or catchphrase in `text` (empty = clean)."""
    return name_violations(text) + catchphrase_violations(text)


def before_advice_boundary(text: str) -> str:
    """The persona's own text: everything before the shared ADVICE_BOUNDARY (whole text if
    the marker is absent, so a prompt that lost its boundary is scanned in full)."""
    text = text or ""
    i = text.find(ADVICE_MARKER)
    return text if i < 0 else text[:i]


def directive_violations(text: str) -> List[str]:
    """Trading-directive and holder-framing hits in the persona's own text."""
    own = before_advice_boundary(text)
    return (
        [m.group(0) for m in _DIRECTIVE_RE.finditer(own)]
        + [m.group(0) for m in _HOLDER_RE.finditer(own)]
    )
