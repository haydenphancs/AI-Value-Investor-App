"""
Stock Overview Service — aggregates FMP data, computes derived stats
(performance, snapshots, sector info) for the TickerDetailView Overview tab.

Pattern follows etf_service.py: parallel FMP calls, in-memory caching,
helper functions for return calculations and snapshot ratings.
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import (
    FMPClient,
    FMPUnavailableException,
    get_fmp_client,
)
from app.integrations.finra_short_interest import get_short_interest
# The one definition of "undefined multiple" (Neg.) vs "unknown" (—). Shared with
# the Price snapshot card so the two halves of the SAME screen cannot disagree —
# they did, visibly: P/FCF read "Neg." two rows below a P/E reading "—".
from app.services.valuation_snapshot_service import (
    _fmt_ratio,
    build_price_snapshot,
)
from app.schemas.etf import (
    BenchmarkSummaryResponse,
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    MarketStatusResponse,
    PerformancePeriodResponse,
    RelatedTickerResponse,
)
from app.services.benchmark_math import format_since, overlapping_cagrs
from app.schemas.stock_overview import (
    CompanyProfileResponse,
    SectorIndustryResponse,
    SnapshotItemResponse,
    SnapshotMetricResponse,
    StockOverviewCoreResponse,
    StockOverviewResponse,
    snapshot_build_time,
)
from app.services.sector_benchmark_service import _FMP_SECTOR_MAP
from app.utils.market_hours import (
    ET,
    SESSION_AFTERHOURS,
    SESSION_CLOSED,
    SESSION_PREMARKET,
    SESSION_REGULAR,
    market_status_fields,
    previous_trading_day,
    session_phase,
    session_trading_date,
)
from app.integrations.fmp_entitlements import is_blocked_symbol
from app.services.asset_class import profile_is_fund, uses_coingecko_price
from app.services.price_service import price_source, profile_change_is_current
from app.services.market_movers_service import get_market_movers_service
# The close-cycle boundary every close-aligned cache in the app shares (weekday 18:00 ET).
# Bound at MODULE level, like index/etf/commodity, so a test can freeze this module's clock.
from app.services.ticker_report_cache import current_close_cycle_start
from app.utils.currency import currency_code

logger = logging.getLogger(__name__)


def _normalize_sector(name: str) -> str:
    """Map FMP sector name to canonical app sector name using the shared map."""
    return _FMP_SECTOR_MAP.get(name, name)

# ── In-memory cache ──────────────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_VOLATILE_TTL = 120            # 2 min for intraday data (quote, chart)
# The two fundamentals TTLs are CEILINGS: the bundle carries the daily price history, so
# it is also a miss once the close cycle turns (`_bundle_is_current`).
_FUNDAMENTALS_MEM_TTL = 3600   # 1 hour in-memory for fundamentals
_FUNDAMENTALS_DB_TTL_HOURS = 24  # 24 hours in Supabase for fundamentals
_SP_HIST_CACHE_TTL = 3600      # 1 hour for S&P historical (key carries the close cycle)
_SP_HIST_KEY_PREFIX = "spy_hist_full:"
# Stamped into the fundamentals bundle: the settled-session date its two histories were
# cut at. See `_bundle_is_current`.
_SETTLED_THROUGH_KEY = "history_settled_through"
# INTERNAL key `_fetch_fundamentals` adds and `_get_fundamentals` pops before anything is
# cached or returned: the names of the list slices whose RAW upstream answer was a list
# (checked before `_list` turns a failure or a non-list body into []). It is what tells a
# fund's genuine 200 [] key-metrics answer from a failed leg (see the fund waiver in
# `_get_fundamentals`). A missing key (a replaced `_fetch_fundamentals`) means no waiver.
_ANSWERED_LISTS_KEY = "_answered_lists"
# The LIVE price fields of `/stable/profile`. Never kept in either fundamentals tier
# (price_service invariant 2: never persist a live price). `_build_full_response` falls
# back to the profile's price / change / % when the quote leg fails (`_get_volatile`
# folds that failure to `{}`), so a cached copy of them was served up to 24 h later as
# a live header price with `change_known=True`. Stripped, a cache hit with a failed
# quote reaches the "no usable price" FMPUnavailableException instead. Only the request
# that fetched the bundle (profile seconds old) still falls back to them.
_LIVE_PROFILE_PRICE_FIELDS = ("price", "change", "changePercentage", "changesPercentage")
_CACHE_TTL = _VOLATILE_TTL     # default TTL for general cache
# Hard cap on live entries. Expired rows are only swept lazily on read of the
# same key, so without a cap this dict grows unbounded in the long-lived Railway
# process (one entry per ticker×range×key-type). Eviction is least-recently-
# written; a miss just re-fetches, so there's no correctness impact.
_CACHE_MAX_ENTRIES = 1024


def _cache_get(key: str, ttl: float = _CACHE_TTL) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any):
    # Move-to-end on write so the dict head is the least-recently-written, then
    # evict from the head once we exceed the cap.
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# ── Settled-close alignment for the daily price history (C7) ──────────────────
#
# Same defect the index / ETF / commodity screens were fixed for (F16-2, see the block in
# `index_service`): FMP `historical-price-eod/full` includes the CURRENT session's partial
# bar, and `_fetch_fundamentals` asks for history through today. The first viewer after
# 09:30 used to pin that ~snapshot bar into `stock_fundamentals_cache` as "today" for 24h
# (plus a 1h memory tier), so the Performance card (1M/YTD/1Y/3Y/5Y/10Y vs S&P), the
# Benchmark card and the 3M/6M/1Y daily chart on the first overview load all ended on an
# intraday price and never picked up that afternoon's move. SPY came from a separate 1h
# cache keyed by the UTC date, so the two series could also end on different sessions.
#
# Three parts, all keyed on ONE cutoff per bundle (`_settled_cutoff_date`):
#   1. both histories are cut to settled sessions before anything derives from or
#      persists them (`_fetch_fundamentals`);
#   2. a bundle is a MISS once the close cycle turns, in both tiers, whatever the 24h / 1h
#      ceiling says (`_bundle_is_current`) — otherwise the fix trades a wrong last bar for
#      a missing one until the rolling TTL runs out;
#   3. the SPY history is keyed on the same cutoff, so stock and S&P end on one session.


def _settled_cutoff_date(now: Optional[datetime] = None) -> str:
    """ISO date of the most recent SETTLED session — the last row a daily history may carry."""
    return current_close_cycle_start(now).astimezone(ET).date().isoformat()


def _settled_bars(historical: Any, now: Optional[datetime] = None) -> List[Dict]:
    """The daily rows whose session has settled (see `chart_helper.settled_bars`)."""
    from app.services.chart_helper import settled_bars

    return settled_bars(historical, _settled_cutoff_date(now))


def _bundle_is_current(
    bundle: Any, cached_at: datetime, now: Optional[datetime] = None
) -> bool:
    """True when a cached fundamentals bundle still describes the current close cycle.

    Two tests, both needed:
      * written on/after `current_close_cycle_start()` — the rule every close-aligned
        cache in the app uses;
      * its histories were cut at the CURRENT cycle's settled date (the stamp
        `_fetch_fundamentals` writes). The timestamp alone misses two cases: a row written
        by the pre-fix code inside the current cycle (no stamp, and it may still carry the
        in-progress bar), and a fetch that straddled 18:00 ET (cut at the old date, stamped
        in the new cycle — the settled bar would be missing for the whole cycle).
    """
    if not isinstance(bundle, dict):
        return False
    cycle_start = current_close_cycle_start(now)
    if cached_at < cycle_start:
        return False
    return bundle.get(_SETTLED_THROUGH_KEY) == cycle_start.astimezone(ET).date().isoformat()


def _without_live_price(bundle: Any) -> Any:
    """The fundamentals bundle as it may be CACHED: its `profile` without the live price
    fields (`_LIVE_PROFILE_PRICE_FIELDS`).

    A shallow copy with a new `profile` dict when there is something to drop — the
    caller's bundle is never mutated, so the request that fetched it still returns the
    full profile. A bundle with nothing to drop (or a non-dict bundle / profile) comes
    back as the SAME object.
    """
    if not isinstance(bundle, dict):
        return bundle
    profile = bundle.get("profile")
    if not isinstance(profile, dict) or not any(
        k in profile for k in _LIVE_PROFILE_PRICE_FIELDS
    ):
        return bundle
    stripped = dict(bundle)
    stripped["profile"] = {
        k: v for k, v in profile.items() if k not in _LIVE_PROFILE_PRICE_FIELDS
    }
    return stripped


def _bundle_needs_provider_pe(bundle: Any) -> bool:
    """Does this fundamentals bundle's filer need FMP's TTM multiple (`provider_pe_needed`)?"""
    if not isinstance(bundle, dict):
        return False
    return provider_pe_needed(bundle.get("profile"), bundle.get("income_quarterly"),
                              bundle.get("income_annual"))


def _fundamentals_mem_get(key: str) -> Optional[Dict[str, Any]]:
    """Tier-1 read for the fundamentals bundle: the 1h ceiling, plus a MISS (and eviction)
    once the close cycle turns."""
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if not _bundle_is_current(value, datetime.fromtimestamp(ts, tz=timezone.utc)):
        _cache.pop(key, None)
        return None
    return _cache_get(key, ttl=_FUNDAMENTALS_MEM_TTL)


# The four hardcoded _SECTOR_*_AVG tables that used to live here were DELETED.
# Their only consumer was `_build_valuation_snapshot`, the degraded fallback for
# the Price card, which now renders its multiples with NO peer comparison at all
# (2026-10-07: they are annual, the medians are not — see that method). Made-up
# averages are worse than no average: the star rating they produced was a
# confident wrong verdict on a card the user cannot tell apart from the real one
# (Technology 30.0 where the live benchmark reads 22).

# ── Number formatting helpers ────────────────────────────────────


def _fmt(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"{value:,.{decimals}f}"


def _fmt_large(value: Optional[float]) -> str:
    """Format large numbers without dollar sign."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,.0f}"


def _pct(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}%"


def profile_country_fields(raw_profile: Any) -> Dict[str, Any]:
    """``country`` and ``is_adr`` from the RAW FMP profile for the formatted
    `company_profile_cache` write (JSONB — no schema change). Ask Cay AI's ownership tool
    reads them to say a foreign issuer may be exempt from Form 4. Like `fund_flags`, only a
    real value is copied — a failed profile fetch writes nothing rather than a False or an
    empty country a reader would trust. Pure."""
    if not isinstance(raw_profile, dict):
        return {}
    out: Dict[str, Any] = {}
    country = raw_profile.get("country")
    if isinstance(country, str) and country.strip():
        out["country"] = country.strip()[:60]
    if isinstance(raw_profile.get("isAdr"), bool):
        out["is_adr"] = raw_profile["isAdr"]
    return out


_DISPLAY_NAME_MAX = 160
_DISPLAY_IMAGE_MAX = 500


def profile_display_fields(raw_profile: Any) -> Dict[str, str]:
    """``companyName`` and ``image`` (the logo URL) from the RAW FMP profile, for the shared
    `company_profile_cache` write. `whale_service` reads both for its holdings' names and logos
    and trusts the row for 7 days by its ``cached_at``; the merge keeps whatever the row holds,
    so a writer that RE-STAMPS the row carries today's values rather than leave a week-old write's
    under a fresh stamp (2026-10-09 review). Only a real, non-empty string is copied (an image
    only when it is an http(s) URL; both length-capped): a failed fetch writes nothing and the
    merge keeps the row's own. Neither key makes the row read as a raw profile (that needs a
    ``symbol`` / ``ipoDate`` / ``fullTimeEmployees``, which this write never carries). Pure."""
    if not isinstance(raw_profile, dict):
        return {}
    out: Dict[str, str] = {}
    name = raw_profile.get("companyName")
    if isinstance(name, str) and name.strip():
        out["companyName"] = name.strip()[:_DISPLAY_NAME_MAX]
    image = raw_profile.get("image")
    if isinstance(image, str):
        image = image.strip()
        if image.lower().startswith(("https://", "http://")) and len(image) <= _DISPLAY_IMAGE_MAX:
            out["image"] = image
    return out


def short_percent_of_float(
    short_interest: Any, float_shares: Any, key_metrics: Any = None,
) -> Optional[float]:
    """Short interest as a % of the public float — the Key Stats "Short % of Float" rule, in
    one place so Ask Cay AI's ownership tool states the figure the screen shows. Pure.

    1. ``shares_short`` (FINRA / Nasdaq) ÷ ``float_shares`` × 100, rounded to 2 dp, when both
       are positive;
    2. else the source's own pre-computed ``short_percent_of_float``;
    3. else FMP key metrics' ``shortPercentOutstanding`` / ``shortPercentFloat`` (a fraction
       below 1 is scaled to a percent) — rarely present on the stable API.
    None when none applies. Same behaviour as the inline block it replaced (2026-10-08).
    """
    si = short_interest if isinstance(short_interest, dict) else {}
    short_pct_val = None

    # Primary: compute from sharesShort (FINRA/Nasdaq) / floatShares (FMP)
    shares_short = si.get("shares_short")
    if shares_short and shares_short > 0 and float_shares and float_shares > 0:
        short_pct_val = round((shares_short / float_shares) * 100, 2)

    # Fallback 1: a source-supplied pre-computed short_percent_of_float
    if short_pct_val is None:
        short_pct_val = si.get("short_percent_of_float")

    # Fallback 2: try FMP key_metrics (rarely available on stable API)
    if short_pct_val is None:
        km = key_metrics[0] if key_metrics else {}
        raw = km.get("shortPercentOutstanding") or km.get("shortPercentFloat")
        if raw is not None:
            try:
                sp = float(raw)
                short_pct_val = sp * 100 if sp < 1 else sp
            except (ValueError, TypeError):
                pass
    return short_pct_val


# ── Key facts for Ask Cay AI (`StockOverviewService.get_key_facts`) ──────────
#
# What the Key Stats card prints when it does not know a figure. A key fact never carries one
# as a VALUE: a model reading "Short % of Float: N/A" may say "N/A" is the figure, or worse, 0.
_KEY_FACTS_PLACEHOLDERS = frozenset({"—", "-", "N/A", "n/a", ""})
# The live trading day: the screen merges the session bars into Open / Day High / Day Low, so
# a quote-only copy here could disagree with it — and the chat's price tool owns the live day.
_KEY_FACTS_INTRADAY = frozenset({
    "Open", "Previous Close", "Day High", "Day Low", "Volume", "Avg. Volume (3M)",
})
# Rows that need the live price; withheld (→ unavailable) when the quote did not load. Market
# Cap and the 52-week range included (final review 2026-10-09): with no quote they fell back to
# the cached bundle profile's `marketCap` / `range` (up to a close cycle old) and were served as
# current Key Stats under a basis saying the price-based rows were missing — the Overview refuses
# a priceless build, so the screen never shows them in this state.
_KEY_FACTS_NEEDS_PRICE = ("Market Cap", "52-Week High", "52-Week Low", "P/E (TTM)", "P/E (FWD)",
                          "Dividends")
_KEY_FACTS_SHORT_WAIT = 3.0
# Strong references to short-interest reads that outlived their wait (they finish and warm the
# integration's cache); a bare `ensure_future` result may be garbage-collected mid-flight.
_key_facts_side_tasks: set = set()

# ── One fundamentals build per ticker at a time (CLAUDE.md invariant 4, 2026-10-08) ──────────
#
# `_get_fundamentals` had no in-flight dedup, and a cold build is ~15 FMP calls (a daily history
# from 1900 for the stock AND for SPY among them). The overview, Ask Cay AI's financials tool
# (`get_key_facts`, which several sections of one chat round read at once) and a second viewer
# of the same cold ticker each started their own. A build is a Task held here until it finishes;
# a caller that goes away (a closed screen, a cancelled chat turn) never cancels it — it
# finishes and fills both cache tiers for the next reader.
_fundamentals_inflight: Dict[str, "asyncio.Task"] = {}
# The quote endpoint's `ratios-ttm` fetches still running (`_quote_pe_ratios`): one per ticker,
# however many viewers poll it.
_quote_pe_ratios_inflight: Dict[str, "asyncio.Task"] = {}
# FMP's TTM multiple is a daily figure: one hour, like the fundamentals bundle's Tier 1.
_QUOTE_PE_RATIOS_TTL = 3600
# The Overview's `company_profile_cache` writes still running (`get_overview`), held strongly so
# a request cancelled mid-flight never leaves its write to the garbage collector.
_profile_write_tasks: set = set()


def _profile_write_settled(task: "asyncio.Future") -> None:
    """Release a settled profile write; log one that raised (the method itself never should)."""
    _profile_write_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        exc = task.exception()
        logger.warning("Company profile write task failed: %s: %s", type(exc).__name__, exc)


def _awaitable_here(task: "asyncio.Task") -> bool:
    """A running task this event loop can await — never one a closed loop left behind."""
    try:
        return not task.done() and task.get_loop() is asyncio.get_running_loop()
    except RuntimeError:
        return False


def _fundamentals_build_done(ticker: str, task: "asyncio.Task") -> None:
    """Clear the in-flight slot and log a failed build — the failure is also raised to every
    caller still waiting, but one that nobody awaits any more must still leave a trace."""
    if _fundamentals_inflight.get(ticker) is task:
        _fundamentals_inflight.pop(ticker, None)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Fundamentals build failed for %s: %s: %s", ticker,
                       type(exc).__name__, exc, exc_info=(type(exc), exc, exc.__traceback__))


def _quote_pe_ratios_done(ticker: str, task: "asyncio.Task") -> None:
    """Clear the quote's `ratios-ttm` in-flight slot (the fetch logs its own failures)."""
    if _quote_pe_ratios_inflight.get(ticker) is task:
        _quote_pe_ratios_inflight.pop(ticker, None)


def _currency_code(raw: Any) -> Optional[str]:
    """A 3-letter ISO currency code ('USD', 'TWD'), upper-cased — or None. Never a guess.

    The ONE shared rule (`app.utils.currency.currency_code`). This copy used to upper-case
    BEFORE its ASCII check, so "ßU" read as the code "SSU" here and as unknown in the report."""
    return currency_code(raw)


