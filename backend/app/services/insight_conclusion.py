"""Checks on the Insights card's CONCLUSION — pure, stdlib only, linear-time.

WHY THIS EXISTS — TestFlight, ETHUSD, 2026-09-10. The card's ↳ conclusion read "A
proposed $5,000 dividend could boost ETH if Republicans control Congress" under three
points about ETF flows, quantum risk and a 2030 price forecast. It was not a
conclusion at all: it was a fourth, unrelated story that happened to be last. The
model now writes the conclusion as its own field, told to build it ONLY from its
points — and these checks are how the service knows whether it did:

* ``unsupported_figures`` — a money / percent / scaled figure in the conclusion that
  no point, the headline, or the shown catalyst carries. The ETH "$5,000". This is
  the one HARD check: a new figure is a new fact.
* ``opens_with_people_framing`` — "Investors should…", "For investors, …", "You…".
  The user asked for the point itself, not a sentence about who should care.
* ``novelty_flags`` — an event word (dividend, merger, lawsuit, …) or a proper noun
  none of the points mention, and relative-day words (the card is read hours later).
* ``duplicates_a_point`` — a restated point is not a synthesis.
* ``stale_timing_claims`` — "set to report", "upcoming earnings", "ahead of its
  report", or a kept pre-report prediction ("options are pricing in an 11% swing")
  once the calendar says the report HAS happened (ORCL, the same evening).

Everything except figures only asks for ONE repair; the service never rejects a card
for style (the previous card is never better than a slightly imperfect new one).

A number is a FIGURE only when it carries a currency sign, a scale word, a percent or
basis-point unit, or an ``x`` multiple — so "Q1", "FY2027", "S&P 500", "10-year",
"3M" and years are never figures. Comparison is by absolute value within a kind
(pct / amount / multiple), with a tolerance of half a unit of the last digit, or 10%
after a hedge word ("about $600 billion" matches "$638 billion").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

# ── figures ────────────────────────────────────────────────────────────────

_SCALE = {
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
}
_WORD_SCALES = r"trillion|billion|million|thousand"
_LETTER_SCALES = r"tn|bn|mn|t|b|m|k"
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_CUR = r"[$€£¥]"
# "US$5 billion" is dollars: the prefix is consumed so the lookbehind still sees a boundary.
_CUR_PREFIX = r"(?:US|U\.S\.)?"
_DASH = r"(?:-|–|—|to)"
_HEDGE = re.compile(
    r"(?:about|around|roughly|nearly|almost|approximately|approx\.?|some|over|under|"
    r"more than|less than|close to|up to|above|below|at least|north of|south of|"
    r"topping|exceeding|just over|just under|~)\s*$",
    re.IGNORECASE,
)

# Ranges first (both ends share the currency / scale / unit), then single figures.
_MONEY_RANGE = re.compile(
    rf"(?<![\w.]){_CUR_PREFIX}({_CUR})\s?({_NUM})\s?{_DASH}\s?(?:{_CUR})?\s?({_NUM})"
    rf"(?:\s*({_WORD_SCALES})\b|({_LETTER_SCALES})\b)?",
    re.IGNORECASE,
)
_PCT_RANGE = re.compile(
    rf"(?<![\w.])({_NUM})\s?%?\s?{_DASH}\s?({_NUM})\s?(%|percent\b|per cent\b)",
    re.IGNORECASE,
)
_MONEY = re.compile(
    rf"(?<![\w.]){_CUR_PREFIX}({_CUR})\s?({_NUM})(?:\s*({_WORD_SCALES})\b|({_LETTER_SCALES})\b)?",
    re.IGNORECASE,
)
_PCT = re.compile(
    rf"(?<![\w.])({_NUM})\s?(%|percent\b|per cent\b|pct\b|percentage points?\b)",
    re.IGNORECASE,
)
_BPS = re.compile(rf"(?<![\w.])({_NUM})\s?(bps|basis points?)\b", re.IGNORECASE)
_SCALED_COUNT = re.compile(rf"(?<![\w.$€£¥])({_NUM})\s+({_WORD_SCALES})\b", re.IGNORECASE)
_DOLLARS = re.compile(rf"(?<![\w.])({_NUM})\s?(dollars|usd)\b", re.IGNORECASE)
_CENTS = re.compile(rf"(?<![\w.])({_NUM})\s?(cents?)\b", re.IGNORECASE)
_MULTIPLE = re.compile(rf"(?<![\w.])({_NUM})\s?(?:[x×]|times)(?![\w])", re.IGNORECASE)


@dataclass(frozen=True)
class Figure:
    kind: str          # "pct" | "amount" | "multiple"
    currency: str      # "" unless a currency sign / "dollars" was written
    value: float       # absolute value, scale applied
    tol: float
    raw: str


def _to_float(num: str) -> float:
    return float(num.replace(",", ""))


def _unit_tol(num: str) -> float:
    """Half a unit of the last written digit: '638' → 0.5, '0.64' → 0.005."""
    clean = num.replace(",", "")
    if "." in clean:
        return 0.5 * 10 ** (-len(clean.split(".", 1)[1]))
    return 0.5


def _scale_of(word: Optional[str], letter: Optional[str]) -> float:
    key = (word or letter or "").lower()
    return _SCALE.get(key, 1.0)


def _hedged(text: str, start: int) -> bool:
    return bool(_HEDGE.search(text[max(0, start - 16):start]))


def _fig(kind, currency, num, scale, raw, text, start, *, divisor=1.0) -> Figure:
    value = abs(_to_float(num)) * scale / divisor
    tol = _unit_tol(num) * scale / divisor
    if _hedged(text, start):
        tol = max(tol, 0.10 * value)
    return Figure(kind, currency, value, tol, raw)


def extract_figures(text: str) -> List[Figure]:
    """Every figure in ``text``. Ranges contribute both ends."""
    text = text or ""
    out: List[Figure] = []
    taken: List[Tuple[int, int]] = []

    def _free(m) -> bool:
        return all(m.end() <= a or m.start() >= b for a, b in taken)

    def _take(m) -> None:
        taken.append((m.start(), m.end()))

    for m in _MONEY_RANGE.finditer(text):
        cur, lo, hi, word, letter = m.groups()
        scale = _scale_of(word, letter)
        out.append(_fig("amount", cur, lo, scale, m.group(0), text, m.start()))
        out.append(_fig("amount", cur, hi, scale, m.group(0), text, m.start()))
        _take(m)
    for m in _PCT_RANGE.finditer(text):
        if not _free(m):
            continue
        lo, hi, _unit = m.groups()
        out.append(_fig("pct", "", lo, 1.0, m.group(0), text, m.start()))
        out.append(_fig("pct", "", hi, 1.0, m.group(0), text, m.start()))
        _take(m)
    for m in _MONEY.finditer(text):
        if not _free(m):
            continue
        cur, num, word, letter = m.groups()
        out.append(_fig("amount", cur, num, _scale_of(word, letter), m.group(0), text, m.start()))
        _take(m)
    for m in _BPS.finditer(text):
        if not _free(m):
            continue
        out.append(_fig("pct", "", m.group(1), 1.0, m.group(0), text, m.start(), divisor=100.0))
        _take(m)
    for m in _PCT.finditer(text):
        if not _free(m):
            continue
        out.append(_fig("pct", "", m.group(1), 1.0, m.group(0), text, m.start()))
        _take(m)
    for m in _SCALED_COUNT.finditer(text):
        if not _free(m):
            continue
        out.append(_fig("amount", "", m.group(1), _scale_of(m.group(2), None), m.group(0), text, m.start()))
        _take(m)
    for m in _DOLLARS.finditer(text):
        if not _free(m):
            continue
        out.append(_fig("amount", "$", m.group(1), 1.0, m.group(0), text, m.start()))
        _take(m)
    for m in _CENTS.finditer(text):
        if not _free(m):
            continue
        out.append(_fig("amount", "$", m.group(1), 1.0, m.group(0), text, m.start(), divisor=100.0))
        _take(m)
    for m in _MULTIPLE.finditer(text):
        if not _free(m):
            continue
        out.append(_fig("multiple", "", m.group(1), 1.0, m.group(0), text, m.start()))
        _take(m)
    return out


def _supported(fig: Figure, sources: Sequence[Figure]) -> bool:
    for src in sources:
        if src.kind != fig.kind:
            continue
        if fig.currency and src.currency and fig.currency != src.currency:
            continue
        if abs(fig.value - src.value) <= max(fig.tol, src.tol):
            return True
    return False


def pct_figure(value: object) -> Optional[Figure]:
    """A trusted percent (a quote's change, the catalyst's move) as an allowed figure."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return Figure("pct", "", abs(float(value)), 0.05, f"{value}%")


