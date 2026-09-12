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
from app.integrations.fmp import FMPRateLimitException, get_fmp_client
from app.config import settings
from app.integrations.fmp_entitlements import is_blocked_symbol
from app.services.asset_class import detect_asset_class, uses_coingecko_price
from app.utils.market_hours import previous_trading_day, session_trading_date

logger = logging.getLogger(__name__)

# ── Tiering ───────────────────────────────────────────────────────────────────────
# The screener sweep is ~1.5 s for ~8k rows, and every symbol on screen shares it, so a
# short TTL turns a burst of tile requests into one upstream call. 60 s matches the
# freshness a user perceives on a price and is what `home_dashboard_service` already
# assumed of batch-quote.
_UNIVERSE_TTL = 60.0

# A single profile is cheap; this only collapses the duplicate calls a single screen makes.
_QUOTE_TTL = 30.0

# Crypto quotes come from CoinGecko, whose Basic plan is 100,000 calls/MONTH — a
# sustained 2.3/minute, not the 300/minute burst ceiling. That budget, not latency, is
# what sets this TTL: one `/coins/markets` call serves a whole batch, so 60s costs at
# most ~43k/month if something rebuilds every minute forever. In-process only, per
# invariant #2 — a live price is never written to Supabase.
_CRYPTO_QUOTE_TTL = 60.0

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

# Two ~12 MB batch-eod calls back to back reliably trip FMP's burst limiter; measured,
# it clears within seconds. This is a once-daily background job, so waiting is free.
_RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_BACKOFF_SECONDS = 10.0

# "Did the US market trade on this date?" cannot be answered by "did batch-eod return
# rows" — that endpoint is global, and on a US-only holiday the international exchanges
# still report. Measured on Labor Day 2026-09-07: `batch-eod?date=2026-09-07` returned
# 40,159 rows and AAPL was ABSENT from every one of them. A row-count check accepted that
# as a session, so the ingest wrote 37,695 international symbols and silently skipped the
# entire US universe.
#
# These three are the probe. They are the most liquid US listings there are: if a real US
# session happened, all three are in the payload. A quorum of two tolerates one symbol
# being halted or renamed without falsely rejecting a genuine session.
#: A healthy prior session covers nearly all of the latest one — both come from the same
#: whole-market `batch-eod` call. Well below this is a truncated response, and writing it
#: would null `previous_close` on every symbol it omits.
_MIN_PREV_SESSION_COVERAGE = 0.5

_US_SESSION_BELLWETHERS = ("AAPL", "MSFT", "SPY")
_US_SESSION_QUORUM = 2

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


async def _empty_quote_map() -> Dict[str, Dict[str, Any]]:
    """An awaitable `{}` — `asyncio.gather` needs a coroutine, not a plain dict.

    Defined at module level rather than inlined as a lambda so it cannot be
    collaterally deleted by a block edit without `test_no_undefined_globals.py`
    catching it (see the `_empty_list` incident).
    """
    return {}


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


