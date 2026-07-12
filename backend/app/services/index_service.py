"""
Index Detail Service — aggregates FMP data, computes derived stats,
and generates AI-powered snapshot stories via Gemini.

Serves the IndexDetailView screen on iOS.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client, FMPClient
from app.integrations.gemini import get_gemini_client
from app.schemas.index import (
    BenchmarkSummaryResponse,
    ChartDataPointResponse,
    IndexDetailResponse,
    IndexNewsArticleResponse,
    IndexProfileResponse,
    IndexSnapshotsDataResponse,
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    MacroForecastItemResponse,
    MacroForecastSnapshotResponse,
    MarketStatusResponse,
    PerformancePeriodResponse,
    SectorPerformanceEntryResponse,
    SectorPerformanceSnapshotResponse,
    ValuationSnapshotResponse,
)
from app.database import get_supabase

logger = logging.getLogger(__name__)


# ── Static index profile metadata ────────────────────────────────

_INDEX_PROFILES: Dict[str, Dict[str, Any]] = {
    "^GSPC": {
        "name": "S&P 500",
        "description": (
            "The S&P 500 Index is a market-capitalization-weighted index of "
            "500 leading publicly traded companies in the U.S. It is widely "
            "regarded as the best single gauge of large-cap U.S. equities and "
            "serves as the foundation for a wide range of investment products."
        ),
        "exchange": "NYSE / NASDAQ",
        "number_of_constituents": 503,
        "weighting_methodology": "Market-Cap Weighted",
        "inception_date": "March 4, 1957",
        "index_provider": "S&P Dow Jones Indices",
        "website": "www.spglobal.com",
        "historical_avg_pe": 21.0,
        "historical_period": "10-year",
        "avg_annual_return": 10.5,
    },
    "^IXIC": {
        "name": "Nasdaq Composite",
        "description": (
            "The Nasdaq Composite Index measures the performance of more than "
            "3,000 stocks listed on the Nasdaq stock exchange. It is heavily "
            "weighted toward technology companies and serves as a key barometer "
            "for the tech sector and growth stocks."
        ),
        "exchange": "NASDAQ",
        "number_of_constituents": 3000,
        "weighting_methodology": "Market-Cap Weighted",
        "inception_date": "February 5, 1971",
        "index_provider": "Nasdaq, Inc.",
        "website": "www.nasdaq.com",
        "historical_avg_pe": 25.0,
        "historical_period": "10-year",
        "avg_annual_return": 12.2,
    },
    "^DJI": {
        "name": "Dow Jones Industrial Average",
        "description": (
            "The Dow Jones Industrial Average (DJIA) is a price-weighted index "
            "of 30 prominent U.S. companies. One of the oldest and most widely "
            "followed equity indices, it is often cited as a proxy for the "
            "overall health of the U.S. stock market."
        ),
        "exchange": "NYSE / NASDAQ",
        "number_of_constituents": 30,
        "weighting_methodology": "Price Weighted",
        "inception_date": "May 26, 1896",
        "index_provider": "S&P Dow Jones Indices",
        "website": "www.spglobal.com",
        "historical_avg_pe": 18.0,
        "historical_period": "10-year",
        "avg_annual_return": 9.8,
    },
}

# ── Simple in-memory cache ───────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes for market data
_AI_CACHE_TTL_SECONDS = 3600  # 1 hour for AI-generated stories
_MACRO_CACHE_TTL_DAYS = 7  # 7 days for macro forecast (changes weekly)
# Hard cap on live entries — see stock_overview_service for rationale. Eviction
# is least-recently-written; a miss just re-fetches (no correctness impact).
_CACHE_MAX_ENTRIES = 1024


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value, entry_ttl = entry
    if time.time() - ts > entry_ttl:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any, ttl: Optional[float] = None):
    _cache.pop(key, None)
    _cache[key] = (time.time(), value, ttl or _CACHE_TTL_SECONDS)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# ── Helpers ──────────────────────────────────────────────────────


def _fmt(value: Optional[float], decimals: int = 2) -> str:
    """Format a number with commas and N decimal places."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.1f}T"
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{value:,.{decimals}f}"


def _pct(value: Optional[float]) -> str:
    """Format a percentage with sign."""
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _compute_return(prices: List[Dict], days_back: int) -> Optional[float]:
    """Compute % return over the last N trading days."""
    if not prices or len(prices) < 2:
        return None
    # prices are sorted chronologically (oldest first)
    # Not enough history to cover the requested window: return None so the caller
    # OMITS this period rather than mislabeling a shorter return under a longer
    # horizon label. (Supported indices have decades of history so this is a
    # defensive guard, but it keeps the helper consistent with stock/etf/crypto.)
    if len(prices) <= days_back:
        return None
    start = prices[-(days_back + 1)].get("close") or prices[-(days_back + 1)].get("adjClose")
    end = prices[-1].get("close") or prices[-1].get("adjClose")

    if not start or not end or start == 0:
        return None
    return ((end - start) / start) * 100


