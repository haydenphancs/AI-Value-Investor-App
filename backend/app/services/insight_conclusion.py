"""Checks on the Insights card's CONCLUSION — pure, stdlib only, linear-time.

WHY THIS EXISTS — TestFlight, ETHUSD, 2026-09-10. The card's ↳ conclusion read "A
proposed $5,000 dividend could boost ETH if Republicans control Congress" under three
points about ETF flows, quantum risk and a 2030 price forecast. It was not a
conclusion at all: it was a fourth, unrelated story that happened to be last. The
model now writes the conclusion as its own field, told to build it ONLY from its
points — and these checks are how the service knows whether it did:

* ``unsupported_figures`` — a money / percent / scaled figure in the conclusion that
  no point or the headline carries. The ETH "$5,000". This is
  the one HARD check: a new figure is a new fact.
* ``opens_with_people_framing`` — "Investors should…", "For investors, …", "You…".
  The user asked for the point itself, not a sentence about who should care.
* ``novelty_flags`` — an event word (dividend, merger, lawsuit, …) or a proper noun
  none of the points mention, and relative-day words (the card is read hours later).
* ``duplicates_a_point`` — a restated point is not a synthesis.
* ``stale_timing_claims`` — "set to report", "upcoming earnings", "ahead of its
  report", or a kept pre-report prediction ("options are pricing in an 11% swing")
  once the calendar says the report HAS happened (ORCL, the same evening).
* ``price_claims`` — a share / coin price move or level ("CoreWeave shares experienced a
  2.2% slip", "countered by recent price declines", "Bitcoin dipped below $60,000") on a
  ticker card, whose live chip already shows the price (CRWV, 2026-10-06). Run on every
  field, not only the conclusion; the service decides what a hit costs.

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
from functools import lru_cache
from itertools import islice
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
    """A trusted percent (a quote's change) as an allowed figure."""
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


# ── price claims (ticker cards only) ─────────────────────────────────────────
#
# WHY — TestFlight CRWV, Tue 2026-10-06: the chip read +6.3% while the Insights card said
# "CoreWeave shares experienced a 2.2% slip" and concluded "…countered by recent price
# declines": an older article's move, copied into text written that same session. Owner,
# 2026-10-09: a ticker card states NO share / coin price move and NO price level; market
# cap as a size, valuation multiples and analyst price targets stay. The live chip beside
# the card shows the price.
#
# Built test-first against three review rounds (tests/test_insight_price_claims.py). A
# naive detector flagged 45 of 240 honest lines ("Earnings per share rose 18%", "Bitcoin
# ETF inflows rose 20%", tickers that are words: NET, ON, HAS); a narrow one missed 98 of
# 117 real claims ("shares pulled back", "CoreWeave Rallies on …"); round 3 found 54 more
# false positives ("Visa topped $3 in quarterly earnings per share", "a 10% pullback by
# advertisers", "the yen lost 10% of its value") and 180 more misses (present-tense
# headlines, ¥ / HK$ levels, "The price of Bitcoin fell"); round 4 found 62 more false
# positives (Form 4 values "sold shares worth $850,000", share pools, "Apple Moves Up
# iPhone Fold Launch", rankings, "returned 34% on equity", "Prime Day was Amazon's best day",
# "Copper climbed 3% in London trading", the Bitcoin halving, airdrops, private rounds) and
# 71 more misses (soft verbs after a symbol, "Why Is CoreWeave Stock Down Today?", ATH,
# "six figures", "the TRUMP token"). Hence: subject discipline (no
# bare singular "share"), closed verb lists, bounded gaps that never cross a clause
# joiner, positional "in <something else>" guards, unnamed move nouns only at a sentence
# start, and the card's own names only in the subject position. Every pattern is linear:
# bounded lazy gaps, no nested unbounded quantifiers, numbers anchored to the start of a
# digit run, the verb lists compiled as prefix tries behind a first-word guard, and the
# subject look-behinds behind a one-character guard.

_I = re.IGNORECASE

# Atoms. A number starts only at the START of a digit run (the look-behind), so a long
# run of digits is scanned once, never re-tried from each of its positions.
_PC_NUM = r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d|[.,]\d)"
_PC_WORDNUM = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|"
               r"twenty|thirty|forty|fifty)")
_PC_PCT = (rf"(?:{_PC_NUM}\s?(?:%|percent\b|per\s+cent\b|pct\b)|"
           rf"\b{_PC_WORDNUM}\s+(?:percent|per\s+cent)\b)")
_PC_SIGNED = rf"(?<![\w.])[+−-]\s?{_PC_NUM}\s?%"
_PC_FOLD = (rf"(?:(?:two|three|four|five|six|seven|eight|nine|ten)fold|(?:\d+|{_PC_WORDNUM})-fold)"
            r"(?![\w-])")
# Currencies: $ € £ ¥ ₹ ₩ and the dollar prefixes (HK$, US$, A$, C$, S$, NZ$, NT$, R$).
_PC_CUR = r"(?:(?<![\w$])(?-i:US|HK|NZ|NT|A|C|S|R)\$|[$€£¥₹₩])"
_PC_PER_UNIT = (r"(?:month|year|week|day|hour|unit|user|seat|subscriber|member|ton|tonne|barrel|"
                r"ounce|gallon|pound|night|ride|transaction|transfer|token|query|license|kwh|mwh|"
                r"gb|tb|item|person|head|vehicle|car|device|kilogram|kg|mmbtu|bushel|room|"
                r"ticket|order|household|customer|employee|account|swap|trade|mint|call|message|"
                r"block|operation|bridge|deposit|withdrawal|payment|request|stream|game|click)")
# A fundamental an amount can be "in" ("$3 in quarterly earnings per share").
_PC_FUND_NOUN = (r"(?:revenues?|sales|bookings|backlog|orders|funding|debt|earnings|eps|profits?|"
                 r"income|cash|dividends?|payouts?|buybacks?|repurchases?|fees|deposits|assets|"
                 r"loans|capex|spending|costs?|expenses?|losses|contracts?|financing|proceeds|"
                 r"savings|compensation|pay|bonus(?:es)?|charges?|fines?|penalt(?:y|ies)|"
                 r"damages|free\s+cash\s+flow|ebitda|net\s+income|prices?|ASPs?)")
# A price LEVEL: a currency amount with no scale word ("$145", "$60K", "¥2,800"; never
# "$2 billion"), not a per-unit product price ("$17.99 a month", "$2/hour", "$799 iPhone"),
# not a fundamental ("$3 in quarterly earnings per share", "$1.47 a share in adjusted
# earnings") and not a forecast horizon ("$200,000 by 2026" is a target, which is allowed).
# "$130 a share" IS a level: a share's price is the price.
_PC_LEVEL = (
    rf"{_PC_CUR}\s?{_PC_NUM}(?:\s?[kK]\b)?"
    r"(?!\s*(?:billion|million|trillion|thousand|bn|mn|tn|[bmt])\b)"
    rf"(?!\s*(?:/|(?:a|an|per)\s+{_PC_PER_UNIT}\b))"
    r"(?!\s+(?:of|worth|each|apiece)\b)"
    rf"(?!\s+(?:(?:a|per)\s+share\s+)?(?:in|for)\s+(?:[\w-]+\s+){{0,3}}?{_PC_FUND_NOUN}\b)"
    r"(?!\s+(?:a|per)\s+share\s+(?:in|of|for)\b)"
    r"(?!\s+(?:in|under|per|from)\s+(?:the\s+|its\s+|their\s+|a\s+)?(?:IPO|offering|listing|"
    r"placement|deal|offer|agreement|merger|terms|acquisition|buyout|tender|takeover)\b)"
    r"(?!\s+(?:by\s+(?:20\d\d|year-end|the\s+end)|within\s|over\s+the\s+next|next\s+year)\b)"
    # a product, a plan or a threshold, with at most one hyphenated modifier ("$30,000 EVs",
    # "a $25 free-shipping threshold")
    r"(?!\s+(?:[\w]+-[\w-]+\s+)?(?:price\s+(?:points?|tags?|targets?)|targets?|plans?|tiers?|"
    r"models?|versions?|devices?|phones?|iphones?|cars?|vehicles?|EVs?|SUVs?|trucks?|homes?|"
    r"houses?|laptops?|PCs?|chips?|GPUs?|tablets?|watch(?:es)?|headsets?|consoles?|bikes?|"
    r"cameras?|TVs?|subscriptions?|memberships?|fees?|tickets?|items?|products?|vouchers?|"
    r"checks?|cheques?|credits?|rebates?|minimums?|wages?|fines?|penalt(?:y|ies)|bills?|"
    r"settlements?|stores?|plants?|factor(?:y|ies)|offices?|thresholds?|caps?|limits?|"
    r"baskets?|budgets?)\b)"
)
_PC_CENTS = (
    rf"(?:{_PC_NUM}|one)\s+cents?\b"
    rf"(?!\s*(?:/|(?:a|an|per)\s+(?:share|{_PC_PER_UNIT})\b))(?!\s+(?:of|in|for|each)\b)"
)
_PC_LEVEL_ANY = (rf"(?:{_PC_LEVEL}|{_PC_CENTS}|(?:five|six|seven)\s+figures\b|"
                 # "trading in the $4,000s", "in the low $90s"
                 rf"in\s+the\s+(?:(?:low|mid|high|upper|lower)[- ])?{_PC_CUR}\s?\d[\d,]*(?-i:s)\b)")
_PC_MONTHS = (r"(?:January|February|March|April|May|June|July|August|September|October|"
              r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)")
_PC_HL_NOT = (
    # "of" only before a share-price level ("its June peak of $187"; never "a record high of
    # $5 billion in revenue")
    rf"(?!\s+(?:of(?!\s+(?:about\s+|around\s+|nearly\s+|roughly\s+)?{_PC_LEVEL})|in|for)\b)"
    # "hit an all-time high with 497,099 deliveries": a count's record
    rf"(?!\s+with\s+(?:about\s+|nearly\s+|more\s+than\s+|over\s+|almost\s+|roughly\s+)?(?:\d|{_PC_CUR}))"
    r"(?!\s+(?:revenue|sales|bookings|backlog|margins?|earnings|profits?|deliveries|volumes?|"
    r"demand|output|production|levels?|usage|attendance|traffic|inflows?|outflows?|deposits|"
    r"users|subscribers|orders|shipments|capacity|hashrate|hash\s+rate|difficulty|fees|"
    r"yields?|rates?|prices?|valuation|quarters?|results|numbers|figures|count|share)\b)"
)
# A high / low LEVEL: "a record high", "its June peak", "decade lows", "its highest since
# July", "its previous record of $124,000", "its 200-day moving average".
_PC_HIGHLOW = (
    r"(?:"
    r"(?:(?:new|fresh)\s+)?"
    r"(?:record|all-time|52-week|lifetime|multi-?(?:day|week|month|year|decade)|decade|"
    rf"(?:\d+|{_PC_WORDNUM})-(?:day|week|month|year|decade)|new|fresh|{_PC_MONTHS}|"
    r"(?:19|20)\d\d|IPO|post-IPO|pre-IPO|pandemic|cycle)\s+(?:highs?|lows?|peaks?|bottoms?)\b"
    + _PC_HL_NOT
    + r"|record\s+(?:close|closing\s+(?:high|level|price)|finish)\b" + _PC_HL_NOT
    + r"|(?:previous|prior|old|former|earlier)\s+(?:record|peak|high|all-time\s+high)\b"
    rf"(?:\s+(?:of|at|near|around)\s+{_PC_LEVEL}|(?!\s+(?:of|in|for)\b))"
    r"|(?:highest|lowest)(?:\s+(?:level|close|closing\s+level|point|price|mark))?"
    r"(?=\s+(?:since|ever|on\s+record|in\s+(?:more\s+than\s+|nearly\s+|over\s+|almost\s+)?"
    r"(?:a|\d+|two|three|four|five|six|seven|eight|nine|ten|several|many)\s+"
    r"(?:days?|weeks?|months?|years?|decades?))\b)"
    r"|(?:\d+|fifty|hundred|two-hundred)-day\s+(?:moving\s+average|MA|SMA|EMA)\b"
    # crypto shorthand: "a new ATH", "its ATL"
    r"|(?:(?:new|fresh)\s+)?(?-i:ATHs?|ATLs?)\b" + _PC_HL_NOT
    + r")"
)
# "its high", "their lows" with no qualifier — a level only for the stock itself (shares /
# stock / a symbol), at a clause end, before a level or before a cause / time
_PC_HIGHLOW_BARE_HEAD = (
    r"(?:its|their)\s+(?:(?:recent|earlier|prior|previous|summer|spring|autumn|winter|"
    r"intraday|session|morning|opening)\s+)?(?:highs?|lows?|peaks?|bottom)\b"
)
_PC_TERRITORY = (
    r"(?:(?:trades|traded|trading|sits|sat|is|was|are|were|remains|remained|now|already)\s+){0,2}"
    r"(?:entered|enters|entering|(?:fell|falls|slid|slides|sank|sinks|moved|moves|tipped|tips)"
    r"\s+into|in|into|near|nears|nearing|neared|out\s+of)\s+(?:a\s+)?"
    r"(?:bear[- ]market|bull[- ]market|correction|record(?:[- ]high)?|all-time[- ]high|"
    r"(?-i:ATH))\s+territory\b(?!\s+(?:in|for|of)\b)"
)


def _pc_alt(words: Iterable[str]) -> str:
    """Literal words as ONE prefix-factored alternation ("ris(?:e(?:[ns])?|ing)|rose"), so
    the engine tests a character per level instead of trying every word in turn — the
    verb lists are tried at every gap position, and a flat list of ~150 words made the
    hostile-input test about nine times slower. A space in a phrase matches any whitespace run."""
    root: dict = {}
    for word in words:
        node = root
        for ch in word.lower():
            node = node.setdefault(ch, {})
        node[""] = {}

    def emit(node: dict) -> str:
        alts = [(r"\s+" if ch == " " else re.escape(ch)) + emit(child)
                for ch, child in sorted(node.items()) if ch]
        if not alts:
            return ""
        body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
        return "(?:" + body + ")?" if "" in node else body

    return "(?:" + emit(root) + ")"


def _pc_words(text: str) -> Tuple[str, ...]:
    return tuple(text.split())


# A gap token never crosses a clause joiner, a modal / forecast word, a speech verb or
# "target" (the CRWV point "analysts' price target" must not lend its noun to a later
# verb; "Tesla Shares Robotaxi Update, Says Rides Doubled" is two clauses), and never a
# lower-case -ing word (a new participial clause: "issued shares, helping revenue rise").
# Its "." only joins characters ("U.S", "$1.47"), so a sentence end stops it.
_PC_STOP_WORDS = _pc_words(
    "as while whilst and but or nor yet so that which who whose whom where when whereas "
    "after before because since though although despite amid amidst if unless until with "
    "without including like unlike versus vs plus via per target targets could would may "
    "might should will can must to expected forecast projected seen likely estimated "
    "predicted thanks due given meanwhile says said say tells told"
)
_PC_STOP = _pc_alt(_PC_STOP_WORDS) + r"\b"
_PC_TOKEN = (
    rf"(?!{_PC_STOP})(?!(?-i:(?!(?:trading|morning|evening)\b)[a-z]+ing\b))"
    r"[\w'’%$€£¥₹+&/-]+(?:\.[\w%]+)*"
)
_PC_SEP = r"[\s,()]+"
_PC_GAP = rf"(?:{_PC_SEP}{_PC_TOKEN}){{0,5}}?{_PC_SEP}"

# ── verbs: closed inflection lists, never open stems ──
_PC_HARD_WORDS = _pc_words(
    "rose rise rises rising risen fell fall falls falling fallen gained gain gains gaining "
    "jumped jump jumps jumping surged surge surges surging soared soar soars soaring rallied "
    "rally rallies rallying climbed climb climbs climbing spiked spike spikes spiking "
    "rebounded rebound rebounds rebounding popped pop pops popping advanced advances "
    "advancing doubled doubles doubling tripled triples tripling quadrupled halved halves "
    "halving dropped drop drops dropping declined decline declines declining slipped slip "
    "slips slipping slid slide slides sliding tumbled tumble tumbles tumbling plunged plunge "
    "plunges plunging plummeted plummet plummets plummeting sank sink sinks sinking sunk "
    "slumped slump slumps slumping dipped dip dips dipping retreated retreat retreats "
    "retreating lost loses losing shed sheds shedding tanked tank tanks tanking cratered "
    "crater craters cratering skidded skid skids skidding sagged sag sags sagging nosedived "
    "nosedive nosedives nosediving crashed crash crashes crashing skyrocketed skyrocket "
    "skyrockets skyrocketing rocketed rockets rocketing leapt leaped leap leaps leaping "
    "zoomed zoom zooms zooming vaulted vault vaults vaulting collapsed collapses collapsing "
    "dove dived dives diving stumbled stumbles stumbling firmed firming"
)
_PC_HARD = _pc_alt(_PC_HARD_WORDS + ("sold off", "sell off", "sells off", "selling off"))
# verbs that count only with a magnitude ("Bitcoin added 3%", "Shares have returned 120%";
# never "the stock was added to the S&P 500")
_PC_MAGONLY_WORDS = _pc_words("added adds adding returned returns returning")
_PC_MAGONLY = _pc_alt(_PC_MAGONLY_WORDS)
# "returned" / "added" also move cash and staff: "returned 20% more cash", "added 5% to
# its workforce" are not price
# … nor "Adds 2% to Bitcoin Holdings" (only "to <a level>" stays) or "returned 34% on equity"
_PC_MAG_ONLY_NOT = (r"(?!\s+(?:more|less|fewer|additional|extra|new)\b)"
                    rf"(?!\s+to\s+(?!{_PC_CUR}|\d))"
                    r"(?!\s+on\s+(?:average\s+)?(?:equity|capital|invested|assets|investment|"
                    r"tangible)\b)")