def _newest_row(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The statement row with the latest period-end ``date`` (FMP sends newest first, but the
    order is not trusted); ``{}`` for no rows. A row with no date sorts oldest."""
    dated = [r for r in rows if isinstance(r, dict)]
    if not dated:
        return {}
    return max(dated, key=lambda r: str(r.get("date") or ""))


def _statement_number(row: Dict[str, Any], key: str) -> Optional[float]:
    """A finite statement figure, or None — never a bool, a string or NaN/inf read as data."""
    value = row.get(key)
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def _balance_sheet_totals(balance_annual: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The newest annual balance sheet's debt and cash totals, or None when there is none.

    ONE net-debt definition, FMP's own (final review 2026-10-09): total debt minus cash and cash
    EQUIVALENTS — short-term investments are not deducted. Net debt is the statement's ``netDebt``
    (FMP computes it that way: AAPL FY2024 106.63B − 29.94B = 76.69B); only when that is absent is
    it ``totalDebt − cashAndCashEquivalents``, and only when both are known. The old fallback
    subtracted cash PLUS short-term investments, so the same field meant two things across
    companies, and the block showed three figures that did not add up. Cash and cash equivalents
    and cash plus short-term investments are each carried under their OWN key, only when FMP
    reports that line (cash equivalents are never shown under the short-term-investments label).
    A missing figure is omitted — never 0 (a company with no reported debt line is "not
    reported", not debt-free).
    """
    row = _newest_row(balance_annual)
    if not row:
        return None
    total_debt = _statement_number(row, "totalDebt")
    cash_eq = _statement_number(row, "cashAndCashEquivalents")
    cash_sti = _statement_number(row, "cashAndShortTermInvestments")
    net_debt = _statement_number(row, "netDebt")
    if net_debt is None and total_debt is not None and cash_eq is not None:
        net_debt = total_debt - cash_eq
    figures = {
        "total_debt": total_debt,
        "cash_and_cash_equivalents": cash_eq,
        "cash_and_short_term_investments": cash_sti,
        "net_debt": net_debt,
    }
    out: Dict[str, Any] = {k: v for k, v in figures.items() if v is not None}
    if not out:
        return None
    date = row.get("date")
    out["period_end"] = date[:10] if isinstance(date, str) and date.strip() else None
    fiscal_year = row.get("fiscalYear") or row.get("calendarYear")
    if isinstance(fiscal_year, (str, int)) and not isinstance(fiscal_year, bool) and str(fiscal_year).strip():
        out["fiscal_year"] = str(fiscal_year).strip()[:8]
    out["reported_currency"] = _currency_code(row.get("reportedCurrency"))
    return out


# ── Key Stats: TTM EPS and the P/E basis (2026-10-09) ─────────────────────────
#
# Two defects in the same two rows, measured on recorded FMP answers
# (tests/fixtures/key_stats_pe/fmp_2026_10_09.json):
#
#   1. CURRENCY. The price is in the TRADING currency (USD for an ADR); TTM EPS comes from the
#      statements, in the REPORTING currency. TSM: $452.69 ÷ 434.95 TWD printed "1.04" against
#      a true ~29.6 (BABA 3.74 vs 23.5, HDB 0.14, TM 0.05; ASML / SAP ~11% off, plausible enough
#      to go unnoticed). FMP's `ratios-ttm` P/E is currency-consistent — it converts the market
#      cap into the reporting currency (TSM: 66.13T TWD ÷ TTM net income) — so a filer whose two
#      currencies differ is served THAT multiple (owner decision 2026-10-09). A price is never
#      converted. ADR share ratios need nothing: FMP's statement EPS is already per ADS on all
#      11 ADRs sampled (market cap ÷ price ÷ diluted shares = 0.97–1.01; TSM 1 ADS = 5 shares).
#   2. CADENCE. "Sum the last four quarterly rows" is two YEARS for a half-year filer: BHP's
#      `period=quarter` rows are half-years six months apart, so EPS (TTM) read 7.44 for 3.88
#      and P/E 11.5 for ~21.9.

# The P/E basis Key Stats used (also `get_key_facts`' ``pe_basis``).
PE_BASIS_LIVE = "live"                       # live price ÷ TTM EPS, one currency
PE_BASIS_PROVIDER = "provider_ttm"           # FMP's TTM multiple: the two currencies differ
PE_BASIS_UNCONFIRMED = "currency_unconfirmed"  # statements' currency known, the price's not
# Days between consecutive period ends: a quarter (52/53-week calendars and transition stubs
# included) and a half-year. Anything else — a missing period, a duplicate — is no TTM.
_QUARTER_GAP_DAYS = (45, 135)
_HALF_YEAR_GAP_DAYS = (150, 215)
# How far FMP's own TTM EPS may sit from ours before its multiple is refused (same sign
# required). Sampled ADRs agree within 0.3-7%; a different period, an ordinary-share basis
# or another currency is far outside it (EUR/USD alone is ~10%: the sign and period are what
# this proves, the currency is FMP's own consistency).
_PROVIDER_EPS_TOLERANCE = 0.15
# The `ratios-ttm` fields the bundle keeps (no absolute price — never persist one).
_PROVIDER_PE_FIELDS = ("priceToEarningsRatioTTM", "netIncomePerShareTTM")


def _utc_today_iso() -> str:
    """Today's UTC date, ISO — the "not yet ended" line for the forward estimate (a seam)."""
    return datetime.now(tz=timezone.utc).date().isoformat()


def _period_end(row: Dict[str, Any]) -> Optional[datetime]:
    raw = row.get("date")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _eps_value(row: Dict[str, Any]) -> Optional[float]:
    """`epsDiluted`, else `eps` — PRESENCE, not truthiness: a break-even 0.0 is a value."""
    val = row.get("epsDiluted")
    if val is None:
        val = row.get("eps")
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    return f if math.isfinite(f) else None


def _ttm_rows(income_quarterly: Any) -> Optional[List[Dict[str, Any]]]:
    """The `period=quarter` statement rows that make up the trailing twelve months, or None.

    Every row dated (FMP always dates them): newest first; four rows 45-135 days apart → those
    four; else, when the newest three are 150-215 days apart (a half-year filer) → the newest
    two; else None — a gap or a duplicate is never summed into a "TTM". A row without a
    parseable date leaves no cadence to check: the first four as given (the shape before dates
    were read). Pure.
    """
    rows = [r for r in income_quarterly if isinstance(r, dict)] if isinstance(income_quarterly, list) else []
    ends = [_period_end(r) for r in rows]
    if any(e is None for e in ends):
        return rows[:4] if len(rows) >= 4 else None
    paired = sorted(zip(ends, rows), key=lambda p: p[0], reverse=True)
    gaps = [(a[0] - b[0]).days for a, b in zip(paired, paired[1:])]
    if len(paired) >= 4 and all(_QUARTER_GAP_DAYS[0] <= g <= _QUARTER_GAP_DAYS[1] for g in gaps[:3]):
        return [r for _, r in paired[:4]]
    if len(paired) >= 3 and all(_HALF_YEAR_GAP_DAYS[0] <= g <= _HALF_YEAR_GAP_DAYS[1] for g in gaps[:2]):
        return [r for _, r in paired[:2]]
    return None


def _ttm_eps(income_quarterly: Any, symbol: Optional[str] = None) -> Optional[float]:
    """Trailing-twelve-month diluted EPS from the statements (`_ttm_rows`), rounded to cents —
    or None. A NEGATIVE EPS IS DATA, NOT A MISSING VALUE (a loss-maker's most important number).
    None when a row has no EPS, the sum is not finite, or the rows carry two currencies."""
    rows = _ttm_rows(income_quarterly)
    if rows is None:
        if isinstance(income_quarterly, list) and len(income_quarterly) >= 4:
            logger.warning("[eps-ttm-cadence] %s: the quarterly statement rows are not four "
                           "quarters or two half-years in a row — EPS (TTM) reads as unknown",
                           symbol or "?")
        return None
    codes = {c for c in (_currency_code(r.get("reportedCurrency")) for r in rows) if c}
    if len(codes) > 1:
        logger.warning("[eps-ttm-mixed-currency] %s: TTM rows report in %s — never summed",
                       symbol or "?", ", ".join(sorted(codes)))
        return None
    values = [_eps_value(r) for r in rows]
    if any(v is None for v in values):
        return None
    total = sum(values)
    return round(total, 2) if math.isfinite(total) else None


def key_stats_currencies(
    profile: Any, quote: Any, income_quarterly: Any, income_annual: Any = None,
) -> Tuple[Optional[str], Optional[str]]:
    """``(trading, statements)``: the currency the price is in (the profile's, else the
    quote's) and the one the EPS rows are in (the newest quarterly statement's, else the newest
    annual one's). Either is None when absent — never assumed USD. Pure."""
    profile = profile if isinstance(profile, dict) else {}
    quote = quote if isinstance(quote, dict) else {}
    trading = _currency_code(profile.get("currency")) or _currency_code(quote.get("currency"))
    statements = None
    for rows in (income_quarterly, income_annual):
        if isinstance(rows, list):
            statements = _currency_code(_newest_row(rows).get("reportedCurrency"))
            if statements:
                break
    return trading, statements


def pe_basis(trading: Optional[str], statements: Optional[str]) -> str:
    """Which P/E Key Stats may show. One currency (or the statements' unknown — the behaviour
    before currencies were read) → the live multiple; two KNOWN currencies → FMP's TTM
    multiple; statements known but the price's currency not → none (it cannot be checked)."""
    if statements is None or statements == trading:
        return PE_BASIS_LIVE
    return PE_BASIS_UNCONFIRMED if trading is None else PE_BASIS_PROVIDER


def provider_pe_needed(profile: Any, income_quarterly: Any, income_annual: Any) -> bool:
    """Does this bundle need FMP's `ratios-ttm` multiple (its two currencies differ)? Pure."""
    trading, statements = key_stats_currencies(profile, None, income_quarterly, income_annual)
    return pe_basis(trading, statements) == PE_BASIS_PROVIDER


def _provider_pe_fields(raw: Any) -> Dict[str, float]:
    """The finite `_PROVIDER_PE_FIELDS` of a `ratios-ttm` answer's first row ({} for none)."""
    row = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], dict) else {}
    out: Dict[str, float] = {}
    for key in _PROVIDER_PE_FIELDS:
        value = _statement_number(row, key)
        if value is not None:
            out[key] = value
    return out


def _provider_pe(ratios_ttm: Any, eps: Optional[float]) -> Optional[Tuple[float, float]]:
    """``(pe, price_in_reporting_currency)`` from FMP's TTM multiple — only when FMP's own TTM
    EPS agrees with ours (same sign, within `_PROVIDER_EPS_TOLERANCE`), the proof that the
    multiple is on the EPS basis shown beside it. None otherwise, or with no EPS of ours to
    check it against. The price is FMP's (P/E × its EPS), at FMP's daily timestamp. Pure."""
    if not isinstance(ratios_ttm, dict) or eps is None or eps == 0:
        return None
    pe = _statement_number(ratios_ttm, "priceToEarningsRatioTTM")
    nips = _statement_number(ratios_ttm, "netIncomePerShareTTM")
    if pe is None or nips is None or pe == 0 or nips == 0:
        return None
    if (pe > 0) != (nips > 0) or (nips > 0) != (eps > 0):
        return None
    if abs(nips - eps) / abs(eps) > _PROVIDER_EPS_TOLERANCE:
        return None
    price = pe * nips
    return (pe, price) if math.isfinite(price) and price > 0 else None


def _finite_multiple(value: float) -> Optional[float]:
    """A multiple rounded to cents, or None when the division overflowed (a near-zero
    denominator) — `_fmt_ratio` would otherwise print "inf"."""
    return round(value, 2) if math.isfinite(value) else None


# ── The quote endpoint's EPS / P/E (2026-10-09) ───────────────────────────────
#
# GET /stocks/{t}/quote (polled every ~15 s; its `eps` / `pe` feed the detail screen's FALLBACK
# Key Stats when the Overview fails, TickerDetailViewModel) summed the four newest quarterly
# EPS rows itself and divided the live price by them: the same two defects as above — TSM
# "1.04", BHP two years of earnings — plus `ttm_eps > 0`, which dropped a loss-maker's EPS.
# It now reads the same helpers (`_ttm_eps`, `key_stats_currencies`, `pe_basis`). What
# differs is what that fallback prints: EPS bare, with no currency code, and P/E only when
# > 0. Owner decisions 2026-10-09: a filer whose currencies differ gets FMP's TTM multiple
# (`StockOverviewService.quote_valuation`, one cached `ratios-ttm` call) and no EPS; a
# negative or 0.00 EPS is sent, with no P/E.