def _compute_average(prices: List[Dict], days: int) -> Optional[float]:
    """Compute the average closing price over the last N trading days."""
    if not prices or len(prices) < days:
        # Use all available data if we have fewer days
        relevant = prices
    else:
        relevant = prices[-days:]

    if not relevant:
        return None

    closes = [p.get("close") or p.get("adjClose") or 0 for p in relevant]
    closes = [c for c in closes if c > 0]
    return sum(closes) / len(closes) if closes else None


def _compute_ytd_return(prices: List[Dict]) -> Optional[float]:
    """Compute year-to-date return."""
    if not prices or len(prices) < 2:
        return None

    current_year = datetime.now(tz=timezone.utc).year
    # Find the first trading day of the current year
    for p in prices:
        date_str = p.get("date") or ""
        if date_str.startswith(str(current_year)):
            start_price = p.get("close") or p.get("adjClose")
            end_price = prices[-1].get("close") or prices[-1].get("adjClose")
            if start_price and end_price and start_price > 0:
                return ((end_price - start_price) / start_price) * 100
            break
    return None


def _compute_index_pe_from_sectors() -> Optional[float]:
    """
    Compute aggregate index P/E from sector benchmark medians.

    Queries the sector_benchmarks table for pe_ratio entries,
    picks the most recent quarterly period that has >= 8 sectors,
    and returns the simple average across all sectors.
    """
    try:
        supabase = get_supabase()
        result = (
            supabase.table("sector_benchmarks")
            .select("sector, period_type, period_label, median_value")
            .eq("metric_name", "pe_ratio")
            .eq("period_type", "quarterly")
            .execute()
        )
        if not result.data:
            return None

        # Group by period_label
        from collections import defaultdict
        periods: Dict[str, List[float]] = defaultdict(list)
        for row in result.data:
            val = row.get("median_value")
            if val and val > 0:
                periods[row["period_label"]].append(val)

        # Sort periods descending (Q1'26 > Q4'25 > Q3'25 ...) and pick
        # the most recent one with >= 8 sectors
        def period_sort_key(label: str) -> str:
            # Labels are "Q1'25" / "Q4'24" (no space) — sort by year then quarter.
            # The old `.split()` (whitespace) returned a single token for the
            # spaceless label, so `q, y = ...` always raised and every label fell
            # back to a raw lexicographic sort that ordered Q4'24 ahead of Q1'25.
            try:
                q_part, y_part = label.replace("Q", "").split("'")
                return f"{int(y_part):02d}-{int(q_part):02d}"
            except Exception:
                return label

        for label in sorted(periods.keys(), key=period_sort_key, reverse=True):
            pes = periods[label]
            if len(pes) >= 8:
                avg_pe = sum(pes) / len(pes)
                logger.info(
                    f"Index PE computed from {len(pes)} sectors ({label}): {avg_pe:.2f}"
                )
                return round(avg_pe, 2)

        return None
    except Exception as e:
        logger.warning(f"Failed to compute index PE from sectors: {e}")
        return None


def _get_market_status() -> MarketStatusResponse:
    """Determine current market status based on time."""
    now = datetime.now(tz=timezone(timedelta(hours=-5)))  # EST
    hour = now.hour
    minute = now.minute
    weekday = now.weekday()  # 0=Monday, 6=Sunday

    if weekday >= 5:  # Weekend
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


# ── Main service ─────────────────────────────────────────────────


_INDEX_DB_TTL_HOURS = 24