_PC_ADV = (r"(?:about|nearly|roughly|almost|around|some|over|more\s+than|less\s+than|"
           r"as\s+much\s+as|by|another|a\s+further|an\s+additional|close\s+to|just\s+over|"
           r"just\s+under)")
# A percentage counts as a move unless it is a share OF something or a move IN something
# that is not a trading session ("rose 3% in Friday trading" counts; "rose 2% in same-store
# sales", "is up 200% in revenue" do not).
_PC_MAG_IN_OK = (
    r"(?:early|late|pre-?market|after-?hours|extended|heavy|morning|afternoon|midday|"
    r"New\s+York|U\.S\.|trading|the\s+(?:session|day|week|month|year)|"
    r"(?:a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|a\s+few)\s+"
    r"(?:days?|weeks?|months?|sessions?|years?|quarters?)|\d|"
    r"(?:[\w'’.-]+\s+){1,3}?(?:trading|session|dealings)"
    r"(?!\s+(?:volumes?|revenue|activity|platform|days?|hours|desks?|app)\b))\b"
)
_PC_PCT_MOVE = (
    rf"{_PC_PCT}(?!\s+(?:of|share|shares|stake|stakes|market\s+share|ownership|interest|"
    r"holdings?|weighting|weight|dominance|points?\s+of|more|fewer|less|additional|extra)\b)"
    rf"(?!\s+in\s+(?!{_PC_MAG_IN_OK}))"
)
# "up"/"down" are a move only before a number or as "up sharply", "down on the day".
_PC_UPDOWN = (
    rf"(?:up|down)\s+(?:{_PC_ADV}\s+)?(?:{_PC_PCT_MOVE}|{_PC_CUR}\s?\d|"
    rf"(?:double|triple)[- ]digits\b(?!\s+in\s+(?!{_PC_MAG_IN_OK}))|{_PC_FOLD})|"
    r"(?:up|down)\s+(?:sharply|steeply|strongly|on\s+the\s+(?:day|week|month|year|session|news))"
)
# After shares / stock / a symbol only (a NAME that is "down today" may be an outage): the
# headline shapes "Why Is CoreWeave Stock Down Today?", "Stock Is Up Big Today".
_PC_UPDOWN_WIDE = (
    rf"{_PC_UPDOWN}|(?:up|down)\s+(?:big\b\s*)?(?:today|yesterday|overnight|"
    r"this\s+(?:week|month|year|morning|afternoon)|year[- ]to[- ]date|ytd|so\s+far)\b|"
    r"(?:up|down)\s+big\b"
)
# "higher"/"lower" as an adverb — never before a noun ("pushes higher prices") or "than".
_PC_HIGHER = (
    r"(?:higher|lower)(?![\w-])"
    r"(?!\s+(?:than|prices?|rates?|fees|costs?|wages|margins?|volumes?|demand|sales|revenue|"
    r"output|production|interest|taxes|tariffs|inventory|levels?|for\s+longer)\b)"
)
_PC_HL_ADV = (r"(?:sharply|slightly|modestly|much|significantly|marginally|steadily|broadly|"
              r"further|solidly|firmly)")
_PC_DIR_WORDS = _pc_words(
    "edge edges edged edging tick ticks ticked ticking inch inches inched inching drift "
    "drifts drifted drifting move moves moved moving trade trades traded trading head heads "
    "headed heading"
)
_PC_DIR_VERB = _pc_alt(_PC_DIR_WORDS)
_PC_HL_WORDS = _PC_DIR_WORDS + _pc_words(
    "close closes closed closing end ends ended ending finish finishes finished finishing "
    "open opens opened opening turn turns turned turning trend trends trended trending went "
    "go goes going is are was were been be remain remains remained stay stays stayed"
)
_PC_HL_VERB = _pc_alt(_PC_HL_WORDS)
_PC_PUSH_WORDS = _pc_words(
    "push pushes pushed pushing grind grinds ground grinding creep creeps crept creeping rip "
    "rips ripped ripping gapped gaps gapping shot shoots march marches marched marching "
    "power powers powered powering spring springs sprang sprung"
)
_PC_PUSH = _pc_alt(_PC_PUSH_WORDS)
_PC_BE_WORDS = _pc_words("is are was were been remain remains remained stay stays stayed")
_PC_SOFT_WORDS = _pc_words(
    "eased ease eases easing softened soften softens softening steadied steadies "
    "steadying stabilized stabilised stabilize stabilise stabilizes stabilises stabilizing "
    "stabilising recovered recover recovers recovering swung swing swings swinging whipsawed "
    "whipsaw whipsaws whipsawing seesawed outperformed outperform outperforms outperforming "
    "underperformed underperform underperforms underperforming weakened weaken weakens "
    "weakening strengthened strengthens lagged lag lags lagging trailed trail trails "
    "outpaced outpace outpaces outpacing recouped recoup recoups recouping hammered "
    "crushed punished clobbered battered pummeled pummelled slammed whacked walloped savaged"
)
_PC_BACK_WORDS = _pc_words(
    "pull pulls pulled pulling bounce bounces bounced bouncing give gives gave giving"
)
_PC_FLAT_WORDS = _PC_BE_WORDS + _pc_words("traded trading closed ended finished held holding")
_PC_VOLATILE_WORDS = _PC_BE_WORDS + _pc_words("turned became proved")
_PC_TOOK_WORDS = _pc_words("took take takes taking")
_PC_WINNER_WORDS = _pc_words("been is was are were became become becomes")
# first words of the soft moves added in review round 4 (the verb guard must admit them)
_PC_SOFT4_WORDS = _pc_words(
    "reversed reverses reversing changed changes changing cooled cools cooling round-tripped "
    "round-trips round-tripping roundtripped roundtrips roundtripping gone gapped gaps gapping "
    "delivered generated "
    "produced posted record ended finished closed in into on"
)
_PC_SOFT = (
    rf"{_PC_DIR_VERB}\s+(?:up|down)\b|"
    # "reversed course after an early gain", "changed direction", "cooled off after a run",
    # "round-tripped after earnings", "has gone parabolic", "gapped up at the open"
    r"(?:reversed|reverses|reversing|changed|changes|changing)\s+(?:course|direction)\b|"
    r"(?:cooled(?:\s+off)?|cools\s+off|cooling\s+off)\b|"
    r"(?:round-?trip(?:ped|s|ping))\b|"
    r"(?:gone|went|goes|going)\s+parabolic\b|"
    r"(?:gapped|gaps|gapping)\s+(?:up|down)\b|"
    # "is in the red for the week", "ended the week in the red"
    r"(?:in|into)\s+the\s+(?:red|green)\s+(?:for|on)\s+the\s+(?:week|day|session|month)\b|"
    r"(?:ended|finished|closed)\s+the\s+(?:week|day|session|month)\s+(?:in\s+the\s+(?:red|green)|"
    r"higher|lower|(?:up|down)\b)|"
    # "has delivered a 200% return since its IPO" (never a return ON equity)
    rf"(?:delivered|generated|produced|posted)\s+(?:a|an)\s+(?:{_PC_ADV}\s+)?{_PC_PCT}\s+"
    r"(?:total\s+)?return\b(?!\s+on\s+(?:average\s+)?(?:equity|capital|invested|assets|"
    r"investment|tangible))|"
    # "on track for a record month"
    r"on\s+(?:track|pace|course)\s+for\s+(?:a|an|its|their)\s+record\s+"
    r"(?:day|week|month|session)\b|"
    rf"{_pc_alt(_PC_TOOK_WORDS)}\s+it\s+on\s+the\s+chin\b|"
    rf"(?:{_PC_HL_VERB}|{_PC_PUSH})\s+(?:{_PC_HL_ADV}\s+)?{_PC_HIGHER}|"
    rf"{_pc_alt(_PC_HL_WORDS)}\s+sideways\b|"
    rf"{_pc_alt(_PC_FLAT_WORDS)}\s+range-?bound\b|"
    rf"{_pc_alt(_PC_BACK_WORDS)}\s+back|"
    # "steady" alone only as a verb-like predicate — never "a steady dividend", "the shares
    # offer a steady income stream" (review 2026-10-09); "a steady gainer" still counts
    r"(?:(?<!\ba\s)(?<!\ban\s)(?<!\bthe\s)steady\b|steady\s+(?:gainers?|performers?|"
    r"outperformers?|underperformers?|winners?|losers?)\b)|"
    rf"{_pc_alt(_PC_SOFT_WORDS)}|"
    rf"{_pc_alt(_PC_FLAT_WORDS)}\s+(?:little\s+changed|flat|unchanged|steady)|"
    rf"{_pc_alt(_PC_VOLATILE_WORDS)}\s+(?:(?:highly|very|extremely|more|less)\s+)?volatile|"
    r"under\s+(?:(?:heavy|renewed|intense|significant|some|more|further|selling|mounting|"
    r"sustained|downward)\s+)?pressure|"
    # "PLUG's stock is facing downward pressure" (replay, 2026-10-09)
    r"(?:facing|faces|faced)\s+(?:(?:heavy|renewed|continued|sustained|mounting|some|more)\s+)?"
    r"(?:downward|selling)\s+pressure|"
    rf"{_pc_alt(_PC_TOOK_WORDS)}\s+a\s+(?:(?:big|major|sharp|heavy|severe)\s+)?"
    r"(?:beating|hit|tumble|dive|nosedive|hammering|drubbing|bath)|"
    r"on\s+a\s+(?:tear|losing\s+run|winning\s+run|hot\s+streak)|"
    r"cut\s+in\s+half|"
    rf"{_pc_alt(_PC_WINNER_WORDS)}\s+(?:a|an|the|one\s+of\s+the)\s+"
    r"(?:(?:(?:big|bigger|biggest|top|clear|standout|notable|major|real|huge)\s+)?(?:winners?|losers?)|"
    r"(?:top|best|worst|standout|strongest|weakest)\s+performers?)\b"
)
# "N% below its June peak", "40% off its high", "20% below its 50-day moving average",
# "20% below where it began the year", "10 times their 2023 low".
_PC_REL = (
    rf"(?:{_PC_PCT}\s+(?:below|above|off|from|under|beneath)\s+(?:"
    r"(?:its|their|the)\s+"
    rf"(?:(?:[\w-]+|{_PC_CUR}\d[\d.,]*)\s+){{0,2}}?"
    r"(?:peak|peaks|highs?|lows?|record|IPO\s+price|listing\s+price|offering\s+price|debut|"
    r"all-time\s+high|moving\s+average|(?-i:ATH|ATL))\b"
    r"(?!\s+(?:capacity|demand|season|levels?|output|production|utilization|load|rates?|"
    r"volumes?|sales|revenue|of|for|in)\b)"
    r"|where\s+(?:it|they)\s+(?:began|started|opened|ended|closed)\s+the\s+(?:year|month|week|"
    r"day|session|quarter))"
    rf"|(?:\d+|{_PC_WORDNUM})\s+times\s+(?:its|their)\s+(?:[\w-]+\s+){{0,2}}?"
    r"(?:peak|lows?|highs?|IPO\s+price|listing\s+price|offering\s+price)\b)"
)
_PC_LEVEL_WORDS = _pc_words(
    "closed close closes closing traded trade trades trading hovered hover hovers hovering "
    "ended end ends ending settled settle settles settling finished finish finishes finishing "
    "opened open opens opening reclaimed reclaim reclaims reclaiming regained regain regains "
    "regaining broke break breaks breaking topped top tops topping crossed cross crosses "
    "crossing cleared clear clears clearing hit hits hitting dipped dip dips dipping fell fall "
    "falls falling rose rise rises rising climbed climb climbs climbing jumped jump jumps "
    "jumping surged surge surges surging soared soar soars soaring sank sink sinks sinking "
    "slid slide slides sliding slipped slip slips slipping dropped drop drops dropping "
    "plunged plunge plunges plunging tumbled tumble tumbles tumbling plummeted plummet "
    "plummets plummeting retreated retreat retreats retreating rebounded rebound rebounds "
    "rebounding rallied rally rallies rallying spiked spike spikes spiking stood stand stands "
    "sat sit sits sitting held hold holds holding touched touch touches touching tested test "
    "tests testing reached reach reaches reaching neared nears nearing approached approach "
    "approaches approaching set sets setting notched marked logged scaled struck is was are "
    "were remains remained remain stays stayed stay surpassed surpass surpasses surpassing "
    "breached breach breaches breaching eclipsed eclipse eclipses eclipsing peaked peak peaks "
    "peaking bottomed bottoms bottoming steadied steadies steadying stabilized stabilizes "
    "stabilised stabilises stabilizing stabilising consolidated consolidates consolidating "
    "stalled stall stalls stalling struggled struggle struggles struggling languished "
    "languish languishes languishing smashed smashes smashing blew blows blowing zoomed zooms "
    "zooming vaulted vaults vaulting leapt leaped leaps worth flirts flirted flirting defends "
    "defended defending defend faces faced facing crashed crashes collapsed collapses "
    "skyrocketed skyrockets rocketed rockets edged edges inched inches ticked ticks drifted "
    "drifts moved moves fluctuated slumped slumps sagged sags skidded skids tanked tanks "
    "cratered craters dove dived nosedived bounced bounces bouncing printed prints"
)
_PC_LEVEL_VERB = _pc_alt(_PC_LEVEL_WORDS + ("changed hands", "changing hands", "changes hands"))
_PC_LEVEL_PREP = (r"(?:at|near|around|above|below|under|over|past|beyond|through|to|toward|"
                  r"towards|about|roughly|just|nearly|almost|a|an|the|record|new|fresh|back|"
                  r"again|briefly|its|another|intraday|well|far|with|as\s+high\s+as|"
                  r"as\s+low\s+as|more\s+than|less\s+than|the\s+(?:session|day|week)|"
                  r"(?:(?:key|major|strong|critical|psychological|stiff|firm)\s+)?"
                  r"(?:resistance|support))")
_PC_HL_PREP = (r"(?:a|an|its|their|the|another|to|at|of|near|around|above|below|off|from|"
               r"close|just|within|toward|towards|well|far|still|back|again|nearly|almost|"
               r"slightly|sharply|further|now|currently)")
# A bare "record" is a level only after a closing / trading verb and at a clause end or
# before a time / cause ("Oracle closed at a record on Tuesday"; never "hit a record in
# quarterly bookings", "set a record for cloud revenue").
_PC_RECORD_CLOSE_VERB = _pc_alt(_pc_words(
    "closed closes ended ends finished finishes settled settles traded trades"
))
_PC_RECORD_MOVE_VERB = _pc_alt(_pc_words(
    "hit hits touched touches reached reaches rose rises climbed climbs jumped jumps surged "
    "surges soared soars rallied rallies topped tops zoomed zooms vaulted vaults notched"
))
_PC_RECORD_END = (r"(?=\s*(?:$|[,.;:!?—–)])|\s+(?:on|after|as|amid|despite|following|before|"
                  r"today|yesterday|overnight|intraday|this|last)\b)")
_PC_LEVEL_ALT = (
    rf"{_PC_LEVEL_VERB}(?:(?:\s+{_PC_LEVEL_PREP}){{0,3}}\s+{_PC_LEVEL_ANY}|"
    rf"(?:\s+{_PC_HL_PREP}){{0,4}}\s+{_PC_HIGHLOW})|"
    rf"{_PC_TERRITORY}"
)
_PC_RECORD_OBJ = (rf"(?:\s+(?:at|to))?\s+(?:a\s+|another\s+|new\s+|a\s+new\s+|a\s+fresh\s+)?"
                  rf"record{_PC_RECORD_END}")
_PC_RECORD_CLOSE = _PC_RECORD_CLOSE_VERB + _PC_RECORD_OBJ
_PC_RECORD_ANY = rf"(?:{_PC_RECORD_CLOSE_VERB}|{_PC_RECORD_MOVE_VERB}){_PC_RECORD_OBJ}"
# for shares / stock / a symbol / a coin; a company NAME gets _PC_LEVEL_NAME
_PC_HIGHLOW_BARE = (
    rf"{_PC_LEVEL_VERB}(?:\s+{_PC_HL_PREP}){{0,3}}?\s+{_PC_HIGHLOW_BARE_HEAD}"
    rf"(?=\s*(?:$|[,.;:!?—–)])|\s+(?:of|at|near|around)\s+{_PC_LEVEL_ANY}|"
    r"\s+(?:on|after|as|amid|despite|following|today|yesterday|this|last)\b)"
)
_PC_LEVEL_REC = (rf"{_PC_LEVEL_ALT}|{_PC_RECORD_ANY}|{_PC_HIGHLOW_BARE}|"
                 rf"(?:fetched|fetches|fetching)(?:\s+{_PC_LEVEL_PREP}){{0,3}}\s+{_PC_LEVEL_ANY}")
