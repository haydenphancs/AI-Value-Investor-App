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
from app.utils.inflight import fail_shared_future
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


# Hard cap on the in-memory tier. Without it this dict grew with the number of DISTINCT
# keys ever requested and was never pruned: `_cache_get` only deletes an entry when that
# SAME key is read again after expiry, so a ticker fetched once and never revisited stayed
# resident for the life of the process. Across ~17 services on a long-lived Railway
# container that is a slow leak whose only resolution is an OOM restart — which drops every
# in-flight report with it. Bounded LRU-ish: evict from the head (least recently WRITTEN).
_CACHE_MAX_ENTRIES = 1024


def _cache_set(key: str, value: Any) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


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

    Blends a sector-RELATIVE read with an ABSOLUTE-growth floor: a metric growing
    strongly in absolute terms is never scored "weak" merely because a thin /
    contaminated latest-period sector benchmark is even higher. Without this, a
    65%-grower whose semiconductor peers' FY benchmark reads an (uncredible) 79%
    YoY scored a 1/5 ("below sector") — see the persona-scoring validation. The
    relative read still drives the UPSIDE (true outperformers reach 5).
    """
    if value is None:
        return 3  # neutral if no data

    # Absolute floor: strong absolute growth can't read as weak regardless of peers.
    abs_floor = 4 if value >= 40 else 3 if value >= 20 else 1

    if sector_median is None:
        # Absolute-only when no sector benchmark.
        if value > 20:
            rel = 5
        elif value > 10:
            rel = 4
        elif value > 0:
            rel = 3
        elif value > -10:
            rel = 2
        else:
            rel = 1
        return max(rel, abs_floor)

    diff = value - sector_median  # percentage points above/below sector
    if diff > 10:
        rel = 5  # 10pp+ above sector
    elif diff > 3:
        rel = 4  # 3-10pp above
    elif diff > -3:
        rel = 3  # within 3pp of sector
    elif diff > -10:
        rel = 2  # 3-10pp below
    else:
        rel = 1  # 10pp+ below sector
    return max(rel, abs_floor)


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
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Growth snapshot cache MISS for {ticker} — computing")
            result, degraded = await self._compute_with_status(ticker)

            # NEVER persist a degraded build. GrowthService turns a failed FMP leg into an
            # empty series and refuses to write that build to its own 24h tier; this card
            # would render the hole as "—" scored with the neutral sentinel 3 and then pin
            # that made-up rating in snapshot_cache for a day (the Overview card and the
            # report's snap_growth both read it). Serve it, keep the 5-min memory tier to
            # absorb the retry storm, and let the next miss rebuild. Mirrors
            # valuation_snapshot_service.
            if degraded:
                logger.warning(
                    "Growth snapshot NOT persisted for %s (degraded: %s) — will rebuild "
                    "after the in-memory TTL", ticker, ", ".join(degraded),
                )
            else:
                asyncio.get_running_loop().run_in_executor(
                    None, self._upsert_supabase_cache, ticker, result,
                )

            _cache_set(cache_key, result)
            if not future.done():
                future.set_result(result)
            return result
        except asyncio.CancelledError:
            # CancelledError is a BaseException, NOT an Exception, so it skips the handler
            # below and used to leave this future unresolved forever — every joiner attached
            # via `await _inflight[...]` then hung for the life of the process. Reachable
            # whenever the LEADER is a cancellable caller: a report run hitting
            # RESEARCH_PIPELINE_TIMEOUT_SECONDS, or any pre-warm task cancelled at shutdown.
            # Hand waiters a normal exception so they fail fast through their own error path.
            fail_shared_future(future, RuntimeError("in-flight fetch was cancelled"))
            raise
        except Exception as e:
            fail_shared_future(future, e)
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
        """`_compute_with_status` without the degradation list."""
        snapshot, _degraded = await self._compute_with_status(ticker)
        return snapshot

    async def _compute_with_status(
        self, ticker: str,
    ) -> Tuple[SnapshotItemResponse, List[str]]:
        """Reuse GrowthService (Financials tab) to get exact same data the user sees.

        Returns ``(snapshot, degraded)``. ``degraded`` carries GrowthService's own list of
        failed FMP legs for the build it served, plus ``"no_values"`` when none of the
        four metrics has a value (every score is then the neutral sentinel 3).
        `get_growth_snapshot` refuses to persist a build with a non-empty list.
        """
        from app.services.growth_service import get_growth_service

        growth, upstream_degraded = await get_growth_service().get_growth_with_status(ticker)
        degraded: List[str] = list(upstream_degraded)

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

        if all(v is None for v in (rev_growth, eps_growth, fcf_growth, op_growth)):
            degraded.append("no_values")

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

        snapshot = SnapshotItemResponse(
            category="Growth",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
            weighted_score=round(weighted, 3),
        )
        return snapshot, degraded


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[GrowthSnapshotService] = None


def get_growth_snapshot_service() -> GrowthSnapshotService:
    global _service
    if _service is None:
        _service = GrowthSnapshotService()
    return _service