def quote_eps_pe(
    price: Any, eps: Optional[float], basis: str, ratios_ttm: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """The ``eps`` / ``pe`` keys GET /stocks/{t}/quote may send — a key is absent, never a
    guess. Pure.

    One currency (`PE_BASIS_LIVE`): ``eps`` whatever its sign (0.00 included), and ``pe`` =
    price ÷ eps only for a positive EPS and price. Two currencies (`PE_BASIS_PROVIDER`): ``pe``
    = FMP's TTM multiple when it checks out against our EPS (`_provider_pe`) and is positive;
    no ``eps`` — the fallback prints it bare, and 434.95 TWD beside a $ price reads as dollars.
    The price's currency unknown (`PE_BASIS_UNCONFIRMED`): neither.
    """
    out: Dict[str, float] = {}
    if eps is None or not math.isfinite(eps):
        return out
    if basis == PE_BASIS_LIVE:
        out["eps"] = eps
        p = None if isinstance(price, bool) else _finite(price)
        if eps > 0 and p is not None and p > 0:
            pe = _finite_multiple(p / eps)
            if pe is not None:
                out["pe"] = pe
    elif basis == PE_BASIS_PROVIDER and eps > 0:
        provider = _provider_pe(ratios_ttm, eps)
        if provider is not None and provider[0] > 0:
            pe = _finite_multiple(provider[0])
            if pe is not None:
                out["pe"] = pe
    return out


def _nearest_forward_eps(analyst_est: Any, today_iso: str) -> Optional[float]:
    """The analysts' EPS for the nearest fiscal year not yet ended — or None (0 is FMP's
    absent). A NEGATIVE estimate is a real forecast loss (MRNA 2027: -4.90)."""
    if not isinstance(analyst_est, list):
        return None
    future = [e for e in analyst_est
              if isinstance(e, dict) and isinstance(e.get("date"), str) and e["date"] >= today_iso]
    if not future:
        return None
    nearest = min(future, key=lambda e: e["date"])
    return _safe_float(nearest, "epsAvg") or _safe_float(nearest, "estimatedEpsAvg") or None


def fund_flags(raw_profile: Any) -> Dict[str, bool]:
    """`isEtf` / `isFund` from the RAW FMP profile, for the formatted `company_profile_cache`
    write. This row is shared with whale_service's raw write (last write wins WHOLE), and
    readers use the flags to tell a fund from a company (push routing, the Updates tab's
    "Ask Cay AI" ETF grounding) — without them every detail view of SPY erased "is an ETF"
    for a week. Only a real bool is copied: a failed profile fetch must never write a
    False, which readers treat as a definite, cacheable "not a fund". Pure."""
    if not isinstance(raw_profile, dict):
        return {}
    return {k: raw_profile[k] for k in ("isEtf", "isFund") if isinstance(raw_profile.get(k), bool)}


def _safe_float(d: Dict, key: str, default: float = 0.0) -> float:
    """Safely extract a FINITE float from a dict.

    Non-finite (NaN/Inf) is rejected → the default. A NaN/Inf reaching a REQUIRED
    response Double (current_price / price_change / …) would serialize as a
    non-standard JSON ``NaN``/``Infinity`` token and crash the iOS JSONDecoder on
    the whole response. Guarding here protects BOTH the full ``/overview`` and the
    fast-core price extraction (both route price/change/etc. through this helper)."""
    v = d.get(key)
    if v is None:
        return default
    try:
        f = float(v)
    except (ValueError, TypeError):
        return default
    return f if math.isfinite(f) else default


def _finite(v: Any) -> Optional[float]:
    """Coerce to a finite float, or None (drops NaN/Inf and non-numeric).

    Used for chart OHLCV — those flow into ``chart_data: List[Dict[str, Any]]``
    (no Pydantic guard) and a non-finite value would serialize as an invalid
    JSON ``NaN``/``Infinity`` token and crash the iOS decode of the whole
    response. Mirrors ``_safe_float`` but returns None instead of a default so
    optional OHLC fields stay absent rather than fabricated as 0.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return f if math.isfinite(f) else None


def _safe_int(v: Any, default: int = 0) -> int:
    """Coerce an FMP field to an int, tolerating comma-grouped / decimal strings.

    FMP ``/stable/profile`` returns ``fullTimeEmployees`` as a string; a bare
    ``int("164,000")`` / ``int("12345.0")`` raises ValueError, which — since the
    profile is built with no surrounding try/except — would 500 the ENTIRE
    /overview response (blanking a screen whose price/chart/stats all succeeded).
    Parse defensively; non-numeric / non-finite → ``default``.
    """
    if v is None or isinstance(v, bool):
        return default
    try:
        f = float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return default
    return int(f) if math.isfinite(f) else default


def _first_present_or_none(*sources: tuple) -> Optional[float]:
    """The first (dict, key) whose value is PRESENT (key exists, non-None, finite), or
    None when NO source carries one — the three-state read `change_known` needs. A
    coerced 0.0 cannot tell "no source had a change" from "a flat day", and the
    equity header used to paint the former as "+0.00 (+0.00%)" in green."""
    for d, key in sources:
        if not isinstance(d, dict):
            continue
        v = d.get(key)
        if v is None or isinstance(v, bool):
            continue
        try:
            f = float(v)
        except (ValueError, TypeError):
            continue
        if math.isfinite(f):
            return f
    return None


def _first_present_float(*sources: tuple, default: float = 0.0) -> float:
    """Return the first (dict, key) whose value is PRESENT (key exists, non-None,
    finite) — so a legitimate ``0.0`` from a fresher source is NOT discarded in
    favour of a staler fallback the way Python ``or`` (0.0 is falsy) would. Used
    for price change / change%, where 0.0 is a valid flat-day value.
    """
    v = _first_present_or_none(*sources)
    return default if v is None else v


# ── Return computation helpers (same as etf_service) ─────────────


def _compute_return(prices: List[Dict], days_back: int) -> Optional[float]:
    """Compute % return over the last N trading days."""
    if not prices or len(prices) < 2:
        return None
    # Not enough history to cover the requested window: return None so the caller
    # OMITS this period rather than mislabeling a shorter (e.g. since-inception)
    # return under a "3Y"/"5Y"/"10Y" label (a young stock would otherwise show its
    # full-history return identically for 3Y/5Y/10Y). Genuine since-inception rows
    # use a dedicated helper, not this fallback.
    if len(prices) <= days_back:
        return None
    # Finite-guard both ends: a NaN/Inf close is truthy and slips past
    # `not start`/`start == 0`, producing a NaN change_percent. A NaN anywhere in the
    # response serializes to an invalid-JSON `NaN` token and crashes the iOS decode of
    # the WHOLE stock detail screen. Matches index/commodity/crypto/etf.
    from app.services.chart_helper import _finite_or_none
    start = _finite_or_none(prices[-(days_back + 1)].get("close") or prices[-(days_back + 1)].get("adjClose"))
    end = _finite_or_none(prices[-1].get("close") or prices[-1].get("adjClose"))

    if not start or not end or start == 0:
        return None
    return ((end - start) / start) * 100


def _compute_ytd_return(prices: List[Dict], now: Optional[datetime] = None) -> Optional[float]:
    """YTD from the previous year's last close — the one shared definition
    (`chart_helper.ytd_return`), so the stock, ETF, index, crypto, commodity and theme
    screens cannot disagree on the same metric."""
    from app.services.chart_helper import ytd_return

    return ytd_return(prices, now)


def _get_market_status() -> MarketStatusResponse:
    """Current session, delegated to the one holiday/half-day-aware implementation.

    This used to be a local copy of weekday+hour arithmetic — one of three — and it
    knew nothing about market holidays or the 13:00 ET half-days, so it reported
    "open" at 11:00 on Thanksgiving and until 16:00 the Friday after. `market_hours`
    owns the calendar (and `home_dashboard_service` already delegated to it).
    """
    return MarketStatusResponse(**market_status_fields())
def _parse_historical(hist_raw) -> List[Dict]:
    """Parse FMP historical prices into sorted list (oldest-first)."""
    historical: List[Dict] = []
    if isinstance(hist_raw, dict):
        historical = hist_raw.get("historical", [])
    elif isinstance(hist_raw, list):
        historical = hist_raw
    # A malformed payload (`{"historical": "..."}`, or rows that are not dicts) used to
    # raise here — `.sort` on a str, `.get` on a str — and 502 the whole overview.
    if not isinstance(historical, list):
        return []
    historical = [p for p in historical if isinstance(p, dict)]
    # `date` may be an explicit JSON null (not just absent); `or ""` avoids a
    # None<str TypeError when sorting a malformed FMP row.
    historical.sort(key=lambda p: p.get("date") or "")
    return historical


# ── Session Open / Day High / Day Low ─────────────────────────────
#
# `/stable/quote` is outside the licence, and the profile-backed quote row that replaced
# it (`PriceService._shape`) carries no `open` / `dayHigh` / `dayLow` — so the three Key
# Statistics rows read "—" for every stock, all day (TestFlight, build 1.0 (8): AAPL at
# 20:09, after the close). Both entitled sources are used here:
#   * the EOD row for the session (`historical-price-eod/full`; probed 2026-09-16 22:17
#     ET: today's row is present after the close with open/high/low/close/volume), and
#   * the session's own 5-minute bars (regular hours only) while it is live.
# Which session: the one `price` / `previousClose` already describe — today during the
# regular session and after hours, the LAST COMPLETED session in pre-market and while
# closed (a Saturday reads Friday's range beside Friday's close). A T-1 row must never be
# printed under today's labels: `chat_service._day_range` documents that gate too.

_OHL_LOOKBACK_DAYS = 7


def _ohl_fields(
    open_: Any, high: Any, low: Any, *, ticker: str = "", source: str = ""
) -> Dict[str, float]:
    """The quote keys `_build_key_statistics` reads, from raw values — only the ones that
    survive: finite, positive, and mutually consistent. ABSENT when unknown, never 0.0 —
    the index screen once rendered `Open 0.00` as a fact from exactly that default."""
    o, h, l = _finite(open_), _finite(high), _finite(low)
    o = o if (o is not None and o > 0) else None
    h = h if (h is not None and h > 0) else None
    l = l if (l is not None and l > 0) else None
    if h is not None and l is not None and l > h:
        logger.info("[ohl-inconsistent] %s %s: low %.4f > high %.4f — range dropped",
                    ticker, source, l, h)
        h = l = None
    if o is not None and h is not None and l is not None and not (l <= o <= h):
        logger.info("[ohl-inconsistent] %s %s: open %.4f outside [%.4f, %.4f] — open dropped",
                    ticker, source, o, l, h)
        o = None
    out: Dict[str, float] = {}
    if o is not None:
        out["open"] = o
    if h is not None:
        out["dayHigh"] = h
    if l is not None:
        out["dayLow"] = l
    return out


def _session_ohl_from_eod(rows: Any, session_iso: str, *, ticker: str = "") -> Dict[str, float]:
    """Open/high/low from the EOD row dated exactly ``session_iso``, else ``{}``."""
    hist = _parse_historical(rows) if isinstance(rows, (list, dict)) else []
    last = hist[-1] if hist else None
    if not isinstance(last, dict):
        return {}
    if str(last.get("date") or "")[:10] != session_iso:
        return {}
    return _ohl_fields(last.get("open"), last.get("high"), last.get("low"),
                       ticker=ticker, source="eod")


def _session_ohl_from_bars(bars: Any, session_iso: str, *, ticker: str = "") -> Dict[str, float]:
    """Open/high/low of ``session_iso``'s REGULAR-HOURS bars, else ``{}``.

    Regular hours only: with Extended Hours on, the bars in hand start at 04:00 ET and
    the 04:00 print is not the session's Open. Daily rows (no time of day) are ignored —
    they are the EOD source's job."""
    from app.services.chart_helper import _filter_regular_hours

    if not isinstance(bars, list):
        return {}
    same_day = [
        b for b in bars
        if isinstance(b, dict)
        and len(str(b.get("date") or "")) > 10
        and str(b.get("date"))[:10] == session_iso
    ]
    regular = sorted(_filter_regular_hours(same_day), key=lambda b: b.get("date") or "")
    if not regular:
        return {}
    highs = [v for v in (_finite(b.get("high")) for b in regular) if v is not None and v > 0]
    lows = [v for v in (_finite(b.get("low")) for b in regular) if v is not None and v > 0]
    return _ohl_fields(
        regular[0].get("open"),
        max(highs) if highs else None,
        min(lows) if lows else None,
        ticker=ticker, source="bars",
    )


async def session_ohl_for(fmp: Any, ticker: str, *, bars: Any = None) -> Dict[str, float]:
    """`{open, dayHigh, dayLow}` for the session the header price describes, else ``{}``.

    The session-selection half of `StockOverviewService._get_session_ohl`, at module level
    so every screen priced from a profile-backed quote shares ONE implementation — the
    commodity screen's metal funds (GLD/SLV/PPLT/PALL) read "—" for these three rows for
    the same reason stocks did (TestFlight 1.0 (9)). Takes the caller's FMP client so each
    service keeps its own seam. RAISES on an upstream failure; callers own the fallback
    and the cache. Sources, in order:
      1. ``bars`` already in hand, when they describe today's session — no extra call;
      2. the session's EOD row (``historical-price-eod/full``, ≤7-day window, date-gated);
      3. one single-day 5-minute fetch while today's EOD row has not been written yet.
    """
    phase = session_phase()
    session = session_trading_date()
    # `session_trading_date()` flips to today at 04:00 ET (pre-market), but the
    # numbers on screen still describe the LAST COMPLETED session until 09:30.
    target = previous_trading_day(session) if phase == SESSION_PREMARKET else session
    target_iso = target.isoformat()
    # The bars in hand (and the single-day rescue below) describe TODAY's
    # session; pre-market and a weekend describe an earlier one. `target ==
    # session` is that test — not the phase, which reads `closed` on a half-day
    # afternoon and after 20:00 while the session's own bars still exist.
    describes_todays_session = target == session

    result: Dict[str, float] = {}
    if describes_todays_session and bars:
        result = _session_ohl_from_bars(bars, target_iso, ticker=ticker)
    if not result:
        frm = (target - timedelta(days=_OHL_LOOKBACK_DAYS)).isoformat()
        rows = await fmp.get_historical_prices(ticker, frm, target_iso)
        result = _session_ohl_from_eod(rows, target_iso, ticker=ticker)
    # The single-day rescue applies whenever the numbers on screen describe
    # TODAY's session and FMP has not written its EOD row yet — not only while
    # the phase is live: on a half-day afternoon `session_phase()` is already
    # `closed` while `session_trading_date()` is today, and the same gap exists
    # after 20:00 until the row lands.
    if not result and describes_todays_session:
        fresh = await fmp.get_intraday_prices(
            ticker, interval="5min", from_date=target_iso, to_date=target_iso
        )
        result = _session_ohl_from_bars(fresh, target_iso, ticker=ticker)
    return result


def _extract_chart_data(prices: List[Dict], chart_range: str) -> List[Dict]:
    """Extract OHLCV data for the requested chart range.

    Includes extra TRADING DAYS before the display range so technical indicators can warm
    up. This slices by bar count, not by date like the other four detail services, so it
    uses `_WARMUP_DATA_POINTS` (bars) rather than `daily_range_days` (calendar days) --
    the same window, counted in the unit this function actually works in.

    The warm-up used to be 50 bars, sized for MACD (~34) and RSI (~14) only. MA(200) needs
    200, so the overlay drew nothing until 200 bars into the visible window -- i.e. never,
    on a 3M chart. 210 covers every indicator the client offers.
    """
    from app.services.chart_helper import _WARMUP_DATA_POINTS

    range_days = {
        "1D": 2, "1W": 5,
        "3M": 63 + _WARMUP_DATA_POINTS,
        "6M": 126 + _WARMUP_DATA_POINTS,
        "1Y": 252 + _WARMUP_DATA_POINTS,
        "5Y": 1260 + _WARMUP_DATA_POINTS,
        "ALL": 999999,
    }
    days = range_days.get(chart_range, 63 + _WARMUP_DATA_POINTS)
    relevant = prices[-days:] if len(prices) > days else prices
    result = []
    for p in relevant:
        date = p.get("date")
        if not date:
            continue
        close = _finite(p.get("close") or p.get("adjClose"))
        if close is None or close <= 0:
            continue
        result.append({
            "date": date,
            "open": _finite(p.get("open")),
            "high": _finite(p.get("high")),
            "low": _finite(p.get("low")),
            "close": close,
            "volume": _finite(p.get("volume")),
        })
    return result


# ── Main service ─────────────────────────────────────────────────


class StockOverviewService:
    """Aggregates FMP data for the Stock Detail Overview tab."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()
        from app.database import get_supabase
        self.supabase = get_supabase()

    async def get_overview(
        self, ticker: str, chart_range: str = "3M", interval: str = None,
        extended_hours: bool = False,
    ) -> StockOverviewResponse:
        """
        Split-cache architecture:
          - Volatile data (quote, chart): 120s in-memory only
          - Fundamental data (P/E, EPS, ownership, etc.): 24h Supabase + 1h in-memory
          - Combined into one clean JSON response for the frontend
        """
        ticker = ticker.upper()

        # Check full response cache (120s — volatile freshness window)
        overview_key = f"stock_overview:{ticker}:{chart_range}:{interval or 'default'}:{extended_hours}"
        cached_full = _cache_get(overview_key, ttl=_VOLATILE_TTL)
        if cached_full is not None:
            return cached_full

        # ── Fetch fundamentals (cached 24h), volatile (live), and snapshot services in parallel ──
        from app.services.profitability_snapshot_service import get_profitability_snapshot_service
        from app.services.growth_snapshot_service import get_growth_snapshot_service
        from app.services.valuation_snapshot_service import get_valuation_snapshot_service
        from app.services.health_snapshot_service import get_health_snapshot_service
        from app.services.ownership_snapshot_service import get_ownership_snapshot_service
        fund_task = self._get_fundamentals(ticker)
        # A Task (not a bare coroutine) so the session-range fetch below can await the
        # SAME volatile result — reusing the 1D/1W bars already in hand — while the
        # gather still runs everything concurrently.
        vol_task = asyncio.ensure_future(
            self._get_volatile(ticker, chart_range, interval, extended_hours)
        )
        ohl_task = self._get_session_ohl(ticker, volatile=vol_task)
        sector_perf_task = get_market_movers_service().get_sector_performance()
        industry_perf_task = get_market_movers_service().get_industry_performance()
        prof_task = get_profitability_snapshot_service().get_profitability_snapshot(ticker)
        growth_task = get_growth_snapshot_service().get_growth_snapshot(ticker)
        val_task = get_valuation_snapshot_service().get_valuation_snapshot(ticker)
        health_task = get_health_snapshot_service().get_health_snapshot(ticker)
        ownership_task = get_ownership_snapshot_service().get_ownership_snapshot(ticker)
        fundamentals, volatile, live_sector_perf, live_industry_perf, prof_snapshot, growth_snapshot, val_snapshot, health_snapshot, ownership_snapshot, session_ohl = await asyncio.gather(
            fund_task, vol_task, sector_perf_task, industry_perf_task, prof_task, growth_task, val_task, health_task, ownership_task, ohl_task, return_exceptions=True,
        )
        # `fundamentals` and `volatile` are load-bearing (dict-mutated + price
        # source below). With return_exceptions=True a failed FMP fan-out returns
        # an Exception object here; using it as a dict would raise a bare
        # TypeError/AttributeError -> 502. Re-raise so the typed exception maps to
        # a proper ErrorCode instead of an opaque crash. (The 5 snapshots below
        # degrade to None; these two cannot.)
        if isinstance(fundamentals, Exception):
            logger.error(f"Fundamentals fetch failed for {ticker}: {type(fundamentals).__name__}: {fundamentals}")
            raise fundamentals
        if isinstance(volatile, Exception):
            logger.error(f"Volatile (quote/chart) fetch failed for {ticker}: {type(volatile).__name__}: {volatile}")
            raise volatile
        # Session Open / Day High / Day Low, merged into a NEW dict: `price_service`
        # caches the quote row by reference and ~20 call sites share it. `_get_session_ohl`
        # never raises, but `return_exceptions=True` makes the isinstance check free.
        if isinstance(session_ohl, dict) and session_ohl and isinstance(volatile.get("quote"), dict):
            volatile = {**volatile, "quote": {**volatile["quote"], **session_ohl}}
        # Override cached sector/industry perf with fresh data
        if not isinstance(live_sector_perf, Exception) and isinstance(live_sector_perf, list) and live_sector_perf:
            fundamentals["sector_perf"] = live_sector_perf
        if not isinstance(live_industry_perf, Exception) and isinstance(live_industry_perf, list) and live_industry_perf:
            fundamentals["industry_perf"] = live_industry_perf
        # Handle snapshot failures gracefully
        if isinstance(prof_snapshot, Exception):
            logger.warning(f"Profitability snapshot failed for {ticker}: {prof_snapshot}")
            prof_snapshot = None
        if isinstance(growth_snapshot, Exception):
            logger.warning(f"Growth snapshot failed for {ticker}: {growth_snapshot}")
            growth_snapshot = None
        if isinstance(val_snapshot, Exception):
            logger.warning(f"Valuation snapshot failed for {ticker}: {val_snapshot}")
            val_snapshot = None
        if isinstance(health_snapshot, Exception):
            logger.warning(f"Health snapshot failed for {ticker}: {health_snapshot}")
            health_snapshot = None
        if isinstance(ownership_snapshot, Exception):
            logger.warning(f"Ownership snapshot failed for {ticker}: {ownership_snapshot}")
            ownership_snapshot = None

        # ── Ensure short interest is populated (FINRA/Nasdaq) ──
        if not fundamentals.get("short_interest"):
            from app.integrations.finra_short_interest import get_short_interest
            fundamentals["short_interest"] = await get_short_interest(ticker)

        # ── Fetch IPO-era price for true all-time CAGR ──────────────
        # FMP caps historical data at 5,000 rows; for old stocks like AAPL
        # (IPO 1980) we fetch the first few days around the IPO date separately.
        #
        # ⚠️ This price no longer moves the benchmark card. The same 5,000-row cap applies
        # to SPY, so the shared stock-vs-S&P window floors at the cap date either way and
        # `benchmark_math` falls through to the dense ranges (measured: AAPL's all-time row
        # is identical with and without a 1993 SPY anchor). It still carries the degraded
        # path where SPY history fails outright. The fetch is a candidate for deletion.
        ipo_price_data = None
        profile = fundamentals.get("profile", {})
        ipo_date_str = profile.get("ipoDate", "")
        if ipo_date_str:
            try:
                from datetime import date as _ipo_date
                ipo_end = (_ipo_date.fromisoformat(ipo_date_str) + timedelta(days=30)).isoformat()
                ipo_raw = await self.fmp.get_historical_prices(ticker, ipo_date_str, ipo_end)
                if isinstance(ipo_raw, dict):
                    ipo_prices = ipo_raw.get("historical", [])
                elif isinstance(ipo_raw, list):
                    ipo_prices = ipo_raw
                else:
                    ipo_prices = []
                if ipo_prices:
                    ipo_prices.sort(key=lambda p: p.get("date") or "")
                    ipo_price_data = {
                        "price": ipo_prices[0].get("close") or ipo_prices[0].get("adjClose"),
                        "date": ipo_prices[0].get("date") or "",
                    }
            except Exception as e:
                logger.warning(f"IPO price fetch failed for {ticker}: {e}")

        # ── Build response from both data sources ─────────────────
        response = self._build_full_response(
            ticker, fundamentals, volatile, chart_range, interval, extended_hours,
            profitability_snapshot=prof_snapshot,
            growth_snapshot=growth_snapshot,
            valuation_snapshot=val_snapshot,
            health_snapshot=health_snapshot,
            ownership_snapshot=ownership_snapshot,
            ipo_price_data=ipo_price_data,
        )

        # Cache the formatted profile for chat AI context: a read-merge-write of the shared
        # row (`_upsert_company_profile_db`), off the event loop — see the note where it used
        # to live, inside the synchronous `_build_full_response`. Only from a REAL profile:
        # with none, every field is a placeholder ("N/A"), and writing those over the row
        # would erase a CEO / sector another writer stored. Started here and awaited after the
        # related tickers, so its two round trips overlap that fetch instead of adding to it.
        profile_write: Optional["asyncio.Future"] = None
        raw_profile = fundamentals.get("profile")
        if isinstance(raw_profile, dict) and raw_profile:
            profile_write = asyncio.ensure_future(asyncio.to_thread(
                self._upsert_company_profile_db,
                ticker,
                {
                    "description": response.company_profile.description,
                    "ceo": response.company_profile.ceo,
                    "founded": response.company_profile.founded,
                    "employees": response.company_profile.employees,
                    "headquarters": response.company_profile.headquarters,
                    "website": response.company_profile.website,
                    "sector": response.sector_industry.sector,
                    "industry": response.sector_industry.industry,
                    "sector_performance": response.sector_industry.sector_performance,
                    "industry_rank": response.sector_industry.industry_rank,
                    **fund_flags(fundamentals.get("profile")),
                    **profile_country_fields(fundamentals.get("profile")),
                    **profile_display_fields(fundamentals.get("profile")),
                },
            ))
            # Held strongly until it settles: a viewer who leaves mid-request (this coroutine
            # cancelled at the await below) must not orphan the write to the garbage collector.
            _profile_write_tasks.add(profile_write)
            profile_write.add_done_callback(_profile_write_settled)
        else:
            logger.info("Company profile for %s not cached: the fundamentals carry no profile "
                        "(nothing real to write over the shared row)", ticker)

        # Related tickers (async call, uses its own caching)
        response.related_tickers = await self._build_related_tickers(ticker)
        if profile_write is not None:
            # The write never raises (it logs), but a failure here must never fail the screen.
            try:
                await asyncio.shield(profile_write)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — best effort; the method logs its own
                logger.warning("Company profile write for %s raised: %s: %s", ticker,
                               type(e).__name__, e)

        # Cache full response for 120s (volatile freshness)
        _cache_set(overview_key, response)
        return response

    # ── The quote endpoint's EPS / P/E ─────────────────────────────

    async def quote_valuation(
        self, ticker: str, *, price: Any, quote: Any, profile: Any, income_quarterly: Any,
    ) -> Dict[str, float]:
        """The ``eps`` / ``pe`` for GET /stocks/{t}/quote (`quote_eps_pe`), from the rows that
        endpoint already fetched. `ratios-ttm` is read only for a filer whose currencies differ
        AND whose EPS is positive (a loss-maker gets no P/E either way). Never raises on an
        upstream failure: the price is what the poll is for, so a failed leg omits ``pe``."""
        trading_ccy, statement_ccy = key_stats_currencies(profile, quote, income_quarterly)
        basis = pe_basis(trading_ccy, statement_ccy)
        eps = _ttm_eps(income_quarterly, ticker)
        ratios = None
        if basis == PE_BASIS_PROVIDER and eps is not None and eps > 0:
            ratios = await self._quote_pe_ratios(ticker)
        out = quote_eps_pe(price, eps, basis, ratios)
        if basis != PE_BASIS_LIVE:
            # INFO, not WARNING: this is the designed outcome for every poll of such a filer
            # (~15 s). The fetch failure and the Overview's own EPS mismatch log at WARNING.
            logger.info("[quote-pe-basis] %s: statements in %s, price in %s (%s) — eps omitted, "
                        "pe %s", ticker, statement_ccy, trading_ccy or "unknown", basis,
                        "from FMP's TTM multiple" if "pe" in out else "omitted")
        return out

    async def _quote_pe_ratios(self, ticker: str) -> Optional[Dict[str, float]]:
        """FMP's TTM multiple for the quote (`_provider_pe_fields`), or None when the leg
        failed. In-memory only (`_QUOTE_PE_RATIOS_TTL`), one fetch per ticker at a time: a
        single cheap call whose Overview copy the fundamentals bundle already persists, so it
        gets no Supabase tier. FMP's answered empty list is cached ({}); a failure is not."""
        key = f"quote_pe_ratios:{ticker}"
        hit = _cache_get(key, ttl=_QUOTE_PE_RATIOS_TTL)
        if hit is not None:
            return hit
        running = _quote_pe_ratios_inflight.get(ticker)
        if running is not None and _awaitable_here(running):
            return await asyncio.shield(running)
        task = asyncio.ensure_future(self._fetch_quote_pe_ratios(ticker, key))
        _quote_pe_ratios_inflight[ticker] = task
        task.add_done_callback(lambda t, ticker=ticker: _quote_pe_ratios_done(ticker, t))
        return await asyncio.shield(task)

    async def _fetch_quote_pe_ratios(self, ticker: str, key: str) -> Optional[Dict[str, float]]:
        try:
            raw = await self.fmp.get_ratios_ttm(ticker)
        except Exception as e:
            logger.warning("[quote-pe-provider-leg] ratios-ttm failed for %s (%s: %s) — the quote "
                           "omits pe; not cached", ticker, type(e).__name__, e)
            return None
        if not isinstance(raw, list):
            logger.warning("[quote-pe-provider-leg] ratios-ttm for %s answered a %s, not a list "
                           "— the quote omits pe; not cached", ticker, type(raw).__name__)
            return None
        fields = _provider_pe_fields(raw)
        _cache_set(key, fields)
        return fields

    # ── Fundamentals: 24h Supabase + 1h in-memory ─────────────────

    async def _get_fundamentals(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch slow-moving data with two-tier cache:
          Tier 1: in-memory (1h TTL)
          Tier 2: Supabase stock_fundamentals_cache (24h TTL)
          Miss:   parallel FMP calls → cache in both tiers
        Both TTLs are ceilings: the bundle carries the daily price history, so either tier
        is also a MISS once the close cycle turns (`_bundle_is_current`).

        Concurrent callers for the same ticker share ONE build past Tier 1
        (`_fundamentals_inflight`); a caller that goes away never cancels it.
        """
        mem_key = f"fundamentals:{ticker}"

        # Tier 1: in-memory
        cached = _fundamentals_mem_get(mem_key)
        if cached is not None:
            logger.debug(f"Fundamentals in-memory HIT for {ticker}")
            return cached

        # In-flight dedup: Tier 2 and the FMP fan-out run once per ticker at a time.
        running = _fundamentals_inflight.get(ticker)
        if running is not None and _awaitable_here(running):
            logger.info("Fundamentals in-flight JOIN for %s", ticker)
            return await asyncio.shield(running)
        task = asyncio.ensure_future(self._load_fundamentals(ticker, mem_key))
        _fundamentals_inflight[ticker] = task
        task.add_done_callback(lambda t, ticker=ticker: _fundamentals_build_done(ticker, t))
        return await asyncio.shield(task)

    async def _load_fundamentals(self, ticker: str, mem_key: str) -> Dict[str, Any]:
        """Tier 2, then the FMP fan-out — the body behind `_get_fundamentals`'s in-flight slot."""
        # Tier 2: Supabase
        # `to_thread`: the Supabase SDK is SYNCHRONOUS (app/database.py), Railway runs a
        # single uvicorn worker, and this call sits on the cold /overview path — so run
        # on the loop it stalls every OTHER in-flight request, including the
        # /overview/core "fast paint" the detail screen fires alongside it.
        db_data = await asyncio.to_thread(self._check_fundamentals_db, ticker)
        if db_data is not None and _bundle_needs_provider_pe(db_data) and "ratios_ttm" not in db_data:
            # Written before the bundle carried FMP's TTM multiple (2026-10-09): rebuilt once,
            # or a foreign filer's P/E would read "—" until the row aged out.
            logger.info("Fundamentals tier-2 row for %s predates ratios_ttm — rebuilding", ticker)
            db_data = None
        if db_data is not None:
            # Stripped on READ too: a row written before the live price fields were kept
            # out of the tier still carries them until its 24 h ceiling / close cycle.
            db_data = _without_live_price(db_data)
            _cache_set(mem_key, db_data)
            return db_data

        # Miss: fetch from FMP + short-interest integration
        logger.info(f"Fundamentals MISS for {ticker} — fetching from APIs")
        data = await self._fetch_fundamentals(ticker)
        # Popped FIRST, so the internal set never reaches either cache tier or the caller.
        answered_raw = data.pop(_ANSWERED_LISTS_KEY, None) if isinstance(data, dict) else None
        answered_lists = (
            frozenset(answered_raw)
            if isinstance(answered_raw, (set, frozenset, list, tuple))
            else frozenset()
        )

        # Only cache a *usable* result. A transient FMP failure degrades the
        # profile call to {} (see _fetch_fundamentals' _safe/_list defaults); if
        # we wrote that to the 24h Supabase tier it would pin a blank profile
        # (company_name=ticker, no sector/industry, all key stats "—") for the
        # full TTL even after FMP recovers seconds later. Gate on a non-empty
        # profile so a blip can't poison the cache.
        # The gate checked ONLY the profile, while the bundle has ~15 independent slices.
        # The heaviest of them — `get_historical_prices(ticker, "1900-01-01", today)` —
        # is exactly the one FMP's burst limiter 429s first, and its failure degrades to
        # `[]` just as quietly. Writing that combination pinned a bundle with a good
        # profile and NO price history for 24 hours, which is what silently empties the
        # performance periods and the benchmark comparison on the Overview tab. Require
        # the slices the screen actually needs, not just the one that names the company.
        profile_ok = isinstance(data.get("profile"), dict) and bool(data["profile"])
        # Fund waiver (2026-10-01): a fund (VB, SPY …) has no key metrics — FMP answers a
        # genuine 200 [] — so the gate below refused its bundle on every build and the
        # overview re-ran all ~15 FMP calls (a full daily history among them) on each view.
        # Waived ONLY when the profile in THIS bundle positively says fund AND the
        # key-metrics leg answered a raw list (`_ANSWERED_LISTS_KEY`): a leg that raised or
        # answered an error dict is still a failure. Profile and history stay required.
        key_metrics_ok = bool(data.get("key_metrics"))
        fund_waived = (
            not key_metrics_ok
            and "key_metrics" in answered_lists
            and profile_is_fund(data.get("profile"))
        )
        essential = {
            "profile": profile_ok,
            "stock_historical": bool(data.get("stock_historical")),
            "key_metrics": key_metrics_ok or fund_waived,
            # A foreign filer's P/E IS this leg (`_fetch_fundamentals`): a failed one would pin
            # "—" for the whole TTL. An answered empty list is FMP's answer and is cached.
            "ratios_ttm": "ratios_ttm" in answered_lists or not _bundle_needs_provider_pe(data),
        }
        missing = [k for k, ok in essential.items() if not ok]
        if fund_waived and not missing:
            logger.info("[fund-shape] ticker=%s step=%s", ticker, "overview_fundamentals")
        if missing:
            logger.warning(
                "Fundamentals NOT cached for %s — degraded slices: %s. Serving this "
                "response but not persisting it, so a transient FMP failure cannot pin "
                "an empty bundle for the full 24h TTL.",
                ticker, ", ".join(missing),
            )
        else:
            # Both tiers get the copy WITHOUT the profile's live price fields; this request
            # still returns `data` whole (its profile price is seconds old). A later hit
            # whose quote fails then raises "no usable price" rather than serving this
            # price as live (`_LIVE_PROFILE_PRICE_FIELDS`).
            cacheable = _without_live_price(data)
            _cache_set(mem_key, cacheable)
            # The heaviest of the three: `response_json` carries `stock_historical` AND
            # `spy_historical`, up to 5,000 rows each, so this is a multi-MB serialize +
            # HTTP POST. Off the loop.
            await asyncio.to_thread(self._upsert_fundamentals_db, ticker, cacheable)

        return data

    async def _fetch_fundamentals(self, ticker: str) -> Dict[str, Any]:
        """Parallel FMP calls for all fundamental/slow-moving data.

        Both daily histories are cut to SETTLED sessions (FMP's in-progress bar dropped)
        before they are returned or persisted, and the bundle is stamped with the cutoff
        it was cut at — see the "Settled-close alignment" block at the top of the module.
        """
        from app.services.chart_helper import settled_bars

        today = datetime.now(tz=timezone.utc).date()
        from_date_full = "1900-01-01"  # Fetch full history — FMP returns from actual IPO
        to_date = today.isoformat()
        # ONE cutoff for the whole bundle: the stock filter, the SPY filter, the SPY cache
        # key and the freshness stamp all agree even if this fetch straddles 18:00 ET.
        settled_through = _settled_cutoff_date()

        # SPY historical (separate 1h cache). Keyed on the SETTLED date, not the UTC date:
        # the UTC key rolled over at 20:00 ET and not at the close, so a stock bundle and
        # the SPY series it is compared with could end on different sessions.
        sp_cache_key = f"{_SP_HIST_KEY_PREFIX}{settled_through}"
        cached_spy = _cache_get(sp_cache_key, _SP_HIST_CACHE_TTL)

        tasks = [
            self.fmp.get_company_profile(ticker),                                # 0
            self.fmp.get_key_metrics(ticker, period="annual", limit=5),          # 1
            self.fmp.get_financial_ratios(ticker, period="annual", limit=5),     # 2
            self.fmp.get_income_statement(ticker, period="annual", limit=3),     # 3
            self.fmp.get_balance_sheet(ticker, period="annual", limit=2),        # 4
            self.fmp.get_cash_flow_statement(ticker, period="annual", limit=2),  # 5
            self.fmp.get_analyst_estimates(ticker, period="annual", limit=5),    # 6
            self.fmp.get_shares_float(ticker),                                   # 7
            self.fmp.get_institutional_ownership_summary(ticker),                # 8
            self.fmp.get_income_statement(ticker, period="quarter", limit=4),    # 9
            get_short_interest(ticker),                                          # 10
            get_market_movers_service().get_sector_performance(),                                   # 11
            self.fmp.get_historical_prices(ticker, from_date_full, to_date),     # 12
            get_market_movers_service().get_industry_performance(),                                 # 13
        ]

        spy_task_idx = None
        if cached_spy is None:
            spy_task_idx = len(tasks)
            tasks.append(self.fmp.get_historical_prices("SPY", from_date_full, to_date))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        def _safe(i, default=None):
            if default is None:
                default = {}
            return results[i] if not isinstance(results[i], Exception) else default

        # Log failures
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"Fundamentals FMP call {i} failed for {ticker}: {r}")

        # Parse SPY historical
        if cached_spy is not None:
            spy_hist = cached_spy
        else:
            spy_raw = _safe(spy_task_idx) if spy_task_idx is not None else {}
            spy_hist = settled_bars(_parse_historical(spy_raw), settled_through)
            if spy_hist:
                # A previous cycle's series (up to 5,000 rows) is never read again once
                # the key moves on; drop it now rather than leave it resident until the
                # entry cap happens to evict it.
                for stale_key in [
                    k for k in _cache
                    if k.startswith(_SP_HIST_KEY_PREFIX) and k != sp_cache_key
                ]:
                    _cache.pop(stale_key, None)
                _cache_set(sp_cache_key, spy_hist)

        # Parse lists safely
        def _list(i): return _safe(i, []) if isinstance(_safe(i, []), list) else []

        stock_hist = settled_bars(_parse_historical(_safe(12)), settled_through)

        # Which list slices' RAW answers were lists (not an exception, not an error dict) —
        # `_list` hides the difference. Internal: `_get_fundamentals` pops it.
        list_slices = {
            "key_metrics": 1, "fin_ratios": 2, "income_annual": 3, "balance_annual": 4,
            "cashflow_annual": 5, "analyst_est": 6, "income_quarterly": 9,
            "sector_perf": 11, "industry_perf": 13,
        }
        answered_lists = frozenset(
            name for name, i in list_slices.items() if isinstance(results[i], list)
        )

        # FMP's TTM P/E, fetched ONLY for a filer whose statements are in another currency
        # than its price (TSM: TWD vs USD) — the Key Stats P/E for exactly those
        # (`_build_key_statistics`; owner decision 2026-10-09). After the gather because the
        # two currencies come from it: one more round trip on those cold builds only. Kept
        # trimmed (`_PROVIDER_PE_FIELDS`, no absolute price). A failed leg is NOT an answer:
        # `_load_fundamentals` refuses to cache that bundle, so P/E is retried next view.
        ratios_ttm: Dict[str, float] = {}
        if provider_pe_needed(_safe(0), _list(9), _list(3)):
            try:
                raw_ratios = await self.fmp.get_ratios_ttm(ticker)
            except Exception as e:
                logger.warning("[pe-provider-leg] ratios-ttm failed for %s (%s: %s) — P/E reads "
                               "'—' and this bundle is not cached", ticker, type(e).__name__, e)
                raw_ratios = None
            if isinstance(raw_ratios, list):
                answered_lists = answered_lists | {"ratios_ttm"}
                ratios_ttm = _provider_pe_fields(raw_ratios)
            elif raw_ratios is not None:
                logger.warning("[pe-provider-leg] ratios-ttm for %s answered a %s, not a list — "
                               "P/E reads '—' and this bundle is not cached",
                               ticker, type(raw_ratios).__name__)

        return {
            "profile": _safe(0),
            "key_metrics": _list(1),
            "fin_ratios": _list(2),
            "income_annual": _list(3),
            "balance_annual": _list(4),
            "cashflow_annual": _list(5),
            "analyst_est": _list(6),
            "shares_float": _safe(7),
            "inst_ownership": _safe(8, []),
            "income_quarterly": _list(9),
            "short_interest": _safe(10),
            "sector_perf": _list(11),
            "stock_historical": stock_hist,
            "spy_historical": spy_hist,
            "industry_perf": _list(13),
            "ratios_ttm": ratios_ttm,
            _SETTLED_THROUGH_KEY: settled_through,
            _ANSWERED_LISTS_KEY: answered_lists,
        }

    def _check_fundamentals_db(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Check Supabase stock_fundamentals_cache (24h ceiling, cut at the close cycle)."""
        try:
            row = (
                self.supabase.table("stock_fundamentals_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=_FUNDAMENTALS_DB_TTL_HOURS):
                logger.info(f"Fundamentals Supabase STALE (age={age}) for {ticker}")
                return None

            data = entry.get("response_json")
            if data and isinstance(data, dict):
                if not _bundle_is_current(data, cached_at):
                    logger.info(
                        "Fundamentals Supabase STALE for %s — written for an earlier close "
                        "cycle (cached_at=%s, settled_through=%s, current=%s)",
                        ticker, cached_at_str, data.get(_SETTLED_THROUGH_KEY),
                        _settled_cutoff_date(),
                    )
                    return None
                logger.info(f"Fundamentals Supabase HIT for {ticker} (age={age})")
                return data
            return None
        except Exception as e:
            logger.warning(
                f"Fundamentals Supabase check failed for {ticker}: {type(e).__name__}: {e}"
            )
            return None

    def _upsert_fundamentals_db(self, ticker: str, data: Dict[str, Any]) -> None:
        """Upsert fundamentals into Supabase cache."""
        try:
            self.supabase.table("stock_fundamentals_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": data,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker",
            ).execute()
            logger.info(f"Fundamentals cached in Supabase for {ticker}")
        except Exception as e:
            logger.warning(f"Fundamentals Supabase upsert failed for {ticker}: {e}")

    # ── Company Profile Cache (for chat AI context) ────────────────

    def _check_company_profile_db(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Check Supabase company_profile_cache (24h TTL)."""
        try:
            row = (
                self.supabase.table("company_profile_cache")
                .select("profile_json, cached_at")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None
            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None
            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=_FUNDAMENTALS_DB_TTL_HOURS):
                return None
            return entry.get("profile_json")
        except Exception as e:
            logger.warning(f"Company profile cache check failed for {ticker}: {e}")
            return None

    def _upsert_company_profile_db(self, ticker: str, data: Dict[str, Any]) -> None:
        """READ-MERGE-WRITE of the formatted company profile into the SHARED
        `company_profile_cache` row. SYNC (the caller runs it in a thread); best effort,
        never raises.

        The row has other writers: `company_facts_service` adds its ``facts`` and
        ``key_executives`` blocks, `whale_service` stores the raw profile (logo, name, fund
        flags). This write used to REPLACE the row whole, so every detail view dropped those
        blocks and the next chat question about the ticker re-fetched a profile and its
        executives upstream (2026-10-08 review). Now the row is re-read and the Overview's
        keys are laid over it by `company_facts_service.merge_profile_row` — the same rules
        that writer uses: every other key kept, a raw profile's price fields and duplicated
        identity fields (head count, IPO date, city / state, exchange, currency, ADR flag)
        dropped — a fresh `cached_at` would re-date them, and kept they made the row read as
        whale's raw profile, whose old head count beat this write's fresh one — and the daily
        keys replaced by this write's own. Whale's name and logo are refreshed by this write
        (`profile_display_fields`), not carried stale.

        A failed READ skips the write (logged): a blind write would drop exactly the blocks
        this merge exists to keep, and the next detail view tries again. Read-merge-write is
        not atomic — a write landing between this read and this write is lost, and its writer
        re-fetches on its next read."""
        from app.services.company_facts_service import merge_profile_row

        table = "company_profile_cache"
        try:
            res = (
                self.supabase.table(table)
                .select("profile_json, cached_at")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            rows = getattr(res, "data", None) or []
        except Exception as e:
            logger.warning(
                "Company profile NOT cached for %s: the row read failed (%s: %s) — a blind "
                "write would drop the blocks the other writers keep", ticker,
                type(e).__name__, e,
            )
            return
        base = rows[0] if rows and isinstance(rows[0], dict) else None
        now = datetime.now(timezone.utc)
        try:
            merged = merge_profile_row(base, data, now=now)
            self.supabase.table(table).upsert(
                {"ticker": ticker, "profile_json": merged, "cached_at": now.isoformat()},
                on_conflict="ticker",
            ).execute()
            logger.info("Company profile cached in Supabase for %s (merged over %s)", ticker,
                        "the existing row" if base else "no row")
        except Exception as e:
            logger.warning("Company profile upsert failed for %s: %s: %s", ticker,
                           type(e).__name__, e)

    def get_cached_company_profile(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Public accessor for other services (e.g. chat) to read cached profile."""
        return self._check_company_profile_db(ticker.upper())

    # ── Key facts for Ask Cay AI (2026-10-08) ─────────────────────

    async def get_key_facts(self, ticker: str) -> Dict[str, Any]:
        """The Overview's Key Stats for `ticker`, for Ask Cay AI's financials tool — built by
        the SAME `_build_key_statistics` from the SAME fundamentals bundle and live quote, so a
        row reads byte-for-byte what the screen shows. Never re-derives a figure.

        Returns::

            {"ticker", "company_name", "rows": {label: value}, "unavailable": [label, ...],
             "live_price_ok", "statement_currency", "price_currency", "pe_basis", "is_fund",
             "country"?, "is_adr"?, "balance_sheet": {...} | None,
             "short_interest_settlement_date", "degraded": [slice, ...]}

        * ``degraded`` names the fundamentals slices that came back empty for a company that
          always has them ("profile", "key_metrics", "quarterly_income", "balance_sheet") — the
          caller says "did not load", never "none reported". Empty for a fund.

        * ``rows`` holds only real values. A placeholder the screen prints for "unknown"
          ('—', 'N/A', empty) goes to ``unavailable`` — never a value a model could quote.
          The live trading day (`_KEY_FACTS_INTRADAY`) is left out: the screen merges the
          session bars into those rows, and the price tool owns the live day.
        * A failed or priceless quote serves the fundamentals-only rows with
          ``live_price_ok=False``: the price-dependent rows (P/E (TTM), P/E (FWD), and a
          payer's Dividends, which `_build_key_statistics` would otherwise print as "None"
          with no price) go to ``unavailable``. It never raises for the quote — unlike the
          full overview, which refuses a priceless build.
        * ``statement_currency`` is the newest annual income statement's ``reportedCurrency``
          (else the balance sheet's), ``price_currency`` the profile's trading currency (else
          the live quote's); None when absent or not a 3-letter code — never assumed USD.
        * ``pe_basis`` is how the P/E rows were made (`pe_basis`): ``"live"`` (live price ÷ TTM
          EPS, one currency), ``"provider_ttm"`` (the statements are in another currency than
          the price: FMP's TTM multiple at its daily close, and EPS (TTM) carries its code) or
          ``"currency_unconfirmed"`` (no P/E: the price's currency is unknown).
        * ``balance_sheet`` is the newest annual balance sheet's totals (total debt, cash and
          short-term investments, net debt) with its period end; a missing or non-finite
          figure is omitted, never 0.

        Raises only what the fundamentals read raises (it folds upstream failures itself);
        the caller degrades that one block.
        """
        sym = (ticker or "").strip().upper()
        fundamentals, quote = await asyncio.gather(
            self._get_fundamentals(sym),
            price_source(self).get_quote(sym),
            return_exceptions=True,
        )
        if isinstance(fundamentals, BaseException):
            logger.warning("key facts: fundamentals failed for %s: %s: %s",
                           sym, type(fundamentals).__name__, fundamentals)
            raise fundamentals
        fund = fundamentals if isinstance(fundamentals, dict) else {}
        if isinstance(quote, BaseException):
            logger.warning("key facts: live quote failed for %s (%s: %s) — fundamentals-only rows",
                           sym, type(quote).__name__, quote)
            quote = {}
        quote = quote if isinstance(quote, dict) else {}
        live_price_ok = _safe_float(quote, "price") > 0
        if not live_price_ok:
            # A cached bundle's profile carries no price (`_without_live_price`), so a priceless
            # quote must not borrow one: every price-dependent row is withheld below instead.
            quote = {}

        profile = fund.get("profile") if isinstance(fund.get("profile"), dict) else {}
        short_interest = fund.get("short_interest")
        if not short_interest:
            # The overview fills the same gap the same way (`get_overview`); bounded here, and
            # a read still running keeps going behind its own cache.
            short_interest = await self._key_facts_short_interest(sym)
        short_interest = short_interest if isinstance(short_interest, dict) else {}

        def _rows_of(name: str) -> List[Dict]:
            rows = fund.get(name)
            return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

        price = (_safe_float(quote, "price") or _safe_float(profile, "price")) if live_price_ok else 0.0
        _flat, groups = self._build_key_statistics(
            quote, profile, _rows_of("key_metrics"), _rows_of("analyst_est"), price,
            shares_float_data=fund.get("shares_float") if isinstance(fund.get("shares_float"), dict) else {},
            inst_ownership_data=fund.get("inst_ownership"),
            income_quarterly=_rows_of("income_quarterly"),
            short_interest=short_interest,
            income_annual=_rows_of("income_annual"),
            ratios_ttm=fund.get("ratios_ttm") if isinstance(fund.get("ratios_ttm"), dict) else None,
        )

        pays_dividend =(_safe_float(profile, "lastDiv") or _safe_float(profile, "lastDividend")) > 0
        withheld = set() if live_price_ok else set(_KEY_FACTS_NEEDS_PRICE)
        if not live_price_ok and not pays_dividend:
            withheld.discard("Dividends")      # "None" (pays none) needs no price
        rows: Dict[str, str] = {}
        unavailable: List[str] = []
        for group in groups:
            for item in group.statistics:
                label = item.label
                if label in _KEY_FACTS_INTRADAY or label in rows or label in unavailable:
                    continue
                value = item.value if isinstance(item.value, str) else ""
                if label in withheld or value.strip() in _KEY_FACTS_PLACEHOLDERS:
                    unavailable.append(label)
                else:
                    rows[label] = value

        income_annual = _rows_of("income_annual")
        balance_annual = _rows_of("balance_annual")
        # Which slices came back EMPTY. `_get_fundamentals` folds an FMP failure into an empty
        # slice (and serves that bundle uncached), and a cached bundle can still hold an empty
        # non-essential slice from a leg that failed when it was built — so an empty slice a
        # listed company always has is "did not load", never "the company has none". A fund
        # has no statements by nature and is never marked.
        degraded: List[str] = []
        if not profile:
            degraded.append("profile")
        if not profile_is_fund(profile):
            if not _rows_of("key_metrics"):
                degraded.append("key_metrics")
            if not _rows_of("income_quarterly"):
                degraded.append("quarterly_income")
            if not balance_annual:
                degraded.append("balance_sheet")
        if degraded:
            logger.info("key facts for %s served with empty slices: %s", sym, ", ".join(degraded))
        statement_currency = (
            _currency_code(_newest_row(income_annual).get("reportedCurrency"))
            or _currency_code(_newest_row(balance_annual).get("reportedCurrency"))
        )
        # The same two currencies the Key Stats builder read (the quote's only when the profile
        # has none), so `pe_basis` names the P/E the rows above were built with.
        trading_currency, eps_currency = key_stats_currencies(
            profile, quote, _rows_of("income_quarterly"), income_annual)
        settlement = short_interest.get("settlement_date")
        name = profile.get("companyName")
        out: Dict[str, Any] = {
            "ticker": sym,
            "company_name": name.strip()[:120] if isinstance(name, str) and name.strip() else None,
            "rows": rows,
            "unavailable": unavailable,
            "live_price_ok": live_price_ok,
            "statement_currency": statement_currency,
            "price_currency": trading_currency,
            "pe_basis": pe_basis(trading_currency, eps_currency),
            "is_fund": profile_is_fund(profile),
            "balance_sheet": _balance_sheet_totals(balance_annual),
            "short_interest_settlement_date": (
                settlement[:10] if isinstance(settlement, str) and settlement.strip() else None),
            "degraded": degraded,
        }
        out.update(profile_country_fields(profile))
        return out

    async def _key_facts_short_interest(self, sym: str) -> Dict[str, Any]:
        """The exchange-reported short interest through its own cache, waited on for at most
        `_KEY_FACTS_SHORT_WAIT` seconds. A read still running keeps going (held in
        `_key_facts_side_tasks`) and warms the cache; its failure is logged. Never raises."""
        task = asyncio.ensure_future(get_short_interest(sym))
        _key_facts_side_tasks.add(task)

        def _done(t: "asyncio.Task") -> None:
            _key_facts_side_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.warning("key facts: short interest read failed for %s: %s: %s",
                               sym, type(t.exception()).__name__, t.exception())

        task.add_done_callback(_done)
        await asyncio.wait({task}, timeout=_KEY_FACTS_SHORT_WAIT)
        if not task.done():
            logger.info("key facts: short interest for %s still loading after %.1fs — "
                        "its rows read as unavailable", sym, _KEY_FACTS_SHORT_WAIT)
            return {}
        if task.cancelled() or task.exception() is not None:
            return {}
        result = task.result()
        return result if isinstance(result, dict) else {}

    # ── Volatile: live intraday data (120s response-level cache) ──

    async def get_overview_core(
        self,
        ticker: str,
        chart_range: str = "3M",
        interval: Optional[str] = None,
        extended_hours: bool = False,
    ) -> StockOverviewCoreResponse:
        """Fast subset for the instant first paint — price + chart + company name.

        Reuses the FAST paths only: ``_get_volatile`` (live quote + intraday chart)
        and ``get_company_profile`` (company name). Deliberately does NOT touch
        ``_get_fundamentals`` / ``get_historical_prices`` / snapshot services / the
        IPO fetch — those are the 2–5s bottleneck the full ``/overview`` bundles.

        Chart: only INTRADAY ranges (1D/1W) carry a chart in core (``fast_only=True``);
        daily/5Y/ALL yield ``chart_data=[]`` and the parallel full ``/overview`` call
        supplies them — so core NEVER pays the slow historical fetch for ANY range
        (including 5Y/ALL). The default open is 1D (intraday) → core carries a real
        chart. Never raises on a partial upstream failure — degrades field-by-field."""
        ticker = ticker.upper()
        core_key = (
            f"stock_overview_core:{ticker}:{chart_range}:{interval or 'default'}:{extended_hours}"
        )
        cached = _cache_get(core_key, ttl=_VOLATILE_TTL)
        if cached is not None:
            return cached

        vol, profile = await asyncio.gather(
            self._get_volatile(ticker, chart_range, interval, extended_hours, fast_only=True),
            self.fmp.get_company_profile(ticker),
            return_exceptions=True,
        )
        if isinstance(vol, Exception):
            logger.warning("Overview core volatile fetch failed for %s: %s", ticker, vol)
            vol = {}
        if isinstance(profile, Exception):
            logger.warning("Overview core profile fetch failed for %s: %s", ticker, profile)
            profile = {}
        profile = profile or {}
        quote = (vol.get("quote") or {}) if isinstance(vol, dict) else {}

        # Same price extraction as the full builder (_build_full_response).
        price = _safe_float(quote, "price") or _safe_float(profile, "price")
        # Prefer the (fresher) quote, falling back to the profile only when the
        # quote key is ABSENT — not when it is a legitimate 0.0 (a genuinely flat
        # day). Python `or` treats 0.0 as falsy, so the old `or`-chain discarded a
        # real 0.0 change and surfaced the staler profile's nonzero %, contradicting
        # the (correctly 0.0) price_change. (Fallback key was also wrong: stable
        # profile exposes "change", not "changes".)
        # An inactive listing's profile change is a PAST session's (`profile_change_is_current`).
        profile_change = profile if profile_change_is_current(profile) else {}
        raw_change = _first_present_or_none((quote, "change"), (profile_change, "change"))
        raw_pct = _first_present_or_none(
            (quote, "changePercentage"), (quote, "changesPercentage"),
            (profile_change, "changePercentage"), (profile_change, "changesPercentage"),
        )
        # `is not None`, never truthiness: an explicit 0.0 is a KNOWN flat day. Only
        # a change absent from EVERY source is unknown — `/stable/profile` answers
        # `change: null` for a halted/OTC listing, and the quote leg can fail while
        # the profile lands — and that used to ship as `price_change: 0.0` with no
        # flag, which the equity header rendered as "▲ +0.00 (+0.00%)" in green with
        # a bullish flash (the other four asset classes carry `change_known`).
        change_known = raw_change is not None or raw_pct is not None
        change = raw_change if raw_change is not None else 0.0
        change_pct = raw_pct if raw_pct is not None else 0.0
        # A missing price is an upstream FAILURE, not a price of zero.
        #
        # Both fetches above degrade to `{}` on any exception, and `_safe_float` then
        # returns its 0.0 default — so an FMP outage produced a structurally valid
        # `current_price: 0.0`, HTTP 200, cached for 120s. This endpoint paints the FIRST
        # thing the user sees on a stock, so the screen opened showing "$0.00" as the
        # company's price. Raise instead: the endpoint's `upstream_error_response` then
        # returns a typed, retryable error and the iOS shimmer stays up rather than being
        # replaced by a fabricated number.
        if not price or price <= 0:
            raise FMPUnavailableException(
                f"No usable price for {ticker} in the fast-core fetch "
                f"(quote={'ok' if quote else 'empty'}, profile={'ok' if profile else 'empty'})"
            )

        company_name = profile.get("companyName") or quote.get("name") or ticker
        # NEVER slice from stock_historical here — that requires the slow bundle.
        chart_data = (vol.get("chart_data") if isinstance(vol, dict) else None) or []

        response = StockOverviewCoreResponse(
            symbol=ticker,
            company_name=company_name,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
            change_known=change_known,
            market_status=_get_market_status(),
            chart_data=chart_data,
        )
        _cache_set(core_key, response)
        return response

    async def _get_volatile(
        self, ticker: str, chart_range: str, interval: str, extended_hours: bool,
        fast_only: bool = False,
    ) -> Dict[str, Any]:
        """Fetch live quote + chart data (no persistent caching).

        ``fast_only`` (the instant-first-paint core path) fetches the chart ONLY
        for genuinely-fast INTRADAY ranges (1D/1W → 5min/1hour). For daily/weekly/
        monthly ranges (3M/6M/1Y and — critically — 5Y and ALL) it returns
        ``chart_data=None`` so the core NEVER pays the slow historical fetch
        (5Y = a multi-year daily pull; ALL = up to 5 sequential paginated pulls via
        ``_fetch_all_daily``). The full ``/overview`` supplies those charts."""
        quote_task = price_source(self).get_quote(ticker)

        from app.services.chart_helper import (
            fetch_chart_data,
            resolve_interval,
            INTRADAY_INTERVALS,
        )
        resolved = resolve_interval(chart_range, interval)

        results = await asyncio.gather(quote_task, return_exceptions=True)
        quote = results[0] if not isinstance(results[0], Exception) else {}

        # Chart data
        if fast_only:
            # Only the fast intraday chart. 5Y (weekly) / ALL (monthly) / daily all
            # require the slow historical bundle → skip here (full /overview fills them).
            want_chart = resolved in INTRADAY_INTERVALS
        else:
            want_chart = resolved != "daily" or chart_range == "ALL"

        if want_chart:
            chart_data = await fetch_chart_data(
                self.fmp, ticker, chart_range, interval, extended_hours=extended_hours,
            )
        else:
            chart_data = None  # Will be sliced from fundamental stock_historical (full path)

        return {"quote": quote, "chart_data": chart_data}

    # ── Session Open / Day High / Day Low ─────────────────────────

    async def _get_session_ohl(
        self,
        ticker: str,
        volatile: Optional["asyncio.Future"] = None,
        chart_data: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, float]:
        """`{open, dayHigh, dayLow}` for the session the header price describes, else ``{}``.

        Never raises — the overview re-raises a failed volatile gather, and three
        stat rows are not worth the screen. Sources, in order:
          1. the intraday bars the overview already fetched (``volatile`` / ``chart_data``)
             when the session is live — no extra call;
          2. the session's EOD row (``historical-price-eod/full``, ≤7-day window, date-gated);
          3. one single-day 5-minute fetch while the session is live and FMP has not yet
             written the EOD row.
        A legitimate ``{}`` (pre-open, no bars yet) is cached like a hit; a failure is not.
        """
        sym = (ticker or "").upper()
        if not sym or is_blocked_symbol(sym) or uses_coingecko_price(sym):
            return {}
        key = f"stock_ohl:{sym}"
        hit = _cache_get(key, ttl=_VOLATILE_TTL)
        if hit is not None:
            return hit

        try:
            bars = chart_data
            if bars is None and volatile is not None:
                try:
                    vol = await volatile
                    bars = vol.get("chart_data") if isinstance(vol, dict) else None
                except Exception:
                    bars = None   # the overview reports that failure itself

            result = await session_ohl_for(self.fmp, sym, bars=bars)
            _cache_set(key, result)
            return result
        except Exception as e:
            logger.warning("[ohl-unavailable] %s: %s: %s — Open / Day High / Day Low read "
                           "as unknown for this request", sym, type(e).__name__, e)
            return {}

    # ── Build full response from both data sources ────────────────

    def _build_full_response(
        self, ticker: str, fund: Dict, vol: Dict,
        chart_range: str, interval: str, extended_hours: bool,
        profitability_snapshot=None, growth_snapshot=None, valuation_snapshot=None,
        health_snapshot=None, ownership_snapshot=None,
        ipo_price_data=None,
    ) -> StockOverviewResponse:
        """Combine fundamentals + volatile into one response.

        Synchronous, so it must do no I/O. (The degraded Price card used to need sector
        medians prefetched for it; it shows no peer comparison since 2026-10-07.)"""
        profile = fund.get("profile", {})
        quote = vol.get("quote", {})
        key_metrics = fund.get("key_metrics", [])
        fin_ratios = fund.get("fin_ratios", [])
        income_annual = fund.get("income_annual", [])
        balance_annual = fund.get("balance_annual", [])
        cashflow_annual = fund.get("cashflow_annual", [])
        analyst_est = fund.get("analyst_est", [])
        shares_float = fund.get("shares_float", {})
        inst_ownership = fund.get("inst_ownership", [])
        income_quarterly = fund.get("income_quarterly", [])
        short_interest = fund.get("short_interest", {})
        sector_perf = fund.get("sector_perf", [])
        industry_perf = fund.get("industry_perf", [])
        stock_historical = fund.get("stock_historical", [])
        spy_historical = fund.get("spy_historical", [])

        # Price from volatile quote, fallback to profile
        price = _safe_float(quote, "price") or _safe_float(profile, "price")
        # Prefer the (fresher) quote, falling back to the profile only when the
        # quote key is ABSENT — not when it is a legitimate 0.0 (a genuinely flat
        # day). Python `or` treats 0.0 as falsy, so the old `or`-chain discarded a
        # real 0.0 change and surfaced the staler profile's nonzero %, contradicting
        # the (correctly 0.0) price_change. (Fallback key was also wrong: stable
        # profile exposes "change", not "changes".)
        # An inactive listing's profile change is a PAST session's (`profile_change_is_current`).
        profile_change = profile if profile_change_is_current(profile) else {}
        raw_change = _first_present_or_none((quote, "change"), (profile_change, "change"))
        raw_pct = _first_present_or_none(
            (quote, "changePercentage"), (quote, "changesPercentage"),
            (profile_change, "changePercentage"), (profile_change, "changesPercentage"),
        )
        # `is not None`, never truthiness: an explicit 0.0 is a KNOWN flat day. Only
        # a change absent from EVERY source is unknown — `/stable/profile` answers
        # `change: null` for a halted/OTC listing, and the quote leg can fail while
        # the profile lands — and that used to ship as `price_change: 0.0` with no
        # flag, which the equity header rendered as "▲ +0.00 (+0.00%)" in green with
        # a bullish flash (the other four asset classes carry `change_known`).
        change_known = raw_change is not None or raw_pct is not None
        change = raw_change if raw_change is not None else 0.0
        change_pct = raw_pct if raw_pct is not None else 0.0
        # The same refusal the fast-core path makes above (`get_overview_core`): a missing
        # price is an upstream FAILURE, never a price of zero. `_fetch_fundamentals` folds
        # every slice to `{}`/`[]` and `_get_volatile` folds a failed quote to `{}`, so a
        # symbol FMP cannot serve at all — a bare coin ticker such as DOGE on the equity
        # screen, or any unknown symbol — used to come out of here as a structurally valid
        # HTTP 200 with `current_price: 0.0`, and iOS painted a "$0.00" page under the
        # company's own ticker (TestFlight 1.0 (7)). Raising here reaches the endpoint's
        # `upstream_error_response` (typed, retryable FMP_UNAVAILABLE), the detail screen
        # then falls back to `/stocks/{t}` + `/quote`, both 404 for such a symbol, and shows
        # its failure card with Retry. Same exception class as the core guard on purpose:
        # at this layer "both empty" is indistinguishable from an FMP outage, so a 404
        # "not found" would tell a user AAPL does not exist during a blip. Nothing is
        # cached — the raise precedes `_cache_set` in `get_overview`.
        if not price or price <= 0:
            raise FMPUnavailableException(
                f"No usable price for {ticker} in the full overview "
                f"(quote={'ok' if quote else 'empty'}, profile={'ok' if profile else 'empty'})"
            )
        company_name = profile.get("companyName") or quote.get("name") or ticker

        # Chart data: use volatile if available, else slice from historical
        chart_data = vol.get("chart_data")
        if chart_data is None:
            chart_data = _extract_chart_data(stock_historical, chart_range)

        # Key statistics
        key_statistics, key_statistics_groups = self._build_key_statistics(
            quote, profile, key_metrics, analyst_est, price,
            shares_float_data=shares_float,
            inst_ownership_data=inst_ownership,
            income_quarterly=income_quarterly,
            short_interest=short_interest,
            income_annual=income_annual,
            ratios_ttm=fund.get("ratios_ttm"),
        )

        # Performance periods
        performance_periods = self._build_performance_periods(
            stock_historical, spy_historical,
        )

        # Snapshots
        sector_name = profile.get("sector") or "N/A"
        snapshots = self._build_snapshots(
            key_metrics, fin_ratios, income_annual, balance_annual,
            cashflow_annual, price,
            _safe_float(profile, "mktCap") or _safe_float(quote, "marketCap"),
            sector_name,
            profile=profile,
            industry=profile.get("industry", "") if isinstance(profile, dict) else "",
            ticker=ticker,
            income_quarterly=income_quarterly,
            profitability_snapshot=profitability_snapshot,
            growth_snapshot=growth_snapshot,
            valuation_snapshot=valuation_snapshot,
            health_snapshot=health_snapshot,
            ownership_snapshot=ownership_snapshot,
        )

        # Sector & Industry
        sector_industry = self._build_sector_industry(profile, sector_perf, industry_perf)

        # Company profile (includes sector/industry data)
        company_profile = self._build_company_profile(profile, sector_industry=sector_industry)

        # The formatted profile is cached for chat AI context by the CALLER, not here.
        # This builder is synchronous, so the Supabase write it used to do ran blocking
        # on the event loop — on EVERY /overview cache miss, even when fundamentals came
        # back warm from tier 1 or 2. `get_overview` now does the same write via
        # asyncio.to_thread once this returns; both fields it needs are on the response.

        benchmark_summary = self._build_benchmark_summary(
            stock_historical, spy_historical, ipo_price_data=ipo_price_data,
            ticker=ticker,
        )

        return StockOverviewResponse(
            symbol=ticker,
            company_name=company_name,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
            change_known=change_known,
            market_status=_get_market_status(),
            chart_data=chart_data,
            key_statistics=key_statistics,
            key_statistics_groups=key_statistics_groups,
            performance_periods=performance_periods,
            snapshots=snapshots,
            sector_industry=sector_industry,
            company_profile=company_profile,
            related_tickers=[],  # Populated below
            benchmark_summary=benchmark_summary,
        )

    # ── Key Statistics ────────────────────────────────────────────

    def _build_key_statistics(
        self, quote: Dict, profile: Dict, key_metrics: List[Dict],
        analyst_est: List[Dict], price: float,
        shares_float_data: Dict = None, inst_ownership_data=None,
        income_quarterly: List[Dict] = None,
        short_interest: Dict = None,
        income_annual: List[Dict] = None,
        ratios_ttm: Dict = None,
    ) -> Tuple[List[KeyStatisticItem], List[KeyStatisticsGroupResponse]]:
        """The Overview's Key Stats rows, flat and in their four groups.

        ``income_annual`` (the statements' currency when the quarterly rows carry none) and
        ``ratios_ttm`` (the bundle's trimmed FMP TTM multiple, `_provider_pe_fields`) decide
        the P/E rows for a filer whose statements are in another currency than its price."""
        open_val = _safe_float(quote, "open")
        prev_close = _safe_float(quote, "previousClose")
        day_high = _safe_float(quote, "dayHigh")
        day_low = _safe_float(quote, "dayLow")
        volume = _safe_float(quote, "volume") or _safe_float(profile, "volume")
        avg_volume = (_safe_float(quote, "avgVolume") or _safe_float(profile, "volAvg")
                      or _safe_float(profile, "averageVolume"))
        market_cap = (_safe_float(quote, "marketCap") or _safe_float(profile, "mktCap")
                      or _safe_float(profile, "marketCap"))
        year_high = _safe_float(quote, "yearHigh")
        year_low = _safe_float(quote, "yearLow")
        # Fallback: parse profile.range string "124.17-199.62"
        if not year_high or not year_low:
            range_str = profile.get("range", "")
            if isinstance(range_str, str) and "-" in range_str:
                parts = range_str.split("-")
                if len(parts) == 2:
                    try:
                        low_val, high_val = float(parts[0]), float(parts[1])
                        if not year_low:
                            year_low = low_val
                        if not year_high:
                            year_high = high_val
                    except ValueError:
                        pass
        beta = _safe_float(profile, "beta") or _safe_float(quote, "beta")
        last_div = _safe_float(profile, "lastDiv") or _safe_float(profile, "lastDividend")
        shares_out = (_safe_float(quote, "sharesOutstanding")
                      or _safe_float(shares_float_data or {}, "outstandingShares"))

        # ── EPS (TTM): the statement rows covering the trailing twelve months ──
        #
        # ⚠️ A NEGATIVE EPS IS DATA, NOT A MISSING VALUE. This used to end in
        # `if ttm_eps > 0`, so a loss-making company had its EPS computed correctly from
        # four real quarterly statements and then thrown away — the card rendered the same
        # "—" it uses when the upstream is down. A TestFlight tester photographed exactly
        # that on MRNA ("Data is missing? Double check for me") whose TTM EPS is -7.98,
        # and it applied to EVERY loss-maker: measured on one basket, 8 of 10 tickers,
        # including PLUG, which that same tester holds. For a company losing money, "we
        # lose $7.98 a share" is the single most important number on the screen.
        #
        # The rows are four quarters, or two half-years for a half-year filer (`_ttm_rows`:
        # BHP's four "quarterly" rows were two YEARS of earnings), each read by presence,
        # not truthiness (`_eps_value`: a break-even 0.0 quarter used to drop the whole sum).
        # Which currency each figure is in decides the P/E below — see the "Key Stats: TTM
        # EPS and the P/E basis" block at the top of the module.
        symbol = profile.get("symbol") if isinstance(profile, dict) else None
        trading_ccy, statement_ccy = key_stats_currencies(
            profile, quote, income_quarterly, income_annual)
        basis = pe_basis(trading_ccy, statement_ccy)
        eps = _ttm_eps(income_quarterly, symbol)
        fwd_eps = _nearest_forward_eps(analyst_est, _utc_today_iso())
        pe = None
        pe_fwd = None

        # ── Why there are TWO P/E values on this screen ────────────────────
        #
        # This "P/E (TTM)" and the Snapshots "Price" card's
        # "P/E (1.63x sector avg 22)" render in the SAME scroll view
        # (TickerDetailOverviewContent.swift:19 and :30) and can disagree.
        # That is the design, not a bug.
        #
        # Both use the same TTM EPS basis; only the PRICE TIMESTAMP differs.
        # This one is the live quote (in-memory 120s, never persisted); the
        # other is FMP's own `priceToEarningsRatioTTM`, priced at FMP's
        # timestamp and held up to 24h in `snapshot_cache`. Measured drift:
        # KO identical, AAPL $1.07 of price, UBER 1.8% (worst observed).
        #
        # ⚠️ DO NOT UNIFY THEM — see the long form at
        # valuation_snapshot_service.build_price_snapshot, which carries the
        # full reasoning and the invariant the cached row must keep. Short
        # version: making that row live bakes a live price into a 24-hour
        # Supabase row, and making this one read that row stales a number that
        # is internally consistent on screen (35.90 × 8.73 = $313.40, the price
        # shown directly above it).
        #
        # ⚠️ ONE QUALIFICATION on "this is the live one": `price` in
        # `_build_full_response` is `quote.price` OR `profile.price`, and `profile`
        # is the fundamentals bundle's. `_get_volatile` degrades `quote` to {} on
        # any exception. The bundle's two cache tiers (`stock_fundamentals_cache`,
        # 24h) no longer keep the profile's price fields (`_without_live_price`),
        # so on a cache hit a failed quote raises "no usable price" instead of
        # computing this P/E from a price up to 24 hours old. Only the request
        # that fetched the bundle falls back to its seconds-old profile price.
        #
        # ── P/E (TTM): price / EPS — the price and the statements share a currency ──
        #
        # Computed for a NEGATIVE eps too, so the formatter can tell "undefined because
        # the company loses money" ("Neg.") from "we don't have it" ("—"). The old
        # `eps > 0` guard destroyed that distinction three lines before it was needed.
        # `eps != 0` guards the division, not the sign.
        if basis == PE_BASIS_LIVE:
            # Fallback: try quote fields, then key_metrics earningsYield.
            #
            # `is None`, NOT `not eps`: a genuine EPS of exactly 0.00 (a break-even company)
            # is falsy, and the old test sent it down the fallback path to be overwritten by
            # whatever the quote happened to hold.
            if eps is None:
                eps = _finite(quote.get("eps"))
            if eps is None and price and price > 0:
                km_latest = key_metrics[0] if key_metrics and isinstance(key_metrics[0], dict) else {}
                earnings_yield = _finite(km_latest.get("earningsYield"))
                # Negative yield allowed through for the same reason as the EPS above — it is
                # the loss, expressed differently. Zero is excluded: it is FMP's "absent".
                if earnings_yield:
                    eps = round(earnings_yield * price, 2)
            if eps and price and price > 0:
                pe = _finite_multiple(price / eps)
            # ── Forward P/E: the nearest fiscal year not yet ended ──
            # Negative forward EPS is a real analyst estimate, not a gap: MRNA's nearest
            # future year (2027) is -4.90. Let the formatter render "Neg.".
            if fwd_eps and price and price > 0:
                pe_fwd = _finite_multiple(price / fwd_eps)
        else:
            # ── The statements are in another currency than the price ──
            #
            # Never price ÷ EPS. No trading-currency fallback either: the quote's EPS and
            # earnings yield × price are in the price's currency, while EPS (TTM) is shown in
            # the statements' (labelled with its code below). FMP's TTM multiple instead,
            # checked against our EPS (`_provider_pe`) — the same multiple the Price card
            # shows, priced at FMP's daily timestamp, not the live quote.
            provider = _provider_pe(ratios_ttm, eps) if basis == PE_BASIS_PROVIDER else None
            if provider is not None:
                provider_pe, provider_price = provider
                pe = _finite_multiple(provider_pe)
                # Forward: FMP's price (reporting currency) ÷ the analysts' EPS for the
                # nearest year, which FMP gives in the reporting currency too (TSM 2026:
                # 538.40 TWD per ADS), so the currency cancels. One timestamp for both rows.
                if fwd_eps:
                    pe_fwd = _finite_multiple(provider_price / fwd_eps)
            else:
                if basis == PE_BASIS_UNCONFIRMED:
                    reason = "the price's currency is unknown"
                elif eps is None:
                    reason = "no TTM EPS to check FMP's multiple against"
                elif not ratios_ttm:
                    reason = "no FMP TTM multiple in the bundle"
                else:
                    reason = "FMP's TTM EPS does not match ours"
                logger.warning("[pe-withheld] %s: statements in %s, price in %s — P/E (TTM) and "
                               "P/E (FWD) read '—': %s", symbol or "?", statement_ccy,
                               trading_ccy or "unknown", reason)

        # EPS (TTM) as shown: a statement figure in another currency than the price carries
        # its code ("TWD 434.95") — on a USD screen a bare 434.95 reads as dollars.
        eps_shown = "—"
        if eps is not None:
            eps_shown = f"{eps:.2f}" if basis == PE_BASIS_LIVE else f"{statement_ccy} {eps:.2f}"

        # Ownership from shares-float and institutional ownership endpoints
        shares_float_data = shares_float_data or {}
        float_shares_val = _safe_float(shares_float_data, "floatShares")
        free_float = _safe_float(shares_float_data, "freeFloat")
        insider_pct = round(100 - free_float, 4) if free_float else None

        # Institutional ownership
        inst_pct = None
        if isinstance(inst_ownership_data, list) and inst_ownership_data:
            inst_dict = inst_ownership_data[0] if isinstance(inst_ownership_data[0], dict) else {}
            inst_pct = _safe_float(inst_dict, "ownershipPercent")
        elif isinstance(inst_ownership_data, dict):
            inst_pct = _safe_float(inst_ownership_data, "ownershipPercent")

        # Fallback: try key_metrics for ownership if endpoints returned nothing
        if insider_pct is None or inst_pct is None:
            km = key_metrics[0] if key_metrics else {}
            if insider_pct is None:
                insider_pct = _safe_float(km, "insidersPercentage")
            if inst_pct is None:
                inst_pct = _safe_float(km, "institutionPercentage") or _safe_float(km, "institutionalOwnership")

        # Dividend yield
        # ── Dividends ──
        #
        # Three answers, not two. `_safe_float` defaults a MISSING key to 0.0, so a failed
        # profile fetch and a company that genuinely pays no dividend both arrived here as
        # `0` and both rendered "—". On MRNA — which pays nothing — that read as broken
        # data sitting next to three other dashes.
        #
        # A profile we actually received IS the evidence: if it carries either dividend
        # key, a 0 means "pays none". No profile at all means we do not know.
        _has_div_field = isinstance(profile, dict) and (
            profile.get("lastDiv") is not None or profile.get("lastDividend") is not None
        )
        if last_div > 0 and price > 0:
            annual_div = last_div  # FMP lastDiv is already annualized
            div_yield = (annual_div / price) * 100
            div_str = f"{annual_div:.2f} ({div_yield:.2f}%)"
        elif _has_div_field:
            div_str = "None"
        else:
            div_str = "—"

        # Flat list
        flat_stats = [
            KeyStatisticItem(label="Open", value=f"{open_val:.2f}" if open_val else "—"),
            KeyStatisticItem(label="Previous Close", value=f"{prev_close:.2f}" if prev_close else "—"),
            KeyStatisticItem(label="Day High", value=f"{day_high:.2f}" if day_high else "—"),
            KeyStatisticItem(label="Day Low", value=f"{day_low:.2f}" if day_low else "—"),
            KeyStatisticItem(label="Volume", value=_fmt_large(volume) if volume else "—"),
            KeyStatisticItem(label="Avg. Volume (3M)", value=_fmt_large(avg_volume) if avg_volume else "—"),
            KeyStatisticItem(label="Market Cap", value=_fmt(market_cap) if market_cap else "—"),
            KeyStatisticItem(label="52-Week High", value=f"{year_high:.2f}" if year_high else "—"),
            KeyStatisticItem(label="52-Week Low", value=f"{year_low:.2f}" if year_low else "—"),
            KeyStatisticItem(label="P/E (TTM)", value=_fmt_ratio(pe)),
            KeyStatisticItem(label="P/E (FWD)", value=_fmt_ratio(pe_fwd)),
            # `is not None`, not truthiness: a break-even company's 0.00 is a real EPS.
            KeyStatisticItem(label="EPS (TTM)", value=eps_shown),
            KeyStatisticItem(label="Dividends", value=div_str),
            KeyStatisticItem(label="Beta", value=f"{beta:.2f}" if beta else "—"),
        ]

        # Groups (4 columns)
        group1 = KeyStatisticsGroupResponse(statistics=[
            KeyStatisticItem(label="Open", value=f"{open_val:.2f}" if open_val else "—"),
            KeyStatisticItem(label="Previous Close", value=f"{prev_close:.2f}" if prev_close else "—"),
            KeyStatisticItem(label="Volume", value=_fmt_large(volume) if volume else "—"),
            KeyStatisticItem(label="Avg. Volume (3M)", value=_fmt_large(avg_volume) if avg_volume else "—"),
            KeyStatisticItem(label="Market Cap", value=_fmt(market_cap) if market_cap else "—"),
        ])

        # 52-Week % Range = ((High - Low) / Low) * 100
        week52_pct_range = None
        if year_high and year_low and year_low > 0:
            week52_pct_range = round(((year_high - year_low) / year_low) * 100, 2)

        group2 = KeyStatisticsGroupResponse(statistics=[
            KeyStatisticItem(label="Day High", value=f"{day_high:.2f}" if day_high else "—"),
            KeyStatisticItem(label="Day Low", value=f"{day_low:.2f}" if day_low else "—"),
            KeyStatisticItem(label="52-Week High", value=f"{year_high:.2f}" if year_high else "—"),
            KeyStatisticItem(label="52-Week Low", value=f"{year_low:.2f}" if year_low else "—"),
            KeyStatisticItem(label="52-Week % Range", value=f"{week52_pct_range:.2f}%" if week52_pct_range is not None else "—"),
        ])

        group3 = KeyStatisticsGroupResponse(statistics=[
            KeyStatisticItem(label="P/E (TTM)", value=_fmt_ratio(pe)),
            KeyStatisticItem(label="P/E (FWD)", value=_fmt_ratio(pe_fwd)),
            # `is not None`, not truthiness: a break-even company's 0.00 is a real EPS.
            KeyStatisticItem(label="EPS (TTM)", value=eps_shown),
            KeyStatisticItem(label="Dividends", value=div_str),
            KeyStatisticItem(label="Beta", value=f"{beta:.2f}" if beta else "—"),
        ])

        # Ownership group
        # Short % of Float: one rule, shared with Ask Cay AI's ownership tool.
        short_interest = short_interest or {}
        short_pct_val = short_percent_of_float(short_interest, float_shares_val, key_metrics)

        short_pct_str = f"{short_pct_val:.2f}%" if short_pct_val is not None else "N/A"

        # 3M Short Change — with color state
        short_change_3m = short_interest.get("short_change_3m")
        short_change_color = None
        if short_change_3m is not None:
            sign = "+" if short_change_3m > 0 else ""
            short_change_str = f"{sign}{short_change_3m:.2f}%"
            if short_change_3m >= 20:
                short_change_color = "warning"   # red — short interest rising fast
            elif short_change_3m <= -20:
                short_change_color = "squeeze"   # green — short squeeze potential
        else:
            short_change_str = "—"

        # Days to Cover
        days_to_cover = short_interest.get("short_ratio")
        dtc_str = f"{days_to_cover:.2f}" if days_to_cover is not None else "—"

        # Free Float from shares-float endpoint (or fallback calculation)
        if not float_shares_val and shares_out and insider_pct is not None:
            try:
                ins = float(insider_pct)
                if ins < 1:
                    ins *= 100
                float_shares_val = shares_out * (1 - ins / 100)
            except (ValueError, TypeError):
                pass
        float_str = _fmt_large(float_shares_val) if float_shares_val else "—"

        group4 = KeyStatisticsGroupResponse(statistics=[
            KeyStatisticItem(label="Short % of Float", value=short_pct_str, is_highlighted=True),
            KeyStatisticItem(label="3M Short Change", value=short_change_str, color_state=short_change_color),
            KeyStatisticItem(label="Days to Cover", value=dtc_str),
            KeyStatisticItem(label="Shares Outstanding", value=_fmt_large(shares_out) if shares_out else "—"),
            KeyStatisticItem(label="Free Float", value=float_str),
        ])

        return flat_stats, [group1, group2, group3, group4]

    # ── Performance Periods ───────────────────────────────────────

    def _build_performance_periods(
        self, stock_hist: List[Dict], spy_hist: List[Dict]
    ) -> List[PerformancePeriodResponse]:
        periods = []
        definitions = [
            ("1 Month", 21),
            ("YTD", None),
            ("1 Year", 252),
            ("3 Years", 756),
            ("5 Years", 1260),
            ("10 Years", 2520),
        ]
        for label, days in definitions:
            if days is None:
                stock_ret = _compute_ytd_return(stock_hist)
                sp_ret = _compute_ytd_return(spy_hist)
            else:
                stock_ret = _compute_return(stock_hist, days)
                sp_ret = _compute_return(spy_hist, days)

            if stock_ret is not None:
                vs_market = round(stock_ret - (sp_ret or 0), 2) if sp_ret is not None else None
                periods.append(PerformancePeriodResponse(
                    label=label,
                    change_percent=round(stock_ret, 2),
                    vs_market_percent=vs_market,
                    sp_return_percent=round(sp_ret, 2) if sp_ret is not None else None,
                ))
        return periods

    # ── Snapshots ─────────────────────────────────────────────────

    def _build_snapshots(
        self, key_metrics: List[Dict], fin_ratios: List[Dict],
        income_annual: List[Dict], balance_annual: List[Dict],
        cashflow_annual: List[Dict], price: float, market_cap: float,
        sector: str, profitability_snapshot=None, growth_snapshot=None, valuation_snapshot=None,
        health_snapshot=None, ownership_snapshot=None,
        profile: Optional[Dict] = None, industry: str = "", ticker: Optional[str] = None,
        income_quarterly: Optional[List[Dict]] = None,
    ) -> List[SnapshotItemResponse]:
        snapshots = []

        # Get most recent data
        km = key_metrics[0] if key_metrics else {}
        fr = fin_ratios[0] if fin_ratios else {}
        inc0 = income_annual[0] if income_annual else {}
        inc1 = income_annual[1] if len(income_annual) > 1 else {}
        bs = balance_annual[0] if balance_annual else {}
        cf0 = cashflow_annual[0] if cashflow_annual else {}
        cf1 = cashflow_annual[1] if len(cashflow_annual) > 1 else {}

        # 1. Profitability (use cached sector-relative snapshot if available)
        if profitability_snapshot is not None:
            snapshots.append(profitability_snapshot)
        else:
            snapshots.append(self._build_profitability_snapshot(km, fr, inc0, bs=bs))

        # 2. Growth (use cached sector-relative snapshot if available)
        if growth_snapshot is not None:
            snapshots.append(growth_snapshot)
        else:
            snapshots.append(self._build_growth_snapshot(inc0, inc1, cf0, cf1, km, key_metrics))

        # 3. Price / Valuation (use cached sector-relative snapshot if available)
        if valuation_snapshot is not None:
            snapshots.append(valuation_snapshot)
        else:
            # Degraded fallback: the multiples only, no peer comparison or score (its
            # ratios are ANNUAL — see `_build_valuation_snapshot`).
            snapshots.append(self._build_valuation_snapshot(
                fr, km, cf0, inc0, bs, profile or {},
            ))

        # 4. Financial Health (use cached sector-relative snapshot if available)
        if health_snapshot is not None:
            snapshots.append(health_snapshot)
        else:
            snapshots.append(self._build_health_snapshot(
                bs, inc0, cf0, fr, km, market_cap, sector=sector, industry=industry,
                ticker=ticker, income_quarterly=income_quarterly,
            ))

        # 5. Insiders & Ownership (use cached snapshot if available)
        if ownership_snapshot is not None:
            snapshots.append(ownership_snapshot)
        else:
            snapshots.append(self._build_ownership_snapshot(km))

        return snapshots

    def _build_profitability_snapshot(
        self, km: Dict, fr: Dict, inc: Dict, bs: Optional[Dict] = None,
    ) -> SnapshotItemResponse:
        """DEGRADED fallback for the Profitability card: the values, with NO verdict.

        Reached ONLY when `get_profitability_snapshot` raised (see `_build_snapshots`).
        Like the Price fallback (`_build_valuation_snapshot`) it rates nothing: its inputs
        are the overview's ANNUAL payloads, and the primary card's verdict is TTM against
        TTM peer medians — so `rating=0` (iOS: unavailable, "—"), every `score` None, no
        `weighted_score` (2026-10-07, review round 2).

        Until then it rated on fixed ROE / margin bands and read ROE from ``km["roe"]``,
        a v3 name /stable key-metrics never sends (it sends ``returnOnEquity``): ROE was
        always absent, so every fallback card rated 1/5 "Low". ROE on negative or zero
        shareholder equity is "N/M", exactly as the primary card and the Health Check
        show it (the same `_equity_state` test, on the annual D/E and the annual balance
        sheet ``bs`` when the caller has one): FMP's ROE is net income / equity, so a
        negative denominator flips its sign (McDonald's-shaped: -216% on a profitable
        company)."""
        from app.services.profitability_snapshot_service import _equity_state

        op_margin = _safe_float(fr, "operatingProfitMargin") or _safe_float(km, "operatingProfitMargin")
        # Fall back to a real MARGIN field only. netIncomePerShare is EPS in dollars,
        # NOT a margin — using it rendered per-share earnings (e.g. $6.13) as a
        # "Net Margin" of 6.13% (or 50% for a $0.50 EPS via the <1 *100 heuristic).
        net_margin = _safe_float(fr, "netProfitMargin") or _safe_float(km, "netProfitMargin")

        def _first_finite(*pairs: Tuple[Dict, str]) -> Optional[float]:
            """First finite value among (record, key) — None, never a 0.0 default, so an
            absent ROE / ROA prints "—" rather than a fabricated "0.00%". A bool is junk,
            not 1.0 / 0.0."""
            for record, key in pairs:
                raw = record.get(key) if isinstance(record, dict) else None
                val = None if isinstance(raw, bool) else _finite(raw)
                if val is not None:
                    return val
            return None

        # /stable key-metrics: `returnOnEquity` / `returnOnAssets`, DECIMALS (1.54 = 154%:
        # scaled unconditionally, as the primary card's `_to_pct` does). ratios and the
        # v3 `roe` stay as fallbacks; `returnOnTangibleAssets` is the last resort for ROA.
        roe_dec = _first_finite((km, "returnOnEquity"), (fr, "returnOnEquity"), (km, "roe"))
        roa_dec = _first_finite(
            (km, "returnOnAssets"), (fr, "returnOnAssets"), (km, "returnOnTangibleAssets"),
        )
        roe = round(roe_dec * 100, 2) if roe_dec is not None else None
        roa = round(roa_dec * 100, 2) if roa_dec is not None else None

        # If margins are in decimal form (0.25 = 25%), convert
        if op_margin and abs(op_margin) < 1:
            op_margin *= 100
        if net_margin and abs(net_margin) < 1:
            net_margin *= 100

        roe_value = _pct(roe)
        if roe is not None:
            de_ratio = _first_finite((fr, "debtToEquityRatio"), (km, "debtToEquity"))
            bs_equity = _first_finite((bs or {}, "totalStockholdersEquity"))
            equity_state = _equity_state(de_ratio, bs_equity)
            if equity_state is not None:
                logger.info(
                    "Overview fallback profitability: ROE %s%% is not meaningful — "
                    "shareholder equity %s (annual D/E=%r, equity=%r) — shown as N/M",
                    roe, equity_state, de_ratio, bs_equity,
                )
                roe_value = "N/M"

        metrics = [
            SnapshotMetricResponse(name="Operating Margin", value=_pct(op_margin)),
            SnapshotMetricResponse(name="Net Margin", value=_pct(net_margin)),
            SnapshotMetricResponse(name="Return on Equity (ROE)", value=roe_value),
            SnapshotMetricResponse(name="Return on Assets (ROA)", value=_pct(roa)),
        ]
        return SnapshotItemResponse(
            category="Profitability", rating=0, metrics=metrics, weighted_score=None,
            computed_at=snapshot_build_time(),
        )

    def _build_growth_snapshot(
        self, inc0: Dict, inc1: Dict, cf0: Dict, cf1: Dict,
        km: Dict, key_metrics: List[Dict],
    ) -> SnapshotItemResponse:
        def _yoy_growth(curr: Dict, prev: Dict, key: str) -> Optional[float]:
            c = _safe_float(curr, key)
            p = _safe_float(prev, key)
            if p and p != 0:
                return ((c - p) / abs(p)) * 100
            return None

        rev_growth = _yoy_growth(inc0, inc1, "revenue")
        # EPS: prefer epsDiluted from income statement, fallback to key-metrics
        eps_curr = _safe_float(inc0, "epsDiluted") or _safe_float(inc0, "eps") or (_safe_float(km, "netIncomePerShare") if km else None)
        km1 = key_metrics[1] if len(key_metrics) > 1 else {}
        eps_prev = _safe_float(inc1, "epsDiluted") or _safe_float(inc1, "eps") or (_safe_float(km1, "netIncomePerShare") if km1 else None)
        eps_growth = None
        if eps_curr and eps_prev and eps_prev != 0:
            eps_growth = ((eps_curr - eps_prev) / abs(eps_prev)) * 100

        fcf_growth = _yoy_growth(cf0, cf1, "freeCashFlow")
        op_growth = _yoy_growth(inc0, inc1, "operatingIncome")

        # Rating based on average of available growths
        growths = [g for g in [rev_growth, eps_growth, fcf_growth, op_growth] if g is not None]
        avg_growth = sum(growths) / len(growths) if growths else 0

        if avg_growth > 20:
            rating = 5
        elif avg_growth > 10:
            rating = 4
        elif avg_growth > 0:
            rating = 3
        elif avg_growth > -10:
            rating = 2
        else:
            rating = 1

        def _fmt_growth(v: Optional[float]) -> str:
            if v is None:
                return "—"
            sign = "+" if v >= 0 else ""
            return f"{sign}{v:.1f}%"

        metrics = [
            SnapshotMetricResponse(name="Revenue Growth (YoY)", value=_fmt_growth(rev_growth)),
            SnapshotMetricResponse(name="EPS Growth", value=_fmt_growth(eps_growth)),
            SnapshotMetricResponse(name="Free Cash Flow Growth (YoY)", value=_fmt_growth(fcf_growth)),
            SnapshotMetricResponse(name="Operating Income Growth", value=_fmt_growth(op_growth)),
        ]
        return SnapshotItemResponse(
            category="Growth", rating=rating, metrics=metrics,
            computed_at=snapshot_build_time(),
        )

    def _build_valuation_snapshot(
        self, fr: Dict, km: Dict, cf: Dict, inc: Dict, bs: Dict, profile: Dict,
    ) -> SnapshotItemResponse:
        """DEGRADED fallback for the Price card: the multiples, with NO peer comparison
        and NO score.

        Reached ONLY when `get_valuation_snapshot` raised (see `_build_snapshots`);
        the primary is `valuation_snapshot_service.get_valuation_snapshot`. It goes
        through the SAME builder (`build_price_snapshot`), so the values follow the
        primary's rules — six metrics, "Neg." for a loss-maker's multiple, never a
        hardcoded sector-average table. (It used to be a private second implementation
        whose every divergence was a silently wrong number; a TestFlight tester
        photographed one.)

        🔴 No comparison since 2026-10-07. This card's inputs are the overview's own
        ANNUAL payloads (`_fetch_fundamentals` reads ratios and key metrics with
        `period="annual"`): fiscal-year-end multiples, up to ~15 months old. Every peer
        median is a TTM (or complete-year) median of CURRENT multiples, so "P/E (1.63x
        sector avg 22)" and the 1-5 rating it fed compared two different clocks — a
        confident verdict on a card the user cannot tell from the real one. Fetching
        /ratios-ttm here would add an FMP call to a path that runs only while the
        valuation service is already failing; the honest option is also the cheaper one:
        bare names ("P/E"), `score=None` on every row, `rating=0` (iOS renders it as
        unavailable, "—"), no `weighted_score`, and no benchmark read at all.
        """
        card = build_price_snapshot(
            fr=fr, km=km, cf=cf, inc=inc, bs=bs, profile=profile, bench={},
        )
        return card.model_copy(update={
            "rating": 0,
            "weighted_score": None,
            "metrics": [m.model_copy(update={"score": None}) for m in card.metrics],
        })

    def _build_health_snapshot(
        self, bs: Dict, inc: Dict, cf: Dict, fr: Dict, km: Dict,
        market_cap: float, *, sector: str = "", industry: str = "",
        ticker: Optional[str] = None, income_quarterly: Optional[List[Dict]] = None,
    ) -> SnapshotItemResponse:
        # Altman Z-Score — ONE implementation, shared with `health_check_service`.
        #
        # 🔴 This was a second, independently written copy, and it still carried the exact
        # defect its twin was fixed for: every missing term was substituted with **0**
        # (`… if market_cap else 0`, and the same for `ebit` and `revenue`). Because this
        # module's `_safe_float` returns `0.0` rather than `None` for an absent field, a
        # failed quote AND profile fetch — logged upstream as a warning only — valued the
        # equity at zero and dropped the 0.6-weighted term entirely. On Apple-shaped inputs
        # that is Z=8.9 ("fortress") rendered as Z=2.1 ("Grey zone. Moderate financial
        # stress signals"), and `z_score` also drives `rating` 1-5 for the whole Health
        # card. `revenue` (weight 1.0) and `ebit` (weight **3.3**) had the same hole.
        #
        # Two behaviour changes fall out of sharing, and both are corrections:
        #   • The `or _safe_float(inc, "ebitda")` fallback is gone. EBITDA is not EBIT — it
        #     adds back D&A — so it overstated the heaviest-weighted term whenever
        #     `operatingIncome` was absent (or an honest 0, which `or` also swallows).
        #   • A missing market cap / EBIT / revenue now OMITS the metric (rating 0, "—")
        #     instead of publishing a confident distress verdict.
        #
        # Imported inside the method: module-scope would couple two large services at
        # import time for one helper, and this mirrors how the rest of the file defers.
        from app.services.financials_metric_gate import (
            INTEREST_COVERAGE,
            company_metric_applicable,
            resolve_payment_network,
            resolve_withheld_company_rows,
            trailing_interest_row,
        )
        from app.services.health_check_service import _compute_z_score, altman_z_applicable

        # `_compute_z_score` reads the raw dicts with its OWN Optional-returning
        # `_safe_float`, so "absent" survives the trip; passing this module's 0.0-defaulted
        # locals would re-introduce the substitution one layer up.
        # `sector` / `industry` reach the shared gate: no Z (rating 0, "—") for a bank,
        # insurer or REIT, where the model reads deposit funding as distress.
        z_score = _compute_z_score(
            bs, inc, market_cap if market_cap else None, sector=sector, industry=industry,
        )

        # Still needed BELOW, for the FCF-margin row — it is not a Z-Score input here any
        # more. (`test_no_undefined_globals` caught the deletion: `ast.parse` and importing
        # the module both stay green on a name that is only read inside a function.)
        revenue = _safe_float(inc, "revenue")

        # Interest coverage
        interest_coverage = _safe_float(fr, "interestCoverage") or _safe_float(km, "interestCoverage")

        # Cash to Debt
        cash = _safe_float(bs, "cashAndCashEquivalents") or _safe_float(bs, "cashAndShortTermInvestments")
        total_debt = _safe_float(bs, "totalDebt") or _safe_float(bs, "longTermDebt")
        cash_to_debt = round(cash / total_debt, 2) if cash and total_debt and total_debt > 0 else None

        # FCF Margin
        fcf = _safe_float(cf, "freeCashFlow")
        fcf_margin = round((fcf / revenue) * 100, 1) if fcf and revenue and revenue > 0 else None

        # Asset Turnover
        asset_turnover = _safe_float(fr, "assetTurnover")

        # Rating based on Z-Score
        if z_score is not None:
            if z_score > 3.0:
                rating = 5
            elif z_score > 2.5:
                rating = 4
            elif z_score > 1.8:
                rating = 3
            elif z_score > 1.0:
                rating = 2
            else:
                rating = 1
        else:
            rating = 0  # unavailable if can't compute

        metrics: List[SnapshotMetricResponse] = []
        # The Z row is OMITTED where the model does not apply (banks, insurers, REITs — the
        # rest of Financial Services / Real Estate), as the Health Check omits it; it used to
        # print a bare "Altman Z-Score —" on every such card (review 2026-10-07, HC-5).
        if altman_z_applicable(sector, industry):
            metrics.append(SnapshotMetricResponse(
                name="Altman Z-Score",
                # `is not None`, not truthiness: a genuine Z of exactly 0.0 is a MEASURED
                # deep-distress reading and must not render as "no data".
                value=f"{z_score}" if z_score is not None else "—",
            ))
        # Interest coverage means nothing for a bank, insurer, capital-markets firm, asset
        # manager or lender (interest IS its cost of goods): the Health Check omits the row
        # for them (`financials_metric_gate.interest_coverage_applicable` — an insurance
        # broker keeps it: a fee business whose lenders watch it), and so does this card.
        # In a MIXED industry ("Financial - Credit Services") only a listed non-lender member
        # keeps the row (`financials_metric_gate.resolve_payment_network`: the ticker is on
        # the curated `NON_LENDER_MEMBERS` and its trailing four quarters — the
        # ``income_quarterly`` the overview already fetched, the SAME input every other
        # surface uses, never this card's annual `inc` — do not read as a lender's; no
        # quarters → the list stands); every other member, and a call with no ticker, does
        # not (fail closed). A curated per-company fact withholds it in any industry
        # (`CURATED_WITHHELD_ROWS`: WU). The row is never compared or scored here (this card
        # rates on Altman Z only).
        network = resolve_payment_network(
            ticker, industry, trailing_interest_row(income_quarterly),
            source="overview_fallback_health",
        )
        resolve_withheld_company_rows(ticker, source="overview_fallback_health")
        if company_metric_applicable(
            INTEREST_COVERAGE, industry, network=network, ticker=ticker,
        ):
            metrics.append(SnapshotMetricResponse(
                name="Interest Coverage",
                value=f"{interest_coverage:.1f}x" if interest_coverage else "—"
            ))
        metrics += [
            SnapshotMetricResponse(name="Cash to Debt", value=f"{cash_to_debt}" if cash_to_debt else "—"),
            SnapshotMetricResponse(name="Free Cash Flow Margin", value=_pct(fcf_margin, 1)),
            SnapshotMetricResponse(
                name="Asset Turnover",
                value=f"{asset_turnover:.2f}" if asset_turnover else "—"
            ),
        ]
        return SnapshotItemResponse(
            category="Financial Health", rating=rating, metrics=metrics,
            computed_at=snapshot_build_time(),
        )

    def _build_ownership_snapshot(self, km: Dict) -> SnapshotItemResponse:
        inst_pct = km.get("institutionalOwnership") or km.get("institutionPercentage")
        insider_pct = km.get("insidersPercentage")

        # Format: if decimal (0.61) multiply by 100
        def _fmt_own(val) -> str:
            if val is None:
                return "—"
            # FMP can hand back "" / "N/A" for absent numeric fields — a raw
            # float() would ValueError and 500 the whole /overview. Degrade to
            # "—" (honest absent) rather than fabricating a 0.0%.
            try:
                v = float(val)
            except (ValueError, TypeError):
                return "—"
            if not math.isfinite(v):
                return "—"
            if v < 1:
                v *= 100
            return f"{v:.1f}%"

        # Unavailable by default — only rate if we have actual data
        rating = 0

        metrics = [
            SnapshotMetricResponse(name="Institutional Ownership", value=_fmt_own(inst_pct)),
            SnapshotMetricResponse(name="Insider Ownership", value=_fmt_own(insider_pct)),
            SnapshotMetricResponse(name="Top 10 Holders", value="—"),
            SnapshotMetricResponse(name="Institutional Activity", value="—"),
        ]
        return SnapshotItemResponse(
            category="Insiders & Ownership", rating=rating, metrics=metrics,
            computed_at=snapshot_build_time(),
        )

    # ── Sector & Industry ─────────────────────────────────────────

    def _build_sector_industry(
        self, profile: Dict, sector_perf: List[Dict],
        industry_perf: List[Dict] = None,
    ) -> SectorIndustryResponse:
        sector_name = profile.get("sector") or "N/A"
        industry = profile.get("industry") or "N/A"

        # --- Sector performance (DAILY) ---
        # `oneYearPerformance` used to be preferred here. It came from FMP's
        # `sector-performance-snapshot`, which is outside the signed Order Form and now
        # answers 402; the entitled substitute is `market_movers_service._group_performance`
        # (screener rows grouped by sector), which publishes a same-session equal-weighted
        # mean and no 1-year column. Reading the absent key just returned 0.0 and fell
        # through to the daily value on every call, so the branch was dead. Removed rather
        # than left in place: the next reader would reasonably assume 1Y is live.
        # The UI label is period-agnostic ("Sector Performance"), so nothing on screen
        # claims a window this figure does not cover.
        sector_perf_value = 0.0
        sector_perf_known = False
        if isinstance(sector_perf, list) and sector_perf:
            logger.debug("[SectorIndustry] sector_perf sample: %s", sector_perf[0])
            for sp in sector_perf:
                sp_sector = sp.get("sector", "")
                if _normalize_sector(sp_sector) == _normalize_sector(sector_name):
                    val = (
                        _safe_float(sp, "changesPercentage")
                        or _safe_float(sp, "averageChangePercent")
                        or _safe_float(sp, "changePercent")
                        or _safe_float(sp, "change_percentage")
                    )
                    if val != 0.0:
                        sector_perf_value = val
                    # A matched sector row IS a measurement (a flat 0.0 included). An
                    # unmatched one is not — see `sector_performance_known`.
                    sector_perf_known = True
                    # Break on the SECTOR match, not on a non-zero value: a sector that
                    # genuinely closed flat is 0.0, and continuing past it used to leave
                    # the loop scanning rows that can never match.
                    break

        # --- Industry rank within sector ---
        industry_rank = "--"
        if industry_perf and isinstance(industry_perf, list):
            logger.debug("[SectorIndustry] industry_perf sample: %s", industry_perf[0])
        if industry_perf and isinstance(industry_perf, list) and sector_name != "N/A":
            # Filter industries in the same sector, sorted by performance desc
            same_sector = [
                ip for ip in industry_perf
                if _normalize_sector(ip.get("sector") or "") == _normalize_sector(sector_name)
                and ip.get("industry")
            ]
            if same_sector:
                same_sector.sort(
                    key=lambda x: _safe_float(x, "changesPercentage")
                    or _safe_float(x, "averageChangePercent")
                    or _safe_float(x, "changePercent")
                    or _safe_float(x, "change_percentage")
                    or 0.0,
                    reverse=True,
                )
                total = len(same_sector)
                rank = None
                for i, ip in enumerate(same_sector):
                    if (ip.get("industry") or "").lower() == industry.lower():
                        rank = i + 1
                        break
                if rank is not None:
                    industry_rank = f"#{rank} of {total}"

        return SectorIndustryResponse(
            sector=sector_name,
            industry=industry,
            sector_performance=round(sector_perf_value, 2),
            industry_rank=industry_rank,
            sector_performance_known=sector_perf_known,
        )

    # ── Company Profile ───────────────────────────────────────────

    def _build_company_profile(
        self, profile: Dict,
        sector_industry: Optional[SectorIndustryResponse] = None,
    ) -> CompanyProfileResponse:
        city = profile.get("city") or ""
        state = profile.get("state") or ""
        country = profile.get("country") or ""
        if city and state:
            hq = f"{city}, {state}"
        elif city and country:
            hq = f"{city}, {country}"
        else:
            hq = country or "N/A"

        website = profile.get("website") or "N/A"
        if website.startswith("https://"):
            website = website[8:]
        elif website.startswith("http://"):
            website = website[7:]

        return CompanyProfileResponse(
            description=profile.get("description") or "No description available.",
            ceo=profile.get("ceo") or "N/A",
            founded=profile.get("ipoDate") or "N/A",
            employees=_safe_int(profile.get("fullTimeEmployees")),
            headquarters=hq,
            website=website,
            sector=sector_industry.sector if sector_industry else profile.get("sector") or "N/A",
            industry=sector_industry.industry if sector_industry else profile.get("industry") or "N/A",
            sector_performance=sector_industry.sector_performance if sector_industry else 0.0,
            sector_performance_known=(
                sector_industry.sector_performance_known if sector_industry else False
            ),
        )

    # ── Related Tickers ───────────────────────────────────────────

    async def _build_related_tickers(
        self, ticker: str
    ) -> List[RelatedTickerResponse]:
        try:
            peers = await self.fmp.get_stock_peers(ticker)
            peers = peers[:6]  # limit to 6
            if not peers:
                return []

            peer_quotes = await price_source(self).get_quotes_list(peers)
            related = []
            for q in peer_quotes:
                if not isinstance(q, dict):
                    continue
                symbol = q.get("symbol", "")
                if not symbol:
                    continue
                # Same defect as the ETF rail (`etf_service._build_related_etfs`):
                # `_safe_float` coerces an ABSENT change to 0.0, and `a or b` folds a
                # genuine 0.0 into the fallback key. The batch path answers
                # `changePercentage: None` for a stale/missing close snapshot, and
                # `RelatedTicker.changePercent` is a non-Optional Double on iOS coloured
                # off `>= 0` — so the peer rendered "+0.0%" in green, a fabricated flat
                # day. OMIT the row when price or change is unknown; keep a real 0.0.
                rel_price = _finite(q.get("price"))
                rel_change = _finite(q.get("changePercentage"))
                if rel_change is None:
                    rel_change = _finite(q.get("changesPercentage"))
                if rel_price is None or rel_price <= 0:
                    logger.warning(
                        "Related ticker %s (for %s) has no usable price — omitting the "
                        "row rather than rendering $0.00", symbol, ticker,
                    )
                    continue
                if rel_change is None:
                    logger.info(
                        "Related ticker %s (for %s) has a price but no day change — "
                        "omitting the row rather than rendering a green +0.0%%",
                        symbol, ticker,
                    )
                    continue
                related.append(RelatedTickerResponse(
                    symbol=symbol,
                    name=q.get("name") or symbol,
                    price=round(rel_price, 2),
                    change_percent=round(rel_change, 2),
                ))
            return related
        except Exception as e:
            logger.warning(f"Related tickers failed for {ticker}: {e}")
            return []

    # ── Benchmark Summary ─────────────────────────────────────────

    def _build_benchmark_summary(
        self, stock_hist: List[Dict], spy_hist: List[Dict],
        ipo_price_data: Optional[Dict] = None,
        ticker: str = "",
    ) -> Optional[BenchmarkSummaryResponse]:
        """Annualised (CAGR) returns for the stock next to the S&P 500, SAME WINDOW.

        ⚠️ THE BUG THIS REPLACED, because it is invisible from the screen and easy to
        reintroduce. The old code aligned SPY to the stock's start date with a scan, and
        when SPY had no row that far back it fell through to `spy_hist[0]` and kept the
        STOCK's label. FMP caps a daily series at 5,000 rows, so the full-history SPY
        fetch begins 2006-10-05 (measured 2026-08-23) — meaning a card reading
        "S&P 500 9.1% · Since Dec 31, 1981" was showing SPY's 2006→2026 CAGR. The two
        numbers a reader was invited to compare covered windows 25 years apart.

        `overlapping_cagrs` measures both sides over the window they share and returns
        the start of it, which is then published verbatim as `since_date`.

        `ipo_price_data` is passed through as the asset anchor. It is INERT on the normal
        path — FMP applies the same 5,000-row cap to SPY, so the shared window floors at
        the cap date regardless — and only reaches the number when SPY history is missing
        entirely. See `benchmark_math`'s module docstring for the measurement.
        """
        if not stock_hist or len(stock_hist) < 252:
            return None

        from datetime import date as _date

        label = f"stock:{ticker or '?'}"
        ipo_anchor = None
        if ipo_price_data and ipo_price_data.get("price") and ipo_price_data.get("date"):
            ipo_anchor = {
                "price": ipo_price_data["price"],
                "date": str(ipo_price_data["date"])[:10],
            }

        # ── All-time, over the window the stock and the S&P both cover ──
        alltime_stock, alltime_sp, alltime_since = overlapping_cagrs(
            stock_hist, spy_hist or [],
            asset_anchor=ipo_anchor, label=label,
        )

        # ── 5-year window (the primary row whenever the stock is old enough) ──
        five_year_cutoff = (_date.today() - timedelta(days=365 * 5)).isoformat()

        def _from_cutoff(rows):
            return [p for p in (rows or []) if (p.get("date") or "")[:10] >= five_year_cutoff]

        hist_5y = _from_cutoff(stock_hist)
        if len(hist_5y) >= 252:
            primary_stock, primary_sp, primary_since = overlapping_cagrs(
                hist_5y, _from_cutoff(spy_hist), label=label,
            )
        else:
            primary_stock, primary_sp, primary_since = alltime_stock, alltime_sp, alltime_since

        # Derived from the MEASURED window, never from which branch ran. `len(hist_5y)
        # >= 252` counts ROWS: a stock that listed fourteen months ago has ~290 of them
        # inside the five-year cutoff, so it took the 5-year branch and would have been
        # labelled "5-year" directly above "since Jul 2025". When the trailing window
        # starts where the whole history does, the honest name for it is "All-time".
        window_label = "All-time" if primary_since == alltime_since else "5-year"

        # The asset's own figure is the reason the block exists. With no number for it
        # there is nothing to compare, and the old `or 0.0` published a flat 0.0% for a
        # stock whose history merely failed to parse. iOS gates the section on `if let`.
        if primary_stock is None:
            return None

        # The secondary all-time row only earns its space when it covers a DIFFERENT
        # window from the primary one.
        show_alltime = (
            alltime_since is not None
            and alltime_since != primary_since
            and alltime_stock is not None
        )

        return BenchmarkSummaryResponse(
            avg_annual_return=primary_stock,
            # Required float on the wire — the shipped iOS build decodes a non-optional
            # Double — so "we could not measure it" travels in `benchmark_available`.
            sp_benchmark=primary_sp if primary_sp is not None else 0.0,
            benchmark_name="S&P 500",
            since_date=format_since(primary_since),
            badge_threshold=0.0,
            window_label=window_label,
            benchmark_available=primary_sp is not None,
            alltime_annual_return=alltime_stock if show_alltime else None,
            alltime_benchmark=alltime_sp if show_alltime else None,
            alltime_since_date=(
                format_since(alltime_since, style="day") if show_alltime else None
            ),
        )


# ── Singleton ────────────────────────────────────────────────────

_service: Optional[StockOverviewService] = None


def get_stock_overview_service() -> StockOverviewService:
    global _service
    if _service is None:
        _service = StockOverviewService()
    return _service
