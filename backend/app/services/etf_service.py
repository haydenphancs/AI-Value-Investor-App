"""
ETF Detail Service — aggregates FMP data, computes derived stats,
and generates AI-powered snapshot analysis via Gemini.

Serves the ETFDetailView screen on iOS.
"""

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client, FMPClient
from app.services.agents.persona_config import neutral_system_instruction
from app.integrations.gemini import get_gemini_client
from app.schemas.etf import (
    BenchmarkSummaryResponse,
    ETFAssetAllocationResponse,
    ETFConcentrationResponse,
    ETFDetailResponse,
    ETFDividendHistoryResponse,
    ETFDividendPaymentResponse,
    ETFHoldingsRiskResponse,
    ETFIdentityRatingResponse,
    ETFNetYieldResponse,
    ETFNewsArticleResponse,
    ETFProfileResponse,
    ETFSectorWeightResponse,
    ETFStrategyResponse,
    ETFTopHoldingResponse,
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    MarketStatusResponse,
    PerformancePeriodResponse,
    RelatedTickerResponse,
)
from app.utils.market_hours import market_status_fields, to_utc_instant

logger = logging.getLogger(__name__)

# ── Per-section in-memory cache ──────────────────────────────────
#
# This service used ONE key and ONE 5-minute TTL over a payload whose sections range from
# "moves every second" (the quote) to "changes at a corporate action" (expense ratio,
# inception date). Because the key carried range+interval, every range pill was a separate
# cold build that re-fetched the SAME quote, the SAME 1.1 MB daily history and the SAME
# profile / holdings / sector weights / dividends. Measured: ~104 FMP calls and ~10 MB of
# byte-identical history to browse one ETF's 7 range pills, 84% of it duplicated.
#
# Now every section has its own key and a TTL matched to how fast that data really moves,
# and every range-INDEPENDENT section is keyed on the SYMBOL ALONE — which is the whole
# saving, since `chart_data` is the only field the range actually reaches. Same shape as
# commodity_service (migration 149) and index_service.
#
# The TTL travels WITH the entry rather than being supplied by the reader: the writer knows
# the section's volatility, so a reader cannot mismatch it. `_cache_get` still honours an
# explicit ttl argument for the call sites that predate this.

_cache: Dict[str, Tuple] = {}
_CACHE_TTL_SECONDS = 300  # default when a writer declares nothing
_AI_CACHE_TTL_SECONDS = 3600  # 1 hour for AI-generated snapshots
# 12h, not 1h: the S&P history is daily EOD bars shared by every ETF on the platform, and
# it only changes at a close. At 1h it was re-pulled ~11x a day for no new data.
_SP_HIST_CACHE_TTL = 43_200

# Per-section TTLs, ordered by how fast the underlying data actually moves.
_QUOTE_TTL = 45             # live price + the quote-derived key-stat rows
_RELATED_TTL = 60           # sibling ETF quotes — same data class, less prominent
_INTRADAY_CHART_TTL = 60    # 1D/1W bars; the only genuinely per-range fetch
_HISTORY_TTL = 43_200       # 12h — daily EOD bars only change at the close
_DERIVED_TTL = 43_200       # 12h — performance periods + benchmark, both from history
_FUNDAMENTALS_TTL = 43_200  # 12h — profile, etf-info, holdings, sectors, dividends

# Hard cap on live entries — see stock_overview_service for rationale. Eviction
# is least-recently-written; a miss just re-fetches (no correctness impact).
_CACHE_MAX_ENTRIES = 1024


def _cache_get(key: str, ttl: Optional[float] = None) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value, entry_ttl = entry
    # An explicit reader TTL still wins, for the sites that predate per-entry TTLs.
    if time.time() - ts > (entry_ttl if ttl is None else ttl):
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any, ttl: Optional[float] = None) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value, ttl or _CACHE_TTL_SECONDS)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# Thundering-herd guard, keyed on the FULL request shape because the assembled response is
# range-specific. This service had none: N concurrent viewers of a cold SPY each ran the
# entire fan-out. The per-section fetchers dedup through their own keys within one build.
_inflight: Dict[str, asyncio.Future] = {}


# ── Related ETF mappings ─────────────────────────────────────────

_RELATED_ETFS: Dict[str, List[str]] = {
    "SPY": ["VOO", "IVV", "QQQ", "DIA", "IWM", "VTI"],
    "VOO": ["SPY", "IVV", "VTI", "QQQ", "SCHX", "SPLG"],
    "IVV": ["SPY", "VOO", "VTI", "QQQ", "SCHX", "SPLG"],
    "QQQ": ["QQQM", "SPY", "VGT", "XLK", "IWM", "ARKK"],
    "DIA": ["SPY", "VOO", "IWM", "VTI", "SCHD", "VYM"],
    "IWM": ["IJR", "VB", "SCHA", "SPY", "QQQ", "DIA"],
    "VTI": ["ITOT", "SPTM", "SPY", "VOO", "SCHB", "IWV"],
    "ARKK": ["QQQ", "ARKW", "ARKG", "VGT", "XLK", "QQQM"],
    "SCHD": ["VYM", "DVY", "HDV", "DGRO", "VIG", "SDY"],
    "VYM": ["SCHD", "DVY", "HDV", "DGRO", "VIG", "SDY"],
    "XLK": ["VGT", "QQQ", "IGV", "FTEC", "IYW", "SMH"],
    "XLF": ["VFH", "IYF", "KBE", "KRE", "FNCL", "IYG"],
    "XLE": ["VDE", "IYE", "FENY", "OIH", "XOP", "AMLP"],
    "GLD": ["IAU", "SLV", "GLDM", "SGOL", "AAAU", "BAR"],
    "TLT": ["IEF", "SHY", "BND", "AGG", "VGLT", "EDV"],
    "BND": ["AGG", "BNDX", "TLT", "IEF", "SCHZ", "FBND"],
}

_DEFAULT_RELATED = ["SPY", "QQQ", "DIA", "IWM", "VTI", "SCHD"]


# ── Static ETF reference data (fallback when FMP premium endpoints are unavailable) ──

_ETF_REFERENCE: Dict[str, Dict[str, Any]] = {
    "SPY":  {"expense_ratio": 0.0945, "holdings": 503, "turnover": 2.0, "index": "S&P 500"},
    "VOO":  {"expense_ratio": 0.03,   "holdings": 504, "turnover": 2.4, "index": "S&P 500"},
    "IVV":  {"expense_ratio": 0.03,   "holdings": 503, "turnover": 5.0, "index": "S&P 500"},
    "QQQ":  {"expense_ratio": 0.20,   "holdings": 101, "turnover": 8.4, "index": "Nasdaq-100"},
    "QQQM": {"expense_ratio": 0.15,   "holdings": 101, "turnover": 8.4, "index": "Nasdaq-100"},
    "DIA":  {"expense_ratio": 0.16,   "holdings": 30,  "turnover": 14.0, "index": "Dow Jones Industrial"},
    "IWM":  {"expense_ratio": 0.19,   "holdings": 1974, "turnover": 18.0, "index": "Russell 2000"},
    "VTI":  {"expense_ratio": 0.03,   "holdings": 3636, "turnover": 2.2, "index": "CRSP US Total Market"},
    "ARKK": {"expense_ratio": 0.75,   "holdings": 35,  "turnover": 60.0, "index": "Active (No Index)"},
    "SCHD": {"expense_ratio": 0.06,   "holdings": 104, "turnover": 14.0, "index": "Dow Jones US Dividend 100"},
    "VYM":  {"expense_ratio": 0.06,   "holdings": 462, "turnover": 8.0, "index": "FTSE High Dividend Yield"},
    "XLK":  {"expense_ratio": 0.09,   "holdings": 69,  "turnover": 5.0, "index": "Technology Select Sector"},
    "XLF":  {"expense_ratio": 0.09,   "holdings": 72,  "turnover": 5.0, "index": "Financial Select Sector"},
    "XLE":  {"expense_ratio": 0.09,   "holdings": 23,  "turnover": 5.0, "index": "Energy Select Sector"},
    "GLD":  {"expense_ratio": 0.40,   "holdings": 1,   "turnover": 0.0, "index": "Gold Spot Price"},
    "TLT":  {"expense_ratio": 0.15,   "holdings": 36,  "turnover": 15.0, "index": "ICE US Treasury 20+ Year"},
    "BND":  {"expense_ratio": 0.03,   "holdings": 17400, "turnover": 40.0, "index": "Bloomberg US Aggregate"},
    "AGG":  {"expense_ratio": 0.03,   "holdings": 12200, "turnover": 40.0, "index": "Bloomberg US Aggregate"},
    "VGT":  {"expense_ratio": 0.10,   "holdings": 316, "turnover": 3.0, "index": "MSCI US IMI Info Tech"},
    "SPLG": {"expense_ratio": 0.02,   "holdings": 503, "turnover": 2.0, "index": "S&P 500"},
    "ITOT": {"expense_ratio": 0.03,   "holdings": 3496, "turnover": 4.0, "index": "S&P Total Market"},
    "IJR":  {"expense_ratio": 0.06,   "holdings": 602, "turnover": 16.0, "index": "S&P SmallCap 600"},
    "VB":   {"expense_ratio": 0.05,   "holdings": 1381, "turnover": 11.0, "index": "CRSP US Small Cap"},
    "SMH":  {"expense_ratio": 0.35,   "holdings": 26,  "turnover": 17.0, "index": "MVIS US Listed Semiconductor"},
    "DGRO": {"expense_ratio": 0.08,   "holdings": 407, "turnover": 14.0, "index": "Morningstar US Dividend Growth"},
    "VIG":  {"expense_ratio": 0.06,   "holdings": 315, "turnover": 10.0, "index": "S&P US Dividend Growers"},
}


# ── Helpers ──────────────────────────────────────────────────────


def _finite_num(v: Any, default: float = 0.0) -> float:
    """Coerce to a finite float, or ``default``.

    FMP weight / price / change fields are forwarded straight into REQUIRED
    response floats (``weight``/``price``/``change_percent``) that have no Pydantic
    finiteness guard. ``float("NaN")`` / ``float("inf")`` SUCCEED (the string
    ``try`` only catches ValueError/TypeError), so a malformed holdings row could
    put a non-finite into the response — Starlette renders with ``allow_nan=False``
    and would raise, 500-ing the ENTIRE ETF detail (blanking valid price/chart/
    profile). Reject non-finite here, mirroring ``chart_helper._finite_or_none``.
    """
    try:
        f = float(v)
    except (ValueError, TypeError):
        return default
    return f if math.isfinite(f) else default


