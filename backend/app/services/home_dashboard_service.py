"""
Home Dashboard Service — aggregates the redesigned Caydex Home screen
(`HomeDashboardView`) into a single response.

Currently powers the top "Market Pulse" strip: the major US indices plus
Bitcoin and key commodities, each with a live quote and a short daily-close
sparkline. Built to grow top-to-bottom (scanners, signals, themes) behind the
same `HomeDashboardResponse`.

Caching (CLAUDE.md invariant 4, lite):
- The pulse is GLOBAL (not per-user) and fast-moving, so a 5-minute in-memory
  tier is the right freshness ceiling — same tier `index_service` uses for live
  market data. A Supabase tier would only serve stale prices here, so it is
  intentionally omitted (~6 cheap FMP quote calls repopulate a cold cache).
- An `_inflight` dedup future collapses concurrent cold-cache loads into ONE
  FMP fan-out, preventing a thundering herd when many users open Home at once.

Each symbol degrades gracefully: a failed quote/history drops that one tile
rather than failing the whole strip (mirrors the legacy `home_service`).
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client, FMPClient
from app.schemas.home_dashboard import (
    HomeDashboardResponse,
    MarketPulseItemResponse,
)

logger = logging.getLogger(__name__)


# ── Market Pulse universe ─────────────────────────────────────────────
# Order is meaningful — it drives the card order in the iOS strip.
_PULSE_SYMBOLS: List[Dict[str, str]] = [
    {"symbol": "^GSPC", "name": "S&P 500", "type": "index"},
    {"symbol": "^IXIC", "name": "Nasdaq", "type": "index"},
    {"symbol": "^DJI", "name": "Dow Jones", "type": "index"},
    {"symbol": "BTCUSD", "name": "Bitcoin", "type": "crypto"},
    {"symbol": "GCUSD", "name": "Gold", "type": "commodity"},
    {"symbol": "CLUSD", "name": "Crude Oil", "type": "commodity"},
]

_SPARKLINE_POINTS = 24          # most-recent trading-day closes per mini-chart
_SPARKLINE_LOOKBACK_DAYS = 50   # calendar window to source ~24 trading closes
_CACHE_TTL_SECONDS = 300        # 5 min — live market-data freshness ceiling
_CACHE_KEY = "dashboard"


# ── Pure helpers (unit-tested without network) ────────────────────────


def _market_status(now: Optional[datetime] = None) -> Tuple[str, bool]:
    """Return ``(display_text, is_open)`` for the US equity session.

    Uses real America/New_York time (DST-aware) so the open/closed copy is
    correct year-round. ``now`` is injectable for testing.
    """
    if now is None:
        try:
            from zoneinfo import ZoneInfo

            now = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            # Fallback: fixed EST offset if tzdata is somehow unavailable.
            now = datetime.now(tz=timezone(timedelta(hours=-5)))

    weekday = now.weekday()  # 0=Monday … 6=Sunday
    minutes = now.hour * 60 + now.minute

    if weekday >= 5:
        return "Markets Closed", False
    if minutes < 4 * 60:                 # before 4:00 AM
        return "Markets Closed", False
    if minutes < 9 * 60 + 30:            # 4:00 AM – 9:30 AM
        return "Pre-Market", False
    if minutes < 16 * 60:                # 9:30 AM – 4:00 PM
        return "Markets Open", True
    if minutes < 20 * 60:                # 4:00 PM – 8:00 PM
        return "After Hours", False
    return "Markets Closed", False


def _extract_sparkline(raw: Any, points: int = _SPARKLINE_POINTS) -> List[float]:
    """Pure transform: FMP historical payload → oldest-first list of closes.

    Robust against the messy shapes FMP returns:
    - list OR ``{"historical": [...]}`` dict OR anything else → [] when unusable
    - unknown ordering (plan-dependent) → sorts newest-first, then reverses so
      the returned series is OLDEST-first (ascending in time = the iOS
      "ascending = rising price" contract)
    - missing / None / non-numeric / non-positive closes → skipped
    - ``adjClose`` fallback when ``close`` is absent

    Never fabricates a synthetic series — returns [] so the iOS sparkline
    simply draws nothing rather than a fake trend.
    """
    if isinstance(raw, list):
        historical = raw
    elif isinstance(raw, dict):
        historical = raw.get("historical", []) or []
    else:
        historical = []

    if not historical:
        return []

    historical = sorted(
        historical, key=lambda p: (p.get("date") or "") if isinstance(p, dict) else "",
        reverse=True,
    )
    recent = list(historical[:points])
    recent.reverse()  # oldest-first

    closes: List[float] = []
    for day in recent:
        if not isinstance(day, dict):
            continue
        c = day.get("close")
        if c is None:
            c = day.get("adjClose")
        try:
            cf = float(c)
        except (TypeError, ValueError):
            continue
        if cf > 0:
            closes.append(round(cf, 2))
    return closes


# ── Service ───────────────────────────────────────────────────────────


class HomeDashboardService:
    """Builds the aggregated Caydex Home dashboard from FMP market data."""

    # Class-level so the cache/dedup are shared across requests.
    _cache: Dict[str, Tuple[float, HomeDashboardResponse]] = {}
    _inflight: Dict[str, asyncio.Future] = {}

    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()

    # ── Public API ────────────────────────────────────────────────────

    async def get_dashboard(self) -> HomeDashboardResponse:
        """Return the aggregated dashboard, cache-aside with in-flight dedup."""
        cached = self._cache.get(_CACHE_KEY)
        if cached is not None and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            logger.debug("Home dashboard served from in-memory cache")
            return cached[1]

        inflight = self._inflight.get(_CACHE_KEY)
        if inflight is not None:
            logger.debug("Home dashboard joining in-flight fetch")
            return await inflight

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[_CACHE_KEY] = fut
        try:
            result = await self._build_dashboard()
            self._cache[_CACHE_KEY] = (time.time(), result)  # cache only on success
            if not fut.done():
                fut.set_result(result)
            return result
        except BaseException as exc:  # propagate to all awaiters, then re-raise
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(_CACHE_KEY, None)

    # ── Assembly ──────────────────────────────────────────────────────

    async def _build_dashboard(self) -> HomeDashboardResponse:
        status_text, is_open = _market_status()

        results = await asyncio.gather(
            *[self._fetch_pulse_item(cfg) for cfg in _PULSE_SYMBOLS],
            return_exceptions=True,
        )

        pulse: List[MarketPulseItemResponse] = []
        for cfg, res in zip(_PULSE_SYMBOLS, results):
            if isinstance(res, BaseException):
                logger.warning(
                    "Pulse item %s failed: %s: %s",
                    cfg["symbol"], type(res).__name__, res,
                )
                continue
            if res is not None:
                pulse.append(res)

        if not pulse:
            logger.warning("Home dashboard: all %d pulse symbols failed", len(_PULSE_SYMBOLS))

        return HomeDashboardResponse(
            market_status_text=status_text,
            market_is_open=is_open,
            pulse=pulse,
        )

    async def _fetch_pulse_item(
        self, cfg: Dict[str, str]
    ) -> Optional[MarketPulseItemResponse]:
        """Fetch one tile: a live quote + a daily-close sparkline, concurrently.

        A missing quote/price drops the tile (returns None). Sparkline failure is
        non-fatal — the tile still renders with an empty series.
        """
        symbol = cfg["symbol"]
        quote, spark = await asyncio.gather(
            self.fmp.get_stock_price_quote(symbol),
            self._fetch_sparkline(symbol),
        )

        if not quote:
            logger.warning("No quote for pulse symbol %s — dropping tile", symbol)
            return None

        price = quote.get("price")
        if price is None:
            logger.warning("Quote for %s missing price — dropping tile", symbol)
            return None

        # FMP returns the % field as `changesPercentage`; some endpoints/versions
        # use the singular `changePercentage`. Accept either.
        change = quote.get("changesPercentage")
        if change is None:
            change = quote.get("changePercentage")

        try:
            price_f = float(price)
        except (TypeError, ValueError):
            logger.warning("Quote for %s has non-numeric price %r — dropping tile", symbol, price)
            return None

        try:
            change_f = float(change) if change is not None else 0.0
        except (TypeError, ValueError):
            change_f = 0.0

        return MarketPulseItemResponse(
            symbol=symbol,
            name=cfg["name"],
            type=cfg["type"],
            price=round(price_f, 2),
            change_percent=round(change_f, 2),
            spark=spark,
        )

    async def _fetch_sparkline(self, symbol: str) -> List[float]:
        """Last ~24 daily closes (oldest-first) for the mini-chart, or []."""
        try:
            to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            from_date = (
                datetime.now(timezone.utc) - timedelta(days=_SPARKLINE_LOOKBACK_DAYS)
            ).strftime("%Y-%m-%d")
            raw = await self.fmp.get_historical_prices(
                symbol, from_date=from_date, to_date=to_date
            )
            return _extract_sparkline(raw)
        except Exception as exc:
            logger.warning(
                "Sparkline for %s failed: %s: %s", symbol, type(exc).__name__, exc
            )
            return []


# ── Singleton ─────────────────────────────────────────────────────────

_service: Optional[HomeDashboardService] = None


def get_home_dashboard_service() -> HomeDashboardService:
    global _service
    if _service is None:
        _service = HomeDashboardService()
    return _service
