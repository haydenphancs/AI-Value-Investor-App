"""
Growth Snapshot service — computes sector-relative growth ratings by
reusing the existing GrowthService (Financials tab) to ensure data consistency.

Extracts the most recent annual YoY growth for Revenue, EPS, FCF, and
Operating Income, along with their sector benchmarks, then scores 1-5.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``snapshot_cache`` table (24-hour TTL)

Matches the iOS SnapshotItemDTO struct.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse

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

def _fmt_growth(val: Optional[float]) -> str:
    """Format growth as +X.X% or -X.X% string."""
    if val is None:
        return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _growth_score(value: Optional[float], sector_median: Optional[float]) -> int:
    """
    Score 1-5 based on how a company's growth compares to sector median.
    Both value and sector_median are in percentage points (e.g., 12.5 = 12.5%).
    """
    if value is None:
        return 3  # neutral if no data

    if sector_median is None:
        # Absolute fallback when no sector benchmark
        if value > 20:
            return 5
        if value > 10:
            return 4
        if value > 0:
            return 3
        if value > -10:
            return 2
        return 1

    diff = value - sector_median  # percentage points above/below sector
    if diff > 10:
        return 5  # 10pp+ above sector
    if diff > 3:
        return 4  # 3-10pp above
    if diff > -3:
        return 3  # within 3pp of sector
    if diff > -10:
        return 2  # 3-10pp below
    return 1      # 10pp+ below sector


# ── Service ───────────────────────────────────────────────────────

class GrowthSnapshotService:
    def __init__(self):
        self.supabase = get_supabase()

    async def get_growth_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"growth_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Growth snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Growth snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Growth snapshot in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Growth snapshot cache MISS for {ticker} — computing")
            result = await self._compute(ticker)

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
        try:
            row = (
                self.supabase.table("snapshot_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .eq("category", "Growth")
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
                logger.info(f"Growth snapshot Supabase STALE (age={age}) for {ticker}")
                return None

            json_data = entry["response_json"]
            return SnapshotItemResponse(**json_data)

        except Exception as e:
            logger.warning(f"Growth snapshot cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        try:
            self.supabase.table("snapshot_cache").upsert(
                {
                    "ticker": ticker,
                    "category": "Growth",
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,category",
            ).execute()
        except Exception as e:
            logger.warning(f"Growth snapshot upsert failed for {ticker}: {e}")

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """Reuse GrowthService (Financials tab) to get exact same data the user sees."""
        from app.services.growth_service import get_growth_service

        growth = await get_growth_service().get_growth(ticker)

        # Extract the most recent annual YoY + sector benchmark for each metric.
        # GrowthResponse lists are sorted oldest→newest. Walk backwards to find
        # the most recent point with a non-None yoy_change_percent (handles cases
        # where prior year's value was 0, making YoY computation impossible).
        def _latest(points) -> Tuple[Optional[float], Optional[float]]:
            """Return (yoy_change_percent, sector_average_yoy) from most recent valid point."""
            if not points:
                return None, None
            for pt in reversed(points):
                if pt.yoy_change_percent is not None:
                    return pt.yoy_change_percent, pt.sector_average_yoy
            return None, None

        rev_growth, sector_rev = _latest(growth.revenue_annual)
        eps_growth, sector_eps = _latest(growth.eps_annual)
        fcf_growth, sector_fcf = _latest(growth.free_cash_flow_annual)
        op_growth, sector_op = _latest(growth.operating_profit_annual)

        # Score each metric against sector median
        score_rev = _growth_score(rev_growth, sector_rev)
        score_eps = _growth_score(eps_growth, sector_eps)
        score_fcf = _growth_score(fcf_growth, sector_fcf)
        score_op = _growth_score(op_growth, sector_op)

        # Weighted average: Revenue 30%, EPS 30%, FCF 20%, Op Income 20%
        weighted = (score_rev * 0.30) + (score_eps * 0.30) + (score_fcf * 0.20) + (score_op * 0.20)
        rating = max(1, min(5, round(weighted)))

        metrics = [
            SnapshotMetricResponse(name="Revenue Growth (YoY)", value=_fmt_growth(rev_growth),
                                   metric_key="revenue_growth", score=score_rev if rev_growth is not None else None),
            SnapshotMetricResponse(name="EPS Growth", value=_fmt_growth(eps_growth),
                                   metric_key="eps_growth", score=score_eps if eps_growth is not None else None),
            SnapshotMetricResponse(name="Free Cash Flow Growth (YoY)", value=_fmt_growth(fcf_growth),
                                   metric_key="fcf_growth", score=score_fcf if fcf_growth is not None else None),
            SnapshotMetricResponse(name="Operating Income Growth", value=_fmt_growth(op_growth),
                                   metric_key="operating_income_growth", score=score_op if op_growth is not None else None),
        ]

        return SnapshotItemResponse(
            category="Growth",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
            weighted_score=round(weighted, 3),
        )


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[GrowthSnapshotService] = None


def get_growth_snapshot_service() -> GrowthSnapshotService:
    global _service
    if _service is None:
        _service = GrowthSnapshotService()
    return _service
