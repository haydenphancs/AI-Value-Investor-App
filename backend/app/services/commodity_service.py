"""
Commodity Service — Aggregates FMP data for the CommodityDetailView screen.

Supports commodity symbols like GCUSD (Gold), SIUSD (Silver),
CLUSD (Crude Oil WTI), NGUSD (Natural Gas), etc.
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional

from app.integrations.fmp import (
    FMPClient,
    FMPUnavailableException,
    get_fmp_client,
)
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
from app.services.chart_helper import _finite_or_none
from app.services.benchmark_math import (
    cagr_between,
    format_since,
    overlapping_cagrs,
)
from app.utils.market_hours import to_utc_instant

logger = logging.getLogger(__name__)


# ── Per-section caches + in-flight dedup (CLAUDE.md invariant #4) ────
#
# This service used ONE key and ONE 5-minute TTL for a nine-section payload whose
# sections range from "moves every second" to "hardcoded constant". Because the key
# included range+interval, every range pill was a separate cold build that re-fetched
# the SAME quote, the SAME 972 KB / 4,333-row daily history and the SAME related quotes.
# Measured: browsing all 7 ranges of one commodity cost 55 FMP calls and pulled 6.6 MB
# of byte-identical history.
#
# Now each section is cached under its own key with a TTL matched to how fast that data
# actually changes, and the expensive range-INDEPENDENT sections are shared by every
# range. Same idea as index_service / stock_overview_service, which already run several
# TTLs inside one service.
#
# The TTL travels WITH the entry (index_service's variant) rather than being passed by
# the reader: the writer knows the section's volatility, and a reader cannot mismatch it.
_cache: Dict[str, tuple] = {}
_CACHE_TTL_SECONDS = 300  # default when a caller declares nothing

# Per-section TTLs, ordered by how fast the underlying data really moves.
_QUOTE_TTL = 45          # live price / intraday key-stat rows
_RELATED_TTL = 60        # sibling commodity quotes — same data class, less prominent
_INTRADAY_CHART_TTL = 60 # 1D/1W bars; the only genuinely per-range fetch
_HISTORY_TTL = 43_200    # 12h — daily EOD bars only change at the close
_DERIVED_TTL = 43_200    # 12h — performance / benchmark / daily key stats, all from history

# Hard cap: `_cache_get` only evicts a key when that same key is read again after
# expiry, so on a long-lived Railway process a symbol fetched once would sit resident
# forever. Bounded, evicting least-recently-written. 1024 matches every sibling service
# — 256 was below the ~294 keys this service can hold, so it evicted under normal use.
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


def _cache_set(key: str, value: Any, ttl: Optional[float] = None) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value, ttl or _CACHE_TTL_SECONDS)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# Thundering-herd guard. Keyed on the FULL request shape, because `chart_data` is built
# from range+interval — keying on the symbol alone is the bug that made the ETF range
# picker a no-op (see etf_service._cache_key).
_inflight: Dict[str, asyncio.Future] = {}


def _commodity_market_status() -> str:
    """Honest open/closed string for commodity futures (was hardcoded "Market Open",
    which showed a live status on weekends/holidays over a stale last close).

    CME Globex commodity futures run ~Sunday 6pm ET → Friday 5pm ET (with a short
    daily maintenance break). We conservatively report "Market Closed" only when we
    are confident it is closed — all day Saturday, Sunday before 6pm ET, and Friday
    after 5pm ET — and "Market Open" otherwise (the iOS decoder maps any non-open
    string to a closed state, so this never over-claims 'open'). The ~1h daily break
    is intentionally treated as open to avoid false 'closed' during active sessions.
    """
    now = datetime.now(tz=ZoneInfo("America/New_York"))
    weekday = now.weekday()  # 0=Mon … 5=Sat, 6=Sun
    hour = now.hour
    if weekday == 5:  # Saturday — fully closed
        return "Market Closed"
    if weekday == 6 and hour < 18:  # Sunday before the 6pm ET reopen
        return "Market Closed"
    if weekday == 4 and hour >= 17:  # Friday after the 5pm ET close
        return "Market Closed"
    return "Market Open"


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



def _normalized_published_at(article: Dict[str, Any]) -> str:
    """FMP's publishedDate as a true UTC instant, falling back to the raw string.

    The wall clock FMP sends is naive America/New_York. The News tab now serves a
    normalised UTC instant (news_cache_service._sanitize_published_at), and this
    builder bypasses that path entirely, so without this the same article reads 4h
    apart on the commodity screen and the News tab. The field is REQUIRED, so an
    unparseable value degrades to the raw string rather than None (a None 500s the
    whole commodity detail — see the guard note at the call site).
    """
    raw = article.get("publishedDate") or article.get("published_date") or ""
    dt = to_utc_instant(raw)
    return dt.isoformat() if dt is not None else raw


class CommodityService:
    """Aggregates FMP data for the Commodity Detail screen."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()

    async def get_commodity_detail(
        self, symbol: str, chart_range: str = "3M", interval: str = None
    ) -> CommodityDetailResponse:
        """Cache-aside wrapper (per-section Tier 1 + in-flight dedup) around the build.

        The ASSEMBLED response keeps a short TTL deliberately. Its slow sections are now
        cached individually for 12h, so a rebuild here costs zero FMP calls when they are
        warm — but the price header is part of this payload, and a 5-minute assembled TTL
        would serve a 5-minute-old price on a screen whose whole point is a live quote.
        Short outer TTL + long inner TTLs is the combination that gives both.
        """
        cache_key = f"{symbol.upper().strip()}_{chart_range}_{interval or 'default'}"

        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info("Commodity detail in-memory HIT for %s", cache_key)
            return cached

        # SHIELDED join: a joiner that gives up must not cancel the shared future and
        # take every other waiter down with it.
        if cache_key in _inflight:
            logger.info("Commodity detail in-flight JOIN for %s", cache_key)
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future
        try:
            result = await self._build_commodity_detail(
                symbol, chart_range=chart_range, interval=interval
            )
            # Degradation gate. A build whose quote AND history both failed still returns
            # (the price falls back to the last close), but caching it pins the hole for
            # the whole TTL — the defect profit_power_service's gate exists to prevent.
            # The per-section caches already refuse to store their own empty results, so
            # this is the last line: never pin a priceless response.
            # `getattr`, not attribute access: the gate must judge only what it can
            # actually inspect. A build that is not a CommodityDetailResponse (test
            # doubles, future shapes) has no price to assess, so it is cached normally
            # rather than crashing the request on an AttributeError.
            price = getattr(result, "current_price", None)
            if price is None or price > 0:
                _cache_set(cache_key, result, _QUOTE_TTL)
            else:
                logger.warning(
                    "Commodity detail NOT cached for %s — no usable price "
                    "(degraded build); will rebuild on the next request",
                    cache_key,
                )
            if not future.done():
                future.set_result(result)
            return result
        except asyncio.CancelledError:
            # CancelledError is a BaseException, so it skips the handler below and would
            # leave the future unresolved — every joiner would hang for the life of the
            # process. Hand them a normal exception, then honour our own cancellation.
            if not future.done():
                future.set_exception(
                    RuntimeError("commodity detail fetch was cancelled")
                )
            raise
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            _inflight.pop(cache_key, None)

    async def get_commodity_core(
        self,
        symbol: str,
        chart_range: str = "3M",
        interval: Optional[str] = None,
    ) -> "CommodityCoreResponse":
        """FIRST PAINT: the header line and, when it is free, the chart.

        Deliberately NOT a projection of the full build — that is what
        `get_commodity_quote` is, and it is why `/quote` cannot serve first paint: on a
        cold cache it pays the entire assembly. This calls the SAME per-section builders,
        just two of them, so there is still exactly one code path per number.

        Never touches `_get_derived` (the daily history plus the S&P benchmark leg) or the
        related-quote fan-out.
        """
        from app.schemas.commodity import CommodityCoreResponse

        # Strip only a TRAILING "USD" pair suffix — a global replace is the bug the crypto
        # endpoint was fixed for (it turned USDT into T). Same three lines as the full
        # build, deliberately: a core that resolved a symbol differently would be a
        # different asset.
        symbol = symbol.upper().strip()
        if len(symbol) > 3 and symbol.endswith("USD"):
            symbol = symbol[:-3]
        fmp_symbol = _resolve_fmp_symbol(symbol)
        commodity_name = _resolve_name(symbol)

        quote, chart_points = await asyncio.gather(
            self._get_quote(fmp_symbol),
            self._get_chart(fmp_symbol, chart_range, interval, fast_only=True),
            return_exceptions=True,
        )
        if isinstance(chart_points, BaseException) or not isinstance(chart_points, list):
            logger.warning(
                "Commodity core chart failed for %s: %r — serving the header alone",
                fmp_symbol, chart_points,
            )
            chart_points = []
        if isinstance(quote, BaseException) or not isinstance(quote, dict):
            quote = {}

        price = _finite_or_none(quote.get("price")) or 0
        if price <= 0:
            # A zero-price core would paint "$0.00" as the FIRST thing on screen. Refuse:
            # the client fetches core with `try?`, so the skeleton simply stays up.
            raise ValueError(f"commodity core has no usable price for {symbol}")

        change = _finite_or_none(quote.get("change")) or 0
        change_pct = (_finite_or_none(quote.get("changePercentage"))
                      or _finite_or_none(quote.get("changesPercentage")) or 0)
        prev_close = _finite_or_none(quote.get("previousClose")) or 0
        if not change_pct and change and prev_close:
            try:
                change_pct = round((float(change) / float(prev_close)) * 100, 4)
            except (ValueError, TypeError, ZeroDivisionError):
                change_pct = 0

        chart_data = []
        for row in chart_points:
            # Defensive on BOTH required fields. `close` is a required float on the
            # response model, so a NaN or a missing key here serialises to an invalid
            # JSON token and 500s the screen from inside Starlette — where this method's
            # caller cannot catch it.
            if not isinstance(row, dict):
                continue
            date = row.get("date")
            close = _finite_or_none(row.get("close") or row.get("adjClose"))
            if not date or close is None or close <= 0:
                continue
            chart_data.append(CommodityChartPointResponse(
                date=date,
                open=_finite_or_none(row.get("open")),
                high=_finite_or_none(row.get("high")),
                low=_finite_or_none(row.get("low")),
                close=close,
                volume=_finite_or_none(row.get("volume")),
            ))

        return CommodityCoreResponse(
            symbol=symbol,
            name=commodity_name,
            current_price=round(price, 2),
            price_change=round(change, 2),
            price_change_percent=round(change_pct, 2),
            market_status=_commodity_market_status(),
            chart_data=chart_data,
        )

    async def get_commodity_quote(
        self,
        symbol: str,
        chart_range: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> "CommodityQuoteResponse":
        """The light slice behind `GET /commodities/{symbol}/quote`.

        PROJECTED from the full build rather than assembled from a second, parallel set
        of section-builders. That is deliberate: every section is already cached, so the
        full assembly costs ZERO extra FMP calls here, and a second assembly path for the
        same fields is exactly how two code paths drift into disagreeing about the same
        number — the failure mode this whole pass has been unpicking.

        What the client actually gains is the WIRE payload: ~2 KB instead of ~10 KB, and
        no need to replace its entire view model on a 30-second tick. The sections dropped
        here (`performance_periods`, `benchmark_summary`, `commodity_profile`,
        `news_articles`) are all range-independent and cannot change between refreshes.
        """
        from app.schemas.commodity import CommodityQuoteResponse

        full = await self.get_commodity_detail(
            symbol,
            chart_range=chart_range or "3M",
            interval=interval,
        )
        return CommodityQuoteResponse(
            symbol=full.symbol,
            current_price=full.current_price,
            price_change=full.price_change,
            price_change_percent=full.price_change_percent,
            market_status=full.market_status,
            # Bars only when the caller asked for a range — the 30s loop skips them on a
            # daily chart, where nothing below the last candle can have moved.
            chart_data=full.chart_data if chart_range else [],
            key_statistics_groups=full.key_statistics_groups,
            related_commodities=full.related_commodities,
        )

    # ── Tier 2 (Supabase `commodity_cache`) ───────────────────────
    #
    # The header comment above used to argue that a Supabase tier was wrong here because
    # "24-hour rows are what made the ETF/Index screens serve a day-old price". That was
    # true of the MONOLITH — one row froze `current_price` along with everything else.
    #
    # After the decomposition the objection dissolves structurally rather than being
    # patched: only sections that CANNOT contain a live price are persisted. The quote and
    # the related quotes are Tier-1-only, so a Tier-2 hit still renders a price fetched
    # seconds ago. That is why this service needs no `_refresh_volatile` — its ETF/Index
    # siblings need one precisely because they persist the whole payload.
    #
    # `get_supabase()` is called here rather than cached on `self`: the tests construct
    # this service with `__new__`, so `__init__` never runs and `self.supabase` would not
    # exist on the hot path.

    _TIER2_TTL_HOURS = 12

    @staticmethod
    def _tier2_get(cache_key: str) -> Optional[Dict[str, Any]]:
        """Read one cached section, or None. Never raises.

        Best-effort by design: until migration 149 is applied this logs a warning about
        the missing relation and returns a MISS, so Tier 2 is simply inactive and Tier 1
        carries the load — slower on a cold process, never wrong.
        """
        try:
            from app.database import get_supabase

            row = (
                get_supabase()
                .table("commodity_cache")
                .select("response_json, cached_at")
                .eq("cache_key", cache_key)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None
            entry = row.data[0]
            cached_at = datetime.fromisoformat(
                (entry.get("cached_at") or "").replace("Z", "+00:00")
            )
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=CommodityService._TIER2_TTL_HOURS):
                return None
            return entry.get("response_json")
        except Exception as e:
            logger.warning(
                "Commodity tier-2 read failed for %s: %s: %s",
                cache_key, type(e).__name__, e,
            )
            return None

    @staticmethod
    def _tier2_put(
        cache_key: str, symbol: str, category: str, payload: Dict[str, Any]
    ) -> None:
        """Persist one section. Best-effort; a failure only costs a rebuild."""
        try:
            from app.database import get_supabase

            get_supabase().table("commodity_cache").upsert(
                {
                    "cache_key": cache_key,
                    "symbol": symbol,
                    "category": category,
                    "response_json": payload,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="cache_key",
            ).execute()
        except Exception as e:
            logger.warning(
                "Commodity tier-2 write failed for %s: %s: %s",
                cache_key, type(e).__name__, e,
            )

    # ── Per-section fetchers ──────────────────────────────────────
    #
    # Each is cached under its OWN key with its OWN TTL, and the three below are keyed
    # on the SYMBOL ALONE — deliberately, because none of them depends on the chart
    # range. That is the whole saving: the 972 KB history and the quote used to be
    # re-fetched once per range pill.

    async def _get_quote(self, fmp_symbol: str) -> Dict[str, Any]:
        """Live quote. Range-independent, so every range pill shares one fetch."""
        key = f"com:quote:{fmp_symbol}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            quote = await self.fmp.get_stock_price_quote(fmp_symbol)
        except Exception as e:
            logger.warning(
                "Commodity quote fetch failed for %s: %s: %s",
                fmp_symbol, type(e).__name__, e,
            )
            return {}
        if not isinstance(quote, dict) or not quote:
            return {}
        # Only a usable quote is cached — caching {} would pin a blank price header for
        # the whole TTL, which is the degradation gate this service never had.
        _cache_set(key, quote, _QUOTE_TTL)
        return quote

    async def _get_history(self, fmp_symbol: str) -> List[Dict[str, Any]]:
        """FULL daily history, oldest-first. Range-independent and 12h-cached.

        The full history (not the old hardcoded 2010 floor) because every daily-or-coarser
        chart is now DERIVED from this one list: 3M/6M/1Y by slicing, 5Y/ALL by
        aggregating. GCUSD really starts 2007-05-29 and FMP pages at 5,000 rows, so a
        2010-capped fetch would silently truncate the ALL range.

        Costs 1-2 paged calls once per symbol per 12h, replacing one 972 KB call per
        range per 5 minutes.
        """
        key = f"com:hist:{fmp_symbol}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            from app.services.chart_helper import _fetch_all_daily
            historical = await _fetch_all_daily(self.fmp, fmp_symbol)
        except Exception as e:
            logger.warning(
                "Commodity history fetch failed for %s: %s: %s",
                fmp_symbol, type(e).__name__, e,
            )
            return []
        if not historical:
            return []
        # `date` may be an explicit JSON null (not just absent); `or ""` avoids a
        # None<str TypeError when sorting a malformed FMP row.
        historical.sort(key=lambda p: p.get("date") or "")
        _cache_set(key, historical, _HISTORY_TTL)
        return historical

    async def _get_spy_history(self) -> List[Dict[str, Any]]:
        """S&P 500 daily history, oldest-first — the benchmark every commodity is scored
        against. Keyed WITHOUT the commodity symbol on purpose: the series is identical
        for all of them, so this is one fetch per 12h for the whole service rather than
        one per commodity.

        This replaced a hardcoded `sp_benchmark=10.5`. That literal was compared against
        a window the commodity chose, under a label naming that window, so the card
        rendered an "Outperforming"/"Underperforming" verdict against a number that had
        never been measured over anything.
        """
        key = "com:spy_hist"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            from app.services.chart_helper import _fetch_all_daily
            spy = await _fetch_all_daily(self.fmp, "SPY")
        except Exception as e:
            logger.warning(
                "Commodity SPY benchmark history fetch failed: %s: %s",
                type(e).__name__, e,
            )
            return []
        if not spy:
            logger.warning("Commodity SPY benchmark history came back empty")
            return []
        spy.sort(key=lambda p: p.get("date") or "")
        _cache_set(key, spy, _HISTORY_TTL)
        return spy

    async def _get_derived(self, fmp_symbol: str) -> Dict[str, Any]:
        """Everything computed FROM the daily history, cached in both tiers.

        This is the section that makes Tier 2 worth having. `performance_periods` and the
        daily key-stat rows are functions of the 4,333-row history alone, so persisting
        them lets a cold process serve a full screen WITHOUT ever fetching the 972 KB —
        the single most expensive thing this service does.

        Deliberately stores `bench_base` (the inception close + date) rather than a
        finished `benchmark_summary`: the benchmark's total-return figure is a function of
        the LIVE price, and persisting it would put a stale price in Tier 2 — the one
        thing this design refuses to do.
        """
        key = f"com:derived:{fmp_symbol}"
        cached = _cache_get(key)
        if cached is not None:
            return cached

        tier2_key = f"{fmp_symbol}:derived"
        db = await asyncio.to_thread(self._tier2_get, tier2_key)
        if db is not None:
            logger.info("Commodity derived tier-2 HIT for %s", fmp_symbol)
            _cache_set(key, db, _DERIVED_TTL)
            return db

        historical, spy_hist = await asyncio.gather(
            self._get_history(fmp_symbol), self._get_spy_history(),
        )
        derived = self._derive_from_history(historical, spy_hist)

        # Degradation gate: a history that failed or came back empty yields a bundle of
        # nulls. Persisting it would pin an empty Performance card and a blank 200-day
        # average for 12 hours. Mirrors profit_power_service's `if degraded: skip upsert`.
        if not historical or derived.get("last_close") is None:
            logger.warning(
                "Commodity derived NOT persisted for %s (empty/failed history) — "
                "will rebuild on the next request",
                fmp_symbol,
            )
            return derived

        _cache_set(key, derived, _DERIVED_TTL)
        await asyncio.to_thread(
            self._tier2_put, tier2_key, fmp_symbol, "derived", derived
        )
        return derived

    def _derive_from_history(
        self,
        historical: List[Dict[str, Any]],
        spy_hist: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Pure: daily history -> the JSON-serialisable scalars every section needs."""
        closes = [p.get("close", 0) for p in historical if p.get("close")]
        ma_200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None

        avg_volume_30d = None
        volumes_30d = [
            p.get("volume") or 0 for p in historical[-30:] if p.get("volume")
        ]
        if volumes_30d:
            avg_volume_30d = sum(volumes_30d) / len(volumes_30d)

        # `_build_performance` baselines off `historical[-1].close`, never the live quote,
        # so its output is a pure function of the history and is safe to persist verbatim.
        performance = [p.model_dump() for p in self._build_performance(historical)]

        # ── Benchmark baseline: the SHARED window, not the commodity's own start ──
        #
        # `bench_base` is the commodity's close at the start of the window it and the
        # S&P BOTH cover, and `bench_sp_cagr` is the S&P's annualised return over that
        # same window. Storing the window rather than a finished summary is what lets
        # the commodity's own figure be recomputed from the LIVE price at response time
        # without ever persisting a price (see this method's caller).
        #
        # Neither value is a live quote: both are functions of closed daily bars, so
        # they are safe in Tier 2 for the full 12h.
        bench_base = None
        bench_sp_cagr = None
        bench_since = None
        if historical and len(historical) >= 252:
            _, bench_sp_cagr, bench_since = overlapping_cagrs(
                historical, spy_hist or [], label="commodity",
            )
            if bench_since:
                start_row = next(
                    (p for p in historical if (p.get("date") or "")[:10] >= bench_since),
                    None,
                )
                start_close = _finite_or_none((start_row or {}).get("close"))
                if start_close and start_close > 0:
                    bench_base = {
                        "close": start_close,
                        "date": (start_row.get("date") or "")[:10],
                    }
            if bench_base is None:
                logger.warning(
                    "Commodity benchmark baseline unavailable (%d history rows, "
                    "%d SPY rows) — the Performance card will omit the comparison",
                    len(historical), len(spy_hist or []),
                )

        return {
            "ma_200": _finite_or_none(ma_200),
            "avg_volume_30d": _finite_or_none(avg_volume_30d),
            "last_close": _finite_or_none(closes[-1]) if closes else None,
            "prev_close": _finite_or_none(closes[-2]) if len(closes) >= 2 else None,
            "performance_periods": performance,
            "bench_base": bench_base,
            "bench_sp_cagr": bench_sp_cagr,
            "bench_since": bench_since,
        }

    async def _get_related(
        self, root_symbol: str, related_symbols: List[str]
    ) -> List[tuple]:
        """Quotes for the sibling commodities. Range-independent.

        Keyed on the ROOT symbol rather than per sibling: the set is a fixed property of
        the root, and one key means one in-flight fan-out instead of N.
        """
        if not related_symbols:
            return []
        key = f"com:related:{root_symbol}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        results = await asyncio.gather(
            *[self.fmp.get_stock_price_quote(s) for s in related_symbols],
            return_exceptions=True,
        )
        related_quotes = [
            (sym, q)
            for sym, q in zip(related_symbols, results)
            if not isinstance(q, Exception) and q
        ]
        # Partial is fine to serve but NOT to cache: pinning a short list for the TTL is
        # how "People Also Check" silently loses a row for everyone.
        if len(related_quotes) == len(related_symbols):
            _cache_set(key, related_quotes, _RELATED_TTL)
        else:
            logger.warning(
                "Commodity related quotes partial for %s (%d/%d) — not cached",
                root_symbol, len(related_quotes), len(related_symbols),
            )
        return related_quotes

    async def _get_chart(
        self,
        fmp_symbol: str,
        chart_range: str,
        interval: Optional[str],
        fast_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Chart bars for one range, derived from `historical` wherever possible.

        Only 1D (5min) and 1W (1hour) are genuinely sub-daily and need their own FMP
        call. Everything daily-or-coarser comes out of the shared history:
          * 3M/6M/1Y  -> slice        (`_extract_chart_data`, as before)
          * 5Y/ALL    -> aggregate    (`_aggregate_prices`, which `fetch_chart_data`
                                       was already calling — after re-fetching the very
                                       history we are holding)

        `fast_only` is the first-paint contract for the `/core` endpoint: serve bars from
        Tier-2, or from a single intraday fetch, but NEVER pull the daily history to make
        them. That pull is the one thing standing between core and a sub-second answer;
        returning `[]` is honest and cheap, because the full response is already in flight
        and fills the chart a moment later. Mirrors `_get_volatile(fast_only=...)` in
        `stock_overview_service`.
        """
        from app.services.chart_helper import (
            AGGREGATED_INTERVALS,
            INTRADAY_INTERVALS,
            _aggregate_prices,
            fetch_chart_data,
            resolve_interval,
            window_by_range,
        )

        resolved = resolve_interval(chart_range, interval)

        # Non-intraday bars are close-cadence, so they persist. An intraday series must
        # NOT: a 12h-old 5-minute chart would paint yesterday's session under a live
        # header, which is precisely the staleness this design refuses to ship.
        tier2_key = f"{fmp_symbol}:chart:{chart_range}:{resolved}"
        persistable = resolved not in INTRADAY_INTERVALS

        if persistable:
            db = await asyncio.to_thread(self._tier2_get, tier2_key)
            if db:
                logger.info("Commodity chart tier-2 HIT for %s", tier2_key)
                return db

        if fast_only and resolved not in INTRADAY_INTERVALS:
            # Every remaining path needs `historical`. Under `fast_only` that pull is
            # exactly what we are here to avoid.
            logger.info(
                "Commodity core chart skipped for %s — cold section, would need the "
                "daily history", tier2_key,
            )
            return []

        historical = (
            [] if resolved in INTRADAY_INTERVALS else await self._get_history(fmp_symbol)
        )

        if resolved in AGGREGATED_INTERVALS and historical:
            bars = _aggregate_prices(historical, resolved)
            if chart_range != "ALL":
                # ALL means the whole series; every other aggregated range is windowed --
                # with the interval's indicator warm-up kept, which the local copy of this
                # trim silently dropped (5Y weekly lost ~210 leading bars, so MA(200) was
                # blank across the whole chart).
                bars = window_by_range(bars, chart_range, resolved)
            if bars:
                await asyncio.to_thread(
                    self._tier2_put, tier2_key, fmp_symbol, "chart", bars
                )
            return bars

        if resolved == "daily":
            bars = self._extract_chart_data(historical, chart_range)
            if bars:
                await asyncio.to_thread(
                    self._tier2_put, tier2_key, fmp_symbol, "chart", bars
                )
            return bars

        # Genuinely intraday (1D/1W): its own short-lived key.
        key = f"com:chart:{fmp_symbol}:{chart_range}:{resolved}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            # Commodities trade nearly 24h — do NOT apply the equity 09:30-16:00 ET
            # regular-hours filter (it would gut a ~23h market to a 6.5h slice).
            bars = await fetch_chart_data(
                self.fmp, fmp_symbol, chart_range, interval, extended_hours=True
            )
        except Exception as e:
            logger.warning(
                "Commodity intraday chart failed for %s %s: %s: %s",
                fmp_symbol, chart_range, type(e).__name__, e,
            )
            return []
        if bars:
            _cache_set(key, bars, _INTRADAY_CHART_TTL)
        return bars

    async def _build_commodity_detail(
        self, symbol: str, chart_range: str = "3M", interval: str = None
    ) -> CommodityDetailResponse:
        # Strip only a TRAILING "USD" pair suffix. A global replace is the bug the
        # crypto endpoint was fixed for (it turned USDT into T and USDC into C); harmless
        # for today's futures roots, but the same trap one new symbol away.
        symbol = symbol.upper().strip()
        if len(symbol) > 3 and symbol.endswith("USD"):
            symbol = symbol[:-3]
        fmp_symbol = _resolve_fmp_symbol(symbol)
        commodity_name = _resolve_name(symbol)
        meta = _get_meta(symbol)

        # ── Step 1: Parallel, per-section cached fetches ──────────
        #
        # All three are keyed on the SYMBOL alone and shared by every range pill. They
        # used to be re-fetched per range: 7 quotes, 7 x 972 KB of history and 28 related
        # quotes to browse one commodity. Still gathered concurrently, so a cold build is
        # no slower than before.
        #
        # The news call that used to sit here is GONE. It cost one FMP call per build and
        # returned `[]` every time — FMP publishes no news under a futures code like
        # GCUSD. The screen's News tab reads `GET /commodities/{sym}/news`, which resolves
        # proxy tickers and is already two-tier cached (6h). `news_articles` stays in the
        # response as its schema default `[]` because the iOS DTO is non-optional.
        related_symbols = meta.get("related", [])

        # `_get_derived` and `_get_chart` may both want the daily history; `_get_history`
        # is itself cached, so the second caller gets the first one's list rather than a
        # second 972 KB fetch. On a Tier-2 derived HIT with a non-daily range, the history
        # is never fetched at all.
        quote, derived, chart_points, related_quotes = await asyncio.gather(
            self._get_quote(fmp_symbol),
            self._get_derived(fmp_symbol),
            self._get_chart(fmp_symbol, chart_range, interval),
            self._get_related(symbol, related_symbols),
        )
        news_raw: List[Dict[str, Any]] = []
        # Only the paths that genuinely need raw bars still touch the history; everything
        # else now reads the small `derived` bundle.
        historical = _cache_get(f"com:hist:{fmp_symbol}") or []

        # ── Step 2: Extract quote data ────────────────────────────
        # FMP quote numerics can be bare NaN/Inf JSON tokens; those are truthy so
        # `x or 0` keeps them, and they flow into the REQUIRED current_price/
        # price_change/price_change_percent Doubles → whole commodity screen fails to
        # decode on iOS (or 500s via Starlette allow_nan=False). Coerce to finite.
        from app.services.chart_helper import _finite_or_none
        price = _finite_or_none(quote.get("price")) or 0
        change = _finite_or_none(quote.get("change")) or 0

        # A failed quote must NOT become "$0.00". `quote` degrades to `{}` above when
        # the FMP call raised, and every read below then defaults to 0 — so an FMP 429 or
        # timeout rendered gold at $0.00 (+0.00%) under a chart that still had real data.
        # Commodity is the worst place for that: it has no cache to fall back on and, unlike
        # ETF/Index, no live-price WebSocket to correct it afterwards. Recover the last
        # finite close from the history we already fetched (crypto_service does the same),
        # and only if that is missing too, fail loudly so the endpoint can return a typed
        # error the user can retry instead of a fabricated number.
        if price <= 0:
            last_close = None
            prev_close_hist = None
            for row in reversed(historical):
                if not isinstance(row, dict):
                    continue
                c = _finite_or_none(row.get("close") or row.get("adjClose"))
                if c is None or c <= 0:
                    continue
                if last_close is None:
                    last_close = c
                else:
                    prev_close_hist = c
                    break
            if last_close is None:
                raise FMPUnavailableException(
                    f"No usable quote or price history for commodity {fmp_symbol}"
                )
            logger.warning(
                "Commodity %s quote unavailable — falling back to last historical close "
                "%.4f (chart data is still live)", fmp_symbol, last_close,
            )
            price = last_close
            if prev_close_hist:
                change = last_close - prev_close_hist
                quote = dict(quote or {})
                quote.setdefault("previousClose", prev_close_hist)
        # FMP /quote returns the daily move under `changesPercentage` (plural) for
        # most symbols; some feeds use `changePercentage`. Read both, then fall
        # back to computing it from change/previousClose so commodity screens
        # never show a hard 0.00%.
        change_pct = (_finite_or_none(quote.get("changePercentage"))
                      or _finite_or_none(quote.get("changesPercentage")) or 0)
        day_high = quote.get("dayHigh") or 0
        day_low = quote.get("dayLow") or 0
        year_high = quote.get("yearHigh") or 0
        year_low = quote.get("yearLow") or 0
        volume = quote.get("volume") or 0
        avg_volume = quote.get("avgVolume") or 0
        open_price = quote.get("open") or 0
        prev_close = _finite_or_none(quote.get("previousClose")) or 0
        if not change_pct and change and prev_close:
            try:
                change_pct = round((float(change) / float(prev_close)) * 100, 4)
            except (ValueError, TypeError, ZeroDivisionError):
                change_pct = 0

        # ── Step 3: Build chart data ──────────────────────────────


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
            # A non-finite volume (bare NaN/Inf FMP token) is truthy and reaches
            # `str(int(v))`, where int(NaN)→ValueError / int(inf)→OverflowError,
            # 500-ing the whole commodity endpoint. Degrade to "—".
            if not v or (isinstance(v, float) and not math.isfinite(v)):
                return "—"
            if v >= 1_000_000:
                return f"{v / 1_000_000:.1f}M"
            if v >= 1_000:
                return f"{v / 1_000:.1f}K"
            return str(int(v))

        # From the cached `derived` bundle, NOT a re-scan of the raw history: on a Tier-2
        # hit the history was never fetched at all.
        ma_200 = derived.get("ma_200")

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
        # FMP often omits avgVolume on futures; fall back to the 30-day mean carried in
        # the cached `derived` bundle rather than re-scanning the raw history, which on a
        # Tier-2 hit was never fetched.
        if not avg_volume:
            avg_volume = derived.get("avg_volume_30d") or 0

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
        # Rebuilt from the persisted bundle. `_build_performance` baselines off the last
        # historical close (never the live quote), so its output is a pure function of the
        # history and round-trips through Tier 2 unchanged.
        performance_periods = [
            PerformancePeriodResponse(**row)
            for row in (derived.get("performance_periods") or [])
        ]

        # ── Step 6: Build news ────────────────────────────────────
        news_articles = []
        if isinstance(news_raw, list):
            for article in news_raw[:10]:
                # Guard every REQUIRED str with a trailing `or ""`/`"Unknown"`: FMP can
                # return an explicit null title/publishedDate, and `.get(k, default)`
                # only covers a MISSING key — a present-null returns None into the
                # required published_at/headline/source_name → Pydantic 500s the whole
                # commodity detail. Mirrors the crypto/etf/index _build_news builders.
                # (source_name previously fell back to the publishedDate TIMESTAMP.)
                news_articles.append(CommodityNewsArticleResponse(
                    headline=article.get("title") or article.get("text") or "",
                    source_name=article.get("site") or article.get("publisher") or article.get("source") or "Unknown",
                    source_icon=None,
                    sentiment="neutral",
                    published_at=_normalized_published_at(article),
                    thumbnail_url=article.get("image") or article.get("thumbnail_url"),
                    related_tickers=[s.strip() for s in (article.get("symbol") or "").split(",") if s.strip()],
                    summary_bullets=[],
                    article_url=article.get("url") or article.get("article_url"),
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
            # FMP returns the daily move under `changesPercentage` (plural) for most
            # symbols; reading only the singular key left every related commodity at
            # +0.00%. Mirror the main quote (both keys).
            rel_change = rq.get("changePercentage") or rq.get("changesPercentage") or 0
            related_commodities.append(RelatedCommodityResponse(
                symbol=sym,
                name=rel_name,
                # Finite-guard: a NaN/Inf from a related quote survives round() into
                # the REQUIRED price/change_percent Doubles and crashes the whole
                # CommodityDetail decode on one bad related row.
                price=round(_finite_or_none(rel_price) or 0.0, 2),
                change_percent=round(_finite_or_none(rel_change) or 0.0, 2),
            ))

        # ── Step 9: Build benchmark summary ───────────────────────
        # Use the earliest available data point for longest history
        # Recomputed from the persisted inception close + the LIVE price. The total-return
        # figure is a function of the current price, so persisting the finished summary
        # would put a stale price in Tier 2 — the one thing this design refuses to do.
        # ⚠️ Two bugs lived here, both visible on screen as confident numbers:
        #
        #   1. `avg_annual = total_return / years` is an ARITHMETIC mean, not a CAGR.
        #      Gold's ~+180% over 16 years read 11.3%/yr where the compound rate is
        #      6.6%/yr — and the label said "Average Annual Return".
        #   2. `sp_benchmark=10.5` was a hardcoded literal compared against whatever
        #      window the commodity happened to have, under a label naming that window.
        #      With no `badge_threshold` in this schema iOS falls back to 0, so the card
        #      also rendered an "Outperforming"/"Underperforming" verdict off it.
        #
        # There was a third, silent one: `if _bench_base and price` lets a NaN `price`
        # through (NaN is truthy), and it survived `round()` into the REQUIRED
        # `avg_annual_return` float, where Starlette's `allow_nan=False` 500s the whole
        # commodity detail. The `except Exception: pass` below could not catch it
        # because nothing raised.
        #
        # The commodity's side still ends on the LIVE price while the S&P's ends on its
        # last close — deliberate, and the same asymmetry this file has always had. Over
        # a 15-year CAGR the gap is orders of magnitude below the 1 dp published.
        benchmark = None
        _bench_base = derived.get("bench_base") or {}
        _live_price = _finite_or_none(price)
        if _bench_base and _live_price and _live_price > 0:
            avg_annual = cagr_between(
                _bench_base.get("close"),
                _live_price,
                _bench_base.get("date"),
                datetime.now(tz=timezone.utc).date().isoformat(),
            )
            if avg_annual is not None:
                sp_cagr = _finite_or_none(derived.get("bench_sp_cagr"))
                benchmark = BenchmarkSummaryResponse(
                    avg_annual_return=avg_annual,
                    # Required float on the wire (iOS decodes a non-optional Double),
                    # so "we could not measure it" travels in `benchmark_available`.
                    sp_benchmark=sp_cagr if sp_cagr is not None else 0.0,
                    benchmark_name="S&P 500",
                    since_date=format_since(
                        derived.get("bench_since") or _bench_base.get("date"), style="day"
                    ),
                    window_label="All-time",
                    benchmark_available=sp_cagr is not None,
                )
            else:
                logger.warning(
                    "Commodity benchmark CAGR unmeasurable for %s (base=%s @ %s, "
                    "price=%s) — omitting the Performance comparison",
                    fmp_symbol, _bench_base.get("close"), _bench_base.get("date"), price,
                )

        return CommodityDetailResponse(
            symbol=fmp_symbol,
            name=commodity_name,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
            market_status=_commodity_market_status(),
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

        from app.services.chart_helper import _finite_or_none, daily_range_days

        # The visible window PLUS the MA(200) warm-up, from the one shared definition.
        # This service used to carry its own copy of the range map with NO warm-up in it,
        # so the client's `TickerChartView.warmupCount` resolved to 0 and the moving
        # average never drew on a daily chart. index_service and stock_overview_service
        # always had the warm-up; these three never did.
        today = datetime.now(tz=timezone.utc).date()
        cutoff = (today - timedelta(days=daily_range_days(chart_range))).isoformat()

        result = []
        for p in historical:
            date = p.get("date")
            # `date` may be None (explicit null); `None >= cutoff` / `None < cutoff`
            # raises TypeError, so guard before comparing.
            if not date or date < cutoff:
                continue
            # A NaN close survives `close <= 0`, and raw open/high/low/volume can
            # carry a NaN/Inf token — either serializes to invalid JSON and 500s
            # the whole commodity detail. Route every OHLCV value through the guard.
            close = _finite_or_none(p.get("close") or p.get("adjClose"))
            if close is None or close <= 0:
                continue
            result.append({
                "date": date,
                "open": _finite_or_none(p.get("open")),
                "high": _finite_or_none(p.get("high")),
                "low": _finite_or_none(p.get("low")),
                "close": round(close, 2),
                "volume": _finite_or_none(p.get("volume")),
            })
        return result

    def _build_performance(self, historical: List[Dict]) -> List[PerformancePeriodResponse]:
        from app.services.chart_helper import _finite_or_none

        periods = [
            ("1M", 21), ("3M", 63), ("6M", 126), ("YTD", None),
            ("1Y", 252), ("3Y", 756), ("5Y", 1260), ("10Y", 2520),
        ]
        result = []
        if not historical:
            return result

        # A NaN/Inf close is truthy and survives `not x` / `x <= 0`, so it would flow
        # into change_percent as NaN and serialize to an invalid-JSON `NaN` token that
        # crashes the iOS decode of the whole screen. Route every close through the
        # finite guard (the chart path already does this).
        current_close = _finite_or_none(historical[-1].get("close"))
        if not current_close or current_close <= 0:
            return result

        for label, days in periods:
            if label == "YTD":
                # Find first trading day of current year
                year_start = datetime.now(tz=timezone.utc).strftime("%Y-01-01")
                past_close = None
                for p in historical:
                    if (p.get("date") or "") >= year_start:
                        past_close = _finite_or_none(p.get("close"))
                        break
                if not past_close or past_close <= 0:
                    continue
            else:
                # Not enough history to cover the window: OMIT the period rather than
                # clamp idx to 0 and mislabel a shorter return under the longer horizon
                # (e.g. a ~3y-old commodity's "10Y" figure). Matches the index helper.
                if len(historical) < days:
                    continue
                idx = len(historical) - days
                past_close = _finite_or_none(historical[idx].get("close"))
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
