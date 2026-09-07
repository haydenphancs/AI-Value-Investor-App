"""The single price primitive, rebuilt on endpoints the FMP contract actually grants.

WHY THIS EXISTS
---------------
FMP enforced its Data Packages on 2026-09-03. "Real-time Market Data" — `quote`,
`batch-quote`, `batch-quote-short`, `stock-price-change`, `aftermarket-quote` — is not one
we bought, so all five answer `402 Restricted Endpoint`. Those two of them supplied every
price in the app, across 39 call sites in 27 files: Home tiles, the watchlist, Updates
pills, portfolio valuation, price alerts, the widget, the AI report's macro block.

WHAT REPLACES THEM
------------------
Two entitled endpoints, each covering half the problem:

===================  ==========================  ==========================================
                     ``/stable/profile`` (pkg 3)  ``/stable/company-screener``
===================  ==========================  ==========================================
symbols per call     ONE (``?symbol=A,B`` → [])   whole US universe, ~1 s
price                live                         live
change / change %    **yes**                      **NO — the field does not exist**
volume, marketCap    yes                          yes
===================  ==========================  ==========================================

So single-symbol quotes come from `profile` complete. A BATCH change% has no upstream
source at all, and is computed here against the previous official close held in
``market_close_snapshot`` (migration 157), refreshed daily from `/stable/batch-eod`.

THE CONTRACT THIS MODULE KEEPS
------------------------------
`get_quote` / `get_quotes` return **quote-shaped dicts** — the same keys the FMP
`quote` row carried, including the `changesPercentage` spelling alongside
`changePercentage`, because consumers read both. That is deliberate: it makes migrating 39
call sites a change of *which function they call*, not a rewrite of how each one reads its
fields, which is the difference between a mechanical diff and 27 chances to introduce a
subtle bug.

TWO INVARIANTS, both learned the hard way in this repo
------------------------------------------------------
1. **An unknown number is ``None``, never ``0.0``.** A missing previous close means the
   day change is unknown; emitting ``0.00%`` invents a fact. The repo has shipped this bug
   more than once — the ETF "Well Diversified" badge on zero data, and `index_service`
   painting ``$0.00`` under a live market-status badge.
2. **Never persist a live price.** Only the settled close is written to Supabase. Anything
   with a price in it stays in the in-process tier, where staleness is bounded by the TTL.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.integrations.fmp_entitlements import is_blocked_symbol

logger = logging.getLogger(__name__)

# ── Tiering ───────────────────────────────────────────────────────────────────────
# The screener sweep is ~1.5 s for ~8k rows, and every symbol on screen shares it, so a
# short TTL turns a burst of tile requests into one upstream call. 60 s matches the
# freshness a user perceives on a price and is what `home_dashboard_service` already
# assumed of batch-quote.
_UNIVERSE_TTL = 60.0

# A single profile is cheap; this only collapses the duplicate calls a single screen makes.
_QUOTE_TTL = 30.0

# Previous closes move once per session. Held for an hour so a restart re-reads Supabase
# rather than the 11.7 MB bulk endpoint.
_CLOSES_TTL = 3600.0

# `marketCapMoreThan` trims the long tail of shells and delisted husks that would otherwise
# consume most of the 10,000-row ceiling. $50M keeps every symbol with a detail screen
# while leaving ~2k rows of headroom under the cap.
_UNIVERSE_MIN_MARKET_CAP = 50_000_000
_UNIVERSE_EXCHANGES = "NASDAQ,NYSE,AMEX"

# `/stable/company-screener` hard-caps at 10,000 rows per call regardless of `limit`
# (verified: limit=20000 and limit=50000 both return exactly 10,000). It DOES paginate.
_SCREENER_PAGE_SIZE = 10_000
_SCREENER_MAX_PAGES = 4

# A holiday is a weekday with no session, so the ingest asks the data rather than
# carrying a calendar. Bounded so an upstream outage cannot spin: 5 steps covers the
# longest US market closure in living memory (Sandy, 2 sessions) with room to spare.
_MAX_SESSION_LOOKBACK = 5

_cache: Dict[str, Tuple[float, Any]] = {}
_inflight: Dict[str, asyncio.Future] = {}


def _cache_get(key: str, ttl: float) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


def _finite(value: Any) -> Optional[float]:
    """A float, or None for anything that is not a real finite number.

    FMP emits NaN and Infinity for thin or just-listed symbols. Those serialize to invalid
    JSON under `allow_nan=False` and 500 the screen, and NaN additionally defeats ordinary
    `<= 0` guards *and* `except (TypeError, ValueError)` — the trap recorded in
    `project_whale_tab_deep_check_2026_08`. Guard on `math.isfinite`, not on truthiness.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_range_band(raw: Any) -> tuple[Optional[float], Optional[float]]:
    """Split FMP /stable's ``"low-high"`` 52-week band into ``(low, high)`` floats.

    `/stable` folds the 52-week band into a single string on `profile` (`"223.78-344.57"`)
    instead of the `yearHigh`/`yearLow` numbers the retired `quote` endpoint carried. Seven
    consumers still read those two keys — `etf_service:1024`, `index_service:1167-1169`,
    `commodity_service:1076-1078`, `chat_service:984-992`, `stock_overview_service:972`, and
    the report collector — so the band is parsed HERE, once, rather than at each of them.

    Returns ``(None, None)`` for anything unparseable: a missing band must leave the fields
    absent so callers show "—", never invent a number.
    """
    if not isinstance(raw, str):
        return (None, None)
    parts = raw.split("-")
    if len(parts) != 2:
        return (None, None)
    try:
        lo, hi = float(parts[0].strip()), float(parts[1].strip())
    except (TypeError, ValueError):
        return (None, None)
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return (None, None)
    return (min(lo, hi), max(lo, hi))


