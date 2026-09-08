"""Market movers and sector/industry performance, rebuilt on entitled endpoints.

WHY THIS EXISTS
---------------
FMP's package enforcement (2026-09-03) took the whole **Market Performance** dataset,
which is named by none of the nine packages on the Order Form. All eight of its endpoints
answer `402 Restricted Endpoint`:

    biggest-gainers · biggest-losers · most-actives
    sector-performance-snapshot · industry-performance-snapshot
    sector-pe-snapshot · industry-pe-snapshot · historical-sector-performance

Those fed Home's "Today's Top Movers" and "Heavy Traffic" cards, the sector strip on the
index screens, the widget bands, and the sector/industry context on every stock overview.

WHAT REPLACES THEM
------------------
One entitled call. `/stable/company-screener` returns the whole US universe in ~1 s with
`price`, `volume`, `avgVolume`, `marketCap`, `sector`, `industry`, `isEtf`, `isFund` —
everything the ranking needs except a previous close, which comes from
`market_close_snapshot` (migrations 157/158, fed daily from `/stable/batch-eod`).

THIS IS CHEAPER THAN WHAT IT REPLACES, not just a substitute. The old Home scanner made
three FMP calls to seed a candidate list and then a **profile fan-out in chunks of 50** to
get marketCap / averageVolume / isEtf for each candidate. The screener carries all of it
inline, so an N-call fan-out collapses into the single sweep `price_service` already
caches and shares.

AND IT RANKS BETTER. FMP's `biggest-gainers` is dominated by sub-$300M names and leveraged
ETFs — measured on 2026-09-07, six of its top twenty at a $300M floor were 2x/3x products
(MVLL, KORU, TSLQ, MULL, MUU, SOXL). Ranking a quality-filtered universe ourselves means
the movers are companies a user has heard of, and the thresholds are ours to tune.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.services.price_service import PriceService, _finite, price_source

logger = logging.getLogger(__name__)

# Closes move once per session. An hour keeps a redeploy from re-paying the 63-request
# sweep on every request while never serving a stale session.
_CLOSES_TTL = 3600.0
# The derived universe rides the screener's own 60 s freshness.
_UNIVERSE_TTL = 60.0

# An "average move" computed from two members is noise presented as a statistic. Sectors
# always clear this easily (11 groups over ~11k symbols); the long tail of ~150 industries
# does not, and publishing those would put a made-up number on a real screen.
_MIN_GROUP_MEMBERS = 5

_cache: Dict[str, Tuple[float, Any]] = {}
_inflight: Dict[str, asyncio.Future] = {}


def _cache_get(key: str, ttl: float) -> Optional[Any]:
    hit = _cache.get(key)
    if hit is None:
        return None
    ts, value = hit
    if time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


class MarketMoversService:
    """Screener-derived movers. One instance per process."""

    # ── the close map ─────────────────────────────────────────────────────────────

    async def _all_closes(self) -> Dict[str, Dict[str, Any]]:
        """Every stored close, keyed by symbol.

        Loaded whole rather than per-symbol because the ranking needs a change % for the
        ENTIRE universe, and PostgREST caps a response at 1,000 rows regardless of the
        `range` asked for (verified: `.range(0, 49999)` still returns 1,000). So it is 63
        requests / ~6.6 s either way — worth doing once an hour behind a shared future,
        never per request.
        """
        key = "movers:closes"
        hit = _cache_get(key, _CLOSES_TTL)
        if hit is not None:
            return hit
        if key in _inflight:
            return await asyncio.shield(_inflight[key])

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        _inflight[key] = future
        try:
            closes = await asyncio.to_thread(self._select_all_closes)
            _cache_set(key, closes)
            if not future.done():
                future.set_result(closes)
            return closes
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            _inflight.pop(key, None)

    @staticmethod
    def _select_all_closes() -> Dict[str, Dict[str, Any]]:
        supabase = get_supabase()
        out: Dict[str, Dict[str, Any]] = {}
        start = 0
        PAGE = 1000
        while True:
            rows = (
                supabase.table("market_close_snapshot")
                .select("symbol,close,previous_close")
                .range(start, start + PAGE - 1)
                .execute()
            ).data or []
            if not rows:
                break
            for r in rows:
                sym = (r.get("symbol") or "").upper()
                if sym:
                    out[sym] = r
            if len(rows) < PAGE:
                break
            start += PAGE
        return out

    # ── the derived universe ──────────────────────────────────────────────────────

    async def get_universe(self) -> Dict[str, Dict[str, Any]]:
        """Screener rows reshaped to look like an FMP **company profile**, plus a change %.

        Profile-shaped on purpose. `home_dashboard_service` already ranks a `profile_map`
        through `_is_quality_company` / `_movers_from_universe` / `_volume_rows`, and that
        logic is well tested and carries several hard-won guards (the signed-zero loser
        that paints green on iOS; the NaN that slips a `<` comparison). Feeding it the same
        shape from a different source means the ranking is untouched — only where the rows
        come from changes.

        Note the key rename: the screener says `avgVolume`, a profile says `averageVolume`,
        and `_is_quality_company` reads the profile spelling. Getting that wrong drops
        EVERY row at the quality gate, silently, because a missing averageVolume fails it.
        """
        key = "movers:universe"
        hit = _cache_get(key, _UNIVERSE_TTL)
        if hit is not None:
            return hit

        ps = price_source()
        rows, closes = await asyncio.gather(
            ps._get_universe(), self._all_closes(), return_exceptions=True,
        )
        if isinstance(rows, Exception):
            logger.warning("movers: screener universe unavailable: %s: %s",
                           type(rows).__name__, rows)
            return {}
        if isinstance(closes, Exception):
            logger.warning("movers: close map unavailable: %s: %s",
                           type(closes).__name__, closes)
            closes = {}

        out: Dict[str, Dict[str, Any]] = {}
        for symbol, r in rows.items():
            price = _finite(r.get("price"))
            if price is None or price <= 0:
                continue
            prev = PriceService._pick_denominator(price, closes.get(symbol))
            change_pct = None
            if prev is not None and prev > 0:
                change_pct = (price / prev - 1) * 100.0
            # change_pct stays None when there is no usable previous close. Callers must
            # skip those rather than treat them as 0.0% — a fabricated flat day on a real
            # company is worse than an absent row.
            out[symbol] = {
                "symbol": symbol,
                "companyName": r.get("companyName"),
                "price": price,
                "marketCap": _finite(r.get("marketCap")),
                "volume": _finite(r.get("volume")),
                "averageVolume": _finite(r.get("avgVolume")),   # profile spelling
                "changePercentage": change_pct,
                "changesPercentage": change_pct,
                "sector": r.get("sector"),
                "industry": r.get("industry"),
                "exchange": r.get("exchangeShortName") or r.get("exchange"),
                "isEtf": bool(r.get("isEtf")),
                "isFund": bool(r.get("isFund")),
            }
        _cache_set(key, out)
        return out

    async def get_scanner_inputs(self) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, float]]:
        """`(profile_map, change_map)` — the two arguments Home's scanner already takes."""
        universe = await self.get_universe()
        change_map = {
            sym: row["changePercentage"]
            for sym, row in universe.items()
            if row.get("changePercentage") is not None
        }
        return universe, change_map

    # ── sector / industry performance ─────────────────────────────────────────────

    async def _group_performance(self, field: str) -> List[Dict[str, Any]]:
        universe = await self.get_universe()
        buckets: Dict[str, List[float]] = defaultdict(list)
        for row in universe.values():
            # Companies only. A leveraged ETF's 3x move would swamp the average of the
            # sector it nominally tracks.
            if row.get("isEtf") or row.get("isFund"):
                continue
            name = (row.get(field) or "").strip()
            change = row.get("changePercentage")
            if name and change is not None:
                buckets[name].append(change)

        out: List[Dict[str, Any]] = []
        for name, changes in buckets.items():
            if len(changes) < _MIN_GROUP_MEMBERS:
                # Dropped, not published with a caveat: a one-line card has nowhere to
                # show "n=2", so the honest option is absence.
                logger.debug("movers: dropping %s %r — only %d members",
                             field, name, len(changes))
                continue
            out.append({
                field: name,
                # Equal-weighted, matching what FMP's snapshot returned (`averageChange`),
                # so every downstream consumer reads the same magnitude it always did.
                "changesPercentage": round(sum(changes) / len(changes), 4),
                "constituents": len(changes),
            })
        out.sort(key=lambda r: r["changesPercentage"], reverse=True)
        return out

    async def get_sector_performance(self) -> List[Dict[str, Any]]:
        """`[{"sector": ..., "changesPercentage": ...}]` — the shape callers already read."""
        return await self._group_performance("sector")

    async def get_industry_performance(self) -> List[Dict[str, Any]]:
        """`[{"industry": ..., "changesPercentage": ...}]`."""
        return await self._group_performance("industry")


_service: Optional[MarketMoversService] = None


def get_market_movers_service() -> MarketMoversService:
    global _service
    if _service is None:
        _service = MarketMoversService()
    return _service