def unsupported_figures(
    conclusion: str,
    sources: Iterable[str],
    extra: Iterable[Optional[Figure]] = (),
) -> List[str]:
    """Raw text of each conclusion figure no source carries (deduplicated, in order)."""
    allowed: List[Figure] = []
    for text in sources:
        allowed.extend(extract_figures(text or ""))
    allowed.extend(f for f in extra if f is not None)
    missing: List[str] = []
    for fig in extract_figures(conclusion):
        if not _supported(fig, allowed) and fig.raw not in missing:
            missing.append(fig.raw)
    return missing


# ── framing, novelty, duplication, timing ─────────────────────────────────

_PEOPLE = r"(?:investors|shareholders|stockholders|holders|traders|owners|buyers)"
_FRAMING = [
    re.compile(rf"^(?:for\s+)?(?:[\w'-]+\s+)?{_PEOPLE}\b", re.IGNORECASE),
    re.compile(
        r"^investor\s+(?:should|must|may|might|could|would|need|needs|will|can)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:if\s+)?you(?:r)?\b", re.IGNORECASE),
    re.compile(r"^(?:anyone|those)\s+(?:holding|who)\b", re.IGNORECASE),
    re.compile(r"^(?:this|it)\s+matters\b", re.IGNORECASE),
    re.compile(r"^why\s+(?:it|this)\s+matters\b", re.IGNORECASE),
]