class IndexService:
    """Aggregates FMP data + Gemini AI for the Index Detail screen."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()
        self.supabase = get_supabase()

    # ── Supabase cache-aside ────────────────────────────────────

    def _check_db_cache(self, symbol: str, chart_range: str) -> Optional[Dict]:
        """Check Supabase index_detail_cache (24h TTL)."""
        cache_key = f"{symbol}_{chart_range}"
        try:
            row = (
                self.supabase.table("index_detail_cache")
                .select("response_json, cached_at")
                .eq("cache_key", cache_key)
                .limit(1)
                .execute()
            )
            if not row.data:
                logger.info(f"Index cache MISS for {cache_key}")
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(
                cached_at_str.replace("Z", "+00:00")
            )
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=_INDEX_DB_TTL_HOURS):
                logger.info(
                    f"Index cache STALE (age={age}) for {cache_key}"
                )
                return None

            data = entry.get("response_json")
            if data and isinstance(data, dict):
                logger.info(f"Index cache HIT for {cache_key} (age={age})")
                return data
            return None
        except Exception as e:
            logger.warning(f"Index cache check failed for {cache_key}: {e}")
            return None

    def _upsert_db_cache(
        self, symbol: str, chart_range: str, data: Dict
    ) -> None:
        """Upsert index detail response into Supabase cache."""
        cache_key = f"{symbol}_{chart_range}"
        try:
            self.supabase.table("index_detail_cache").upsert(
                {
                    "cache_key": cache_key,
                    "symbol": symbol,
                    "chart_range": chart_range,
                    "response_json": data,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="cache_key",
            ).execute()
            logger.info(f"Index detail cached in Supabase for {cache_key}")
        except Exception as e:
            logger.warning(f"Index cache upsert failed for {cache_key}: {e}")

    # ── Macro forecast Supabase cache (7-day TTL) ────────────────

    def _check_macro_cache(self, symbol: str):
        """Check Supabase index_macro_forecast_cache (7-day TTL)."""
        try:
            row = (
                self.supabase.table("index_macro_forecast_cache")
                .select("story_template, indicators_json, cached_at")
                .eq("symbol", symbol.upper())
                .limit(1)
                .execute()
            )
            if not row.data:
                logger.info(f"Macro cache MISS for {symbol}")
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(
                cached_at_str.replace("Z", "+00:00")
            )
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(days=_MACRO_CACHE_TTL_DAYS):
                logger.info(f"Macro cache STALE (age={age}) for {symbol}")
                return None

            story = entry.get("story_template", "")
            indicators_raw = entry.get("indicators_json", [])
            if story and indicators_raw:
                indicators = []
                for item in indicators_raw:
                    signal = item.get("signal", "neutral").lower()
                    if signal not in ("positive", "neutral", "cautious"):
                        signal = "neutral"
                    indicators.append(MacroForecastItemResponse(
                        title=item.get("title", ""),
                        description=item.get("description", ""),
                        signal=signal,
                    ))
                logger.info(f"Macro cache HIT for {symbol} (age={age})")
                return story, indicators

            return None
        except Exception as e:
            logger.warning(f"Macro cache check failed for {symbol}: {e}")
            return None

    def _upsert_macro_cache(
        self, symbol: str, story: str, indicators: list
    ) -> None:
        """Save macro forecast to Supabase (7-day TTL)."""
        try:
            indicators_json = [
                {"title": i.title, "description": i.description, "signal": i.signal}
                for i in indicators
            ]
            self.supabase.table("index_macro_forecast_cache").upsert(
                {
                    "symbol": symbol.upper(),
                    "story_template": story,
                    "indicators_json": indicators_json,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="symbol",
            ).execute()
            logger.info(f"Macro forecast cached in Supabase for {symbol} (7-day TTL)")
        except Exception as e:
            logger.warning(f"Macro cache upsert failed for {symbol}: {e}")

    # ── Main entry point ────────────────────────────────────────

    async def get_index_detail(
        self, symbol: str, chart_range: str = "3M", interval: str = None
    ) -> IndexDetailResponse:
        """
        Fetch and assemble complete index detail data.

        Cache-aside: Check Supabase first. If hit & fresh (< 24h), return
        cached data. Otherwise fetch live, cache, and return.
        """
        # ── Step 0: Check Supabase cache ─────────────────────────
        cached = self._check_db_cache(symbol, chart_range)
        if cached:
            try:
                return IndexDetailResponse(**cached)
            except Exception as e:
                logger.warning(
                    f"Cached data deserialization failed for {symbol}: {e}"
                )
                # Fall through to live fetch

        profile_meta = _INDEX_PROFILES.get(
            symbol.upper(),
            _INDEX_PROFILES.get("^GSPC"),  # fallback to S&P 500
        )
        index_name = profile_meta.get("name", symbol)

        # ── Step 1: Parallel FMP fetches ──────────────────────────
        today = datetime.now(tz=timezone.utc).date()
        # Fetch 11 years of history for computing up to 10-year returns
        from_date = (today - timedelta(days=365 * 11)).isoformat()
        to_date = today.isoformat()

        # Chart-specific date range
        chart_from, chart_to = self._chart_date_range(chart_range)

        quote_task = self.fmp.get_stock_price_quote(symbol)
        hist_task = self.fmp.get_historical_prices(symbol, from_date, to_date)
        sector_task = self.fmp.get_sector_performance()
        constituents_task = self.fmp.get_index_constituents(symbol)

        quote, hist_raw, sector_raw, constituents_raw = await asyncio.gather(
            quote_task, hist_task, sector_task, constituents_task,
            return_exceptions=True,
        )

        # Handle exceptions gracefully
        if isinstance(quote, Exception):
            logger.error(f"Quote fetch failed for {symbol}: {quote}")
            quote = {}
        if isinstance(hist_raw, Exception):
            logger.error(f"Historical fetch failed for {symbol}: {hist_raw}")
            hist_raw = {}
        if isinstance(sector_raw, Exception):
            logger.error(f"Sector performance fetch failed: {sector_raw}")
            sector_raw = []
        if isinstance(constituents_raw, Exception):
            logger.warning(f"Constituents fetch failed for {symbol}: {constituents_raw}")
            constituents_raw = []

        # News is now fetched via separate GET /indices/{symbol}/news endpoint
        news_raw = []

        # Live constituent count (fall back to profile metadata)
        live_constituents = (
            len(constituents_raw) if isinstance(constituents_raw, list) and constituents_raw
            else profile_meta.get("number_of_constituents", 0)
        )

        # Parse historical prices (sorted oldest-first)
        historical = []
        if isinstance(hist_raw, dict):
            historical = hist_raw.get("historical", [])
        elif isinstance(hist_raw, list):
            historical = hist_raw
        historical.sort(key=lambda p: p.get("date") or "")

        # ── Step 2: Extract quote data ────────────────────────────
        if isinstance(quote, Exception) or not quote:
            logger.error(
                f"Quote data unavailable for {symbol}. "
                f"FMP returned: {type(quote).__name__}={quote}. "
                f"Check that FMP API key is valid and symbol '{symbol}' exists."
            )
        price = quote.get("price") or 0
        change = quote.get("change") or 0
        change_pct = quote.get("changesPercentage") or 0
        # FMP doesn't return PE for indices — compute from sector benchmarks
        pe = quote.get("pe") or _compute_index_pe_from_sectors() or 0
        eps = quote.get("eps") or 0
        prev_close = quote.get("previousClose") or 0
        open_price = quote.get("open") or 0
        day_high = quote.get("dayHigh") or 0
        day_low = quote.get("dayLow") or 0
        year_high = quote.get("yearHigh") or 0
        year_low = quote.get("yearLow") or 0
        volume = quote.get("volume") or 0
        avg_volume = quote.get("avgVolume") or 0
        market_cap = quote.get("marketCap") or 0

        # ── Step 3: Compute derived stats ─────────────────────────
        avg_50 = _compute_average(historical, 50)
        avg_200 = _compute_average(historical, 200)

        # Compute avg volume from historical if FMP doesn't provide it for indices
        if not avg_volume and historical:
            recent = historical[-30:]  # most recent 30 trading days
            vols = [d.get("volume", 0) for d in recent if d.get("volume")]
            avg_volume = int(sum(vols) / len(vols)) if vols else 0
        ytd_return = _compute_ytd_return(historical)
        one_month_return = _compute_return(historical, 21)
        one_year_return = _compute_return(historical, 252)
        three_year_return = _compute_return(historical, 252 * 3) if len(historical) > 252 * 3 else None
        five_year_return = _compute_return(historical, 252 * 5) if len(historical) > 252 * 5 else None
        ten_year_return = _compute_return(historical, 252 * 10) if len(historical) > 252 * 10 else None

        # Forward P/E estimate (simple: if PE is known, forward = PE * 0.85)
        forward_pe = pe * 0.85 if pe and pe > 0 else 0
        earnings_yield = (1 / pe * 100) if pe and pe > 0 else 0

        # ── Step 4: Build chart data ──────────────────────────────
        from app.services.chart_helper import fetch_chart_data, resolve_interval
        resolved = resolve_interval(chart_range, interval)
        if resolved != "daily" or chart_range == "ALL":
            raw_chart = await fetch_chart_data(self.fmp, symbol, chart_range, interval)
            chart_data = [
                ChartDataPointResponse(
                    date=p.get("date"),
                    open=p.get("open"),
                    high=p.get("high"),
                    low=p.get("low"),
                    close=round(float(p.get("close", 0)), 2),
                    volume=p.get("volume"),
                )
                for p in raw_chart
                if p.get("close") and float(p.get("close", 0)) > 0
            ]
        else:
            chart_data = self._extract_chart_data(historical, chart_range)

        # ── Step 5: Build key statistics ──────────────────────────
        key_stats = self._build_key_statistics(
            open_price=open_price,
            prev_close=prev_close,
            day_high=day_high,
            day_low=day_low,
            year_high=year_high,
            year_low=year_low,
            avg_50=avg_50,
            avg_200=avg_200,
            volume=volume,
            avg_volume=avg_volume,
            constituents=live_constituents,
        )

        # ── Step 6: Build performance periods ─────────────────────
        perf_periods = self._build_performance_periods(
            one_month=one_month_return,
            ytd=ytd_return,
            one_year=one_year_return,
            three_year=three_year_return,
            five_year=five_year_return,
            ten_year=ten_year_return,
        )

        # ── Step 7: Build snapshots (AI-enhanced) ─────────────────
        snapshots = await self._build_snapshots(
            symbol=symbol,
            pe=pe,
            forward_pe=forward_pe,
            earnings_yield=earnings_yield,
            historical_avg_pe=profile_meta.get("historical_avg_pe", 21),
            historical_period=profile_meta.get("historical_period", "10-year"),
            sector_raw=sector_raw if isinstance(sector_raw, list) else [],
            index_name=index_name,
        )

        # ── Step 8: Build profile ─────────────────────────────────
        profile = IndexProfileResponse(
            description=profile_meta.get("description", ""),
            exchange=profile_meta.get("exchange", ""),
            number_of_constituents=live_constituents,
            weighting_methodology=profile_meta.get("weighting_methodology", ""),
            inception_date=profile_meta.get("inception_date", ""),
            index_provider=profile_meta.get("index_provider", ""),
            website=profile_meta.get("website", ""),
        )

        # ── Step 9: Build news ────────────────────────────────────
        news_articles = self._build_news(news_raw if isinstance(news_raw, list) else [])

        # ── Step 10: Build benchmark summary ──────────────────────
        benchmark = BenchmarkSummaryResponse(
            avg_annual_return=profile_meta.get("avg_annual_return", 10.5),
            sp_benchmark=profile_meta.get("avg_annual_return", 10.5),
        )

        response = IndexDetailResponse(
            symbol=symbol.upper(),
            index_name=index_name,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
            market_status=_get_market_status(),
            chart_data=chart_data,
            key_statistics_groups=key_stats,
            performance_periods=perf_periods,
            snapshots_data=snapshots,
            index_profile=profile,
            benchmark_summary=benchmark,
            news_articles=news_articles,
        )

        # ── Step 11: Cache in Supabase ───────────────────────────
        try:
            self._upsert_db_cache(
                symbol.upper(), chart_range, response.model_dump()
            )
        except Exception as e:
            logger.warning(f"Failed to cache index detail for {symbol}: {e}")

        return response

    # ── Chart helpers ────────────────────────────────────────────

    def _chart_date_range(self, range_code: str) -> Tuple[Optional[str], str]:
        today = datetime.now(tz=timezone.utc).date()
        deltas = {
            "1W": timedelta(weeks=1),
            "3M": timedelta(days=90),
            "6M": timedelta(days=180),
            "1Y": timedelta(days=365),
            "5Y": timedelta(days=365 * 5),
        }
        if range_code == "ALL" or range_code == "1D":
            return None, today.isoformat()
        delta = deltas.get(range_code, timedelta(days=90))
        return (today - delta).isoformat(), today.isoformat()

    def _extract_chart_data(
        self, historical: List[Dict], chart_range: str
    ) -> List[ChartDataPointResponse]:
        """Extract OHLCV data for chart, filtered by range.

        Includes ~320 extra calendar days (~200 trading days) before the
        visible start so the frontend can compute MA(200) and other
        technical indicators during warm-up.  The frontend's
        ``TickerChartView.warmupCount`` hides these extra points from
        the displayed chart.
        """
        if not historical:
            return []

        # 320 calendar days ≈ 210 trading days — enough for MA(200)
        _WARMUP_CALENDAR_DAYS = 320

        today = datetime.now(tz=timezone.utc).date()
        range_days = {
            "1D": 2 + 7,
            "1W": 7 + 14,
            "3M": 90 + _WARMUP_CALENDAR_DAYS,
            "6M": 180 + _WARMUP_CALENDAR_DAYS,
            "1Y": 365 + _WARMUP_CALENDAR_DAYS,
            "5Y": 365 * 5 + _WARMUP_CALENDAR_DAYS,
            "ALL": 99999,
        }
        days = range_days.get(chart_range, 90 + _WARMUP_CALENDAR_DAYS)
        cutoff = (today - timedelta(days=days)).isoformat()

        result = []
        for p in historical:
            if (p.get("date") or "") >= cutoff:
                close = p.get("close") or p.get("adjClose")
                if close and close > 0:
                    result.append(ChartDataPointResponse(
                        date=p.get("date"),
                        open=p.get("open"),
                        high=p.get("high"),
                        low=p.get("low"),
                        close=round(float(close), 2),
                        volume=p.get("volume"),
                    ))
        return result

    # ── Key statistics builder ───────────────────────────────────

    def _build_key_statistics(
        self, *, open_price, prev_close, day_high, day_low,
        year_high, year_low, avg_50, avg_200,
        volume, avg_volume, constituents,
    ) -> List[KeyStatisticsGroupResponse]:
        return [
            # Column 1: Price & Breadth
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="Constituents", value=str(constituents)),
                KeyStatisticItem(label="Open", value=_fmt(open_price)),
                KeyStatisticItem(label="Previous Close", value=_fmt(prev_close)),
                KeyStatisticItem(label="Day High", value=_fmt(day_high)),
                KeyStatisticItem(label="Day Low", value=_fmt(day_low)),
            ]),
            # Column 2: Range & Volume
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="52-Week High", value=_fmt(year_high)),
                KeyStatisticItem(label="52-Week Low", value=_fmt(year_low)),
                KeyStatisticItem(label="200-Day Avg", value=_fmt(avg_200)),
                KeyStatisticItem(label="Volume", value=_fmt(volume, 0)),
                KeyStatisticItem(label="Avg. Volume (30D)", value=_fmt(avg_volume, 0)),
            ]),
        ]

    # ── Performance periods builder ──────────────────────────────

    def _build_performance_periods(
        self, *, one_month, ytd, one_year, three_year, five_year, ten_year,
    ) -> List[PerformancePeriodResponse]:
        periods = []
        for label, val in [
            ("1 Month", one_month),
            ("YTD", ytd),
            ("1 Year", one_year),
            ("3 Years", three_year),
            ("5 Years", five_year),
            ("10 Years", ten_year),
        ]:
            # Skip periods where data isn't available
            if val is None:
                continue
            periods.append(PerformancePeriodResponse(
                label=label,
                change_percent=round(val, 2),
                # An index IS the market — there is no separate benchmark return for
                # this window. Echoing the index's own return here made the UI render
                # a bogus "S&P: +X%" line where X was the index's OWN %. Leave unset
                # so the vs-market line is hidden (both sides are Optional).
                vs_market_percent=None,
            ))
        return periods

    # ── Snapshots builder (with Gemini AI) ───────────────────────

    async def _build_snapshots(
        self, *, symbol, pe, forward_pe, earnings_yield,
        historical_avg_pe, historical_period, sector_raw, index_name,
    ) -> IndexSnapshotsDataResponse:
        """Build the three snapshot cards with AI-generated story templates."""

        # Parse sector performance
        sectors = []
        for item in sector_raw:
            sector_name = item.get("sector", "")
            change = item.get("changesPercentage")
            if change is None:
                # Try string format
                change_str = str(item.get("changesPercentage", "0"))
                try:
                    change = float(change_str.replace("%", ""))
                except (ValueError, TypeError):
                    change = 0
            if sector_name:
                sectors.append(SectorPerformanceEntryResponse(
                    sector=sector_name,
                    change_percent=round(float(change), 2),
                ))

        # Sort sectors by change (best first)
        sectors.sort(key=lambda s: s.change_percent, reverse=True)

        # Determine valuation label
        if pe and pe > 0:
            if pe < 18:
                val_label = "Bargain"
            elif pe < 24:
                val_label = "Fair Value"
            elif pe < 30:
                val_label = "Expensive"
            else:
                val_label = "Overheated"
        else:
            val_label = "Unknown"

        # ── Generate AI stories (or use templates) ────────────────
        valuation_story, sector_story, macro_story, macro_indicators = (
            await self._generate_ai_stories(
                symbol=symbol,
                index_name=index_name,
                pe=pe,
                forward_pe=forward_pe,
                earnings_yield=earnings_yield,
                val_label=val_label,
                historical_avg_pe=historical_avg_pe,
                historical_period=historical_period,
                sectors=sectors,
            )
        )

        today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        return IndexSnapshotsDataResponse(
            valuation=ValuationSnapshotResponse(
                pe_ratio=round(pe, 1) if pe else 0,
                forward_pe=round(forward_pe, 1) if forward_pe else 0,
                earnings_yield=round(earnings_yield, 2) if earnings_yield else 0,
                historical_avg_pe=historical_avg_pe,
                historical_period=historical_period,
                story_template=valuation_story,
            ),
            sector_performance=SectorPerformanceSnapshotResponse(
                sectors=sectors[:11],  # Top 11 sectors
                story_template=sector_story,
            ),
            macro_forecast=MacroForecastSnapshotResponse(
                indicators=macro_indicators,
                story_template=macro_story,
            ),
            generated_date=today_str,
            generated_by="Gemini 2.0 Flash",
        )

    async def _generate_ai_stories(
        self, *, symbol, index_name, pe, forward_pe, earnings_yield,
        val_label, historical_avg_pe, historical_period, sectors,
    ) -> Tuple[str, str, str, List[MacroForecastItemResponse]]:
        """
        Try Gemini for AI-generated stories. Fall back to templates on failure.
        Macro forecast is cached in Supabase for 7 days (changes weekly).
        Valuation + Sector stories refresh on every cache miss (data-driven).
        Returns (valuation_story, sector_story, macro_story, macro_indicators).
        """
        cache_key = f"ai_stories_{symbol}"
        cached = _cache_get(cache_key)
        if cached:
            return cached

        # ── Check macro cache (7-day TTL in Supabase) ────────────
        macro_cached = self._check_macro_cache(symbol)
        has_macro = macro_cached is not None

        # ── Build default templates (always work) ─────────────────
        valuation_template = (
            f"The market is trading at {{PE_RATIO}} earnings, "
            f"which is considered {{VALUATION_LABEL}}. "
            f"That's {'a premium to' if pe and pe > historical_avg_pe else 'below'} "
            f"the {{HISTORICAL_PERIOD}} average of {{HISTORICAL_AVG_PE}} — "
            f"{'investors are pricing in strong future growth' if pe and pe > historical_avg_pe else 'suggesting potential value'}. "
            f"The forward P/E of {{FORWARD_PE}} tells a "
            f"{'slightly better' if forward_pe and forward_pe < pe else 'similar'} story, "
            f"suggesting analysts expect earnings to "
            f"{'catch up' if forward_pe and forward_pe < pe else 'remain steady'}."
        )

        advancing = sum(1 for s in sectors if s.change_percent >= 0)
        sector_template = (
            f"{{TOP_SECTOR}} led the way at {{TOP_SECTOR_CHANGE}}, "
            f"while {{BOTTOM_SECTOR}} lagged at {{BOTTOM_SECTOR_CHANGE}}. "
            f"{{ADVANCING_COUNT}} of {len(sectors)} sectors finished in the green "
            f"and {{DECLINING_COUNT}} declined"
            f"{' — breadth is solid.' if advancing > 7 else ' — breadth is narrow.'}"
        )

        # Default macro (used only if Gemini + Supabase both miss)
        default_macro_indicators = [
            MacroForecastItemResponse(
                title="GDP Growth",
                description="Economy expanding at a moderate pace with consumer spending and business investment remaining solid.",
                signal="positive",
            ),
            MacroForecastItemResponse(
                title="Inflation & Fed Policy",
                description="Core inflation moderating but above the 2% target. Fed maintaining data-dependent stance with rate adjustments expected.",
                signal="neutral",
            ),
            MacroForecastItemResponse(
                title="Labor Market",
                description="Unemployment near historic lows with steady job creation. Wage growth moderating but still above inflation.",
                signal="positive",
            ),
            MacroForecastItemResponse(
                title="Trade & Geopolitics",
                description="Global trade tensions and geopolitical uncertainty could pressure margins for globally exposed companies.",
                signal="cautious",
            ),
        ]

        macro_template = (
            "Macro outlook is constructive — {TOP_INDICATOR} is {TOP_SIGNAL}. "
            "We're watching {INDICATOR_COUNT} indicators. "
            "Growth is solid, but the Fed's next move and trade policy are the swing factors."
        )

        # Use cached macro if available
        if has_macro:
            macro_template, default_macro_indicators = macro_cached

        result = (valuation_template, sector_template, macro_template, default_macro_indicators)

        # ── Try Gemini for stories ────────────────────────────────
        try:
            gemini = get_gemini_client()

            sector_text = ", ".join(
                f"{s.sector} ({'+' if s.change_percent >= 0 else ''}{s.change_percent:.1f}%)"
                for s in sectors[:6]
            )

            # Build prompt — skip macro section if already cached
            if has_macro:
                prompt = f"""You are a financial analyst writing brief market commentary for the overall U.S. equity market.
