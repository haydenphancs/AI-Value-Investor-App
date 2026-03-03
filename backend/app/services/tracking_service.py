"""
Tracking Service — aggregates watchlist + FMP market data for the Assets tab.

Design (mirrors home_service.py):
- All external calls (FMP, Supabase) run concurrently via asyncio.gather.
- Each section degrades gracefully: if one data source fails, the rest
  still return so the Assets tab always loads.
- Sparkline data is cached per-ticker for 5 minutes.
- Full feed is cached per-user for 30 seconds.
"""

import asyncio
import random
import time as _time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
import logging

from app.integrations.fmp import get_fmp_client, FMPClient
from app.database import get_supabase
from app.schemas.tracking import (
    TrackedAssetResponse,
    EarningsAlertResponse,
    TrackingFeedResponse,
)

logger = logging.getLogger(__name__)

# ── Simple TTL Caches ───────────────────────────────────────────────

_feed_cache: Dict[str, Tuple[float, Any]] = {}
FEED_CACHE_TTL = 30  # 30 seconds per-user

_sparkline_cache: Dict[str, Tuple[float, List[float]]] = {}
SPARKLINE_CACHE_TTL = 300  # 5 minutes per-ticker


def _feed_cache_get(user_id: str) -> Optional[TrackingFeedResponse]:
    entry = _feed_cache.get(user_id)
    if entry is None:
        return None
    ts, value = entry
    if _time.monotonic() - ts > FEED_CACHE_TTL:
        del _feed_cache[user_id]
        return None
    return value


def _feed_cache_set(user_id: str, value: TrackingFeedResponse) -> None:
    _feed_cache[user_id] = (_time.monotonic(), value)


def _sparkline_cache_get(ticker: str) -> Optional[List[float]]:
    entry = _sparkline_cache.get(ticker)
    if entry is None:
        return None
    ts, value = entry
    if _time.monotonic() - ts > SPARKLINE_CACHE_TTL:
        del _sparkline_cache[ticker]
        return None
    return value


def _sparkline_cache_set(ticker: str, value: List[float]) -> None:
    _sparkline_cache[ticker] = (_time.monotonic(), value)


def _synthetic_sparkline(positive: bool) -> List[float]:
    """Fallback sparkline when historical prices are unavailable."""
    data: List[float] = []
    value = random.uniform(90.0, 110.0)
    for _ in range(20):
        change = random.uniform(-3.0, 3.0)
        trend = 0.5 if positive else -0.5
        value += change + trend
        value = max(80.0, min(120.0, value))
        data.append(round(value, 2))
    return data


# ── Service ─────────────────────────────────────────────────────────