_PC_LEVEL_NAME = rf"{_PC_LEVEL_ALT}|{_PC_RECORD_CLOSE}"
_PC_MOVE_NOUN_WORDS = _pc_words(
    "pullback pullbacks correction corrections drawdown drawdowns sell-off sell-offs "
    "selloff selloffs swoon swoons rout routs run-up run-ups runup runups"
)
# never "for deliveries", "of buybacks" within the next six words
_PC_NOT_FOR_OTHER = (r"(?!(?:\s+[\w$'’-]+){0,6}?\s+(?:for|of)\s+(?!(?:the|its|their)\s+"
                     r"(?:shares|stock|year|month|week|day|session|decade)\b))")
_PC_BEST_PERIOD = (r"(?:(?:best|worst)\s+(?:(?:single|one|two|three)-)?"
                   r"(?:day|week|month|quarter|year|session|stretch|start|showing)|"
                   r"record\s+(?:day|week|month|session))\b"
                   + _PC_NOT_FOR_OTHER)
# A hard verb into a COUNT is not a price ("Shares eligible for sale jumped to 1.2 billion",
# "free-float shares rose to 40% of the total")
_PC_COUNT_TAIL = (
    r"(?!\s+(?:to|by|from)\s+(?:about\s+|nearly\s+|roughly\s+|more\s+than\s+|over\s+|almost\s+)?"
    rf"(?:{_PC_NUM}\s*(?:million|billion|thousand|trillion|mn|bn|[mbk])\b|{_PC_PCT}\s+of\b))"
)
# An index change is not a move: "Intel's stock was dropped from the Dow", "the stock lost
# its spot in the S&P 500", "gained a spot in the S&P 500" — but "dropped from the S&P 500's
# top performers" and "fell from the index high" still are (review 2026-10-09).
_PC_INDEX_CHANGE_NOT = (
    r"(?!\s+(?:from|out\s+of)\s+(?:the\s+)?(?:S&P\s*(?:500|400|600)|Dow\s+Jones(?:\s+Industrial"
    r"\s+Average)?|Dow|Nasdaq[- ]?100|Russell\s+\d{4}|index|benchmark)(?![\w-])(?!['’]|\s+"
    r"(?:highs?|lows?|levels?|peaks?|records?|top|gains?|rally)\b))"
    r"(?!\s+(?:a|an|its|their)\s+(?:spot|place|seat|slot)\s+(?:in|on)\b)"
)
# "went from $40 to $187"
_PC_FROM_TO = (
    r"(?:went|goes|gone|going|moved|ran|rose|fell|climbed|surged|soared|jumped|dropped|slid|sank|"
    rf"plunged|rallied)\s+from\s+(?:about\s+|around\s+|roughly\s+)?{_PC_LEVEL_ANY}\s+to\s+"
    rf"(?:about\s+|around\s+|roughly\s+|nearly\s+|more\s+than\s+)?{_PC_LEVEL_ANY}"
)

# "closed down 2.89%" (the v7 price line's wording, copied into stored cards), "ended up
# 3%", "closed 2.9% lower" — never "closed down its Texas plant" (no magnitude)
_PC_CLOSE_MOVE = (
    r"(?:closed|closes|ended|ends|finished|finishes|settled|settles)\s+(?:up|down)\s+"
    rf"(?:{_PC_ADV}\s+)?{_PC_PCT_MOVE}|"
    r"(?:closed|closes|ended|ends|finished|finishes|settled|settles|traded|trades|trading)\s+"
    rf"{_PC_PCT}\s+(?:higher|lower)\b"
)
# "The stock is part of a broader downward trend" (PLUG replay, 2026-10-09) — only after the
# stock as subject, so "Margins have been in a downward trend" never counts
_PC_TREND = (
    r"(?:is|was|are|were|has\s+been|have\s+been|remains|remained)\s+(?:part\s+of\s+|caught\s+in\s+|in\s+)?"
    r"(?:a|an|the)\s+(?:(?:broader|wider|sector-wide|industry-wide|steady|sharp|prolonged|"
    r"persistent|longer)\s+)?(?:downward|upward|bearish|bullish|downhill|losing)\s+trend\b"
)
# ── the verb slot of a subject (after "shares", "the stock", a coin's price …) ──
_PC_VERB_CORE = (
    rf"{_PC_TREND}|{_PC_CLOSE_MOVE}|{_PC_LEVEL_REC}|{_PC_REL}|{_PC_UPDOWN_WIDE}|{_PC_SOFT}|{_PC_FROM_TO}|"
    rf"{_PC_HARD}{_PC_COUNT_TAIL}{_PC_INDEX_CHANGE_NOT}|\(?\s*{_PC_SIGNED}|"
    rf"{_PC_MAGONLY}\s+(?:{_PC_ADV}\s+)?(?:{_PC_PCT_MOVE}|{_PC_FOLD}){_PC_MAG_ONLY_NOT}|"
    rf"{_pc_alt(_PC_MOVE_NOUN_WORDS)}\b|{_PC_BEST_PERIOD}"
)
# Every alternative of _PC_VERB_CORE starts with one of these words, a digit, a sign or
# "(" — checked first, as one trie walk, so a gap position that cannot start a verb
# costs a few steps instead of a pass over every alternative (test: hostile input).
_PC_VERB_FIRST = tuple(dict.fromkeys(
    _PC_HARD_WORDS + _PC_HL_WORDS + _PC_SOFT_WORDS + _PC_BACK_WORDS + _PC_FLAT_WORDS
    + _PC_VOLATILE_WORDS + _PC_TOOK_WORDS + _PC_LEVEL_WORDS + _PC_PUSH_WORDS
    + _PC_MAGONLY_WORDS + _PC_MOVE_NOUN_WORDS + _PC_WINNER_WORDS + _PC_SOFT4_WORDS
    + _pc_words("now already went goes gone going ran fetched fetches fetching steady")
    + _pc_words("closes ends finishes settled settles remains remained facing faces faced")
    + _pc_words("sold sell sells selling up down under on changed cut best worst entered "
                "enters entering fell falls slid slides sank sinks moved moves tipped tips "
                "in into near nears nearing neared out")
    + _pc_words("one two three four five six seven eight nine ten eleven twelve fifteen "
                "twenty thirty forty fifty twofold threefold fourfold fivefold sixfold "
                "sevenfold eightfold ninefold tenfold")
))
_PC_VERB_FIRST_ALT = _pc_alt(_PC_VERB_FIRST)
_PC_VERB_GUARD = rf"(?={_PC_VERB_FIRST_ALT}(?![\w'’-])|[(+−\d-])"
_PC_VERB = rf"{_PC_VERB_GUARD}(?:{_PC_VERB_CORE})(?![\w-])"

# ── subjects ──
# No bare singular "share" (per share, share of revenue, market share, share count). The
# look-behinds drop counts and inventories ("diluted shares", "crude stocks", "dealer
# stock", "total shares"), conditions ("if shares fall below $30, the notes convert") and
# forecasts ("analysts see shares reaching $250", "Jefferies sees the stock rising 30%");
# the look-aheads drop share-count / pay / inventory / content senses ("shares
# outstanding", "stock options", "stock of homes", "stock availability", "stock photo",
# "shares between friends") and "shares" used as a VERB ("Oracle shares a site with
# OpenAI", the headline "Tesla Shares Robotaxi Update").
_PC_HYPO_VERBS = _pc_words("see sees expect expects project projects forecast forecasts "
                           "predict predicts")
_PC_HYPO_LB = (
    r"(?<!\bif\s)(?<!\bunless\s)(?<!\bwhether\s)(?<!\bonce\s)(?<!\bshould\s)"
    + "".join(rf"(?<!\b{v}\s)" for v in _PC_HYPO_VERBS)
    + "".join(rf"(?<!\b{v}\s{d}\s)" for v in _PC_HYPO_VERBS for d in ("the", "its"))
)
_PC_SUBJ_LB = (
    r"(?<![\w(-])(?<!\(\s)(?<!\bper\s)(?<!\bmarket\s)(?<!\boutstanding\s)(?<!\bdiluted\s)"
    r"(?<!\bbasic\s)(?<!\btreasury\s)(?<!\brestricted\s)(?<!\bpreferred\s)(?<!\bcommon\s)"
    r"(?<!\brolling\s)(?<!\bcrude\s)(?<!\binventory\s)(?<!\bsafety\s)(?<!\bnew\s)"
    r"(?<!\bdistillate\s)(?<!\bgasoline\s)(?<!\bhousing\s)(?<!\bemployee\s)(?<!\bunvested\s)"
    r"(?<!\bvested\s)(?<!\bweighted\s)(?<!\baverage\s)(?<!\bexcess\s)(?<!\bmillion\s)"
    r"(?<!\bbillion\s)(?<!\bthousand\s)(?<!\d\s)(?<!\bof\s)(?<!\bin\s)(?<!\btotal\s)"
    r"(?<!\bdealer\s)(?<!\bchannel\s)(?<!\bwarehouse\s)(?<!\bstore\s)(?<!\bspare\s)"
    r"(?<!\bfloat\s)(?<!\bfloating\s)"
    # the stock as an OBJECT: "volume in the stock tripled", "ownership of the stock rose"
    r"(?<!\bin\sthe\s)(?<!\bin\sits\s)(?<!\bof\sthe\s)(?<!\bof\sits\s)(?<!\bfor\sthe\s)"
    r"(?<!\bfor\sits\s)(?<!\bon\sthe\s)(?<!\bon\sits\s)" + _PC_HYPO_LB
)
_PC_SHARES_DET = (r"(?:a|an|the|its|his|her|their|our|that|every|each|both|common|similar|"
                  r"key|data|information|details|insights|space|ownership|control|"
                  r"responsibility|knowledge|news|views?|concerns?|updates?)\b")
_PC_SHARES_NOT = (
    r"(?!\s+(?:outstanding|count|counts|buybacks?|repurchas\w*|bought|purchased|issued|"
    r"held(?!\s+(?:steady|firm|flat))|owned|pledged|tendered|vested|granted|awarded|delivered|"
    r"sold(?!\s+off)|offered|registered|reserved|authorized|available|underlying|remaining|"
    r"retired|issuance|offerings?|sales?|splits?|options?|awards?|grants?|units?|plans?|"
    r"programs?|-?based|short|shorted|volumes?|turnover|between|among|amongst|via|per|"
    # a Form 4 value ("sold shares worth $850,000") and share pools ("shares eligible for
    # sale", "shares set aside for awards", "shares on loan")
    r"worth|eligible|set\s+aside|on\s+loan|lent|borrowed|earmarked|locked\s+up|"
    r"in\s+(?:circulation|issue|escrow))\b)"
    r"(?!\s+of\s+(?:common|preferred|class)\b)"
    r"(?!\s+of\s+(?:(?:the|its|their|total|overall|global|net)\s+)?(?:revenue|sales|"
    r"the\s+market|market|spending|volume|traffic|deliveries|profits?|income|wallet)\b)"
)
# Title Case "<Name> Shares <Noun>" is the verb "shares" ("Oracle Shares OCI Growth Plan");
# a headline subject is followed by its verb ("Oracle Shares Slide"). Only the generic
# subject needs it: a name's rows never cross a noun gap.
_PC_SHARES_TITLE_VERB = (
    r"(?!(?<=[A-Za-z0-9.]\s(?-i:Shares))\s+(?=(?-i:[A-Z0-9]))"
    r"(?!(?:of|in|on|that|which|are|were|have|has|had|is|was|extend|extends|extended|pare|"
    r"pares|pared|trim|trims|trimmed|erase|erases|erased|reverse|reverses|reversed)\b)"
    rf"(?!{_PC_VERB_FIRST_ALT}(?![\w'’-])))"
)
_PC_STOCK_NOT = (
    r"(?![-‐])(?!\s+(?:of|outstanding|count|buybacks?|repurchas\w*|splits?|options?|awards?|"
    r"grants?|units?|comp|compensation|based|issuance|offerings?|sales?|levels?|rates?|"
    r"markets?|exchanges?|index|indices|indexes|futures|dividends?|picks?|pickers?|plans?|"
    r"programs?|ownership|holdings?|portfolios?|positions?|purchases?|issues?|consideration|"
    r"deal|swap|warrants?|universe|screeners?|piles?|a|an|the|its|their|our|his|her|"
    r"price\s+targets?|component|portion|mix|allocation|trading|traders|analysts|investors|"
    r"volumes?|turnover|loans?|lending|research|ratings?|coverage|quotes?|data|tokens?|"
    r"tokenization|accounts?|app|brokerage|availability|photos?|images?|imagery|footage|"
    r"videos?|music|art|illustrations?|content|connect|keeping|keepers?|room|outs?|takes?|"
    r"checks?|turns?|worth|pay|payouts?|dilution|on\s+hand|in\s+hand|in\s+transit)\b)"
    # inventory at a place ("stock at retailers", "stock at Ford dealers") — never "the
    # stock at one point", "the stock at $40", "at a record"
    rf"(?!\s+at\s+(?!(?:a|an|the|its|their|one|some|times|that|this|least|midday|noon|"
    rf"record|about|around|roughly|nearly|over|under|just|\d)\b|{_PC_CUR}|\d))"
    # a list of instruments: "(stock, options)"
    r"(?!\s*,\s*(?:options|bonds|ETFs?|crypto|futures|equities|funds|cash|derivatives)\b)"
)
_PC_PRICE_NOT = r"(?!\s*-?\s*targets?\b)(?![\w-])"
_PC_ADR = (r"(?-i:ADRs?|ADSs)\b(?!\s+(?:issuance|fees?|programs?|ratios?|holders?|conversions?|"
           r"facility|facilities|business|depositary|services?|levels?|counts?|outstanding|"
           r"sales?|volumes?)\b)")
_PC_SUBJ = (
    # a one-character guard first, so the look-behinds run only where a subject can start
    r"(?=[sSaA])" + r"(?<!(?-i:SPDR\sGold)\s)" + _PC_SUBJ_LB + r"(?:"
    rf"(?:share|stock)(?:-|\s+)price{_PC_PRICE_NOT}|"
    rf"(?:stock['’]s|shares['’]|share['’]s)\s+price{_PC_PRICE_NOT}|"
    rf"shares\b(?!\s+{_PC_SHARES_DET}){_PC_SHARES_NOT}{_PC_SHARES_TITLE_VERB}|"
    rf"(?!(?-i:STOCK\s+Act)\b)stock\b{_PC_STOCK_NOT}|"
    rf"{_PC_ADR}"
    r")"
)
_PC_AUX = (r"(?:is|was|are|were|has|have|had|been|being|also|briefly|then|again|just|now|"
           r"still|already|itself|initially|later|eventually|ultimately|reportedly|last|"
           r"more\s+than|nearly|almost|only|currently|today|yesterday|recently)")

# ── move nouns ──
# PNOUN after "price"; MNOUN after "the stock's" / "the shares'" / "in its shares".
_PC_PNOUN = (r"(?:declines?|drops?|gains?|rally|rallies|weakness|swings?|volatility|momentum|"
             r"pullbacks?|run-?ups?|sell-?offs?|selloffs?|slides?|slumps?|surges?|action|"
             r"plunges?|swoon|rout|performance|retreat|crash|falls?|rebounds?|reaction)")
# with no adjective, only nouns that cannot be a PRODUCT's or a commodity's price ("hurt by
# price declines" is a memory maker's honest line, "little affected by price swings" a
# pipeline's, "its price performance" a chip's; "offset by price weakness" is not)
_PC_PNOUN_STOCK = (r"(?:weakness|momentum|action|pullbacks?|run-?ups?|sell-?offs?|selloffs?|"
                   r"rally|rallies|slumps?|swoon|rout)")
_PC_MNOUN = (r"(?:rally|rallies|slide|slump|sell-?off|selloff|plunge|tumble|slip|dip|pullback|"
             r"correction|swoon|rout|drawdown|run-?up|run|surge|rebound|retreat|drop|decline|"
             r"fall|crash|rise|climb|jump|gains?|losses|advance|weakness|momentum|performance|"
             r"volatility|swing|swings)")
_PC_ADJ = (r"(?:recent|sharp|steep|continued|ongoing|wild|heavy|big|bigger|biggest|modest|"
           r"notable|significant|persistent|latest|further|sustained|sudden|brief|massive|"
           r"huge|strong|broad|broader|renewed|extended|choppy|daily|weekly|monthly|intraday|"
           r"overnight|early|late|post-earnings|year-to-date|week-long|month-long|relief|"
           r"double-digit|single-digit|furious|violent|steady|slight|mild|swift|quick)")
# Before "price", only adjectives of TIMING / pace: "strong price gains" is a memory maker
# selling dearer, "recent price declines" (the CRWV conclusion) reads as the stock.
_PC_PRICE_ADJ = (r"(?:recent|sharp|steep|sudden|continued|ongoing|persistent|latest|wild|"
                 r"choppy|daily|weekly|intraday|post-earnings|year-to-date|renewed|extended|"
                 r"further|sustained)")
# The card's own name + "'s" is ambiguous ("Intel's decline as a chip leader", "Nvidia's
# strong performance"), so only price-flavoured adjectives and motion nouns count there.
_PC_TERM_ADJ = (r"(?:recent|sharp|steep|sudden|brief|daily|weekly|monthly|intraday|"
                r"overnight|post-earnings|year-to-date|double-digit|single-digit|wild|"
                r"furious|violent|latest|week-long|month-long)")
