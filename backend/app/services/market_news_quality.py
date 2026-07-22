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


def _is_material(title_lower: str) -> bool:
    return any(k in title_lower for k in _MATERIAL_KEYWORDS)


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
        if not _is_reputable(src):
            tl = title.lower()
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
