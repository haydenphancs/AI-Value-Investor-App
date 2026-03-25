"""
Financial Health Snapshot service — computes sector-relative health ratings
by reusing the existing HealthCheckService (Financials tab) to ensure
data consistency.

Extracts the overall rating and 4 metrics (Debt-to-Equity, P/E Ratio,
Return on Equity, Current Ratio) from the health check, maps them to
the 1-5 snapshot rating, and formats for display.

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
from app.integrations.fmp import get_fmp_client
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


# ── Display helpers ──────────────────────────────────────────────

# Map health check metric types to display names
_DISPLAY_NAMES = {
    "debt_to_equity": "Debt-to-Equity",
    "pe_ratio": "P/E Ratio",
    "roe": "Return on Equity (ROE)",
    "current_ratio": "Current Ratio",
}

# Map health check overall_rating to 1-5 snapshot rating
_RATING_MAP = {
    "excellent": 5,
    "good": 4,
    "mix": 3,
    "caution": 2,
    "poor": 1,
}

# Metrics where the value is a percentage (ROE)
_PCT_METRICS = {"roe"}


def _fmt_value(metric_type: str, value: Optional[float]) -> str:
    """Format metric value for display."""
    if value is None:
        return "—"
    if metric_type in _PCT_METRICS:
        return f"{value:.2f}%"
    return f"{value:.2f}"


def _metric_name(metric_type: str, value: Optional[float], comparison_value: Optional[float]) -> str:
    """Build metric name with optional sector context."""
    label = _DISPLAY_NAMES.get(metric_type, metric_type)
    if value is not None and comparison_value is not None:
        return f"{label} (vs sector {comparison_value:.2f})"
    return label


def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _compute_z_score(bs: Dict, inc: Dict, mcap: Optional[float]) -> Optional[float]:
    """Compute Altman Z-Score from balance sheet, income, and market cap."""
    ta = _safe_float(bs, "totalAssets")
    tl = _safe_float(bs, "totalLiabilities")
    ca = _safe_float(bs, "totalCurrentAssets")
    cl = _safe_float(bs, "totalCurrentLiabilities")
    re = _safe_float(bs, "retainedEarnings")
    ebit = _safe_float(inc, "operatingIncome")
    rev = _safe_float(inc, "revenue")

    if not ta or ta <= 0 or not tl or tl <= 0:
        return None

    wc = (ca or 0) - (cl or 0)
    z = (
        1.2 * (wc / ta)
        + 1.4 * ((re or 0) / ta)
        + 3.3 * ((ebit or 0) / ta)
        + 0.6 * ((mcap or 0) / tl)
        + 1.0 * ((rev or 0) / ta)
    )
    return round(z, 1)


def _zscore_rating(z: Optional[float]) -> int:
    """Score Z-Score 1-5 using Altman's universal thresholds."""
    if z is None:
        return 3
    if z > 3.0:
        return 5  # Safe zone
    if z > 2.5:
        return 4
    if z > 1.8:
        return 3  # Grey zone
    if z > 1.0:
        return 2
    return 1      # Distress zone


# ── Service ───────────────────────────────────────────────────────

class HealthSnapshotService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_health_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"health_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Health snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Health snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Health snapshot in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Health snapshot cache MISS for {ticker} — computing")
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
        try:
            row = (
                self.supabase.table("snapshot_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .eq("category", "Financial Health")
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
                logger.info(f"Health snapshot Supabase STALE (age={age}) for {ticker}")
                return None

            json_data = entry["response_json"]
            return SnapshotItemResponse(**json_data)

        except Exception as e:
            logger.warning(f"Health snapshot cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        try:
            self.supabase.table("snapshot_cache").upsert(
                {
                    "ticker": ticker,
                    "category": "Financial Health",
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,category",
            ).execute()
        except Exception as e:
            logger.warning(f"Health snapshot upsert failed for {ticker}: {e}")

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """Reuse HealthCheckService (Financials tab) + compute Altman Z-Score."""
        from app.services.health_check_service import get_health_check_service

        # Fetch health check + Z-Score data in parallel
        health_task = get_health_check_service().get_health_check(ticker)
        bs_task = self.fmp.get_balance_sheet(ticker, period="annual", limit=1)
        inc_task = self.fmp.get_income_statement(ticker, period="annual", limit=1)
        profile_task = self.fmp.get_company_profile(ticker)

        results = await asyncio.gather(
            health_task, bs_task, inc_task, profile_task, return_exceptions=True,
        )

        health = results[0] if not isinstance(results[0], Exception) else None
        bs_raw = results[1] if not isinstance(results[1], Exception) else []
        inc_raw = results[2] if not isinstance(results[2], Exception) else []
        profile_raw = results[3] if not isinstance(results[3], Exception) else {}

        # Parse data
        bs = bs_raw[0] if isinstance(bs_raw, list) and bs_raw else {}
        inc = inc_raw[0] if isinstance(inc_raw, list) and inc_raw else {}
        profile = {}
        if isinstance(profile_raw, dict):
            profile = profile_raw
        elif isinstance(profile_raw, list) and profile_raw:
            profile = profile_raw[0]

        mcap = _safe_float(profile, "mktCap")
        if mcap is None:
            mcap = _safe_float(profile, "marketCap")

        # Build health check metrics
        health_rating = 3
        metrics: List[SnapshotMetricResponse] = []
        if health is not None:
            health_rating = _RATING_MAP.get(health.overall_rating, 3)
            for m in health.metrics:
                name = _metric_name(m.type, m.value, m.comparison_value)
                value = _fmt_value(m.type, m.value)
                metrics.append(SnapshotMetricResponse(name=name, value=value))

        # Compute Altman Z-Score
        z_score = _compute_z_score(bs, inc, mcap)

        # Add Z-Score as metric (uses Altman's universal thresholds, no sector benchmark needed)
        z_value = f"{z_score}" if z_score is not None else "—"
        metrics.append(SnapshotMetricResponse(name="Altman Z-Score", value=z_value))

        # Blend rating: 60% health check + 40% Z-Score
        z_rating = _zscore_rating(z_score)
        rating = max(1, min(5, round(0.6 * health_rating + 0.4 * z_rating)))

        if not metrics:
            metrics.append(SnapshotMetricResponse(name="Financial Health", value="—"))

        return SnapshotItemResponse(
            category="Financial Health",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
        )


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[HealthSnapshotService] = None


def get_health_snapshot_service() -> HealthSnapshotService:
    global _service
    if _service is None:
        _service = HealthSnapshotService()
    return _service
