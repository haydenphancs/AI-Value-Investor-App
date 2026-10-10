"""Ask Cay AI's financials tool, `check_company_financials`: a company's reported figures.

WHY THIS EXISTS (the "Caydex data first" audit, 2026-10-08). Licensed, already-cached company
figures — statements, margins, the balance sheet, earnings results and dates, analysts'
revenue/EPS estimates, valuation multiples and the fair-value model, segments, dividends and
splits — never reached chat. Only the stock on screen got a thin enrichment; a NORMAL chat, or
any other ticker, got nothing, and the model answered revenue and EPS from memory.

WHAT IT READS — nothing new is fetched here. Only the cache-aside services the Financials and
Overview tabs already use (their in-memory and Supabase tiers and their in-flight dedup), so a
figure here is the figure on the screen:
  * `StockOverviewService.get_key_facts` — the Overview's Key Stats rows (the same builder),
    the statements' and the listing's currencies, the latest annual balance sheet's totals,
    and the fund flags;
  * `GrowthService` (revenue, GAAP diluted EPS, net income, operating income, free cash flow),
    `ProfitPowerService` (margins), `HealthSnapshotService`, `EarningsService` (results against
    estimates, the next report date), `ValuationSnapshotService` (multiples; the Caydex fair
    value only while `DCF_ENABLED`, else the third-party DCF, labelled so),
    `RevenueBreakdownService` (segments), `SignalOfConfidenceService` (dividends) and
    `CorporateActionsService.get_split_rows` (splits);
  * `AnalystService.get_analysis().estimates` ONLY while `analyst_estimates_available()` — the
    loader keeps nothing else from that response: ratings, price targets, grades and their
    distributions never leave it (they are outside the data licence).
Never Gemini, never the overview/index/ETF/crypto detail pipelines (each can call it).

COLD PATH. Every source runs as its own task; the tool waits at most `_BLOCK_WAIT_SECONDS`
(inside the 20 s handler ceiling, `gemini._TOOL_TIMEOUTS`). A source still running reads "not
loaded in this answer" — never zero — and keeps running (strong references in `_side_tasks`)
so the next question finds its cache warm. Concurrent calls for the same (ticker, section) —
parallel specialists — share one build (`_inflight`), and two sections asked in one round share
each SOURCE read (`_source_tasks`, per (source, ticker)): every section reads the key facts, and
a cold key-facts read is the overview's whole fundamentals fan-out.

A build that came back EMPTY because its own upstream legs failed (a degraded health or
valuation card, a fundamentals bundle with no profile or no balance sheet) is "did not load",
never "none reported" — an answered absence is only what a complete build says.

THE RESULT is compact text per period ("FY2025: 391.04B, +2.0% YoY, industry median +5.1%
YoY"), newest first, every block with its period, basis and currency (statements are never
converted; a price-based figure is in the trading currency, a statement figure in the reporting
currency; a missing currency is "not confirmed", never assumed US dollars). NaN, inf, None and
bools are omitted, never shown as 0; a negative figure keeps its sign. It trims itself below the
tool-result cap (`_fit`: oldest periods first, `shortened` stamped) so the blind pruner never
cuts it. No vendor is named anywhere in it (IDENTITY_RULE).

OLDER PERIODS (2026-10-09, eval `hallucination-bait`). The growth and margins lists show only the
newest periods, and read alone they said "the data goes back to Q4 2024" for a company whose
cache holds ~16 fiscal years and ~80 quarters. So every growth and margins block carries
`history` — the first and last fiscal year and quarter of the WHOLE cached series — and the
optional `period` (`chat_tools.normalize_period`: one fiscal year or quarter, never echoed) reads
that period's rows from the same series in the summary, growth and margins sections. An absent
period is answered from the real series: before its first period ("starts at"), after its last
(not reported yet / not ended yet / in the future) or a gap inside it — never "does not exist".
Fiscal is not calendar: `fiscal_calendar` places the period against the calendar from the latest
annual balance sheet's own period end (Apple's fiscal Q3 ends around June).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import re
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from app.services.agents.chat_tools import (
    FINANCIAL_SECTIONS,
    FINANCIALS_TOOL,
    FiscalPeriod,
    normalize_period,
    normalize_section,
    two_digit_year,
)
from app.services.asset_class import detect_asset_class
from app.services.chat_security import neutralize_fences, normalize_text, sanitize_symbol

logger = logging.getLogger(__name__)

# Below the tool-result cap (`GEMINI_TOOL_RESULT_MAX_CHARS`, 8000) so the structural pruner never
# cuts this result blind — plus room for the handler's fixed section note (`chat_tools`), which
# is added after this tool returns.
_BUDGET_MARGIN = 600
_NOTE_RESERVE = 300
# How long one answer waits for a source (inside the 20 s handler ceiling).
_BLOCK_WAIT_SECONDS = 15.0
_ANNUAL_ROWS = 5
_QUARTER_ROWS = 8
_EARNINGS_ROWS = 6
_ESTIMATE_ROWS = 5
_SEGMENT_ROWS = 12
_DIVIDEND_YEARS = 6
_SPLIT_ROWS = 10
_SPLIT_LOOKBACK_YEARS = 10
_LABEL_MAX = 24
_NAME_MAX = 60
_TEXT_MAX = 240
# Beyond these a figure is a unit or digit glitch, not data: omitted (and logged), never shown.
_MONEY_MAX = 1e18
_PER_SHARE_MAX = 1e9
_PCT_MAX = 1e7

_PLACEHOLDERS = frozenset({"", "-", "—", "N/A", "n/a"})

_NOT_LOADED = "not loaded in this answer (still loading) - never treat it as zero or none"
_FAILED = "could not be loaded right now - never treat it as zero or none"
_SHORTENED = ("some periods or detail were left out to fit this answer: anything not listed "
              "was not loaded - never zero or none")

_HOW_TO_READ = (
    "Caydex's financial data, newest period first. FY = fiscal year, Q = fiscal quarter, TTM = "
    "trailing twelve months. Give every figure with its period, its basis and its currency. "
    "Anything under 'unavailable', or absent, was not loaded - never treat it as zero or none. "
    "In Key Stats, 'Neg.' means the multiple is not meaningful because earnings are negative, "
    "and a Dividends value of 'None' means the company pays no dividend. Estimates are analysts' "
    "forecasts, not reported results."
)
_KEY_STATS_BASIS = (
    "Key Stats as shown on the stock's Overview: P/E (TTM) is the live price over trailing-"
    "twelve-month GAAP diluted EPS (EPS (TTM)); P/E (FWD) uses analysts' EPS estimate for the "
    "nearest fiscal year not yet ended (for most of the year that is the current fiscal year). "
    "Share counts, the float and short interest are in the ownership filings data, not here."
)
# A filer whose statements are in another currency than its price (key facts' ``pe_basis``,
# `stock_overview_service.PE_BASIS_PROVIDER` — pinned equal by test; a literal here keeps the
# service import lazy): the Overview's P/E rows are a TTM multiple with price and earnings in ONE
# currency at the latest daily close, never the live price over EPS (TTM) (2026-10-09).
_PE_BASIS_TWO_CURRENCIES = "provider_ttm"
_KEY_STATS_BASIS_TWO_CURRENCIES = (
    "Key Stats as shown on the stock's Overview. The company reports in a different currency "
    "from the one its shares trade in, so neither P/E is the live price over EPS (TTM): P/E (TTM) "
    "is the trailing-twelve-month multiple with price and earnings in the same currency, as of "
    "the latest daily close; P/E (FWD) divides that same close by analysts' EPS estimate for the "
    "nearest fiscal year not yet ended. Share counts, the float and short interest are in the "
    "ownership filings data, not here."
)
# Which Key Stats rows are priced in the TRADING currency and which come from the statements
# (the REPORTING currency). Beta and the P/E ratios carry no currency.
_KS_PRICE_BASED = ("Market Cap", "52-Week High", "52-Week Low", "Dividends")
_KS_STATEMENT_BASED = ("EPS (TTM)",)
# The fundamentals legs each key-facts block is built from (`get_key_facts`'s `degraded`).
_KS_LEGS = frozenset({"profile", "key_metrics", "quarterly_income"})
_BALANCE_LEGS = frozenset({"balance_sheet"})
_SNAPSHOT_INCOMPLETE = ("Some of this card's inputs did not load in this build - anything "
                        "missing was not loaded, never zero.")
_KEY_STATS_NO_PRICE = (" The live price did not load, so Market Cap, the 52-week range, the P/E "
                       "ratios and a payer's dividend yield are not shown here.")
# "On that multiple's own basis", never "the same price and EPS (TTM)": for a two-currency
# filer the P/E (TTM) is the daily-close multiple and EPS (TTM) is in another currency.
_KEY_STATS_EARNINGS_YIELD = (" earnings_yield is 1 / the P/E (TTM) shown here, on that "
                             "multiple's own basis.")
_GROWTH_BASIS = (
    "Fiscal years and fiscal quarters as filed, newest first. EPS is GAAP diluted EPS from the "
    "income statement. Free cash flow is operating cash flow minus capital expenditure. YoY "
    "compares with the same period a year earlier; the peer median is the named peer group's "
    "median YoY for the same period."
)
_MARGINS_BASIS = (
    "Each margin is a percentage of revenue for that fiscal year or quarter, newest first; the "
    "peer median is the named peer group's median for the same period."
)
_HEALTH_BASIS = (
    "Ratios from the latest reported financial statements (as_of is when they were computed); "
    "the balance-sheet totals are from the latest annual balance sheet (period_end)."
)
_BALANCE_BASIS = (
    "Latest annual balance sheet, as reported (period_end); net debt = total debt minus cash and "
    "cash equivalents (short-term investments are not deducted)."
)
_EARNINGS_BASIS = (
    "Quarterly results against analysts' estimates, newest first. EPS here is the figure "
    "compared with estimates - usually adjusted (non-GAAP) - so it can differ from the GAAP "
    "diluted EPS in the growth section; never call that a contradiction. A beat or a miss is "
    "the reported figure against the estimate."
)
_ESTIMATES_BASIS = (
    "Analysts' consensus estimates for future fiscal years: the average, with the low and high, "
    "and how many analysts. Forecasts by analysts, not company-reported results, and never a "
    "rating or a price target. Fiscal years follow the company's own numbering, as in the "
    "growth section; trust each row's period end when naming the year."
)
# Two P/E bases reach chat: the card's (TTM, priced when the card was built — up to a day old)
# and Key Stats' (the live price over EPS (TTM)). Each carries its OWN earnings yield, derived
# from it (`app.utils.earnings_yield`): the eval of 2026-10-09 paired a P/E of 34.1 with the
# card's 3.36% and called one the inverse of the other.
_VALUATION_BASIS = (
    "Multiples from the Price card: trailing twelve months, priced when the card was built "
    "(as_of), not the live price, with the peer median where shown. The card's Earnings Yield is "
    "1 / the card's own P/E. The Key Stats P/E (TTM) and its earnings_yield {key_stats}, so the "
    "two P/E figures, and the two yields, can differ - never call that a contradiction, and "
    "never pair a P/E from one with the yield from the other."
)
# How the Key Stats P/E was priced, for the sentence above — the same choice `_key_stats_block`
# makes for its basis (two currencies → the daily-close multiple; anything else → live).
_KEY_STATS_PE_LIVE = "use the live price"
_KEY_STATS_PE_DAILY_CLOSE = "are priced at the latest daily close (see Key Stats' basis)"
_SEGMENTS_BASIS = (
    "Revenue by business segment as reported for the fiscal year; shares are of reported "
    "revenue. Segments can add up to more than revenue when sales between segments are "
    "eliminated in consolidation."
)
_DIVIDENDS_BASIS = (
    "Trailing-twelve-month yields from the cash dividends and buybacks actually paid; dividends "
    "per share by completed fiscal year, newest first, to 4 decimals as the Signal of "
    "Confidence card shows them (a year marked '(none paid)' is one with no dividend)."
)
_CAYDEX_FV_LABEL = (
    "a model estimate of intrinsic value from projected cash flows - not a price target or a "
    "forecast"
)
_THIRD_PARTY_DCF_LABEL = "a third-party discounted-cash-flow model estimate, not a price target"

# Which sources each section reads. `key_facts` is in every one: it carries the currencies, the
# company name for `resolved_as` and the fund flags.
_SECTION_SOURCES: Dict[str, Tuple[str, ...]] = {
    "summary": ("key_facts", "growth", "margins", "earnings", "valuation"),
    "growth": ("key_facts", "growth"),
    "margins": ("key_facts", "margins"),
    "health": ("key_facts", "health"),
    "earnings": ("key_facts", "earnings"),
    "estimates": ("key_facts", "estimates"),
    "valuation": ("key_facts", "valuation"),
    "segments": ("key_facts", "segments"),
    "dividends": ("key_facts", "dividends", "splits"),
}
# Share counts, the float and short interest are the OWNERSHIP tool's (one float source per
# answer: two reads of the same figure at different times would contradict each other), and
# the live trading day is the price tool's.
_SUMMARY_KEY_STATS = (
    "Market Cap", "P/E (TTM)", "P/E (FWD)", "EPS (TTM)", "Dividends", "Beta",
    "52-Week High", "52-Week Low",
)
_VALUATION_KEY_STATS = ("Market Cap", "P/E (TTM)", "P/E (FWD)", "EPS (TTM)")

# Strong references: a source task outlives the answer that started it (it warms its cache), and
# a bare `ensure_future` result may be garbage-collected mid-flight.
_side_tasks: set = set()
# One build per (ticker, section) at a time — parallel specialists share it.
_inflight: Dict[Tuple[str, str], "asyncio.Task"] = {}
# One read per (source, ticker) at a time — two sections asked in the same round (the capability
# line asks for exactly that) share it. `key_facts` is in every section, and a cold key-facts
# read is the overview's whole fundamentals fan-out (a daily history from 1900 among it).
_source_tasks: Dict[Tuple[str, str], "asyncio.Task"] = {}

# Sentinel the estimates loader returns while the dataset is outside the licence.
_UNLICENSED = object()


class _WithStatus(NamedTuple):
    """A card plus the degraded legs of the build that produced it (`get_*_with_status`)."""
    value: Any
    degraded: List[str]


def _unwrap_status(value: Any) -> Tuple[Any, List[str]]:
    """(card, degraded legs) from a loader's value; a bare card has no recorded status."""
    if isinstance(value, _WithStatus):
        legs = value.degraded if isinstance(value.degraded, (list, tuple)) else []
        return value.value, [d for d in legs if isinstance(d, str)]
    return value, []