class PriceService:
    """Quote-shaped prices from entitled endpoints. One instance per process."""

    # ── shaping ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _shape(
        *,
        symbol: str,
        name: Optional[str],
        price: Optional[float],
        previous_close: Optional[float],
        change: Optional[float],
        change_pct: Optional[float],
        volume: Optional[float],
        avg_volume: Optional[float],
        market_cap: Optional[float],
        exchange: Optional[str],
        year_low: Optional[float] = None,
        year_high: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Build one quote-shaped row.

        `changesPercentage` is emitted alongside `changePercentage` because consumers read
        BOTH spellings — 32 sites use one, 25 the other. Dropping either would silently
        blank a field on roughly half the screens.
        """
        row: Dict[str, Any] = {
            "symbol": symbol,
            "name": name,
            "price": price,
            "change": change,
            "changePercentage": change_pct,
            "changesPercentage": change_pct,   # legacy spelling, still widely read
            "previousClose": previous_close,
            "volume": volume,
            "avgVolume": avg_volume,
            "marketCap": market_cap,
            "exchange": exchange,
        }
        # 52-week band: OMITTED when unknown, never emitted as None.
        #
        # The distinction matters and it is the opposite of the `change` fields above. There,
        # `None` is meaningful — "unknown, and 0.0 would be a fabricated flat day" — and every
        # consumer guards for it. Here, consumers use `quote.get("yearHigh", 0)`, and
        # `dict.get` returns None for a PRESENT-but-None key, so emitting None would disarm
        # their default and hand a `None` to `f"{...:.2f}"`. Absent keeps the default reachable.
        if year_low is not None:
            row["yearLow"] = year_low
        if year_high is not None:
            row["yearHigh"] = year_high
        return row

    @classmethod
    def _from_profile(cls, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        symbol = (row.get("symbol") or "").upper()
        if not symbol:
            return None
        price = _finite(row.get("price"))
        change = _finite(row.get("change"))
        # profile has no previousClose; it is exactly price - change when both are real.
        prev = price - change if (price is not None and change is not None) else None
        # `/stable/profile` carries the 52-week band as a "low-high" string, which is the only
        # entitled source for it now that `quote` is 402. Parsed here so every consumer of a
        # single quote gets it back; the batch (screener) path has no band and omits both keys.
        year_low, year_high = parse_range_band(row.get("range"))
        return cls._shape(
            symbol=symbol,
            name=row.get("companyName"),
            price=price,
            previous_close=prev,
            change=change,
            change_pct=_finite(row.get("changePercentage")),
            volume=_finite(row.get("volume")),
            avg_volume=_finite(row.get("averageVolume")),
            market_cap=_finite(row.get("marketCap")),
            exchange=row.get("exchange"),
            year_low=year_low,
            year_high=year_high,
        )

    @staticmethod
    def _pick_denominator(
        price: Optional[float], snap: Optional[Dict[str, Any]]
    ) -> Optional[float]:
        """The close of the session BEFORE the one this price belongs to.

        ⚠️ This choice is the whole point of migration 158, and getting it wrong is
        invisible. The live price is the price of the most recent session that has one:
        today while the market is open, and the last official close while it is shut. So
        using the latest stored close unconditionally makes `price == close` every night,
        every weekend and every holiday, and the day change collapses to exactly 0.00% —
        a fabricated flat market on every tile. Found by cross-checking the batch path
        against `profile`, which reported -2.51% for a symbol the batch path called flat.

        Deciding by comparing price to close, rather than by asking whether the market is
        open, keeps this correct across the open and the close with no dependency on
        session state, holiday calendars, or when the ingest job happened to run.
        """
        if snap is None or price is None:
            return None
        close = _finite(snap.get("close"))
        prev = _finite(snap.get("previous_close"))
        if close is None:
            return prev
        # A price that has moved off the stored close belongs to a LATER session, so that
        # close is the right denominator. Compared with a relative epsilon because these
        # are floats round-tripped through JSON and Postgres NUMERIC.
        if abs(price - close) > max(abs(close), 1.0) * 1e-9:
            return close
        return prev

    @classmethod
    def _from_screener(
        cls, row: Dict[str, Any], snap: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        symbol = (row.get("symbol") or "").upper()
        if not symbol:
            return None
        price = _finite(row.get("price"))
        prev = cls._pick_denominator(price, snap)

        change = change_pct = None
        if price is not None and prev is not None and prev > 0:
            change = price - prev
            change_pct = (change / prev) * 100.0
        # else: left as None on purpose. No previous close means the day change is
        # genuinely unknown, and 0.00% would be a fabricated fact (invariant 1).

        return cls._shape(
            symbol=symbol,
            name=row.get("companyName"),
            price=price,
            previous_close=prev,
            change=change,
            change_pct=change_pct,
            volume=_finite(row.get("volume")),
            avg_volume=_finite(row.get("avgVolume")),
            market_cap=_finite(row.get("marketCap")),
            exchange=row.get("exchangeShortName") or row.get("exchange"),
        )

    # ── single symbol ─────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """One quote-shaped row, or ``{}``. Replaces `FMPClient.get_stock_price_quote`.

        Returns ``{}`` rather than ``None`` on a miss, deliberately: that is the exact
        contract the method it replaces had (`return data[0] if data else {}`), so all 20
        call sites keep working unchanged. Several of them sit inside `asyncio.gather(...)`
        lists where an `or {}` cannot be applied to a coroutine, so a `None` here would
        have meant restructuring those call sites — and every one of them would have been
        a chance to introduce an `AttributeError` on a degraded path that only fires when
        upstream is already unhappy. Callers test falsiness (`if not quote`), which is
        identical for both.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return {}
        if is_blocked_symbol(sym):
            # Index / commodity / crypto / FX. Empty so the caller hides the surface;
            # raising here would turn "not covered" into an error page.
            logger.debug("price_service: %s is outside the FMP licence", sym)
            return {}

        key = f"price:quote:{sym}"
        hit = _cache_get(key, _QUOTE_TTL)
        if hit is not None:
            return hit

        try:
            rows = await get_fmp_client().get_company_profile(sym)
        except Exception as e:
            logger.warning("price_service: profile failed for %s: %s: %s",
                           sym, type(e).__name__, e)
            return {}

        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or not rows:
            return {}
        quote = self._from_profile(rows[0])
        if quote is None:
            return {}
        _cache_set(key, quote)
        return quote

    # ── batch ─────────────────────────────────────────────────────────────────────

    async def get_quotes(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """Quote-shaped rows keyed by UPPERCASE symbol. Replaces `get_batch_quotes_bulk`.

        Returns only the symbols it could resolve — a caller must treat a missing key as
        "no data" and drop the row, exactly as it did when batch-quote omitted a symbol.
        """
        wanted = {(s or "").strip().upper() for s in symbols if s and s.strip()}
        wanted = {s for s in wanted if not is_blocked_symbol(s)}
        if not wanted:
            return {}

        universe, closes = await asyncio.gather(
            self._get_universe(),
            self.get_close_snapshots(wanted),
            return_exceptions=True,
        )
        if isinstance(universe, Exception):
            logger.warning("price_service: universe unavailable: %s: %s",
                           type(universe).__name__, universe)
            universe = {}
        if isinstance(closes, Exception):
            logger.warning("price_service: previous closes unavailable: %s: %s",
                           type(closes).__name__, closes)
            closes = {}

        out: Dict[str, Dict[str, Any]] = {}
        for sym in wanted:
            row = universe.get(sym)
            if row is None:
                continue
            quote = self._from_screener(row, closes.get(sym))
            if quote is not None and quote.get("price") is not None:
                out[sym] = quote

        missing = wanted - set(out)
        if missing:
            # The screener covers actively-traded US listings above the cap. Anything else
            # — a foreign listing, a sub-$50M microcap, a brand-new ticker — falls through
            # to the single-symbol path rather than being silently absent.
            resolved = await asyncio.gather(
                *(self.get_quote(s) for s in sorted(missing)),
                return_exceptions=True,
            )
            for quote in resolved:
                if isinstance(quote, dict) and quote.get("symbol"):
                    out[quote["symbol"]] = quote

        return out

    async def get_quotes_list(self, symbols: Sequence[str]) -> List[Dict[str, Any]]:
        """`get_quotes` as a LIST — the drop-in shape for `get_batch_quotes_bulk`.

        Every one of the 19 former batch-quote call sites iterates the result and builds
        its own `{symbol: row}` map, so returning a list keeps each migration a one-line
        change instead of a restructure. Prefer `get_quotes` in new code: it hands back
        the map those callers were building by hand.

        Rows come back in the ORDER THE SYMBOLS WERE ASKED FOR, which `batch-quote` also
        did. That is load-bearing, not cosmetic: callers such as
        `portfolio_insights_service` rank straight off this list, so returning
        `dict.values()` silently reordered their output. Caught by a ranking test.

        Symbols that could not be resolved are ABSENT, exactly as `batch-quote` omitted
        them — callers already skip a missing symbol rather than rendering a zero.
        """
        resolved = await self.get_quotes(symbols)
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for raw in symbols:
            sym = (raw or "").strip().upper()
            if sym in resolved and sym not in seen:
                seen.add(sym)
                out.append(resolved[sym])
        return out

    async def _get_universe(self) -> Dict[str, Dict[str, Any]]:
        """The screener sweep, keyed by symbol. One upstream call shared by every caller."""
        key = "price:universe"
        hit = _cache_get(key, _UNIVERSE_TTL)
        if hit is not None:
            return hit

        if key in _inflight:
            # Shielded so a caller that times out cannot cancel the shared fetch and leave
            # every other awaiter with a CancelledError.
            return await asyncio.shield(_inflight[key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[key] = future
        try:
            rows = await self._fetch_universe_pages()
            universe = {}
            for row in rows:
                sym = (row.get("symbol") or "").upper()
                if sym and not is_blocked_symbol(sym):
                    universe[sym] = row
            _cache_set(key, universe)
            if not future.done():
                future.set_result(universe)
            return universe
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            _inflight.pop(key, None)

    async def _fetch_universe_pages(self) -> List[Dict[str, Any]]:
        fmp = get_fmp_client()
        rows: List[Dict[str, Any]] = []
        for page in range(_SCREENER_MAX_PAGES):
            batch = await fmp.get_company_screener(
                market_cap_more_than=_UNIVERSE_MIN_MARKET_CAP,
                exchange=_UNIVERSE_EXCHANGES,
                actively_trading=True,
                limit=_SCREENER_PAGE_SIZE,
                page=page,
            )
            if not isinstance(batch, list) or not batch:
                break
            rows.extend(batch)
            if len(batch) < _SCREENER_PAGE_SIZE:
                break
        return rows

    # ── previous closes ───────────────────────────────────────────────────────────

    async def get_close_snapshots(
        self, symbols: Iterable[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Stored close AND previous close per symbol. Missing symbols are simply absent.

        Both are needed: which one is the day-change denominator depends on whether the
        live price has moved past the stored close — see `_pick_denominator`.
        """
        wanted = sorted({(s or "").upper() for s in symbols if s})
        if not wanted:
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        lookup: List[str] = []
        for sym in wanted:
            hit = _cache_get(f"price:close:{sym}", _CLOSES_TTL)
            if hit is not None:
                out[sym] = hit
            else:
                lookup.append(sym)
        if not lookup:
            return out

        try:
            rows = await asyncio.to_thread(self._select_closes, lookup)
        except Exception as e:
            # Until migration 157 is applied this is a missing relation. Degrade to "no
            # change %" rather than failing the whole screen.
            logger.warning("price_service: close lookup failed: %s: %s",
                           type(e).__name__, e)
            return out

        for row in rows:
            sym = (row.get("symbol") or "").upper()
            if not sym:
                continue
            snap = {"close": _finite(row.get("close")),
                    "previous_close": _finite(row.get("previous_close"))}
            if snap["close"] is None and snap["previous_close"] is None:
                continue
            out[sym] = snap
            _cache_set(f"price:close:{sym}", snap)
        return out

    @staticmethod
    def _select_closes(symbols: List[str]) -> List[Dict[str, Any]]:
        """Synchronous Supabase read — call via `asyncio.to_thread`.

        Chunked because a very long `in_` list becomes a URL longer than PostgREST will
        accept, which fails the whole lookup rather than the tail of it.
        """
        supabase = get_supabase()
        rows: List[Dict[str, Any]] = []
        CHUNK = 200
        for i in range(0, len(symbols), CHUNK):
            chunk = symbols[i:i + CHUNK]
            resp = (
                supabase.table("market_close_snapshot")
                .select("symbol,close,previous_close")
                .in_("symbol", chunk)
                .execute()
            )
            rows.extend(resp.data or [])
        return rows

    # ── daily ingest ──────────────────────────────────────────────────────────────

    async def refresh_close_snapshot(self, trade_date: Optional[str] = None) -> int:
        """Ingest the two most recent sessions' official closes. Returns rows written.

        TWO sessions, not one. The day-change denominator is the close of the session
        BEFORE the one the live price belongs to, and while the market is shut the live
        price already IS the latest close — so storing only that one makes every change %
        read 0.00% overnight and at weekends (migration 158).

        🔴 Index, commodity, crypto and FX symbols are DROPPED here, deliberately.
        `batch-eod` includes `^GSPC`, `GCUSD`, `BTCUSD` and `EURUSD` even though the
        per-symbol `historical-price-eod/full` answers 402 for every one of them — FMP
        enforces the symbol block on one endpoint and not the other. Ingesting them would
        be taking data we did not buy through a gap in the vendor's enforcement, which is
        what ToS §2.10 (monitor and terminate) is written for. Do not remove this filter to
        make an index chart work; buy the package instead.
        """
        latest_date, latest = await self._fetch_latest_session(trade_date)
        if not latest:
            logger.warning("price_service: no batch-eod session found to ingest")
            return 0

        # The session before it. Walking back from `latest_date` rather than from today,
        # so a holiday run does not silently pair two non-adjacent sessions.
        prev_date, prev_rows = await self._fetch_latest_session(
            self._step_back(latest_date)
        )
        prev_by_symbol = {
            (r.get("symbol") or "").upper(): _finite(r.get("close"))
            for r in prev_rows
        }
        if not prev_by_symbol:
            logger.warning(
                "price_service: no prior session before %s — change %% will be unknown "
                "until the next successful ingest", latest_date,
            )

        payload: List[Dict[str, Any]] = []
        skipped_blocked = 0
        for row in latest:
            sym = (row.get("symbol") or "").upper()
            if not sym:
                continue
            if is_blocked_symbol(sym):
                skipped_blocked += 1
                continue
            close = _finite(row.get("close"))
            if close is None or close <= 0:
                continue
            volume = _finite(row.get("volume"))
            prev_close = prev_by_symbol.get(sym)
            payload.append({
                "symbol": sym,
                "trade_date": row.get("date") or latest_date,
                "close": close,
                "previous_close": prev_close if (prev_close or 0) > 0 else None,
                "previous_trade_date": prev_date if prev_close else None,
                "volume": int(volume) if volume is not None else None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

        if not payload:
            logger.warning("price_service: nothing to write for %s", latest_date)
            return 0

        try:
            written = await asyncio.to_thread(self._upsert_closes, payload)
        except Exception as e:
            logger.error("price_service: close upsert failed for %s: %s: %s",
                         latest_date, type(e).__name__, e, exc_info=True)
            return 0

        with_prev = sum(1 for r in payload if r["previous_close"] is not None)
        logger.info(
            "price_service: stored %d closes for %s (prev session %s on %d of them; "
            "%d unlicensed symbols skipped)",
            written, latest_date, prev_date, with_prev, skipped_blocked,
        )
        for key in [k for k in _cache if k.startswith("price:close:")]:
            _cache.pop(key, None)
        return written

    async def _fetch_latest_session(
        self, start_date: Optional[str] = None
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Walk back from `start_date` to the first date `batch-eod` actually has rows for.

        Weekday arithmetic alone is not enough: a market holiday is a weekday with no
        session, and today — Labor Day — is exactly that. Rather than carry a holiday
        calendar, ask the data. Bounded so a persistent upstream outage cannot spin.
        """
        target = start_date or self._last_trading_day()
        fmp = get_fmp_client()
        for _ in range(_MAX_SESSION_LOOKBACK):
            try:
                rows = await fmp.get_batch_eod(target)
            except Exception as e:
                logger.error("price_service: batch-eod failed for %s: %s: %s",
                             target, type(e).__name__, e, exc_info=True)
                return target, []
            if rows:
                return target, rows
            logger.info("price_service: no session on %s, stepping back", target)
            target = self._step_back(target)
        return target, []

    @staticmethod
    def _step_back(iso_date: str) -> str:
        """The previous weekday before `iso_date` (holidays are handled by the caller)."""
        d = date.fromisoformat(iso_date) - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.isoformat()

    @staticmethod
    def _upsert_closes(payload: List[Dict[str, Any]]) -> int:
        supabase = get_supabase()
        written = 0
        CHUNK = 1000
        for i in range(0, len(payload), CHUNK):
            chunk = payload[i:i + CHUNK]
            supabase.table("market_close_snapshot").upsert(
                chunk, on_conflict="symbol", returning="minimal",
            ).execute()
            written += len(chunk)
        return written

    @staticmethod
    def _last_trading_day(today: Optional[date] = None) -> str:
        """Most recent weekday on or before yesterday, as YYYY-MM-DD.

        Weekday-only. A market holiday simply yields a date `batch-eod` has no rows for,
        which is logged and leaves the previous snapshot in place — correct, because the
        last real close IS still the previous session's. Callers that need true session
        state use `/stable/exchange-market-hours`, which does account for holidays (it
        reported `isMarketOpen: false` on Labor Day 2026-09-07 while a weekday check
        would have said open).
        """
        d = (today or datetime.now(timezone.utc).date()) - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.isoformat()


_price_service: Optional[PriceService] = None


def get_price_service() -> PriceService:
    global _price_service
    if _price_service is None:
        _price_service = PriceService()
    return _price_service


def price_source(owner: Any = None) -> Any:
    """The price primitive for `owner`, honouring an injected stand-in.

    Services hold their upstream as `self.fmp`, and dozens of tests drive them by
    assigning a fake to it. Prices no longer come from that client, so those tests need a
    seam of their own — this is it: set `svc.price` and every price read on that instance
    goes through it.

    An explicit, documented seam rather than monkeypatching a module global, because the
    fakes in question are built inside helper functions where `monkeypatch` is not in
    scope, and an instance attribute needs no teardown to stay isolated between tests.
    Production never sets `price`, so `owner` falls through to the singleton.
    """
    injected = getattr(owner, "price", None)
    return injected if injected is not None else get_price_service()