IMPORTANT: Do NOT mention any specific index names like "S&P 500", "Dow Jones", or "Nasdaq". Use "the market" instead.

Current data:
- P/E Ratio (TTM): {pe:.1f}x
- Forward P/E: {forward_pe:.1f}x
- Earnings Yield: {earnings_yield:.2f}%
- Historical Avg P/E ({historical_period}): {historical_avg_pe}x
- Valuation Level: {val_label}
- Top sectors today: {sector_text}
- Advancing sectors: {advancing} of {len(sectors)}

Generate exactly 2 items separated by "---":

1. VALUATION STORY (2-3 sentences about the market valuation — do NOT name any index. Use "the market" instead. Mention the P/E ratio, how it compares to historical average, and forward outlook. Use these placeholders in your text: {{PE_RATIO}}, {{FORWARD_PE}}, {{EARNINGS_YIELD}}, {{VALUATION_LABEL}}, {{HISTORICAL_AVG_PE}}, {{HISTORICAL_PERIOD}})

---

2. SECTOR STORY (2-3 sentences about sector performance.
CRITICAL: You MUST use these exact placeholder tokens — do NOT replace them with actual sector names or numbers:
  {{TOP_SECTOR}} = best performing sector name
  {{TOP_SECTOR_CHANGE}} = its % change (e.g. "+1.6%")
  {{BOTTOM_SECTOR}} = worst performing sector name
  {{BOTTOM_SECTOR_CHANGE}} = its % change (e.g. "-1.5%")
  {{ADVANCING_COUNT}} = number of sectors in the green
  {{DECLINING_COUNT}} = number of sectors in the red
CORRECT example: "{{TOP_SECTOR}} led at {{TOP_SECTOR_CHANGE}}, while {{BOTTOM_SECTOR}} lagged."
WRONG example: "Technology led at +0.8%." — NEVER hardcode names or values!)