# ── Sources (lazy imports: each service pulls its own datastore client) ────────

async def _load_key_facts(sym: str) -> Any:
    from app.services.stock_overview_service import get_stock_overview_service

    return await get_stock_overview_service().get_key_facts(sym)


async def _load_growth(sym: str) -> Any:
    from app.services.growth_service import get_growth_service

    resp, _degraded = await get_growth_service().get_growth_with_status(sym)
    return resp


async def _load_margins(sym: str) -> Any:
    from app.services.profit_power_service import get_profit_power_service

    return await get_profit_power_service().get_profit_power(sym)


async def _load_health(sym: str) -> Any:
    """The card WITH its build status: a card whose legs failed carries only a placeholder
    metric, and without the status that read as "no figures reported"."""
    from app.services.health_snapshot_service import get_health_snapshot_service

    snap, degraded = await get_health_snapshot_service().get_health_snapshot_with_status(sym)
    return _WithStatus(snap, list(degraded or []))


async def _load_earnings(sym: str) -> Any:
    from app.services.earnings_service import get_earnings_service

    return await get_earnings_service().get_earnings(sym)


async def _load_estimates(sym: str) -> Any:
    """ONLY the estimates leave this function. The analysis response also carries the ratings
    half (consensus, targets, grades, distributions, actions) — outside the data licence — and
    nothing from it is kept, even when it holds values."""
    from app.services._analyst_common import analyst_estimates_available

    if not analyst_estimates_available():
        return _UNLICENSED
    from app.services.analyst_service import get_analyst_service

    resp = await get_analyst_service().get_analysis(sym)
    return {
        "available": getattr(resp, "estimates_available", False) is True,
        "periods": list(getattr(resp, "estimates", None) or []),
    }


async def _load_valuation(sym: str) -> Any:
    """The card (with the serve-time fair value) WITH the multiples build's status."""
    from app.services.valuation_snapshot_service import get_valuation_snapshot_service

    snap, degraded = await get_valuation_snapshot_service().get_valuation_snapshot_with_status(sym)
    return _WithStatus(snap, list(degraded or []))


async def _load_segments(sym: str) -> Any:
    from app.services.revenue_breakdown_service import get_revenue_breakdown_service

    return await get_revenue_breakdown_service().get_revenue_breakdown(sym)


async def _load_dividends(sym: str) -> Any:
    from app.services.signal_of_confidence_service import get_signal_of_confidence_service

    return await get_signal_of_confidence_service().get_signal_of_confidence(sym)


async def _load_splits(sym: str) -> Any:
    """``{"rows": the named splits (None = could not look), "unclassified": the dates of share-
    basis changes that are NOT nameable splits (None = could not look)}`` — one derivation, read
    twice through its cache (final review 2026-10-09: an unclassified 1-for-150 reverse split
    used to vanish, and the line said "no stock split")."""
    from app.services.corporate_actions_service import get_corporate_actions_service

    service = get_corporate_actions_service()
    start = _split_window_start()
    rows = await service.get_split_rows(sym, from_date=start)
    unclassified = await service.unclassified_adjustment_dates_or_none(sym, from_date=start)
    return {"rows": rows, "unclassified": unclassified}


# ── Small pure helpers ──────────────────────────────────────────────────────────

def _today_et() -> date:
    from app.utils.market_hours import ET

    return datetime.now(ET).date()


def _split_window_start() -> str:
    """Jan 1 ten years back: a stable window per calendar year, so the splits read keeps hitting
    its own window-keyed cache."""
    return f"{_today_et().year - _SPLIT_LOOKBACK_YEARS}-01-01"


def _get(obj: Any, name: str) -> Any:
    """`obj.name` for a model, `obj[name]` for a dict (a cached payload), else None."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _num(value: Any) -> Optional[float]:
    """A finite number, or None — never a bool, a string, NaN or inf read as data."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    if not math.isfinite(f):
        return None
    return 0.0 if f == 0 else f      # no "-0.0"


def _money(value: Any) -> Optional[str]:
    f = _num(value)
    if f is None:
        return None
    if abs(f) >= _MONEY_MAX:
        logger.warning("chat tool %s: an implausible amount (%.3g) was omitted", FINANCIALS_TOOL, f)
        return None
    a = abs(f)
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{f / div:.2f}{suffix}"
    return f"{f:.2f}"


def _per_share(value: Any) -> Optional[str]:
    f = _num(value)
    if f is None:
        return None
    if abs(f) >= _PER_SHARE_MAX:
        logger.warning("chat tool %s: an implausible per-share figure (%.3g) was omitted",
                       FINANCIALS_TOOL, f)
        return None
    return f"{f:.2f}"


def _dividend_per_share(value: Any) -> Optional[str]:
    """A dividend per share to 4 decimals (the card's precision — a token $0.004 dividend is
    never "0.00"), within the same plausibility bound as `_per_share`; None when unusable."""
    f = _num(value)
    if f is None:
        return None
    if abs(f) >= _PER_SHARE_MAX:
        logger.warning("chat tool %s: an implausible dividend per share (%.3g) was omitted",
                       FINANCIALS_TOOL, f)
        return None
    return f"{f:.4f}"


def _pct(value: Any, *, signed: bool = True, digits: int = 1) -> Optional[str]:
    f = _num(value)
    if f is None or abs(f) >= _PCT_MAX:
        return None
    return f"{f:+.{digits}f}%" if signed else f"{f:.{digits}f}%"


def _text(value: Any, cap: int) -> Optional[str]:
    """Server-held free text (a period label, a segment name, a refusal reason) made safe for a
    tool result: control characters stripped, fence runs broken, whitespace folded, capped."""
    if not isinstance(value, str):
        return None
    t = neutralize_fences(normalize_text(value))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:cap] or None


def _iso_date(value: Any) -> Optional[str]:
    """'YYYY-MM-DD' from an ISO date / timestamp string, or None."""
    if not isinstance(value, str):
        return None
    head = value.strip()[:10]
    try:
        return date.fromisoformat(head).isoformat()
    except ValueError:
        return None


_ANNUAL_RE = re.compile(r"^(?:FY\s*)?(\d{4})$", re.IGNORECASE)
_QUARTER_RE = re.compile(r"^Q([1-4])\s*'?\s*(\d{2}|\d{4})$", re.IGNORECASE)


def _period_label(raw: Any) -> Optional[str]:
    """'2025' → 'FY2025'; a quarter label ("Q1'24") is kept as the screen shows it."""
    t = _text(raw, _LABEL_MAX)
    if not t:
        return None
    m = _ANNUAL_RE.match(t)
    return f"FY{m.group(1)}" if m else t


def _period_key(label: str) -> Optional[Tuple[int, int]]:
    """(fiscal year, quarter) of a period label — 9 for a whole fiscal year, so a year sorts
    after its own quarters. A two-digit year is read with `chat_tools.two_digit_year`."""
    m = _ANNUAL_RE.match(label)
    if m:
        return int(m.group(1)), 9
    m = _QUARTER_RE.match(label)
    if m:
        year = int(m.group(2))
        return (two_digit_year(year, _today_et().year) if year < 100 else year), int(m.group(1))
    return None


def _newest_first(points: Any, label_field: str = "period") -> List[Tuple[str, Any]]:
    """`[(label, point), ...]` newest first. One row per label (the LAST in the service's order
    wins a duplicate — the services send oldest first). Sorted by the parsed period when every
    label parses, else the service's own order reversed. Unlabelled rows are dropped."""
    if not isinstance(points, (list, tuple)):
        return []
    by_label: Dict[str, Tuple[int, Any]] = {}
    for i, p in enumerate(points):
        label = _period_label(_get(p, label_field))
        if label is None:
            continue
        by_label.pop(label, None)       # re-insert so the later duplicate keeps its position
        by_label[label] = (i, p)
    items = [(label, i, p) for label, (i, p) in by_label.items()]
    keys = [_period_key(label) for label, _i, _p in items]
    if items and all(k is not None for k in keys):
        items.sort(key=lambda it: _period_key(it[0]), reverse=True)
    else:
        items.sort(key=lambda it: it[1], reverse=True)
    return [(label, p) for label, _i, p in items]


def _peer_word(level: Any) -> str:
    return level if level in ("industry", "sector") else "peer"


