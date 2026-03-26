"""
Stock Overview Service — aggregates FMP data, computes derived stats
(performance, snapshots, sector info) for the TickerDetailView Overview tab.

Pattern follows etf_service.py: parallel FMP calls, in-memory caching,
helper functions for return calculations and snapshot ratings.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client, FMPClient
from app.integrations.yahoo_finance import get_short_interest
from app.schemas.etf import (
    BenchmarkSummaryResponse,
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    MarketStatusResponse,
    PerformancePeriodResponse,
    RelatedTickerResponse,
)
from app.schemas.stock_overview import (
    CompanyProfileResponse,
    SectorIndustryResponse,
    SnapshotItemResponse,
    SnapshotMetricResponse,
    StockOverviewResponse,
)
from app.services.sector_benchmark_service import _FMP_SECTOR_MAP

logger = logging.getLogger(__name__)


def _normalize_sector(name: str) -> str:
    """Map FMP sector name to canonical app sector name using the shared map."""
    return _FMP_SECTOR_MAP.get(name, name)

# ── In-memory cache ──────────────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_VOLATILE_TTL = 120            # 2 min for intraday data (quote, chart)
_FUNDAMENTALS_MEM_TTL = 3600   # 1 hour in-memory for fundamentals
_FUNDAMENTALS_DB_TTL_HOURS = 24  # 24 hours in Supabase for fundamentals
_SP_HIST_CACHE_TTL = 3600      # 1 hour for S&P historical
_CACHE_TTL = _VOLATILE_TTL     # default TTL for general cache


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
    _cache[key] = (time.time(), value)


# ── Sector P/E averages (approximate, for valuation context) ─────

_SECTOR_PE_AVG: Dict[str, float] = {
    "Technology": 30.0,
    "Healthcare": 22.0,
    "Financial Services": 15.0,
    "Consumer Cyclical": 22.0,
    "Consumer Defensive": 23.0,
    "Industrials": 21.0,
    "Energy": 12.0,
    "Utilities": 18.0,
    "Real Estate": 35.0,
    "Basic Materials": 16.0,
    "Communication Services": 20.0,
}

_SECTOR_PS_AVG: Dict[str, float] = {
    "Technology": 7.0,
    "Healthcare": 4.5,
    "Financial Services": 3.0,
    "Consumer Cyclical": 2.5,
    "Consumer Defensive": 2.0,
    "Industrials": 2.5,
    "Energy": 1.5,
    "Utilities": 2.5,
    "Real Estate": 6.0,
    "Basic Materials": 2.0,
    "Communication Services": 4.0,
}

_SECTOR_PFCF_AVG: Dict[str, float] = {
    "Technology": 30.0,
    "Healthcare": 25.0,
    "Financial Services": 15.0,
    "Consumer Cyclical": 22.0,
    "Consumer Defensive": 20.0,
    "Industrials": 22.0,
    "Energy": 12.0,
    "Utilities": 15.0,
    "Real Estate": 30.0,
    "Basic Materials": 16.0,
    "Communication Services": 22.0,
}

_SECTOR_EV_EBITDA_AVG: Dict[str, float] = {
    "Technology": 22.0,
    "Healthcare": 16.0,
    "Financial Services": 12.0,
    "Consumer Cyclical": 14.0,
    "Consumer Defensive": 15.0,
    "Industrials": 14.0,
    "Energy": 8.0,
    "Utilities": 12.0,
    "Real Estate": 20.0,
    "Basic Materials": 10.0,
    "Communication Services": 14.0,
}


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


def _safe_float(d: Dict, key: str, default: float = 0.0) -> float:
    """Safely extract a float from a dict."""
    v = d.get(key)
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


# ── Return computation helpers (same as etf_service) ─────────────


def _compute_return(prices: List[Dict], days_back: int) -> Optional[float]:
    """Compute % return over the last N trading days."""
    if not prices or len(prices) < 2:
        return None
    if len(prices) <= days_back:
        start = prices[0].get("close") or prices[0].get("adjClose")
        end = prices[-1].get("close") or prices[-1].get("adjClose")
    else:
        start = prices[-(days_back + 1)].get("close") or prices[-(days_back + 1)].get("adjClose")
        end = prices[-1].get("close") or prices[-1].get("adjClose")

    if not start or not end or start == 0:
        return None
    return ((end - start) / start) * 100


def _compute_ytd_return(prices: List[Dict]) -> Optional[float]:
    if not prices or len(prices) < 2:
        return None
    current_year = datetime.now(tz=timezone.utc).year
    for p in prices:
        date_str = p.get("date", "")
        if date_str.startswith(str(current_year)):
            start_price = p.get("close") or p.get("adjClose")
            end_price = prices[-1].get("close") or prices[-1].get("adjClose")
            if start_price and end_price and start_price > 0:
                return ((end_price - start_price) / start_price) * 100
            break
    return None


def _get_market_status() -> MarketStatusResponse:
    now = datetime.now(tz=timezone(timedelta(hours=-5)))  # EST
    hour = now.hour
    minute = now.minute
    weekday = now.weekday()

    if weekday >= 5:
        status = "closed"
    elif hour < 4:
        status = "closed"
    elif hour < 9 or (hour == 9 and minute < 30):
        status = "pre_market"
    elif hour < 16:
        status = "open"
    elif hour < 20:
        status = "after_hours"
    else:
        status = "closed"

    if status == "closed":
        return MarketStatusResponse(
            status="closed",
            date=now.strftime("%Y-%m-%dT16:00:00-05:00"),
            time="4:00 PM",
            timezone="EST",
        )
    return MarketStatusResponse(status=status)


def _parse_historical(hist_raw) -> List[Dict]:
    """Parse FMP historical prices into sorted list (oldest-first)."""
    historical: List[Dict] = []
    if isinstance(hist_raw, dict):
        historical = hist_raw.get("historical", [])
    elif isinstance(hist_raw, list):
        historical = hist_raw
    historical.sort(key=lambda p: p.get("date", ""))
    return historical


def _extract_chart_data(prices: List[Dict], chart_range: str) -> List[Dict]:
    """Extract OHLCV data for the requested chart range.

    Includes extra trading days before the display range so that
    technical indicators (MACD ≈ 34, RSI ≈ 14) can warm up.
    """
    _WARMUP_TRADING_DAYS = 50
    range_days = {
        "1D": 2, "1W": 5,
        "3M": 63 + _WARMUP_TRADING_DAYS,
        "6M": 126 + _WARMUP_TRADING_DAYS,
        "1Y": 252 + _WARMUP_TRADING_DAYS,
        "5Y": 1260 + _WARMUP_TRADING_DAYS,
        "ALL": 999999,
    }
    days = range_days.get(chart_range, 63 + _WARMUP_TRADING_DAYS)
    relevant = prices[-days:] if len(prices) > days else prices
    result = []
    for p in relevant:
        close = p.get("close") or p.get("adjClose")
        if close:
            result.append({
                "date": p.get("date"),
                "open": p.get("open"),
                "high": p.get("high"),
                "low": p.get("low"),
                "close": float(close),
                "volume": p.get("volume"),
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
        vol_task = self._get_volatile(ticker, chart_range, interval, extended_hours)
        sector_perf_task = self.fmp.get_sector_performance()
        industry_perf_task = self.fmp.get_industry_performance()
        prof_task = get_profitability_snapshot_service().get_profitability_snapshot(ticker)
        growth_task = get_growth_snapshot_service().get_growth_snapshot(ticker)
        val_task = get_valuation_snapshot_service().get_valuation_snapshot(ticker)
        health_task = get_health_snapshot_service().get_health_snapshot(ticker)
        ownership_task = get_ownership_snapshot_service().get_ownership_snapshot(ticker)
        fundamentals, volatile, live_sector_perf, live_industry_perf, prof_snapshot, growth_snapshot, val_snapshot, health_snapshot, ownership_snapshot = await asyncio.gather(
            fund_task, vol_task, sector_perf_task, industry_perf_task, prof_task, growth_task, val_task, health_task, ownership_task, return_exceptions=True,
        )
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

        # ── Build response from both data sources ─────────────────
        response = self._build_full_response(
            ticker, fundamentals, volatile, chart_range, interval, extended_hours,
            profitability_snapshot=prof_snapshot,
            growth_snapshot=growth_snapshot,
            valuation_snapshot=val_snapshot,
            health_snapshot=health_snapshot,
            ownership_snapshot=ownership_snapshot,
        )

        # Related tickers (async call, uses its own caching)
        response.related_tickers = await self._build_related_tickers(ticker)

        # Cache full response for 120s (volatile freshness)
        _cache_set(overview_key, response)
        return response

    # ── Fundamentals: 24h Supabase + 1h in-memory ─────────────────

    async def _get_fundamentals(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch slow-moving data with two-tier cache:
          Tier 1: in-memory (1h TTL)
          Tier 2: Supabase stock_fundamentals_cache (24h TTL)
          Miss:   parallel FMP calls → cache in both tiers
        """
        mem_key = f"fundamentals:{ticker}"

        # Tier 1: in-memory
        cached = _cache_get(mem_key, ttl=_FUNDAMENTALS_MEM_TTL)
        if cached is not None:
            logger.debug(f"Fundamentals in-memory HIT for {ticker}")
            return cached

        # Tier 2: Supabase
        db_data = self._check_fundamentals_db(ticker)
        if db_data is not None:
            _cache_set(mem_key, db_data)
            return db_data

        # Miss: fetch from FMP + Yahoo
        logger.info(f"Fundamentals MISS for {ticker} — fetching from APIs")
        data = await self._fetch_fundamentals(ticker)

        # Cache in both tiers
        _cache_set(mem_key, data)
        self._upsert_fundamentals_db(ticker, data)

        return data

    async def _fetch_fundamentals(self, ticker: str) -> Dict[str, Any]:
        """Parallel FMP calls for all fundamental/slow-moving data."""
        today = datetime.now(tz=timezone.utc).date()
        from_date_10y = (today - timedelta(days=365 * 10 + 30)).isoformat()
        to_date = today.isoformat()

        # SPY historical (separate 1h cache)
        sp_cache_key = f"spy_hist:{from_date_10y}:{to_date}"
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
            self.fmp.get_sector_performance(),                                   # 11
            self.fmp.get_historical_prices(ticker, from_date_10y, to_date),     # 12
            self.fmp.get_industry_performance(),                                 # 13
        ]

        spy_task_idx = None
        if cached_spy is None:
            spy_task_idx = len(tasks)
            tasks.append(self.fmp.get_historical_prices("SPY", from_date_10y, to_date))

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
            spy_hist = _parse_historical(spy_raw)
            if spy_hist:
                _cache_set(sp_cache_key, spy_hist)

        # Parse lists safely
        def _list(i): return _safe(i, []) if isinstance(_safe(i, []), list) else []

        stock_hist = _parse_historical(_safe(12))

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
        }

    def _check_fundamentals_db(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Check Supabase stock_fundamentals_cache (24h TTL)."""
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
                logger.info(f"Fundamentals Supabase HIT for {ticker} (age={age})")
                return data
            return None
        except Exception as e:
            logger.warning(f"Fundamentals Supabase check failed for {ticker}: {e}")
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
        """Upsert formatted company profile into Supabase cache."""
        try:
            self.supabase.table("company_profile_cache").upsert(
                {
                    "ticker": ticker,
                    "profile_json": data,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker",
            ).execute()
            logger.info(f"Company profile cached in Supabase for {ticker}")
        except Exception as e:
            logger.warning(f"Company profile upsert failed for {ticker}: {e}")

    def get_cached_company_profile(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Public accessor for other services (e.g. chat) to read cached profile."""
        return self._check_company_profile_db(ticker.upper())

    # ── Volatile: live intraday data (120s response-level cache) ──

    async def _get_volatile(
        self, ticker: str, chart_range: str, interval: str, extended_hours: bool,
    ) -> Dict[str, Any]:
        """Fetch live quote + chart data (no persistent caching)."""
        quote_task = self.fmp.get_stock_price_quote(ticker)

        from app.services.chart_helper import fetch_chart_data, resolve_interval
        resolved = resolve_interval(chart_range, interval)

        results = await asyncio.gather(quote_task, return_exceptions=True)
        quote = results[0] if not isinstance(results[0], Exception) else {}

        # Chart data
        if resolved != "daily" or chart_range == "ALL":
            chart_data = await fetch_chart_data(
                self.fmp, ticker, chart_range, interval, extended_hours=extended_hours,
            )
        else:
            chart_data = None  # Will be sliced from fundamental stock_historical

        return {"quote": quote, "chart_data": chart_data}

    # ── Build full response from both data sources ────────────────

    def _build_full_response(
        self, ticker: str, fund: Dict, vol: Dict,
        chart_range: str, interval: str, extended_hours: bool,
        profitability_snapshot=None, growth_snapshot=None, valuation_snapshot=None,
        health_snapshot=None, ownership_snapshot=None,
    ) -> StockOverviewResponse:
        """Combine fundamentals + volatile into one response."""
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
        # If fundamentals cache has empty short interest, check the dedicated
        # short_interest_cache (longer TTL, background-populated by Yahoo worker)
        if not short_interest:
            from app.integrations.yahoo_finance import (
                _supabase_cache_get, _supabase_cache_get_stale,
                _mem_cache_get, _enqueue_fetch,
            )
            mem_key = f"yahoo_short:{ticker.upper()}"
            short_interest = _mem_cache_get(mem_key) or {}
            if not short_interest:
                short_interest = _supabase_cache_get(ticker.upper()) or {}
            if not short_interest:
                short_interest = _supabase_cache_get_stale(ticker.upper()) or {}
            if not short_interest:
                _enqueue_fetch(ticker.upper())
        sector_perf = fund.get("sector_perf", [])
        industry_perf = fund.get("industry_perf", [])
        stock_historical = fund.get("stock_historical", [])
        spy_historical = fund.get("spy_historical", [])

        # Price from volatile quote, fallback to profile
        price = _safe_float(quote, "price") or _safe_float(profile, "price")
        change = _safe_float(quote, "change") or _safe_float(profile, "changes")
        change_pct = _safe_float(quote, "changesPercentage") or _safe_float(profile, "changesPercentage")
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

        # Cache formatted profile for chat AI context
        self._upsert_company_profile_db(ticker, {
            "description": company_profile.description,
            "ceo": company_profile.ceo,
            "founded": company_profile.founded,
            "employees": company_profile.employees,
            "headquarters": company_profile.headquarters,
            "website": company_profile.website,
            "sector": sector_industry.sector,
            "industry": sector_industry.industry,
            "sector_performance": sector_industry.sector_performance,
            "industry_rank": sector_industry.industry_rank,
        })

        # Benchmark summary
        benchmark_summary = self._build_benchmark_summary(
            stock_historical, spy_historical,
        )

        return StockOverviewResponse(
            symbol=ticker,
            company_name=company_name,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
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
    ) -> Tuple[List[KeyStatisticItem], List[KeyStatisticsGroupResponse]]:
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

        # ── EPS (TTM): sum diluted EPS from last 4 quarterly income statements ──
        eps = None
        pe = None
        if income_quarterly and len(income_quarterly) >= 4:
            try:
                ttm_eps = sum(
                    float(q.get("epsDiluted") or q.get("eps") or 0)
                    for q in income_quarterly[:4]
                )
                if ttm_eps > 0:
                    eps = round(ttm_eps, 2)
            except (ValueError, TypeError):
                pass

        # Fallback: try quote fields, then key_metrics earningsYield
        if not eps:
            eps = _safe_float(quote, "eps")
        if not eps and price and price > 0:
            km_latest = key_metrics[0] if key_metrics else {}
            earnings_yield = _safe_float(km_latest, "earningsYield")
            if earnings_yield and earnings_yield > 0:
                eps = round(earnings_yield * price, 2)

        # ── P/E (TTM): price / EPS ──
        if eps and eps > 0 and price and price > 0:
            pe = round(price / eps, 2)

        # ── Forward P/E: use nearest future fiscal year estimate ──
        pe_fwd = None
        if analyst_est and price and price > 0:
            today_str = datetime.now(tz=timezone.utc).date().isoformat()
            # Sort by date ascending to find nearest future year
            future_ests = []
            for est in analyst_est:
                if isinstance(est, dict):
                    est_date = est.get("date", "")
                    if est_date >= today_str:
                        future_ests.append(est)
            # Pick the nearest future estimate
            if future_ests:
                future_ests.sort(key=lambda x: x.get("date", ""))
                nearest = future_ests[0]
                fwd_eps = _safe_float(nearest, "epsAvg") or _safe_float(nearest, "estimatedEpsAvg")
                if fwd_eps and fwd_eps > 0:
                    pe_fwd = round(price / fwd_eps, 2)

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
        div_str = "—"
        if last_div > 0 and price > 0:
            annual_div = last_div  # FMP lastDiv is already annualized
            div_yield = (annual_div / price) * 100
            div_str = f"{annual_div:.2f} ({div_yield:.2f}%)"

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
            KeyStatisticItem(label="P/E (TTM)", value=f"{pe:.2f}" if pe and pe > 0 else "—"),
            KeyStatisticItem(label="P/E (FWD)", value=f"{pe_fwd:.2f}" if pe_fwd else "—"),
            KeyStatisticItem(label="EPS (TTM)", value=f"{eps:.2f}" if eps else "—"),
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
            KeyStatisticItem(label="P/E (TTM)", value=f"{pe:.2f}" if pe and pe > 0 else "—"),
            KeyStatisticItem(label="P/E (FWD)", value=f"{pe_fwd:.2f}" if pe_fwd else "—"),
            KeyStatisticItem(label="EPS (TTM)", value=f"{eps:.2f}" if eps else "—"),
            KeyStatisticItem(label="Dividends", value=div_str),
            KeyStatisticItem(label="Beta", value=f"{beta:.2f}" if beta else "—"),
        ])

        # Ownership group
        # Short % of Float: compute from sharesShort / floatShares when possible
        short_interest = short_interest or {}
        short_pct_val = None

        # Primary: compute from sharesShort (Yahoo) / floatShares (FMP)
        shares_short = short_interest.get("shares_short")
        if shares_short and shares_short > 0 and float_shares_val and float_shares_val > 0:
            short_pct_val = round((shares_short / float_shares_val) * 100, 2)

        # Fallback 1: use Yahoo's pre-computed short_percent_of_float
        if short_pct_val is None:
            short_pct_val = short_interest.get("short_percent_of_float")

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

        short_pct_str = f"{short_pct_val:.2f}%" if short_pct_val is not None else "N/A"

        # Float shares from shares-float endpoint (or fallback calculation)
        if not float_shares_val and shares_out and insider_pct is not None:
            try:
                ins = float(insider_pct)
                if ins < 1:
                    ins *= 100
                float_shares_val = shares_out * (1 - ins / 100)
            except (ValueError, TypeError):
                pass
        float_str = _fmt_large(float_shares_val) if float_shares_val else "—"

        insider_str = _pct(insider_pct) if insider_pct is not None else "—"
        inst_str = _pct(inst_pct) if inst_pct is not None else "—"

        group4 = KeyStatisticsGroupResponse(statistics=[
            KeyStatisticItem(label="Short % of Float", value=short_pct_str, is_highlighted=True),
            KeyStatisticItem(label="Shares Outstanding", value=_fmt_large(shares_out) if shares_out else "—"),
            KeyStatisticItem(label="Float", value=float_str),
            KeyStatisticItem(label="% Held by Insiders", value=insider_str),
            KeyStatisticItem(label="% Held Inst.", value=inst_str),
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
                ))
        return periods

    # ── Snapshots ─────────────────────────────────────────────────

    def _build_snapshots(
        self, key_metrics: List[Dict], fin_ratios: List[Dict],
        income_annual: List[Dict], balance_annual: List[Dict],
        cashflow_annual: List[Dict], price: float, market_cap: float,
        sector: str, profitability_snapshot=None, growth_snapshot=None, valuation_snapshot=None,
        health_snapshot=None, ownership_snapshot=None,
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
            snapshots.append(self._build_profitability_snapshot(km, fr, inc0))

        # 2. Growth (use cached sector-relative snapshot if available)
        if growth_snapshot is not None:
            snapshots.append(growth_snapshot)
        else:
            snapshots.append(self._build_growth_snapshot(inc0, inc1, cf0, cf1, km, key_metrics))

        # 3. Price / Valuation (use cached sector-relative snapshot if available)
        if valuation_snapshot is not None:
            snapshots.append(valuation_snapshot)
        else:
            snapshots.append(self._build_valuation_snapshot(fr, km, sector))

        # 4. Financial Health (use cached sector-relative snapshot if available)
        if health_snapshot is not None:
            snapshots.append(health_snapshot)
        else:
            snapshots.append(self._build_health_snapshot(bs, inc0, cf0, fr, km, market_cap))

        # 5. Insiders & Ownership (use cached snapshot if available)
        if ownership_snapshot is not None:
            snapshots.append(ownership_snapshot)
        else:
            snapshots.append(self._build_ownership_snapshot(km))

        return snapshots

    def _build_profitability_snapshot(
        self, km: Dict, fr: Dict, inc: Dict
    ) -> SnapshotItemResponse:
        op_margin = _safe_float(fr, "operatingProfitMargin") or _safe_float(km, "operatingProfitMargin")
        net_margin = _safe_float(fr, "netProfitMargin") or _safe_float(km, "netIncomePerShare")
        roe = _safe_float(fr, "returnOnEquity") or _safe_float(km, "roe")
        roa = _safe_float(fr, "returnOnAssets") or _safe_float(km, "returnOnTangibleAssets")

        # If margins are in decimal form (0.25 = 25%), convert
        if op_margin and abs(op_margin) < 1:
            op_margin *= 100
        if net_margin and abs(net_margin) < 1:
            net_margin *= 100
        if roe and abs(roe) < 5:  # likely decimal
            roe *= 100
        if roa and abs(roa) < 5:
            roa *= 100

        # Rating
        if roe and roe > 20 and net_margin and net_margin > 15:
            rating = 5
        elif roe and roe > 10 and net_margin and net_margin > 8:
            rating = 4
        elif roe and roe > 5:
            rating = 3
        elif roe and roe > 0:
            rating = 2
        else:
            rating = 1

        metrics = [
            SnapshotMetricResponse(name="Operating Margin", value=_pct(op_margin)),
            SnapshotMetricResponse(name="Net Margin", value=_pct(net_margin)),
            SnapshotMetricResponse(name="Return on Equity (ROE)", value=_pct(roe)),
            SnapshotMetricResponse(name="Return on Assets (ROA)", value=_pct(roa)),
        ]
        return SnapshotItemResponse(
            category="Profitability", rating=rating, metrics=metrics
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
            category="Growth", rating=rating, metrics=metrics
        )

    def _build_valuation_snapshot(
        self, fr: Dict, km: Dict, sector: str
    ) -> SnapshotItemResponse:
        pe = _safe_float(fr, "priceEarningsRatio") or _safe_float(km, "peRatio")
        ps = _safe_float(fr, "priceToSalesRatio") or _safe_float(km, "priceToSalesRatio")
        pfcf = _safe_float(fr, "priceToFreeCashFlowsRatio") or _safe_float(km, "pfcfRatio")
        ev_ebitda = _safe_float(fr, "enterpriseValueOverEBITDA") or _safe_float(km, "enterpriseValueOverEBITDA")

        sector_pe = _SECTOR_PE_AVG.get(sector, 20.0)
        sector_ps = _SECTOR_PS_AVG.get(sector, 3.0)
        sector_pfcf = _SECTOR_PFCF_AVG.get(sector, 25.0)
        sector_ev_ebitda = _SECTOR_EV_EBITDA_AVG.get(sector, 18.0)

        # Rating: lower multiples = better (inverted)
        def _val_score(val: float, avg: float) -> int:
            if val <= 0:
                return 3  # can't rate negative
            ratio = val / avg
            if ratio <= 0.8:
                return 5
            if ratio <= 1.2:
                return 4
            if ratio <= 1.5:
                return 3
            if ratio <= 2.0:
                return 2
            return 1

        scores = []
        if pe > 0:
            scores.append(_val_score(pe, sector_pe))
        if ps > 0:
            scores.append(_val_score(ps, sector_ps))
        if pfcf > 0:
            scores.append(_val_score(pfcf, sector_pfcf))
        if ev_ebitda > 0:
            scores.append(_val_score(ev_ebitda, sector_ev_ebitda))

        rating = round(sum(scores) / len(scores)) if scores else 3

        # Format with sector context
        def _val_ctx(val: float, avg: float, label: str) -> str:
            if val <= 0:
                return "—"
            ratio = val / avg if avg > 0 else 0
            return f"{ratio:.2f}x sector avg {avg:.0f}"

        metrics = [
            SnapshotMetricResponse(
                name=f"P/E ({_val_ctx(pe, sector_pe, 'P/E')})",
                value=f"{pe:.2f}" if pe > 0 else "—"
            ),
            SnapshotMetricResponse(
                name=f"P/S ({_val_ctx(ps, sector_ps, 'P/S')})",
                value=f"{ps:.2f}" if ps > 0 else "—"
            ),
            SnapshotMetricResponse(
                name=f"P/FCF ({_val_ctx(pfcf, sector_pfcf, 'P/FCF')})",
                value=f"{pfcf:.2f}" if pfcf > 0 else "—"
            ),
            SnapshotMetricResponse(
                name=f"EV/EBITDA ({_val_ctx(ev_ebitda, sector_ev_ebitda, 'EV/EBITDA')})",
                value=f"{ev_ebitda:.2f}" if ev_ebitda > 0 else "—"
            ),
        ]
        return SnapshotItemResponse(
            category="Price", rating=rating, metrics=metrics
        )

    def _build_health_snapshot(
        self, bs: Dict, inc: Dict, cf: Dict, fr: Dict, km: Dict,
        market_cap: float,
    ) -> SnapshotItemResponse:
        # Altman Z-Score components
        total_assets = _safe_float(bs, "totalAssets")
        total_liab = _safe_float(bs, "totalLiabilities")
        current_assets = _safe_float(bs, "totalCurrentAssets")
        current_liab = _safe_float(bs, "totalCurrentLiabilities")
        retained_earnings = _safe_float(bs, "retainedEarnings")
        ebit = _safe_float(inc, "operatingIncome") or _safe_float(inc, "ebitda")
        revenue = _safe_float(inc, "revenue")

        z_score = None
        if total_assets and total_assets > 0 and total_liab > 0:
            wc = current_assets - current_liab
            z_score = (
                1.2 * (wc / total_assets)
                + 1.4 * (retained_earnings / total_assets if retained_earnings else 0)
                + 3.3 * (ebit / total_assets if ebit else 0)
                + 0.6 * (market_cap / total_liab if market_cap else 0)
                + 1.0 * (revenue / total_assets if revenue else 0)
            )
            z_score = round(z_score, 1)

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
            rating = 3  # neutral if can't compute

        metrics = [
            SnapshotMetricResponse(name="Altman Z-Score", value=f"{z_score}" if z_score else "—"),
            SnapshotMetricResponse(
                name="Interest Coverage",
                value=f"{interest_coverage:.1f}x" if interest_coverage else "—"
            ),
            SnapshotMetricResponse(name="Cash to Debt", value=f"{cash_to_debt}" if cash_to_debt else "—"),
            SnapshotMetricResponse(name="Free Cash Flow Margin", value=_pct(fcf_margin, 1)),
            SnapshotMetricResponse(
                name="Asset Turnover",
                value=f"{asset_turnover:.2f}" if asset_turnover else "—"
            ),
        ]
        return SnapshotItemResponse(
            category="Financial Health", rating=rating, metrics=metrics
        )

    def _build_ownership_snapshot(self, km: Dict) -> SnapshotItemResponse:
        inst_pct = km.get("institutionalOwnership") or km.get("institutionPercentage")
        insider_pct = km.get("insidersPercentage")

        # Format: if decimal (0.61) multiply by 100
        def _fmt_own(val) -> str:
            if val is None:
                return "—"
            v = float(val)
            if v < 1:
                v *= 100
            return f"{v:.1f}%"

        # Default neutral rating
        rating = 3

        metrics = [
            SnapshotMetricResponse(name="Institutional Ownership", value=_fmt_own(inst_pct)),
            SnapshotMetricResponse(name="Insider Ownership", value=_fmt_own(insider_pct)),
            SnapshotMetricResponse(name="Hedge Fund Holdings", value="—"),
            SnapshotMetricResponse(name="Top 10 Holders", value="—"),
            SnapshotMetricResponse(name="Institutional Activity", value="—"),
        ]
        return SnapshotItemResponse(
            category="Insiders & Ownership", rating=rating, metrics=metrics
        )

    # ── Sector & Industry ─────────────────────────────────────────

    def _build_sector_industry(
        self, profile: Dict, sector_perf: List[Dict],
        industry_perf: List[Dict] = None,
    ) -> SectorIndustryResponse:
        sector_name = profile.get("sector") or "N/A"
        industry = profile.get("industry") or "N/A"

        # --- Sector performance (prefer 1Y, fallback to daily) ---
        sector_perf_value = 0.0
        if isinstance(sector_perf, list) and sector_perf:
            logger.info(f"[SectorIndustry] sector_perf sample: {sector_perf[0]}")
            for sp in sector_perf:
                sp_sector = sp.get("sector", "")
                if _normalize_sector(sp_sector) == _normalize_sector(sector_name):
                    # Prefer 1Y performance, fallback to daily
                    val = _safe_float(sp, "oneYearPerformance")
                    if val == 0.0:
                        val = (
                            _safe_float(sp, "changesPercentage")
                            or _safe_float(sp, "averageChangePercent")
                            or _safe_float(sp, "changePercent")
                            or _safe_float(sp, "change_percentage")
                        )
                    if val != 0.0:
                        sector_perf_value = val
                        break

        # --- Industry rank within sector ---
        industry_rank = "--"
        if industry_perf and isinstance(industry_perf, list):
            logger.info(f"[SectorIndustry] industry_perf sample: {industry_perf[0] if industry_perf else 'empty'}")
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
            employees=int(profile.get("fullTimeEmployees") or 0),
            headquarters=hq,
            website=website,
            sector=sector_industry.sector if sector_industry else profile.get("sector") or "N/A",
            industry=sector_industry.industry if sector_industry else profile.get("industry") or "N/A",
            sector_performance=sector_industry.sector_performance if sector_industry else 0.0,
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

            peer_quotes = await self.fmp.get_batch_quotes(peers)
            related = []
            for q in peer_quotes:
                if not isinstance(q, dict):
                    continue
                symbol = q.get("symbol", "")
                if not symbol:
                    continue
                related.append(RelatedTickerResponse(
                    symbol=symbol,
                    name=q.get("name") or symbol,
                    price=round(_safe_float(q, "price"), 2),
                    change_percent=round(
                        _safe_float(q, "changePercentage") or _safe_float(q, "changesPercentage"), 2
                    ),
                ))
            return related
        except Exception as e:
            logger.warning(f"Related tickers failed for {ticker}: {e}")
            return []

    # ── Benchmark Summary ─────────────────────────────────────────

    def _build_benchmark_summary(
        self, stock_hist: List[Dict], spy_hist: List[Dict]
    ) -> Optional[BenchmarkSummaryResponse]:
        if not stock_hist or len(stock_hist) < 252:
            return None

        # Use 5-year data for annualized return if available
        days = min(len(stock_hist) - 1, 1260)  # up to 5 years
        years = days / 252

        stock_start = stock_hist[-(days + 1)].get("close") or stock_hist[-(days + 1)].get("adjClose")
        stock_end = stock_hist[-1].get("close") or stock_hist[-1].get("adjClose")

        if not stock_start or not stock_end or stock_start <= 0:
            return None

        stock_annual = ((stock_end / stock_start) ** (1 / years) - 1) * 100

        sp_annual = 0.0
        if spy_hist and len(spy_hist) > days:
            sp_start = spy_hist[-(days + 1)].get("close") or spy_hist[-(days + 1)].get("adjClose")
            sp_end = spy_hist[-1].get("close") or spy_hist[-1].get("adjClose")
            if sp_start and sp_end and sp_start > 0:
                sp_annual = ((sp_end / sp_start) ** (1 / years) - 1) * 100

        return BenchmarkSummaryResponse(
            avg_annual_return=round(stock_annual, 1),
            sp_benchmark=round(sp_annual, 1),
        )


# ── Singleton ────────────────────────────────────────────────────

_service: Optional[StockOverviewService] = None


def get_stock_overview_service() -> StockOverviewService:
    global _service
    if _service is None:
        _service = StockOverviewService()
    return _service