_PC_TERM_MNOUN = (r"(?:rally|rallies|slide|slump|sell-?off|selloff|plunge|tumble|slip|dip|"
                  r"pullback|correction|swoon|rout|drawdown|run-?up|surge|rebound|retreat|"
                  r"drop|decline|fall|crash|rise|climb|jump|advance)")
# No flag when the phrase is about something else: "price declines in NAND", "price
# drops on the Model Y", "a 10% pullback in capex spending", "a 10% pullback by
# advertisers" — unless that something is the shares themselves ("a slip in CoreWeave shares").
_PC_ABOUT_ELSE = (
    r"(?!(?:\s+(?:year[- ]over[- ]year|from\s+a\s+year\s+earlier|sequentially|annually|"
    r"quarter[- ]over[- ]quarter)|\s*,\s*(?:especially|particularly|notably|mainly|mostly|"
    r"largely|primarily|chiefly))?\s+(?:in|for|on|of|across|at|from|among|within|into|to|by|"
    r"with)\s+"
    r"(?!(?:its|the|their|(?-i:[A-Z])[\w.&-]*['’]s)?\s*(?:shares|stock|share\s+price|"
    r"stock\s+price)\b)"
    # …and never a day, a date or a session: "a 2.2% slip on Monday", "price declines on
    # Monday, Oct 5", "a 2.2% slip in early trading" are WHEN the stock moved, not another
    # topic (review 2026-10-09; the prompt's TIME rule asks the model to name the day)
    r"(?!(?-i:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day)\b(?!s)|"
    r"(?-i:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d|"
    r"the\s+(?:day|week|month|session|open|close|opening\s+bell|closing\s+bell)\b|"
    r"(?:(?:early|late|heavy|light|midday|morning|afternoon|extended|after-?hours|pre-?market|"
    r"premarket|overnight|intraday|regular|(?-i:[A-Z])[\w'’]*)\s+)?trading\b(?!\s+(?:revenues?|"
    r"income|desks?|business|volumes?|activity|platforms?|firms?|operations?|results?|"
    r"segments?|units?|fees?|apps?|partners?|houses?)\b)|"
    r"after-?hours\b|pre-?market\b|premarket\b|the\s+news\b))"
)
_PC_VALUE_SHARE = (
    r"(?:about\s+|nearly\s+|roughly\s+|almost\s+|more\s+than\s+|over\s+)?"
    rf"(?:half|a\s+third|a\s+quarter|two-thirds|three-quarters|{_PC_PCT})\s+(?:of\s+)?"
    r"(?:its|their)\s+(?:market\s+)?value\b(?!\s+(?:proposition|chain)\b)(?!\s+against\b)"
)
_PC_MAG = (
    rf"(?:sharply|steeply|precipitously|dramatically|{_PC_VALUE_SHARE}|{_PC_FOLD}|{_PC_PCT_MOVE})"
)

# ── what may follow an intransitive move ("CoreWeave Rallies on Microsoft Deal") ──
# never an idiom ("jumped on the agentic-AI wave", "advanced on several fronts") or a role
# ("surged as the top chain for memecoin launches", "AI surged as a priority")
_PC_ON = (
    r"on\b(?!\s+(?:(?:the|a|an|several|multiple|many|all|both|two|three|its|their|this|that)\s+)?"
    r"(?:[\w-]+\s+){0,2}?(?:opportunit(?:y|ies)|chances?|bandwagon|trends?|ideas?|wave|train|"
    r"craze|hype|fronts?|board|scene|map|radar|stage|sidelines|offensive|list|rankings?)\b)"
)
_PC_AS = (
    r"as\b(?!\s+(?:the|a|an)\s+(?:[\w-]+\s+)?(?:top|leading|largest|biggest|most|best|first|"
    r"second|third|preferred|go-to|dominant|main|primary|favou?rite|default|fastest|cheapest|"
    r"only|priority|choice|platform|chain|leader|winner|standard|alternative|option)\b)"
)
_PC_TIME_FOLLOW = (
    r"today|yesterday|overnight|intraday|premarket|"
    r"this\s+(?:week|month|year|morning|afternoon)|last\s+(?:week|month|year)|"
    r"over\s+the\s+(?:weekend|week|month|past|last|session|day)|"
    r"in\s+(?:early|late|pre-?market|after-?hours|extended|heavy|volatile|midday|afternoon|"
    r"morning|trading|the\s+session)\b|"
    rf"in\s+(?:a|one|two|three|\d+)\s+(?:day|week|month|session|year|quarter)s?\b|"
    r"for\s+(?:a|the)\s+(?:second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"\d+(?:st|nd|rd|th))\s+(?:straight|consecutive)\s+(?:sessions?|days?|trading\s+days?|weeks?)\b|"
    r"to\s+(?:a|an|its|the|their)\s+(?:new\s+|fresh\s+)?(?:record|all-time|\d+-\w+|multi-\w+|"
    rf"{_PC_WORDNUM}-\w+)"
)
_PC_FOLLOW_WORD = (
    rf"\s+(?:{_PC_ON}|after|{_PC_AS}|amid|despite|following|before|while|since|with|alongside|"
    rf"along\s+with|in\s+(?:line|tandem)\s+with|{_PC_TIME_FOLLOW})"
)
# for verbs with a common non-price sense ("Apple gained while Huawei slipped in China's
# premium smartphones" is market share): only a cause or a time
_PC_FOLLOW_NARROW = rf"\s+(?:{_PC_ON}|after|amid|despite|following|since|{_PC_TIME_FOLLOW})"
_PC_CLAUSE_END = r"(?=\s*(?:$|[,.;:!?—–)]))"

# Index and coin objects of "outperformed / lagged …" (never "the market" or "the
# benchmark": Ford's sales and BlackRock's funds outperform those).
_PC_INDEX_OBJ = (
    r"(?:the\s+)?(?:broader\s+|wider\s+|overall\s+)?(?:S&P(?:\s*500)?|Nasdaq(?:\s*100|\s+Composite)?|"
    r"Dow(?:\s+Jones)?|Russell(?:\s*\d+)?|Bitcoin|BTC|Ether(?:eum)?|ETH|Solana|"
    r"crypto(?:\s+market)?)(?![\w-])"
)
_PC_INDEX_OBJ_TERM = (
    r"(?:" + _PC_INDEX_OBJ + r"|gold(?![\w-])(?!\s+(?:prices?|miners?|producers?|futures|"
    r"reserves?|output|production)\b))"
)
# "-ing" words that are themselves price moves ("…, rising 12%")
_PC_PRICE_ING = _pc_alt(tuple(w for w in _PC_HARD_WORDS if w.endswith("ing")) + _pc_words(
    "trading closing ending finishing hitting reaching topping hovering breaking crossing"))
_PC_VS_TAIL = (
    r"(?!\s+(?:in|on|across|among)\s+(?!(?:price|performance|returns?|gains?)\b))"
    # "by 150 points", "by 20%" is the price gap; "by transaction count" is not
    r"(?!\s+by\s+(?!(?:about\s+|nearly\s+|roughly\s+|more\s+than\s+|over\s+|almost\s+)?"
    r"(?:\d|a\s+wide\s+margin|a\s+mile|double|triple)))"
    # a metric follows: "outpaced Ethereum again, settling three times as many
    # transactions", "lagged Bitcoin, with ETF inflows of only $100 million"
    rf"(?!(?:\s+again)?\s*,\s*(?:with\s+(?!(?:a|an)\s)|(?!{_PC_PRICE_ING}\b)(?-i:[a-z]+ing)\b))"
    r"(?!\s+for\s+(?!(?:a|the|\d+|"
    rf"{_PC_WORDNUM})\s+(?:(?:straight|consecutive|second|third|fourth|fifth|sixth|row|"
    r"year|month)\s+)*(?:days?|weeks?|months?|quarters?|years?|sessions?|time)\b))"
)
_PC_SENT_START = r"(?:^|(?<=[.!?;:]\s)|\b(?:after|following|despite|amid|since|before)\s+)"
_PC_FUNDING = (r"(?![^.!?;]{0,80}?\b(?:funding|financing|fundraising|round|tender\s+offer|"
               r"raise|raised|raising|Series\s+[A-H]|secondary|tender|share\s+sale)\b)")
_PC_DESC = (
    r"\b(?:the|this|that)\s+(?:[\w-]+\s+){0,3}?"
    r"(?:company|firm|chipmaker|provider|operator|maker|automaker|carmaker|retailer|lender|"
    r"bank|insurer|carrier|airline|giant|miner|startup|developer|manufacturer|producer|"
    r"supplier|conglomerate|brokerage|neocloud|hyperscaler|fund|ETF|ETP|trust)"
)

# A bare coin / token's verb: a magnitude, a level or a clause end ("The token gained a
# listing" is adoption; "after the token dropped" is an airdrop, so a bare "dropped" needs
# a magnitude)
_PC_COIN_VERB = (
    rf"(?:(?:{_PC_HARD}\s+(?:{_PC_ADV}\s+)?{_PC_MAG}|{_PC_MAGONLY}\s+(?:{_PC_ADV}\s+)?{_PC_MAG}"
    rf"{_PC_MAG_ONLY_NOT})|"
    rf"{_PC_UPDOWN}|{_PC_REL}|(?:{_PC_HL_VERB}|{_PC_HARD}|{_PC_PUSH})\s+{_PC_HIGHER}|"
    rf"{_PC_LEVEL_REC}|(?!(?:dropped|drops|drop)\b){_PC_HARD}{_PC_CLAUSE_END})(?![\w-])"
)
# Capitalised subjects that are not a stock: commodities, indexes, currencies and metrics
# that open a sentence ("Copper climbed 3% in London trading", "Volume rose 30% on the day")
_PC_SESSION_NOT = (
    r"(?:copper|gold|silver|oil|crude|brent|wti|platinum|palladium|lithium|nickel|aluminum|"
    r"aluminium|zinc|tin|iron|uranium|wheat|corn|soybeans?|cocoa|coffee|sugar|cotton|lumber|"
    r"gas|gasoline|diesel|treasur\w*|bonds?|yields?|futures|volumes?|revenues?|sales|traffic|"
    r"downloads?|storage|inventor(?:y|ies)|output|production|demand|inflows?|outflows?|usage|"
    r"bookings|orders|shipments|deliveries|prices?|rates?|spending|nasdaq|dow|s&p|russell|"
    r"stoxx|ftse|dax|cac|nikkei|hang|kospi|sensex|nifty|tsx|asx|vix|index(?:es)?|indices|"
    r"markets?|stocks|equities|dollar|yen|euro|sterling|pound|yuan|renminbi|rupee|won|peso|"
    r"franc)(?![\w&'’-])"
)
# "outperformed Ether ETFs", "Ethereum's" — the object is a product or another metric
_PC_VS_OBJ_NOT = (
    r"(?!['’])(?!\s+(?:ETFs?|ETPs?|funds?|products?|futures|options|miners?|stocks?|treasur\w*|"
    r"holdings?|volumes?|networks?|chains?|ecosystems?|developers?|users?|fees|transactions?|"
    r"as\s+a\s+whole)\b)"
)
# "Its market cap of $65 billion is down from $90 billion" (the static "of $65 billion"
# alone is a size, which is allowed)
_PC_MCAP_DOWN_FROM = (
    r"market\s+(?:cap|value|capitalization)\s+of\s+(?:about\s+|nearly\s+|roughly\s+)?"
    rf"{_PC_CUR}\s?\d[\d.,]*\s*(?:billion|trillion|bn|tn|[bt])\b\s+(?:is|was|has\s+been|"
    r"had\s+been|now)\s+(?:now\s+)?(?:down|up|off)\s+(?:about\s+|nearly\s+|roughly\s+)?"
    rf"(?:{_PC_PCT}\s+)?from\b"
)
_PC_LVL_ADV = (r"(?:about|around|roughly|nearly|more\s+than|less\s+than|over|under|just\s+over|"
               r"just\s+under)")
# "52-week low is $33", "all-time high of $126,000", "ATH of $293"
_PC_NAMED_HL = (
    r"(?:(?:52-week|all-time|lifetime|intraday|post-IPO)\s+(?:closing\s+)?"
    r"(?:highs?|lows?|peaks?)|(?-i:ATHs?|ATLs?))\s+(?:is|was|of|at|stands\s+at|stood\s+at|"
    rf"came\s+in\s+at|near|around)\s+(?:{_PC_LVL_ADV}\s+)?{_PC_LEVEL_ANY}"
)