def _statement_currency(kf: Optional[Dict[str, Any]]) -> Optional[str]:
    return (kf or {}).get("statement_currency") if isinstance((kf or {}).get("statement_currency"), str) else None


def _price_currency(kf: Optional[Dict[str, Any]]) -> Optional[str]:
    """The currency the stock TRADES in (the profile's) — what every price-based figure is in."""
    return (kf or {}).get("price_currency") if isinstance((kf or {}).get("price_currency"), str) else None


def _currency_label(kf: Optional[Dict[str, Any]]) -> str:
    ccy = _statement_currency(kf)
    return ccy or "not confirmed (the company's reporting currency)"


def _trading_label(kf: Optional[Dict[str, Any]]) -> str:
    ccy = _price_currency(kf)
    return ccy or "not confirmed (the currency the stock trades in)"


def _kf_degraded(kf: Optional[Dict[str, Any]]) -> frozenset:
    legs = (kf or {}).get("degraded")
    return frozenset(d for d in legs if isinstance(d, str)) if isinstance(legs, (list, tuple)) else frozenset()


def _key_stats_currency(kf: Dict[str, Any], shown: Dict[str, str]) -> Optional[str]:
    """Which currency the shown Key Stats rows are in: price-based rows in the trading currency,
    EPS (TTM) in the reporting currency — one code when both agree."""
    price_rows = [label for label in _KS_PRICE_BASED if label in shown]
    stmt_rows = [label for label in _KS_STATEMENT_BASED if label in shown]
    if not price_rows and not stmt_rows:
        return None
    trading, statements = _price_currency(kf), _statement_currency(kf)
    if trading and statements and trading == statements:
        return trading
    if trading and not stmt_rows:
        return trading
    if statements and not price_rows:
        return statements
    parts = []
    if price_rows:
        parts.append(f"{', '.join(price_rows)}: {_trading_label(kf)}")
    if stmt_rows:
        parts.append(f"{', '.join(stmt_rows)}: {_currency_label(kf)}")
    return "; ".join(parts)


# ── Blocks ──────────────────────────────────────────────────────────────────────
# Each builder returns the block (a dict), or None when the source holds nothing to show — an
# ANSWERED absence, not an outage. A build that came back EMPTY because its own upstream legs
# failed raises `_Incomplete` (named "did not load", never "none reported"); a malformed payload
# raises anything else. The caller degrades that one block either way.


class _Incomplete(Exception):
    """The source answered, but with nothing because its own upstream legs failed."""


