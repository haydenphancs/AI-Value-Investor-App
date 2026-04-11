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
    BenchmarkSummaryResponse,
    CommodityChartPointResponse,
    CommodityDetailResponse,
    CommodityNewsArticleResponse,
    CommodityProfileResponse,
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    PerformancePeriodResponse,
    RelatedCommodityResponse,
)

logger = logging.getLogger(__name__)

# ── Commodity metadata ────────────────────────────────────────────

_COMMODITY_PROFILES: Dict[str, Dict[str, Any]] = {
    "GC": {
        "name": "Gold",
        "fmp_symbol": "GCUSD",
        "category": "metals",
        "exchange": "COMEX",
        "trading_hours": "Sun–Fri 6:00 PM – 5:00 PM ET",
        "contract_size": "100 troy ounces",
        "unit": "troy_ounce",
        "tick_size": "$0.10",
        "major_producers": "China, Australia, Russia, USA, Canada",
        "major_consumers": "China, India, USA, Germany, Turkey",
        "description": "Gold is a precious metal widely regarded as a store of value and safe-haven asset. Prices are driven by inflation expectations, interest rates, geopolitical risk, and US dollar strength. Central banks hold gold as reserve assets, and demand spans jewelry, electronics, and investment products.",
        "related": ["SIUSD", "HGUSD", "PLUSD", "PAUSD"],
    },
    "SI": {
        "name": "Silver",
        "fmp_symbol": "SIUSD",
        "category": "metals",
        "exchange": "COMEX",
        "trading_hours": "Sun–Fri 6:00 PM – 5:00 PM ET",
        "contract_size": "5,000 troy ounces",
        "unit": "troy_ounce",
        "tick_size": "$0.005",
        "major_producers": "Mexico, Peru, China, Poland, Chile",
        "major_consumers": "USA, India, Japan, China, Germany",
        "description": "Silver is both a precious and industrial metal. It is used in electronics, solar panels, medical devices, and jewelry. Silver prices correlate with gold but are more volatile due to its dual nature as a store of value and industrial commodity.",
        "related": ["GCUSD", "HGUSD", "PLUSD", "PAUSD"],
    },
    "CL": {
        "name": "Crude Oil WTI",
        "fmp_symbol": "CLUSD",
        "category": "energy",
        "exchange": "NYMEX",
        "trading_hours": "Sun–Fri 6:00 PM – 5:00 PM ET",
        "contract_size": "1,000 barrels",
        "unit": "barrel",
        "tick_size": "$0.01",
        "major_producers": "USA, Saudi Arabia, Russia, Canada, Iraq",
        "major_consumers": "USA, China, India, Japan, South Korea",
        "description": "West Texas Intermediate (WTI) crude oil is the primary benchmark for US oil pricing. Prices are influenced by OPEC+ production decisions, global demand growth, geopolitical tensions in oil-producing regions, and US inventory levels.",
        "related": ["NGUSD", "HGUSD"],
    },
    "NG": {
        "name": "Natural Gas",
        "fmp_symbol": "NGUSD",
        "category": "energy",
        "exchange": "NYMEX",
        "trading_hours": "Sun–Fri 6:00 PM – 5:00 PM ET",
        "contract_size": "10,000 MMBtu",
        "unit": "mmbtu",
        "tick_size": "$0.001",
        "major_producers": "USA, Russia, Iran, Qatar, Canada",
        "major_consumers": "USA, Russia, China, Iran, Japan",
        "description": "Natural gas is a fossil fuel used for heating, electricity generation, and industrial processes. Prices are highly seasonal, driven by weather patterns, storage levels, LNG export demand, and production trends in major basins like the Permian and Appalachian.",
        "related": ["CLUSD", "HGUSD"],
    },
    "HG": {
        "name": "Copper",
        "fmp_symbol": "HGUSD",
        "category": "metals",
        "exchange": "COMEX",
        "trading_hours": "Sun–Fri 6:00 PM – 5:00 PM ET",
        "contract_size": "25,000 pounds",
        "unit": "pound",
        "tick_size": "$0.0005",
        "major_producers": "Chile, Peru, China, DRC, USA",
        "major_consumers": "China, USA, Germany, Japan, South Korea",
        "description": "Copper is a key industrial metal often called 'Dr. Copper' for its ability to signal economic health. It is essential for construction, electronics, electric vehicles, and renewable energy infrastructure. Prices reflect global manufacturing activity and infrastructure spending.",
        "related": ["GCUSD", "SIUSD", "PLUSD"],
    },
    "PL": {
        "name": "Platinum",
        "fmp_symbol": "PLUSD",
        "category": "metals",
        "exchange": "NYMEX",
        "trading_hours": "Sun–Fri 6:00 PM – 5:00 PM ET",
        "contract_size": "50 troy ounces",
        "unit": "troy_ounce",
        "tick_size": "$0.10",
        "major_producers": "South Africa, Russia, Zimbabwe",
        "major_consumers": "Europe, Japan, China, USA",
        "description": "Platinum is a rare precious metal used primarily in catalytic converters, jewelry, and industrial applications. Supply is concentrated in South Africa, making prices sensitive to mining disruptions and automotive demand trends.",
        "related": ["PAUSD", "GCUSD", "SIUSD"],
    },
    "PA": {
        "name": "Palladium",
        "fmp_symbol": "PAUSD",
        "category": "metals",
        "exchange": "NYMEX",
        "trading_hours": "Sun–Fri 6:00 PM – 5:00 PM ET",
        "contract_size": "100 troy ounces",
        "unit": "troy_ounce",
        "tick_size": "$0.05",
        "major_producers": "Russia, South Africa, Canada, USA",
        "major_consumers": "USA, China, Europe, Japan",
        "description": "Palladium is a precious metal primarily used in gasoline vehicle catalytic converters. Its price is driven by automotive demand, emission regulations, and supply constraints from Russia and South Africa.",
        "related": ["PLUSD", "GCUSD", "SIUSD"],
    },
    "ZW": {
        "name": "Wheat",
        "fmp_symbol": "ZWUSD",
        "category": "agriculture",
        "exchange": "CBOT",
        "trading_hours": "Sun–Fri 7:00 PM – 7:45 AM, 8:30 AM – 1:20 PM CT",
        "contract_size": "5,000 bushels",
        "unit": "bushel",
        "tick_size": "$0.0025",
        "major_producers": "China, India, Russia, USA, France",
        "major_consumers": "China, India, Russia, USA, Pakistan",
        "description": "Wheat is a staple grain and one of the most widely traded agricultural commodities. Prices are influenced by global weather patterns, planting conditions, export policies, and geopolitical factors affecting key producing regions.",
        "related": ["ZCUSD", "ZSUSD"],
    },
    "ZC": {
        "name": "Corn",
        "fmp_symbol": "ZCUSD",
        "category": "agriculture",
        "exchange": "CBOT",
        "trading_hours": "Sun–Fri 7:00 PM – 7:45 AM, 8:30 AM – 1:20 PM CT",
        "contract_size": "5,000 bushels",
        "unit": "bushel",
        "tick_size": "$0.0025",
        "major_producers": "USA, China, Brazil, Argentina, Ukraine",
        "major_consumers": "USA, China, Brazil, EU, Mexico",
        "description": "Corn is the most produced grain globally, used for animal feed, ethanol production, and food products. Prices are driven by US crop conditions, ethanol mandates, global demand, and competition with soybeans for acreage.",
        "related": ["ZWUSD", "ZSUSD"],
    },
    "ZS": {
        "name": "Soybeans",
        "fmp_symbol": "ZSUSD",
        "category": "agriculture",
        "exchange": "CBOT",
        "trading_hours": "Sun–Fri 7:00 PM – 7:45 AM, 8:30 AM – 1:20 PM CT",
        "contract_size": "5,000 bushels",
        "unit": "bushel",
        "tick_size": "$0.0025",
        "major_producers": "Brazil, USA, Argentina, China, India",
        "major_consumers": "China, USA, Brazil, Argentina, EU",
        "description": "Soybeans are a versatile crop used for animal feed (soybean meal), cooking oil, and biodiesel. Prices are highly influenced by Chinese import demand, South American harvest conditions, and US planting decisions.",
        "related": ["ZCUSD", "ZWUSD"],
    },
    "KC": {
        "name": "Coffee",
        "fmp_symbol": "KCUSD",
        "category": "consumables",
        "exchange": "ICE",
        "trading_hours": "Mon–Fri 4:15 AM – 1:30 PM ET",
        "contract_size": "37,500 pounds",
        "unit": "pound",
        "tick_size": "$0.0005",
        "major_producers": "Brazil, Vietnam, Colombia, Indonesia, Ethiopia",
        "major_consumers": "EU, USA, Japan, Brazil, Canada",
        "description": "Coffee is the world's second most traded commodity after crude oil. Arabica coffee futures are particularly sensitive to Brazilian weather conditions, as Brazil produces roughly 40% of global supply.",
        "related": ["SBUSD", "CCUSD"],
    },
    "SB": {
        "name": "Sugar",
        "fmp_symbol": "SBUSD",
        "category": "consumables",
        "exchange": "ICE",
        "trading_hours": "Mon–Fri 3:30 AM – 1:00 PM ET",
        "contract_size": "112,000 pounds",
        "unit": "pound",
        "tick_size": "$0.0001",
        "major_producers": "Brazil, India, Thailand, China, Australia",
        "major_consumers": "India, EU, China, USA, Brazil",
        "description": "Sugar is a widely consumed agricultural commodity used in food, beverages, and ethanol production. Brazilian production and Indian export policies are key price drivers, along with weather and government subsidies.",
        "related": ["KCUSD", "CCUSD"],
    },
    "CC": {
        "name": "Cocoa",
        "fmp_symbol": "CCUSD",
        "category": "consumables",
        "exchange": "ICE",
        "trading_hours": "Mon–Fri 4:45 AM – 1:30 PM ET",
        "contract_size": "10 metric tons",
        "unit": "ton",
        "tick_size": "$1.00",
        "major_producers": "Ivory Coast, Ghana, Indonesia, Ecuador, Cameroon",
        "major_consumers": "EU, USA, Russia, Brazil, Japan",
        "description": "Cocoa is the primary raw material for chocolate production. Prices are heavily influenced by weather and disease in West Africa (which produces over 60% of global cocoa), along with grinding demand and speculative positioning.",
        "related": ["KCUSD", "SBUSD"],
    },
    "CT": {
        "name": "Cotton",
        "fmp_symbol": "CTUSD",
        "category": "consumables",
        "exchange": "ICE",
        "trading_hours": "Mon–Fri 9:00 PM – 2:20 PM ET",
        "contract_size": "50,000 pounds",
        "unit": "pound",
        "tick_size": "$0.0001",
        "major_producers": "China, India, USA, Brazil, Pakistan",
        "major_consumers": "China, India, Bangladesh, Vietnam, Turkey",
        "description": "Cotton is a soft commodity used primarily in the textile industry. Prices are driven by global textile demand, US and Indian crop conditions, Chinese stockpile levels, and competition with synthetic fibers.",
        "related": ["KCUSD", "SBUSD"],
    },
}


