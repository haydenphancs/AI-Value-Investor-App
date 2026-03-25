"""
Profitability Snapshot service — computes sector-relative profitability
ratings using Operating Margin, Net Margin, ROE, ROA compared against
pre-computed sector medians from the sector_benchmarks table.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``snapshot_profitability_cache`` table (24-hour TTL)

Matches the iOS SnapshotItemDTO struct.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services.sector_benchmark_lookup import get_sector_benchmark_lookup
from app.services.sector_benchmark_service import _normalize_sector

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


# ── In-flight deduplication ───────────────────────────────────────
_inflight: Dict[str, asyncio.Future] = {}

# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_pct(val: Optional[float]) -> Optional[float]:
    """Convert decimal to percentage if needed. FMP ratios are decimals (0.25 = 25%)."""
    if val is None:
        return None
    if abs(val) < 1:
        return round(val * 100, 2)
    return round(val, 2)


def _fmt_pct(val: Optional[float]) -> str:
    """Format as percentage string for display."""
    if val is None:
        return "—"
    return f"{val:.2f}%"


def _profitability_score(value: Optional[float], sector_median_decimal: Optional[float]) -> int:
    """
    Score 1-5 based on how a company's metric compares to sector median.

    Args:
        value: Company metric as percentage (e.g., 25.0 for 25%)
        sector_median_decimal: Sector median as decimal from benchmarks (e.g., 0.15 for 15%)
    """
    if value is None:
        return 3  # neutral if no data

    if sector_median_decimal is None or sector_median_decimal == 0:
        # No sector benchmark — use absolute thresholds as fallback
        if value >= 20:
            return 5
        if value >= 12:
            return 4
        if value >= 5:
            return 3
        if value >= 0:
            return 2
        return 1

    sector_pct = sector_median_decimal * 100  # convert to percentage
    if sector_pct == 0:
        return 3

    ratio = value / sector_pct
    if ratio >= 1.5:
        return 5  # 50%+ above sector
    if ratio >= 1.1:
        return 4  # 10-50% above
    if ratio >= 0.8:
        return 3  # within 20% of sector
    if ratio >= 0.5:
        return 2  # 20-50% below
    return 1      # 50%+ below sector


def _get_latest_benchmark(benchmarks: Dict[str, Dict[str, float]], metric: str) -> Optional[float]:
    """Get the most recent year's benchmark value for a metric."""
    metric_data = benchmarks.get(metric, {})
    if not metric_data:
        return None
    # Sort by year descending, pick the most recent
    latest_year = max(metric_data.keys())
    return metric_data[latest_year]


# ── Service ───────────────────────────────────────────────────────

class ProfitabilitySnapshotService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_profitability_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"prof_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Profitability snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Profitability snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Profitability snapshot in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Profitability snapshot cache MISS for {ticker} — computing")
            result = await self._compute(ticker)

            # Persist to Supabase in background thread
            asyncio.get_running_loop().run_in_executor(
                None, self._upsert_supabase_cache, ticker, result,
            )

            _cache_set(cache_key, result)
            future.set_result(result)
            return result
        except Exception as e:
            future.set_exception(e)
            raise
        finally:
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ──────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[SnapshotItemResponse]:
        """Return cached response if fresh (< 24h). Synchronous — call via to_thread."""
        try:
            row = (
                self.supabase.table("snapshot_profitability_cache")
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
            if age > timedelta(hours=24):
                logger.info(f"Profitability snapshot Supabase STALE (age={age}) for {ticker}")
                return None

            json_data = entry["response_json"]
            return SnapshotItemResponse(**json_data)

        except Exception as e:
            logger.warning(f"Profitability snapshot cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        """Upsert to Supabase. Synchronous — call via run_in_executor."""
        try:
            self.supabase.table("snapshot_profitability_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Profitability snapshot upsert failed for {ticker}: {e}")

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """Reuse ProfitPowerService (Financials tab) for margins, FMP for ROE/ROA."""
        from app.services.profit_power_service import get_profit_power_service

        # Fetch margins from profit_power (same data as Financials tab) + ROE/ROA from FMP key-metrics
        pp_task = get_profit_power_service().get_profit_power(ticker)
        km_task = self.fmp.get_key_metrics(ticker, period="annual", limit=1)
        profile_task = self.fmp.get_company_profile(ticker)

        results = await asyncio.gather(
            pp_task, km_task, profile_task, return_exceptions=True
        )

        # Margins from profit_power (exact same as Financials tab)
        pp = results[0] if not isinstance(results[0], Exception) else None
        op_margin = None
        net_margin = None
        if pp and pp.annual:
            latest = pp.annual[-1]  # sorted oldest→newest
            op_margin = latest.operating_margin
            net_margin = latest.net_margin

        # ROE/ROA from FMP key-metrics (ratios endpoint returns None for these)
        km_raw = results[1]
        km = {}
        if isinstance(km_raw, list) and km_raw:
            km = km_raw[0]
        elif isinstance(km_raw, dict):
            km = km_raw

        roe = _to_pct(_safe_float(km, "returnOnEquity"))
        roa = _to_pct(_safe_float(km, "returnOnAssets") or _safe_float(km, "returnOnTangibleAssets"))

        # Sector for benchmark comparison
        profile_raw = results[2]
        profile = {}
        if isinstance(profile_raw, dict):
            profile = profile_raw
        elif isinstance(profile_raw, list) and profile_raw:
            profile = profile_raw[0]

        raw_sector = profile.get("sector", "")
        sector = _normalize_sector(raw_sector) if raw_sector else ""

        benchmarks: Dict[str, Dict[str, float]] = {}
        if sector:
            try:
                lookup = get_sector_benchmark_lookup()
                benchmarks = lookup.get_sector_benchmarks(
                    sector,
                    ["operating_margin", "net_margin", "roe", "roa"],
                    "annual",
                )
            except Exception as e:
                logger.warning(f"Sector benchmark lookup failed for {ticker}: {e}")

        # Score each metric against sector median
        sector_op = _get_latest_benchmark(benchmarks, "operating_margin")
        sector_net = _get_latest_benchmark(benchmarks, "net_margin")
        sector_roe = _get_latest_benchmark(benchmarks, "roe")
        sector_roa = _get_latest_benchmark(benchmarks, "roa")

        score_op = _profitability_score(op_margin, sector_op)
        score_net = _profitability_score(net_margin, sector_net)
        score_roe = _profitability_score(roe, sector_roe)
        score_roa = _profitability_score(roa, sector_roa)

        # Weighted average: Op Margin 25%, Net Margin 25%, ROE 30%, ROA 20%
        weighted = (score_op * 0.25) + (score_net * 0.25) + (score_roe * 0.30) + (score_roa * 0.20)
        rating = max(1, min(5, round(weighted)))

        metrics = [
            SnapshotMetricResponse(name="Operating Margin", value=_fmt_pct(op_margin)),
            SnapshotMetricResponse(name="Net Margin", value=_fmt_pct(net_margin)),
            SnapshotMetricResponse(name="Return on Equity (ROE)", value=_fmt_pct(roe)),
            SnapshotMetricResponse(name="Return on Assets (ROA)", value=_fmt_pct(roa)),
        ]

        return SnapshotItemResponse(
            category="Profitability",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
        )


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[ProfitabilitySnapshotService] = None


def get_profitability_snapshot_service() -> ProfitabilitySnapshotService:
    global _service
    if _service is None:
        _service = ProfitabilitySnapshotService()
    return _service