def _key_stats_block(kf: Dict[str, Any], labels: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    rows = kf.get("rows") if isinstance(kf.get("rows"), dict) else {}
    listed = kf.get("unavailable") if isinstance(kf.get("unavailable"), list) else []
    unavailable = {u for u in listed if isinstance(u, str)}
    shown: Dict[str, str] = {}
    missing: List[str] = []
    for label in labels:
        value = rows.get(label)
        text = _text(value, 40) if isinstance(value, str) else None
        if text is None or text in _PLACEHOLDERS or label in unavailable:
            missing.append(label)
        else:
            shown[label] = text
    failed_legs = _kf_degraded(kf) & _KS_LEGS
    if not shown:
        if failed_legs:
            raise _Incomplete("key facts: " + ", ".join(sorted(failed_legs)))
        return None
    basis = (_KEY_STATS_BASIS_TWO_CURRENCIES if kf.get("pe_basis") == _PE_BASIS_TWO_CURRENCIES
             else _KEY_STATS_BASIS)
    basis += "" if kf.get("live_price_ok") is True else _KEY_STATS_NO_PRICE
    # The earnings yield on THIS P/E's basis (not a Key Stats row, so beside the rows): the
    # Price card's yield is 1 / the card's P/E, never this one's (`app.utils.earnings_yield`).
    from app.utils.earnings_yield import NOT_AVAILABLE, earnings_yield_text

    earnings_yield = earnings_yield_text(shown.get("P/E (TTM)"))
    if earnings_yield != NOT_AVAILABLE:
        basis += _KEY_STATS_EARNINGS_YIELD
    block: Dict[str, Any] = {"basis": basis, "rows": shown}
    if earnings_yield != NOT_AVAILABLE:
        block["earnings_yield"] = earnings_yield
    currency = _key_stats_currency(kf, shown)
    if currency:
        block["currency"] = currency
    trading, statements = _price_currency(kf), _statement_currency(kf)
    if "EPS (TTM)" in shown and trading and statements and trading != statements:
        block["note"] = (f"EPS (TTM) is in {statements}, the company's reporting currency; the price "
                         f"is in {trading}. Never divide one by the other.")
    if missing:
        block["not_available"] = missing
    if failed_legs:
        block["incomplete"] = ("Some of the data behind these rows did not load in this build - "
                               "anything missing was not loaded, never zero.")
    return block


def _balance_block(kf: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sheet = kf.get("balance_sheet") if isinstance(kf.get("balance_sheet"), dict) else None
    if not sheet:
        if _kf_degraded(kf) & _BALANCE_LEGS:
            raise _Incomplete("the annual balance sheet did not load")
        return None
    figures = {
        "total_debt": _money(sheet.get("total_debt")),
        "cash_and_cash_equivalents": _money(sheet.get("cash_and_cash_equivalents")),
        "cash_and_short_term_investments": _money(sheet.get("cash_and_short_term_investments")),
        "net_debt": _money(sheet.get("net_debt")),
    }
    shown = {k: v for k, v in figures.items() if v is not None}
    if not shown:
        return None
    period_end = _iso_date(sheet.get("period_end"))
    fy = _text(sheet.get("fiscal_year"), 8)
    ccy = sheet.get("reported_currency") if isinstance(sheet.get("reported_currency"), str) else None
    block: Dict[str, Any] = {
        "basis": _BALANCE_BASIS,
        "period": (f"FY{fy}" if fy and fy.isdigit() else (fy or "latest annual")),
        "period_end": period_end or "not stated",
        "currency": ccy or _currency_label(kf),
    }
    block.update(shown)
    return block


_GROWTH_SERIES = (
    # (result key, response attribute prefix, formatter, peer-level key prefix)
    ("revenue", "revenue", _money, "revenue"),
    ("eps", "eps", _per_share, "eps"),
    ("net_income", "net_income", _money, "net_income"),
    ("operating_income", "operating_profit", _money, "operating_profit"),
    ("free_cash_flow", "free_cash_flow", _money, "fcf"),
)
_DEGRADED_LEGS = {
    "annual_income": "the annual income statement",
    "quarterly_income": "the quarterly income statement",
    "annual_cashflow": "the annual cash-flow statement",
    "quarterly_cashflow": "the quarterly cash-flow statement",
    "benchmarks": "the peer medians",
    "profile": "the company profile",
}


def _degraded_note(degraded: Any) -> Optional[str]:
    legs = [d for d in degraded if isinstance(d, str)] if isinstance(degraded, (list, tuple)) else []
    if not legs:
        return None
    named = sorted({_DEGRADED_LEGS.get(d, "other source data") for d in legs})
    return ("Not loaded in this build: " + ", ".join(named)
            + " - anything missing was not loaded, never zero.")


def _growth_row(label: str, point: Any, fmt: Any, peer: str) -> Optional[str]:
    value = fmt(_get(point, "value"))
    if value is None:
        return None
    parts = [f"{label}: {value}"]
    yoy = _pct(_get(point, "yoy_change_percent"))
    if yoy:
        parts.append(f"{yoy} YoY")
    median = _pct(_get(point, "sector_average_yoy"))
    if median:
        parts.append(f"{peer} median {median} YoY")
    return ", ".join(parts)


def _growth_block(resp: Any, kf: Optional[Dict[str, Any]], annual: int, quarterly: int) -> Optional[Dict[str, Any]]:
    levels = _get(resp, "peer_group_levels")
    levels = levels if isinstance(levels, dict) else {}
    block: Dict[str, Any] = {"basis": _GROWTH_BASIS, "currency": _currency_label(kf)}
    any_rows = False
    for key, attr, fmt, level_key in _GROWTH_SERIES:
        series: Dict[str, Any] = {}
        for span, limit in (("annual", annual), ("quarterly", quarterly)):
            peer = _peer_word(levels.get(f"{level_key}_{span}"))
            rows = []
            for label, point in _newest_first(_get(resp, f"{attr}_{span}")):
                row = _growth_row(label, point, fmt, peer)
                if row:
                    rows.append(row)
                if len(rows) >= limit:
                    break
            if rows:
                series[span] = rows
                any_rows = True
        if series:
            block[key] = series
    note = _degraded_note(_get(resp, "degraded"))
    if note:
        block["incomplete"] = note
    if not any_rows:
        if note:
            raise _Incomplete(note)
        return None
    block["eps_basis"] = "GAAP diluted EPS (per share)"
    # The periods held, from the WHOLE cached series: the rows above are only the newest, and
    # read alone they said "the data goes back to Q4 2024" (eval `hallucination-bait`).
    history = _history({
        span: set().union(*(_keys_shown(_get(resp, f"{attr}_{span}"), _growth_shown, span, fmt)
                            for _k, attr, fmt, _lk in _GROWTH_SERIES))
        for span in ("annual", "quarterly")})
    if history:
        block["history"] = history
    return block


_MARGINS = (("gross", "gross_margin"), ("operating", "operating_margin"),
            ("net", "net_margin"), ("FCF", "fcf_margin"))


def _margin_row(label: str, point: Any, peers: Dict[str, str]) -> Optional[str]:
    """'FY2025: gross 46.9%, ...; industry median: net 12.3%; sector median: FCF 9.0%' — each
    peer median under its OWN line's peer group (`peers`: margin field → peer word)."""
    own = [f"{word} {v}" for word, field in _MARGINS
           if (v := _pct(_get(point, field), signed=False)) is not None]
    if not own:
        return None
    text = f"{label}: " + ", ".join(own)
    groups: Dict[str, List[str]] = {}
    for word, field in _MARGINS:
        v = _pct(_get(point, f"sector_average_{field}"), signed=False)
        if v is not None:
            groups.setdefault(peers.get(field, "peer"), []).append(f"{word} {v}")
    for peer, medians in groups.items():
        text += f"; {peer} median: " + ", ".join(medians)
    return text


def _margin_peers(levels: Dict[str, Any], span: str, fallback_level: Any) -> Dict[str, str]:
    """Each margin line's peer word: its own `<span>.<field>` level (one per drawn line), else
    the span's net-margin line level, else the response's single level."""
    return {field: _peer_word(levels.get(f"{span}.{field}") or levels.get(span) or fallback_level)
            for _word, field in _MARGINS}


def _margins_block(resp: Any, annual: int, quarterly: int) -> Optional[Dict[str, Any]]:
    levels = _get(resp, "peer_group_levels")
    levels = levels if isinstance(levels, dict) else {}
    fallback_level = _get(resp, "peer_group_level")
    block: Dict[str, Any] = {"basis": _MARGINS_BASIS}
    any_rows = False
    for span, limit in (("annual", annual), ("quarterly", quarterly)):
        peers = _margin_peers(levels, span, fallback_level)
        rows = []
        for label, point in _newest_first(_get(resp, span)):
            row = _margin_row(label, point, peers)
            if row:
                rows.append(row)
            if len(rows) >= limit:
                break
        if rows:
            block[span] = rows
            any_rows = True
    note = _degraded_note(_get(resp, "degraded"))
    if note:
        block["incomplete"] = note
    if not any_rows and note:
        raise _Incomplete(note)
    if any_rows:
        history = _history({span: _keys_shown(_get(resp, span), _margin_shown, span)
                            for span in ("annual", "quarterly")})
        if history:
            block["history"] = history
    return block if any_rows else None


# ── A requested period (`period`, 2026-10-09) ───────────────────────────────────
# The eval `hallucination-bait`: the newest 8 quarters read as "the data goes back to Q4 2024",
# so fiscal Q3 2019 — in the cache — was called missing. Every growth and margins block now says
# which periods Caydex holds (`history`, from the FULL cached series), and a `period` reads one
# fiscal year or quarter from those same series. An absence names the REAL first / last period
# of the series (never the trimmed list's), and a gap inside the range is a gap, never "missing".

_PERIOD_SECTIONS = frozenset({"summary", "growth", "margins"})
_PERIOD_SOURCES: Dict[str, Tuple[str, ...]] = {
    "summary": ("key_facts", "growth", "margins"),
    "growth": ("key_facts", "growth"),
    "margins": ("key_facts", "margins"),
}
_PERIOD_ELSEWHERE = ("A period selects rows only in the summary, growth and margins sections, so "
                     "this section is shown as usual.")
_HISTORY_NOTE = ("`history` gives the fiscal years and quarters Caydex's data holds for this "
                 "company; only the newest are listed above. For an older one call again with "
                 "`period` (e.g. FY2019 or Q3 2019) - never say it is missing from Caydex's data "
                 "unless that call says so.")
_HISTORY_NOTE_PERIOD = ("`history` gives the fiscal years and quarters Caydex's data holds for "
                        "this company. For another one call again with its `period` - never say "
                        "a period is missing from Caydex's data unless a call for it says so.")
_SPAN_WORDS = {"annual": "fiscal years", "quarterly": "fiscal quarters"}
_SPAN_LEGS = {"annual": frozenset({"annual_income", "annual_cashflow"}),
              "quarterly": frozenset({"quarterly_income", "quarterly_cashflow"})}
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
# How long after a period ends its figures may still be on the way (a foreign filer's annual
# report can follow its year by about four months).
_REPORTING_LAG_MONTHS = 6
_QUARTERS_NOT_ANNUAL = ("The annual figures for {label} are not in Caydex's data, but its fiscal "
                        "quarters are, listed under `quarters`. A sum of quarters is not a "
                        "reported annual figure: say so if you add them.")


def _key_name(key: Tuple[int, int]) -> str:
    """'FY2019' / 'Q3 FY2019' for a `_period_key`."""
    year, q = key
    return f"FY{year}" if q == 9 else f"Q{q} FY{year}"


def _span_of(period: FiscalPeriod) -> str:
    return "quarterly" if period.quarter else "annual"


def _target(period: FiscalPeriod) -> Tuple[int, int]:
    return period.year, (period.quarter or 9)


def _growth_shown(point: Any, fmt: Any) -> bool:
    return fmt(_get(point, "value")) is not None


def _margin_shown(point: Any, _fmt: Any = None) -> bool:
    return any(_pct(_get(point, field), signed=False) is not None for _w, field in _MARGINS)


def _keys_shown(points: Any, shown: Any, span: str, fmt: Any = None) -> set:
    """The period keys of the rows a block would show — parsed labels of the span's kind with a
    usable value — over the WHOLE cached series, never the trimmed list."""
    keys = set()
    for label, point in _newest_first(points):
        key = _period_key(label)
        if key is None or (key[1] == 9) != (span == "annual"):
            continue
        if shown(point, fmt):
            keys.add(key)
    return keys


def _key_range(keys: set) -> str:
    """'FY2010 to FY2025' (the real first and last), or 'FY2025 only'."""
    first, last = min(keys), max(keys)
    return f"{_key_name(first)} to {_key_name(last)}" if first != last else f"{_key_name(first)} only"


def _history(keys_by_span: Dict[str, set]) -> Optional[Dict[str, str]]:
    """{'fiscal_years': 'FY2010 to FY2025', 'fiscal_quarters': 'Q1 FY2006 to Q2 FY2026'} — the
    periods a series holds (`_build` adds the `_HISTORY_NOTE` text once beside it)."""
    out = {_SPAN_WORDS[span].replace(" ", "_"): _key_range(keys_by_span[span])
           for span in ("annual", "quarterly") if keys_by_span.get(span)}
    return out or None


def _fiscal_year_end(kf: Optional[Dict[str, Any]]) -> Optional[Tuple[int, int, int, str]]:
    """(the latest reported fiscal year, the calendar year and month it ended in, its period-end
    date) from the key facts' annual balance sheet — a close on day 1-7 of a month is read as the
    month before (a 52/53-week year ending 2026-01-03 is a December year end)."""
    anchor = _fiscal_anchor(kf)
    if anchor is None:
        return None
    fy, end = anchor
    adj = end - timedelta(days=7)
    return fy, adj.year, adj.month, end.isoformat()


def _approx_end(period: FiscalPeriod, fye: Tuple[int, int, int, str]) -> Tuple[int, int]:
    """(calendar year, month) the requested fiscal period most likely ended in, assuming the
    fiscal year end has not moved: the anchor's end month shifted by whole years, less three
    months per quarter before Q4."""
    fy, end_year, end_month, _iso = fye
    idx = (end_year + (period.year - fy)) * 12 + (end_month - 1)
    if period.quarter:
        idx -= 3 * (4 - period.quarter)
    return idx // 12, idx % 12 + 1


def _fiscal_calendar(kf: Optional[Dict[str, Any]], period: FiscalPeriod) -> str:
    """How the company's fiscal periods sit against the calendar (Apple's fiscal Q3 ends in
    June): read from the latest annual balance sheet's own period end, never assumed."""
    fye = _fiscal_year_end(kf)
    if fye is None:
        return ("These are the company's own fiscal periods. Its fiscal year end is not confirmed "
                "here, so they may not match calendar quarters or years: if the user means a "
                "calendar period, say you are giving the fiscal one.")
    fy, _year, month, iso = fye
    if month == 12:
        return (f"The company's fiscal year ends in December (FY{fy} ended {iso}), so its fiscal "
                "quarters line up with calendar quarters.")
    year, end_month = _approx_end(period, fye)
    today = _today_et()
    verb = "ends" if (year, end_month) >= (today.year, today.month) else "ended"
    return (f"Fiscal, not calendar: the company's fiscal year ends in {_MONTHS[month - 1]} (FY{fy} "
            f"ended {iso}), so {period.label} most likely {verb} around {_MONTHS[end_month - 1]} "
            f"{year}. If the user means a calendar quarter or year, say which fiscal period you "
            "are giving.")


def _absence(period: FiscalPeriod, keys: set, other_keys: set,
             kf: Optional[Dict[str, Any]]) -> str:
    """Why `period` has no row, from the series' REAL first and last periods."""
    span = _span_of(period)
    other_span = "annual" if span == "quarterly" else "quarterly"
    words = _SPAN_WORDS[span]
    label = period.label
    if not keys:
        held = (f"; it holds {_SPAN_WORDS[other_span]} {_key_range(other_keys)}"
                if other_keys else "")
        return (f"Caydex's data holds no {words} for this company{held}. Never say the company "
                f"did not report {label}, and never estimate it.")
    first, last = min(keys), max(keys)
    want = _target(period)
    if want < first:
        return (f"Caydex's data for this company starts at {_key_name(first)} ({words}), so "
                f"{label} is earlier than Caydex's data goes. Say exactly that - never that the "
                "company did not report it or that the figure does not exist.")
    if want > last:
        today = _today_et()
        if period.year > today.year + 1:
            return (f"{label} is in the future: the latest reported period in Caydex's data is "
                    f"{_key_name(last)}. Never estimate or forecast it.")
        fye = _fiscal_year_end(kf)
        end = _approx_end(period, fye) if fye is not None else None
        if end is not None and end >= (today.year, today.month):
            return (f"{label} has most likely not ended yet (around {_MONTHS[end[1] - 1]} "
                    f"{end[0]}): the latest reported period in Caydex's data is "
                    f"{_key_name(last)}. Never estimate it.")
        # Ended within the reporting lag (a 10-K can follow its year by ~3 months, a foreign
        # filer's by ~4) — else the series simply ENDS there (an acquired or delisted company,
        # a filer that stopped reporting) and "not reported yet" would be false.
        months_ago = (today.year * 12 + today.month - (end[0] * 12 + end[1])) if end else None
        if (months_ago is not None and months_ago <= _REPORTING_LAG_MONTHS) or (
                end is None and period.year >= today.year - 1):
            return (f"{label} is not in Caydex's data yet: the latest reported period is "
                    f"{_key_name(last)}. It may not have been reported yet - never estimate it.")
        return (f"Caydex's data for this company ends at {_key_name(last)} ({words}), so it "
                f"holds nothing for {label}. Say exactly that - never that the company did not "
                "report it, and never estimate it.")
    before = max((k for k in keys if k < want), default=None)
    after = min((k for k in keys if k > want), default=None)
    nearest = " and ".join(_key_name(k) for k in (before, after) if k is not None)
    return (f"Caydex's data has no row for {label}, although it holds {words} from "
            f"{_key_name(first)} to {_key_name(last)}: a gap in the filed data (for example "
            f"around a change of fiscal year end). The nearest periods it holds are {nearest}; "
            f"never present either as {label}, and never estimate it.")


def _failed_legs(degraded: Any) -> set:
    return {d for d in degraded if isinstance(d, str)} if isinstance(degraded, (list, tuple)) else set()


def _span_failed(degraded: Any, span: str) -> bool:
    return bool(_failed_legs(degraded) & _SPAN_LEGS[span])


# Which statement each growth series is read from (the service's `degraded` leg names).
_SERIES_STATEMENT = {"revenue": "income", "eps": "income", "net_income": "income",
                     "operating_income": "income", "free_cash_flow": "cashflow"}


def _money_exact(value: Any) -> Optional[str]:
    """`_money` plus the full amount as filed — one period's answer can carry it ("Apple's EXACT
    revenue in fiscal Q3 2019"), where the rounded "53.81B" alone could not."""
    short = _money(value)
    f = _num(value)
    if short is None or f is None or abs(f) < 1e4:
        return short
    return f"{short} ({f:,.0f} as reported)"


def _growth_period_block(resp: Any, kf: Optional[Dict[str, Any]],
                         period: FiscalPeriod) -> Optional[Dict[str, Any]]:
    """`period`'s rows from the growth series (every cached period, not the trimmed list), or an
    honest absence naming the series' real first and last periods."""
    levels = _get(resp, "peer_group_levels")
    levels = levels if isinstance(levels, dict) else {}
    span = _span_of(period)
    other_span = "annual" if span == "quarterly" else "quarterly"
    want = _target(period)
    keys = {"annual": set(), "quarterly": set()}
    found: Dict[str, str] = {}
    quarters: Dict[str, List[str]] = {}
    for key, attr, fmt, level_key in _GROWTH_SERIES:
        exact = _money_exact if fmt is _money else fmt
        for s in ("annual", "quarterly"):
            peer = _peer_word(levels.get(f"{level_key}_{s}"))
            for label, point in _newest_first(_get(resp, f"{attr}_{s}")):
                pk = _period_key(label)
                if pk is None or (pk[1] == 9) != (s == "annual") or not _growth_shown(point, fmt):
                    continue
                keys[s].add(pk)
                if s == span and pk == want:
                    row = _growth_row(period.label, point, exact, peer)
                    if row and key not in found:
                        found[key] = row
                elif span == "annual" and s == "quarterly" and pk[0] == period.year:
                    row = _growth_row(_key_name(pk), point, exact, peer)
                    if row:
                        quarters.setdefault(key, []).append(row)
    degraded = _get(resp, "degraded")
    note = _degraded_note(degraded)
    if not keys["annual"] and not keys["quarterly"]:
        if note:
            raise _Incomplete(note)
        return None
    block: Dict[str, Any] = {"basis": _GROWTH_BASIS, "period": period.label,
                             "currency": _currency_label(kf)}
    if found:
        for key, _attr, _fmt, _lk in _GROWTH_SERIES:
            if key in found:
                block[key] = found[key]
        # A series whose statement leg failed in this build did not load; any other one has no
        # figure for this period in Caydex's data. Neither is ever zero.
        failed = _failed_legs(degraded)
        missing = [key for key, *_r in _GROWTH_SERIES if key not in found]
        unloaded = [key for key in missing if f"{span}_{_SERIES_STATEMENT[key]}" in failed]
        absent = [key for key in missing if key not in unloaded]
        if unloaded:
            block["not_loaded_in_this_build"] = unloaded
        if absent:
            block["not_in_data_for_this_period"] = absent
        block["eps_basis"] = "GAAP diluted EPS (per share)"
    elif _span_failed(degraded, span):
        raise _Incomplete(f"{period.label}: {note}")
    elif quarters:
        block["note"] = _QUARTERS_NOT_ANNUAL.format(label=period.label)
        block["quarters"] = quarters
        block["eps_basis"] = "GAAP diluted EPS (per share)"
    else:
        block["not_in_data"] = _absence(period, keys[span], keys[other_span], kf)
    if note:
        block["incomplete"] = note
    history = _history(keys)
    if history:
        block["history"] = history
    return block


def _margins_period_block(resp: Any, kf: Optional[Dict[str, Any]],
                          period: FiscalPeriod) -> Optional[Dict[str, Any]]:
    """`period`'s margins from the whole cached series, or an honest absence."""
    levels = _get(resp, "peer_group_levels")
    levels = levels if isinstance(levels, dict) else {}
    fallback_level = _get(resp, "peer_group_level")
    span = _span_of(period)
    other_span = "annual" if span == "quarterly" else "quarterly"
    want = _target(period)
    keys = {"annual": set(), "quarterly": set()}
    found: Optional[str] = None
    quarters: List[str] = []
    for s in ("annual", "quarterly"):
        peers = _margin_peers(levels, s, fallback_level)
        for label, point in _newest_first(_get(resp, s)):
            pk = _period_key(label)
            if pk is None or (pk[1] == 9) != (s == "annual") or not _margin_shown(point):
                continue
            keys[s].add(pk)
            if s == span and pk == want and found is None:
                found = _margin_row(period.label, point, peers)
            elif span == "annual" and s == "quarterly" and pk[0] == period.year:
                row = _margin_row(_key_name(pk), point, peers)
                if row:
                    quarters.append(row)
    degraded = _get(resp, "degraded")
    note = _degraded_note(degraded)
    if not keys["annual"] and not keys["quarterly"]:
        if note:
            raise _Incomplete(note)
        return None
    block: Dict[str, Any] = {"basis": _MARGINS_BASIS, "period": period.label}
    if found:
        block["margins"] = found
    elif _span_failed(degraded, span):
        raise _Incomplete(f"{period.label}: {note}")
    elif quarters:
        block["note"] = _QUARTERS_NOT_ANNUAL.format(label=period.label)
        block["quarters"] = quarters
    else:
        block["not_in_data"] = _absence(period, keys[span], keys[other_span], kf)
    if note:
        block["incomplete"] = note
    history = _history(keys)
    if history:
        block["history"] = history
    return block


def _snapshot_metrics(snap: Any, ticker: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """`("Name: value", ...)` from a snapshot card (peer-worded names), and the names whose value
    is a placeholder. A card's Earnings Yield is re-derived from the card's own P/E
    (`with_derived_earnings_yield`) — the yield beside a P/E is always that P/E's inverse."""
    from app.utils.earnings_yield import with_derived_earnings_yield
    from app.utils.peer_wording import peer_worded_metric_name

    shown: List[str] = []
    missing: List[str] = []
    for metric in with_derived_earnings_yield(_get(snap, "metrics") or [], ticker):
        view = SimpleNamespace(name=_get(metric, "name"), peer_level=_get(metric, "peer_level"))
        name = _text(peer_worded_metric_name(view), _NAME_MAX)
        value = _text(_get(metric, "value"), 32)
        if not name:
            continue
        if value is None or value in _PLACEHOLDERS:
            missing.append(name)
        else:
            shown.append(f"{name}: {value}")
    return shown, missing


def _health_block(value: Any) -> Optional[Dict[str, Any]]:
    """The Financial Health card. A degraded build with nothing to show (its legs failed — the
    card then carries only a placeholder metric) did not load; it never reads as "none"."""
    snap, degraded = _unwrap_status(value)
    shown, missing = _snapshot_metrics(snap)
    if not shown:
        if degraded:
            raise _Incomplete("the health card's build was degraded: " + ", ".join(degraded[:4]))
        return None
    block: Dict[str, Any] = {"basis": _HEALTH_BASIS, "ratios": shown}
    as_of = _text(_get(snap, "computed_at"), 24)
    if as_of:
        block["as_of"] = as_of
    if missing:
        block["not_available"] = missing
    if degraded:
        block["incomplete"] = _SNAPSHOT_INCOMPLETE
    return block


_UPCOMING_ROWS = 2
_UNDATED = "9999-12-31"


def _earnings_rows(quarters: Any, fmt: Any, today: str,
                   limit: int) -> Tuple[List[str], List[Tuple[str, str]]]:
    """(reported rows newest first, upcoming `(fiscal date, row)` NEAREST first).

    A quarter with no actual, or a period that ends after today, is pending — never a miss. Only
    quarters AFTER the newest reported one are pending (an older quarter with no result is a hole
    in the history, never "upcoming"), and the NEAREST `_UPCOMING_ROWS` are kept: the earnings
    service lists every future quarter it has an estimate for, so keeping the farthest would
    present a quarter nine months out as the next report. One row per fiscal date (the later
    row in the service's order wins a duplicate)."""
    if not isinstance(quarters, (list, tuple)):
        return [], []
    by_period: Dict[str, Tuple[str, int, Any]] = {}
    for i, q in enumerate(quarters):
        fiscal = _iso_date(_get(q, "fiscal_date")) or ""
        key = fiscal or f"label:{_text(_get(q, 'quarter'), 16) or i}"
        by_period[key] = (fiscal, i, q)
    reported_rows: List[Tuple[str, int, Any, str]] = []
    pending: List[Tuple[str, int, Any]] = []
    for fiscal, i, q in by_period.values():
        actual = fmt(_get(q, "actual_value"))
        if actual is None or (fiscal and fiscal > today):
            pending.append((fiscal, i, q))
        else:
            reported_rows.append((fiscal, i, q, actual))
    latest_reported = max((f for f, _i, _q, _a in reported_rows if f), default="")
    pending = [p for p in pending if not (p[0] and latest_reported and p[0] <= latest_reported)]
    pending.sort(key=lambda p: (p[0] or _UNDATED, p[1]))
    upcoming: List[Tuple[str, str]] = []
    for fiscal, _i, q in pending[:_UPCOMING_ROWS]:
        label = _text(_get(q, "quarter"), 16) or "quarter"
        head = f"{label} (period ending {fiscal})" if fiscal else label
        estimate = fmt(_get(q, "estimate_value")) if _get(q, "has_estimate") is not False else None
        # A period that has not ended is "upcoming"; one that ended with no result yet is "not
        # yet reported" — neither is ever a miss.
        state = "upcoming" if (not fiscal or fiscal > today) else "not yet reported in Caydex's data"
        upcoming.append((fiscal or _UNDATED,
                         f"{head}: {state}" + (f", estimate {estimate}" if estimate else "")))
    reported_rows.sort(key=lambda it: (it[0], it[1]), reverse=True)
    reported: List[str] = []
    for fiscal, _i, q, actual in reported_rows[:max(0, limit)]:
        label = _text(_get(q, "quarter"), 16) or "quarter"
        head = f"{label} (period ending {fiscal})" if fiscal else label
        has_estimate = _get(q, "has_estimate")
        estimate = fmt(_get(q, "estimate_value")) if has_estimate is not False else None
        surprise = _num(_get(q, "surprise_percent"))
        comparable = has_estimate is True or (has_estimate is None and surprise is not None)
        if comparable and estimate:
            text = f"{head}: {actual} vs estimate {estimate}"
            if surprise is not None and abs(surprise) < _PCT_MAX:
                if surprise > 0:
                    text += f" - beat by {surprise:.1f}%"
                elif surprise < 0:
                    text += f" - missed by {abs(surprise):.1f}%"
                else:
                    text += " - in line"
        else:
            text = f"{head}: {actual} (no comparable estimate)"
        reported.append(text)
    return reported, upcoming


def _next_report(ned: Any, today: str) -> Optional[str]:
    if ned is None:
        return None
    day = _iso_date(_get(ned, "date"))
    if not day:
        return None
    if day < today:
        return f"not yet announced in Caydex's data (the last known date, {day}, has passed)"
    status = ("confirmed by the company" if _get(ned, "is_confirmed") is True
              else "projected, not yet confirmed")
    timing = _text(_get(ned, "timing"), 40)
    return f"{day} ({status}" + (f", {timing}" if timing else "") + ")"


def _earnings_block(resp: Any, kf: Optional[Dict[str, Any]], limit: int) -> Optional[Dict[str, Any]]:
    today = _today_et().isoformat()
    eps, eps_up = _earnings_rows(_get(resp, "eps_quarters"), _per_share, today, limit)
    rev, rev_up = _earnings_rows(_get(resp, "revenue_quarters"), _money, today, limit)
    nxt = _next_report(_get(resp, "next_earnings_date"), today)
    if not (eps or rev or eps_up or rev_up or nxt):
        if _get(resp, "degraded"):
            raise _Incomplete("the earnings build was partial")
        return None
    ccy = _statement_currency(kf)
    block: Dict[str, Any] = {
        "basis": _EARNINGS_BASIS,
        "currency": ("USD" if ccy == "USD" else
                     "not confirmed - results against estimates may be in US dollars or in the "
                     "company's reporting currency; never convert"),
    }
    if eps:
        block["eps"] = eps
    if rev:
        block["revenue"] = rev
    if eps_up or rev_up:
        # Nearest first across both series (EPS before revenue for the same quarter), so the
        # size trimmer — which drops the LAST entry — drops the farthest quarter first.
        merged = sorted([(day, 0, "EPS " + r) for day, r in eps_up]
                        + [(day, 1, "revenue " + r) for day, r in rev_up])
        block["upcoming"] = [row for _day, _kind, row in merged]
    if nxt:
        block["next_report"] = nxt
    note = _degraded_note(_get(resp, "degraded"))
    if note:
        block["incomplete"] = note
    return block


def _range_text(rng: Any, fmt: Any) -> Optional[str]:
    avg = fmt(_get(rng, "avg")) if rng is not None else None
    if avg is None:
        return None
    low, high = fmt(_get(rng, "low")), fmt(_get(rng, "high"))
    if low and high:
        return f"{avg} (low {low}, high {high})"
    return avg


def _fiscal_anchor(kf: Optional[Dict[str, Any]]) -> Optional[Tuple[int, date]]:
    """(fiscal year, period end) of the latest REPORTED annual statement — the key facts' balance
    sheet carries FMP's own `fiscalYear` and its period end — normalised for a Jan 1-7 close
    (`annual_fiscal_year`). None without a real 4-digit fiscal year: a date-only guess is the
    calendar-year label this anchor exists to replace."""
    from app.utils.period_labels import annual_fiscal_year

    sheet = (kf or {}).get("balance_sheet")
    if not isinstance(sheet, dict):
        return None
    end = _iso_date(sheet.get("period_end"))
    raw_fy = sheet.get("fiscal_year")
    if not end or not (isinstance(raw_fy, str) and len(raw_fy.strip()) == 4 and raw_fy.strip().isdigit()):
        return None
    fy = annual_fiscal_year({"date": end, "fiscalYear": raw_fy.strip()})
    if not (len(fy) == 4 and fy.isdigit()):
        return None
    try:
        return int(fy), date.fromisoformat(end)
    except ValueError:
        return None


def _estimate_fiscal_label(day: Optional[str], anchor: Optional[Tuple[int, date]]) -> Optional[str]:
    """The company's own fiscal-year label for an estimate row ending on `day`: the newest
    reported fiscal year plus the whole years between the two period ends — the report's
    `_forecast_fiscal_label` math (final review 2026-10-09: the calendar year of the period end
    labelled Home Depot's FY2026, ending 2027-01-31, "FY2027", one ahead of its growth rows, and
    the year in between vanished). None when it cannot be anchored."""
    if not day or anchor is None:
        return None
    try:
        end = date.fromisoformat(day)
    except ValueError:
        return None
    fy, anchor_end = anchor
    return f"FY{fy + round((end - anchor_end).days / 365.25)}"


def _estimates_block(payload: Dict[str, Any], kf: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    periods = [p for p in payload.get("periods") or [] if _get(p, "is_forward") is True]
    periods.sort(key=lambda p: _iso_date(_get(p, "date")) or "9999-12-31")
    anchor = _fiscal_anchor(kf)
    rows: List[str] = []
    for p in periods:
        day = _iso_date(_get(p, "date"))
        # Never the analyst feed's own "FY<calendar year of the period end>" label: with no
        # reported anchor the row is named by its period end alone.
        label = _estimate_fiscal_label(day, anchor) or (
            "fiscal year" if day else "a future fiscal year (undated)")
        parts = []
        rev = _range_text(_get(p, "revenue"), _money)
        if rev:
            n = _num(_get(p, "num_analysts_revenue"))
            parts.append(f"revenue {rev}" + (f", {int(n)} analysts" if n and n > 0 else ""))
        eps = _range_text(_get(p, "eps"), _per_share)
        if eps:
            n = _num(_get(p, "num_analysts_eps"))
            parts.append(f"EPS {eps}" + (f", {int(n)} analysts" if n and n > 0 else ""))
        if not parts:
            continue
        head = f"{label} (period ending {day})" if label.startswith("FY") else (
            f"fiscal year ending {day}" if day else label)
        rows.append(head + ": " + "; ".join(parts))
        if len(rows) >= _ESTIMATE_ROWS:
            break
    if not rows:
        return None
    ccy = _statement_currency(kf)
    if ccy == "USD":
        currency = "USD"
    elif ccy:
        currency = f"{ccy} (the company's reporting currency, as published - never converted)"
    else:
        currency = "not confirmed (as published - never converted)"
    return {"basis": _ESTIMATES_BASIS, "currency": currency, "periods": rows}


def _ccy_code(raw: Any) -> Optional[str]:
    """A 3-letter currency code, upper-cased, or None — never a guess."""
    if not isinstance(raw, str):
        return None
    code = raw.strip().upper()
    return code if len(code) == 3 and code.isascii() and code.isalpha() else None


def _fair_value(snap: Any, kf: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The Caydex estimate ONLY while `DCF_ENABLED` (it is unpublished otherwise); else the
    third-party DCF, labelled as one. Neither is ever a price target. Both are per-share values
    set against the live price, so they are in the currency the stock TRADES in (the estimate's
    own code when it carries one), never the statements' currency."""
    from app.config import settings

    trading = _price_currency(kf)
    if settings.DCF_ENABLED:
        est = _get(snap, "caydex_estimate")
        if est is None:
            return None
        status = _get(est, "status")
        if status == "refused":
            reason = _text(_get(est, "refusal_reason"), 200)
            return {"source": "Caydex fair value model", "status": "no estimate for this company",
                    "reason": reason or "the model declined to estimate this company"}
        if status != "ok":
            return None
        value = _per_share(_get(est, "fair_value"))
        if value is None:
            return None
        ccy = _ccy_code(_get(est, "currency")) or trading
        text = f"{value} {ccy} per share" if ccy else f"{value} per share"
        low, high = _per_share(_get(est, "range_low")), _per_share(_get(est, "range_high"))
        out: Dict[str, Any] = {"source": "Caydex fair value model", "value": text,
                               "currency": ccy or _trading_label(kf), "label": _CAYDEX_FV_LABEL}
        if low and high:
            out["range"] = f"{low} to {high}"
        as_of = _iso_date(_get(est, "as_of"))
        if as_of:
            out["as_of"] = as_of
        return out
    dcf = _get(snap, "dcf")
    if dcf is None:
        return None
    status = _get(dcf, "status")
    if status == "negative_cash_flow":
        return {"source": "third-party discounted-cash-flow model", "status": "no value",
                "reason": "the model's projected cash flow is negative, so it gives no value",
                "label": _THIRD_PARTY_DCF_LABEL}
    if status != "ok":
        return None
    value = _per_share(_get(dcf, "value"))
    if value is None:
        return None
    out = {"source": "third-party discounted-cash-flow model",
           "value": f"{value} {trading} per share" if trading else f"{value} per share",
           "currency": _trading_label(kf), "label": _THIRD_PARTY_DCF_LABEL}
    as_of = _iso_date(_get(dcf, "as_of"))
    if as_of:
        out["as_of"] = as_of
    return out


def _valuation_block(value: Any, kf: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The Price card's multiples plus the fair value. A degraded multiples build with nothing to
    show (and no fair value) did not load; it never reads as "none"."""
    snap, degraded = _unwrap_status(value)
    shown, missing = _snapshot_metrics(snap, (kf or {}).get("ticker"))
    fair = _fair_value(snap, kf)
    if not shown and not fair:
        if degraded:
            raise _Incomplete("the Price card's build was degraded: " + ", ".join(degraded[:4]))
        return None
    priced = (_KEY_STATS_PE_DAILY_CLOSE
              if (kf or {}).get("pe_basis") == _PE_BASIS_TWO_CURRENCIES else _KEY_STATS_PE_LIVE)
    block: Dict[str, Any] = {"basis": _VALUATION_BASIS.format(key_stats=priced)}
    as_of = _text(_get(snap, "computed_at"), 24)
    if as_of:
        block["as_of"] = as_of
    if shown:
        block["multiples"] = shown
    if missing:
        block["not_available"] = missing
    if degraded:
        block["incomplete"] = _SNAPSHOT_INCOMPLETE
    if fair:
        block["fair_value"] = fair
    return block


_TOTAL_REVENUE_BAR = "Total Revenue"     # `revenue_breakdown_service._TOTAL_REVENUE_NAME`


def _is_revenue_placeholder(raw_sources: Any) -> bool:
    """The Revenue card's zero-height chart bar — exactly one source, named "Total Revenue", at
    0.0 — which the service draws whenever it has no revenue source at all (final review
    2026-10-09). It is a CHART placeholder, never a revenue figure: read as one, a just-listed
    company or a feed gap told the model it earned exactly zero. The honest POSITIVE Total
    Revenue fallback (no segmentation, revenue from the income statement) is not this shape."""
    if not isinstance(raw_sources, (list, tuple)) or len(raw_sources) != 1:
        return False
    src = raw_sources[0]
    value = _num(_get(src, "value"))
    return _get(src, "name") == _TOTAL_REVENUE_BAR and value == 0.0


def _segments_block(resp: Any, kf: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    fy = _text(_get(resp, "fiscal_year"), 8)
    reported = _num(_get(resp, "reported_revenue"))
    raw_sources = _get(resp, "revenue_sources") or []
    placeholder = _is_revenue_placeholder(raw_sources)
    if placeholder and reported is None:
        # The "we know nothing" card (income statement empty, revenue unreported): did not load
        # — never a zero, never "no segments".
        raise _Incomplete("the revenue breakdown carries only its chart placeholder")
    sources = []
    for src in ([] if placeholder else raw_sources):
        name = _text(_get(src, "name"), _NAME_MAX)
        value = _num(_get(src, "value"))
        if name and value is not None and abs(value) < _MONEY_MAX:
            sources.append((name, value))
    sources.sort(key=lambda it: it[1], reverse=True)
    rows = []
    for name, value in sources[:_SEGMENT_ROWS]:
        text = f"{name}: {_money(value)}"
        if reported is not None and reported > 0:
            share = value / reported * 100
            if math.isfinite(share) and abs(share) < _PCT_MAX:
                text += f" ({share:.1f}% of reported revenue)"
        rows.append(text)
    degraded = _get(resp, "degraded") or []
    if not rows and reported is None:
        return None
    block: Dict[str, Any] = {
        "basis": _SEGMENTS_BASIS,
        "period": f"FY{fy}" if fy and fy.isdigit() else (fy or "latest fiscal year"),
        "currency": _currency_label(kf),
    }
    if _money(reported):
        block["reported_revenue"] = _money(reported)
    if rows:
        block["segments"] = rows
    elif placeholder:
        # A REPORTED zero or negative revenue (a pre-revenue filer): the signed figure above is
        # the answer; the 0.0 bar is the chart's, never a segment.
        block["segments_note"] = "no segment breakdown was reported for this period"
    if len(sources) > _SEGMENT_ROWS:
        block["more_segments_not_shown"] = len(sources) - _SEGMENT_ROWS
    elims = _money(_get(resp, "intersegment_eliminations"))
    if elims:
        block["intersegment_eliminations"] = (
            f"{elims} of sales between segments are removed in consolidation")
    if isinstance(degraded, (list, tuple)) and "segmentation_unavailable" in degraded:
        block["incomplete"] = "Segment detail did not load in this build; only total revenue is shown."
    return block


def _window_text(raw: Any) -> Optional[str]:
    """The average-yield window ('8Q' → 'the last 8 quarters', '5Y' → '5 years')."""
    t = _text(raw, 6)
    if not t:
        return None
    m = re.match(r"^(\d{1,2})([QY])$", t.upper())
    if not m:
        return t
    n, unit = int(m.group(1)), m.group(2)
    return f"the last {n} quarters" if unit == "Q" else f"{n} years"


def _dividends_block(soc: Any, kf: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    block: Dict[str, Any] = {"basis": _DIVIDENDS_BASIS, "currency": _currency_label(kf)}
    rows = (kf or {}).get("rows") if isinstance((kf or {}).get("rows"), dict) else {}
    key_row = rows.get("Dividends")
    if isinstance(key_row, str) and key_row.strip() and key_row.strip() not in _PLACEHOLDERS:
        # The Key Stats row is the profile's annual dividend at the LIVE price: the trading
        # currency, not the statements' (which `per_share_by_year` below is in).
        block["key_stats_dividends"] = (
            "pays no dividend" if key_row.strip() == "None"
            else (f"{_text(key_row, 40)} (annual dividend per share in {_trading_label(kf)}, "
                  "and its yield at the live price)"))
    degraded = _get(soc, "degraded") or []
    info = _get(soc, "dividend_info")
    summary = _get(soc, "summary")
    if info is None:
        block["dividend"] = (
            "could not be confirmed in this build" if degraded
            else "Caydex's records show no current dividend")
    else:
        # Yields to 2 dp — the precision Key Stats prints (0.45%, never rounded to 0.5%). A
        # PAYER's 0.0 is the service's "nothing measured" (no trailing cash-flow quarter known),
        # never a real yield — omitted like a zero buyback yield below.
        dy = _pct(_get(summary, "dividend_yield"), signed=False, digits=2)
        if dy and _num(_get(summary, "dividend_yield")) != 0:
            block["dividend_yield_ttm"] = dy
        # 4 decimals, the precision the Signal of Confidence card prints ("$%.4f"), and "(none
        # paid)" from the NUMBER (final review 2026-10-09): a 2-decimal string turned a real
        # $0.004 dividend into "0.00 (none paid)" and NVDA's $0.0160 into "0.02".
        dps = _dividend_per_share(_get(info, "dividend_per_share"))
        dps_year = _text(_get(info, "dividend_per_share_year"), 8)
        if dps:
            block["latest_fiscal_year_dividend_per_share"] = (
                f"FY{dps_year}: {dps}" if dps_year else dps)
        years = []
        for label, point in _newest_first(_get(info, "annual_dividends"), label_field="year"):
            raw = _get(point, "per_share")
            value = _dividend_per_share(raw)
            if value is not None:
                years.append(f"{label}: {value}" + (" (none paid)" if _num(raw) == 0 else ""))
            if len(years) >= _DIVIDEND_YEARS:
                break
        if years:
            block["per_share_by_year"] = years
        growth = _pct(_get(info, "dividend_growth_pct"))
        span = _num(_get(info, "dividend_growth_years"))
        if growth and span and span > 0:
            block["dividend_growth"] = f"{growth} over {int(span)} years"
        avg = _pct(_get(info, "five_year_avg_yield"), signed=False, digits=2)
        window = _window_text(_get(info, "avg_yield_window"))
        if avg and window:
            block["average_yield"] = f"{avg} (average over {window})"
        ex_date = _iso_date(_get(info, "ex_dividend_date"))
        if ex_date:
            block["latest_ex_dividend_date"] = ex_date
        pay = _iso_date(_get(info, "payment_date"))
        if pay:
            block["latest_payment_date"] = pay
    by = _pct(_get(summary, "buyback_yield"), signed=False, digits=2)
    if by and _num(_get(summary, "buyback_yield")) != 0:
        block["buyback_yield_ttm"] = by
    if _get(summary, "share_count_change_known") is True:
        scc = _pct(_get(summary, "share_count_change"))
        if scc:
            block["share_count_change"] = scc
    return block


_UNCLASSIFIED_SPLIT = ("{day}: the share basis changed by an amount that could not be identified "
                       "as a split (possibly a large reverse split or a spin-off) - never say "
                       "there were no splits")
_UNCLASSIFIED_ROWS = 3


def _splits_text(value: Any, status: str) -> Any:
    """The splits line. `value` is `_load_splits`' dict (a bare list is read as the named rows
    with no unclassified changes). "No stock split" is said ONLY when both reads are clean: no
    named split AND no unclassified share-basis change in the window."""
    if isinstance(value, dict):
        rows, unclassified = value.get("rows"), value.get("unclassified")
    else:
        rows, unclassified = value, []
    if (status != "ok" or rows is None or not isinstance(rows, list)
            or unclassified is None or not isinstance(unclassified, list)):
        return "could not be checked right now - do not say there were none"
    odd = [_UNCLASSIFIED_SPLIT.format(day=d) for d in
           (_iso_date(u) for u in unclassified[:_UNCLASSIFIED_ROWS]) if d]
    if not rows:
        return odd or f"no stock split since {_split_window_start()}"
    out = []
    for row in rows[:_SPLIT_ROWS]:
        day = _iso_date(_get(row, "date"))
        num, den = _num(_get(row, "numerator")), _num(_get(row, "denominator"))
        if not day or not num or not den or num <= 0 or den <= 0:
            continue
        if num >= den:
            out.append(f"{day}: {num:g}-for-{den:g} split")
        else:
            out.append(f"{day}: 1-for-{den / num:g} reverse split")
    if not out:
        return odd or "could not be read - do not say there were none"
    return out + odd


# ── Assembly ────────────────────────────────────────────────────────────────────

def _resolved_as(sym: str, kf: Optional[Dict[str, Any]]) -> str:
    name = _text((kf or {}).get("company_name"), 100)
    text = (f"{name} ({sym})" if name else sym) + ": the listed company"
    if detect_asset_class(sym, include_bare_coins=True) == "crypto":
        text += ", not the cryptocurrency of the same symbol"
    return text


def _currency_block(kf: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    statements = _statement_currency(kf)
    trading = (kf or {}).get("price_currency") if isinstance((kf or {}).get("price_currency"), str) else None
    block: Dict[str, Any] = {
        "statements": statements or "not confirmed",
        "trading": trading or "not confirmed",
    }
    if statements and trading and statements != trading:
        block["note"] = (f"The financial statements are reported in {statements} while the stock "
                         f"trades in {trading}: never convert between them - give each figure "
                         "in its own currency.")
    elif not statements:
        block["note"] = ("The statements' currency is not confirmed: say 'in the company's "
                         "reporting currency', never assume US dollars.")
    return block


def _sections_available() -> List[str]:
    from app.services._analyst_common import analyst_estimates_available

    licensed = analyst_estimates_available()
    return [s for s in FINANCIAL_SECTIONS if s != "estimates" or licensed]


def _block_wait() -> float:
    return float(_BLOCK_WAIT_SECONDS)


def _on_this_loop(task: "asyncio.Task") -> bool:
    """A running task this event loop can await — never one left behind by a closed loop."""
    try:
        return not task.done() and task.get_loop() is asyncio.get_running_loop()
    except RuntimeError:
        return False


def _start_source(name: str, sym: str) -> "asyncio.Task":
    """One source read as a task held in `_side_tasks` until it finishes; a failure is logged
    with its stack (never a silent loss), even when no answer waits for it any more. A read of
    the same (source, symbol) already running is JOINED, never started twice — two sections in
    one round each read the key facts, and a cold read is the full fundamentals fan-out."""
    key = (name, sym)
    running = _source_tasks.get(key)
    if running is not None and _on_this_loop(running):
        logger.info("chat tool %s: joining the in-flight %s read for %s", FINANCIALS_TOOL, name, sym)
        return running
    loader = globals()[f"_load_{name}"]
    started = time.monotonic()
    task = asyncio.ensure_future(loader(sym))
    _side_tasks.add(task)
    _source_tasks[key] = task

    def _done(t: "asyncio.Task") -> None:
        _side_tasks.discard(t)
        if _source_tasks.get(key) is t:
            _source_tasks.pop(key, None)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.warning(
                "chat tool %s: %s read failed for %s after %.1fs: %s: %s", FINANCIALS_TOOL,
                name, sym, time.monotonic() - started, type(exc).__name__, exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    task.add_done_callback(_done)
    return task


async def _gather_sources(sym: str, names: Tuple[str, ...]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Run the named sources together; wait at most `_block_wait()` for them. Returns the
    loaded values and each source's status: "ok", "not_loaded" (still running — it keeps going
    and warms its cache) or "failed"."""
    tasks = {name: _start_source(name, sym) for name in names}
    await asyncio.wait(set(tasks.values()), timeout=max(0.0, _block_wait()))
    loaded: Dict[str, Any] = {}
    status: Dict[str, str] = {}
    for name, task in tasks.items():
        if not task.done():
            status[name] = "not_loaded"
            logger.info("chat tool %s: %s for %s still loading after %.1fs — named as not loaded",
                        FINANCIALS_TOOL, name, sym, _block_wait())
        elif task.cancelled() or task.exception() is not None:
            status[name] = "failed"
        else:
            status[name] = "ok"
            loaded[name] = task.result()
    return loaded, status


# (result key, source, builder) per section. A builder takes (value, key facts) and returns a
# block or None (nothing to show — answered).
def _plan(sec: str, period: Optional[FiscalPeriod] = None) -> List[Tuple[str, str, Any]]:
    if period is not None:
        # One fiscal period, read from the whole cached series (`_PERIOD_SECTIONS` only).
        growth = ("growth", "growth", lambda v, kf: _growth_period_block(v, kf, period))
        margins = ("margins", "margins", lambda v, kf: _margins_period_block(v, kf, period))
        return {"summary": [growth, margins], "growth": [growth], "margins": [margins]}[sec]
    summary_stats = lambda kf, _kf: _key_stats_block(kf, _SUMMARY_KEY_STATS)  # noqa: E731
    return {
        "summary": [
            ("key_stats", "key_facts", summary_stats),
            ("balance_sheet", "key_facts", lambda kf, _kf: _balance_block(kf)),
            ("growth", "growth", lambda v, kf: _growth_block(v, kf, 1, 1)),
            ("margins", "margins", lambda v, _kf: _margins_block(v, 1, 1)),
            ("earnings", "earnings", lambda v, kf: _earnings_block(v, kf, 1)),
            ("valuation", "valuation", lambda v, kf: _valuation_block(v, kf)),
        ],
        "growth": [("growth", "growth", lambda v, kf: _growth_block(v, kf, _ANNUAL_ROWS, _QUARTER_ROWS))],
        "margins": [("margins", "margins", lambda v, _kf: _margins_block(v, _ANNUAL_ROWS, _QUARTER_ROWS))],
        "health": [
            ("health", "health", lambda v, _kf: _health_block(v)),
            ("balance_sheet", "key_facts", lambda kf, _kf: _balance_block(kf)),
        ],
        "earnings": [("earnings", "earnings", lambda v, kf: _earnings_block(v, kf, _EARNINGS_ROWS))],
        "estimates": [("estimates", "estimates", None)],     # licence-aware, below
        "valuation": [
            ("valuation", "valuation", lambda v, kf: _valuation_block(v, kf)),
            ("key_stats", "key_facts", lambda kf, _kf: _key_stats_block(kf, _VALUATION_KEY_STATS)),
        ],
        "segments": [("segments", "segments", lambda v, kf: _segments_block(v, kf))],
        "dividends": [("dividends", "dividends", lambda v, kf: _dividends_block(v, kf))],
    }[sec]


#: The fixed, vendor-free error of a build that failed outright (the exception stays in the log).
_BUILD_FAILED = "company financials could not be loaded right now (upstream fetch failed)"

# An ANSWERED absence (a complete build with nothing to show) — still never a zero, and never
# "the company has none": Caydex's data not carrying a figure is not the company lacking it.
_NOTHING_REPORTED = ("no figures reported in Caydex's data for this company - never treat that "
                     "as zero or as the company having none")


async def _build(sym: str, sec: str, period: Optional[FiscalPeriod] = None) -> Dict[str, Any]:
    """One section's result for `sym` (unfitted) — with `period` (only for `_PERIOD_SECTIONS`),
    that one fiscal period's rows. Never raises."""
    try:
        sources = _SECTION_SOURCES[sec] if period is None else _PERIOD_SOURCES[sec]
        loaded, status = await _gather_sources(sym, sources)
        kf = loaded.get("key_facts") if isinstance(loaded.get("key_facts"), dict) else None
        if kf is not None and kf.get("is_fund") is True:
            # Answered, not an outage: a fund has holdings, not company statements.
            return {
                "ticker": sym,
                "resolved_as": _resolved_as(sym, kf).replace("the listed company", "a fund"),
                "error": "a fund has no company financial statements",
                "note": ("This symbol is a fund, not a company: describe it from the fund's own "
                         "profile data if you have it, never from company financial statements."),
            }
        result: Dict[str, Any] = {
            "ticker": sym,
            "resolved_as": _resolved_as(sym, kf),
            "section": sec,
            "sections_available": _sections_available(),
            "today": f"{_today_et().isoformat()} (US Eastern)",
            "currency": _currency_block(kf),
        }
        if period is not None:
            result["period"] = (f"{period.label}: the company's own fiscal "
                                + ("quarter" if period.quarter else "year"))
            result["fiscal_calendar"] = _fiscal_calendar(kf, period)
        unavailable: List[str] = []
        shown = 0
        upstream_missing = 0
        for key, source, builder in _plan(sec, period):
            state = status.get(source)
            if state != "ok":
                unavailable.append(f"{key}: {_NOT_LOADED if state == 'not_loaded' else _FAILED}")
                upstream_missing += 1
                continue
            value = loaded.get(source)
            try:
                if source == "estimates":
                    if value is _UNLICENSED:
                        unavailable.append("estimates: analysts' estimates are not available in "
                                           "Caydex's data")
                        continue
                    block = (_estimates_block(value, kf)
                             if isinstance(value, dict) and value.get("available") else None)
                    if block is None:
                        # Neutral: an analysis build whose estimates leg failed is cached
                        # with an empty list, indistinguishable from "no coverage".
                        unavailable.append("estimates: none in Caydex's data right now - never "
                                           "treat that as zero or as no analyst coverage")
                        continue
                else:
                    block = builder(value, kf)
            except _Incomplete as e:
                logger.info("chat tool %s: %s block for %s came back empty from a partial build "
                            "(%s)", FINANCIALS_TOOL, key, sym, e)
                unavailable.append(f"{key}: did not load in this build - never treat it as "
                                   "zero or none")
                upstream_missing += 1
                continue
            except Exception as e:  # noqa: BLE001 — one malformed payload degrades one block
                logger.warning("chat tool %s: %s block for %s could not be read: %s: %s",
                               FINANCIALS_TOOL, key, sym, type(e).__name__, e, exc_info=True)
                unavailable.append(f"{key}: could not be read - never treat it as zero or none")
                upstream_missing += 1
                continue
            if block is None:
                unavailable.append(f"{key}: {_NOTHING_REPORTED}")
                continue
            result[key] = block
            shown += 1
        if sec == "dividends":
            result["splits"] = _splits_text(loaded.get("splits"), status.get("splits", "failed"))
        if any(isinstance(result.get(k), dict) and "history" in result[k]
               for k in ("growth", "margins")):
            result["older_periods"] = _HISTORY_NOTE if period is None else _HISTORY_NOTE_PERIOD
        if unavailable:
            result["unavailable"] = unavailable
        result["how_to_read"] = _HOW_TO_READ
        if shown == 0 and upstream_missing:
            # Nothing usable reached the model: the doors' refund gate counts this as a failed tool.
            result["error"] = "company financials could not be loaded right now (upstream fetch failed)"
            result["upstream"] = True
            result["note"] = "Say the figures could not be loaded right now; never say there are none."
        not_ok = {k: v for k, v in status.items() if v != "ok"}
        if not_ok:
            logger.info("chat tool %s: %s/%s served with %s", FINANCIALS_TOOL, sym, sec,
                        ", ".join(f"{k}={v}" for k, v in sorted(not_ok.items())))
        return result
    except Exception as e:  # noqa: BLE001 — a tool failure is data for the model
        from app.log_redaction import redact_secrets

        # The exception's class and text go to the LOG only: a class name carries a vendor
        # ("FMPRateLimitException") into model-facing text (final review 2026-10-09).
        logger.warning("chat tool %s failed for %s/%s%s: %s: %s", FINANCIALS_TOOL, sym, sec,
                       f" {period.label}" if period is not None else "",
                       type(e).__name__, redact_secrets(str(e))[:300], exc_info=True)
        return {"ticker": sym, "section": sec, "upstream": True,
                "error": _BUILD_FAILED,
                "note": "Company financials could not be loaded right now; never say there are none."}


def _inflight_build(sym: str, sec: str, period: Optional[FiscalPeriod] = None) -> "asyncio.Task":
    """The running build for (sym, sec[, period]), or a new one. A caller that goes away (a
    cancelled turn, the handler ceiling) never cancels it — it finishes and warms every cache it
    touched. A period build is keyed apart ("growth@Q3 FY2019"); it shares the SOURCE reads."""
    key = (sym, sec if period is None else f"{sec}@{period.label}")
    task = _inflight.get(key)
    if task is not None and _on_this_loop(task):
        logger.info("chat tool %s: joining the in-flight %s/%s build", FINANCIALS_TOOL, *key)
        return task
    task = asyncio.ensure_future(_build(sym, sec, period))
    _inflight[key] = task

    def _clear(t: "asyncio.Task", key: Tuple[str, str] = key) -> None:
        if _inflight.get(key) is t:
            _inflight.pop(key, None)

    task.add_done_callback(_clear)
    return task


# ── Size ────────────────────────────────────────────────────────────────────────

# List keys that may be shortened, cheapest first, each down to `keep` entries. Every list here
# is ordered newest (or nearest) first, so the entry dropped is the oldest (or farthest).
_TRIM = (
    ("quarterly", 0, 1), ("annual", 1, 1), ("eps", 2, 1), ("revenue", 2, 1), ("upcoming", 2, 1),
    ("per_share_by_year", 2, 1), ("periods", 2, 1), ("segments", 2, 3), ("splits", 3, 1),
    ("not_available", 3, 0), ("multiples", 4, 2), ("ratios", 4, 2),
)
_TRIM_RANK = {key: (rank, keep) for key, rank, keep in _TRIM}
# Whole blocks, least needed first — each dropped one is NAMED under `not_shown`.
_DROP_ORDER = ("balance_sheet", "margins", "valuation", "earnings", "growth", "key_stats",
               "health", "estimates", "segments", "dividends", "splits", "unavailable")


def _size(result: Dict[str, Any]) -> int:
    return len(json.dumps(result, default=str))


def _trimmable_lists(node: Any, out: List[Tuple[int, int, list]]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, list) and key in _TRIM_RANK:
                rank, keep = _TRIM_RANK[key]
                out.append((rank, keep, value))
            elif isinstance(value, (dict, list)):
                _trimmable_lists(value, out)
    elif isinstance(node, list):
        for value in node:
            _trimmable_lists(value, out)


def _fit(result: Dict[str, Any], budget: int) -> Dict[str, Any]:
    """Shrink to `budget` characters of JSON: the oldest periods go first (quarterly before
    annual, then the other lists), then whole blocks by name, least needed first; anything cut
    stamps `shortened`. The result ALWAYS fits."""
    if _size(result) <= budget:
        return result
    lists: List[Tuple[int, int, list]] = []
    _trimmable_lists(result, lists)
    while _size(result) > budget:
        candidates = [(rank, -len(lst), i) for i, (rank, keep, lst) in enumerate(lists)
                      if len(lst) > keep]
        if not candidates:
            break
        _rank, _neg, i = min(candidates)
        lists[i][2].pop()
        result["shortened"] = _SHORTENED
    dropped: List[str] = []
    for key in _DROP_ORDER:
        if _size(result) <= budget:
            break
        if key in result:
            result.pop(key)
            dropped.append(key)
            result["not_shown"] = dropped
            result["shortened"] = _SHORTENED
    if _size(result) > budget:
        # Last resort: the envelope alone.
        keep = {k: result[k] for k in ("ticker", "resolved_as", "section", "period", "today",
                                       "error", "upstream", "note", "section_note", "period_note")
                if k in result}
        keep["shortened"] = _SHORTENED
        result.clear()
        result.update(keep)
    return result


def _budget() -> int:
    from app.config import settings

    try:
        cap = int(settings.GEMINI_TOOL_RESULT_MAX_CHARS or 8000)
    except (TypeError, ValueError):
        cap = 8000
    return max(2000, cap - _BUDGET_MARGIN - _NOTE_RESERVE)


# ── Entry point ─────────────────────────────────────────────────────────────────

# A US share class typed with a dot ("BRK.B", "BF.B") — the screens' and the data's spelling is
# the dash form. ONLY a one-letter A/B/C class on an all-letter base: every other dotted suffix
# is an exchange ("SHOP.TO", "VOD.L", "7203.T", "MC.PA" — the licence covers global listings)
# and is kept as typed, or every source would be asked for a symbol that does not exist.
_US_CLASS_DOT_RE = re.compile(r"^([A-Z]{1,6})\.([ABC])$")


def _equity_symbol(sym: str) -> str:
    m = _US_CLASS_DOT_RE.match(sym)
    return f"{m.group(1)}-{m.group(2)}" if m else sym


def _refusal(ticker: Any) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """(symbol, None) for a symbol this tool covers, else (None, the answered refusal)."""
    sym = sanitize_symbol(ticker) if isinstance(ticker, str) else None
    if sym is None:
        return None, {"error": "invalid or missing ticker"}
    sym = _equity_symbol(sym)
    if detect_asset_class(sym) != "stock":
        # Answered, not an outage: an index, a commodity or a coin has no company statements.
        return None, {"ticker": sym,
                      "error": "company financials exist only for a listed company's stock",
                      "note": ("This symbol is not a listed company: describe it from its own "
                               "profile or market data if you have it, never from company "
                               "financial statements.")}
    return sym, None


async def fetch_company_financials(ticker: Any, section: Any = "summary",
                                   period: Any = None) -> Dict[str, Any]:
    """`section` of `ticker`'s reported figures (see the module docstring). `section` is
    normalised (`chat_tools.normalize_section`): an unknown value serves the summary with a
    fixed note, never an echo. `period` (`chat_tools.normalize_period`: a `FiscalPeriod`, or the
    model's raw text) picks one fiscal year or quarter in the summary, growth and margins
    sections; an unreadable one serves the latest periods with a fixed note, and on any other
    section the section is served as usual with a fixed note. Never raises; every caller gets
    its own copy of the result."""
    try:
        sym, refusal = _refusal(ticker)
        if refusal is not None:
            return refusal
        sec, note = normalize_section(section)
        per, period_note = normalize_period(period)
        if per is not None and sec not in _PERIOD_SECTIONS:
            per, period_note = None, _PERIOD_ELSEWHERE
        result = copy.deepcopy(await asyncio.shield(_inflight_build(sym, sec, per)))
        if note:
            result["section_note"] = note
        if period_note:
            result["period_note"] = period_note
        return _fit(result, _budget())
    except Exception as e:  # noqa: BLE001 — never raises (CancelledError is not an Exception)
        from app.log_redaction import redact_secrets

        # Log only: never the exception's class or text in the model-facing result.
        logger.warning("chat tool %s failed: %s: %s", FINANCIALS_TOOL, type(e).__name__,
                       redact_secrets(str(e))[:300], exc_info=True)
        return {"upstream": True, "error": _BUILD_FAILED,
                "note": "Company financials could not be loaded right now; never say there are none."}
