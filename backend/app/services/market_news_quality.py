"""
Deterministic quality filter for the general MARKET news corpus.

FMP's news payload carries NO popularity / view-count / engagement signal, so a
"most viewed" ranking is impossible. Instead this trims the high-volume Market
feed to fewer, higher-quality rows **without reordering** — callers keep their
newest-first order, there are just fewer things to scroll:

  1. Collapse syndicated copies of the same wire story (the same headline runs on
     many sites; the service already dedupes exact URLs, but not titles).
  2. Drop PR-wire / sponsored spam (globenewswire, prnewswire, businesswire, …).
  3. Drop obvious listicle / promo noise ("5 stocks to buy", "Is X a buy?") from
     non-reputable publishers — but ALWAYS keep reputable wires, and RESCUE a
     noisy-looking headline that is actually material (a market-moving keyword,
     or a ticker currently trending on Reddit).

Pure and dependency-free (the Reddit "buzz" set is passed in as ``trending_tickers``)
so it is exhaustively testable. MARKET SCOPE ONLY — ticker / crypto / etf / index
feeds are already narrow and must show everything, so they never call this.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Sequence

logger = logging.getLogger(__name__)


# ── Source tiers (matched as lowercase substrings of publisher + site) ────────

# Press-release wires and sponsored content — almost never real market news.
JUNK_SOURCE_MARKERS = (
    "globenewswire", "globe newswire", "prnewswire", "pr newswire", "businesswire",
    "business wire", "accesswire", "access newswire", "newsfile", "einpresswire",
    "ein presswire", "prweb", "issuewire", "24-7 press", "press release",
    "sponsored", "newmediawire", "openpr", "prlog", "send2press",
)

# Reputable wires / outlets — kept regardless of headline shape. Deliberately the
# top tier only: mid-tier houses (Motley Fool, Zacks, Benzinga, Seeking Alpha, …)
# are NOT here, so their genuine coverage passes the noise gate while their
# "3 stocks to buy" listicles get trimmed.
REPUTABLE_SOURCE_MARKERS = (
    "reuters", "bloomberg", "wall street journal", "wsj", "associated press",
    "cnbc", "financial times", "barron", "marketwatch", "the economist", "axios",
    "npr", "new york times", "washington post", "forbes", "business insider",
    "the guardian", "fortune", "politico", "yahoo finance", "the hill", "cnn",
    "abc news", "cbs news", "nbc news", "investing.com", "the wall street journal",
)

# Listicle / promo / clickbait shapes. Applied to the TITLE, and only for
# non-reputable sources. Kept conservative so real news is not swept up.
#
# The count is bounded to 1-2 digits (`\d{1,2}`) on purpose: a bare `\d+` matched
# fund names like "S&P 500 ETF" ("500 ETF") and wrongly dropped real coverage.
# "N things/ways to know" is DELIBERATELY not here — that is the standard shape of
# a legitimate pre-market brief ("5 things to know before Wall Street opens").
_NOISE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # "3 stocks", "5 dividend stocks", "7 AI stocks" — numbered stock listicles
        r"\b\d{1,2}\s+(ai\s+|growth\s+|value\s+|dividend\s+|tech\s+|top\s+|best\s+)?stocks?\b",
        # "5 reasons …", "3 ETFs …" (\d{1,2} so index names like "500 ETF" are safe)
        r"\b\d{1,2}\s+(reasons?|etfs?)\b",
        # "stocks / ETFs to buy | watch | sell | …"
        r"\b(stocks?|etfs?)\s+to\s+(buy|watch|avoid|sell|consider|own)\b",
        # "best / top N stocks | ETFs | picks"
        r"\b(best|top)\s+\d{0,2}\s*(stocks?|etfs?|dividend|growth|ai|value|picks?)\b",
        # "Is X a buy?"
        r"\bis\s+\w[\w.\s&'-]{0,40}?\s+a\s+(buy|sell|good\s+(stock|buy|investment))\b",
        # "Should you buy / sell / invest"
        r"\bshould\s+you\s+(buy|sell|invest)\b",
        # Zacks-style promo: "… Be on Your (Investing) Radar?"
        r"\bbe\s+on\s+your\s+(investing\s+)?radar\b",
        # clickbait
        r"\b(get\s+rich|millionaire|retire\s+rich|horoscope|smart\s+money\s+moves)\b",
    )
)

# ── Off-topic / lifestyle gate — the ONLY rule a reputable outlet cannot bypass ──
#
# Pass 1 runs `_is_noise` only for non-reputable sources, so the entire top tier
# had no title gate at all. That is how "A 158-year-old lawn company says it's a
# lifestyle brand now" (Yahoo Finance) reached the Market card: a consumer feature
# from a wire that auto-qualifies.
#
# Because this is the one rule a top-tier wire cannot bypass, it is deliberately
# tiny and every pattern is ANCHORED to two tokens. Measured over 3,204 unique
# live `news/general-latest` + index rows: 1 hit, 0 false positives. Only
# `lifestyle brand` fired — the rest are category guards for the recurring
# CNBC "Make It" / consumer-vertical shapes, kept because the hole they close is
# structural rather than because the sample happened to contain one.
#
# REJECTED ON MEASURED EVIDENCE — do not re-add these as bare words, each killed a
# real market story in testing:
#   `recipe`      → "…is a recipe for a painful bearish unwind"
#   `\d+-year-old` → "What a 125-Year-Old Bull Market Says About Today's Trading Craze"
#   `vacation`    → "Review & Preview: Vacation's Over" (Barron's)
#   sports tokens → "Pizza Hut makes surprising change… ahead of NFL season"
#                   (media-rights and sponsorship stories are genuine market news)
_OFFTOPIC_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\blifestyle\s+brand\b",
        # CNBC "Make It" first-person shapes. `here's how much` is anchored to
        # i|we so "Here's how much the Fed cut" survives.
        r"\bside\s+hustles?\b",
        r"\bself[-\s]?made\s+(millionaire|billionaire)s?\b",
        r"\bhere'?s\s+how\s+much\s+(i|we)\b",
        r"\bi\s+(quit|left)\s+my\s+(job|career)\b",
        r"\bday\s+in\s+the\s+life\b",
        r"\bwhat\s+it'?s\s+really\s+like\b",
        # Lifestyle verbs only — "best places to invest" and "cities to open
        # stores" are untouched.
        r"\b(places?|cities|towns|states|neighborhoods?)\s+to\s+(live|retire|visit|raise)\b",
        # `cruises?` is deliberately absent: "Carnival's best cruise season ever".
        r"\bbest\s+(restaurants?|hotels?|resorts?|beaches)\b",
        # Two-token, so "travel demand slumps" survives.
        r"\b(travel|vacation)\s+(tips|hacks|deals|guides?|destinations?)\b",
        r"\b(horoscope|astrology|zodiac)\b",
        # "Amazon Prime Day sales jump 12%" survives — needs the adjacent noun.
        r"\b(gift\s+guides?|prime\s+day\s+deals|coupon\s+codes?)\b",
        r"\b(met\s+gala|red\s+carpet|fashion\s+week|celebrity\s+chef)\b",
    )
)

# Market-moving substrings that RESCUE a noisy-looking headline from the drop.
_MATERIAL_KEYWORDS = (
    "fed", "federal reserve", "interest rate", "rate cut", "rate hike", "inflation",
    "cpi", "ppi", "gdp", "jobs report", "payroll", "unemployment", "recession",
    "earnings", "guidance", "revenue", "profit warning", "merger", "acquisition",
    "buyout", "ipo", "sec ", "lawsuit", "antitrust", "tariff", "sanction",
    "downgrade", "upgrade", "bankruptcy", "layoff", "stimulus", "treasury",
    "yield", "default", "recall", "data breach",
)

_TIER_REPUTABLE = 2
_TIER_OTHER = 1


def _source_text(row: Dict[str, Any]) -> str:
    return f"{row.get('publisher') or ''} {row.get('site') or ''}".lower()


def _is_junk(src: str) -> bool:
    return any(m in src for m in JUNK_SOURCE_MARKERS)


def _is_reputable(src: str) -> bool:
    return any(m in src for m in REPUTABLE_SOURCE_MARKERS)


def _is_noise(title_lower: str) -> bool:
    return any(p.search(title_lower) for p in _NOISE_PATTERNS)


def _is_offtopic(title_lower: str) -> bool:
    return any(p.search(title_lower) for p in _OFFTOPIC_PATTERNS)


def _is_material(title_lower: str) -> bool:
    return any(k in title_lower for k in _MATERIAL_KEYWORDS)


def is_material_headline(title: Any) -> bool:
    """Public, defensive wrapper over the material-keyword test.

    Exported because the Insights card ORDERS its citation list by materiality
    (``news_insight_service._corpus_sources``) and must not reach into a private
    name. Kept here so the keyword vocabulary has exactly one home.

    Note what this is NOT for: materiality must never DROP a market article.
    Measured over 800 live ``news/general-latest`` headlines, a lifestyle/off-topic
    regex gate caught one genuine consumer feature and three real market stories
    ("...is a *recipe* for a painful bearish unwind", "Review & Preview:
    *Vacation*'s Over", "What a *125-Year-Old* Bull Market Says..."). Ranking has
    no such failure mode: a non-material story simply sorts later, and still
    appears when there are not enough material ones to fill the list.

    Accepts anything; a non-str is not material rather than an error.
    """
    if not isinstance(title, str):
        return False
    return _is_material(title.lower())


def _row_tickers(row: Dict[str, Any]) -> List[str]:
    sym = row.get("symbol")
    if not isinstance(sym, str):
        return []
    return [s.strip().upper() for s in sym.split(",") if s.strip()]


def _norm_title(title: Any) -> str:
    """Normalise a headline for syndication dedup: lowercase, strip everything but
    alphanumerics + spaces, collapse whitespace. Two syndications of one wire
    story share the exact headline, so this collapses them; different stories keep
    distinct keys."""
    if not isinstance(title, str):
        return ""
    t = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def filter_market_articles(
    rows: Sequence[Dict[str, Any]],
    *,
    trending_tickers: frozenset = frozenset(),
) -> List[Dict[str, Any]]:
    """Return the quality subset of ``rows``, in the SAME order.

    ``trending_tickers`` is an optional set of uppercase symbols currently
    trending on Reddit (ApeWisdom); it only ever RESCUES a noisy-looking headline,
    never drops anything. Never raises — a caller can trust the result or, on a
    programming error, catch and fall back to the unfiltered corpus.
    """
    if not isinstance(rows, (list, tuple)):
        return []
    trending = {str(t).strip().upper() for t in (trending_tickers or ())}

    # Pass 1 — source + noise gate, order preserved.
    survivors: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = row.get("title")
        if not isinstance(title, str) or not title.strip():
            continue  # a title-less row cannot render or dedupe; drop it
        src = _source_text(row)
        if _is_junk(src):
            continue
        tl = title.lower()
        # Applies to EVERY tier, reputable included — the one gate a top-tier wire
        # cannot bypass. Materiality only ever RESCUES: absence of market
        # vocabulary is not evidence of anything (plenty of real headlines read
        # "Warsh Delivers" or "Morning Bid: Six months and counting"), so this
        # must never become a "must contain a market word to survive" rule.
        if _is_offtopic(tl) and not _is_material(tl):
            logger.debug("Market filter: off-topic drop %r (%s)", title, src)
            continue
        if not _is_reputable(src):
            if _is_noise(tl):
                rescued = _is_material(tl) or (
                    bool(trending) and any(t in trending for t in _row_tickers(row))
                )
                if not rescued:
                    continue
        survivors.append(row)

    # Pass 2 — collapse syndicated copies by normalised title, keeping the
    # highest-tier copy (reputable over other; tie → the first / newest one).
    # Order of the kept copies is preserved.
    best_pos: Dict[str, int] = {}
    best_tier: Dict[str, int] = {}
    for i, row in enumerate(survivors):
        key = _norm_title(row.get("title")) or f"__uniq_{i}"
        tier = _TIER_REPUTABLE if _is_reputable(_source_text(row)) else _TIER_OTHER
        if key not in best_pos or tier > best_tier[key]:
            best_pos[key] = i
            best_tier[key] = tier
    keep = set(best_pos.values())
    return [row for i, row in enumerate(survivors) if i in keep]