def _positive_price(value: Any) -> Optional[float]:
    """A finite price strictly above zero, else None.

    FMP reports `price: 0` for a halted or delisted listing, and `_finite(0)` is a real
    0.0 — so `_from_screener` computed a -100.0% "day change" against the stored close,
    and `_from_profile` shipped `$0.00 +0.00%`, while `market_movers_service` dropped the
    same rows. An unknown price is None (invariant 1); consumers already skip None.
    """
    f = _finite(value)
    return f if f is not None and f > 0 else None


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
        is_etf: bool = False,
        is_fund: bool = False,
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
            # Always present, so a ranking can refuse an open-end fund (one NAV print a
            # day — never an intraday mover) or a leveraged ETF without a second lookup.
            # Both the screener and `/stable/profile` carry the flags.
            "isEtf": bool(is_etf),
            "isFund": bool(is_fund),
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
        price = _positive_price(row.get("price"))
        # A change without a price describes nothing: FMP reports `price: 0, change: 0`
        # for a halted/delisted listing, and shipping `change 0.0` beside `price None`
        # rendered "$0.00 +0.00%" on the profile path. Both stay None together.
        change = _finite(row.get("change")) if price is not None else None
        change_pct = _finite(row.get("changePercentage")) if price is not None else None
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
            change_pct=change_pct,
            volume=_finite(row.get("volume")),
            avg_volume=_finite(row.get("averageVolume")),
            market_cap=_finite(row.get("marketCap")),
            exchange=row.get("exchange"),
            year_low=year_low,
            year_high=year_high,
            is_etf=bool(row.get("isEtf")),
            is_fund=bool(row.get("isFund")),
        )

    @staticmethod
    def _snapshot_is_current(snap: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
        """Is this stored close recent enough to be the day change's denominator?

        ⚠️ The row is written by an HOURLY job that returns 0 on every abort path (rate
        limit after retries, a truncated prior session, the coverage guard, a Supabase
        failure) and leaves the previous row in place — which is correct for a holiday
        and wrong for a real session that was never ingested. Nothing on the read path
        checked, so after two missed sessions every batch day-change (Home tiles, Top
        Movers, the widget's "Down X% today", price alerts) was a multi-session move
        presented as today's. Migration 158 stored `trade_date` precisely "so a stale or
        skipped ingest is visible in the data"; this is the reader that looks.

        The rule: the stored close must be at least the session BEFORE the one the current
        numbers describe. `previous_trading_day` is holiday-aware, so the Tuesday after
        Labor Day still accepts Friday's close; one genuinely missed session does not.
        A row without a `trade_date` (a pre-158 shape) is trusted, as before.
        """
        if not snap:
            return False
        raw = snap.get("trade_date")
        if not raw:
            return True
        try:
            stored = date.fromisoformat(str(raw)[:10])
        except ValueError:
            return True
        return stored >= previous_trading_day(session_trading_date(now))

    @classmethod
    def _change_session(
        cls, prev: Optional[float], snap: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        """WHICH SESSION a change computed against `prev` describes (ISO date), or None.

        `_pick_denominator` already decides it: a price that has moved off the stored
        close belongs to a LATER session (so the change is the current one), while a
        price still equal to the stored close means the change is the one that close
        ENDED — `trade_date`. It matters premarket: at 07:00 ET on a Monday the screener
        still reports Friday's close, so the change is FRIDAY's move, and without the
        stamp the widget printed it as "Down 4.8% today" under a Monday date.
        """
        if prev is None or not snap:
            return None
        close = _finite(snap.get("close"))
        if close is not None and abs(prev - close) < 1e-12:
            return session_trading_date().isoformat()
        return str(snap.get("trade_date") or "")[:10] or None

    _stale_snapshot_dates: set = set()
    # Above this share of a batch, stale rows are a MISSED INGEST, not a few illiquid
    # names whose last print was days ago (CCZ / FEMD-class rows are always a little
    # stale and are the normal case).
    _STALE_BATCH_WARN_SHARE = 0.5

    @classmethod
    def _report_stale_snapshots(cls, stale: Dict[str, str], total: int) -> None:
        """The read-side trace of a stale close map, per BATCH.

        `stale` maps symbol → its stale `trade_date`. A handful of stale rows in a batch
        is an INFO line (illiquid listings whose last close is old); a majority is the
        signature of an ingest that missed a session, and that is a WARNING — once per
        distinct newest stale date, so a missed session cannot page on every 30 s poll.
        """
        if not stale or total <= 0:
            return
        dates = sorted({d for d in stale.values() if d})
        sample = ", ".join(sorted(stale)[:5])
        share = len(stale) / total
        if share < cls._STALE_BATCH_WARN_SHARE:
            logger.info(
                "price: %d of %d symbols carry a stale close snapshot (trade_date %s; e.g. %s) "
                "— their day change reads as unknown",
                len(stale), total, "/".join(dates[-3:]), sample,
            )
            return
        newest = dates[-1] if dates else "?"
        if newest in cls._stale_snapshot_dates:
            return
        cls._stale_snapshot_dates.add(newest)
        logger.warning(
            "price: market_close_snapshot is STALE for %d of %d symbols (newest trade_date=%s; "
            "e.g. %s) — every batch day change reads as unknown until the close-snapshot "
            "ingest catches up",
            len(stale), total, newest, sample,
        )

    @staticmethod
    def _stale_trade_date(snap: Optional[Dict[str, Any]]) -> Optional[str]:
        """The row's `trade_date` when it is too old to be a denominator, else None."""
        if not snap or PriceService._snapshot_is_current(snap):
            return None
        return str(snap.get("trade_date") or "")[:10] or "?"

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
        if not PriceService._snapshot_is_current(snap):
            # A stale row cannot be "yesterday's close". Unknown beats a multi-session
            # move labelled as today's (invariant 1). The batch callers count these and
            # report them (`_report_stale_snapshots`) — a majority of a batch being stale
            # is the read-side trace of a missed ingest, whose only other signal is the
            # job's own ERROR hours earlier.
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
        price = _positive_price(row.get("price"))
        prev = cls._pick_denominator(price, snap)

        change = change_pct = None
        if price is not None and prev is not None and prev > 0:
            change = price - prev
            change_pct = (change / prev) * 100.0
        # else: left as None on purpose. No previous close means the day change is
        # genuinely unknown, and 0.00% would be a fabricated fact (invariant 1).

        out = cls._shape(
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
            is_etf=bool(row.get("isEtf")),
            is_fund=bool(row.get("isFund")),
        )
        if change_pct is not None:
            # Present only when there IS a change to describe, so the fixed key set
            # every consumer reads is unchanged (`yearLow`/`yearHigh` follow the same rule).
            session = cls._change_session(prev, snap)
            if session:
                out["changeSession"] = session
        return out

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
        if self._crypto_quotes_enabled() and uses_coingecko_price(sym):
            # Crypto is blocked on FMP but licensed on CoinGecko. Route it rather than
            # returning {} — this is the path a single-symbol caller (a Tracking row, a
            # price-alert baseline at creation time) takes, and {} is what made a BTC
            # alert get created with a NULL baseline that could never trigger.
            rows = await self._crypto_quotes([sym])
            return rows.get(sym, {})
        if is_blocked_symbol(sym):
            # Index / commodity / FX. Empty so the caller hides the surface; raising
            # here would turn "not covered" into an error page.
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
        # SPLIT, don't just drop. `is_blocked_symbol` is about the FMP LICENCE, and it
        # is true for indices, commodities, FX *and* crypto alike — but crypto is the
        # one class with a licensed alternative source. Dropping it here is what made
        # crypto price alerts never fire (silently: no symbol, no observation, no log),
        # Tracking rows render $0.00, and the iOS widget blank a coin.
        #
        # This is the single choke point for all 18 `get_quotes_list` call sites, so
        # fixing it here fixes every surface at once and consistently, rather than
        # 18 partial migrations that drift apart.
        crypto = {
            s for s in wanted
            if self._crypto_quotes_enabled() and uses_coingecko_price(s)
        }
        wanted = {s for s in wanted if not is_blocked_symbol(s)} - crypto
        if not wanted and not crypto:
            return {}

        universe, closes, crypto_rows = await asyncio.gather(
            self._get_universe(),
            self.get_close_snapshots(wanted),
            self._crypto_quotes(crypto) if crypto else _empty_quote_map(),
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
        if isinstance(crypto_rows, Exception):
            # Degrade the crypto half only — an equity batch must not fail because
            # CoinGecko is down.
            logger.warning("price_service: crypto quotes unavailable: %s: %s",
                           type(crypto_rows).__name__, crypto_rows)
            crypto_rows = {}

        out: Dict[str, Dict[str, Any]] = dict(crypto_rows or {})
        stale: Dict[str, str] = {}
        seen = 0
        for sym in wanted:
            row = universe.get(sym)
            if row is None:
                continue
            seen += 1
            snap = closes.get(sym)
            stale_date = self._stale_trade_date(snap)
            if stale_date:
                stale[sym] = stale_date
            quote = self._from_screener(row, snap)
            if quote is not None and quote.get("price") is not None:
                out[sym] = quote
        self._report_stale_snapshots(stale, seen)

        missing = wanted - set(out)   # `wanted` already excludes crypto
        if missing and not universe:
            # THE UNIVERSE ITSELF FAILED — every symbol is "missing", so the per-symbol
            # fallback below would issue one `/stable/profile` call PER REQUESTED SYMBOL,
            # per caller, for as long as the outage lasts. A cold Home is ~30 symbols and
            # the Tracking feed polls every 30 s, so a single screener 429 turns into
            # hundreds of profile calls a minute against the same rate-limited upstream —
            # the amplification that makes an outage self-sustaining. `get_universe`
            # already memoises its degraded state for 15 s so the next call retries; the
            # honest answer here is to serve what we have (nothing) and let the caller
            # degrade, exactly as it does for a symbol the screener genuinely omits.
            logger.warning(
                "price: universe unavailable and %d symbol(s) requested — skipping the "
                "per-symbol profile fallback rather than fanning out",
                len(missing),
            )
            missing = set()
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

    # ── Crypto quotes (CoinGecko) ─────────────────────────────────────────

    @staticmethod
    def _crypto_quotes_enabled() -> bool:
        """False restores the pre-Phase-5 behaviour exactly: crypto is simply dropped.

        The FMP crypto paths are HIDDEN, not removed — set `CRYPTO_PRICE_SOURCE=fmp`
        and every call site here reverts to what it did before, so buying FMP's crypto
        package back is one environment variable plus its entitlement manifest entry.
        """
        return str(getattr(settings, "CRYPTO_PRICE_SOURCE", "coingecko") or "").lower() != "fmp"

    async def _crypto_quotes(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """Quote-shaped rows for crypto pairs, keyed by the symbol the caller asked for.

        ONE `/coins/markets` request covers the whole batch — price, 24h change, volume,
        market cap and the 52-week band — so a six-coin watchlist costs one credit, not
        six. That matters: the monthly budget is the binding constraint, not the rate
        limit.

        Three things here are load-bearing:

        * **Rows are keyed by COIN ID, never by `row["symbol"]`.** MATIC and POL both
          resolve to `polygon-ecosystem-token` and CoinGecko answers with one canonical
          symbol, so keying by symbol drops the other. `/coins/markets` also sorts by
          market cap rather than request order, so positional zipping is wrong too.
        * **Every row goes out through `_shape`**, so both the `changePercentage` and
          `changesPercentage` spellings exist. Consumers are split roughly evenly
          between them, and a hand-rolled dict silently never fires a price alert.
        * **A missing price OMITS the row** rather than emitting 0.0 (invariant #1).
          Callers already treat an absent symbol as "no data" and hide the surface.
        """
        wanted = [(s or "").strip().upper() for s in symbols if s and str(s).strip()]
        if not wanted:
            return {}

        key = "price:crypto:" + ",".join(sorted(set(wanted)))
        hit = _cache_get(key, _CRYPTO_QUOTE_TTL)
        if hit is not None:
            return hit
        if key in _inflight:
            # Shielded: a caller that times out must not cancel the shared fetch and
            # leave every other awaiter with a CancelledError.
            return await asyncio.shield(_inflight[key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[key] = future
        try:
            from app.integrations.coingecko import get_coingecko_client
            from app.services.coingecko_adapter import (
                crypto_base_symbol,
                markets_rows_by_id,
            )

            client = get_coingecko_client()
            # Resolve first so we can map the response back by id. Tier 1 is a free
            # dict lookup for the ~110 symbols we ship, so this is normally free.
            id_by_symbol: Dict[str, str] = {}
            for sym in dict.fromkeys(wanted):
                coin_id = await client.resolve_coin_id(crypto_base_symbol(sym))
                if coin_id:
                    id_by_symbol[sym] = coin_id
            if not id_by_symbol:
                _cache_set(key, {})
                if not future.done():
                    future.set_result({})
                return {}

            rows = await client.get_markets(
                [crypto_base_symbol(s) for s in id_by_symbol]
            )
            by_id = markets_rows_by_id(rows)

            out: Dict[str, Dict[str, Any]] = {}
            for sym, coin_id in id_by_symbol.items():
                row = by_id.get(coin_id)
                if not isinstance(row, dict):
                    continue
                price = _finite(row.get("current_price"))
                if price is None:
                    # Unknown price → omit. Never a fabricated $0.00.
                    continue
                change = _finite(row.get("price_change_24h"))
                change_pct = _finite(row.get("price_change_percentage_24h"))
                # previousClose is DERIVED, never defaulted: an absent 24h change means
                # the reference is unknown, and `price - 0` would put the dashed
                # reference line exactly on the last tick — plausible and wrong.
                previous_close = (price - change) if change is not None else None
                out[sym] = self._shape(
                    symbol=sym,
                    name=row.get("name"),
                    price=price,
                    previous_close=previous_close,
                    change=change,
                    change_pct=change_pct,
                    volume=_finite(row.get("total_volume")),
                    avg_volume=None,
                    market_cap=_finite(row.get("market_cap")),
                    exchange="CRYPTO",
                    # 52-week band deliberately OMITTED. `/coins/markets` carries `atl`
                    # and `ath`, which are ALL-TIME, not 52-week — labelling Bitcoin's
                    # 2013 low as a "52-Week Low" is precisely the $67.81 bug this
                    # rebuild removed. `_shape` omits the keys entirely when None, which
                    # keeps each caller's `.get("yearLow", 0)` default reachable. The
                    # real band comes from `crypto_service._cg_52_week_band` (/ohlc).
                )

            _cache_set(key, out)
            if not future.done():
                future.set_result(out)
            return out
        except asyncio.CancelledError:
            # CancelledError is a BaseException, so it SKIPS `except Exception` below.
            # A joiner is parked on `asyncio.shield(_inflight[key])`, and `finally` has
            # already popped the key — so if the leader dies without resolving the
            # future, that joiner waits forever with nothing able to recover it. Resolve
            # it with an exception (never a result: a cancelled fetch produced no data,
            # and handing back `{}` would look like "this coin has no price").
            if not future.done():
                future.set_exception(RuntimeError("shared crypto fetch was cancelled"))
            raise
        except Exception as e:
            # Never let a CoinGecko failure break the equity path that shares the batch.
            # Deliberately a RESULT, not an exception: the caller's contract is "a symbol
            # I could not price is absent", and `get_quotes` gathers this leg with
            # `return_exceptions=True` alongside the equity legs.
            logger.warning(
                "price_service: crypto quotes unavailable for %d symbol(s) (%s: %s)",
                len(wanted), type(e).__name__, e,
            )
            if not future.done():
                future.set_result({})
            return {}
        finally:
            _inflight.pop(key, None)

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
        except asyncio.CancelledError:
            # CancelledError is a BaseException, so it skips `except Exception`
            # below. Without this arm the future is never settled and every joiner
            # parked on `asyncio.shield(...)` waits for the life of the process —
            # while `finally` has already popped the key, so nothing can recover it.
            if not future.done():
                future.set_exception(RuntimeError("shared fetch was cancelled"))
            raise
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
                # Open-end mutual funds are not a market universe: one NAV print a day,
                # `volume: 0`, an AUM masquerading as `marketCap`. Without this filter the
                # sweep carried 3,719 of them (GOLDX headed the widget's prior-session
                # drop every cycle) across TWO pages; with it the whole >$50M universe is
                # one 7,116-row page — one screener call per refresh instead of two.
                is_fund=False,
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
                    "previous_close": _finite(row.get("previous_close")),
                    "trade_date": str(row.get("trade_date") or "")[:10] or None}
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
                .select("symbol,close,previous_close,trade_date")
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
        # ⚠️ COVERAGE, not just emptiness. The guard below used to fire only when the prior
        # session was FULLY empty; a truncated one (say 500 of 65,000 rows) passed it, and
        # every symbol missing from it was then written with `previous_close = NULL` — the
        # same destructive partial write, just quieter. `batch-eod` is one call for the
        # whole market, so a healthy prior session covers most of the latest one; anything
        # far below that is a truncated response, not a real session.
        #
        # Measured over the ENTITLED UNIVERSE (NASDAQ/NYSE/AMEX, the screener's rows) when
        # it is known, not over the global row count. `batch-eod` is worldwide, and the
        # prior session can be a day most non-US exchanges were shut while the US traded
        # (1 May, Whit Monday, Boxing Day observed…): a prior payload that is US-only
        # would then be a fraction of a full global latest and trip the floor — aborting
        # a perfectly good US ingest for the whole day. The global ratio is the fallback
        # when the universe is unavailable, so a truncated prior session is still caught.
        universe = await self._universe_symbols_for_coverage()
        if universe:
            latest_us = [r for r in latest if (r.get("symbol") or "").upper() in universe]
            prev_us = [sym for sym in prev_by_symbol if sym in universe]
            coverage = (len(prev_us) / len(latest_us)) if latest_us else 0.0
        else:
            coverage = (len(prev_by_symbol) / len(latest)) if latest else 0.0
        if prev_by_symbol and coverage < _MIN_PREV_SESSION_COVERAGE:
            logger.error(
                "price_service: prior session %s covers only %.1f%% of the %d symbols in "
                "%s (%d rows) — SKIPPING the write rather than nulling previous_close on "
                "the rest",
                prev_date, coverage * 100, len(latest), latest_date, len(prev_by_symbol),
            )
            return 0

        if not prev_by_symbol:
            # 🔴 ABORT rather than write. This upsert replaces the whole row, so writing
            # here would set `previous_close = NULL` on every symbol and DESTROY a good
            # denominator that is still perfectly valid.
            #
            # Not hypothetical: observed in production 2026-09-07. The hourly loop ran a
            # build without the rate-limit backoff, the second (prior-session) batch-eod
            # call was rejected by FMP's burst limiter, and the job cheerfully nulled
            # `previous_close` across all 63,394 rows — turning a working day-change into
            # "unknown" app-wide, hours after a successful ingest had populated it.
            #
            # Skipping is always safe: the stored pair (close, previous_close) stays
            # internally consistent, and yesterday's close is still yesterday's close.
            # The loop retries within the hour.
            logger.error(
                "price_service: prior session before %s unavailable — SKIPPING the write "
                "to avoid nulling previous_close on %d existing rows",
                latest_date, len(latest),
            )
            return 0

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
                # Same condition as `previous_close` above. They used to differ — `> 0` vs
                # truthiness — so a NEGATIVE prior close wrote `previous_close = NULL`
                # beside a non-null `previous_trade_date`: a row claiming we have
                # yesterday's session while holding no usable denominator from it.
                "previous_trade_date": prev_date if (prev_close or 0) > 0 else None,
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
        # ⚠️ ALSO drop the movers close map. It lives in `market_movers_service`'s OWN
        # module-level `_cache` under `movers:closes`, so clearing only this module's keys
        # left it holding the pre-ingest snapshot. Its TTL (3600 s) equals the ingest loop's
        # period, so whether the post-ingest warm in `main.py` saw the new rows came down to
        # a few hundred milliseconds of drift — every scanner and every sector strip reads
        # that map.
        try:
            from app.services.market_movers_service import (  # noqa: PLC0415
                _cache as _movers_cache,
            )

            _movers_cache.pop("movers:closes", None)
        except Exception as e:
            logger.warning(
                "price_service: could not invalidate the movers close map (%s: %s) — it "
                "will serve the previous snapshot until its own TTL expires",
                type(e).__name__, e,
            )

        for key in [k for k in _cache if k.startswith("price:close:")]:
            _cache.pop(key, None)
        return written

    async def _universe_symbols_for_coverage(self) -> Optional[set]:
        """The screener universe's symbols for the coverage ratio, or None if unavailable.

        Best effort: the ingest must never fail because the screener did. One cached
        call (60 s TTL) inside an hourly job.
        """
        try:
            rows = await self._get_universe()
        except Exception as e:                      # noqa: BLE001 — degrade to the global ratio
            logger.warning("price_service: universe unavailable for the coverage ratio "
                           "(%s: %s) — falling back to the global row count",
                           type(e).__name__, e)
            return None
        syms = {str(k).upper() for k in (rows or {}).keys()}
        return syms or None

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
            rows = await self._batch_eod_with_backoff(fmp, target)
            if rows is None:
                return target, []          # hard failure, already logged
            if self._is_us_session(rows):
                return target, rows
            logger.info(
                "price_service: %s is not a US session (%d rows, bellwethers missing) "
                "— stepping back", target, len(rows or []),
            )
            target = self._step_back(target)
        return target, []

    @staticmethod
    async def _batch_eod_with_backoff(fmp: Any, target: str) -> Optional[List[Dict[str, Any]]]:
        """`batch-eod` for one date, retrying a rate limit. None means give up.

        ⚠️ The retry is not optional. A full ingest makes TWO of these calls back to back —
        the latest session and the one before it — and each pulls ~12 MB / ~65k rows. FMP's
        burst limiter rejects the second almost every time, and the observed failure was
        silent in the worst way: the first call succeeded, so 62,893 closes were written
        correctly while EVERY `previous_close` came back NULL, leaving the day change
        unknown across the whole app. Measured: the limit clears in seconds, so a short
        backoff turns a guaranteed daily failure into a non-event.
        """
        delay = _RATE_LIMIT_BACKOFF_SECONDS
        for attempt in range(_RATE_LIMIT_RETRIES + 1):
            try:
                return await fmp.get_batch_eod(target)
            except FMPRateLimitException as e:
                if attempt == _RATE_LIMIT_RETRIES:
                    logger.error(
                        "price_service: batch-eod for %s still rate-limited after %d "
                        "retries: %s", target, _RATE_LIMIT_RETRIES, e,
                    )
                    return None
                logger.warning(
                    "price_service: batch-eod for %s rate-limited, retrying in %.0fs "
                    "(attempt %d/%d)", target, delay, attempt + 1, _RATE_LIMIT_RETRIES,
                )
                await asyncio.sleep(delay)
                delay *= 2
            except Exception as e:
                logger.error("price_service: batch-eod failed for %s: %s: %s",
                             target, type(e).__name__, e, exc_info=True)
                return None
        return None

    @staticmethod
    def _is_us_session(rows: Optional[List[Dict[str, Any]]]) -> bool:
        """Did the US market actually trade on the date these rows came from?

        NOT "are there any rows". `batch-eod` is global, so a US-only holiday still
        returns tens of thousands of international rows — see `_US_SESSION_BELLWETHERS`.
        """
        if not rows:
            return False
        symbols = {(r.get("symbol") or "").upper() for r in rows}
        return sum(1 for b in _US_SESSION_BELLWETHERS if b in symbols) >= _US_SESSION_QUORUM

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