def _resolve_fmp_symbol(symbol: str) -> str:
    symbol = symbol.upper().replace("USD", "")
    meta = _COMMODITY_PROFILES.get(symbol)
    if meta:
        return meta["fmp_symbol"]
    if not symbol.endswith("USD"):
        return f"{symbol}USD"
    return symbol


def _resolve_name(symbol: str) -> str:
    symbol = symbol.upper().replace("USD", "")
    meta = _COMMODITY_PROFILES.get(symbol)
    return meta["name"] if meta else symbol


def _get_meta(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper().replace("USD", "")
    return _COMMODITY_PROFILES.get(symbol, {})


class CommodityService:
    """Aggregates FMP data for the Commodity Detail screen."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()

    async def get_commodity_detail(
        self, symbol: str, chart_range: str = "3M", interval: str = None
    ) -> CommodityDetailResponse:
        symbol = symbol.upper().replace("USD", "")
        fmp_symbol = _resolve_fmp_symbol(symbol)
        commodity_name = _resolve_name(symbol)
        meta = _get_meta(symbol)

        # ── Step 1: Parallel FMP fetches ──────────────────────────
        today = datetime.now(tz=timezone.utc).date()
        from_date = "2010-01-01"  # Fetch max history for performance & benchmark
        to_date = today.isoformat()

        quote_task = self.fmp.get_stock_price_quote(fmp_symbol)
        hist_task = self.fmp.get_historical_prices(fmp_symbol, from_date, to_date)
        news_task = self.fmp.get_stock_news(fmp_symbol, limit=10)

        # Also fetch related commodity quotes
        related_symbols = meta.get("related", [])
        related_tasks = [
            self.fmp.get_stock_price_quote(s) for s in related_symbols
        ]

        all_results = await asyncio.gather(
            quote_task, hist_task, news_task, *related_tasks,
            return_exceptions=True,
        )

        quote = all_results[0] if not isinstance(all_results[0], Exception) else {}
        hist_raw = all_results[1] if not isinstance(all_results[1], Exception) else {}
        news_raw = all_results[2] if not isinstance(all_results[2], Exception) else []

        related_quotes = []
        for i, sym in enumerate(related_symbols):
            q = all_results[3 + i]
            if not isinstance(q, Exception) and q:
                related_quotes.append((sym, q))

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

        # Compute moving averages from historical data
        closes = [p.get("close", 0) for p in historical if p.get("close")]
        ma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
        ma_200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None

        # 52-week change
        yr_ago_close = closes[-365] if len(closes) >= 365 else (closes[0] if closes else None)
        yr_change = None
        if yr_ago_close and yr_ago_close > 0 and price:
            yr_change = ((price - yr_ago_close) / yr_ago_close) * 100

        # Price per unit (top-left, like Constituents in Index)
        _UNIT_ABBREV = {
            "troy_ounce": "/oz",
            "barrel": "/bbl",
            "pound": "/lb",
            "mmbtu": "/MMBtu",
            "gallon": "/gal",
            "bushel": "/bu",
            "ton": "/ton",
            "contract": "",
        }
        unit_abbrev = _UNIT_ABBREV.get(meta.get("unit", ""), "")
        price_per_unit = _fmt(price) + unit_abbrev if price else "—"

        # Avg volume: compute from historical if FMP quote returns 0
        if not avg_volume and len(closes) >= 30:
            volumes_30d = [p.get("volume", 0) for p in historical[-30:] if p.get("volume")]
            if volumes_30d:
                avg_volume = sum(volumes_30d) / len(volumes_30d)

        key_statistics_groups = [
            # Left column (matches Index layout)
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="Price/Unit", value=price_per_unit),
                KeyStatisticItem(label="Open", value=_fmt(open_price)),
                KeyStatisticItem(label="Previous Close", value=_fmt(prev_close)),
                KeyStatisticItem(label="Day High", value=_fmt(day_high)),
                KeyStatisticItem(label="Day Low", value=_fmt(day_low)),
            ]),
            # Right column
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="52-Week High", value=_fmt(year_high)),
                KeyStatisticItem(label="52-Week Low", value=_fmt(year_low)),
                KeyStatisticItem(label="200-Day Avg", value=_fmt(ma_200)),
                KeyStatisticItem(label="Volume", value=_fmt_vol(volume)),
                KeyStatisticItem(label="Avg. Volume (30D)", value=_fmt_vol(avg_volume)),
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

        # ── Step 7: Build commodity profile ───────────────────────
        profile = CommodityProfileResponse(
            description=meta.get("description", ""),
            category=meta.get("category", ""),
            exchange=meta.get("exchange", ""),
            trading_hours=meta.get("trading_hours", ""),
            contract_size=meta.get("contract_size", ""),
            unit=meta.get("unit", ""),
            currency="USD",
            tick_size=meta.get("tick_size", ""),
            major_producers=meta.get("major_producers", ""),
            major_consumers=meta.get("major_consumers", ""),
        )

        # ── Step 8: Build related commodities ─────────────────────
        related_commodities = []
        for sym, rq in related_quotes:
            rel_name = _resolve_name(sym.replace("USD", ""))
            rel_price = rq.get("price") or 0
            rel_change = rq.get("changesPercentage") or 0
            related_commodities.append(RelatedCommodityResponse(
                symbol=sym,
                name=rel_name,
                price=round(rel_price, 2),
                change_percent=round(rel_change, 2),
            ))

        # ── Step 9: Build benchmark summary ───────────────────────
        # Use the earliest available data point for longest history
        benchmark = None
        if historical and len(historical) >= 252 and price:
            earliest = historical[0]
            earliest_close = earliest.get("close") or 0
            earliest_date = earliest.get("date", "")
            if earliest_close and earliest_close > 0:
                total_return = ((price - earliest_close) / earliest_close) * 100
                # Compute years from earliest date
                try:
                    start = datetime.strptime(earliest_date, "%Y-%m-%d")
                    years = max(1, (datetime.now() - start).days / 365.25)
                    avg_annual = total_return / years
                    since_year = earliest_date[:4]  # e.g. "2010"
                    benchmark = BenchmarkSummaryResponse(
                        avg_annual_return=round(avg_annual, 1),
                        sp_benchmark=10.5,
                        benchmark_name="S&P 500",
                        since_date=since_year,
                    )
                except Exception:
                    pass

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
            commodity_profile=profile,
            related_commodities=related_commodities,
            benchmark_summary=benchmark,
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
            ("1M", 21), ("3M", 63), ("6M", 126), ("YTD", None),
            ("1Y", 252), ("3Y", 756), ("5Y", 1260), ("10Y", 2520),
        ]
        result = []
        if not historical:
            return result

        current_close = historical[-1].get("close") or 0
        if not current_close:
            return result

        for label, days in periods:
            if label == "YTD":
                # Find first trading day of current year
                year_start = datetime.now(tz=timezone.utc).strftime("%Y-01-01")
                past_close = None
                for p in historical:
                    if p.get("date", "") >= year_start:
                        past_close = p.get("close")
                        break
                if not past_close or past_close <= 0:
                    continue
            else:
                idx = max(0, len(historical) - days)
                past_close = historical[idx].get("close") or 0
                if not past_close or past_close <= 0:
                    continue

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
