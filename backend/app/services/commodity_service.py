"""
Commodity Service — Aggregates FMP data for the CommodityDetailView screen.

Supports commodity symbols like GCUSD (Gold), SIUSD (Silver),
CLUSD (Crude Oil WTI), NGUSD (Natural Gas), etc.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.integrations.fmp import FMPClient, get_fmp_client
from app.schemas.commodity import (
    CommodityChartPointResponse,
    CommodityDetailResponse,
    CommodityNewsArticleResponse,
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    PerformancePeriodResponse,
)

logger = logging.getLogger(__name__)

# ── Commodity metadata ────────────────────────────────────────────

_COMMODITY_PROFILES = {
    "GC": {"name": "Gold", "fmp_symbol": "GCUSD"},
    "SI": {"name": "Silver", "fmp_symbol": "SIUSD"},
    "CL": {"name": "Crude Oil WTI", "fmp_symbol": "CLUSD"},
    "NG": {"name": "Natural Gas", "fmp_symbol": "NGUSD"},
    "HG": {"name": "Copper", "fmp_symbol": "HGUSD"},
    "PL": {"name": "Platinum", "fmp_symbol": "PLUSD"},
    "PA": {"name": "Palladium", "fmp_symbol": "PAUSD"},
    "ZW": {"name": "Wheat", "fmp_symbol": "ZWUSD"},
    "ZC": {"name": "Corn", "fmp_symbol": "ZCUSD"},
    "ZS": {"name": "Soybeans", "fmp_symbol": "ZSUSD"},
    "KC": {"name": "Coffee", "fmp_symbol": "KCUSD"},
    "SB": {"name": "Sugar", "fmp_symbol": "SBUSD"},
    "CC": {"name": "Cocoa", "fmp_symbol": "CCUSD"},
    "CT": {"name": "Cotton", "fmp_symbol": "CTUSD"},
}


def _resolve_fmp_symbol(symbol: str) -> str:
    """Resolve a commodity symbol to its FMP symbol."""
    symbol = symbol.upper().replace("USD", "")
    meta = _COMMODITY_PROFILES.get(symbol)
    if meta:
        return meta["fmp_symbol"]
    # Fallback: append USD if not already present
    if not symbol.endswith("USD"):
        return f"{symbol}USD"
    return symbol


def _resolve_name(symbol: str) -> str:
    """Resolve a commodity symbol to its display name."""
    symbol = symbol.upper().replace("USD", "")
    meta = _COMMODITY_PROFILES.get(symbol)
    return meta["name"] if meta else symbol


class CommodityService:
    """Aggregates FMP data for the Commodity Detail screen."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()

    async def get_commodity_detail(
        self, symbol: str, chart_range: str = "3M", interval: str = None
    ) -> CommodityDetailResponse:
        """Fetch and assemble complete commodity detail data."""
        symbol = symbol.upper().replace("USD", "")
        fmp_symbol = _resolve_fmp_symbol(symbol)
        commodity_name = _resolve_name(symbol)

        # ── Step 1: Parallel FMP fetches ──────────────────────────
        today = datetime.now(tz=timezone.utc).date()
        from_date = (today - timedelta(days=365 * 2)).isoformat()
        to_date = today.isoformat()

        quote_task = self.fmp.get_stock_price_quote(fmp_symbol)
        hist_task = self.fmp.get_historical_prices(fmp_symbol, from_date, to_date)
        news_task = self.fmp.get_stock_news(fmp_symbol, limit=10)

        quote, hist_raw, news_raw = await asyncio.gather(
            quote_task, hist_task, news_task,
            return_exceptions=True,
        )

        if isinstance(quote, Exception):
            logger.error(f"Commodity quote failed for {fmp_symbol}: {quote}")
            quote = {}
        if isinstance(hist_raw, Exception):
            logger.error(f"Commodity historical failed for {fmp_symbol}: {hist_raw}")
            hist_raw = {}
        if isinstance(news_raw, Exception):
            logger.error(f"Commodity news failed for {fmp_symbol}: {news_raw}")
            news_raw = []

        # Parse historical prices (sorted oldest-first)
        historical = []
        if isinstance(hist_raw, dict):
            historical = hist_raw.get("historical", [])
        elif isinstance(hist_raw, list):
            historical = hist_raw
        historical.sort(key=lambda p: p.get("date", ""))

        # ── Step 2: Extract quote data ────────────────────────────
        price = quote.get("price") or 0
        change = quote.get("change") or 0
        change_pct = quote.get("changesPercentage") or 0
        day_high = quote.get("dayHigh") or 0
        day_low = quote.get("dayLow") or 0
        year_high = quote.get("yearHigh") or 0
        year_low = quote.get("yearLow") or 0
        volume = quote.get("volume") or 0
        avg_volume = quote.get("avgVolume") or 0
        open_price = quote.get("open") or 0
        prev_close = quote.get("previousClose") or 0

        # ── Step 3: Build chart data ──────────────────────────────
        from app.services.chart_helper import fetch_chart_data, resolve_interval
        resolved = resolve_interval(chart_range, interval)
        if resolved != "daily" or chart_range == "ALL":
            chart_points = await fetch_chart_data(
                self.fmp, fmp_symbol, chart_range, interval
            )
        else:
            chart_points = self._extract_chart_data(historical, chart_range)

        chart_data = [
            CommodityChartPointResponse(
                date=p["date"],
                open=p.get("open"),
                high=p.get("high"),
                low=p.get("low"),
                close=p["close"],
                volume=p.get("volume"),
            )
            for p in chart_points
        ]

        # ── Step 4: Build key statistics ──────────────────────────
        def _fmt(v, prefix="$", decimals=2):
            if not v:
                return "—"
            return f"{prefix}{v:,.{decimals}f}" if prefix else f"{v:,.{decimals}f}"

        def _fmt_vol(v):
            if not v:
                return "—"
            if v >= 1_000_000:
                return f"{v / 1_000_000:.1f}M"
            if v >= 1_000:
                return f"{v / 1_000:.1f}K"
            return str(int(v))

        key_statistics_groups = [
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="Open", value=_fmt(open_price)),
                KeyStatisticItem(label="Previous Close", value=_fmt(prev_close)),
                KeyStatisticItem(
                    label="Day Range",
                    value=f"{_fmt(day_low)} - {_fmt(day_high)}"
                ),
                KeyStatisticItem(label="Volume", value=_fmt_vol(volume)),
                KeyStatisticItem(label="Avg. Volume", value=_fmt_vol(avg_volume)),
            ]),
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="52W High", value=_fmt(year_high)),
                KeyStatisticItem(label="52W Low", value=_fmt(year_low)),
            ]),
        ]

        # ── Step 5: Build performance periods ─────────────────────
        performance_periods = self._build_performance(historical)

        # ── Step 6: Build news ────────────────────────────────────
        news_articles = []
        if isinstance(news_raw, list):
            for article in news_raw[:10]:
                news_articles.append(CommodityNewsArticleResponse(
                    headline=article.get("title") or article.get("text", ""),
                    source_name=article.get("site") or article.get("publishedDate", ""),
                    source_icon=None,
                    sentiment="neutral",
                    published_at=article.get("publishedDate", ""),
                    thumbnail_url=article.get("image"),
                    related_tickers=[s for s in (article.get("symbol") or "").split(",") if s],
                    summary_bullets=[],
                    article_url=article.get("url"),
                ))

        return CommodityDetailResponse(
            symbol=fmp_symbol,
            name=commodity_name,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
            market_status="Market Open",
            chart_data=chart_data,
            key_statistics_groups=key_statistics_groups,
            performance_periods=performance_periods,
            news_articles=news_articles,
        )

    def _extract_chart_data(
        self, historical: List[Dict], chart_range: str
    ) -> List[Dict]:
        if not historical:
            return []

        today = datetime.now(tz=timezone.utc).date()
        range_days = {
            "1D": 2, "1W": 7, "3M": 90, "6M": 180,
            "1Y": 365, "5Y": 365 * 5, "ALL": 99999,
        }
        days = range_days.get(chart_range, 90)
        cutoff = (today - timedelta(days=days)).isoformat()

        result = []
        for p in historical:
            if p.get("date", "") >= cutoff:
                close = p.get("close") or p.get("adjClose")
                if close and close > 0:
                    result.append({
                        "date": p.get("date"),
                        "open": p.get("open"),
                        "high": p.get("high"),
                        "low": p.get("low"),
                        "close": round(float(close), 2),
                        "volume": p.get("volume"),
                    })
        return result

    def _build_performance(self, historical: List[Dict]) -> List[PerformancePeriodResponse]:
        periods = [
            ("1M", 30), ("3M", 90), ("6M", 180), ("1Y", 365),
        ]
        result = []
        if not historical:
            return result

        current_close = historical[-1].get("close") or 0
        if not current_close:
            return result

        for label, days in periods:
            idx = max(0, len(historical) - days)
            past_close = historical[idx].get("close") or 0
            if past_close and past_close > 0:
                change_pct = ((current_close - past_close) / past_close) * 100
                result.append(PerformancePeriodResponse(
                    label=label,
                    change_percent=round(change_pct, 2),
                ))

        return result


# ── Singleton ─────────────────────────────────────────────────────

_commodity_service: Optional[CommodityService] = None


def get_commodity_service() -> CommodityService:
    global _commodity_service
    if _commodity_service is None:
        _commodity_service = CommodityService()
    return _commodity_service
