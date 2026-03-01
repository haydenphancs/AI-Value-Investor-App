"""
Home Feed Service — aggregates FMP market data + Supabase user data.

Design:
- All external calls (FMP, Supabase) run concurrently via asyncio.gather.
- Each section degrades gracefully: if one data source fails, the rest
  still return, so the home screen always loads.
- Dates are normalised to YYYY-MM-DDTHH:MM:SSZ for iOS .iso8601 decoder.
- Market tickers and insights use a short TTL cache (60s) to avoid
  hammering FMP on rapid pull-to-refresh.
"""

import asyncio
import random
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
import logging

from app.integrations.fmp import get_fmp_client
from app.database import get_supabase
from app.schemas.home import (
    MarketTickerResponse,
    MarketInsightResponse,
    DailyBriefingItemResponse,
    RecentResearchResponse,
    HomeFeedResponse,
)

logger = logging.getLogger(__name__)

# ── Simple TTL Cache ────────────────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 60  # 1 minute


def _cache_get(key: str) -> Optional[Any]:
    """Return cached value if it exists and hasn't expired."""
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if _time.monotonic() - ts > CACHE_TTL_SECONDS:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    """Store a value in the cache with current timestamp."""
    _cache[key] = (_time.monotonic(), value)

# ── Configuration ────────────────────────────────────────────────────

DEFAULT_MARKET_TICKERS: List[Dict[str, str]] = [
    {"symbol": "^GSPC", "name": "S&P 500", "type": "index"},
    {"symbol": "^IXIC", "name": "Nasdaq", "type": "index"},
    {"symbol": "BTCUSD", "name": "Bitcoin", "type": "crypto"},
    {"symbol": "GCUSD", "name": "Gold", "type": "commodity"},
]

PERSONA_DISPLAY_NAMES: Dict[str, str] = {
    "warren_buffett": "Warren Buffett",
    "peter_lynch": "Peter Lynch",
    "cathie_wood": "Cathie Wood",
    "bill_ackman": "Bill Ackman",
}

PERSONA_GRADIENT_COLORS: Dict[str, List[str]] = {
    "warren_buffett": ["4F46E5", "1E1B4B"],
    "peter_lynch": ["059669", "064E3B"],
    "cathie_wood": ["DC2626", "7F1D1D"],
    "bill_ackman": ["D97706", "78350F"],
}


# ── Helpers ──────────────────────────────────────────────────────────