PRICE_CLAIM_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    # shares / stock / share price / ADRs + (≤5-token gap) + a move, soft move or level
    ("subject_move", re.compile(_PC_SUBJ + _PC_GAP + _PC_VERB, _I)),
    # "Shares, which fell 30% last month, …" — the one joiner a subject may cross
    ("subject_relative_move", re.compile(
        _PC_SUBJ + r"\s*,\s*(?:which|that)\s+(?:(?:has|have|had|is|are|was|were)\s+)?"
        rf"(?:{_PC_HARD}|{_PC_UPDOWN}|{_PC_SOFT})(?![\w-])", _I)),
    # "The coin is down 12%", "the token's price fell", "Crypto prices tumbled" — the bare
    # coin / token needs a magnitude, a level or a clause end ("The token gained a
    # listing", "The cryptocurrency has gained traction" are adoption, not price)
    ("coin_move", re.compile(
        r"\b(?:the|this|that|its)\s+(?:coin|token|cryptocurrency|digital\s+asset|memecoin|"
        r"meme\s+coin|altcoin)"
        rf"(?:\s+{_PC_AUX}){{0,3}}\s+{_PC_COIN_VERB}|"
        r"\b(?:(?:the|this|that|its)\s+(?:coin|token|cryptocurrency)['’]s\s+price|"
        r"(?:crypto(?:currency)?|coin|bitcoin|ether|ethereum)\s+prices?)"
        rf"(?:\s+{_PC_AUX}){{0,3}}\s+{_PC_VERB}", _I)),
    # "The chipmaker fell sharply", "The AI cloud provider gained 4%", "The ETF hit an
    # all-time high", "The company lost half its value"
    ("descriptor_move", re.compile(
        _PC_DESC + rf"(?:\s+{_PC_AUX}){{0,2}}\s+(?:{_PC_HARD}\s+"
        rf"(?:{_PC_ADV}\s+)?{_PC_MAG}|{_PC_UPDOWN}|{_PC_LEVEL_ALT})", _I)),
    # "share-price decline", "stock price swings" — never a pay design ("tied to share
    # price performance against peers")
    ("share_price_noun", re.compile(
        r"(?<![\w-])(?<!\btied\sto\s)(?<!\blinked\sto\s)(?<!\bbased\son\s)(?<!\bpegged\sto\s)"
        r"(?<!\bcontingent\son\s)(?<!\bdependent\son\s)(?<!\bdepends\son\s)(?<!\bmeasured\sby\s)"
        r"(?<!\brelative\s)(?<!\bindexed\sto\s)(?<!\bconditioned\son\s)(?<!\bvests\son\s)"
        + _PC_HYPO_LB + r"(?<!\bwhen\s)"
        rf"(?:share|stock)(?:-|\s+)price(?:['’]s)?\s+(?:{_PC_ADJ}\s+){{0,2}}"
        rf"{_PC_PNOUN}\b", _I)),
    # "countered by recent price declines" (the CRWV conclusion), "sharp price declines" —
    # never price declines that hurt MARGINS (a product's price: "Steep price declines
    # hurt NAND margins at Micron")
    ("price_noun", re.compile(
        rf"\b{_PC_PRICE_ADJ}\s+(?:{_PC_PRICE_ADJ}\s+)?price\s+{_PC_PNOUN}\b{_PC_ABOUT_ELSE}"
        r"(?!(?:\s*,[^,.;:!?]{1,40},)?\s+(?:have\s+|has\s+|had\s+)?(?:hurt|hit|weighed\s+on|"
        r"pressured|squeezed|cut|dented|compressed|eroded|reduced|lowered|trimmed|crimped|"
        r"dragged\s+on|offset|helped|boosted|lifted|spurred|revived|fueled|fuelled|drove|"
        r"stimulated|supported)\s+"
        r"(?:[\w&'’.-]+\s+){0,3}?(?:margins?|profitability|revenues?|earnings|sales|profits?|"
        r"results|ASPs?|demand|volumes?|deliveries|orders|traffic|units|adoption)\b)", _I)),
    # "offset by price weakness", "its price action"
    ("price_noun_stock", re.compile(
        rf"\b(?:by|its|their)\s+price\s+{_PC_PNOUN_STOCK}\b{_PC_ABOUT_ELSE}", _I)),
    # "the stock's sharp run-up", "the shares' slide", "the token's 20% weekly decline",
    # "the stock's reaction to earnings", "after the stock's run"
    ("possessive_move", re.compile(
        r"(?<![\w-])(?<!\bcrude\s)(?<!\binventory\s)(?:stock['’]s|shares['’]|token['’]s|coin['’]s)"
        rf"\s+(?:(?:{_PC_PCT}|{_PC_ADJ})\s+){{0,2}}(?:{_PC_MNOUN}\b{_PC_ABOUT_ELSE}|reaction\b)",
        _I)),
    # "a 2.2% slip in CoreWeave shares", "the decline in its share price" (never "a jump in
    # shares traded": exchange volume)
    ("move_in_shares", re.compile(
        rf"\b{_PC_MNOUN}\s+in\s+(?:its\s+|the\s+|their\s+|(?-i:[A-Z])[\w.&-]*['’]s\s+)?"
        r"(?:(?-i:[A-Z])[\w.&-]*\s+){0,2}?"
        rf"(?:shares\b{_PC_SHARES_NOT}(?!\s+(?:traded|changing\s+hands|exchanged)\b)|"
        rf"stock\b{_PC_STOCK_NOT}|(?:share|stock)(?:-|\s+)price\b)",
        _I)),
    # "Recent stock weakness", "the stock rally"
    ("stock_noun", re.compile(
        r"(?<![\w-])(?<!\bcrude\s)(?<!\binventory\s)(?<!\brolling\s)\bstock\s+"
        r"(?:weakness|volatility|slump|sell-?off|selloff|swoon|rout|rally|rallies|drawdown|"
        r"declines?|slide|plunge|surge|run-?up|pullback|crash)\b", _I)),
    # "amid mixed stock performance", "faces mixed market signals and stock performance",
    # "its share performance" (the 2026-10-09 CRWV replays, after the slip point was
    # dropped) — never a pay design ("stock performance awards", "weak stock performance
    # metrics"): an adjective or "its" must lead, and a pay noun must not follow.
    ("stock_performance", re.compile(
        r"(?:\b(?:mixed|weak|weaker|strong|stronger|poor|lackluster|lacklustre|choppy|"
        r"volatile|disappointing|sluggish|muted|tepid|uneven|erratic|recent|negative|"
        r"positive|declining|falling|rising|lagging|weakening|slumping|sliding|sagging|"
        r"deteriorating|improving)\s+(?:[\w-]+\s+){0,2}?(?:and|&)\s+|"
        r"\b(?:mixed|weak|weaker|strong|stronger|poor|lackluster|lacklustre|choppy|volatile|"
        r"disappointing|sluggish|muted|tepid|uneven|erratic|recent|negative|positive|"
        r"declining|falling|rising|lagging|weakening|slumping|sliding|sagging|deteriorating|"
        r"improving)\s+|"
        r"\b(?:its|their)\s+)(?:stock|share)(?:-price)?\s+performance\b"
        r"(?!\s+(?:awards?|units?|stock|shares|plans?|grants?|conditions?|goals?|targets?|"
        r"metrics?|criteria|vesting|periods?|hurdles?|measures?)\b)(?!-based)", _I)),
    # "as the sector rallied", "the broader market sold off" (TestFlight META 2026-10-09:
    # "Social media stocks, including Meta, showed a flat performance as the sector rallied")
    # — only price-only verbs, so "sector demand surged" and "sector revenue slumped" pass.
    ("sector_move", re.compile(
        r"\b(?:the\s+)?(?:broader\s+|wider\s+|whole\s+|entire\s+)?(?:sector|peer\s+group|"
        r"benchmark|broader\s+market|stock\s+market)\s+(?:has\s+|had\s+|have\s+)?(?:rallied|"
        r"rallies|rallying|sold\s+off|sells\s+off|selling\s+off|tumbled|tumbles|plunged|plunges|"
        r"surged|surges|soared|soars|slumped|slumps|sank|sinks|slid|slides|sagged|cratered|"
        r"rebounded|rebounds)\b", _I)),
    # A peer GROUP's shares moving: "Social media stocks rallied, with Snap leading", "AI
    # stocks, including CoreWeave, declined" (META / CRWV replays, 2026-10-09). Never a
    # stockpile ("crude stocks fell 3 million barrels", "fish stocks declined").
    ("group_stocks_move", re.compile(
        r"(?<!\binventory\s)(?<!\bcrude\s)(?<!\bgasoline\s)(?<!\bdistillate\s)(?<!\bgas\s)"
        r"(?<!\boil\s)(?<!\bfuel\s)(?<!\bgrain\s)(?<!\bfood\s)(?<!\bsafety\s)(?<!\bcopper\s)"
        r"(?<!\baluminum\s)(?<!\baluminium\s)(?<!\bzinc\s)(?<!\bnickel\s)(?<!\bwheat\s)"
        r"(?<!\bcorn\s)(?<!\bcotton\s)(?<!\bcoffee\s)(?<!\bsugar\s)(?<!\bcocoa\s)(?<!\bdealer\s)"
        r"(?<!\bretail\s)(?<!\bfish\s)(?<!\bseed\s)(?<!\bhousing\s)(?<!\bwarehouse\s)"
        r"\bstocks,?\s+(?:including\s+[^,.;]{1,40},\s+)?(?:are\s+|were\s+|have\s+|has\s+|had\s+)?"
        r"(?:rallying|rallied|rally|rallies|declined|declining|fell|falling|slid|sliding|sank|"
        r"sinking|tumbled|tumbling|surged|surging|soared|soaring|jumped|climbed|climbing|dropped|"
        r"dropping|slumped|slumping|sold\s+off|selling\s+off|plunged|plunging)\b", _I)),
    # ── subject-less idioms ──
    # a streak counts with a day count or at a clause end / before a cause ("extended its
    # losing streak to five days"); never "a winning streak for favored NFL teams" or "its
    # winning streak to 10 straight quarters of growth"
    ("streak", re.compile(
        rf"\b(?:\d+|{_PC_WORDNUM})-(?:day|session|week)\s+(?:losing|winning)\s+streak\b|"
        r"\b(?:losing|winning)\s+streak\b(?="
        r"\s*(?:$|[,.;:!?—–)])"
        rf"|\s+(?:to|of|at)\s+(?:\d+|{_PC_WORDNUM})\s+(?:(?:straight|consecutive|trading)\s+)*"
        r"(?:days?|sessions?|weeks?)\b"
        r"|\s+(?:after|amid|despite|following|today|yesterday|this\s+week|last\s+week)\b"
        r"|\s+on\s+(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day|the\s+(?:news|day|session|week)|"
        r"news)\b)"
        # "rose for a fifth straight session" (a SESSION is a trading day; "fell for a third
        # straight week" is also jobless claims)
        rf"|\b(?:{_PC_HARD})\s+for\s+(?:a|the)\s+(?:second|third|fourth|fifth|sixth|seventh|"
        r"eighth|ninth|tenth|\d+(?:st|nd|rd|th))\s+(?:straight|consecutive)\s+"
        r"(?:sessions?|trading\s+days?)\b",
        _I)),
    # "pared losses after the upgrade", "erased earlier gains" — a bare "pared losses" only at
    # a clause end or before a cause (never "Opendoor pared losses, posting its first
    # positive EBITDA": its net loss narrowed)
    ("pared_gains", re.compile(
        r"\b(?:(?:pared|pares|paring|erased|erases|erasing|gave\s+back|gives\s+back|"
        r"giving\s+back|give\s+back|wiped\s+out|reversed|reverses|reversing|extended|extends|"
        r"extending|recouped|recoups|recouping)\s+"
        r"(?:(?:some|all|most|much|part|half)\s+of\s+)?(?:its\s+|their\s+|the\s+|those\s+)?(?:"
        r"(?:earlier|early|initial|intraday|session|morning|premarket|pre-market|post-earnings|"
        r"steep|sharp|year-to-date|ytd)\s+(?:gains|losses|declines|rally|advance|slide)\b"
        r"(?!\s+(?:in|on|at|from|of|to|for|across)\s)"
        r"|(?:(?:recent|prior|previous)\s+)?(?:gains|losses(?!\s+as\b)|declines|rally|advance|"
        r"slide|losing\s+streak)\b"
        r"(?=\s*(?:$|[.!?;:])|\s+(?:after|amid|as|following|despite|today|yesterday|late|"
        r"by\s+the\s+close|ahead\s+of|in\s+(?:early|late|afternoon|morning|midday|after-hours|"
        r"extended|pre-?market)\s+trading)\b))"
        # "trimmed early losses", "gave up its post-earnings gains" — only a session's moves
        r"|(?:trimmed|trims|trimming|gave\s+up|gives\s+up|giving\s+up|give\s+up)\s+"
        r"(?:(?:some|all|most|much|part|half)\s+of\s+)?(?:its\s+|their\s+|the\s+|those\s+)?"
        r"(?:earlier|early|initial|intraday|session|morning|premarket|pre-market|post-earnings|"
        r"year-to-date|ytd)\s+(?:gains|losses|declines|rally|advance)\b"
        r"(?!\s+(?:in|on|at|from|of|to|for|across)\s))", _I)),
    # "Intraday gains faded", "gapped up at the open" (a gap is only ever a price's)
    ("session_gains", re.compile(
        r"\b(?:earlier|intraday|session|premarket|pre-market|after-hours|morning)\s+"
        r"(?:gains|losses)\b(?!\s+(?:in|on|at|from|of|to|for|across)\s)"
        r"|\b(?:gapped|gaps|gapping)\s+(?:up|down|higher|lower)\b", _I)),
    # "Selling pressure followed …", "Profit-taking set in", "Investors took profits" — never
    # "selling pressure from homeowners" or "took profits from the stake sale"
    ("trading_pressure", re.compile(
        r"\b(?:selling|buying)\s+pressure\b" + _PC_ABOUT_ELSE +
        r"|\bprofit[- ]taking\b(?!\s+(?:on|in|from|by)\s+(?!(?:the|its|their)\s+(?:shares|stock)\b))"
        r"|\b(?:took|take|takes|taking|locked\s+in|locks\s+in|locking\s+in|lock\s+in)\s+profits\b"
        r"(?!\s+(?:from|on|in|it|they|that|which)\b)", _I)),
    ("traders_shares", re.compile(
        r"\b(?:(?:sold|sell|sells|selling)\s+off|dumped|dump|dumps|dumping|"
        r"(?:piled|pile|piles|piling)\s+into|(?:bid|bids|bidding)\s+up)\s+"
        r"(?:the\s+|its\s+|their\s+|(?-i:[A-Z])[\w.&-]*['’]s\s+)?"
        rf"(?:shares\b{_PC_SHARES_NOT}|stock\b{_PC_STOCK_NOT})", _I)),
    ("causative_shares", re.compile(
        r"\b(?:sent|send|sends|sending|lifted|lift|lifts|lifting|pushed|push|pushes|pushing|"
        r"drove|drive|drives|driving|dragged|drag|drags|dragging|knocked|knock|knocks|knocking|"
        r"boosted|boost|boosts|boosting|buoyed|buoy|buoys|buoying|"
        r"(?:weighed|weigh|weighs|weighing)\s+on|pressured|pressures|pressuring|"
        r"hammered|hammers|hammering|battered|batters|battering|hit|hits|hitting|"
        r"propelled|propels|propelling|dented|dents|denting|rattled|rattles|rattling|"
        r"crushed|crushes|crushing|slammed|slams|slamming|punished|punishes|punishing)\s+"
        r"(?:the\s+|its\s+|their\s+|(?-i:[A-Z])[\w.&-]*['’]s\s+)?"
        rf"(?:shares\b{_PC_SHARES_NOT}|stock\b{_PC_STOCK_NOT}|"
        rf"(?:share|stock)\s+price{_PC_PRICE_NOT})", _I)),
    # "…, causing stock to fall" (the 2026-10-09 ORCL replay), "caused the stock to drop",
    # "led shares to slide" — a cause that makes the shares the infinitive's subject. Never
    # stock options or inventory ("caused stock options to vest", "caused inventory to fall").
    ("caused_move", re.compile(
        r"\b(?:caus(?:e|es|ed|ing)|l(?:ed|ead|eads|eading)|prompt(?:s|ed|ing)?|forc(?:e|es|ed|ing)|"
        r"spurr(?:ed|ing)|spur|spurs|helped|help|helps|helping)\s+"
        r"(?:the\s+|its\s+|their\s+|(?-i:[A-Z])[\w.&-]*['’]s\s+)?"
        rf"(?:shares\b{_PC_SHARES_NOT}|stock\b{_PC_STOCK_NOT}|(?:share|stock)\s+price\b)\s+"
        r"to\s+(?:fall|drop|decline|slide|slip|tumble|plunge|plummet|sink|slump|dip|retreat|"
        r"crater|tank|rise|climb|jump|surge|soar|rally|rebound|spike|skyrocket|move\s+(?:higher|"
        r"lower|up|down)|trade\s+(?:higher|lower|up|down)|close\s+(?:higher|lower|up|down)|"
        r"(?:hit|reach|touch)\s+(?:a|an|its)\s+(?:new\s+)?(?:record|all-time|52-week)|"
        r"double|halve)\b|"
        # "caused AI stocks, including CoreWeave, to sink" (CRWV replay, 2026-10-09)
        r"\b(?:caus(?:e|es|ed|ing)|l(?:ed|ead|eads|eading)|sen(?:t|d|ds|ding)|push(?:ed|es|ing)?|"
        r"(?:drove|drive|drives|driving))\s+(?:[\w-]+\s+){0,3}?"
        # never stockpiles: "inventory stocks", "crude stocks", "grain stocks"
        r"(?<!inventory\s)(?<!crude\s)(?<!gasoline\s)(?<!distillate\s)(?<!safety\s)(?<!grain\s)"
        r"(?<!food\s)(?<!fuel\s)(?<!oil\s)(?<!dealer\s)(?<!retail\s)stocks,?\s+(?:including\s+[^,.;]{1,40},\s+)?"
        r"to\s+(?:sink|fall|drop|tumble|slide|slip|plunge|sag|rise|rally|surge|jump|climb|soar)\b",
        _I)),
    # "its best day since March", "worst week since the IPO", "its worst session" — never
    # "its worst week since 2024 for flight cancellations" (the for / of within six words)
    ("best_worst_day", re.compile(
        r"(?:"
        rf"\b(?:it|they)(?:\s+{_PC_AUX}){{0,2}}\s+(?:posted|posts|had|has|have|having|logged|"
        r"logs|notched|recorded|saw|sees|suffered|suffers|enjoyed|enjoys|marked|marks|was|is|"
        r"were|are|(?:on\s+(?:pace|track|course)|headed|set)\s+for)\s+(?:its|their)\s+"
        r"|\b(?:drop|decline|fall|slide|slump|plunge|sell-?off|selloff|rout|rally|surge|jump|"
        r"gain|loss|move|advance|rebound|tumble)s?\s+(?:marked|marks|was|is|made|makes|capped|"
        r"caps)\s+(?:its|their)\s+"
        r"|(?<![\w-])(?:stock['’]s|shares['’]|token['’]s|coin['’]s|ETF['’]s|fund['’]s)\s+"
        r")(?:best|worst)\s+(?:single-|one-|two-|three-)?(?:day|week|trading\s+day|session)\b"
        + _PC_NOT_FOR_OTHER +
        r"(?:\s+(?:since|in\s+(?:more\s+than\s+|nearly\s+|over\s+|almost\s+)?"
        r"(?:a|\d+|two|three|four|five|six|several|many)\s+(?:years?|months?|decades?|weeks?))\b|"
        r"(?<=session)|(?<=trading\sday))"
        r"|\b(?:biggest|largest|best|worst|steepest|sharpest)\s+(?:one|single|two|three)-day\s+"
        r"(?:percentage\s+)?(?:gains?|drops?|declines?|rally|loss|falls?|jumps?|surges?|plunges?|"
        r"moves?|slides?|sell-?offs?|selloffs?|rises?|advances?|rout|tumbles?)\b" + _PC_ABOUT_ELSE,
        _I)),
    # "The 5% drop followed …", "Despite the 5% drop, …", "A 10% correction followed …" —
    # an unnamed move that OPENS a sentence (or follows after / despite / amid) is about the
    # price; mid-sentence it is revenue's ("sales posted a 1.5% slip", "applications posted
    # a 20% rebound", "the model assumes a 5% correction")
    ("the_pct_move", re.compile(
        _PC_SENT_START
        + rf"(?:the|a|an|this|that|its|their)\s+{_PC_PCT}\s+(?:{_PC_ADJ}\s+)?"
        r"(?:drop|decline|fall|surge|jump|gain|climb|rise|dip|advance|pop|move|pullback|"
        r"correction|drawdown|sell-?off|selloff|swoon|rout|run-?up|rally|slip|slide|plunge|"
        r"tumble|retreat|rebound)s?\b"
        + _PC_ABOUT_ELSE, _I)),
    # "below its IPO price", "tripled from its IPO price", "3x its IPO price", "a 2025 IPO up 200%"
    ("ipo_price", re.compile(
        r"(?<!\bdiscount\s)(?<!\bpremium\s)"
        rf"\b(?:above|below|under|over|beneath|from|since)\s+(?:its|their|the)\s+"
        rf"(?:{_PC_CUR}\d[\d.,]*\s+)?(?:IPO|listing|offering|debut)\s+price\b"
        rf"|\b(?:\d+(?:\.\d+)?x|(?:\d+|{_PC_WORDNUM})\s+times|double|triple|twice|thrice|half)\s+"
        rf"(?:its|their|the)\s+(?:{_PC_CUR}\d[\d.,]*\s+)?(?:IPO|listing|offering|debut)\s+price\b"
        r"|\b(?:IPO|debut|listing)\s+(?:now\s+|that\s+is\s+|already\s+)?(?:up|down)\s+"
        rf"(?:{_PC_ADV}\s+)?{_PC_PCT_MOVE}", _I)),
    # "underperformed the S&P 500", "outperformed Bitcoin" — not after a fund ("its active
    # funds have outperformed the S&P 500" is an asset manager's product)
    ("vs_index", re.compile(
        r"(?=[ouOU])(?<!\bfunds\s)(?<!\bfund\s)(?<!\bmanagers\s)(?<!\bportfolio\s)(?<!\bportfolios\s)"
        r"(?<!\bstrategies\s)(?<!\bfunds\shave\s)(?<!\bfunds\shad\s)(?<!\bfund\shas\s)"
        r"(?<!\bmanagers\shave\s)(?<!\bstrategies\shave\s)"
        # a metric's comparison: "spot volume underperformed the crypto market", "Spot
        # Bitcoin ETFs outperformed Ether ETFs"
        r"(?<!\bvolume\s)(?<!\bvolumes\s)(?<!\binflows\s)(?<!\boutflows\s)(?<!\brevenue\s)"
        r"(?<!\bsales\s)(?<!\bdeposits\s)(?<!\busage\s)(?<!\bfees\s)(?<!\bETFs\s)(?<!\bETF\s)"
        r"\b(?:out|under)perform(?:s|ed|ing)?\s+" + _PC_INDEX_OBJ + _PC_VS_OBJ_NOT + _PC_VS_TAIL,
        _I)),
    # "A double-digit percentage drop followed …" (a bare "double-digit gain", or "post a
    # low double-digit percentage gain", is revenue language)
    ("digit_move", re.compile(
        r"(?:\b(?:double|triple)-digit\s+(?:(?:percentage|percent)\s+)?"
        r"(?:pullback|correction|sell-?off|selloff|rally|rout|swoon|drawdown)s?\b|"
        + _PC_SENT_START +
        r"(?:the|a|an|this|that|its|their)\s+(?:(?:low|mid|high)[- ])?(?:double|triple|single)-digit"
        r"\s+(?:percentage|percent)\s+(?:drop|decline|fall|plunge|slide|gain|surge|jump|rise|"
        r"climb|advance|dip|slump|loss|move)(?:s|es)?\b)"
        + _PC_ABOUT_ELSE, _I)),
    ("ytd_gains", re.compile(
        r"(?:^|(?<=[.!?;]\s)|(?<=\bits\s)|(?<=\btheir\s))"
        r"(?:gains?|rally|rise|surge|climb|advance|declines?|drop|losses|slide|plunge|run-?up)"
        r"\s+of\s+(?:about\s+|nearly\s+|more\s+than\s+|over\s+|roughly\s+)?"
        rf"{_PC_PCT}\s+(?:year[- ]to[- ]date|ytd|this\s+year|so\s+far|since\s+January)", _I)),
    # "It rose 3% in after-hours trading", "CoreWeave was down 5% premarket" — the subject is
    # it / they or a capitalised name right before the verb (the read path knows no name);
    # never a metric's ("App downloads jumped 50% on the news", "storage rose 3% on the
    # week") or a commodity's or an index's ("Copper climbed 3% in London trading")
    ("session_pct", re.compile(
        r"(?:\b(?:it|they)|(?<![\w.&'’-])(?!" + _PC_SESSION_NOT + r")"
        r"(?!(?:(?<![\s\S])|(?<=[.!?;:]\s))(?-i:[A-Z][a-z]*[a-rtv-z]s)(?![\w.&'’-]))"
        r"(?-i:[A-Z])[\w.&'’-]*)"
        rf"(?:\s+{_PC_AUX}){{0,3}}\s+"
        r"(?:up|down|higher|lower|rose|fell|gained|lost|jumped|slid|slipped|climbed|dropped|"
        r"surged|sank|tumbled|rallied|advanced|declined|added|shed|leapt|leaped|soared|plunged|"
        r"spiked|popped|skyrocketed|rocketed|dove|dived|tanked|plummeted)\s+"
        r"(?:as\s+much\s+as\s+|about\s+|nearly\s+|roughly\s+|almost\s+|more\s+than\s+)?"
        rf"{_PC_PCT}\s+(?:in\s+)?(?:pre-?market|after-?hours|intraday|"
        r"extended(?:-hours)?\s+trading|(?:early|late|midday|afternoon|morning|premarket|"
        r"after-hours|New\s+York|U\.S\.|London|Hong\s+Kong)\s+trading|"
        r"on\s+the\s+(?:day|session|week)(?!\s+(?:of|before|after)\b)|on\s+the\s+news)", _I)),
    # "Despite the rally, …", "The sell-off wiped …", "A sharp move lower followed …" — a
    # reference back to a price move (never "the run-up ahead of the tariff deadline", an
    # event's eve)
    ("the_rally", re.compile(
        rf"\b(?:(?:the|this|that|its|their|recent|ongoing|latest)\s+(?:{_PC_ADJ}\s+)?"
        r"(?:rally|sell-?off|selloff|rout|swoon|run-?up(?!\s+(?:ahead\s+of|before|to|into|"
        r"leading\s+up)\b)|meltdown|short\s+squeeze)|"
        r"an?\s+(?:sell-?off|selloff|rout|swoon|meltdown|short\s+squeeze)|"
        rf"(?:a|an|the|this|that|its)\s+(?:{_PC_ADJ}\s+)?moves?\s+(?:higher|lower)(?![\w-]))\b"
        + _PC_ABOUT_ELSE, _I)),
    # market value / valuation MOVES (a static "market cap of $50 billion" is size: allowed).
    # Someone ELSE's market value is not the card's ("USDC's market capitalization rose",
    # "the S&P 500's market cap threshold"), nor a private funding round ("its valuation
    # rose to $183 billion in the funding round").
    # The subject needs a determiner — "its", "the company's", "the market cap" or a
    # sentence start: "USDC market cap rose" is another asset's; "X said its valuation
    # tripled" is a private company's round.
    ("market_value_move", re.compile(
        r"(?:(?:\b(?:the\s+)?(?:company|firm|group|stock|chipmaker|business)['’]s\s+"
        r"(?:market\s+(?:cap|value|capitalization|valuation)|valuation)|"
        r"(?<!\bsaid\s)(?<!\bsays\s)\b(?:its|their)\s+"
        r"(?:market\s+(?:cap|value|capitalization|valuation)|valuation)|"
        r"(?:^|(?<=[.!?;]\s)|(?<=\bthe\s))market\s+(?:cap|value|capitalization|valuation))\b"
        r"(?!\s+(?:of|on|for|thresholds?|requirements?|cutoffs?|minimum|floor|ranks?|ranking|"
        r"weight|weighting|share|limits?|rules?|criteria|tests?)\b)"
        rf"(?:{_PC_SEP}{_PC_TOKEN}){{0,3}}?\s+"
        r"(?:rose|fell|surged|soared|jumped|climbed|swelled|ballooned|shrank|shrunk|plunged|"
        r"tumbled|slid|dropped|sank|topped|crossed|surpassed|passed|eclipsed|hit|reached|"
        r"doubled|tripled|halved|eroded|evaporated|collapsed|cratered|vaulted|zoomed|"
        r"rises|falls|surges|soars|jumps|climbs|swells|tops|crosses|passes|hits|reaches)\b"
        # "Its market cap of $65 billion is down from $90 billion"
        r"|\b(?:its|their)\s+" + _PC_MCAP_DOWN_FROM + r")" + _PC_FUNDING, _I)),
    ("wiped_value", re.compile(
        r"\b(?:wiped|wipe|wipes|wiping|erased|erase|erases|erasing|shaved|shave|shaves|shaving|"
        r"knocked|knock|knocks|knocking|added|add|adds|adding|lopped|stripped)\s+"
        r"(?:about\s+|nearly\s+|roughly\s+|more\s+than\s+|over\s+|almost\s+)?"
        rf"{_PC_CUR}\s?\d[\d.,]*(?:\s*(?:billion|million|trillion|bn|mn|tn|[bmt])\b)?\s+"
        r"(?:off|from|to)\s+"
        r"(?:its\s+|their\s+|the\s+company['’]s\s+|(?-i:[A-Z])[\w.&-]*['’]s\s+)?"
        r"(?:market\s+(?:value|cap|capitalization)|valuation)\b", _I)),
    ("became_size", re.compile(
        r"(?:\b(?:became|become|becomes|becoming|turned\s+into)\s+(?:a|an|the\s+[\w-]+)\s+"
        rf"{_PC_CUR}\s?\d[\d.,]*\s*(?:billion|trillion|bn|tn|[bt])\b(?:-dollar)?\s+"
        r"(?:company|firm|business|chipmaker)\b"
        # "Oracle became worth nearly $1 trillion overnight" (never a private round)
        r"|\b(?:became|becomes|becoming)\s+worth\s+(?:about\s+|nearly\s+|roughly\s+|more\s+than\s+|"
        rf"over\s+|almost\s+)?{_PC_CUR}\s?\d[\d.,]*\s*(?:billion|trillion|bn|tn|[bt])\b)"
        + _PC_FUNDING, _I)),
    # verbless levels: "At $300, the stock …", "With shares at $70", "Strategy's shares, at
    # $350, …", "its 52-week range", "The stock's closing price was $255.20", "The last
    # trade was $128", "CoreWeave's $130 share price"
    ("level_phrase", re.compile(
        r"(?:^|(?<=[.!?;]\s))At\s+(?:about\s+|around\s+|roughly\s+|nearly\s+|just\s+(?:over|under)\s+)?"
        rf"{_PC_LEVEL}(?:\s+(?:a|per)\s+share)?\s*,\s+(?:the\s+(?:stock|shares)|shares|its\s+shares)\b"
        r"|\bwith\s+(?:its\s+|the\s+)?(?:shares|stock)\s+(?:now\s+|still\s+|currently\s+)?"
        rf"(?:trading\s+)?(?:at|near|around|above|below|over|under)\s+{_PC_LEVEL_ANY}"
        rf"|{_PC_SUBJ}\s*,\s+(?:now\s+|still\s+|currently\s+)?(?:trading\s+)?"
        rf"(?:at|near|around|above|below)\s+{_PC_LEVEL_ANY}"
        r"|\b52-week\s+(?:trading\s+)?range\b"
        # "Its 52-week low is $33" — a 52-week high / low is only ever a price
        r"|\b52-week\s+(?:closing\s+)?(?:highs?|lows?)\s+(?:is|was|of|at|stands\s+at|stood\s+at|"
        rf"came\s+in\s+at|near|around)\s+(?:{_PC_LVL_ADV}\s+)?{_PC_LEVEL_ANY}"
        r"|\b(?:closing|opening|last|intraday)\s+(?:share\s+|stock\s+)?price\s+"
        rf"(?:was|is|of|at|stood\s+at|came\s+in\s+at|hit|reached)\s+{_PC_LEVEL_ANY}"
        rf"|\blast\s+(?:trade|sale)\s+(?:was|of|at)\s+{_PC_LEVEL_ANY}"
        rf"|{_PC_LEVEL}\s+(?:share|stock)\s+price{_PC_PRICE_NOT}"
        # "The stock's 52-week low is $33", "its all-time high of $126,000"
        rf"|(?<![\w-])(?:stock['’]s|shares['’]|its|their|token['’]s|coin['’]s)\s+{_PC_NAMED_HL}"
        # "One share of CoreWeave now sells for about $130", "The price of a CoreWeave share
        # is about $130"
        r"|\b(?:one|a|a\s+single)\s+share\s+(?:of\s+(?:[\w.&'’-]+\s+){1,4}?)?"
        r"(?:(?:now|currently|still|today)\s+)?(?:sells?|sold|costs?|trades?|traded|goes|went|"
        r"fetch(?:es|ed)?|(?:is|was)\s+(?:worth|priced\s+at))\s+(?:for\s+|at\s+)?"
        rf"(?:{_PC_LVL_ADV}\s+)?{_PC_LEVEL_ANY}"
        r"|\bprice\s+of\s+(?:a|one|a\s+single)\s+(?:[\w.&'’-]+\s+){0,3}?share\b"
        rf"(?:\s+{_PC_AUX}){{0,2}}\s+(?:{_PC_LVL_ADV}\s+)?{_PC_LEVEL_ANY}"
        # "A $10,000 investment in CoreWeave at its IPO would be worth $45,000 today"
        rf"|\b(?:a|an)\s+{_PC_CUR}\s?{_PC_NUM}(?:\s?[kK])?\s+investment\s+in\s+"
        r"(?:[\w.&'’-]+\s+){1,6}?(?:would|will|could)\s+(?:now\s+|today\s+)?(?:be\s+worth|"
        r"have\s+(?:grown|turned)\s+(?:in)?to|have\s+become|be\s+valued\s+at)\b", _I)),
    # "the Nasdaq 100's best performer", "among the worst performers in the S&P MidCap 400",
    # "led gainers on the Nasdaq", "one of the year's best-performing stocks"
    ("performance_rank", re.compile(
        r"\b(?:best|worst|top|biggest)[- ]performing\s+(?:stocks?|shares|names|components?|"
        r"members?|coins?|tokens?|cryptocurrenc(?:y|ies))\b"
        r"|\b(?:best|worst|top|biggest|leading)\s+(?:performers?|gainers?|losers?|decliners?|"
        r"advancers?)\s+(?:in|on|among|of)\s+(?:the\s+)?(?:S&P|Nasdaq|Dow|Russell|index|"
        r"benchmark|NYSE|crypto)"
        r"|(?:S&P(?:\s*500)?|Nasdaq(?:\s*100)?|Dow|Russell(?:\s*\d+)?|index|benchmark|NYSE)"
        r"['’]s\s+(?:best|worst|top|biggest)\s+(?:performers?|gainers?|losers?|decliners?)"
        r"|\b(?:led|leads|leading|topped|tops)\s+(?:the\s+)?(?:(?:day['’]s|session['’]s|"
        r"market['’]s|biggest|top)\s+)?(?:gainers|decliners|losers|advancers)\b"
        # "Dogecoin is a 10-bagger this year"
        r"|\b(?:\d+|ten|five|multi)-baggers?\b", _I)),
    # "Rival Nebius rose 8%" — a named peer's move (the card's own names are below)
    ("rival_move", re.compile(
        r"\b(?:rival|rivals|peer|competitor)\s+(?-i:[A-Z])[\w.&-]*(?:\s+(?-i:[A-Z])[\w.&-]*){0,2}"
        rf"(?:\s+{_PC_AUX}){{0,2}}\s+(?:{_PC_HARD}\s+(?:{_PC_ADV}\s+)?{_PC_MAG}|{_PC_MAGONLY}\s+(?:{_PC_ADV}\s+)?{_PC_MAG}{_PC_MAG_ONLY_NOT})",
        _I)),
]