def opens_with_people_framing(text: str) -> bool:
    """Does the conclusion open by addressing people instead of making the point?"""
    t = (text or "").lstrip(" \"'“‘")
    return any(p.match(t) for p in _FRAMING)


_EVENT_WORDS = re.compile(
    r"\b(dividends?|buybacks?|repurchas\w*|mergers?|acqui\w*|lawsuits?|sued|probes?|"
    r"investigat\w*|subpoena\w*|recall\w*|downgrad\w*|upgrad\w*|guidance|layoffs?|"
    r"ipos?|halving|hack\w*|exploit\w*|tariffs?|bankrupt\w*|delist\w*|offerings?|"
    r"dilut\w*|spin-?offs?|partnerships?|contracts?|approv\w*|antitrust|"
    r"stimulus|election\w*|midterms?)\b",
    re.IGNORECASE,
)
_RELATIVE_DAY = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|this (?:morning|afternoon|evening)|later today)\b",
    re.IGNORECASE,
)
_PROPER = re.compile(r"\b[A-Z][a-z][A-Za-z'’-]{1,}\b")
_CALENDAR_WORDS = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
})


def _stem(word: str) -> str:
    return word.lower()[:6]


def novelty_flags(
    conclusion: str,
    allowed_text: str,
    subject_terms: Iterable[str] = (),
) -> List[str]:
    """Event words / proper nouns in the conclusion that the allowed text never
    mentions, plus relative-day words. Each flag is a short human-readable reason."""
    flags: List[str] = []
    allowed = (allowed_text or "").lower()
    for m in _EVENT_WORDS.finditer(conclusion or ""):
        word = m.group(0)
        if _stem(word) not in allowed and f'new event "{word}"' not in flags:
            flags.append(f'new event "{word}"')
    subjects = {s.lower() for s in subject_terms if s}
    text = conclusion or ""
    first = re.match(r"\s*\S+", text)
    first_end = first.end() if first else 0
    for m in _PROPER.finditer(text):
        if m.start() < first_end:
            continue            # sentence-initial capital is grammar, not a name
        word = m.group(0).rstrip("'’")
        base = re.sub(r"['’]s$", "", word)
        low = base.lower()
        if low in _CALENDAR_WORDS or low in subjects:
            continue
        if low in allowed:
            continue
        flag = f'new name "{base}"'
        if flag not in flags:
            flags.append(flag)
    for m in _RELATIVE_DAY.finditer(text):
        flag = f'relative day "{m.group(0)}"'
        if flag not in flags:
            flags.append(flag)
    return flags


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "as", "at", "by", "is", "are", "was", "were", "be", "its", "it", "this", "that",
    "from", "into", "than", "their", "has", "have", "will", "could", "may",
})


def _content(text: str) -> set:
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP}


def duplicates_a_point(conclusion: str, points: Sequence[str]) -> bool:
    """A conclusion whose content words overlap one point by Jaccard >= 0.8."""
    c = _content(conclusion)
    if not c:
        return False
    for p in points:
        pc = _content(p)
        if pc and len(c & pc) / len(c | pc) >= 0.8:
            return True
    return False