def _fmt(value: Optional[float], decimals: int = 2, prefix: str = "$") -> str:
    """Format a number with commas and N decimal places."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000_000_000:
        return f"{prefix}{value / 1_000_000_000_000:.1f}T"
    if abs(value) >= 1_000_000_000:
        return f"{prefix}{value / 1_000_000_000:.1f}B"
    if abs(value) >= 1_000_000:
        return f"{prefix}{value / 1_000_000:.1f}M"
    return f"{value:,.{decimals}f}"


def _fmt_dollar(value: Optional[float], decimals: int = 2) -> str:
    """Format as dollar amount."""
    if value is None:
        return "—"
    return f"${value:,.{decimals}f}"


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}%"


def _compute_return(prices: List[Dict], days_back: int) -> Optional[float]:
    """Compute % return over the last N trading days."""
    if not prices or len(prices) < 2:
        return None
    # Not enough history to cover the requested window: return None so the caller
    # OMITS this period rather than mislabeling a shorter (e.g. since-inception)
    # return under a "3Y"/"5Y"/"10Y" label (a young ETF would otherwise show its
    # full-history return identically for 3Y/5Y/10Y). Genuine since-inception CAGR
    # uses _build_benchmark_summary, not this fallback.
    if len(prices) <= days_back:
        return None
    # Finite-guard both ends: a NaN/Inf close is truthy and slips past
    # `not start`/`start == 0`, producing a NaN change_percent that serializes to an
    # invalid-JSON `NaN` token and crashes the iOS decode of the whole ETF detail
    # screen (change_percent is a non-optional Double on iOS). Matches index/commodity.
    from app.services.chart_helper import _finite_or_none
    start = _finite_or_none(prices[-(days_back + 1)].get("close") or prices[-(days_back + 1)].get("adjClose"))
    end = _finite_or_none(prices[-1].get("close") or prices[-1].get("adjClose"))

    if not start or not end or start == 0:
        return None
    return ((end - start) / start) * 100


def _compute_ytd_return(prices: List[Dict]) -> Optional[float]:
    if not prices or len(prices) < 2:
        return None
    current_year = datetime.now(tz=timezone.utc).year
    from app.services.chart_helper import _finite_or_none
    for p in prices:
        date_str = p.get("date") or ""
        if date_str.startswith(str(current_year)):
            # Finite-guard so a NaN/Inf close degrades to an omitted period, not a
            # NaN change_percent that breaks the (non-optional) iOS decode.
            start_price = _finite_or_none(p.get("close") or p.get("adjClose"))
            end_price = _finite_or_none(prices[-1].get("close") or prices[-1].get("adjClose"))
            if start_price and end_price and start_price > 0:
                return ((end_price - start_price) / start_price) * 100
            break
    return None


def _revalidate_rows(model, rows: Any, symbol: str, label: str) -> List[Any]:
    """Rebuild response models from a Tier-2 payload, dropping anything that no longer fits.

    A persisted row is JSON that a PREVIOUS deploy's schema wrote, so it can be missing a
    field this one requires. `Model(**row)` would then raise a ValidationError out of the
    middle of the build and 500 the whole screen — for up to 12 hours, for every viewer of
    that symbol, with no way to self-heal short of the TTL expiring.

    Drop the bad rows and log loudly instead. An empty Performance card is a visible,
    recoverable degradation; a 500 is not. (Related trap, from an earlier pass: an additive
    field with a DEFAULT is worse still — it laundered stale rows into a confident wrong
    value. Always ask what a cached row lacking the new key deserializes into.)
    """
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            out.append(model(**row))
        except Exception as e:
            logger.warning(
                "ETF tier-2 %s row for %s failed revalidation (stale schema?): %s: %s",
                label, symbol, type(e).__name__, e,
            )
    return out


def _get_market_status() -> MarketStatusResponse:
    """Current session, delegated to the one holiday/half-day-aware implementation.

    This used to be a local copy of weekday+hour arithmetic — one of three — and it
    knew nothing about market holidays or the 13:00 ET half-days, so it reported
    "open" at 11:00 on Thanksgiving and until 16:00 the Friday after. `market_hours`
    owns the calendar (and `home_dashboard_service` already delegated to it).
    """
    return MarketStatusResponse(**market_status_fields())
def _format_date_readable(date_str: str) -> str:
    """Convert YYYY-MM-DD to human-readable format like 'Dec 20, 2025'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return date_str or "—"


# ── Main service ─────────────────────────────────────────────────