# ── the card's own subject (symbol, coin name, company name) ──
# A name never counts right after a preposition ("revenue at CoreWeave rose", "attacks on
# Ethereum"), a quantity ("staked ETH rose 5% to 35 million", "exchange-held BTC", "sold
# 5,000 BTC at $110,000 each") or a forecast / condition ("analysts see Bitcoin reaching
# $200,000").
_PC_TERM_LB = (
    r"(?<![\w'’.&-])(?<!\bthe\s)(?<!\bon\s)(?<!\bof\s)(?<!\bin\s)(?<!\bfor\s)(?<!\bat\s)"
    r"(?<!\bby\s)(?<!\bfrom\s)(?<!\bwith\s)(?<!\bto\s)(?<!\bthan\s)(?<!\binto\s)"
    r"(?<!staked\s)(?<!wrapped\s)(?<!bridged\s)(?<!liquid\s)(?<!held\s)(?<!mined\s)"
    r"(?<!burned\s)(?<!\bspot\s)(?<!\bnet\s)(?<!\btotal\s)(?<!\bmore\s)(?<!\bless\s)"
    r"(?<![^\d]\d\s)(?<![^\d]\d\d\s)(?<![^\d]\d\d\d\s)(?<!,\d\d\d\s)(?<!\.\d\s)"
    r"(?<!\.\d\d\s)(?<![^\d](?!19|20)\d{4}\s)(?<!\d{5}\s)"
    # the cost of USING a chain is a fee ("Using Ethereum now costs about $0.02 per swap")
    r"(?<!\busing\s)(?<!\bsending\s)(?<!\bbridging\s)(?<!\bstaking\s)(?<!\bminting\s)"
    r"(?<!\btransacting\s)(?<!\bswapping\s)"
    + _PC_HYPO_LB
)
_PC_TERM_OBJ_EQUITY = (
    rf"shares\b{_PC_SHARES_NOT}|stock\b{_PC_STOCK_NOT}|(?:share|stock)\s+price{_PC_PRICE_NOT}|"
    + _PC_ADR
)
# A COIN card also reads "Bitcoin's price", "Bitcoin price", "Bitcoin volatility"; a
# company's "price" is a product's ("Apple's price stays at $799 for the base iPhone").
_PC_TERM_OBJ_COIN = (
    _PC_TERM_OBJ_EQUITY
    + r"|(?:price\s+)?volatility\b|price(?!\s*(?:targets?|of|for|on|per)\b)(?![\w-])|token\b|coin\b"
)
# verbs that end a clause as a price move ("ETH rallied.", "CoreWeave slides, …")
_PC_INTRANS_END = (
    r"(?:rallied|rallies|rally|rallying|surged|surges|surge|soared|soars|slid|slides|slide|"
    r"slipped|slips|sank|sinks|tumbled|tumbles|plunged|plunges|plummeted|plummets|slumped|"
    r"slumps|tanked|tanks|cratered|craters|spiked|spikes|jumped|jumps|climbed|climbs|"
    r"dipped|dips|fell|falls|rose|rises|retreated|retreats|rebounded|rebounds|bounced|"
    r"bounces|skidded|skids|popped|pops|nosedived|sagged|sags|crashed|crashes|skyrocketed|"
    r"skyrockets|rocketed|rockets|leapt|leaped|leaps|zoomed|zooms|vaulted|vaults|dove|dived)"
)
# verbs with a common non-price object, counted only before a cause / time phrase
# ("CoreWeave has more than doubled since its IPO"; never "Tesla doubled, then tripled,
# its output")
# ("halved" is not one: "Since Bitcoin halved last year" is the block-reward halving)
_PC_INTRANS_WORD = r"(?:gained|gains|dropped|drops|advanced|advances|doubled|tripled)"
# A business year's recovery: "Hasbro rebounded after a weak 2024, posting record sales"
_PC_BIZ_PERIOD_NOT = (
    r"(?!\s+(?:after|from)\s+(?:a|an)\s+(?:weak|tough|difficult|rough|slow|poor|bad|"
    r"disappointing|soft|challenging|sluggish|down)\s+(?:(?:19|20)\d\d|year|quarter|season|"
    r"holiday|period|first|second|third|fourth|half|fiscal)\b)"
)
# Closing on a RIVAL is market share: "Apple gained on Huawei in China's premium segment",
# "Ford gained on Tesla in U.S. EV sales", "gained on its rivals"
_PC_RIVAL_NOT = (
    r"(?!\s+on\s+(?:(?-i:[A-Z])[\w.&'’-]*(?:\s+(?-i:[A-Z])[\w.&'’-]*){0,2}\s+"
    r"(?:in|among|across|with|for|at)\b|(?:its\s+|their\s+)?(?:rivals?|competitors?|peers|"
    r"the\s+competition|the\s+(?:market\s+)?leader)\b))"
)
# soft verbs that count only with a magnitude after a name ("ORCL pulled back 6%",
# "Bitcoin eased 2%", "SOL dumped 15%", "CoreWeave gave back 4%")
_PC_TERM_SOFT_MAG = _pc_alt(_pc_words("eased eases easing dumped dumps dumping pumped pumps "
                                      "pumping") + ("pulled back", "pulls back", "pulling back",
                                                    "gave back", "gives back", "giving back"))