_REPORT_OBJECT = (
    r"(?:its\s+|the\s+|their\s+)?"
    r"(?:(?:q[1-4]|fiscal|quarterly|first-quarter|second-quarter|third-quarter|"
    r"fourth-quarter|full-year)\s+)*(?:results|earnings|numbers)\b"
)
_STALE_TIMING = [
    # A bare "set / scheduled / due / slated to report" is the report sense by itself.
    re.compile(r"\b(?:set|scheduled|due|slated)\s+to\s+report\b(?!-)", re.IGNORECASE),
    # Other verbs only with an earnings OBJECT: "poised to post its best day",
    # "expected to post 40% cloud growth", "due to post-earnings profit-taking" and
    # "set to announce new data-center deals" are honest after a report.
    re.compile(
        r"\b(?:set|scheduled|expected|due|poised|slated|preparing|gearing\s+up)\s+to\s+"
        r"(?:report|release|announce|post)\s+" + _REPORT_OBJECT,
        re.IGNORECASE,
    ),
    re.compile(
        r"\bupcoming\s+(?:q[1-4]\s+|fiscal\s+|quarterly\s+|first-quarter\s+|"
        r"second-quarter\s+|third-quarter\s+|fourth-quarter\s+)?"
        r"(?:earnings|results|report)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bahead\s+of\s+(?:its|the|their)\s+(?:q[1-4]\s+|quarterly\s+|fiscal\s+|"
        r"upcoming\s+)?(?:earnings|results|report)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:heads?|heading|going)\s+into\s+(?:its\s+|the\s+)?earnings\b", re.IGNORECASE),
    # A pre-report PREDICTION kept after the results: "options markets are pricing in
    # an 11% swing". Seen in 2 of 3 replays of ORCL on 2026-09-10 after the release,
    # despite the prompt rule — so it is checked, not just asked for.
    re.compile(
        r"\boptions?(?:\s+[\w-]+){0,4}?\s+(?:pric\w*|signal\w*|impl\w*|predict\w*|"
        r"expect\w*|anticipat\w*|bet\w*)(?:\s+[\w%-]+){0,8}?\s+(?:move|swing)s?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bimplied\s+(?:move|swing)s?\b", re.IGNORECASE),
]
# "next" exempts a match only when it belongs to THAT claim — inside the matched words
# or as the very next word ("set to report next quarter"; "ahead of its next report"
# never matches at all). A sentence-wide check let "set to report Q1 results as
# investors weigh next year's capex" through.
_NEXT_FOLLOWS = re.compile(r"\s+(?:its\s+|the\s+|their\s+)?next\b", re.IGNORECASE)
_NEXT = re.compile(r"\bnext\b", re.IGNORECASE)


def stale_timing_claims(text: str) -> List[str]:
    """Phrases that frame a report as still ahead, or keep a pre-report prediction.

    A match is exempt only when "next" is part of that claim (inside it, or the word
    right after it: "set to report next quarter"). Overlapping matches report once.
    """
    found: List[str] = []
    spans: List[Tuple[int, int]] = []
    text = text or ""
    for pat in _STALE_TIMING:
        for m in pat.finditer(text):
            if _NEXT.search(m.group(0)) or _NEXT_FOLLOWS.match(text, m.end()):
                continue
            if any(m.start() < b and a < m.end() for a, b in spans):
                continue
            spans.append((m.start(), m.end()))
            if m.group(0) not in found:
                found.append(m.group(0))
    return found


# ── the combined verdict ───────────────────────────────────────────────────

@dataclass
class ConclusionCheck:
    figures: List[str] = field(default_factory=list)
    framing: bool = False
    novelty: List[str] = field(default_factory=list)
    duplicate: bool = False
    timing: List[str] = field(default_factory=list)

    @property
    def hard(self) -> bool:
        """A new figure in the conclusion — a new fact."""
        return bool(self.figures)

    @property
    def soft(self) -> bool:
        return self.framing or bool(self.novelty) or self.duplicate

    @property
    def clean(self) -> bool:
        return not (self.hard or self.soft or self.timing)

    def reasons(self) -> List[str]:
        out = [f'it introduced {f!r}, which none of the points state' for f in self.figures]
        if self.framing:
            out.append("it opened by addressing people (investors / you) instead of making the point")
        out.extend(f"it added a {n} that none of the points mention" for n in self.novelty)
        if self.duplicate:
            out.append("it restated one of the points instead of drawing them together")
        out.extend(
            f'it treated the completed report as still ahead, or kept a pre-report '
            f'prediction ("{t}")' for t in self.timing
        )
        return out


def check_conclusion(
    conclusion: str,
    points: Sequence[str],
    headline: str,
    *,
    catalyst_line: str = "",
    extra_figures: Iterable[Optional[Figure]] = (),
    subject_terms: Iterable[str] = (),
    report_happened: bool = False,
) -> ConclusionCheck:
    """Everything the service needs to decide: accept, repair once, or reject."""
    sources = [headline or "", *points, catalyst_line or ""]
    allowed_text = " ".join(sources)
    timing: List[str] = []
    if report_happened:
        for text in (headline, *points, conclusion):
            for t in stale_timing_claims(text or ""):
                if t not in timing:
                    timing.append(t)
    return ConclusionCheck(
        figures=unsupported_figures(conclusion, sources, extra_figures),
        framing=opens_with_people_framing(conclusion),
        novelty=novelty_flags(conclusion, allowed_text, subject_terms),
        duplicate=duplicates_a_point(conclusion, points),
        timing=timing,
    )


def repair_note(check: ConclusionCheck) -> str:
    """Why the previous conclusion was rejected — names only the offending tokens,
    never article text."""
    reasons = check.reasons()
    if not reasons:
        return ""
    return "The previous conclusion was rejected: " + "; ".join(reasons) + "."