Write in a conversational, confident tone. Be specific and data-driven."""
            else:
                prompt = f"""You are a financial analyst writing brief market commentary for the overall U.S. equity market.
IMPORTANT: Do NOT mention any specific index names like "S&P 500", "Dow Jones", or "Nasdaq". Use "the market" instead.

Current data:
- P/E Ratio (TTM): {pe:.1f}x
- Forward P/E: {forward_pe:.1f}x
- Earnings Yield: {earnings_yield:.2f}%
- Historical Avg P/E ({historical_period}): {historical_avg_pe}x
- Valuation Level: {val_label}
- Top sectors today: {sector_text}
- Advancing sectors: {advancing} of {len(sectors)}

Generate exactly 3 items separated by "---":

1. VALUATION STORY (2-3 sentences about the market valuation — do NOT name any index. Use "the market" instead. Mention the P/E ratio, how it compares to historical average, and forward outlook. Use these placeholders in your text: {{PE_RATIO}}, {{FORWARD_PE}}, {{EARNINGS_YIELD}}, {{VALUATION_LABEL}}, {{HISTORICAL_AVG_PE}}, {{HISTORICAL_PERIOD}})

---

2. SECTOR STORY (2-3 sentences about sector performance.
CRITICAL: You MUST use these exact placeholder tokens — do NOT replace them with actual sector names or numbers:
  {{TOP_SECTOR}} = best performing sector name
  {{TOP_SECTOR_CHANGE}} = its % change (e.g. "+1.6%")
  {{BOTTOM_SECTOR}} = worst performing sector name
  {{BOTTOM_SECTOR_CHANGE}} = its % change (e.g. "-1.5%")
  {{ADVANCING_COUNT}} = number of sectors in the green
  {{DECLINING_COUNT}} = number of sectors in the red
CORRECT example: "{{TOP_SECTOR}} led at {{TOP_SECTOR_CHANGE}}, while {{BOTTOM_SECTOR}} lagged."
WRONG example: "Technology led at +0.8%." — NEVER hardcode names or values!)