# "edged up" after a NAME needs a magnitude, a clause end or a cause / time: "Delta edged
# up its earnings outlook", "Apple Moves Up iPhone Fold Launch" are schedules and guidance
_PC_UPDOWN_TAIL = (
    rf"(?:\s+(?:{_PC_ADV}\s+)?(?:{_PC_PCT_MOVE}|{_PC_FOLD})|\s+to\s+{_PC_LEVEL_ANY}|"
    rf"{_PC_CLAUSE_END}|(?={_PC_FOLLOW_WORD}))"
)
# "higher" / "lower" after a NAME: at a clause end, before a cause / time / level, never
# before a noun ("pushed higher subscription prices") or a ranking ("moved higher in the
# J.D. Power rankings")
_PC_HIGHER_TERM = (
    rf"{_PC_HIGHER}(?:{_PC_CLAUSE_END}|(?={_PC_FOLLOW_WORD})|(?=\s+(?:in\s+{_PC_MAG_IN_OK}|"
    r"into\s+the\s+(?:weekend|close|open|end|holidays?|break)|toward|towards|above|below|past|"
    rf"through|near|again|to\s+(?:{_PC_CUR}|\d|(?:a|an|its|their)\s+(?:new\s+|fresh\s+)?"
    r"(?:record|high|all-time|\d+-)))))"
)
_PC_REL_LEAD = (rf"(?:(?:sits|sit|sat|sitting|stands|stood|standing|trades|traded|trading|"
                rf"remains|remained|hovers|hovered|hovering)\s+(?:{_PC_ADV}\s+)?)?")
# "pulled back from its record", "bounced off its lows"
_PC_OFF_RECORD = (
    r"(?:pulled|pulls|pulling|backed|backs|retreated|retreats|bounced|bounces|rebounded|"
    r"rebounds|slipped|slips|dipped|dips|eased|eases)\s+(?:back\s+)?(?:off|from)\s+"
    r"(?:its|their|a|the)\s+(?:(?:recent|record|all-time|52-week|(?:19|20)\d\d|new|fresh|"
    rf"intraday|post-IPO|{_PC_MONTHS})\s+)?(?:record|highs?|peaks?|lows?|bottom)"
    rf"(?:\s+(?:highs?|close|level))?(?:{_PC_CLAUSE_END}|(?={_PC_FOLLOW_WORD}))"
)
# a strong subject (a symbol or a coin) that eases, hovers or lags at a clause end, before
# a cause / time, or beside a rival's move ("ETH advanced while BTC lagged.")
_PC_SX_SOFT = (
    r"(?:eased|eases|hovered|hovers|hovering|lagged|lags|lagging|trailed|trails|weakened|"
    r"weakens|firmed|firms|steadied|steadies|stabilized|stabilised|wobbled|wavered|cooled|"
    r"faded|fades|dropped|drops|advanced|advances|gained|gains|pulled\s+back|pulls\s+back|"
    r"gave\s+back)"
)
_PC_WHILE_PRICE = (
    r"\s+while\s+(?-i:[A-Z])[\w.&-]*(?:\s+(?-i:[A-Z])[\w.&-]*){0,2}\s+"
    rf"(?:{_PC_INTRANS_END}|{_PC_INTRANS_WORD}|lagged|lags|trailed|trails|eased|eases|weakened|"
    rf"weakens|faded|stalled)\b{_PC_CLAUSE_END}"
)
_PC_TERM_ADJ_TIME = r"(?:recent|latest)"
_PC_TERM_ADJ_PACE = (r"(?:sharp|steep|sudden|brief|daily|weekly|monthly|intraday|"
                     r"overnight|post-earnings|year-to-date|double-digit|single-digit|wild|"
                     r"furious|violent|week-long|month-long)")
# after "recent" / "latest", only nouns a business does not also have ("Snap's recent
# rebound with small advertisers", "Hasbro's recent rebound came from Magic")
_PC_TERM_MNOUN_STRICT = (r"(?:rally|rallies|sell-?off|selloff|slide|slump|plunge|tumble|slip|"
                         r"dip|pullback|correction|swoon|rout|drawdown|run-?up|crash)")
_PC_VALUE_NOUN = r"(?:market\s+(?:value|cap|capitalization|valuation)|valuation)\b"
_PC_TERM_ROWS: List[Tuple[str, str]] = [
    # "Bitcoin fell 3%", "CoreWeave Gains 4% on …", "BTC is up 40%", "CoreWeave climbed as
    # much as 9% intraday", "CoreWeave is now 40% below its June peak", "Bitcoin Edges
    # Higher", "Bitcoin's up 5%", "CoreWeave lost half its value"
    ("term_move_magnitude",
     rf"<<S>>(?:\s+{_PC_AUX}){{0,3}}\s+(?:{_PC_CLOSE_MOVE}|(?:(?:{_PC_HARD}|{_PC_TERM_SOFT_MAG})\s+(?:{_PC_ADV}\s+)?"
     rf"{_PC_MAG}|{_PC_MAGONLY}\s+(?:{_PC_ADV}\s+)?{_PC_MAG}{_PC_MAG_ONLY_NOT})|"
     rf"{_PC_UPDOWN}|{_PC_REL_LEAD}{_PC_REL}|{_PC_DIR_VERB}\s+(?:up|down)\b{_PC_UPDOWN_TAIL}|"
     rf"(?:{_PC_HL_VERB}|{_PC_HARD}|{_PC_PUSH})\s+(?:{_PC_HL_ADV}\s+)?{_PC_HIGHER_TERM}|"
     # "gapped up at the open", "has gone parabolic", "delivered a 200% return since its IPO"
     r"(?:gapped|gaps|gapping)\s+(?:up|down|higher|lower)\b|(?:gone|went|goes|going)\s+parabolic|"
     # "ended the week in the red", "is in the red for the week" (never "for the year": a
     # full-year net loss reads the same)
     r"(?:ended|finished|closed)\s+the\s+(?:week|day|session)\s+(?:in\s+the\s+(?:red|green)|"
     r"higher|lower)|(?:(?:slipped|fell|dipped|moved|turned|went|swung|sank|tipped)\s+)?"
     r"(?:into|in)\s+the\s+(?:red|green)\s+(?:for|on)\s+the\s+(?:week|day|session|month)\b|"
     rf"(?:delivered|generated|produced|posted)\s+(?:a|an)\s+(?:{_PC_ADV}\s+)?{_PC_PCT}\s+"
     r"(?:total\s+)?return\b(?!\s+on\s+(?:average\s+)?(?:equity|capital|invested|assets|"
     r"investment|tangible)\b))(?![\w-])|"
     rf"<<B>>['’]s\s+(?:(?:now|still|already)\s+)?{_PC_UPDOWN}"),
    # "CoreWeave Rallies on Microsoft Deal", "ETH rallied after the upgrade", "CoreWeave
    # slides as investors weigh …" — an intransitive move that ends the clause or is
    # followed by a cause / time phrase (never "rose to prominence", "jumped at the chance").
    ("term_move_intransitive",
     rf"<<S>>(?:\s+{_PC_AUX}){{0,3}}\s+(?:{_PC_INTRANS_END}\b{_PC_BIZ_PERIOD_NOT}"
     rf"(?:{_PC_CLAUSE_END}|(?={_PC_FOLLOW_WORD}))|"
     rf"{_PC_INTRANS_WORD}\b{_PC_RIVAL_NOT}(?={_PC_FOLLOW_NARROW}))"),
    # "CRWV +6% after …"
    ("term_signed_pct", rf"<<S>>\s*(?:\(\s*)?{_PC_SIGNED}"),
    # "CoreWeave's 2.2% slip", "Bitcoin's rally stalled", "Solana's 15% weekly gain",
    # "Bitcoin's surge to $120,000", "Bitcoin's breakout above $120,000"
    ("term_possessive_move",
     r"<<B>>['’]s\s+(?:"
     rf"{_PC_PCT}\s+(?:{_PC_TERM_ADJ}\s+)?(?:{_PC_TERM_MNOUN}|gains?|loss|losses)|"
     rf"{_PC_TERM_ADJ_TIME}\s+{_PC_TERM_MNOUN_STRICT}|{_PC_TERM_ADJ_PACE}\s+{_PC_TERM_MNOUN}|"
     r"(?:rally|rallies|sell-?off|selloff|slump|plunge|tumble|pullback|swoon|rout|"
     r"drawdown|run-?up|slide(?!\s+(?:decks?|shows?|presentations?)\b))"
     rf")\b{_PC_ABOUT_ELSE}|"
     # "Solana's ATH of $293", "CoreWeave's 52-week low is $33"
     rf"<<B>>['’]s\s+{_PC_NAMED_HL}|"
     rf"<<B>>['’]s\s+(?:{_PC_TERM_MNOUN}|breakout|breakdown|move|run)\s+"
     r"(?:to|above|below|past|beyond|through|under|toward|towards|near|over|back\s+(?:to|above|below))\s+"
     rf"(?:{_PC_LEVEL_ANY}|(?:a|an|its|their|the)\s+{_PC_HIGHLOW})"),
    # "Bitcoin dipped below $60,000", "BTC reclaimed $60K", "CoreWeave hit an all-time high",
    # "Oracle closed at a record on Tuesday", "At about $130 a share, CoreWeave is valued …"
    ("term_level",
     rf"<<S>>(?:\s+{_PC_AUX}){{0,3}}\s+(?:{_PC_LEVEL_NAME}|{_PC_OFF_RECORD})|"
     rf"<<SX>>(?:\s+{_PC_AUX}){{0,3}}\s+(?:{_PC_RECORD_ANY}|{_PC_HIGHLOW_BARE}|{_PC_FROM_TO})|"
     r"(?:^|(?<=[.!?;]\s))At\s+(?:about\s+|around\s+|roughly\s+|nearly\s+)?"
     rf"{_PC_LEVEL}(?:\s+(?:a|per)\s+share)?\s*,\s+<<N>>\s+(?:is|trades|now)\b"),
    # "CoreWeave, down 40% from its high, …", "Bitcoin, now at $110,000, …"
    ("term_appositive_move",
     r"<<S>>\s*,\s+(?:already\s+|now\s+|still\s+|currently\s+)?(?:(?:up|down)\s+"
     rf"(?:{_PC_ADV}\s+)?{_PC_PCT_MOVE}|(?:trading\s+)?(?:at|near|around|above|below)\s+{_PC_LEVEL_ANY})"),
    # "The news sent Bitcoin higher", "lifted CoreWeave to a record high"
    ("term_causative",
     r"\b(?:sent|send|sends|sending|lifted|lift|lifts|lifting|pushed|push|pushes|pushing|"
     r"drove|drive|drives|driving|propelled|propels|propelling|dragged|drag|drags|dragging|"
     r"knocked|knocks|knocking|took|take|takes|taking)\s+<<B>>"
     # never a ranking: "drove Tesla lower in Consumer Reports' rankings"
     r"(?:['’]s\s+(?:shares|stock))?\s+(?:(?:higher|lower)(?![\w-])"
     rf"(?!\s+(?:in|among|across|within)\s+(?!{_PC_MAG_IN_OK}))|"
     rf"to\s+(?:a|an|its|their)\s+{_PC_HIGHLOW})"),
    # "CoreWeave's valuation surged past $100 billion"
    # "CoreWeave's valuation surged past $100 billion", "Ethereum market cap rose", "Oracle's
    # value soared by $250 billion", "CoreWeave's market cap of $65 billion is down from …"
    ("term_valuation_move",
     r"(?:<<B>>(?:['’]s)?\s+(?:market\s+(?:cap|value|capitalization)|valuation)\b"
     r"(?!\s+(?:of|on|for)\b)"
     rf"(?:{_PC_SEP}{_PC_TOKEN}){{0,3}}?\s+"
     r"(?:rose|fell|surged|soared|jumped|climbed|swelled|ballooned|shrank|plunged|tumbled|"
     r"slid|dropped|sank|topped|crossed|surpassed|passed|eclipsed|hit|reached|doubled|"
     r"tripled|halved|collapsed)\b|"
     r"<<B>>['’]s\s+value\b(?!\s+(?:proposition|chain|to|for|of|in)\b)"
     rf"(?:\s+{_PC_AUX}){{0,2}}\s+(?:rose|fell|surged|soared|jumped|climbed|swelled|ballooned|"
     r"shrank|plunged|tumbled|slid|dropped|sank|doubled|tripled|halved|collapsed)\b|"
     rf"<<B>>['’]s\s+{_PC_MCAP_DOWN_FROM})" + _PC_FUNDING),
    # "Nvidia hit $5 trillion in market value", "Netflix shed $40 billion in market value",
    # "Nvidia briefly topped a $5 trillion valuation", "Ethereum overtook Mastercard in
    # market value"
    ("term_market_value",
     rf"<<S>>(?:\s+{_PC_AUX}){{0,3}}\s+(?:"
     rf"(?:{_PC_HARD}|{_PC_MAGONLY}|topped|tops|hit|hits|crossed|crosses|reached|reaches|"
     r"surpassed|surpasses|passed|passes|eclipsed|eclipses|breached|erased|erases|"
     r"wiped\s+out)\s+(?:about\s+|nearly\s+|roughly\s+|more\s+than\s+|over\s+|almost\s+)?"
     rf"(?:a\s+)?{_PC_CUR}\s?\d[\d.,]*\s*(?:trillion|billion|tn|bn|[tb])\b(?:-dollar)?\s+"
     rf"(?:(?:in|of)\s+)?{_PC_VALUE_NOUN}|"
     r"(?:overtook|overtakes|overtaken|surpassed|surpasses|passed|passes|eclipsed|eclipses|"
     r"leapfrogged|leapfrogs|topped|tops)\s+(?:[\w&.'’-]+\s+){1,3}?(?:in|by)\s+"
     rf"{_PC_VALUE_NOUN}|"
     # "is now worth $60 billion, down from $90 billion"
     rf"worth\s+(?:{_PC_LVL_ADV}\s+)?{_PC_CUR}\s?\d[\d.,]*\s*(?:trillion|billion|tn|bn|[tb])\b"
     rf"\s*,?\s+(?:down|up)\s+(?:{_PC_ADV}\s+)?(?:{_PC_PCT}\s+)?from\b)"),
    # "Palantir has outpaced the Nasdaq", "Ether lagged Bitcoin for a third straight month"
    # (never "Solana outpaced Ethereum in DEX volume")
    ("term_vs_index",
     rf"<<S>>(?:\s+{_PC_AUX}){{0,3}}\s+(?:(?:out|under)perform(?:s|ed|ing)?|lagged|lags|"
     r"lagging|trailed|trails|trailing|outpaced|outpaces|outpacing|outran|outruns|beat|beats|"
     r"beating|trounced|trounces)\s+"
     + _PC_INDEX_OBJ_TERM + _PC_VS_OBJ_NOT + _PC_VS_TAIL),
    # A symbol or a coin (never a company name) easing, hovering or lagging: "ORCL pulled
    # back after …", "Bitcoin hovered.", "ETH advanced while BTC lagged.", "Bitcoin traded
    # flat over the weekend", "Ether weakened against Bitcoin", "TRUMP got cut in half",
    # "XRP has 5x'd since November"
    ("term_soft_move",
     rf"<<SX>>(?:\s+{_PC_AUX}){{0,3}}\s+(?:"
     rf"{_PC_SX_SOFT}\b(?:{_PC_CLAUSE_END}|(?={_PC_FOLLOW_NARROW})|(?={_PC_WHILE_PRICE}))|"
     r"(?:traded|trades|trading|closed|ended|finished|held|remained|remains|stayed|stays|was|"
     r"is|were|are)\s+(?:flat|sideways|unchanged|little\s+changed|range-?bound)\b|"
     r"(?:weakened|weakens|strengthened|strengthens|firmed|firms|slipped|slips|eased|eases|"
     r"fell|rose|gained|lost|dropped|climbed|rallied)\s+(?:against|versus|vs\.?)\s+"
     rf"{_PC_INDEX_OBJ_TERM}(?!['’])|"
     r"(?:got|gets|was|were|been)\s+cut\s+in\s+half\b|"
     r"(?:\d+|two|three|four|five|ten)x['’]?(?:d|ed)\b)"),
    # "The recent pullback in CoreWeave makes the valuation more reasonable" — a move IN the
    # name itself, followed by its verb or a clause end (never "a decline in CoreWeave
    # revenue", "a pullback in CoreWeave's capex")
    ("term_move_in",
     rf"\b(?:the|a|an|this|that)\s+(?:(?:{_PC_PCT}|{_PC_TERM_ADJ})\s+){{0,2}}"
     r"(?:pullback|sell-?off|selloff|rally|slide|slump|plunge|drop|decline|rebound|surge|rout|"
     r"swoon|correction|run-?up|rise|jump|dip|fall|slip|tumble)\s+in\s+<<N>>"
     r"(?=\s*(?:$|[,.;:!?—–)])|\s+(?:makes?|made|left|leaves|came|comes|followed|follows|has|"
     r"have|had|is|was|could|may|might|would|will|offers?|creates?|created|gives?|gave|"
     r"presents?|looks?|seems?|appears?|reflects?|raises?|raised|wiped|erased|sent|pushed|"
     r"dragged|lifted|after|on|amid|since|following|this|last|today|yesterday)\b)"),
]
# Rows compiled only for a COIN card (a terms list carrying a pair symbol such as
# "BTCUSD"): on a coin card "its price" and "the price of X" can only be the coin's.
_PC_COIN_ROWS: List[Tuple[str, str]] = [
    # "The price of Bitcoin fell 4%", "Prices for Bitcoin fell", "The SOL price surged",
    # "BTC/USD slipped below $110,000", "The ETH/BTC ratio fell", "Its price fell 8%"
    ("coin_price_subject",
     r"(?:\bthe\s+price\s+of\s+(?:one\s+)?<<N>>|\bprices?\s+(?:for|of)\s+<<N>>|"
     r"\bthe\s+<<N>>\s+price\b|"
     r"<<N>>\s*/\s*(?:USDT?|USDC|BTC|ETH)\b(?:\s+(?:ratio|pair|cross|rate))?|"
     rf"\b(?:its|their)\s+price{_PC_PRICE_NOT})(?:\s+{_PC_AUX}){{0,3}}\s+{_PC_VERB}"),
    # "Price action was choppy", "Solana's price surge", "XRP's price spike"
    ("coin_price_noun",
     r"\bprice\s+action\b|"
     rf"<<B>>['’]s\s+price\s+(?:(?:{_PC_PCT}|{_PC_ADJ})\s+){{0,2}}(?:{_PC_PNOUN}|spikes?|moves?)\b"),
    # "Bitcoin Eyes $120K", "One Bitcoin now costs more than $110,000", "ETH is priced at
    # $4,000", "Bitcoin around $110K as ETF flows slow", "With Bitcoin above $100,000"
    ("coin_level",
     rf"<<S>>(?:\s+{_PC_AUX}){{0,3}}\s+(?:(?:eyes|eyed|eyeing|costs|cost|costing|priced)"
     rf"(?:\s+{_PC_LEVEL_PREP}){{0,3}}\s+{_PC_LEVEL_ANY}|"
     rf"(?:above|below|near|around|at|over|under)\s+{_PC_LEVEL_ANY})|"
     r"\bwith\s+<<N>>\s+(?:now\s+|still\s+|trading\s+)?(?:above|below|near|around|at|over|under)"
     rf"\s+{_PC_LEVEL_ANY}"),
    # "Bitcoin is having its best month since 2020", "posted its worst quarter" (a company's
    # best quarter is its sales)
    ("coin_best_period",
     rf"<<S>>(?:\s+{_PC_AUX}){{0,3}}\s+(?:posted|posts|had|has|have|having|logged|logs|recorded|"
     r"notched|saw|sees|suffered|suffers|enjoyed|enjoys|(?:on\s+(?:pace|track|course)|"
     r"headed|set)\s+for)\s+(?:its|their)\s+(?:best|worst)\s+(?:day|week|month|quarter|year|"
     r"session|stretch|start)\b" + _PC_NOT_FOR_OTHER),
]

