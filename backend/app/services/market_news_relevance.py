"""
Market-relevance rule for the Updates "Market" feed: keep market-wide stories, hide
single-company ones — with a verdict that never changes once a row can be read.

TestFlight 1.0 (11), 2026-10-03 — the owner, on the Market tab: "Should only be for
market!" The "Latest Market headlines" card and the Live News timeline led with
"Fire, smoke seen near Aramco facility in Riyadh, witness says". Clarified 2026-10-05:
the Market tab keeps macro / index / sector / market-wide news and drops stories about
one company — that headline included.

WHERE THE COMPANY STORIES COME FROM. The Market feed (`news_cache_service.
_fetch_market_raw`) blends two FMP legs: `news/general-latest`, whose rows carry
`symbol: null` and mix the macro narrative with single-company coverage (recalls, CEO
remarks, deals, an incident at one company's site), and `news/stock?symbols=SPY,ONEQ,
DIA,IWM`, whose rows FMP tags with a broad-index fund. The quality filter
(`market_news_quality`) never asked whether a story is ABOUT the market.

THE RULE — two kinds of evidence, both fixed before a row is first readable:

  1. A headline that cites exactly ONE company with an exchange prefix ("(NASDAQ:
     TSLA)", "(NYSE: BA)") is that company's story — hidden, and decided from the
     headline alone, so it is applied at ingest too ("Tesla (NASDAQ:TSLA) Sinks As
     Market Gains": the market is only the yardstick). An exchange-qualified market
     instrument ("(NYSEARCA: SPY)") is not a company.
  2. The model's scope, recorded ONCE, at ingest. Every new Market row is summarised
     by the existing summary step BEFORE it is written (`news_cache_service.
     _classify_market_ingest`; at most two calls of 25 per refresh, four smaller ones
     on a cold miss), and that call also answers what the article is ABOUT
     (`MARKET_SCOPE_RULE`). Both Market writers are create-only, so a row that already
     exists is never judged again — not even by a cold fetch after a failed read
     (final review 2026-10-07). "company" → hidden
     — the owner's example: an incident at one company's facility is that company's
     story even though it touches oil, while "Oil jumps after attack on Saudi
     facilities" is the market's. "market", "sector" and "unclear" → kept: the
     model's doubt never hides anything.

  Anything else — a row with no ingest-time scope (the call failed, ran out of time,
  or the row predates this field) — is KEPT. It is never re-judged later.

WHY A VERDICT MAY NEVER CHANGE. The feed pages in MARKET-STORY space: a client's next
`offset` is the number of rows it has received (iOS `loadedOffset += dtos.count` —
build 10 included, and build 10 cannot change). If a row a client has already received
is hidden afterwards, every later story moves one place left and the next page skips
one (adversarial review 2026-10-06, reproduced: rows 50-53 never served after the
client's own scroll-time summaries stamped four page-0 rows). So:
  * the scope stamp is written only at INSERT, in the same write that makes the row
    readable (`news_cache_service._build_and_cache_rows`); a later summary — the
    client's scroll window, the sweeper, the pre-warmer — keeps whatever stamp the row
    has and never adds one;
  * the tickers a later summary merges into `related_tickers` are never read here.
    For a Market row, FMP's own symbols are only ever the index basket, so "ingest-
    time FMP symbols only" leaves nothing a company count could use;
  * cashtags ("$DXY") and parenthesised acronyms ("Bank of Korea (BOK)") are not
    evidence: both shapes are as often macro as they are a company, and at ingest
    there was nothing to correct a wrong guess — the same review found central-bank
    and index headlines dropped that way, never to return.
Earlier drafts counted tickers and rescued "market-framed" headlines with a word list;
both read model-merged tickers, so both could flip a verdict after serve. The model's
scope replaced them.

WHERE THE SCOPE IS STORED: appended to the row's `ai_model` provenance stamp as
`<model>|scope=<value>` (`stamp_model_scope`), Market rows only. `ai_model` is written by
the summary step and read by nothing else (no endpoint, no client), so the verdict needs
no migration and never reaches a screen; a dedicated column is the cleaner long-term home.

KNOWN LIMITS (by design):
  * A row written without a scope stays visible for its life (≤ 6 hours unless the
    refresh keeps finding it): fail-open, never a wrong hide.
  * A story naming two companies is whatever the model says it is; the headline rule
    hides only an exact single-company citation.

FLOOR. Hiding stories must never empty a feed that has news: when fewer than
`MIN_MARKET_STORIES` market-wide stories survive, the newest hidden ones are put back
(in feed order) up to the floor, and that is logged at WARNING.

PURE — no I/O, no clock. Never raises: a row it cannot judge is KEPT, with a WARNING.
MARKET SCOPE ONLY — a ticker's feed is about its company by construction.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


#: Below this many market-wide stories the newest hidden ones are put back. Matches
#: `news_insight_service.MIN_CORPUS_ARTICLES`, the card's own "thin window" line.
MIN_MARKET_STORIES = 3

#: Headlines are short; anything past this is not a headline. Capped BEFORE any regex so
#: a pathological row cannot make the scan expensive on the hottest read in the app.
_SCAN_CHARS = 300

# ── Market instruments: an exchange-qualified citation that is not a company ───────
#
# A closed class — index funds, sector / bond / commodity / currency / crypto vehicles —
# so "(NYSEARCA: SPY)" in a headline is never read as one company's story.
MARKET_INSTRUMENT_SYMBOLS: FrozenSet[str] = frozenset({
    # The Market feed's own index basket (news_cache_service.MARKET_INDEX_SYMBOLS).
    "SPY", "ONEQ", "DIA", "IWM",
    # Broad-market and style index funds.
    "QQQ", "QQQM", "VOO", "IVV", "VTI", "ITOT", "SPLG", "RSP", "MDY", "IJH", "IJR",
    "VB", "VO", "VTV", "VUG", "IWB", "IWF", "IWD", "IWV", "SCHB", "SCHX", "VT",
    "ACWI", "VEA", "VWO", "EFA", "EEM", "IEFA", "IEMG", "VXUS",
    # Index, rate and dollar codes as a wire writes them. (Not "COMP": Compass, Inc.)
    "SPX", "INX", "GSPC", "DJI", "DJIA", "DOW", "IXIC", "NDX", "RUT", "VIX", "NYA",
    "SP500", "NASDAQ", "NYSE", "DXY", "TNX", "TYX", "FVX", "IRX",
    # Volatility products.
    "VXX", "UVXY", "SVXY", "VIXY",
    # Sector funds.
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC",
    "SMH", "SOXX", "KRE", "KBE", "XBI", "IBB", "XOP", "XHB", "ITB", "XRT", "JETS",
    "ARKK", "VGT", "VNQ",
    # Rates and credit.
    "TLT", "IEF", "SHY", "AGG", "BND", "LQD", "HYG", "JNK", "TIP", "BIL", "SGOV",
    "GOVT", "EMB", "MUB",
    # Commodities and currencies.
    "GLD", "IAU", "SLV", "USO", "BNO", "UNG", "DBC", "GSG", "PDBC", "CPER", "DBA",
    "GDX", "GDXJ", "OIL", "UUP", "FXE", "FXY", "USD",
    # Crypto: spot funds and bare coin symbols.
    "IBIT", "FBTC", "GBTC", "ARKB", "BITB", "BITO", "ETHA", "ETHE", "FETH",
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BNB", "AVAX", "USDT", "USDC",
    # Leveraged / inverse index funds.
    "SPXL", "SPXS", "SPXU", "SSO", "SDS", "SH", "UPRO", "TQQQ", "SQQQ", "QLD", "PSQ",
    "TNA", "TZA", "UDOW", "SDOW", "SOXL", "SOXS",
    # Country funds.
    "FXI", "MCHI", "KWEB", "EWJ", "EWZ", "INDA", "EWG", "EWU", "EWC", "EWY", "EWT",
    "EWW", "EWA", "EZU", "VGK", "ASHR",
})

#: Not tickers — what a careless citation might hold. Ignored.
_PSEUDO_SYMBOLS: FrozenSet[str] = frozenset({
    "MARKET", "MARKETS", "__MARKET__", "STOCK", "STOCKS", "INDEX", "INDICES",
    "NONE", "NULL", "NA", "TBD", "UNKNOWN",
})

#: Two listed share classes of one company count as ONE company ("(NASDAQ: GOOGL)" and
#: "(NASDAQ: GOOG)" in one headline). Trailing class letters (BRK.B) collapse in
#: `_company_key`.
_SHARE_CLASS_ALIASES: Dict[str, str] = {
    "GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA", "UA": "UAA", "ZG": "Z",
    "LBRDK": "LBRDA",
}

_SYMBOL_SHAPE = re.compile(r"\^?[A-Z0-9][A-Z0-9.\-=]{0,14}")

_EXCHANGES = (
    r"NYSE(?:\s*(?:American|Arca|MKT))?|NASDAQ(?:GS|GM|CM)?|AMEX|NYSEARCA|NYSEAMERICAN|"
    r"BATS|CBOE|OTC(?:QX|QB|MKTS|PK)?|TSXV?|CSE|NEO|ASX|LSE|LON|AIM|HKEX|HKG|SEHK|SGX|"
    r"TYO|TSE|NSE|BSE|ETR|XETRA|FRA|EPA|AMS|BIT|BME|SIX|STO|OSL|CPH|HEL|TASE|BMV|JSE|"
    r"KRX|KOSDAQ|TWSE|SSE|SZSE"
)
#: "(NASDAQ: TSLA)", "(NYSE:BA)", "(NYSEARCA: SPY)". The exchange is case-insensitive
#: ("Nasdaq:"), the symbol is not — "(Nasdaq: stocks rally)" is not a citation. The
#: exchange prefix is the whole point: it is what separates a company citation from a
#: macro token ("$DXY", "Bank of Korea (BOK)", "(OPEC)"), which carries none.
_EXCHANGE_CITATION = re.compile(
    rf"\(\s*(?i:{_EXCHANGES})\s*:\s*\$?([A-Z0-9]{{1,6}}(?:[.\-][A-Z0-9]{{1,3}})?)\s*\)"
)

# ── The model's scope (recorded once, at ingest) ──────────────────────────────────

#: What the Market summary step may answer for "what is this article about" — the enum
#: of `news_cache_service._MARKET_ENRICHMENT_SCHEMA`. Anything else is no answer.
MODEL_SCOPES: Tuple[str, ...] = ("market", "sector", "company", "unclear")
_SCOPE_MARK = "|scope="


def normalize_model_scope(raw: Any) -> Optional[str]:
    """A scope the model gave, case- and space-folded, or None for anything off-list
    (missing, empty, a sentence, a number). None means "no verdict"."""
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return value if value in MODEL_SCOPES else None


def stamp_model_scope(model_name: Any, scope: Any) -> Any:
    """The `ai_model` value for a Market row: ``"<model>|scope=<scope>"``.

    Returns ``model_name`` unchanged when the scope is not one of `MODEL_SCOPES`, so a
    missing or malformed answer writes exactly what the summary step always wrote.
    ⚠️ Called with a FRESH answer only at insert (`_build_and_cache_rows`); a later
    summary passes the row's EXISTING scope (`model_scope(row)`), so it can re-apply a
    stamp but never add or change one (see the module docstring)."""
    value = normalize_model_scope(scope)
    if value is None or not isinstance(model_name, str):
        return model_name
    return f"{model_name.split(_SCOPE_MARK, 1)[0]}{_SCOPE_MARK}{value}"


def model_scope(row: Any) -> Optional[str]:
    """The scope recorded on ``row`` (its `ai_model` stamp), or None when there is none:
    a raw FMP row, a row written without a verdict, a ticker row, an unreadable stamp."""
    if not isinstance(row, dict):
        return None
    stamp = row.get("ai_model")
    if not isinstance(stamp, str) or _SCOPE_MARK not in stamp:
        return None
    return normalize_model_scope(stamp.rsplit(_SCOPE_MARK, 1)[1])


# ── Verdicts ──────────────────────────────────────────────────────────────────────

#: Reasons a story STAYS in the Market feed.
KEEP_REASONS: FrozenSet[str] = frozenset({
    "model_market",       # read at ingest as about the market as a whole
    "model_sector",       # ... or about an industry / a group of companies
    "model_unclear",      # ... or as unclear — doubt never hides a story
    "not_judged",         # no ingest-time verdict: kept for its whole life
    "no_title",           # nothing to judge; other layers decide (iOS drops it)
    "unclassified",       # the classifier failed on this row — fail open
})
#: Reasons a story is HIDDEN from the Market feed.
DROP_REASONS: FrozenSet[str] = frozenset({
    "headline_names_one_company",  # an exchange-qualified citation of one company
    "model_company",               # read at ingest as one company's story
    "not_a_row",                   # not a dict — cannot render, never counted
})


def _title(row: Dict[str, Any]) -> str:
    """The headline of a cache row (`headline`) or a raw FMP row (`title`)."""
    for key in ("headline", "title"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:_SCAN_CHARS]
    return ""


def _norm_symbol(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    sym = raw.strip().upper()
    if sym.startswith("$"):
        sym = sym[1:]
    if not sym or not _SYMBOL_SHAPE.fullmatch(sym) or sym in _PSEUDO_SYMBOLS:
        return None
    return sym


def _is_instrument(sym: str) -> bool:
    if sym in MARKET_INSTRUMENT_SYMBOLS or sym.startswith("^"):
        return True
    if sym.endswith(("=X", "=F")):          # FX / futures codes
        return True
    # Quote-suffixed pairs: BTCUSD, EURUSD, GCUSD (crypto, FX, commodities).
    return len(sym) > 4 and sym.endswith(("USD", "USDT"))


def _company_key(sym: str) -> str:
    """One key per company: share-class suffixes and dual listings collapse."""
    base = re.sub(r"[.\-][A-Z]$", "", sym)
    return _SHARE_CLASS_ALIASES.get(base, base)


def _cited(title: str) -> Tuple[Set[str], Set[str]]:
    """(companies, instruments) the headline cites with an exchange prefix."""
    companies: Set[str] = set()
    instruments: Set[str] = set()
    for m in _EXCHANGE_CITATION.finditer(title):
        sym = _norm_symbol(m.group(1))
        if sym is None:
            continue
        if _is_instrument(sym):
            instruments.add(sym)
        else:
            companies.add(_company_key(sym))
    return companies, instruments


def classify_market_story(row: Any) -> str:
    """The verdict for one feed row, as a reason string from `KEEP_REASONS` or
    `DROP_REASONS`. Accepts a cache row or a raw FMP row. Never raises.

    Reads only the headline and the ingest-time scope stamp — never `related_tickers`,
    which a later summary rewrites (see the module docstring)."""
    if not isinstance(row, dict):
        return "not_a_row"
    try:
        title = _title(row)
        if not title:
            return "no_title"
        companies, instruments = _cited(title)
        if len(companies) == 1 and not instruments:
            return "headline_names_one_company"
        scope = model_scope(row)
        if scope == "company":
            return "model_company"
        if scope is None:
            return "not_judged"
        return f"model_{scope}"
    except Exception as e:  # pragma: no cover - defensive; the helpers do not raise
        logger.warning(
            "Market relevance: could not classify row %r (%s: %s) — kept",
            str(row.get("external_id") or row.get("url") or row.get("id") or "")[:120],
            type(e).__name__, e, exc_info=True,
        )
        return "unclassified"


def is_market_story(row: Any) -> bool:
    """True when ``row`` belongs in the Market feed."""
    return classify_market_story(row) in KEEP_REASONS


def select_market_stories(
    rows: Sequence[Any], *, floor: int = MIN_MARKET_STORIES, label: str = "market"
) -> List[Dict[str, Any]]:
    """The market-wide subset of ``rows``, in the SAME order.

    Non-dict rows are dropped outright. When fewer than ``floor`` rows survive, the
    earliest hidden ones (newest-first input → the newest) are put back in their
    original positions until ``floor`` is met or the hidden rows run out — logged at
    WARNING, because a feed that thin means the upstream mix has gone wrong. ``label``
    names the caller in that log. Never raises.
    """
    if not isinstance(rows, (list, tuple)):
        return []
    candidates = [r for r in rows if isinstance(r, dict)]
    reasons = [classify_market_story(r) for r in candidates]
    keep = [reason in KEEP_REASONS for reason in reasons]
    kept = sum(keep)

    try:
        floor_n = int(floor)
    except (TypeError, ValueError):
        logger.warning(
            "Market relevance (%s): unusable floor %r — using %d", label, floor,
            MIN_MARKET_STORIES,
        )
        floor_n = MIN_MARKET_STORIES
    target = min(max(0, floor_n), len(candidates))
    if kept < target:
        restored = 0
        for i, k in enumerate(keep):
            if kept + restored >= target:
                break
            if not k:
                keep[i] = True
                restored += 1
        logger.warning(
            "Market relevance (%s): only %d of %d stories are market-wide — restored "
            "the %d newest single-company stories to keep the feed at its floor of %d "
            "(hidden: %s)",
            label, kept, len(candidates), restored, target,
            dict(Counter(r for r in reasons if r in DROP_REASONS)),
        )
    elif kept < len(candidates):
        logger.debug(
            "Market relevance (%s): hid %d of %d stories (%s)",
            label, len(candidates) - kept, len(candidates),
            dict(Counter(r for r in reasons if r in DROP_REASONS)),
        )
    return [row for row, k in zip(candidates, keep) if k]


__all__: Iterable[str] = (
    "DROP_REASONS", "KEEP_REASONS", "MARKET_INSTRUMENT_SYMBOLS", "MIN_MARKET_STORIES",
    "MODEL_SCOPES", "classify_market_story", "is_market_story", "model_scope",
    "normalize_model_scope", "select_market_stories", "stamp_model_scope",
)
