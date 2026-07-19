"""
Revenue Breakdown service — fetches product segmentation + income statement
from FMP, groups small segments into "Other", and caches the result in
Supabase for 24 hours (or until next earnings date).

Matches the iOS RevenueBreakdownData struct.
"""

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.schemas.revenue_breakdown import (
    RevenueBreakdownResponse,
    RevenueSourceSchema,
)

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
# Prevents thundering herd: if two requests arrive for the same ticker
# while the cache is cold, only one FMP fetch runs; the other awaits.
_inflight: Dict[str, asyncio.Future] = {}


# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    """Validate and normalize ticker symbol. Raises ValueError if invalid."""
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str, default: float = 0.0) -> float:
    """Safely extract a float value, returning *default* on None/error."""
    val = record.get(key)
    if val is None:
        return default
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (ValueError, TypeError):
        return default


# Keys that are metadata, not segment names, in FMP segmentation response
_SEGMENT_META_KEYS = {"date", "symbol", "reportedCurrency", "cik", "fillingDate",
                      "acceptedDate", "calendarYear", "period", "link", "finalLink",
                      "fiscalYear", "data"}

# Minimum percentage of total revenue for a segment to keep its own name
_OTHER_THRESHOLD_PCT = 5.0


def _extract_segments(record: Dict[str, Any]) -> List[RevenueSourceSchema]:
    """
    Extract revenue segments from an FMP product-segmentation record.

    FMP stable API returns:
      {"symbol": "AAPL", "fiscalYear": 2025, "date": "...", "data": {"iPhone": 209586000000, ...}}
    Segments live inside the nested "data" dict.
    Falls back to flat-key extraction if "data" is absent.

    IMPORTANT: The nested "data" dict may also contain metadata keys like
    "fiscalYear" (value: 2025) that must be filtered out — otherwise a year
    like 2025 gets treated as a $2K revenue segment and poisons percentages.
    """
    # Prefer nested "data" dict (stable API format)
    segment_dict = record.get("data")
    if isinstance(segment_dict, dict):
        # Filter metadata keys even inside the nested "data" dict
        segment_dict = {k: v for k, v in segment_dict.items() if k not in _SEGMENT_META_KEYS}
    else:
        # Fallback: treat top-level keys minus metadata as segments
        segment_dict = {k: v for k, v in record.items() if k not in _SEGMENT_META_KEYS}

    segments: List[Tuple[str, float]] = []
    for key, val in segment_dict.items():
        try:
            amount = float(val)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(amount):
            continue  # NaN/Inf segment -> REQUIRED RevenueSourceSchema.value -> 500
        if amount <= 0:
            continue  # skip zero/negative segments
        # Skip values that look like years (e.g. 2024, 2025) — not revenue
        if 1900 <= amount <= 2100:
            continue
        segments.append((key, amount))

    if not segments:
        return []

    # Sort descending by value
    segments.sort(key=lambda s: s[1], reverse=True)

    total = sum(v for _, v in segments)
    if total <= 0:
        return []

    # Group small segments into "Other"
    result: List[RevenueSourceSchema] = []
    other_total = 0.0

    for name, value in segments:
        pct = (value / total) * 100
        if pct < _OTHER_THRESHOLD_PCT:
            other_total += value
        else:
            result.append(RevenueSourceSchema(name=name, value=value))

    if other_total > 0:
        result.append(RevenueSourceSchema(name="Other", value=other_total))

    return result