_PC_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,14}")
_PC_COIN_PAIR = re.compile(r"[A-Z0-9]{2,12}USD[TC]?")
# A non-ticker term that is one of these is no name at all (callers never send one; this
# is the belt to their braces).
_PC_TERM_STOP = frozenset({
    "the", "a", "an", "of", "on", "in", "for", "and", "or", "to", "at", "by", "it", "its",
    "is", "as", "shares", "stock", "price", "coin", "token",
})
# Symbols that are ordinary prose ("A surge after …", "AI surged as a priority", "IT
# spending"): never a subject; the company's name still is.
_PC_TICKER_STOP = frozenset({
    "AI", "IT", "US", "UK", "EU", "EV", "IPO", "ETF", "CEO", "CFO", "GDP", "CPI", "USA",
})
# Small words inside a name ("Bank of America", "Johnson & Johnson") keep their case.
_PC_NAME_SMALL = frozenset({
    "of", "and", "the", "de", "du", "la", "le", "des", "for", "in", "on", "at", "y", "e", "&",
    "und", "von", "van", "der",
})
# Coin nicknames a scope's names do not carry ("Ether slid 8%" on the ETHUSD card).
_PC_ALIASES = {"ETH": ("Ether",), "ETHUSD": ("Ether",), "Ethereum": ("Ether",)}


def _pc_esc(text: str) -> str:
    """re.escape, with a straight and a curly apostrophe matching each other."""
    return "".join("['’]" if ch in "'’" else re.escape(ch) for ch in text)


def _term_regex(term: str) -> str:
    """A ticker ("CRWV", "BRK-B", "ETH") matches exactly as written; a name matches only
    when capitalised ("CoreWeave", "COREWEAVE" — never "coreweave"), so "NET", "ON" and
    "HAS" never match prose and "first quarter" is never "First Solar". A name's straight
    and curly apostrophes are one ("Lowe’s"), and "amazon.com" is also "Amazon"."""
    if _PC_TICKER.fullmatch(term) and re.search(r"[A-Z]", term):
        return "(?-i:" + re.escape(term) + ")"
    suffix = ""
    if term.lower().endswith(".com") and len(term) > 4:
        term, suffix = term[:-4], r"(?:\.com)?"
    words = []
    for i, word in enumerate(term.split(" ")):
        if i and word.lower() in _PC_NAME_SMALL:
            words.append(_pc_esc(word))                          # "of", "&": any case
        elif word[0].islower() and any(c.isupper() for c in word[1:]):
            words.append("(?-i:" + _pc_esc(word) + ")")          # eBay, iRobot: as written
        elif word[0].isalpha():
            words.append("(?-i:" + re.escape(word[0].upper()) + ")" + _pc_esc(word[1:]))
        else:
            words.append(_pc_esc(word))
    return r"\s+".join(words) + suffix


def _clean_terms(terms: object) -> Tuple[str, ...]:
    """Usable, de-duplicated terms (+ coin aliases); anything malformed is skipped."""
    if isinstance(terms, str):
        terms = (terms,)
    try:
        items = list(islice(iter(terms), 24))       # type: ignore[call-overload]
    except TypeError:
        return ()
    out: List[str] = []
    for t in items:
        if not isinstance(t, str):
            continue
        t = " ".join(t.split())
        if not t or len(t) > 60 or not re.search(r"[A-Za-z0-9]", t):
            continue
        is_ticker = bool(_PC_TICKER.fullmatch(t))
        if not is_ticker and t.lower() in _PC_TERM_STOP:
            continue
        if is_ticker and (len(t) == 1 or t in _PC_TICKER_STOP):
            continue
        for x in (t, *_PC_ALIASES.get(t, ())):
            if x not in out:
                out.append(x)
    return tuple(out)


# The card's own names are matched in TWO steps, so the big rows compile ONCE (two variants:
# equity and coin) instead of once per scope. Compiling them per scope cost ~0.2 s of CPU and
# ~1 MB each, on the event loop, for every ticker the sweeper and the feed touched (review
# 2026-10-09). Step 1 finds the scope's names with a small per-scope regex and swaps each for
# a sentinel ("QZXSYM" for the stock itself — a symbol, or any name on a coin card —
# "QZXNAME" for a company name); step 2 runs the rows, built around the sentinels, over that
# text and maps every match back to the original span. Look-behinds and look-aheads around a
# name see exactly the text they saw before: only the name itself is replaced.
_PC_SENT_STRONG = "QZXSYM"
_PC_SENT_NAME = "QZXNAME"


@lru_cache(maxsize=2)
def _term_patterns(coin: bool) -> Tuple[Tuple[str, "re.Pattern[str]"], ...]:
    """The subject-term rows, compiled once per variant (equity / coin card)."""
    guard = "(?=Q)"
    named = "(?-i:" + _PC_SENT_NAME + "|" + _PC_SENT_STRONG + r")(?![\w-])"
    s_named = "(?-i:" + _PC_SENT_STRONG + r")(?![\w-])"
    bare = guard + _PC_TERM_LB + named
    obj = r"(?:['’]s)?\s+(?:" + (_PC_TERM_OBJ_COIN if coin else _PC_TERM_OBJ_EQUITY) + ")"
    # "CoreWeave (CRWV) rose", "CoreWeave (NASDAQ: CRWV)", and "(CRWV) rose" for a symbol
    paren = r"(?:\s*\((?:[A-Za-z]+:\s*)?(?-i:[A-Z][A-Z0-9.-]{0,9})\))?\)?"
    # after "the" only with its noun: "The TRUMP token is down 80%", "the TRUMP memecoin"
    the_form = (r"(?<=\bthe\s)" + guard + named + r"\s+(?:(?:token|coin|memecoin|meme\s+coin|"
                r"cryptocurrency)\b|stock\b" + _PC_STOCK_NOT + r"|shares\b" + _PC_SHARES_NOT + ")")
    subj = "(?:" + bare + paren + "(?:" + obj + ")?|" + the_form + ")"
    # The STOCK itself — a symbol, a coin, or "<name> shares": "GLD hit a record", never
    # "Delta hit a record on Sunday" (passengers)
    strong = ("(?:" + guard + _PC_TERM_LB + s_named + paren + "(?:" + obj + ")?|"
              + bare + paren + obj + "|" + the_form + ")")
    rows = list(_PC_TERM_ROWS) + (list(_PC_COIN_ROWS) if coin else [])
    return tuple(
        (name, re.compile(
            tpl.replace("<<SX>>", strong).replace("<<S>>", subj).replace("<<B>>", bare)
            .replace("<<N>>", guard + named),
            _I))
        for name, tpl in rows
    )


@lru_cache(maxsize=4096)
def _term_finder(terms: Tuple[str, ...]) -> Tuple["re.Pattern[str]", "re.Pattern[str]", bool]:
    """(every name of the scope, its STRONG names, coin card?) — small per-scope regexes.

    STRONG = the stock itself: a symbol (upper case), or any name on a coin card."""
    coin = any(_PC_COIN_PAIR.fullmatch(t) for t in terms)
    ordered = sorted(terms, key=len, reverse=True)
    finder = re.compile(
        r"(?<!\w)(?:" + "|".join(_term_regex(t) for t in ordered) + r")(?![\w-])", _I)
    strong_terms = terms if coin else tuple(
        t for t in terms if _PC_TICKER.fullmatch(t) and re.search(r"[A-Z]", t))
    strong = re.compile(
        "(?:" + "|".join(_term_regex(t) for t in sorted(strong_terms, key=len, reverse=True))
        + ")" if strong_terms else r"(?!x)x", _I)
    return finder, strong, coin


def _swap_terms(
    text: str, terms: Tuple[str, ...],
) -> Tuple[str, List[Tuple[int, int, int, int]], bool]:
    """``text`` with each of the scope's names swapped for its sentinel, the replacements as
    (new start, new end, old start, old end), and whether this is a coin card."""
    finder, strong, coin = _term_finder(terms)
    parts: List[str] = []
    swaps: List[Tuple[int, int, int, int]] = []
    last = 0
    shift = 0
    for m in finder.finditer(text):
        sentinel = _PC_SENT_STRONG if strong.fullmatch(m.group(0)) else _PC_SENT_NAME
        parts.append(text[last:m.start()])
        new_start = m.start() + shift
        parts.append(sentinel)
        swaps.append((new_start, new_start + len(sentinel), m.start(), m.end()))
        shift += len(sentinel) - (m.end() - m.start())
        last = m.end()
    parts.append(text[last:])
    return "".join(parts), swaps, coin


def _to_original(pos: int, swaps: Sequence[Tuple[int, int, int, int]], end: bool) -> int:
    """A position in the swapped text → the same position in the original text."""
    shift = 0
    for n_start, n_end, o_start, o_end in swaps:
        if n_end <= pos:
            shift = o_end - n_end
            continue
        if n_start < pos < n_end:             # inside a sentinel: the whole name
            return o_end if end else o_start
        break
    return pos + shift


def price_term_patterns(terms: Sequence[str]) -> List[Tuple[str, "re.Pattern[str]"]]:
    """The subject-term rows that apply to ``terms`` (the row-weight test iterates them);
    a coin card (a pair symbol such as "BTCUSD" among the terms) adds the coin rows. They
    are written around sentinels, so they only ever run through ``price_claims``."""
    cleaned = _clean_terms(terms)
    if not cleaned:
        return []
    return list(_term_patterns(_term_finder(cleaned)[2]))


def price_claims(text: str, terms: Sequence[str] = ()) -> List[str]:
    """Phrases that state a share / coin price MOVE or LEVEL, in text order.

    ``terms`` are the card subject's own names (symbol, coin name, company name): they
    let "CoreWeave Gains 4%" or "Bitcoin dipped below $60,000" count. A ticker-like term
    matches case-sensitively, a name only when capitalised and as the verb's subject; a
    one-letter symbol and symbols that are ordinary words in prose ("A", "AI", "IT") never
    count alone. A pair symbol ("BTCUSD") marks a coin card, where "its price" and "the
    price of Bitcoin" can only be the coin's.

    Allowed, never returned: market cap as a SIZE ("a $50B company"), valuation multiples
    ("30x earnings"), analyst price targets, and every fundamental — revenue, EPS,
    per-share figures, share of revenue / market share, share count and buybacks, stock
    pay, in-stock levels, product and commodity prices. Matches are de-duplicated (an
    overlapping later match is dropped). Never raises: a non-str ``text`` gives [].
    """
    if not isinstance(text, str) or not text.strip():
        return []
    spans: List[Tuple[int, int]] = []
    for _name, pat in PRICE_CLAIM_PATTERNS:
        for m in pat.finditer(text):
            if m.end() > m.start():
                spans.append((m.start(), m.end()))
    cleaned = _clean_terms(terms)
    if cleaned:
        swapped, swaps, coin = _swap_terms(text, cleaned)
        for _name, pat in _term_patterns(coin):
            for m in pat.finditer(swapped):
                if m.end() > m.start():
                    spans.append((_to_original(m.start(), swaps, False),
                                  _to_original(m.end(), swaps, True)))
    spans.sort(key=lambda s: (s[0], -s[1]))
    found: List[str] = []
    seen = set()
    reach = -1
    for a, b in spans:
        if a < reach:          # sorted by start: overlaps the last kept span
            continue
        reach = b
        phrase = text[a:b].strip()
        if phrase and phrase not in seen:
            seen.add(phrase)
            found.append(phrase)
    return found


def price_claim_rows(text: str, terms: Sequence[str] = ()) -> List[str]:
    """The names of the rows that match ``text`` the way ``price_claims`` scans it (the
    generic rows over the text, the name rows over the sentinel-swapped text)."""
    if not isinstance(text, str) or not text.strip():
        return []
    names = [n for n, pat in PRICE_CLAIM_PATTERNS if pat.search(text)]
    cleaned = _clean_terms(terms)
    if cleaned:
        swapped, _swaps, coin = _swap_terms(text, cleaned)
        names += [n for n, pat in _term_patterns(coin) if pat.search(swapped)]
    return names


# Compiled at import, not on the first card: the two variants are the expensive part.
_term_patterns(False)
_term_patterns(True)


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
    extra_figures: Iterable[Optional[Figure]] = (),
    subject_terms: Iterable[str] = (),
    report_happened: bool = False,
) -> ConclusionCheck:
    """Everything the service needs to decide: accept, repair once, or reject."""
    sources = [headline or "", *points]
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