---

3. MACRO ITEMS (provide 4 current economic indicators as a JSON array. Each indicator should have a 1-2 sentence analysis based on the latest economic data):
[{{"title": "indicator name", "description": "1-2 sentence analysis", "signal": "positive|neutral|cautious"}}]

Write in a conversational, confident tone. Be specific and data-driven."""

            ai_response = await gemini.generate_text(
                prompt=prompt,
                system_instruction=(
                    "You are a senior market strategist providing concise, insightful commentary. "
                    "Keep stories to 2-3 sentences. Use the placeholder tokens exactly as given."
                ),
                model_name="gemini-2.5-flash",
            )

            text = ai_response.get("text", "")
            parts = text.split("---")

            min_parts = 2 if has_macro else 3
            if len(parts) >= min_parts:
                ai_valuation = parts[0].strip()
                ai_sector = parts[1].strip()

                # Clean up section headers
                for header in ["1.", "2.", "3.", "VALUATION STORY", "SECTOR STORY", "MACRO ITEMS"]:
                    ai_valuation = ai_valuation.replace(header, "").strip()
                    ai_sector = ai_sector.replace(header, "").strip()

                if ai_valuation and len(ai_valuation) > 20:
                    if "{PE_RATIO}" in ai_valuation and "{VALUATION_LABEL}" in ai_valuation:
                        valuation_template = ai_valuation
                    else:
                        logger.warning("Gemini valuation story missing placeholders, using default")

                if ai_sector and len(ai_sector) > 20:
                    has_placeholders = (
                        "{TOP_SECTOR}" in ai_sector
                        and "{ADVANCING_COUNT}" in ai_sector
                    )
                    if has_placeholders:
                        sector_template = ai_sector
                    else:
                        logger.warning("Gemini sector story missing placeholders, using default")

                # Parse macro from Gemini only if NOT cached
                if not has_macro and len(parts) >= 3:
                    ai_macro_raw = parts[2].strip()
                    for header in ["3.", "MACRO ITEMS"]:
                        ai_macro_raw = ai_macro_raw.replace(header, "").strip()

                    import json
                    try:
                        json_start = ai_macro_raw.find("[")
                        json_end = ai_macro_raw.rfind("]") + 1
                        if json_start >= 0 and json_end > json_start:
                            macro_json = json.loads(ai_macro_raw[json_start:json_end])
                            if isinstance(macro_json, list) and len(macro_json) >= 3:
                                parsed_indicators = []
                                for item in macro_json[:4]:
                                    signal = item.get("signal", "neutral").lower()
                                    if signal not in ("positive", "neutral", "cautious"):
                                        signal = "neutral"
                                    parsed_indicators.append(MacroForecastItemResponse(
                                        title=item.get("title", ""),
                                        description=item.get("description", ""),
                                        signal=signal,
                                    ))
                                if parsed_indicators:
                                    default_macro_indicators = parsed_indicators
                                    # Cache macro in Supabase for 7 days
                                    self._upsert_macro_cache(
                                        symbol, macro_template, parsed_indicators
                                    )
                    except (json.JSONDecodeError, KeyError, TypeError) as e:
                        logger.warning(f"Failed to parse Gemini macro indicators: {e}")

                result = (valuation_template, sector_template, macro_template, default_macro_indicators)
                _cache_set(cache_key, result, _AI_CACHE_TTL_SECONDS)
                logger.info(f"Generated AI stories for {symbol} (macro={'cached' if has_macro else 'fresh'})")

        except Exception as e:
            logger.warning(f"Gemini story generation failed for {symbol}, using templates: {e}")

        return result

    # ── News builder ─────────────────────────────────────────────

    def _build_news(
        self, raw_articles: List[Dict],
    ) -> List[IndexNewsArticleResponse]:
        articles = []
        for item in raw_articles[:10]:
            published = item.get("publishedDate") or item.get("published_date") or ""
            articles.append(IndexNewsArticleResponse(
                headline=item.get("title") or item.get("headline") or "",
                source_name=item.get("site") or item.get("source") or "Unknown",
                source_icon=None,
                sentiment="neutral",
                published_at=published,
                thumbnail_url=item.get("image") or item.get("thumbnail_url"),
                related_tickers=[s.strip() for s in (item.get("symbol") or "").split(",") if s.strip()],
                summary_bullets=[],
                article_url=item.get("url") or item.get("article_url"),
            ))
        return articles


# ── Singleton ────────────────────────────────────────────────────

_index_service: Optional[IndexService] = None


def get_index_service() -> IndexService:
    global _index_service
    if _index_service is None:
        _index_service = IndexService()
    return _index_service