def _normalize_iso_date(date_str: Optional[str]) -> Optional[str]:
    """Convert Supabase timestamps to iOS-compatible ISO 8601 (no fractional seconds)."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        return date_str


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


async def _empty_list() -> list:
    """Async coroutine that returns [] — used as placeholder in gather."""
    return []


# ── Service ──────────────────────────────────────────────────────────

class HomeService:
    """Builds the aggregated home feed from FMP + Supabase."""

    def __init__(self) -> None:
        self.fmp = get_fmp_client()

    # ── Public API ───────────────────────────────────────────────

    async def get_home_feed(self, user_id: Optional[str] = None) -> HomeFeedResponse:
        """Return complete home feed.  All sections fetched concurrently."""
        coros = [
            self._get_market_tickers(),
            self._get_market_insight(),
            self._get_daily_briefings(),
            self._get_recent_research(user_id) if user_id else _empty_list(),
        ]

        results = await asyncio.gather(*coros, return_exceptions=True)

        tickers = results[0] if not isinstance(results[0], BaseException) else []
        insight = results[1] if not isinstance(results[1], BaseException) else None
        briefings = results[2] if not isinstance(results[2], BaseException) else []
        research = results[3] if not isinstance(results[3], BaseException) else []

        for idx, res in enumerate(results):
            if isinstance(res, BaseException):
                logger.error("Home feed section %d failed: %s", idx, res)

        return HomeFeedResponse(
            market_tickers=tickers,
            market_insight=insight,
            daily_briefings=briefings,
            recent_research=research,
        )

    # ── Market Tickers ───────────────────────────────────────────

    async def _get_market_tickers(self) -> List[MarketTickerResponse]:
        """Fetch real-time quotes + sparkline for each default ticker.
        Results are cached for 60s to avoid hitting FMP on rapid refreshes."""

        cached = _cache_get("market_tickers")
        if cached is not None:
            logger.debug("Market tickers served from cache")
            return cached

        async def _fetch_one(cfg: Dict[str, str]) -> Optional[MarketTickerResponse]:
            try:
                quote, sparkline = await asyncio.gather(
                    self.fmp.get_stock_price_quote(cfg["symbol"]),
                    self._get_sparkline(cfg["symbol"]),
                )
                if not quote:
                    return None
                return MarketTickerResponse(
                    name=cfg["name"],
                    symbol=cfg["symbol"],
                    type=cfg["type"],
                    price=round(float(quote.get("price") or 0), 2),
                    change_percent=round(float(quote.get("changesPercentage") or 0), 2),
                    sparkline_data=sparkline,
                )
            except Exception as exc:
                logger.warning("Ticker %s failed: %s", cfg["symbol"], exc)
                return None

        results = await asyncio.gather(*[_fetch_one(c) for c in DEFAULT_MARKET_TICKERS])
        tickers = [r for r in results if r is not None]
        if tickers:
            _cache_set("market_tickers", tickers)
        return tickers

    async def _get_sparkline(self, symbol: str) -> List[float]:
        """Last 20 trading-day closing prices for a mini-chart."""
        try:
            to_date = datetime.now().strftime("%Y-%m-%d")
            from_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
            data = await self.fmp.get_historical_prices(
                symbol, from_date=from_date, to_date=to_date,
            )
            if not data:
                return _synthetic_sparkline(True)

            historical = data.get("historical", [])
            if not historical:
                return _synthetic_sparkline(True)

            # historical is newest-first; take 20, reverse for oldest-first
            prices = [float(day.get("close") or 0) for day in historical[:20]]
            prices.reverse()
            return [round(p, 2) for p in prices]
        except Exception as exc:
            logger.warning("Sparkline for %s failed: %s", symbol, exc)
            return _synthetic_sparkline(True)

    # ── Market Insight ───────────────────────────────────────────

    async def _get_market_insight(self) -> Optional[MarketInsightResponse]:
        """Market summary — from DB table or auto-generated from S&P 500 data.
        Cached for 60s to reduce FMP calls."""

        cached = _cache_get("market_insight")
        if cached is not None:
            logger.debug("Market insight served from cache")
            return cached

        # 1. Try Supabase market_insights table
        try:
            sb = get_supabase()
            result = (
                sb.table("market_insights")
                .select("headline, bullet_points, sentiment, created_at")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if result.data:
                row = result.data[0]
                insight = MarketInsightResponse(
                    headline=row.get("headline", ""),
                    bullet_points=row.get("bullet_points", []),
                    sentiment=row.get("sentiment", "Neutral"),
                    updated_at=_normalize_iso_date(row.get("created_at")) or _now_iso(),
                )
                _cache_set("market_insight", insight)
                return insight
        except Exception:
            pass  # table may not exist yet

        # 2. Fallback: derive from S&P 500 quote
        try:
            quote = await self.fmp.get_stock_price_quote("^GSPC")
            if not quote:
                return None

            change = float(quote.get("changesPercentage") or 0)
            price = float(quote.get("price") or 0)

            if change > 0.5:
                sentiment = "Bullish"
                headline = f"Markets Rally as S&P 500 Gains {abs(change):.1f}%"
                bullets = [
                    f"The S&P 500 is trading at {price:,.0f}, up {change:.2f}% in today's session.",
                    "Broad market strength suggests positive investor sentiment across sectors.",
                ]
            elif change < -0.5:
                sentiment = "Bearish"
                headline = f"Markets Pull Back as S&P 500 Drops {abs(change):.1f}%"
                bullets = [
                    f"The S&P 500 has declined to {price:,.0f}, down {abs(change):.2f}% today.",
                    "Investors appear cautious amid market volatility.",
                ]
            else:
                sentiment = "Neutral"
                headline = f"Markets Trade Sideways Near {price:,.0f}"
                bullets = [
                    f"The S&P 500 is holding steady at {price:,.0f} with a {change:+.2f}% move.",
                    "Mixed signals from economic data keep markets range-bound.",
                ]

            insight = MarketInsightResponse(
                headline=headline,
                bullet_points=bullets,
                sentiment=sentiment,
                updated_at=_now_iso(),
            )
            _cache_set("market_insight", insight)
            return insight
        except Exception as exc:
            logger.warning("Market insight generation failed: %s", exc)
            return None

    # ── Daily Briefings ──────────────────────────────────────────

    async def _get_daily_briefings(self) -> List[DailyBriefingItemResponse]:
        """Alerts / briefing items — from DB or FMP earnings calendar."""
        briefings: List[DailyBriefingItemResponse] = []

        # 1. Try Supabase daily_briefings table
        try:
            sb = get_supabase()
            result = (
                sb.table("daily_briefings")
                .select("type, title, subtitle, date, badge_text")
                .eq("is_active", True)
                .order("priority", desc=True)
                .limit(5)
                .execute()
            )
            if result.data:
                for row in result.data:
                    briefings.append(DailyBriefingItemResponse(
                        type=row.get("type", "wiser_trending"),
                        title=row.get("title", ""),
                        subtitle=row.get("subtitle", ""),
                        date=_normalize_iso_date(row.get("date")),
                        badge_text=row.get("badge_text"),
                    ))
                return briefings
        except Exception:
            pass  # table may not exist

        # 2. Fallback: FMP earnings calendar
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            next_week = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            earnings = await self.fmp.get_earnings_calendar(today, next_week)

            if earnings:
                for item in earnings[:2]:
                    sym = item.get("symbol", "")
                    date_str = item.get("date", "")
                    badge: Optional[str] = None
                    iso_date: Optional[str] = None
                    if date_str:
                        try:
                            dt = datetime.strptime(date_str, "%Y-%m-%d")
                            badge = dt.strftime("%d\n%b").upper()
                            iso_date = dt.strftime("%Y-%m-%dT00:00:00Z")
                        except ValueError:
                            pass

                    time_val = item.get("time", "")
                    when = (
                        "after market close"
                        if time_val == "amc"
                        else "before market open"
                        if time_val == "bmo"
                        else "soon"
                    )
                    briefings.append(DailyBriefingItemResponse(
                        type="earnings_alert",
                        title="Earnings Alert",
                        subtitle=f"{sym} reports earnings {when}.",
                        date=iso_date,
                        badge_text=badge,
                    ))
        except Exception as exc:
            logger.warning("Earnings calendar failed: %s", exc)

        # 3. Pad with defaults so the section is never empty
        defaults = [
            DailyBriefingItemResponse(
                type="wiser_trending",
                title="Wiser: Trending",
                subtitle="What sectors are showing the strongest momentum this quarter?",
            ),
            DailyBriefingItemResponse(
                type="whales_alert",
                title="Whales Alert",
                subtitle="Institutional investors increased positions in tech stocks this week.",
            ),
        ]
        for d in defaults:
            if len(briefings) >= 4:
                break
            briefings.append(d)

        return briefings

    # ── Recent Research ──────────────────────────────────────────

    async def _get_recent_research(self, user_id: str) -> List[RecentResearchResponse]:
        """User's most recent completed research reports from Supabase."""
        try:
            sb = get_supabase()
            result = (
                sb.table("research_reports")
                .select(
                    "id, ticker, company_name, investor_persona, title, "
                    "executive_summary, overall_score, fair_value_estimate, created_at"
                )
                .eq("user_id", user_id)
                .eq("status", "completed")
                .order("created_at", desc=True)
                .limit(5)
                .execute()
            )

            if not result.data:
                return []

            reports: List[RecentResearchResponse] = []
            for row in result.data:
                persona_key = row.get("investor_persona", "")
                persona_name = PERSONA_DISPLAY_NAMES.get(persona_key, persona_key)
                gradient = PERSONA_GRADIENT_COLORS.get(persona_key, ["3B82F6", "1E3A8A"])

                ticker = row.get("ticker", "")
                company = row.get("company_name") or ticker

                title = row.get("title") or ""
                if not title:
                    score = row.get("overall_score")
                    if score is not None and score >= 80:
                        title = f"{ticker}: Excellent Quality"
                    elif score is not None and score >= 60:
                        title = f"{ticker}: Strong Quality"
                    else:
                        title = f"{ticker}: Analysis Complete"

                # Best-effort logo icon name
                first_word = company.split()[0].lower() if company else ticker.lower()
                logo = f"icon_{first_word}"

                reports.append(RecentResearchResponse(
                    id=row["id"],
                    stock_ticker=ticker,
                    stock_name=company,
                    company_logo_name=logo,
                    persona=persona_name,
                    headline=title,
                    summary=row.get("executive_summary") or "Research analysis.",
                    rating=float(row.get("overall_score") or 0),
                    fair_value=float(row.get("fair_value_estimate") or 0),
                    created_at=_normalize_iso_date(row.get("created_at")) or "",
                    gradient_colors=gradient,
                ))
            return reports

        except Exception as exc:
            logger.error("Recent research fetch failed: %s", exc)
            return []
