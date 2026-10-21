"""
One settled, full daily price history per symbol — the series every asset-detail chart
pages back through (`GET /{asset}/{symbol}/chart/history`), and the one the stock
weekly / monthly / ALL charts are bucketed from.

Why it exists. A chart range used to be the ONLY bars a screen could ever hold: a 1Y
weekly INTC chart loaded from the week of 2021-09-27, nothing older could be asked for,
and every indicator drew from bar N-1 of that window — so panning left ran into blank
MA/RSI/MACD lines at a hard edge that was not the start of anything. Two more defects
came from the same single windowed fetch: a weekly/monthly bucket built from a mid-week
start was incomplete, and 5Y monthly (~22 years) was cut at FMP's 5,000-row cap.

Sources, by asset class (each mirrors the symbol mapping of its own detail service):
  * stock     — seeded from the overview bundle's `stock_historical` when that is in the
                overview service's MEMORY cache (it is the newest ≤ 5,000 settled rows,
                fetched from 1900); only a seed of a full page pages the OLDER remainder.
                Otherwise FMP is paged newest-first (`chart_helper._iter_daily_pages`);
  * etf       — `ETFService._get_history_strict` (the ETF screen's own 12 h history);
  * index     — `IndexService._get_history_strict` (the proxy ETF, `_proxy_for`);
  * commodity — `CommodityService._get_history_strict` (ETF ref, or FRED for CL/NG);
  * crypto    — `CryptoService._cg_history(base, 730)` (CoinGecko Basic's cap).

Caching is Tier-1 ONLY, deliberately, following `etf_service._get_history` /
`index_service._get_history` and the `index_cache` note in `schema_curation.py`: reading
a multi-MB raw history back out of Supabase is slower than the FMP call it would
replace, and the overview bundle already persists the newest 5,000 stock rows. Entries
are columns (`array('d')` + ordinal dates, ~48 bytes a row against ~460 for FMP's dicts)
in a byte-bounded LRU, close-cycle aligned, behind ONE shared-build map (`_inflight`) whose
builds run as their own tasks: a caller that goes away never cancels a build other callers
share, and `ensure_covers` can return on the newest pages while older ones keep coming.

The contract a history page keeps (plan "Correctness contract"):
  * bars strictly older than `before`, oldest first, contiguous — a page is a slice of one
    series, never a re-fetch with its own seams;
  * `has_more` is false ONLY when the series is non-empty and the page reached its first
    row. An empty or failed source RAISES — a fetch failure must never read as "start of
    history" (`EmptyPriceHistory`, `chart_helper._daily_page_rows`);
  * `reached_start` is true ONLY when that first row is the symbol's first bar — not a
    plan cap, a look-back bound or the end of the provider's intraday depth;
  * open/high/low ≤ 0 is None, never fabricated from the close.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from array import array
from bisect import bisect_left
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Hashable, List, Optional, Set, Tuple

from app.services.ticker_report_cache import current_close_cycle_start
from app.utils.inflight import fail_shared_future
from app.utils.market_hours import ET, is_market_active

logger = logging.getLogger(__name__)


# ── Vocabulary ──────────────────────────────────────────────────────────────

ASSET_STOCK = "stock"
ASSET_ETF = "etf"
ASSET_INDEX = "index"
ASSET_COMMODITY = "commodity"
ASSET_CRYPTO = "crypto"
ASSETS = frozenset({ASSET_STOCK, ASSET_ETF, ASSET_INDEX, ASSET_COMMODITY, ASSET_CRYPTO})

DAILY = "daily"
WEEKLY = "weekly"
MONTHLY = "monthly"
BUCKET_INTERVALS = frozenset({WEEKLY, MONTHLY})
#: The intraday sizes a chart can page: 1W offers 5/15/30 min and 1 hour; 1D does not pan.
PAGE_INTRADAY_INTERVALS = ("5min", "15min", "30min", "1hour")
#: The `interval` each history route accepts — anything else is a 422 at the route.
FMP_PAGE_INTERVALS = (DAILY, WEEKLY, MONTHLY) + PAGE_INTRADAY_INTERVALS
CRYPTO_PAGE_INTERVALS = (DAILY, WEEKLY, MONTHLY, "1hour")
#: CoinGecko's 2-90-day series is HOURLY whatever the screen asked for, so a crypto pair
#: reaching the stocks route at a finer size is served (and labelled) 1 hour.
_CRYPTO_INTRADAY = "1hour"

#: `limit` bounds, shared by the routes (`Query(ge=1, le=MAX_PAGE_LIMIT)`).
MAX_PAGE_LIMIT = 5000
#: Defaults: ~6 years of daily bars; weekly/monthly the whole series (5,000 weekly bars is
#: 96 years); intraday about two weeks of 15-minute regular-session bars.
DEFAULT_PAGE_LIMITS = {DAILY: 1500, WEEKLY: MAX_PAGE_LIMIT, MONTHLY: MAX_PAGE_LIMIT}
DEFAULT_INTRADAY_PAGE_LIMIT = 1000

# ── Daily history ───────────────────────────────────────────────────────────

#: The overview bundle asks FMP for history from this date, so a seed from it and every
#: page this service fetches cover the same span (pre-1970 listings included).
_HISTORY_FROM = "1900-01-01"
#: A seed this long may be a FULL 5,000-row page with older rows behind it. Not
#: `== 5000`: the settled filter drops today's in-progress bar, so a capped page reaches
#: the bundle as 4,999 rows and an exact test would call a truncated history complete.
_SEED_COMPLETE_BELOW = 5000 - 10
#: 30,000 rows is ~119 years; `_iter_daily_pages` logs if a history outlasts it.
_MAX_DAILY_PAGES = 6
#: The ceiling on an FMP history entry; it is also a MISS once the close cycle turns.
_HISTORY_TTL = 43_200
#: Crypto has no close: the CoinGecko daily series is re-read on the detail's own 1 h.
_CRYPTO_HISTORY_TTL = 3600

# ── Intraday pages ──────────────────────────────────────────────────────────

#: Calendar days per intraday window. Windows sit on a FIXED grid (`_GRID_EPOCH`), so two
#: users panning the same symbol share one FMP call per window instead of minting a
#: window per cursor; sized so a window stays under ~650 extended-hours bars.
_INTRADAY_WINDOW_DAYS = {"5min": 7, "15min": 14, "30min": 28, "1hour": 56}
_GRID_EPOCH = date(2000, 1, 3)  # a Monday
#: How far back an intraday page may reach. ⚠️ Provisional: FMP's intraday depth per
#: interval is unprobed (plan Phase 0), so these bound the cost of a long fling; tune them
#: from the probe. Past the bound a page answers `has_more: false`.
_INTRADAY_MAX_LOOKBACK_DAYS = {"5min": 365, "15min": 730, "30min": 1095, "1hour": 1825}
#: Consecutive windows with NO rows (non-error) that end the intraday series — a whole
#: week without one bar is past the data, not a quiet week.
_INTRADAY_EMPTY_WINDOWS_TO_END = 2
#: FMP calls one page may spend once it holds at least one bar (it keeps going while it
#: holds none, so a cursor always advances).
_MAX_WINDOWS_PER_PAGE = 3
#: A window that reaches today is still filling; a closed one changes only at a close
#: (split adjustment), so it is cut with the cycle.
_OPEN_WINDOW_TTL = 60
_CLOSED_WINDOW_TTL = 43_200

#: Crypto hourly pages come from ONE days=90 series per coin (CoinGecko's hourly ceiling),
#: cached far longer than the 120 s live chart: past hours never change.
_CRYPTO_HOURLY_DAYS = 90
_CRYPTO_HOURLY_TTL = 900
#: CoinGecko stamps hourly points at whatever second it sampled, and the 1W range series
#: and the 90-day series sample different instants. A page keeps only points at least
#: this far before `before`, so the seam keeps hourly spacing instead of a near-duplicate.
_CRYPTO_SEAM_GAP = timedelta(minutes=30)

# ── `fetch_chart_data` result cache ─────────────────────────────────────────

_INTRADAY_RESULT_TTL = 30
_DAILY_RESULT_TTL_OPEN = 60
_DAILY_RESULT_TTL_CLOSED = 600

# ── Byte budgets (process-local, one uvicorn worker) ────────────────────────

_HISTORY_MAX_BYTES = 48 * 1024 * 1024
_AGGREGATE_MAX_BYTES = 8 * 1024 * 1024
_WINDOW_MAX_BYTES = 16 * 1024 * 1024
_RESULT_MAX_BYTES = 16 * 1024 * 1024
#: Rough heap cost of one row dict (measured ~460 B for FMP's 10-field rows; ours have 6).
_ROW_DICT_BYTES = 400


class EmptyPriceHistory(Exception):
    """The source answered, with no usable rows. Never cached, and never "start of
    history": the history route turns it into a typed upstream error, a chart into an
    empty series."""


class ChartHistoryInputError(ValueError):
    """A malformed or out-of-range history request (the route answers 422)."""


def _settled_cutoff_date(now: Optional[datetime] = None) -> str:
    """ISO date of the most recent SETTLED session — the last row a daily history may hold.

    The same cut every daily-history consumer makes (`chart_helper.settled_bars`), on this
    module's own `current_close_cycle_start` binding so a test can freeze it alone.
    """
    return current_close_cycle_start(now).astimezone(ET).date().isoformat()


def _nan_to_none(v: float) -> Optional[float]:
    return None if v != v else v


def _round2(v: float) -> float:
    return round(v, 2)


class PriceColumns:
    """An immutable, oldest-first daily (or bucketed) series held as columns.

    ``dates`` are proleptic ordinals (one per row, strictly increasing); missing
    open/high/low/volume are NaN, never 0 and never the close. ``complete`` is False only
    for an `ensure_covers` partial (newest pages, more still coming); ``capped_start``
    marks a series whose first row is a provider cap (CoinGecko's 730 days), not a
    listing start.
    """

    __slots__ = (
        "dates", "opens", "highs", "lows", "closes", "volumes",
        "settled_through", "complete", "capped_start",
    )

    def __init__(
        self,
        dates: array,
        opens: array,
        highs: array,
        lows: array,
        closes: array,
        volumes: array,
        *,
        settled_through: Optional[str],
        complete: bool,
        capped_start: bool = False,
    ):
        self.dates = dates
        self.opens = opens
        self.highs = highs
        self.lows = lows
        self.closes = closes
        self.volumes = volumes
        self.settled_through = settled_through
        self.complete = complete
        self.capped_start = capped_start

    @classmethod
    def from_rows(
        cls,
        rows: List[Dict[str, Any]],
        *,
        settled_through: Optional[str],
        complete: bool,
        capped_start: bool = False,
    ) -> "PriceColumns":
        """Build from FMP-shaped rows. Rows without a parseable date or a finite close > 0
        are dropped; on a repeated date the LATER row wins (callers pass older pages
        first, so the newer page is authoritative)."""
        from app.services.chart_helper import (
            _finite_or_none,
            _positive_or_none,
            _volume_or_none,
        )

        by_day: Dict[int, Tuple[float, float, float, float, float]] = {}
        nan = math.nan
        for r in rows:
            if not isinstance(r, dict):
                continue
            raw = r.get("date")
            if not isinstance(raw, str) or len(raw) < 10:
                continue
            try:
                day = date.fromisoformat(raw[:10]).toordinal()
            except ValueError:
                continue
            close = _finite_or_none(r.get("close") or r.get("adjClose"))
            if close is None or close <= 0:
                continue
            o = _positive_or_none(r.get("open"))
            h = _positive_or_none(r.get("high"))
            lo = _positive_or_none(r.get("low"))
            v = _volume_or_none(r.get("volume"))
            by_day[day] = (
                nan if o is None else o,
                nan if h is None else h,
                nan if lo is None else lo,
                close,
                nan if v is None else v,
            )
        days = sorted(by_day)
        vals = [by_day[d] for d in days]
        return cls(
            array("l", days),
            array("d", (v[0] for v in vals)),
            array("d", (v[1] for v in vals)),
            array("d", (v[2] for v in vals)),
            array("d", (v[3] for v in vals)),
            array("d", (v[4] for v in vals)),
            settled_through=settled_through,
            complete=complete,
            capped_start=capped_start,
        )

    def __len__(self) -> int:
        return len(self.dates)

    @property
    def nbytes(self) -> int:
        cols = (self.dates, self.opens, self.highs, self.lows, self.closes, self.volumes)
        return sum(c.itemsize * len(c) for c in cols) + 512

    @property
    def first_ordinal(self) -> Optional[int]:
        return self.dates[0] if len(self.dates) else None

    def index_before(self, day: date) -> int:
        """Index of the first row ON or after ``day`` — rows ``[:i]`` are strictly older."""
        return bisect_left(self.dates, day.toordinal())

    def rows(
        self,
        lo: int = 0,
        hi: Optional[int] = None,
        *,
        round_close: Optional[Callable[[float], float]] = None,
    ) -> List[Dict[str, Any]]:
        """Rows ``[lo, hi)`` in the app's chart-row shape (NaN → None)."""
        hi = len(self.dates) if hi is None else hi
        out: List[Dict[str, Any]] = []
        fromordinal = date.fromordinal
        for i in range(max(0, lo), max(0, hi)):
            close = self.closes[i]
            out.append({
                "date": fromordinal(self.dates[i]).isoformat(),
                "open": _nan_to_none(self.opens[i]),
                "high": _nan_to_none(self.highs[i]),
                "low": _nan_to_none(self.lows[i]),
                "close": round_close(close) if round_close else close,
                "volume": _nan_to_none(self.volumes[i]),
            })
        return out


class _ByteLRU:
    """A least-recently-used map bounded by an estimated byte size, not an entry count:
    one INTC history is 12k rows and one young ETF's is 2k, so a count bound either wastes
    memory or evicts the hot symbols."""

    def __init__(self, max_bytes: int):
        self._max = max_bytes
        self._items: "OrderedDict[Hashable, Tuple[Any, int]]" = OrderedDict()
        self._bytes = 0

    def get(self, key: Hashable) -> Any:
        item = self._items.get(key)
        if item is None:
            return None
        self._items.move_to_end(key)
        return item[0]

    def put(self, key: Hashable, value: Any, nbytes: int) -> None:
        old = self._items.pop(key, None)
        if old is not None:
            self._bytes -= old[1]
        if nbytes > self._max:
            logger.warning(
                "price history cache: an entry of ~%d bytes exceeds the whole %d-byte "
                "budget — served, not cached (%r)", nbytes, self._max, key,
            )
            return
        self._items[key] = (value, nbytes)
        self._bytes += nbytes
        while self._bytes > self._max and self._items:
            _k, (_v, b) = self._items.popitem(last=False)
            self._bytes -= b

    def pop(self, key: Hashable) -> None:
        old = self._items.pop(key, None)
        if old is not None:
            self._bytes -= old[1]

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0

    def __len__(self) -> int:
        return len(self._items)

    @property
    def total_bytes(self) -> int:
        return self._bytes


class _Entry:
    """A cached value plus what decides its freshness. ``client`` keeps the upstream
    client it was fetched with alive, so its ``id`` (part of the key) cannot be reused by
    a different client while the entry exists."""

    __slots__ = ("value", "built_at", "cycle", "ttl", "client")

    def __init__(self, value: Any, *, cycle: Optional[str], ttl: float, client: Any = None):
        self.value = value
        self.built_at = time.time()
        self.cycle = cycle
        self.ttl = ttl
        self.client = client

    def fresh(self, cutoff: Optional[str]) -> bool:
        if time.time() - self.built_at > self.ttl:
            return False
        return self.cycle is None or self.cycle == cutoff


class _Progress:
    """The newest pages of a stock history build so far, for `ensure_covers`.

    ``changed`` is replaced on every publish and the old event SET, so a waiter that took
    the event before checking the state can never miss a publish.
    """

    __slots__ = ("partial", "changed")

    def __init__(self) -> None:
        self.partial: Optional[PriceColumns] = None
        self.changed = asyncio.Event()

    def publish(self, partial: Optional[PriceColumns] = None) -> None:
        if partial is not None:
            self.partial = partial
        old, self.changed = self.changed, asyncio.Event()
        old.set()


def _client_token(client: Any) -> int:
    return id(client) if client is not None else 0


def _copy_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Callers own what they get back: a cached list (and its row dicts) is never
    handed out by reference."""
    return [dict(r) for r in rows]


def _crypto_round(v: float) -> float:
    from app.services.crypto_service import _round_close

    return _round_close(v)


def _close_rounding(asset: str, interval: str) -> Optional[Callable[[float], float]]:
    """How the asset class's OWN range response at this interval rounds a close, so a page
    and the range bars it is prepended to carry the same precision:

      * stock — unrounded everywhere (`chart_helper`);
      * ETF / commodity — 2 dp on DAILY bars (`_extract_chart_data`); their bucketed and
        intraday bars are served unrounded;
      * index — 2 dp everywhere (`IndexService._get_chart`'s `_points`);
      * crypto — magnitude-aware everywhere (`crypto_service._round_close`).
    """
    if asset == ASSET_CRYPTO:
        return _crypto_round
    if asset == ASSET_INDEX:
        return _round2
    if asset in (ASSET_ETF, ASSET_COMMODITY) and interval == DAILY:
        return _round2
    return None

_INTRADAY_BEFORE_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_DAILY_BEFORE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_before(interval: str, before: str, *, now: Optional[datetime] = None) -> date:
    """Validate an EXCLUSIVE cursor and return its calendar day.

    Daily and coarser take an ISO date; intraday the full ``YYYY-MM-DD HH:MM:SS`` ET
    wall-clock stamp of the oldest bar the client holds (the format every intraday bar
    carries, so the comparison is a fixed-width string compare). A cursor more than a day
    in the future (a day of slack for a client clock or the ET/UTC boundary) is refused.
    """
    if not isinstance(before, str):
        raise ChartHistoryInputError("`before` is required")
    if interval in PAGE_INTRADAY_INTERVALS:
        if not _INTRADAY_BEFORE_RE.fullmatch(before):
            raise ChartHistoryInputError(
                f"`before` for {interval} must be 'YYYY-MM-DD HH:MM:SS', got {before[:32]!r}"
            )
        try:
            day = datetime.strptime(before, "%Y-%m-%d %H:%M:%S").date()
        except ValueError as e:
            raise ChartHistoryInputError(f"`before` is not a real time: {e}") from None
    else:
        if not _DAILY_BEFORE_RE.fullmatch(before):
            raise ChartHistoryInputError(
                f"`before` for {interval} must be 'YYYY-MM-DD', got {before[:32]!r}"
            )
        try:
            day = date.fromisoformat(before)
        except ValueError as e:
            raise ChartHistoryInputError(f"`before` is not a real date: {e}") from None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if day > now.astimezone(ET).date() + timedelta(days=1):
        raise ChartHistoryInputError(f"`before` {before[:32]!r} is in the future")
    return day


def _window_start(day: date, interval: str) -> date:
    span = _INTRADAY_WINDOW_DAYS[interval]
    offset = (day - _GRID_EPOCH).days
    return _GRID_EPOCH + timedelta(days=(offset // span) * span)


class PriceHistoryService:
    """See the module docstring. One instance per process (`get_price_history_service`)."""

    def __init__(self) -> None:
        self._histories = _ByteLRU(_HISTORY_MAX_BYTES)
        self._aggregates = _ByteLRU(_AGGREGATE_MAX_BYTES)
        self._windows = _ByteLRU(_WINDOW_MAX_BYTES)
        self._crypto_hourly = _ByteLRU(_WINDOW_MAX_BYTES)
        self._results = _ByteLRU(_RESULT_MAX_BYTES)
        # Shared builds: key → the future every caller awaits (shielded).
        self._inflight: Dict[Hashable, asyncio.Future] = {}
        # Stock history builds publish their newest pages here (`ensure_covers`).
        self._progress: Dict[Hashable, _Progress] = {}
        # Strong refs: an unreferenced task can be collected mid-flight.
        self._tasks: Set[asyncio.Task] = set()

    def clear(self) -> None:
        """Drop every cached entry (tests, and an operator's escape hatch). In-flight
        builds finish and are simply not joined by later callers."""
        for lru in (self._histories, self._aggregates, self._windows,
                    self._crypto_hourly, self._results):
            lru.clear()
        self._inflight.clear()
        self._progress.clear()

    # ── shared builds ─────────────────────────────────────────────────────

    def _join_or_start(
        self,
        key: Hashable,
        factory: Callable[[], Awaitable[Any]],
        progress: Optional[_Progress] = None,
    ) -> "asyncio.Future":
        """The shared future for ``key``, starting its build (as a task) if none runs.

        Every caller — the first one too — awaits it through `asyncio.shield`, so a caller
        that times out or disconnects never cancels a build other callers are waiting on,
        and a stock build keeps paging after `ensure_covers` has returned.
        """
        loop = asyncio.get_running_loop()
        fut = self._inflight.get(key)
        # A future of ANOTHER loop is a build that loop's teardown abandoned (one process
        # runs one loop; a test runner runs many): never join it — start afresh.
        if fut is not None and not fut.done() and fut.get_loop() is loop:
            return fut
        fut = loop.create_future()
        self._inflight[key] = fut
        task = loop.create_task(self._lead(key, fut, factory, progress))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return fut

    async def _lead(
        self,
        key: Hashable,
        fut: "asyncio.Future",
        factory: Callable[[], Awaitable[Any]],
        progress: Optional[_Progress] = None,
    ) -> None:
        try:
            result = await factory()
            if not fut.done():
                fut.set_result(result)
        except asyncio.CancelledError:
            # CancelledError is a BaseException: without this arm the future stays
            # pending and every joiner parked on it hangs for the life of the process.
            fail_shared_future(fut, RuntimeError(f"price history build {key!r} was cancelled"))
            raise
        except Exception as e:
            # Logged HERE as well as by each caller: a background build (`ensure_covers`
            # already returned) may have no caller left to say it failed.
            logger.warning(
                "price history build %r failed: %s: %s", key, type(e).__name__, e
            )
            fail_shared_future(fut, e)
        finally:
            # Each build clears only ITS OWN entries: a build that a newer one superseded
            # (another loop's, or one `clear()` dropped) must not pop the newer build's
            # future or progress — that would leave the newer build's waiters unwoken.
            if self._inflight.get(key) is fut:
                self._inflight.pop(key, None)
            if progress is not None:
                if self._progress.get(key) is progress:
                    self._progress.pop(key, None)
                progress.publish()  # wakes `ensure_covers` waiters to read the result

    # ── daily history ─────────────────────────────────────────────────────

    @staticmethod
    def _history_key(asset: str, symbol: str, fmp: Any) -> Tuple:
        token = _client_token(fmp) if asset == ASSET_STOCK else 0
        return ("hist", asset, token, symbol.upper())

    def _cached_history(self, key: Tuple) -> Optional[PriceColumns]:
        entry = self._histories.get(key)
        if entry is None:
            return None
        if not entry.fresh(_settled_cutoff_date()):
            self._histories.pop(key)
            return None
        return entry.value

    async def full_daily(
        self, asset: str, symbol: str, *, fmp: Any = None
    ) -> PriceColumns:
        """The whole settled daily history. Raises on a failed or EMPTY source.

        ``symbol`` is the screen's own symbol (an index's ``^GSPC``, a commodity's FMP
        symbol ``GCUSD``, a coin's base ``BTC``); ``fmp`` is required for a stock (the
        route's client, so a test double is honoured) and ignored otherwise.
        """
        if asset not in ASSETS:
            raise ValueError(f"unknown asset class {asset!r}")
        if asset == ASSET_STOCK and fmp is None:
            raise ValueError("a stock history needs the caller's FMP client")
        key = self._history_key(asset, symbol, fmp)
        cached = self._cached_history(key)
        if cached is not None:
            return cached
        fut = self._start_history(key, asset, symbol, fmp)
        return await asyncio.shield(fut)

    def _start_history(self, key: Tuple, asset: str, symbol: str, fmp: Any) -> "asyncio.Future":
        running = self._inflight.get(key)
        if (running is not None and not running.done()
                and running.get_loop() is asyncio.get_running_loop()):
            return running
        cutoff = _settled_cutoff_date()
        progress = _Progress() if asset == ASSET_STOCK else None
        if progress is not None:
            self._progress[key] = progress

        async def _build() -> PriceColumns:
            if asset == ASSET_STOCK:
                cols = await self._build_stock(fmp, symbol.upper(), cutoff, progress)
            elif asset == ASSET_CRYPTO:
                cols = await self._build_crypto(symbol.upper())
            else:
                cols = await self._build_from_service(asset, symbol, cutoff)
            if not len(cols):
                raise EmptyPriceHistory(f"{asset} {symbol}: the source returned no daily rows")
            ttl = _CRYPTO_HISTORY_TTL if asset == ASSET_CRYPTO else _HISTORY_TTL
            self._histories.put(
                key,
                _Entry(cols, cycle=cols.settled_through, ttl=ttl,
                       client=fmp if asset == ASSET_STOCK else None),
                cols.nbytes,
            )
            logger.info(
                "price history built for %s %s: %d rows from %s (%d bytes)",
                asset, symbol, len(cols),
                date.fromordinal(cols.dates[0]).isoformat(), cols.nbytes,
            )
            return cols

        return self._join_or_start(key, _build, progress)

    @staticmethod
    def _overview_seed(fmp: Any, symbol: str, cutoff: str) -> Optional[List[Dict]]:
        """The overview bundle's settled `stock_historical`, when it is in MEMORY, was
        fetched with this same client and is cut at this same session — else None.

        Never constructs the overview service and never awaits its in-flight bundle: the
        first-paint core path calls this, and the bundle is 14 FMP calls.
        """
        from app.services import stock_overview_service as so

        svc = getattr(so, "_service", None)
        if svc is None or getattr(svc, "fmp", None) is not fmp:
            return None
        bundle = so._fundamentals_mem_get(f"fundamentals:{symbol}")
        if not isinstance(bundle, dict) or bundle.get(so._SETTLED_THROUGH_KEY) != cutoff:
            return None
        hist = bundle.get("stock_historical")
        if not isinstance(hist, list) or not hist:
            return None
        return hist

    async def _build_stock(
        self, fmp: Any, symbol: str, cutoff: str, progress: Optional[_Progress]
    ) -> PriceColumns:
        from app.services import chart_helper as CH

        pages: List[List[Dict]] = []  # newest page first

        def _cols(complete: bool) -> PriceColumns:
            rows: List[Dict] = []
            for page in reversed(pages):  # oldest page first: the newer page wins a tie
                rows.extend(page)
            return PriceColumns.from_rows(
                CH.settled_bars(rows, cutoff), settled_through=cutoff, complete=complete
            )

        to_date: Optional[str] = None
        seed = self._overview_seed(fmp, symbol, cutoff)
        if seed is not None:
            pages.append(seed)
            if len(seed) < _SEED_COMPLETE_BELOW:
                # The bundle asked from 1900 and got under a full page: that IS the
                # whole history, at zero extra FMP calls.
                return _cols(complete=True)
            if progress is not None:
                progress.publish(_cols(complete=False))
            earliest = min((str(r.get("date") or "")[:10] for r in seed if isinstance(r, dict)),
                           default="")
            try:
                to_date = (date.fromisoformat(earliest) - timedelta(days=1)).isoformat()
            except ValueError:
                to_date = None
                pages.clear()
        async for page in CH._iter_daily_pages(
            fmp, symbol, from_date=_HISTORY_FROM, to_date=to_date, max_pages=_MAX_DAILY_PAGES
        ):
            pages.append(page)
            if progress is not None:
                progress.publish(_cols(complete=False))
        return _cols(complete=True)

    @staticmethod
    async def _build_from_service(asset: str, symbol: str, cutoff: str) -> PriceColumns:
        from app.services.chart_helper import settled_bars

        if asset == ASSET_ETF:
            from app.services.etf_service import get_etf_service

            rows = await get_etf_service()._get_history_strict(symbol)
        elif asset == ASSET_INDEX:
            from app.services.index_service import get_index_service

            # The index service maps the symbol to its entitled proxy (`_proxy_for`).
            rows = await get_index_service()._get_history_strict(symbol)
        else:
            from app.services.commodity_service import get_commodity_service

            rows = await get_commodity_service()._get_history_strict(symbol)
        # A new list: the service's cached history is never mutated here.
        return PriceColumns.from_rows(
            settled_bars(rows, cutoff), settled_through=cutoff, complete=True
        )

    @staticmethod
    async def _build_crypto(base: str) -> PriceColumns:
        from app.services.crypto_service import _series_is_capped, get_crypto_service

        svc = get_crypto_service()
        cap_days = svc._history_days_cap()
        rows = await svc._cg_history(base, cap_days)
        return PriceColumns.from_rows(
            rows, settled_through=None, complete=True,
            capped_start=_series_is_capped(rows, cap_days),
        )

    async def ensure_covers(self, fmp: Any, symbol: str, from_date: date) -> PriceColumns:
        """A stock's daily history reaching back to at least ``from_date``.

        Returns the moment the newest page(s) reach it — usually the first page (~19.8
        years) — while the older pages keep loading behind the same build, so a cold
        first paint waits for one FMP call, not three. The result may therefore be a
        PARTIAL (``complete=False``); callers bucket it on the fly and never memoise it.
        Raises like `full_daily` when the build fails before covering the date.
        """
        if fmp is None:
            raise ValueError("ensure_covers needs the caller's FMP client")
        key = self._history_key(ASSET_STOCK, symbol, fmp)
        cached = self._cached_history(key)
        if cached is not None:
            return cached
        fut = self._start_history(key, ASSET_STOCK, symbol, fmp)
        progress = self._progress.get(key)
        want = from_date.toordinal()
        while True:
            if fut.done() or progress is None:
                return await asyncio.shield(fut)
            changed = progress.changed  # taken BEFORE the check: no publish can be missed
            partial = progress.partial
            if partial is not None and len(partial) and partial.dates[0] <= want:
                return partial
            await changed.wait()

    # ── bucketed series ──────────────────────────────────────────────────

    def _aggregate(
        self, memo_key: Optional[Tuple], cols: PriceColumns, interval: str
    ) -> PriceColumns:
        """Weekly/monthly buckets of ``cols``, memoised per source history (``memo_key``
        None → computed, not memoised, e.g. for an `ensure_covers` partial)."""
        from app.services.chart_helper import _aggregate_prices, drop_partial_leading_bucket

        if memo_key is not None:
            hit = self._aggregates.get(memo_key)
            if hit is not None and hit[0] is cols:
                return hit[1]
        daily = cols.rows()
        bars = _aggregate_prices(daily, interval)
        if cols.capped_start:
            bars = drop_partial_leading_bucket(bars, daily, interval)
        agg = PriceColumns.from_rows(
            bars, settled_through=cols.settled_through, complete=cols.complete,
            capped_start=False,
        )
        if memo_key is not None and cols.complete:
            self._aggregates.put(memo_key, (cols, agg), agg.nbytes)
        return agg

    async def aggregated(
        self, asset: str, symbol: str, interval: str, *, fmp: Any = None
    ) -> PriceColumns:
        """The whole bucketed series, memoised per (symbol, interval, settled history)."""
        if interval not in BUCKET_INTERVALS:
            raise ValueError(f"not a bucket interval: {interval!r}")
        cols = await self.full_daily(asset, symbol, fmp=fmp)
        memo_key = ("agg",) + self._history_key(asset, symbol, fmp) + (interval,)
        return self._aggregate(memo_key, cols, interval)

    async def aggregated_rows(
        self, fmp: Any, symbol: str, interval: str, *, covering: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        """A stock's bucketed rows (for `chart_helper.fetch_chart_data`).

        With ``covering``, buckets come from the first pages that reach that date, so a
        chart never waits for decades it does not draw; the caller keeps ``covering`` at
        least one bucket before its window, so the oldest bucket it keeps is complete.
        """
        if covering is None:
            return (await self.aggregated(ASSET_STOCK, symbol, interval, fmp=fmp)).rows()
        cols = await self.ensure_covers(fmp, symbol, covering)
        memo_key = ("agg",) + self._history_key(ASSET_STOCK, symbol, fmp) + (interval,)
        return self._aggregate(memo_key if cols.complete else None, cols, interval).rows()

    async def daily_rows(self, fmp: Any, symbol: str) -> List[Dict[str, Any]]:
        """A stock's whole settled daily history as chart rows (the ALL range)."""
        return (await self.full_daily(ASSET_STOCK, symbol, fmp=fmp)).rows()

    # ── history pages ─────────────────────────────────────────────────────

    @staticmethod
    def resolve_symbol(asset: str, symbol: str) -> Tuple[str, Optional[str]]:
        """``(history symbol, intraday upstream symbol or None)`` for a screen symbol —
        the SAME mapping each detail service's `_get_history` / `_get_chart` applies:

          * index: the entitled proxy ETF (`_proxy_for`) — a ``^`` symbol is refused by
            FMP's entitlement guard;
          * commodity: the FMP symbol for the daily history; intraday uses the ETF ref
            (`_ref_of`), and a FRED-sourced screen (CL/NG) has NO intraday (None);
          * crypto: the coin's base symbol (CoinGecko is queried by base).
        """
        s = (symbol or "").strip().upper()
        if asset == ASSET_INDEX:
            from app.services.index_service import _proxy_for

            return s, _proxy_for(s)
        if asset == ASSET_COMMODITY:
            from app.services.commodity_service import (
                _COMMODITY_SOURCE_FRED,
                _raise_if_withdrawn,
                _ref_of,
                _resolve_fmp_symbol,
                _source_of,
            )

            root = s[:-3] if len(s) > 3 and s.endswith("USD") else s
            _raise_if_withdrawn(root)
            fmp_symbol = _resolve_fmp_symbol(root)
            if _source_of(fmp_symbol) == _COMMODITY_SOURCE_FRED:
                return fmp_symbol, None
            return fmp_symbol, _ref_of(fmp_symbol)
        if asset == ASSET_CRYPTO:
            from app.services.coingecko_adapter import crypto_base_symbol

            base = crypto_base_symbol(s)
            return base, base
        return s, s

    async def history_page(
        self,
        asset: str,
        symbol: str,
        interval: str,
        *,
        before: str,
        limit: Optional[int] = None,
        extended_hours: bool = False,
        fmp: Any = None,
        display_symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        """One page of bars strictly older than ``before`` (the `ChartHistoryResponse`
        shape). Raises `ChartHistoryInputError` on bad input and a typed upstream error
        (or `EmptyPriceHistory`) when the source cannot serve it."""
        if asset not in ASSETS:
            raise ChartHistoryInputError(f"unknown asset class {asset!r}")
        allowed = CRYPTO_PAGE_INTERVALS if asset == ASSET_CRYPTO else FMP_PAGE_INTERVALS
        served = interval
        if asset == ASSET_CRYPTO and interval in PAGE_INTRADAY_INTERVALS:
            served = _CRYPTO_INTRADAY
        if served not in allowed:
            raise ChartHistoryInputError(f"interval {interval!r} cannot be paged for {asset}")
        before_day = parse_before(served, before)
        if limit is None:
            limit = DEFAULT_PAGE_LIMITS.get(served, DEFAULT_INTRADAY_PAGE_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_LIMIT:
            raise ChartHistoryInputError(f"limit must be 1..{MAX_PAGE_LIMIT}")

        if asset == ASSET_STOCK and fmp is None:
            raise ValueError("a stock history page needs the caller's FMP client")
        history_symbol, upstream = self.resolve_symbol(asset, symbol)
        display = (display_symbol or symbol or "").strip().upper()
        base: Dict[str, Any] = {"symbol": display, "interval": served}
        rounding = _close_rounding(asset, served)

        if served in PAGE_INTRADAY_INTERVALS:
            if asset == ASSET_CRYPTO:
                page = await self._crypto_hourly_page(history_symbol, before, before_day, limit)
            else:
                page = await self._intraday_page(
                    asset, upstream, served, before, before_day, limit,
                    # Only stocks have an extended-hours chart; ETF / index / commodity
                    # range responses are regular-session, and a page must match them.
                    extended_hours=bool(extended_hours) and asset == ASSET_STOCK,
                    fmp=fmp, rounding=rounding,
                )
            return {**base, **page}

        cols = await self.full_daily(asset, history_symbol, fmp=fmp)
        if served == DAILY:
            series = cols
        else:
            memo_key = ("agg",) + self._history_key(asset, history_symbol, fmp) + (served,)
            series = self._aggregate(memo_key, cols, served)
        hi = series.index_before(before_day)
        lo = max(0, hi - limit)
        return {
            **base,
            "bars": series.rows(lo, hi, round_close=rounding),
            # False only here: the series is non-empty (an empty one raised above) and
            # this page reached its first row.
            "has_more": lo > 0,
            "settled_through": cols.settled_through,
            # The first row is the symbol's first bar unless the source was cut at a plan
            # cap (CoinGecko's daily look-back): there the leftmost bars are warm-up.
            "reached_start": lo == 0 and cols.complete and not cols.capped_start,
        }

    async def _intraday_window(
        self,
        asset: str,
        fmp: Any,
        upstream: str,
        interval: str,
        extended_hours: bool,
        start: date,
        today: date,
    ) -> List[Dict[str, Any]]:
        """One grid window of intraday bars, shared by every cursor that falls in it."""
        from app.services.chart_helper import _filter_regular_hours, _normalize_prices

        end = start + timedelta(days=_INTRADAY_WINDOW_DAYS[interval] - 1)
        cutoff = _settled_cutoff_date()
        key = ("win", asset, _client_token(fmp), upstream, interval, extended_hours,
               start.toordinal())
        entry = self._windows.get(key)
        if entry is not None and entry.fresh(cutoff):
            return entry.value
        is_open = end >= today

        async def _build() -> List[Dict[str, Any]]:
            kwargs: Dict[str, Any] = {
                "interval": interval,
                "from_date": start.isoformat(),
                "to_date": min(end, today).isoformat(),
                # A non-list body RAISES here: the swallowing default would turn an FMP
                # error into an empty window, and empty windows END the series.
                "strict": True,
            }
            if extended_hours:
                kwargs["extended"] = True
            raw = await fmp.get_intraday_prices(upstream, **kwargs)
            rows = sorted(
                (r for r in raw if isinstance(r, dict)), key=lambda p: p.get("date") or ""
            )
            rows = _normalize_prices(rows)
            if not extended_hours:
                rows = _filter_regular_hours(rows)
            rows = [r for r in rows if isinstance(r.get("date"), str) and len(r["date"]) > 10]
            self._windows.put(
                key,
                _Entry(rows, cycle=None if is_open else cutoff,
                       ttl=_OPEN_WINDOW_TTL if is_open else _CLOSED_WINDOW_TTL, client=fmp),
                len(rows) * _ROW_DICT_BYTES + 256,
            )
            return rows

        return await asyncio.shield(self._join_or_start(key, _build))

    async def _intraday_page(
        self,
        asset: str,
        upstream: Optional[str],
        interval: str,
        before: str,
        before_day: date,
        limit: int,
        *,
        extended_hours: bool,
        fmp: Any,
        rounding: Optional[Callable[[float], float]],
    ) -> Dict[str, Any]:
        cutoff = _settled_cutoff_date()
        if upstream is None:
            # A FRED-sourced commodity (CL/NG) is one settled value a day: there is no
            # intraday series to page, which is an answer, not a failure.
            return {"bars": [], "has_more": False, "settled_through": cutoff,
                    "reached_start": False}
        if asset != ASSET_STOCK:
            fmp = self._service_fmp(asset)
        today = datetime.now(timezone.utc).astimezone(ET).date()
        floor = today - timedelta(days=_INTRADAY_MAX_LOOKBACK_DAYS[interval])
        span = timedelta(days=_INTRADAY_WINDOW_DAYS[interval])
        start = _window_start(before_day, interval)
        bars: List[Dict[str, Any]] = []
        empty_run = 0
        scanned = 0
        exhausted = False
        ended_on_empty = False
        while True:
            if start + span <= floor:
                exhausted = True
                break
            rows = await self._intraday_window(
                asset, fmp, upstream, interval, extended_hours, start, today
            )
            scanned += 1
            if rows:
                empty_run = 0
                older = [r for r in rows if r["date"] < before]
                bars = older + bars
            else:
                empty_run += 1
                if empty_run >= _INTRADAY_EMPTY_WINDOWS_TO_END:
                    exhausted = True
                    ended_on_empty = True
                    break
            if len(bars) >= limit:
                break
            # Keep going while the page holds nothing: a page with no bars and
            # `has_more: true` would hand the client back its own cursor forever.
            if bars and scanned >= _MAX_WINDOWS_PER_PAGE:
                break
            start -= span
        trimmed = len(bars) > limit
        # Copied BEFORE rounding: these row dicts are the cached window's own.
        bars = _copy_rows(bars[-limit:])
        if rounding is not None:
            for r in bars:
                r["close"] = rounding(r["close"])
        reached_start = False
        if exhausted and ended_on_empty and not trimmed and asset == ASSET_STOCK:
            oldest_day = date.fromisoformat(bars[0]["date"][:10]) if bars else before_day
            reached_start = await self._intraday_reaches_listing(upstream, oldest_day, fmp)
        return {
            "bars": bars,
            "has_more": trimmed or not exhausted,
            "settled_through": cutoff,
            # True only where the intraday walk ended on empty windows AND the stock's daily
            # history begins that same day or later (a recent listing). The look-back bound,
            # or empty windows over a span the daily history covers, is the provider's depth,
            # not the symbol's first bar.
            "reached_start": reached_start,
        }

    async def _intraday_reaches_listing(self, symbol: str, oldest_day: date, fmp: Any) -> bool:
        """Does a stock's daily history begin on or after ``oldest_day`` — i.e. is the oldest
        intraday bar on the listing day? Only asked when intraday paging ended on empty
        windows (a recent listing, or a provider depth gap), so the daily read is rare and
        Tier-1 cached. A failure answers False: the chart keeps its indicator floor."""
        try:
            cols = await self.full_daily(ASSET_STOCK, symbol, fmp=fmp)
        except Exception as e:
            logger.warning(
                "chart history: listing check failed for %s (intraday reached_start stays "
                "false): %s: %s", symbol, type(e).__name__, e,
            )
            return False
        return len(cols.dates) > 0 and cols.dates[0] >= oldest_day.toordinal()

    @staticmethod
    def _service_fmp(asset: str) -> Any:
        """The FMP client an ETF / index / commodity screen already uses — the route
        holds none (endpoints do not add integration clients)."""
        if asset == ASSET_ETF:
            from app.services.etf_service import get_etf_service

            return get_etf_service().fmp
        if asset == ASSET_INDEX:
            from app.services.index_service import get_index_service

            return get_index_service().fmp
        from app.services.commodity_service import get_commodity_service

        return get_commodity_service().fmp

    async def _crypto_hourly_series(self, base: str) -> List[Dict[str, Any]]:
        key = ("cg_hourly", base)
        entry = self._crypto_hourly.get(key)
        if entry is not None and entry.fresh(None):
            return entry.value

        async def _build() -> List[Dict[str, Any]]:
            from app.services.crypto_service import CryptoService, get_crypto_service

            raw = await get_crypto_service()._cg_history(base, _CRYPTO_HOURLY_DAYS, intraday=True)
            rows = [r for r in CryptoService._chart_rows_from(raw)
                    if isinstance(r.get("date"), str) and len(r["date"]) > 10]
            if not rows:
                raise EmptyPriceHistory(f"crypto {base}: CoinGecko returned no hourly rows")
            self._crypto_hourly.put(
                key, _Entry(rows, cycle=None, ttl=_CRYPTO_HOURLY_TTL),
                len(rows) * _ROW_DICT_BYTES + 256,
            )
            return rows

        return await asyncio.shield(self._join_or_start(key, _build))

    async def _crypto_hourly_page(
        self, base: str, before: str, before_day: date, limit: int
    ) -> Dict[str, Any]:
        series = await self._crypto_hourly_series(base)
        try:
            edge = datetime.strptime(before, "%Y-%m-%d %H:%M:%S") - _CRYPTO_SEAM_GAP
        except ValueError as e:  # parse_before already validated it
            raise ChartHistoryInputError(str(e)) from None
        threshold = edge.strftime("%Y-%m-%d %H:%M:%S")
        dates = [r["date"] for r in series]
        hi = bisect_left(dates, threshold)
        lo = max(0, hi - limit)
        return {
            "bars": _copy_rows(series[lo:hi]),
            # The 90-day hourly series is CoinGecko's ceiling; its first point ends it.
            "has_more": lo > 0,
            "settled_through": None,
            # That ceiling is not the coin's first bar.
            "reached_start": False,
        }

    # ── `fetch_chart_data` result cache ───────────────────────────────────

    async def cached_chart_rows(
        self,
        fmp: Any,
        key: Tuple,
        *,
        intraday: bool,
        build: Callable[[], Awaitable[List[Dict[str, Any]]]],
    ) -> List[Dict[str, Any]]:
        """One shared `fetch_chart_data` result per (client, symbol, range, interval,
        session, window): intraday 30 s; daily and bucketed 60 s while the market is
        open and 10 min otherwise, and never across a close cycle. An empty result is
        never cached (it is usually a degraded upstream answer)."""
        full_key = ("chart", _client_token(fmp)) + tuple(key)
        cutoff = _settled_cutoff_date()
        entry = self._results.get(full_key)
        if entry is not None and entry.fresh(cutoff):
            return _copy_rows(entry.value)

        async def _build() -> List[Dict[str, Any]]:
            rows = await build()
            if rows:
                if intraday:
                    ttl = _INTRADAY_RESULT_TTL
                else:
                    ttl = _DAILY_RESULT_TTL_OPEN if is_market_active() else _DAILY_RESULT_TTL_CLOSED
                self._results.put(
                    full_key, _Entry(rows, cycle=cutoff, ttl=ttl, client=fmp),
                    len(rows) * _ROW_DICT_BYTES + 256,
                )
            return rows

        rows = await asyncio.shield(self._join_or_start(full_key, _build))
        return _copy_rows(rows)


_service: Optional[PriceHistoryService] = None


def get_price_history_service() -> PriceHistoryService:
    global _service
    if _service is None:
        _service = PriceHistoryService()
    return _service