class TrackingService:
    """Builds the enriched tracking feed from Supabase watchlist + FMP data."""

    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()

    async def get_tracking_feed(self, user_id: str) -> TrackingFeedResponse:
        """Return complete tracking feed for the Assets tab."""

        # Check cache first
        cached = _feed_cache_get(user_id)
        if cached is not None:
            logger.debug("Tracking feed served from cache for user %s", user_id)
            return cached

        # 1. Fetch user's watchlist from Supabase
        sb = get_supabase()
        try:
            result = (
                sb.table("watchlist_items")
                .select("*")
                .eq("user_id", user_id)
                .order("added_at", desc=True)
                .execute()
            )
            watchlist = result.data or []
        except Exception as exc:
            logger.error("Failed to fetch watchlist for user %s: %s", user_id, exc)
            return TrackingFeedResponse()

        if not watchlist:
            return TrackingFeedResponse()

        tickers = [item["ticker"] for item in watchlist]

        # 2. Fetch data concurrently
        quotes_task = self._get_batch_quotes(tickers)
        sparklines_task = self._get_all_sparklines(tickers)
        earnings_task = self._get_earnings_alerts(tickers)

        results = await asyncio.gather(
            quotes_task, sparklines_task, earnings_task, return_exceptions=True
        )

        quotes_map: Dict[str, Dict] = (
            results[0] if not isinstance(results[0], BaseException) else {}
        )
        sparklines_map: Dict[str, List[float]] = (
            results[1] if not isinstance(results[1], BaseException) else {}
        )
        alerts: List[EarningsAlertResponse] = (
            results[2] if not isinstance(results[2], BaseException) else []
        )

        for idx, res in enumerate(results):
            if isinstance(res, BaseException):
                logger.error("Tracking feed section %d failed: %s", idx, res)

        # 3. Merge watchlist + quotes + sparklines into TrackedAssetResponse
        assets: List[TrackedAssetResponse] = []
        for item in watchlist:
            ticker = item["ticker"]
            quote = quotes_map.get(ticker, {})
            sparkline = sparklines_map.get(ticker, [])

            change_pct = quote.get("changesPercentage") or 0
            price = quote.get("price") or 0

            assets.append(
                TrackedAssetResponse(
                    ticker=ticker,
                    company_name=item.get("company_name") or quote.get("name") or ticker,
                    price=round(float(price), 2),
                    change_percent=round(float(change_pct), 2),
                    sparkline_data=sparkline,
                    logo_url=item.get("logo_url"),
                    sector=quote.get("sector"),
                    country=quote.get("country"),
                    market_cap=float(quote["marketCap"]) if quote.get("marketCap") else None,
                )
            )

        feed = TrackingFeedResponse(assets=assets, alerts=alerts)
        _feed_cache_set(user_id, feed)
        return feed

    # ── Batch Quotes ────────────────────────────────────────────────

    async def _get_batch_quotes(
        self, tickers: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch real-time quotes for all tickers in a single FMP call."""
        try:
            quotes = await self.fmp.get_batch_quotes(tickers)
            return {q["symbol"]: q for q in quotes if q.get("symbol")}
        except Exception as exc:
            logger.warning("Batch quotes failed: %s", exc)
            return {}

    # ── Sparklines ──────────────────────────────────────────────────

    async def _get_all_sparklines(
        self, tickers: List[str]
    ) -> Dict[str, List[float]]:
        """Fetch sparkline data for all tickers concurrently."""

        async def _fetch_one(ticker: str) -> Tuple[str, List[float]]:
            # Check per-ticker cache
            cached = _sparkline_cache_get(ticker)
            if cached is not None:
                return (ticker, cached)

            try:
                to_date = datetime.now().strftime("%Y-%m-%d")
                from_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
                data = await self.fmp.get_historical_prices(
                    ticker, from_date=from_date, to_date=to_date
                )
                if not data:
                    sparkline = _synthetic_sparkline(True)
                    _sparkline_cache_set(ticker, sparkline)
                    return (ticker, sparkline)

                historical = data.get("historical", [])
                if not historical:
                    sparkline = _synthetic_sparkline(True)
                    _sparkline_cache_set(ticker, sparkline)
                    return (ticker, sparkline)

                # historical is newest-first; take 20, reverse for oldest-first
                prices = [float(day.get("close") or 0) for day in historical[:20]]
                prices.reverse()
                sparkline = [round(p, 2) for p in prices]
                _sparkline_cache_set(ticker, sparkline)
                return (ticker, sparkline)
            except Exception as exc:
                logger.warning("Sparkline for %s failed: %s", ticker, exc)
                return (ticker, _synthetic_sparkline(True))

        results = await asyncio.gather(*[_fetch_one(t) for t in tickers])
        return dict(results)

    # ── Earnings Alerts ─────────────────────────────────────────────

    async def _get_earnings_alerts(
        self, watchlist_tickers: List[str]
    ) -> List[EarningsAlertResponse]:
        """Fetch upcoming earnings from FMP, filtered to user's watchlist."""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
            calendar = await self.fmp.get_earnings_calendar(
                from_date=today, to_date=future
            )
            if not calendar:
                return []

            ticker_set = {t.upper() for t in watchlist_tickers}
            alerts: List[EarningsAlertResponse] = []

            for entry in calendar:
                symbol = (entry.get("symbol") or "").upper()
                if symbol not in ticker_set:
                    continue

                # Parse date for day/month
                date_str = entry.get("date", "")
                day = None
                month = None
                if date_str:
                    try:
                        dt = datetime.strptime(date_str, "%Y-%m-%d")
                        day = dt.day
                        month = dt.strftime("%b").upper()
                    except ValueError:
                        pass

                # Determine report time
                report_time_raw = (entry.get("time") or "").lower()
                if "bmo" in report_time_raw or "before" in report_time_raw:
                    report_time = "before_open"
                elif "amc" in report_time_raw or "after" in report_time_raw:
                    report_time = "after_close"
                else:
                    report_time = None

                # Build description
                eps_est = entry.get("epsEstimated")
                rev_est = entry.get("revenueEstimated")
                desc_parts = []
                if eps_est is not None:
                    desc_parts.append(f"EPS Est: ${eps_est:.2f}")
                if rev_est is not None:
                    rev_b = float(rev_est) / 1_000_000_000
                    desc_parts.append(f"Rev Est: ${rev_b:.1f}B")
                description = ". ".join(desc_parts) if desc_parts else "Earnings report upcoming"

                report_time_display = "before market open" if report_time == "before_open" else "after market close"
                full_desc = f"{symbol} reports earnings {report_time_display}. {description}"

                alerts.append(
                    EarningsAlertResponse(
                        type="earnings",
                        ticker=symbol,
                        company_name=entry.get("companyName") or symbol,
                        title="Earnings Alert",
                        description=full_desc,
                        day=day,
                        month=month,
                        report_time=report_time,
                    )
                )

            return alerts

        except Exception as exc:
            logger.warning("Earnings alerts failed: %s", exc)
            return []