class ETFService:
    """Aggregates FMP data + Gemini AI for the ETF Detail screen."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()
        from app.database import get_supabase
        self.supabase = get_supabase()

    # ── Supabase cache-aside helpers ─────────────────────────────

    @staticmethod
    def _cache_key(symbol: str, chart_range: str, interval: Optional[str]) -> str:
        """Identity of one ETF detail payload: symbol AND the chart shape it was
        built for. See the comment at the cache check in `get_etf_detail`."""
        return f"{symbol.upper()}_{chart_range}_{interval or 'default'}"

    # ── Tier 2 (Supabase `etf_snapshot_cache`) ────────────────────
    #
    # The table already exists with UNIQUE(symbol, category) (migration 034) and already
    # serves the three side-endpoints, so the decomposition needs NO new migration — only
    # new category strings: `fundamentals`, `derived`, and `chart:{range}:{interval}`.
    #
    # Only sections that CANNOT contain a live price are persisted. That is what removes
    # the whole class of bug `_refresh_volatile` existed to patch: the monolith froze
    # `current_price` into a 24-hour row, so a cache hit served a day-old price and the
    # quote-derived key stats alongside it. Here the quote and the related quotes are
    # Tier-1 only, so a Tier-2 hit still renders a price fetched seconds ago.
    #
    # `get_supabase()` is imported inside rather than read off `self`: the tests build this
    # service with `__new__`, so `__init__` never runs and `self.supabase` would not exist.

    _TIER2_TTL_HOURS = 12

    @staticmethod
    def _tier2_get(symbol: str, category: str) -> Optional[Any]:
        """Read one cached section, or None. Never raises.

        Accepts a list OR a dict payload — `chart` persists a list of bars, and the older
        `_check_snapshot_cache` rejects anything that is not a dict.
        """
        try:
            from app.database import get_supabase

            row = (
                get_supabase()
                .table("etf_snapshot_cache")
                .select("response_json, cached_at")
                .eq("symbol", symbol)
                .eq("category", category)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None
            entry = row.data[0]
            cached_at = datetime.fromisoformat(
                (entry.get("cached_at") or "").replace("Z", "+00:00")
            )
            if datetime.now(timezone.utc) - cached_at > timedelta(
                hours=ETFService._TIER2_TTL_HOURS
            ):
                return None
            return entry.get("response_json")
        except Exception as e:
            logger.warning(
                "ETF tier-2 read failed for %s/%s: %s: %s",
                symbol, category, type(e).__name__, e,
            )
            return None

    @staticmethod
    def _tier2_put(symbol: str, category: str, payload: Any) -> None:
        """Persist one section. Best-effort; a failure only costs a rebuild."""
        try:
            from app.database import get_supabase

            get_supabase().table("etf_snapshot_cache").upsert(
                {
                    "symbol": symbol,
                    "category": category,
                    "response_json": payload,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="symbol,category",
            ).execute()
        except Exception as e:
            logger.warning(
                "ETF tier-2 write failed for %s/%s: %s: %s",
                symbol, category, type(e).__name__, e,
            )

    # ── Per-section fetchers ──────────────────────────────────────
    #
    # Every one of these except `_get_chart` is keyed on the SYMBOL ALONE, deliberately:
    # none of them depends on the chart range. That is the entire saving — the quote, the
    # 1.1 MB history and the five fundamentals calls used to be re-issued once per pill.

    async def _get_quote(self, symbol: str) -> Dict[str, Any]:
        """Live quote. Range-independent, so every range pill shares one fetch."""
        key = f"etf:quote:{symbol}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            quote = await self.fmp.get_stock_price_quote(symbol)
        except Exception as e:
            logger.warning(
                "ETF quote fetch failed for %s: %s: %s", symbol, type(e).__name__, e
            )
            return {}
        if not isinstance(quote, dict) or not quote:
            return {}
        # Only a usable quote is cached — caching {} would pin a blank price header for
        # the whole TTL.
        _cache_set(key, quote, _QUOTE_TTL)
        return quote

    async def _get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """Profile + etf-info + holders + sector weights + dividends, as one 12h section.

        Five FMP calls that change at a corporate action, not at a tick, and that the old
        code re-issued on every range pill. Persisted, because none of them carries a live
        price — the `profile` payload does, so it is stored as an explicit PROJECTION of
        the fields this service actually reads rather than raw.

        Dividends are fetched at limit=100 (what `get_dividend_history` wants) rather than
        the detail's 20, so the dividends endpoint can share this section without silently
        losing 80 rows. The detail slices what it needs.
        """
        key = f"etf:fund:{symbol}"
        cached = _cache_get(key)
        if cached is not None:
            return cached

        db = await asyncio.to_thread(self._tier2_get, symbol, "fundamentals")
        if isinstance(db, dict) and db:
            logger.info("ETF fundamentals tier-2 HIT for %s", symbol)
            _cache_set(key, db, _FUNDAMENTALS_TTL)
            return db

        results = await asyncio.gather(
            self.fmp.get_company_profile(symbol),
            self.fmp.get_etf_info(symbol),
            self.fmp.get_etf_holders(symbol, limit=20),
            self.fmp.get_etf_sector_weightings(symbol),
            self.fmp.get_dividend_history(symbol, limit=100),
            return_exceptions=True,
        )
        names = ("profile", "etf_info", "holders", "sector_weights", "dividends")
        for name, r in zip(names, results):
            if isinstance(r, Exception):
                logger.error(
                    "ETF %s fetch failed for %s: %s: %s",
                    name, symbol, type(r).__name__, r,
                )

        def _ok(i, default):
            r = results[i]
            return default if isinstance(r, Exception) or r is None else r

        profile = _ok(0, {})
        bundle = {
            # A PROJECTION, not the raw profile: `get_company_profile` returns `price` and
            # `changes`, and persisting those for 12h is exactly the stale-price bug this
            # design refuses to have. Only the price-free fields this service reads.
            "profile": {
                k: profile.get(k)
                for k in (
                    "companyName", "beta", "averageVolume", "lastDividend", "lastDiv",
                    "website", "description", "ipoDate", "marketCap",
                )
            } if isinstance(profile, dict) else {},
            "etf_info": _ok(1, {}),
            "holders": _ok(2, []),
            "sector_weights": _ok(3, []),
            "dividends": _ok(4, []),
        }

        # Degradation gate: a bundle where every call failed would pin an empty Holdings
        # tab, a blank expense ratio and a dashed dividend row for 12 hours.
        if not any([bundle["etf_info"], bundle["holders"], bundle["profile"]]):
            logger.warning(
                "ETF fundamentals NOT cached for %s — every upstream call failed; "
                "will rebuild on the next request", symbol,
            )
            return bundle

        _cache_set(key, bundle, _FUNDAMENTALS_TTL)
        await asyncio.to_thread(self._tier2_put, symbol, "fundamentals", bundle)
        return bundle

    async def _get_history(self, symbol: str) -> List[Dict]:
        """FULL daily history, oldest-first. Range-independent and 12h-cached.

        `_fetch_all_daily` rather than a single `from_date="1900-01-01"` call: FMP caps a
        response at 5,000 rows, so the old one-shot fetch silently TRUNCATED any ETF with
        more than ~19.8 years of history — SPY starts in 1993. Every daily-or-coarser
        chart is now derived from this one list, so a truncated tail would show up as a
        short ALL range and a wrong since-inception CAGR.

        Deliberately Tier-1 only: ~1 MB per symbol, and reading that back out of Supabase
        is slower than the FMP call it would replace.
        """
        key = f"etf:hist:{symbol}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        # Per-section dedup, separate from the detail-level one. Two callers inside a
        # SINGLE build (`_get_chart` and `_get_derived`) can both miss this key at the same
        # instant, and the detail guard cannot help because it is keyed on the range — as
        # are two concurrent users on different range pills of the same cold symbol.
        # Measured: a cold build fetched the history TWICE without this.
        if key in _inflight:
            return await asyncio.shield(_inflight[key])
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        _inflight[key] = fut
        try:
            try:
                from app.services.chart_helper import _fetch_all_daily
                historical = await _fetch_all_daily(self.fmp, symbol)
            except Exception as e:
                logger.warning(
                    "ETF history fetch failed for %s: %s: %s", symbol, type(e).__name__, e
                )
                historical = []
            if historical:
                # `date` may be an explicit JSON null; `or ""` avoids a None<str TypeError.
                historical.sort(key=lambda p: p.get("date") or "")
                _cache_set(key, historical, _HISTORY_TTL)
            if not fut.done():
                fut.set_result(historical)
            return historical
        except asyncio.CancelledError:
            # CancelledError is a BaseException and would leave the future unresolved,
            # hanging every joiner for the life of the process.
            if not fut.done():
                fut.set_exception(RuntimeError("ETF history fetch was cancelled"))
            raise
        except Exception as e:
            if not fut.done():
                fut.set_exception(e)
            raise
        finally:
            _inflight.pop(key, None)

    async def _get_spy_history(self) -> List[Dict]:
        """The S&P 500 benchmark series, shared by every ETF on the platform.

        Delegates to `_get_history("SPY")` rather than keeping a second body: both want
        SPY's full daily series, and two functions writing the same key is how one of them
        silently drifts. It also inherits the `_fetch_all_daily` paging — the benchmark
        CAGR is computed from the FIRST available date, so a 5,000-row truncation would
        move the "since" date and change the number on screen.
        """
        return await self._get_history("SPY")

    async def _get_derived(self, symbol: str, index_tracked: str = "") -> Dict[str, Any]:
        """Everything computed FROM the two histories: performance periods + benchmark.

        This is the section that makes Tier 2 worth having — both are pure functions of a
        multi-thousand-row history, so persisting them lets a cold process serve a full
        screen without ever pulling the 1.1 MB. Neither reads the live quote, so nothing
        stale can hide in here.
        """
        key = f"etf:derived:{symbol}"
        cached = _cache_get(key)
        if cached is not None:
            return cached

        db = await asyncio.to_thread(self._tier2_get, symbol, "derived")
        if isinstance(db, dict) and db:
            logger.info("ETF derived tier-2 HIT for %s", symbol)
            _cache_set(key, db, _DERIVED_TTL)
            return db

        if symbol == "SPY":
            # The benchmark IS this ETF. Gathering both would run two concurrent misses on
            # one cache key and fetch the identical series twice.
            historical = await self._get_history(symbol)
            spy_hist = historical
        else:
            historical, spy_hist = await asyncio.gather(
                self._get_history(symbol), self._get_spy_history()
            )
        perf = self._build_performance_periods(historical, spy_hist)
        bench = self._build_benchmark_summary(
            historical, spy_hist, symbol=symbol, index_tracked=index_tracked
        )
        derived = {
            "performance_periods": [p.model_dump() for p in perf],
            "benchmark_summary": bench.model_dump() if bench else None,
        }

        # Degradation gate: a failed or empty history yields an empty Performance card.
        # Persisting that pins it for 12 hours.
        if not historical:
            logger.warning(
                "ETF derived NOT persisted for %s (empty/failed history) — will rebuild "
                "on the next request", symbol,
            )
            return derived

        _cache_set(key, derived, _DERIVED_TTL)
        await asyncio.to_thread(self._tier2_put, symbol, "derived", derived)
        return derived

    async def _get_chart(
        self, symbol: str, chart_range: str, interval: Optional[str]
    ) -> List[Dict]:
        """Chart bars for one range, derived from the shared history wherever possible.

        Only 1D (5min) and 1W (1hour) are genuinely sub-daily and need their own FMP call.
        Everything daily-or-coarser comes out of the history we already hold:
          * 3M/6M/1Y -> slice     (`_extract_chart_data`, as before)
          * 5Y/ALL   -> aggregate (`_aggregate_prices`, which `fetch_chart_data` was
                                   already calling — after re-fetching the very history we
                                   are holding, and for ALL that meant up to 5 paged calls
                                   on every single request)
        """
        from app.services.chart_helper import (
            AGGREGATED_INTERVALS,
            INTRADAY_INTERVALS,
            _aggregate_prices,
            fetch_chart_data,
            resolve_interval,
            window_by_range,
        )

        resolved = resolve_interval(chart_range, interval)
        category = f"chart:{chart_range}:{resolved}"
        # Non-intraday bars move at a close, so they persist. An intraday series must NOT:
        # a 12h-old 5-minute chart would paint yesterday's session under a live header.
        persistable = resolved not in INTRADAY_INTERVALS

        if persistable:
            db = await asyncio.to_thread(self._tier2_get, symbol, category)
            if isinstance(db, list) and db:
                # Filter on the way OUT, not just on the way in. These rows were written by
                # a previous deploy, and iOS declares `close` non-optional on every chart
                # point — one row without a usable close fails the whole screen's decode.
                bars = [
                    r for r in db
                    if isinstance(r, dict) and isinstance(r.get("close"), (int, float))
                    and math.isfinite(r["close"]) and r["close"] > 0
                ]
                if bars:
                    logger.info("ETF chart tier-2 HIT for %s/%s", symbol, category)
                    return bars
                logger.warning(
                    "ETF chart tier-2 row for %s/%s had no usable bars — rebuilding",
                    symbol, category,
                )

        historical = (
            [] if resolved in INTRADAY_INTERVALS else await self._get_history(symbol)
        )

        if resolved in AGGREGATED_INTERVALS and historical:
            bars = _aggregate_prices(historical, resolved)
            if chart_range != "ALL":
                # ALL means the whole series; every other aggregated range is windowed —
                # with the interval's indicator warm-up kept.
                bars = window_by_range(bars, chart_range, resolved)
            if bars:
                await asyncio.to_thread(self._tier2_put, symbol, category, bars)
            return bars

        if resolved == "daily":
            bars = self._extract_chart_data(historical, chart_range)
            if bars:
                await asyncio.to_thread(self._tier2_put, symbol, category, bars)
            return bars

        # Genuinely intraday (1D/1W): its own short-lived key, never persisted.
        key = f"etf:chart:{symbol}:{chart_range}:{resolved}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            bars = await fetch_chart_data(self.fmp, symbol, chart_range, interval)
        except Exception as e:
            logger.warning(
                "ETF intraday chart failed for %s %s: %s: %s",
                symbol, chart_range, type(e).__name__, e,
            )
            return []
        if bars:
            _cache_set(key, bars, _INTRADAY_CHART_TTL)
        return bars

    async def _get_related(self, symbol: str) -> List[RelatedTickerResponse]:
        """Related-ETF quotes. Range-independent, and previously not cached AT ALL —
        `_build_related_etfs` ran its peers lookup and batch quote on every single build,
        i.e. once per range pill."""
        key = f"etf:related:{symbol}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        related = await self._build_related_etfs(symbol)
        # Partial is fine to serve but not to cache: pinning a short list for the TTL is
        # how a "Related ETFs" row silently disappears for everyone.
        if related:
            _cache_set(key, related, _RELATED_TTL)
        return related

    # ── Main entry point ──────────────────────────────────────────

    async def get_etf_detail(
        self, symbol: str, chart_range: str = "3M", interval: str = None
    ) -> ETFDetailResponse:
        """Cache-aside wrapper (Tier-1 + in-flight dedup) around the build.

        The ASSEMBLED response keeps a SHORT TTL deliberately. Its expensive sections are
        cached individually for 12h, so a rebuild here costs zero FMP calls when they are
        warm — but the price header is part of this payload, and a 5-minute assembled TTL
        would serve a 5-minute-old price on a screen whose whole point is a live quote.
        Short outer TTL + long inner TTLs gives both.

        The old Supabase tier for the whole payload is gone: it was a 24-hour row carrying
        `current_price`, which is why `_refresh_volatile` had to exist at all.
        """
        symbol = symbol.upper()
        cache_key = self._cache_key(symbol, chart_range, interval)

        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info("ETF detail in-memory HIT for %s", cache_key)
            return cached

        # SHIELDED join: a joiner that gives up must not cancel the shared future and take
        # every other waiter down with it.
        if cache_key in _inflight:
            logger.info("ETF detail in-flight JOIN for %s", cache_key)
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future
        try:
            result = await self._build_etf_detail(
                symbol, chart_range=chart_range, interval=interval
            )
            # Never cache a degraded build: with the quote failed every price field is 0,
            # and pinning that shows "$0.00" to every viewer for the whole TTL.
            # `getattr`, not attribute access — the gate must judge only what it can
            # inspect, and a test double has no price to assess.
            price = getattr(result, "current_price", None)
            if price is None or price > 0:
                _cache_set(cache_key, result, _QUOTE_TTL)
            else:
                logger.warning(
                    "ETF detail NOT cached for %s — no usable price (degraded build); "
                    "will rebuild on the next request", cache_key,
                )
            if not future.done():
                future.set_result(result)
            return result
        except asyncio.CancelledError:
            # CancelledError is a BaseException, so it skips the handler below and would
            # leave the future unresolved — every joiner would hang for the life of the
            # process. Hand them a normal exception, then honour our own cancellation.
            if not future.done():
                future.set_exception(RuntimeError("ETF detail fetch was cancelled"))
            raise
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            _inflight.pop(cache_key, None)

    async def get_etf_quote(
        self,
        symbol: str,
        chart_range: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> "ETFQuoteResponse":
        """The light slice behind `GET /etfs/{symbol}/quote`.

        PROJECTED from the full build rather than assembled from a second, parallel set of
        section builders. That is deliberate: every section is already cached, so the full
        assembly costs ZERO extra FMP calls here, and a second assembly path for the same
        fields is exactly how two code paths drift into disagreeing about one number.

        What the client gains is the WIRE payload — roughly a fifth of the monolith, and
        no need to replace its whole view model on a 30-second tick. The sections dropped
        here (performance, benchmark, profile, identity, strategy, net yield, holdings)
        are all range-independent and cannot change between refreshes.
        """
        from app.schemas.etf import ETFQuoteResponse

        full = await self.get_etf_detail(
            symbol, chart_range=chart_range or "3M", interval=interval
        )
        return ETFQuoteResponse(
            symbol=full.symbol,
            current_price=full.current_price,
            price_change=full.price_change,
            price_change_percent=full.price_change_percent,
            market_status=full.market_status,
            # Bars only when the caller asked for a range — the 30s loop skips them on a
            # daily chart, where nothing below the last candle can have moved.
            chart_data=full.chart_data if chart_range else [],
            key_statistics=full.key_statistics,
            key_statistics_groups=full.key_statistics_groups,
            related_etfs=full.related_etfs,
        )

    async def _build_etf_detail(
        self, symbol: str, chart_range: str = "3M", interval: str = None
    ) -> ETFDetailResponse:
        """
        Assemble complete ETF detail data from the per-section caches.

        Steps:
          1. Parallel, per-section cached fetches
          2. Compute key statistics; read performance periods from `derived`
          3. Generate AI snapshots via Gemini (identity, strategy, net yield, holdings risk)
          4. Build related ETFs
          5. Assemble and return the response
        """
        symbol = symbol.upper()

        # ── Step 1: Parallel, per-section cached fetches ──────────
        #
        # All of these except the chart are keyed on the SYMBOL alone and shared by every
        # range pill. They used to be re-fetched per range: 7 quotes, 7 x 1.1 MB of
        # history, 7 profiles, 7 holdings lists. Still gathered concurrently, so a cold
        # build is no slower than before.
        #
        # The news call that used to sit here is GONE. It cost one FMP call per build for
        # a field iOS never reads — `toNewsArticles()` is defined and called nowhere, and
        # both ViewModels load news from `GET /etfs/{symbol}/news` instead.
        # `news_articles` stays in the response as its schema default `[]`, because the
        # Swift DTO is non-optional and omitting the key fails the WHOLE screen's decode.
        quote, fundamentals, chart_data, related_etfs = await asyncio.gather(
            self._get_quote(symbol),
            self._get_fundamentals(symbol),
            self._get_chart(symbol, chart_range, interval),
            self._get_related(symbol),
        )

        profile = fundamentals.get("profile") or {}
        etf_info = fundamentals.get("etf_info") or {}
        holders = fundamentals.get("holders") or []
        sector_weights = fundamentals.get("sector_weights") or []
        # The detail card shows a short table; the dedicated endpoint wants all 100.
        dividends = (fundamentals.get("dividends") or [])[:20]
        news_raw: List[Dict[str, Any]] = []

        # NOTE: the raw histories are deliberately NOT bound here. Everything that needed
        # them (chart bars, performance periods, benchmark CAGR) now reads a cached
        # section instead, and a local holding ~1 MB that nothing reads is the same dead
        # assignment `index_service` carried for months.

        # ── Step 2: Extract quote data ────────────────────────────
        # _finite_num (not raw float) — FMP can emit a NaN/Infinity JSON token for
        # a computed field; float("nan") succeeds and, forwarded into a REQUIRED
        # response float, makes Starlette (allow_nan=False) 500 the whole ETF detail.
        price = _finite_num(quote.get("price"))
        change = _finite_num(quote.get("change"))
        change_pct = _finite_num(quote.get("changePercentage") or quote.get("changesPercentage"))
        prev_close = _finite_num(quote.get("previousClose"))
        # Safety net: compute from change/previousClose if FMP didn't return percentage
        if not change_pct and change and prev_close > 0:
            change_pct = round((change / prev_close) * 100, 4)
        volume = quote.get("volume") or 0
        avg_volume = (
            quote.get("avgVolume")
            or profile.get("averageVolume")
            or etf_info.get("avgVolume")
            or 0
        )
        year_high = _finite_num(quote.get("yearHigh"))
        year_low = _finite_num(quote.get("yearLow"))
        price_avg_50 = _finite_num(quote.get("priceAvg50"))
        market_cap = _finite_num(quote.get("marketCap") or profile.get("marketCap"))
        beta = _finite_num(profile.get("beta") or quote.get("beta"))

        # ETF-specific data (etf_info may be empty if FMP plan doesn't include it)
        # Fall back to static reference table for popular ETFs
        ref = _ETF_REFERENCE.get(symbol, {})

        expense_ratio = _finite_num(etf_info.get("expenseRatio") or ref.get("expense_ratio"))
        nav = _finite_num(etf_info.get("navPrice") or etf_info.get("nav"), default=price)
        total_assets = _finite_num(
            etf_info.get("assetsUnderManagement") or etf_info.get("totalAssets")
            or etf_info.get("aum") or etf_info.get("netAssets") or market_cap
        )
        holdings_count = int(
            etf_info.get("holdingsCount") or etf_info.get("numberOfHoldings")
            or ref.get("holdings") or 0
        )
        etf_company = (
            etf_info.get("etfCompany") or etf_info.get("companyName")
            or profile.get("companyName") or "—"
        )
        asset_class = etf_info.get("assetClass") or "Equity"
        inception_date_raw = (
            etf_info.get("inceptionDate") or profile.get("ipoDate") or ""
        )
        domicile = etf_info.get("domicile") or "United States"
        index_tracked = (
            etf_info.get("indexTracked") or etf_info.get("index")
            or ref.get("index") or "—"
        )
        website = etf_info.get("website") or profile.get("website") or ""
        if website.startswith("https://"):
            website = website[8:]
        elif website.startswith("http://"):
            website = website[7:]
        description = etf_info.get("description") or profile.get("description") or ""
        turnover = _finite_num(etf_info.get("turnover") or ref.get("turnover"))

        # Dividend yield: prefer etf_info, then compute from lastDividend / price
        last_div_dollar = _finite_num(profile.get("lastDividend") or profile.get("lastDiv"))
        dividend_yield = _finite_num(etf_info.get("dividendYield") or quote.get("dividendYield"))
        if not dividend_yield and last_div_dollar > 0 and price > 0:
            dividend_yield = round((last_div_dollar / price) * 100, 2)

        # Step 3 (build chart data) is gone: `chart_data` now arrives from `_get_chart`
        # in step 1, which derives 5Y/ALL from the shared history instead of re-fetching
        # it. The old branch called `fetch_chart_data` for 1D/1W/5Y/ALL, and for ALL that
        # was `_fetch_all_daily` — up to 5 paged calls — on EVERY request.

        # ── Step 4: Build key statistics ──────────────────────────
        key_statistics, key_statistics_groups = self._build_key_statistics(
            nav=nav,
            total_assets=total_assets,
            expense_ratio=expense_ratio,
            avg_volume=avg_volume,
            dividend_yield=dividend_yield,
            year_high=year_high,
            year_low=year_low,
            beta=beta,
            price_avg_50=price_avg_50,
            holdings_count=holdings_count,
            turnover=turnover,
            inception_date=inception_date_raw,
            asset_class=asset_class,
            domicile=domicile,
            index_tracked=index_tracked,
        )

        # ── Step 5: Performance periods + benchmark (vs S&P 500) ───
        # Both are pure functions of the two histories, so they live in the 12h `derived`
        # section and survive a redeploy without re-pulling the 1.1 MB. Re-validated
        # rather than trusted: a Tier-2 row is JSON that a previous schema wrote.
        derived = await self._get_derived(symbol, index_tracked=index_tracked)
        perf_periods = _revalidate_rows(
            PerformancePeriodResponse, derived.get("performance_periods"), symbol, "performance"
        )

        # ── Step 6: Build holdings & sector data ──────────────────
        top_holdings = self._build_top_holdings(holders)
        top_sectors = self._build_sector_weights(sector_weights)
        concentration = self._build_concentration(top_holdings)

        # ── Step 7: Build dividend data ───────────────────────────
        dividend_payments = self._build_dividend_history(dividends)

        # ── Step 8: Build snapshots (FMP data + Gemini for hook text) ──
        identity_rating = self._build_identity_rating(
            total_assets=total_assets,
            beta=beta,
            expense_ratio=expense_ratio,
            inception_date=inception_date_raw,
            holdings_count=holdings_count,
        )
        strategy = await self._build_strategy(
            symbol=symbol,
            name=etf_company,
            description=description,
            asset_class=asset_class,
            index_tracked=index_tracked,
            holdings_count=holdings_count,
            top_holdings=top_holdings,
            top_sectors=top_sectors,
        )

        # ── Step 9: Build net yield ───────────────────────────────
        fee_per_10k = expense_ratio * 100  # expense_ratio is in %, so 0.0945% → $9.45
        yield_per_10k = dividend_yield * 100  # 1.22% → $122

        # Honest net-yield verdict. Note: expense_ratio == 0 here means the value
        # is UNAVAILABLE (FMP didn't return it and there's no reference entry),
        # NOT a genuinely free fund — never claim "$0 fees" / "charges nothing".
        if expense_ratio <= 0:
            fee_context = "Expense ratio unavailable for this fund."
            net_yield_verdict = "We couldn't confirm this fund's fees."
        elif dividend_yield <= 0:
            fee_context = f"You pay ${fee_per_10k:.2f} per year on a $10,000 investment."
            net_yield_verdict = "This fund doesn't currently pay a dividend — you only pay its fees."
        else:
            fee_context = f"You pay ${fee_per_10k:.2f} per year on a $10,000 investment."
            ratio = dividend_yield / expense_ratio
            if ratio >= 1.05:
                net_yield_verdict = f"This fund pays you {ratio:.1f}x more in dividends than it charges in fees."
            elif ratio >= 0.95:
                net_yield_verdict = "This fund's dividend yield roughly matches its expense ratio."
            else:
                net_yield_verdict = (
                    f"This fund's {expense_ratio:.2f}% fee is higher than its "
                    f"{dividend_yield:.2f}% dividend yield."
                )

        if not dividend_payments:
            last_payment = ETFDividendPaymentResponse(
                dividend_per_share="—",
                ex_dividend_date="—",
                pay_date="—",
            )
        else:
            last_payment = dividend_payments[0]

        pay_frequency = self._infer_pay_frequency(dividends)

        net_yield = ETFNetYieldResponse(
            expense_ratio=expense_ratio,
            fee_context=fee_context,
            dividend_yield=dividend_yield,
            pay_frequency=pay_frequency,
            yield_context=f"You earn ~${yield_per_10k:.0f} per year on a $10,000 investment.",
            verdict=net_yield_verdict,
            last_dividend_payment=last_payment,
            dividend_history=dividend_payments,
        )

        # Step 10 (related ETFs) now arrives from `_get_related` in step 1 — it used to
        # run its peers lookup and batch quote on every range pill.

        # ── Step 11: News ─────────────────────────────────────────
        # `news_raw` is permanently `[]`: the FMP news call was dropped from the fan-out
        # because iOS never reads this field. The key still ships (schema default `[]`)
        # because the Swift DTO is non-optional and omitting it fails the whole decode.
        news_articles = self._build_news(news_raw if isinstance(news_raw, list) else [])

        # ── Step 12: Build profile ────────────────────────────────
        inception_display = _format_date_readable(inception_date_raw)

        etf_profile = ETFProfileResponse(
            description=description,
            symbol=symbol,
            etf_company=etf_company,
            asset_class=asset_class,
            inception_date=inception_display,
            domicile=domicile,
            index_tracked=index_tracked,
            website=website,
        )

        # ── Step 13: Asset allocation (inferred) ──────────────────
        asset_alloc = self._infer_asset_allocation(
            asset_class=asset_class,
            total_assets=total_assets,
        )

        holdings_risk = ETFHoldingsRiskResponse(
            asset_allocation=asset_alloc,
            top_sectors=top_sectors[:5],
            top_holdings=top_holdings[:10],
            concentration=concentration,
        )

        # ── Step 14: Benchmark summary (proper CAGR) ────────────────
        # Read from the same `derived` bundle built in step 5. It is a function of the two
        # histories alone — no live price — which is why it is safe to persist.
        _bench = _revalidate_rows(
            BenchmarkSummaryResponse, [derived.get("benchmark_summary")], symbol, "benchmark"
        )
        benchmark = _bench[0] if _bench else None

        # ── Assemble response ─────────────────────────────────────
        response = ETFDetailResponse(
            symbol=symbol,
            name=profile.get("companyName") or etf_company,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
            market_status=_get_market_status(),
            chart_data=chart_data,
            key_statistics=key_statistics,
            key_statistics_groups=key_statistics_groups,
            performance_periods=perf_periods,
            identity_rating=identity_rating,
            strategy=strategy,
            net_yield=net_yield,
            holdings_risk=holdings_risk,
            etf_profile=etf_profile,
            related_etfs=related_etfs,
            benchmark_summary=benchmark,
            news_articles=news_articles,
        )

        # Caching of the ASSEMBLED response (and its degraded-build gate) belongs to
        # `get_etf_detail`, which owns the in-flight future. The old Supabase upsert of the
        # whole payload is gone: a 24-hour row carrying `current_price` is precisely what
        # made `_refresh_volatile` necessary. Every expensive section is persisted
        # individually now, and not one of them can contain a price.
        return response

    # ── Unified Snapshot Cache (etf_snapshot_cache) ────────────────
    # Single table with (symbol, category) unique constraint.
    # Categories: "dividend_history", "holdings_risk", etc.

    _SNAPSHOT_DB_TTL_HOURS = 24
    _SNAPSHOT_MEM_TTL = 3600  # 1 hour in-memory

    def _check_snapshot_cache(self, symbol: str, category: str) -> Optional[Dict[str, Any]]:
        """Check Supabase etf_snapshot_cache (24h TTL)."""
        try:
            row = (
                self.supabase.table("etf_snapshot_cache")
                .select("response_json, cached_at")
                .eq("symbol", symbol)
                .eq("category", category)
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
            if age > timedelta(hours=self._SNAPSHOT_DB_TTL_HOURS):
                logger.info(f"ETF snapshot STALE ({category}, age={age}) for {symbol}")
                return None
            data = entry.get("response_json")
            if data and isinstance(data, dict):
                logger.info(f"ETF snapshot HIT ({category}, age={age}) for {symbol}")
                return data
            return None
        except Exception as e:
            logger.warning(f"ETF snapshot check failed ({category}) for {symbol}: {e}")
            return None

    def _upsert_snapshot_cache(self, symbol: str, category: str, data: Dict[str, Any]) -> None:
        """Upsert into etf_snapshot_cache."""
        try:
            self.supabase.table("etf_snapshot_cache").upsert(
                {
                    "symbol": symbol,
                    "category": category,
                    "response_json": data,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="symbol,category",
            ).execute()
            logger.info(f"ETF snapshot cached ({category}) for {symbol}")
        except Exception as e:
            logger.warning(f"ETF snapshot upsert failed ({category}) for {symbol}: {e}")

    # ── Dividend History (dedicated endpoint) ─────────────────────

    async def get_dividend_history(self, symbol: str) -> ETFDividendHistoryResponse:
        """
        Fetch full dividend history for an ETF.
        Two-tier cache: in-memory (1h) + Supabase etf_snapshot_cache (24h).
        """
        symbol = symbol.upper()
        category = "dividend_history"

        # ── Cache check: in-memory then Supabase ────────────────
        mem_key = f"etf_{category}_{symbol}"
        cached = _cache_get(mem_key, self._SNAPSHOT_MEM_TTL)
        if cached is not None:
            logger.info(f"Dividend in-memory HIT for {symbol}")
            return cached

        db_data = self._check_snapshot_cache(symbol, category)
        if db_data is not None:
            try:
                response = ETFDividendHistoryResponse(**db_data)
                _cache_set(mem_key, response)
                return response
            except Exception as e:
                logger.warning(f"Dividend snapshot data invalid for {symbol}: {e}")

        # ── Read the SHARED fundamentals section ────────────────
        # These are the same FMP calls the detail fan-out makes. Going through
        # `_get_fundamentals` means opening the ETF screen and then this endpoint costs
        # ZERO extra FMP calls, instead of re-issuing the identical requests.
        # The shared section fetches limit=100 precisely so THIS endpoint can use it —
        # the detail card only renders 20, but truncating the section would silently cost
        # this screen 80 rows.
        raw_dividends = (await self._get_fundamentals(symbol)).get("dividends") or []
        if not raw_dividends:
            logger.warning(f"No dividend data from FMP for {symbol}")
            return ETFDividendHistoryResponse(
                symbol=symbol,
                pay_frequency="—",
                total_dividends=0,
                dividends=[],
            )

        # Use FMP's frequency field directly (first non-empty value)
        pay_frequency = "—"
        for d in raw_dividends:
            freq = d.get("frequency")
            if freq and freq != "—":
                pay_frequency = freq
                break

        # Format each dividend payment
        dividends = []
        for d in raw_dividends:
            div_amount = d.get("dividend") or d.get("adjDividend") or d.get("amount") or 0
            ex_date = d.get("date") or d.get("recordDate") or ""
            pay_date = d.get("paymentDate") or d.get("payDate") or ""

            dividends.append(ETFDividendPaymentResponse(
                dividend_per_share=f"${float(div_amount):.4f}" if div_amount else "—",
                ex_dividend_date=_format_date_readable(ex_date),
                pay_date=_format_date_readable(pay_date),
            ))

        response = ETFDividendHistoryResponse(
            symbol=symbol,
            pay_frequency=pay_frequency,
            total_dividends=len(dividends),
            dividends=dividends,
        )

        # ── Cache in both tiers ─────────────────────────────────
        _cache_set(mem_key, response)
        try:
            self._upsert_snapshot_cache(symbol, category, response.model_dump())
        except Exception as e:
            logger.warning(f"Dividend snapshot cache failed for {symbol}: {e}")

        return response

    # ── ETF Profile (dedicated endpoint) ───────────────────────────

    async def get_profile(self, symbol: str) -> ETFProfileResponse:
        """
        Fetch ETF profile data via dedicated endpoint.
        Two-tier cache: in-memory (1h) + Supabase etf_snapshot_cache (24h).
        """
        symbol = symbol.upper()
        category = "profile"

        # ── Cache check ─────────────────────────────────────────
        mem_key = f"etf_{category}_{symbol}"
        cached = _cache_get(mem_key, self._SNAPSHOT_MEM_TTL)
        if cached is not None:
            logger.info(f"Profile in-memory HIT for {symbol}")
            return cached

        db_data = self._check_snapshot_cache(symbol, category)
        if db_data is not None:
            try:
                response = ETFProfileResponse(**db_data)
                _cache_set(mem_key, response)
                return response
            except Exception as e:
                logger.warning(f"Profile snapshot data invalid for {symbol}: {e}")

        # ── Read the SHARED fundamentals section ────────────────
        # These are the same FMP calls the detail fan-out makes. Going through
        # `_get_fundamentals` means opening the ETF screen and then this endpoint costs
        # ZERO extra FMP calls, instead of re-issuing the identical requests.
        # `profile` here is the price-free PROJECTION the section stores; every field this
        # builder reads (description / companyName / ipoDate / website) is in it.
        _fund = await self._get_fundamentals(symbol)
        etf_info = _fund.get("etf_info") or {}
        profile = _fund.get("profile") or {}

        # ── Build profile ───────────────────────────────────────
        description = etf_info.get("description") or profile.get("description") or ""
        etf_company = (
            etf_info.get("etfCompany") or etf_info.get("companyName")
            or profile.get("companyName") or "—"
        )
        asset_class = etf_info.get("assetClass") or "Equity"
        inception_date_raw = etf_info.get("inceptionDate") or profile.get("ipoDate") or ""
        domicile = etf_info.get("domicile") or "United States"
        ref = _ETF_REFERENCE.get(symbol, {})
        index_tracked = (
            etf_info.get("indexTracked") or etf_info.get("index")
            or ref.get("index") or "—"
        )
        website = etf_info.get("website") or profile.get("website") or ""
        if website.startswith("https://"):
            website = website[8:]
        elif website.startswith("http://"):
            website = website[7:]

        response = ETFProfileResponse(
            description=description,
            symbol=symbol,
            etf_company=etf_company,
            asset_class=asset_class,
            inception_date=_format_date_readable(inception_date_raw),
            domicile=domicile,
            index_tracked=index_tracked,
            website=website,
        )

        # ── Cache in both tiers ─────────────────────────────────
        _cache_set(mem_key, response)
        try:
            self._upsert_snapshot_cache(symbol, category, response.model_dump())
        except Exception as e:
            logger.warning(f"Profile snapshot cache failed for {symbol}: {e}")

        return response

    # ── Holdings & Risk (dedicated endpoint) ─────────────────────

    async def get_holdings_risk(self, symbol: str) -> ETFHoldingsRiskResponse:
        """
        Fetch holdings & risk data for an ETF via dedicated endpoint.

        Data sources (2 parallel FMP calls):
          - etf/info → sectorsList (exposure), assetClass, AUM
          - etf/holdings → top holdings with weightPercentage

        Math:
          - Asset allocation: extracts "Cash & Others" from sectorsList for real cash %
          - Sectors: top 5 from sectorsList sorted by exposure desc
          - Holdings: top 10 from etf/holdings
          - Concentration: sum of top-10 weights with insight text
        """
        symbol = symbol.upper()
        category = "holdings_risk"

        # ── Cache check: in-memory then Supabase ────────────────
        mem_key = f"etf_{category}_{symbol}"
        cached = _cache_get(mem_key, self._SNAPSHOT_MEM_TTL)
        if cached is not None:
            logger.info(f"HoldingsRisk in-memory HIT for {symbol}")
            return cached

        db_data = self._check_snapshot_cache(symbol, category)
        if db_data is not None:
            try:
                response = ETFHoldingsRiskResponse(**db_data)
                _cache_set(mem_key, response)
                return response
            except Exception as e:
                logger.warning(f"HoldingsRisk snapshot data invalid for {symbol}: {e}")

        # ── Read the SHARED fundamentals section ────────────────
        # These are the same FMP calls the detail fan-out makes. Going through
        # `_get_fundamentals` means opening the ETF screen and then this endpoint costs
        # ZERO extra FMP calls, instead of re-issuing the identical requests.
        _fund = await self._get_fundamentals(symbol)
        etf_info = _fund.get("etf_info") or {}
        holders = _fund.get("holders") or []

        # ── Build sectors from sectorsList ───────────────────────
        sectors_list = etf_info.get("sectorsList") or []
        top_sectors = self._build_sectors_from_info(sectors_list)

        # ── Build top holdings ──────────────────────────────────
        top_holdings = self._build_top_holdings(holders if isinstance(holders, list) else [])

        # ── Build concentration ─────────────────────────────────
        concentration = self._build_concentration(top_holdings)

        # ── Build asset allocation (uses real cash from sectorsList) ──
        asset_class = etf_info.get("assetClass") or "Equity"
        total_assets = float(
            etf_info.get("assetsUnderManagement")
            or etf_info.get("totalAssets")
            or etf_info.get("aum")
            or etf_info.get("netAssets")
            or 0
        )
        asset_alloc = self._build_asset_allocation(
            sectors_list=sectors_list,
            asset_class=asset_class,
            total_assets=total_assets,
        )

        response = ETFHoldingsRiskResponse(
            asset_allocation=asset_alloc,
            top_sectors=top_sectors[:5],
            top_holdings=top_holdings[:10],
            concentration=concentration,
        )

        # ── Cache in both tiers ─────────────────────────────────
        _cache_set(mem_key, response)
        try:
            self._upsert_snapshot_cache(symbol, category, response.model_dump())
        except Exception as e:
            logger.warning(f"HoldingsRisk snapshot cache failed for {symbol}: {e}")

        return response

    def _build_sectors_from_info(
        self, sectors_list: List[Dict]
    ) -> List[ETFSectorWeightResponse]:
        """Build sector weights from etf/info sectorsList field.

        sectorsList uses 'industry' and 'exposure' keys (vs 'sector'/'weightPercentage'
        from the separate etf/sector-weightings endpoint). Both return the same data.
        """
        results = []
        for s in sectors_list:
            name = s.get("industry") or s.get("sector") or s.get("name") or "—"
            weight = s.get("exposure") or s.get("weightPercentage") or s.get("weight") or 0
            if isinstance(weight, str):
                try:
                    weight = float(weight.replace("%", ""))
                except (ValueError, TypeError):
                    weight = 0

            # Skip "Cash & Others" from sector display (used in asset allocation instead)
            if "cash" in name.lower() and "other" in name.lower():
                continue

            results.append(ETFSectorWeightResponse(
                name=name,
                weight=round(_finite_num(weight), 2),
            ))

        results.sort(key=lambda x: x.weight, reverse=True)
        return results

    def _build_asset_allocation(
        self, *, sectors_list: List[Dict], asset_class: str, total_assets: float,
    ) -> ETFAssetAllocationResponse:
        """Build asset allocation using real cash % from FMP sectorsList.

        FMP's sectorsList includes a "Cash & Others" entry with the actual
        cash allocation percentage. For the rest, we infer from asset_class.

        Edge case: For bond ETFs, FMP often reports sectorsList as
        [{"industry": "Cash & Others", "exposure": 100}] because it can't
        break down bond sectors. In that case, 100% is actually bonds, not cash.
        We detect this by checking if "Cash & Others" is the ONLY sector AND
        the asset class indicates bonds.
        """
        ac = asset_class.lower()
        is_bond_etf = "bond" in ac or "fixed" in ac or "income" in ac
        # Physical-commodity / gold / alternative funds are the OTHER case FMP can't
        # decompose: it lumps the whole fund into "Cash & Others". Without this a
        # 100%-Cash gold ETF renders as 100% cash in the donut (remaining=0 → the
        # commodities branch below gets nothing), diverging from the _infer path.
        is_commodity_etf = "commodity" in ac or "gold" in ac or "alternative" in ac

        # Extract real cash % from sectorsList
        cash_pct = 0.0
        has_only_cash_sector = False
        for s in sectors_list:
            name = (s.get("industry") or s.get("sector") or "").lower()
            if "cash" in name and "other" in name:
                # Mirror _build_sectors_from_info: FMP exposure can be a '%'-suffixed
                # string OR a non-finite token. A bare float() would ValueError on
                # "3.5%" (→500) or pass NaN into the REQUIRED equities/cash floats
                # (→ allow_nan=False 500). Strip '%' then coerce through _finite_num.
                _raw_cash = s.get("exposure") or s.get("weightPercentage") or 0
                if isinstance(_raw_cash, str):
                    _raw_cash = _raw_cash.replace("%", "").strip()
                raw_cash = round(_finite_num(_raw_cash), 2)
                # If "Cash & Others" is ~100% AND FMP can't break this fund down
                # (bond OR commodity/gold/alternative), the 100% is really the
                # underlying asset, not cash — keep only a token operational cash so
                # `remaining` flows into the correct bucket below.
                if raw_cash >= 95 and (is_bond_etf or is_commodity_etf):
                    has_only_cash_sector = True
                    cash_pct = 5.0  # Typical operational cash
                else:
                    cash_pct = raw_cash
                break

        # Determine primary allocation from asset class
        remaining = round(100.0 - cash_pct, 2)

        commodities = 0.0
        if is_bond_etf:
            equities, bonds, crypto = 0.0, remaining, 0.0
        elif "crypto" in ac or "bitcoin" in ac or "digital" in ac:
            equities, bonds, crypto = 0.0, 0.0, remaining
        elif "commodity" in ac or "gold" in ac or "alternative" in ac:
            # Commodities are neither equity nor cash; a gold ETF shown as
            # "equities" (or the sibling _infer path's "100% cash") corrupts the
            # allocation donut. Route into the dedicated commodities bucket.
            equities, bonds, crypto, commodities = 0.0, 0.0, 0.0, remaining
        else:
            # Default: equity
            equities, bonds, crypto = remaining, 0.0, 0.0

        return ETFAssetAllocationResponse(
            equities=equities,
            bonds=bonds,
            crypto=crypto,
            commodities=commodities,
            cash=cash_pct,
            total_assets=_fmt(total_assets),
        )

    # ── Chart helpers ─────────────────────────────────────────────

    def _extract_chart_data(
        self, historical: List[Dict], chart_range: str
    ) -> List[Dict]:
        if not historical:
            return []

        from app.services.chart_helper import _finite_or_none, daily_range_days

        # The visible window PLUS the MA(200) warm-up, from the one shared definition.
        # This service used to carry its own copy of the range map with NO warm-up in it,
        # so the client's `TickerChartView.warmupCount` resolved to 0 and the moving
        # average never drew on a daily chart. index_service and stock_overview_service
        # always had the warm-up; these three never did.
        today = datetime.now(tz=timezone.utc).date()
        cutoff = (today - timedelta(days=daily_range_days(chart_range))).isoformat()

        result = []
        for p in historical:
            date = p.get("date")
            # `None >= cutoff` raises TypeError; guard before comparing.
            if not date or date < cutoff:
                continue
            # A NaN close survives `close <= 0` (nan comparisons are False) and a
            # non-finite open/high/low/volume forwarded raw serialize as an invalid
            # JSON `NaN`/`Infinity` token — Starlette (allow_nan=False) then 500s
            # the WHOLE ETF detail. Route every OHLCV value through _finite_or_none.
            close = _finite_or_none(p.get("close") or p.get("adjClose"))
            if close is None or close <= 0:
                continue
            result.append({
                "date": date,
                "open": _finite_or_none(p.get("open")),
                "high": _finite_or_none(p.get("high")),
                "low": _finite_or_none(p.get("low")),
                "close": round(close, 2),
                "volume": _finite_or_none(p.get("volume")),
            })
        return result

    # ── Key statistics builder ────────────────────────────────────

    def _build_key_statistics(
        self, *, nav, total_assets, expense_ratio, avg_volume,
        dividend_yield, year_high, year_low, beta, price_avg_50,
        holdings_count, turnover, inception_date, asset_class,
        domicile, index_tracked,
    ) -> Tuple[List[KeyStatisticItem], List[KeyStatisticsGroupResponse]]:
        """Build both flat and grouped key statistics."""

        flat = [
            KeyStatisticItem(label="NAV", value=_fmt_dollar(nav)),
            KeyStatisticItem(label="Total Assets", value=_fmt(total_assets)),
            KeyStatisticItem(
                label="Expense Ratio",
                value=f"{expense_ratio}%" if expense_ratio else "—",
                is_highlighted=True,
            ),
            KeyStatisticItem(label="Avg. Volume", value=_fmt(avg_volume, 0, prefix="")),
            KeyStatisticItem(label="Dividend Yield", value=_pct(dividend_yield) if dividend_yield else "—"),
            KeyStatisticItem(label="52W High", value=_fmt_dollar(year_high)),
            KeyStatisticItem(label="52W Low", value=_fmt_dollar(year_low)),
            KeyStatisticItem(label="Beta", value=f"{beta:.2f}" if beta else "—"),
            KeyStatisticItem(label="50-Day Avg", value=_fmt_dollar(price_avg_50) if price_avg_50 else "—"),
            KeyStatisticItem(label="Holdings", value=str(holdings_count) if holdings_count else "—"),
            KeyStatisticItem(label="Turnover", value=_pct(turnover) if turnover else "—"),
            KeyStatisticItem(label="Inception", value=_format_date_readable(inception_date)),
        ]

        groups = [
            # Column 1: Price & NAV
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="NAV", value=_fmt_dollar(nav)),
                KeyStatisticItem(label="52W High", value=_fmt_dollar(year_high)),
                KeyStatisticItem(label="52W Low", value=_fmt_dollar(year_low)),
                KeyStatisticItem(label="Avg. Volume", value=_fmt(avg_volume, 0, prefix="")),
                KeyStatisticItem(label="Beta", value=f"{beta:.2f}" if beta else "—"),
            ]),
            # Column 2: Fund Details
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="Total Assets", value=_fmt(total_assets)),
                KeyStatisticItem(
                    label="Expense Ratio",
                    value=f"{expense_ratio}%" if expense_ratio else "—",
                    is_highlighted=True,
                ),
                KeyStatisticItem(label="Dividend Yield", value=_pct(dividend_yield) if dividend_yield else "—"),
                KeyStatisticItem(label="50-Day Avg", value=_fmt_dollar(price_avg_50) if price_avg_50 else "—"),
                KeyStatisticItem(label="Turnover", value=_pct(turnover) if turnover else "—"),
            ]),
            # Column 3: Structure
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="Holdings", value=str(holdings_count) if holdings_count else "—"),
                KeyStatisticItem(label="Inception", value=_format_date_readable(inception_date)),
                KeyStatisticItem(label="Asset Class", value=asset_class),
                KeyStatisticItem(label="Domicile", value=domicile),
                KeyStatisticItem(label="Index", value=index_tracked),
            ]),
        ]

        return flat, groups

    # ── Performance periods builder (with S&P 500 comparison) ─────

    def _build_performance_periods(
        self, etf_hist: List[Dict], spy_hist: List[Dict]
    ) -> List[PerformancePeriodResponse]:
        """Build performance periods with real S&P 500 comparison.
        Follows same pattern as stock_overview_service._build_performance_periods."""
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
                etf_ret = _compute_ytd_return(etf_hist)
                sp_ret = _compute_ytd_return(spy_hist)
            else:
                etf_ret = _compute_return(etf_hist, days)
                sp_ret = _compute_return(spy_hist, days)

            if etf_ret is not None:
                vs_market = round(etf_ret - (sp_ret or 0), 2) if sp_ret is not None else None
                periods.append(PerformancePeriodResponse(
                    label=label,
                    change_percent=round(etf_ret, 2),
                    vs_market_percent=vs_market,
                    sp_return_percent=round(sp_ret, 2) if sp_ret is not None else None,
                ))
        return periods

    # ── Benchmark summary builder (proper CAGR) ──────────────────

    def _build_benchmark_summary(
        self, etf_hist: List[Dict], spy_hist: List[Dict],
        *, symbol: str = "", index_tracked: str = "",
    ) -> Optional[BenchmarkSummaryResponse]:
        """Compute annualized (CAGR) returns since inception for ETF vs S&P 500.

        Each uses its OWN full history independently:
          - ETF CAGR: from ETF's first available date to today
          - S&P CAGR: from S&P's first available date to today
        The "Since" dates will differ (e.g. SPY since 2006, S&P since 1993).
        """
        if not etf_hist or len(etf_hist) < 252:
            return None

        # ── ETF: CAGR from its own first available date ──────────
        etf_days = len(etf_hist) - 1
        etf_years = etf_days / 252

        # Finite-guard: a NaN/Inf close (bare NaN/Infinity FMP JSON token) is truthy
        # and slips past `not x` / `x <= 0`, producing a NaN CAGR in the REQUIRED
        # avg_annual_return float → Starlette allow_nan=False 500s the ENTIRE ETF
        # detail (and iOS falls back to sample data). _finite_num returns 0.0 for
        # non-finite, which the existing guard rejects. Mirrors _compute_return.
        etf_start = _finite_num(etf_hist[0].get("close") or etf_hist[0].get("adjClose"))
        etf_end = _finite_num(etf_hist[-1].get("close") or etf_hist[-1].get("adjClose"))
        etf_start_date = etf_hist[0].get("date") or ""

        if not etf_start or not etf_end or etf_start <= 0 or etf_years <= 0:
            return None

        etf_annual = ((etf_end / etf_start) ** (1 / etf_years) - 1) * 100

        # ── S&P 500: CAGR from its own first available date ─────
        sp_annual = 0.0
        sp_start_date = ""
        if spy_hist and len(spy_hist) >= 252:
            sp_start_price = _finite_num(spy_hist[0].get("close") or spy_hist[0].get("adjClose"))
            sp_end_price = _finite_num(spy_hist[-1].get("close") or spy_hist[-1].get("adjClose"))
            sp_start_date = spy_hist[0].get("date") or ""
            sp_days = len(spy_hist) - 1
            sp_years = sp_days / 252

            if sp_start_price and sp_end_price and sp_start_price > 0 and sp_years > 0:
                sp_annual = ((sp_end_price / sp_start_price) ** (1 / sp_years) - 1) * 100

        return BenchmarkSummaryResponse(
            avg_annual_return=round(etf_annual, 1),
            sp_benchmark=round(sp_annual, 1),
            benchmark_name="S&P 500",
            since_date=_format_date_readable(etf_start_date),
            benchmark_since_date=None,
            badge_threshold=0.0,
        )

    # ── Holdings builder ──────────────────────────────────────────

    def _build_top_holdings(
        self, holders: List[Dict]
    ) -> List[ETFTopHoldingResponse]:
        results = []
        for h in holders[:10]:
            weight = h.get("weightPercentage") or h.get("weight") or 0
            if isinstance(weight, str):
                try:
                    weight = float(weight.replace("%", ""))
                except (ValueError, TypeError):
                    weight = 0
            results.append(ETFTopHoldingResponse(
                symbol=h.get("asset") or h.get("symbol") or "—",
                name=h.get("name") or h.get("companyName") or "—",
                weight=round(_finite_num(weight), 2),
            ))
        return results

    # ── Sector weights builder ────────────────────────────────────

    def _build_sector_weights(
        self, sector_raw: List[Dict]
    ) -> List[ETFSectorWeightResponse]:
        results = []
        for s in sector_raw:
            weight = s.get("weightPercentage") or s.get("weight") or "0"
            if isinstance(weight, str):
                try:
                    weight = float(weight.replace("%", ""))
                except (ValueError, TypeError):
                    weight = 0
            sector_name = s.get("sector") or s.get("name") or "—"
            results.append(ETFSectorWeightResponse(
                name=sector_name,
                weight=round(_finite_num(weight), 2),
            ))
        # Sort largest first
        results.sort(key=lambda x: x.weight, reverse=True)
        return results

    # ── Concentration builder ─────────────────────────────────────

    def _build_concentration(
        self, top_holdings: List[ETFTopHoldingResponse]
    ) -> ETFConcentrationResponse:
        top_10 = top_holdings[:10]
        total_weight = sum(h.weight for h in top_10)
        n = len(top_10)

        # No holdings data → report honestly instead of mislabeling an unknown
        # fund as "well diversified" (green / low-risk) off a 0% total weight.
        if n == 0:
            return ETFConcentrationResponse(
                top_n=0,
                weight=0.0,
                insight="Holdings data isn't available for this fund yet.",
            )

        # Boundaries aligned with Swift ETFConcentrationLevel:
        #   < 20% → low (Well Diversified)
        #   20-35% → moderate (Moderate)
        #   >= 35% → high (Concentrated)
        if total_weight >= 35:
            insight = (
                f"Over a third of your money is in just {n} companies. "
                "If these big names stumble, this fund feels it."
            )
        elif total_weight >= 20:
            insight = (
                f"The top {n} holdings make up {total_weight:.0f}% — "
                "moderate concentration with reasonable diversification."
            )
        else:
            insight = (
                f"Only {total_weight:.0f}% in the top {n} holdings — "
                "this fund is well diversified across many companies."
            )

        return ETFConcentrationResponse(
            top_n=n,
            weight=round(total_weight, 1),
            insight=insight,
        )

    # ── Dividend history builder ──────────────────────────────────

    def _build_dividend_history(
        self, dividends: List[Dict]
    ) -> List[ETFDividendPaymentResponse]:
        results = []
        for d in dividends:
            div_amount = d.get("dividend") or d.get("adjDividend") or d.get("amount") or 0
            ex_date = d.get("date") or d.get("recordDate") or ""
            pay_date = d.get("paymentDate") or d.get("payDate") or ""

            results.append(ETFDividendPaymentResponse(
                dividend_per_share=f"${float(div_amount):.4f}" if div_amount else "—",
                ex_dividend_date=_format_date_readable(ex_date),
                pay_date=_format_date_readable(pay_date),
            ))
        return results

    # ── Pay frequency inference ───────────────────────────────────

    def _infer_pay_frequency(self, dividends: List[Dict]) -> str:
        """Infer dividend pay frequency from payment dates."""
        if not dividends or len(dividends) < 2:
            return "—"

        dates = []
        for d in dividends[:8]:
            date_str = d.get("date") or d.get("recordDate") or ""
            try:
                dates.append(datetime.strptime(date_str, "%Y-%m-%d"))
            except (ValueError, TypeError):
                continue

        if len(dates) < 2:
            return "—"

        # Compute average gap between payments
        gaps = []
        for i in range(1, len(dates)):
            gaps.append(abs((dates[i - 1] - dates[i]).days))

        avg_gap = sum(gaps) / len(gaps) if gaps else 365

        if avg_gap < 45:
            return "Monthly"
        elif avg_gap < 120:
            return "Quarterly"
        elif avg_gap < 240:
            return "Semi-Annually"
        else:
            return "Annually"

    # ── Asset allocation inference ────────────────────────────────

    def _infer_asset_allocation(
        self, *, asset_class: str, total_assets: float,
    ) -> ETFAssetAllocationResponse:
        """Infer asset allocation from asset class (FMP doesn't provide granular breakdown)."""
        ac = asset_class.lower()
        commodities = 0.0
        if "bond" in ac or "fixed" in ac:
            equities, bonds, crypto, cash = 0, 95, 0, 5
        elif "crypto" in ac or "bitcoin" in ac:
            equities, bonds, crypto, cash = 0, 0, 95, 5
        elif "commodity" in ac or "gold" in ac or "alternative" in ac:
            # Was "0,0,0,100" (a gold ETF shown as 100% cash). Route into the
            # dedicated commodities bucket, matching _build_asset_allocation so the
            # detail screen and /holdings-risk endpoint agree.
            equities, bonds, crypto, cash, commodities = 0, 0, 0, 5, 95
        elif "real estate" in ac or "reit" in ac:
            equities, bonds, crypto, cash = 95, 0, 0, 5
        else:
            equities, bonds, crypto, cash = 99.5, 0, 0, 0.5

        return ETFAssetAllocationResponse(
            equities=equities,
            bonds=bonds,
            crypto=crypto,
            commodities=commodities,
            cash=cash,
            total_assets=_fmt(total_assets),
        )

    # ── Related ETFs builder ──────────────────────────────────────

    async def _build_related_etfs(
        self, symbol: str
    ) -> List[RelatedTickerResponse]:
        """Fetch related ETFs: curated table first, then FMP peers as fallback.

        Strategy:
          1. If symbol is in the curated _RELATED_ETFS table, use those (high quality).
          2. Otherwise, try FMP's stock peers endpoint.
          3. If FMP returns nothing, use _DEFAULT_RELATED.
        """
        if symbol in _RELATED_ETFS:
            related_symbols = _RELATED_ETFS[symbol]
        else:
            # Try FMP peers endpoint
            try:
                fmp_peers = await self.fmp.get_stock_peers(symbol)
                # Filter out mutual funds (5-char tickers ending in X) and non-alpha
                related_symbols = [
                    p for p in (fmp_peers or [])
                    if p and 2 <= len(p) <= 5 and p.isalpha() and p.isupper()
                    and not (len(p) == 5 and p.endswith("X"))
                ][:6]
                if related_symbols:
                    logger.info(f"Related ETFs from FMP peers for {symbol}: {related_symbols}")
                else:
                    related_symbols = _DEFAULT_RELATED
            except Exception as e:
                logger.warning(f"FMP peers failed for {symbol}: {e}")
                related_symbols = _DEFAULT_RELATED

        # Exclude self
        related_symbols = [s for s in related_symbols if s != symbol][:6]

        if not related_symbols:
            return []

        # ONE `batch-quote` request for the peers instead of one `/quote` each. Same
        # field set (verified live), so this is a pure call-count reduction.
        try:
            rows = await self.fmp.get_batch_quotes_bulk(related_symbols)
        except Exception as e:
            logger.warning(
                "Related-ETF batch quote failed for %s: %s: %s",
                symbol, type(e).__name__, e,
            )
            rows = []
        by_symbol = {
            str(r.get("symbol", "")).upper(): r
            for r in (rows or [])
            if isinstance(r, dict) and r.get("symbol")
        }

        related = []
        for sym in related_symbols:
            res = by_symbol.get(sym.upper())
            if not res:
                continue
            related.append(RelatedTickerResponse(
                symbol=sym,
                name=res.get("name") or sym,
                price=_finite_num(res.get("price")),
                change_percent=round(_finite_num(
                    res.get("changePercentage") or res.get("changesPercentage")
                ), 2),
            ))
        return related

    # ── News builder ──────────────────────────────────────────────

    def _build_news(
        self, raw_articles: List[Dict]
    ) -> List[ETFNewsArticleResponse]:
        articles = []
        for item in raw_articles[:10]:
            # FMP's publishedDate is a naive America/New_York wall clock, but this field is
            # a timestamp the client renders, and the News tab now serves a true UTC instant
            # (news_cache_service._sanitize_published_at). Emitting the raw string here made
            # the same article read 4h apart on two screens. These detail builders bypass
            # news_cache_service entirely, so the ingest-level fix does NOT reach them.
            # Keep the `or ""` fallback: the field is REQUIRED and a None 500s the response.
            _raw_pub = item.get("publishedDate") or item.get("published_date") or ""
            _pub_dt = to_utc_instant(_raw_pub)
            published = _pub_dt.isoformat() if _pub_dt is not None else _raw_pub
            articles.append(ETFNewsArticleResponse(
                headline=item.get("title") or item.get("headline") or "",
                source_name=item.get("site") or item.get("source") or "Unknown",
                source_icon=None,
                sentiment="neutral",
                published_at=published,
                thumbnail_url=item.get("image") or item.get("thumbnail_url"),
                related_tickers=[
                    s.strip() for s in (item.get("symbol") or "").split(",") if s.strip()
                ],
                summary_bullets=[],
                article_url=item.get("url") or item.get("article_url"),
            ))
        return articles

    # ── Identity Rating (100% FMP data) ────────────────────────────

    def _build_identity_rating(
        self, *, total_assets: float, beta: float, expense_ratio: float,
        inception_date: str, holdings_count: int,
    ) -> ETFIdentityRatingResponse:
        """
        Build identity rating from FMP data only.

        Score (1-5): Composite of AUM, age, expense ratio, and diversification.
        Volatility: Directly from beta.
        """
        # ── Score: weighted composite ────────────────────────────
        # AUM component (0-2 points)
        if total_assets > 50_000_000_000:
            aum_pts = 2.0
        elif total_assets > 10_000_000_000:
            aum_pts = 1.5
        elif total_assets > 1_000_000_000:
            aum_pts = 1.0
        elif total_assets > 100_000_000:
            aum_pts = 0.5
        else:
            aum_pts = 0.0

        # Age component (0-1 point) — older = more proven
        age_years = 0
        if inception_date:
            try:
                inception = datetime.strptime(inception_date, "%Y-%m-%d")
                age_years = (datetime.now() - inception).days / 365.25
            except (ValueError, TypeError):
                pass
        if age_years > 15:
            age_pts = 1.0
        elif age_years > 7:
            age_pts = 0.7
        elif age_years > 3:
            age_pts = 0.4
        else:
            age_pts = 0.1

        # Expense ratio component (0-1 point) — lower = better
        if expense_ratio <= 0.05:
            fee_pts = 1.0
        elif expense_ratio <= 0.15:
            fee_pts = 0.8
        elif expense_ratio <= 0.40:
            fee_pts = 0.5
        elif expense_ratio <= 0.75:
            fee_pts = 0.2
        else:
            fee_pts = 0.0

        # Diversification component (0-1 point)
        if holdings_count >= 500:
            div_pts = 1.0
        elif holdings_count >= 100:
            div_pts = 0.7
        elif holdings_count >= 30:
            div_pts = 0.4
        else:
            div_pts = 0.1

        raw_score = aum_pts + age_pts + fee_pts + div_pts  # 0-5
        score = max(1, min(5, round(raw_score)))

        # ── Volatility from beta ─────────────────────────────────
        # Negative beta = inverse/leveraged ETF (moves opposite to market)
        # Use abs(beta) for magnitude; negative beta is always high risk
        if beta < 0:
            vol_label = "High Volatility"
        elif beta < 0.8:
            vol_label = "Low Volatility"
        elif beta < 1.2:
            vol_label = "Moderate Volatility"
        else:
            vol_label = "High Volatility"

        return ETFIdentityRatingResponse(
            score=score,
            max_score=5,
            volatility_label=vol_label,
        )

    # ── Strategy (FMP for tags, Gemini for hook text only) ───────

    async def _build_strategy(
        self, *, symbol: str, name: str, description: str,
        asset_class: str, index_tracked: str, holdings_count: int,
        top_holdings: List[ETFTopHoldingResponse],
        top_sectors: List[ETFSectorWeightResponse],
    ) -> ETFStrategyResponse:
        """
        Build strategy snapshot.
        Tags: derived from FMP data (asset class, index, holdings).
        Hook: Gemini generates a punchy one-liner; falls back to template.
        """
        # ── Tags from FMP data ───────────────────────────────────
        tags = []
        ac = asset_class.lower()

        # Passive vs Active
        if index_tracked and index_tracked != "—":
            tags.append("Passive")
            tags.append("Index")
        else:
            tags.append("Active")

        # Asset class tags
        if "bond" in ac or "fixed" in ac:
            tags.append("Bond")
        elif "commodity" in ac or "gold" in ac:
            tags.append("Thematic")
        elif "real estate" in ac or "reit" in ac:
            tags.append("Sector")
        elif "equity" in ac or ac == "":
            # Size classification from holdings count
            if holdings_count >= 500:
                tags.append("Large Cap")
                tags.append("Blend")
            elif holdings_count >= 100:
                tags.append("Blend")
            elif holdings_count < 50:
                tags.append("Thematic")

        # Check for dividend focus from name
        name_lower = name.lower() + " " + (description or "").lower()
        if "dividend" in name_lower or "yield" in name_lower:
            tags.append("Dividend")
        if "growth" in name_lower:
            tags.append("Growth")
        if "value" in name_lower:
            tags.append("Value")
        if "international" in name_lower or "global" in name_lower or "emerging" in name_lower:
            tags.append("International")

        # Deduplicate and limit
        seen = set()
        unique_tags = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                unique_tags.append(t)
        tags = unique_tags[:4]

        if not tags:
            tags = ["Index", "Blend"]

        # ── Hook: Gemini for creative text, fallback to template ──
        fallback_hook = self._build_hook_fallback(
            asset_class=asset_class,
            index_tracked=index_tracked,
            holdings_count=holdings_count,
        )

        hook = await self._generate_hook_text(
            symbol=symbol,
            name=name,
            description=description,
            asset_class=asset_class,
            index_tracked=index_tracked,
            holdings_count=holdings_count,
            top_holdings=top_holdings,
            top_sectors=top_sectors,
            fallback=fallback_hook,
        )

        return ETFStrategyResponse(hook=hook, tags=tags)

    def _build_hook_fallback(
        self, *, asset_class: str, index_tracked: str, holdings_count: int,
    ) -> str:
        """Template-based hook when Gemini is unavailable."""
        if index_tracked and index_tracked != "—":
            return f"Tracks the {index_tracked}. {holdings_count} holdings for broad market exposure."[:120]
        return f"A {asset_class.lower()} fund with {holdings_count} holdings."[:120]

    async def _generate_hook_text(
        self, *, symbol: str, name: str, description: str,
        asset_class: str, index_tracked: str, holdings_count: int,
        top_holdings: List[ETFTopHoldingResponse],
        top_sectors: List[ETFSectorWeightResponse],
        fallback: str,
    ) -> str:
        """
        Use Gemini to generate ONLY the hook text — one punchy sentence.
        All structured data (score, tags) comes from FMP.
        Cached for 1 hour. Returns fallback on any failure.
        """
        cache_key = f"etf_hook_{symbol}"
        cached = _cache_get(cache_key, _AI_CACHE_TTL_SECONDS)
        if cached:
            return cached

        try:
            gemini = get_gemini_client()

            holdings_text = ", ".join(
                f"{h.symbol} ({h.weight}%)" for h in top_holdings[:5]
            )
            sectors_text = ", ".join(
                f"{s.name} ({s.weight}%)" for s in top_sectors[:3]
            )

            prompt = f"""Write ONE sentence (max 120 characters) that explains what this ETF does in plain English for a beginner investor.

ETF: {symbol} — {name}
Asset Class: {asset_class}
Index Tracked: {index_tracked or 'Actively managed'}
Holdings: {holdings_count}
Top Holdings: {holdings_text}
Top Sectors: {sectors_text}
Description: {(description or 'N/A')[:200]}

RULES:
- Max 120 characters total
- Plain English, no jargon
- Be direct and specific about what this fund actually does
- Do NOT start with "This ETF" or "This fund"
- Output ONLY the sentence, nothing else"""

            ai_response = await gemini.generate_text(
                prompt=prompt,
                # Wrapped: IDENTITY_RULE + ADVICE_BOUNDARY (see persona_config).
                system_instruction=neutral_system_instruction(
                    "You are a concise financial writer. Output only the requested sentence."
                ),
                model_name="gemini-2.5-flash",
            )

            text = ai_response.get("text", "").strip().strip('"').strip("'")
            # Remove any markdown or extra content
            text = text.split("\n")[0].strip()

            if text and len(text) <= 140:
                hook = text[:120]
                _cache_set(cache_key, hook)
                logger.info(f"Generated Gemini hook for ETF {symbol}: {hook}")
                return hook
            else:
                logger.warning(f"Gemini hook too long or empty for {symbol}, using fallback")
                return fallback

        except Exception as e:
            logger.warning(f"Gemini hook failed for {symbol}, using fallback: {e}")
            return fallback


# ── Singleton ────────────────────────────────────────────────────

_etf_service: Optional[ETFService] = None


def get_etf_service() -> ETFService:
    global _etf_service
    if _etf_service is None:
        _etf_service = ETFService()
    return _etf_service