def _find_next_earnings_date_simple(
    ec_records: List[Dict[str, Any]],
) -> Optional[str]:
    """Return the first future earnings date as yyyy-MM-dd, or None."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ec in sorted(ec_records, key=lambda r: r.get("date", "")):
        ec_date = (ec.get("date") or "")[:10]
        if not ec_date or ec_date <= today_str:
            continue
        # Skip if actual EPS is already reported (past quarter)
        if ec.get("eps") is not None:
            continue
        return ec_date
    return None


# ── Service ───────────────────────────────────────────────────────

class RevenueBreakdownService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_revenue_breakdown(self, ticker: str) -> RevenueBreakdownResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"rev_breakdown:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Revenue breakdown in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache (run in thread to avoid blocking event loop) ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Revenue breakdown Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        # If another request is already fetching this ticker, wait for it
        if cache_key in _inflight:
            logger.info(f"Revenue breakdown in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        # Create a future so other concurrent requests can wait on us
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            # ── Cache miss: build from FMP ──
            logger.info(f"Revenue breakdown cache MISS for {ticker} — fetching from FMP")
            result, next_earnings = await self._build_revenue_breakdown(ticker)

            # Persist to Supabase in background thread (truly fire-and-forget)
            asyncio.get_running_loop().run_in_executor(
                None,
                self._upsert_supabase_cache_safe,
                ticker,
                result,
                next_earnings,
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

    def _check_supabase_cache(self, ticker: str) -> Optional[RevenueBreakdownResponse]:
        """Return cached response if fresh (< 24h and before next earnings).
        This is a synchronous method — call via asyncio.to_thread().
        """
        try:
            row = (
                self.supabase.table("revenue_breakdown_cache")
                .select("response_json, cached_at, next_earnings_date")
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

            # Parse cached_at and check 24-hour freshness
            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Supabase cache STALE (age={age}) for {ticker}")
                return None

            # Check next earnings date — invalidate if we've passed it
            next_earnings = entry.get("next_earnings_date")
            if next_earnings:
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today_str >= next_earnings:
                    logger.info(f"Supabase cache STALE (past earnings {next_earnings}) for {ticker}")
                    return None

            # Deserialize
            json_data = entry["response_json"]
            return RevenueBreakdownResponse(**json_data)

        except Exception as e:
            logger.warning(f"Supabase cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: RevenueBreakdownResponse,
        next_earnings: Optional[str],
    ) -> None:
        """Upsert to Supabase cache — safe wrapper that logs and swallows errors.
        This is a synchronous method — call via run_in_executor().
        """
        try:
            self.supabase.table("revenue_breakdown_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Supabase upsert failed for {ticker}: {e}")

    # ── Builder ───────────────────────────────────────────────────

    async def _build_revenue_breakdown(
        self, ticker: str
    ) -> Tuple[RevenueBreakdownResponse, Optional[str]]:
        """
        Fetch FMP data and assemble the response.
        Returns (response, next_earnings_date).
        """
        # Parallel FMP calls
        seg_raw, income_raw, ec_raw = await asyncio.gather(
            self.fmp.get_revenue_product_segmentation(ticker, period="annual"),
            self.fmp.get_income_statement(ticker, period="annual", limit=1),
            self.fmp.get_earning_calendar_full(ticker),
            return_exceptions=True,
        )

        # Handle exceptions gracefully
        if isinstance(seg_raw, Exception):
            logger.warning(f"Segment fetch failed for {ticker}: {seg_raw}")
            seg_raw = []
        if isinstance(income_raw, Exception):
            logger.warning(f"Income statement fetch failed for {ticker}: {income_raw}")
            income_raw = []
        if isinstance(ec_raw, Exception):
            logger.warning(f"Earnings calendar fetch failed for {ticker}: {ec_raw}")
            ec_raw = []

        # ── Income statement fields ──
        income = income_raw[0] if income_raw else {}
        cost_of_sales = _safe_float(income, "costOfRevenue")
        operating_expense = _safe_float(income, "operatingExpenses")
        tax = _safe_float(income, "incomeTaxExpense")
        total_revenue_income = _safe_float(income, "revenue")
        fiscal_year = str(
            income.get("calendarYear")
            or (income.get("date", "")[:4] if income.get("date") else "")
        )

        # ── Segments ──
        # FMP returns list of dicts, one per period. Take the most recent.
        revenue_sources: List[RevenueSourceSchema] = []
        if seg_raw and isinstance(seg_raw, list) and len(seg_raw) > 0:
            # Sort by date descending to get most recent
            sorted_seg = sorted(
                seg_raw,
                key=lambda r: r.get("date", ""),
                reverse=True,
            )
            revenue_sources = _extract_segments(sorted_seg[0])

        # Fallback: if no segments, use total revenue from income statement
        if not revenue_sources and total_revenue_income > 0:
            revenue_sources = [
                RevenueSourceSchema(name="Total Revenue", value=total_revenue_income)
            ]
            logger.info(f"No segment data for {ticker}, using Total Revenue fallback")

        # If still nothing, return zeros (company may have no data at all)
        if not revenue_sources:
            logger.warning(f"No revenue data at all for {ticker}")
            revenue_sources = [RevenueSourceSchema(name="Total Revenue", value=0.0)]

        # ── Next earnings date ──
        next_earnings = _find_next_earnings_date_simple(ec_raw if isinstance(ec_raw, list) else [])

        response = RevenueBreakdownResponse(
            symbol=ticker,
            fiscal_year=fiscal_year,
            revenue_sources=revenue_sources,
            cost_of_sales=cost_of_sales,
            operating_expense=operating_expense,
            tax=tax,
        )

        return response, next_earnings


# ── Singleton ─────────────────────────────────────────────────────
_revenue_breakdown_service: Optional[RevenueBreakdownService] = None


def get_revenue_breakdown_service() -> RevenueBreakdownService:
    global _revenue_breakdown_service
    if _revenue_breakdown_service is None:
        _revenue_breakdown_service = RevenueBreakdownService()
    return _revenue_breakdown_service
