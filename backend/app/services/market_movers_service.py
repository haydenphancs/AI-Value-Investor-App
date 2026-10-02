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
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from postgrest import CountMethod

from app.database import get_supabase
from app.utils.inflight import fail_shared_future
from app.utils.market_hours import session_trading_date
from app.utils.postgrest_paging import fetch_all_rows_concurrent
from app.services.price_service import PriceService, _finite, price_source

logger = logging.getLogger(__name__)

# ── the close map (`movers:closes`) ─────────────────────────────────────────────────
# Closes move once per session, and the map is a ~74-page Supabase sweep (~3 s with
# `_CLOSE_PAGE_WORKERS` pages in flight, 11-14 s serially), so a READ never rebuilds a map
# it already has unless that map is very old:
#
#   * younger than `_CLOSES_SOFT_TTL`  → served as is;
#   * older, but younger than `_CLOSES_MAX_AGE` → served as is AND one background rebuild
#     starts (deduped);
#   * older than `_CLOSES_MAX_AGE`, or no map at all → the read awaits a rebuild, and if
#     that fails it still gets the old map (WARNING), if there is one.
#
# Serving an old map is honest, not merely fast: every reader decides per ROW whether its
# `trade_date` can still be the day-change denominator (`PriceService._snapshot_is_current`
# / `_pick_denominator`), so a row from a session that is too old reads "change unknown",
# never a multi-session move labelled as today's.
_CLOSES_KEY = "movers:closes"
# The soft TTL sits ABOVE the hourly ingest's cycle, so that loop's own rebuild is normally
# the only sweep. `main._run_close_snapshot_loop` ingests, rebuilds (`refresh_closes(
# after_write=True)`), then sleeps `_CLOSE_INGEST_PERIOD_SECONDS`; the next map therefore
# lands one period PLUS one ingest after the last (two ~12 MB batch-eod fetches of ~10 s
# each, then the upsert: typically 10-40 s). At a soft TTL equal to the period (the old
# 3600 s), a read inside that ingest window kicked a second sweep, often over a half-written
# upsert, and `after_write` then ran a third: ~48 sweeps a day instead of 24. The 10-min
# budget is ~15x a normal ingest. A STALLED cycle (FMP sending nothing: up to ~15 min, see
# `fmp._ENDPOINT_TIMEOUTS`) costs one extra sweep, and a harmless one: the ingest writes
# only after both fetches return, so that sweep reads an unchanged table.
# Pinned against the loop's literal sleep by tests/test_market_movers_close_map_swap.py.
_CLOSE_INGEST_PERIOD_SECONDS = 3600.0
_CLOSE_INGEST_BUDGET_SECONDS = 600.0
_CLOSES_SOFT_TTL = _CLOSE_INGEST_PERIOD_SECONDS + _CLOSE_INGEST_BUDGET_SECONDS   # 4200 s
_CLOSES_MAX_AGE = 43200.0
# Rows are only ever UPSERTED into `market_close_snapshot` (nothing in app/ deletes them),
# so a rebuilt map much smaller than the live one is a bad read, not a smaller market —
# refused, and the live map kept. Accepted anyway once the live map is past
# `_CLOSES_MAX_AGE`: a deliberate bulk delete must not freeze the map until a restart.
_CLOSES_MIN_KEEP_SHARE = 0.9
# Pages in flight for the sweep. The PostgREST httpx pool is 20 connections
# (`database.py`); four leaves the rest for request-path reads.
_CLOSE_PAGE_WORKERS = 4
# The derived universe rides the screener's own 60 s freshness. It stays 60 s inside a
# closed window too, where `price_service` keeps the screener sweep for 15 min: rebuilding
# it is CPU only (no FMP call), and it picks up a swapped close map within a minute.
# `home_dashboard_service._SESSION_GRACE_SECONDS` counts this layer.
_UNIVERSE_TTL = 60.0
# A universe built while the close map was DOWN (every day change unknown) is memoised
# under its own key for this long — a herd guard, not an answer. Without it every
# request during a Supabase outage re-ran the paged full-table read to its timeout;
# with the normal TTL the outage was frozen as "no day changes" for a minute past its
# end. `get_universe` has no `_inflight` of its own (only the close map's
# `refresh_closes` does).
_DEGRADED_UNIVERSE_TTL = 15.0

# An "average move" computed from two members is noise presented as a statistic. Sectors
# always clear this easily (11 groups over ~11k symbols); the long tail of ~150 industries
# does not, and publishing those would put a made-up number on a real screen.
_MIN_GROUP_MEMBERS = 5

_cache: Dict[str, Tuple[float, Any]] = {}
_inflight: Dict[str, asyncio.Future] = {}

# The background close-map rebuild a read starts. `asyncio.create_task` keeps only a WEAK
# reference, so `_background_tasks` holds the strong one until the done callback drops it;
# `_closes_refresh_task` is the dedup — at most one such task alive at a time.
_background_tasks: set = set()
_closes_refresh_task: Optional[asyncio.Task] = None
# `time.monotonic()` at which the current close-map leader started reading — how
# `refresh_closes(after_write=True)` tells a build that can contain freshly written rows
# from one that began before them.
_closes_build_started: float = 0.0


class CloseMapRefused(RuntimeError):
    """A rebuilt close map failed its sanity check and was NOT swapped in."""


def _on_close_refresh_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            "movers: background close-map refresh failed (%s: %s) — the previous map "
            "stays in service", type(exc).__name__, exc,
        )


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
        `range` asked for (verified: `.range(0, 49999)` still returns 1,000). So it is ~74
        requests either way — worth doing in the background, never per request.

        Reads `_cache` DIRECTLY: `_cache_get` POPS an expired entry, and dropping the live
        map is exactly what used to put an 11-14 s sweep in front of a request (after
        every deploy, and once an hour when the ingest invalidated it). The ages that
        decide serve / serve-and-refresh / await are at `_CLOSES_SOFT_TTL` above.
        """
        hit = _cache.get(_CLOSES_KEY)
        if hit is not None:
            ts, closes = hit
            age = time.time() - ts
            if age < _CLOSES_MAX_AGE:
                if age >= _CLOSES_SOFT_TTL or age < 0:
                    # Negative = the wall clock stepped back past the stamp; rebuild so
                    # the stamp is re-taken rather than trusting it for a day.
                    self._kick_close_map_refresh()
                return closes

        # No map, or one too old to serve without trying. The rebuild runs as an OWNED
        # task joined under `shield`, so a reader that gives up (Home's 8 s guard) does
        # not cancel the sweep everyone else — and the next request — is waiting for.
        try:
            return await asyncio.shield(self._kick_close_map_refresh())
        except Exception as e:
            old = _cache.get(_CLOSES_KEY)
            if old is None:
                raise
            logger.warning(
                "movers: close-map rebuild failed (%s: %s) — serving the previous map, "
                "%.0f s old (rows whose session is too old read as unknown change)",
                type(e).__name__, e, time.time() - old[0],
            )
            return old[1]

    def _kick_close_map_refresh(self) -> "asyncio.Future[Dict[str, Dict[str, Any]]]":
        """Start ONE background `refresh_closes()`, or return the build already running.

        Never awaits. Deduped twice: on the shared `_inflight` future (a build led
        elsewhere — the hourly loop, the boot warm) and on the task this method started
        last (which has not necessarily reached `_inflight` yet).
        """
        global _closes_refresh_task
        loop = asyncio.get_running_loop()
        shared = _inflight.get(_CLOSES_KEY)
        if shared is not None and shared.get_loop() is loop:
            return shared
        running = _closes_refresh_task
        # Same loop only: a task left pending by a loop that has since closed never
        # finishes, and adopting it would park this reader forever.
        if running is not None and not running.done() and running.get_loop() is loop:
            return running
        task = loop.create_task(self.refresh_closes(), name="movers:closes:refresh")
        _closes_refresh_task = task
        _background_tasks.add(task)
        task.add_done_callback(_on_close_refresh_done)
        return task

    async def refresh_closes(self, *, after_write: bool = False) -> Dict[str, Dict[str, Any]]:
        """Rebuild the close map and swap it in. The ONLY writer of `movers:closes`.

        The new map is built completely BEFORE it replaces the old one, so readers are
        never left without a map while the sweep runs. A rebuilt map is refused
        (`CloseMapRefused`, ERROR, the live map kept) when it is empty, or smaller than
        `_CLOSES_MIN_KEEP_SHARE` of the live map while that map is still servable.

        Concurrent callers share one build through `_inflight[movers:closes]`.
        `after_write=True` is for a caller that has just WRITTEN rows (the hourly ingest
        loop): a build that started before this call cannot contain them, so it is waited
        out and a new one led — or a newer one joined.
        """
        global _closes_build_started
        key = _CLOSES_KEY
        loop = asyncio.get_running_loop()
        called_at = time.monotonic()
        while True:
            shared = _inflight.get(key)
            if shared is None:
                break
            if shared.get_loop() is not loop:
                # Its leader's loop is gone (it never ran `finally`); nothing else will
                # ever settle or clear it.
                _inflight.pop(key, None)
                continue
            if not after_write or _closes_build_started >= called_at:
                return await asyncio.shield(shared)
            try:
                await asyncio.shield(shared)
            except Exception as e:                  # noqa: BLE001 — superseded below
                logger.info(
                    "movers: a close-map build that predates the ingest ended in %s: %s "
                    "— leading a fresh one", type(e).__name__, e,
                )
            # The leader's `finally` popped the key before this frame resumed, so the loop
            # now leads — or joins a NEWER build. Defensive: an entry that is still this
            # same, settled future has no leader left to clear it; drop it rather than spin.
            if _inflight.get(key) is shared and shared.done():
                _inflight.pop(key, None)

        future: asyncio.Future = loop.create_future()
        _inflight[key] = future
        _closes_build_started = time.monotonic()
        started = _closes_build_started
        try:
            closes = await asyncio.to_thread(self._select_all_closes)
            self._check_close_map(closes)
            _cache_set(key, closes)
            logger.info(
                "movers: close map refreshed in %.1fs (%d rows)",
                time.monotonic() - started, len(closes),
            )
            if not future.done():
                future.set_result(closes)
            return closes
        except asyncio.CancelledError:
            # CancelledError is a BaseException, so it skips `except Exception`
            # below. Without this arm the future is never settled and every joiner
            # parked on `asyncio.shield(...)` waits for the life of the process —
            # while `finally` has already popped the key, so nothing can recover it.
            fail_shared_future(future, RuntimeError("shared fetch was cancelled"))
            raise
        except Exception as e:
            fail_shared_future(future, e)
            raise
        finally:
            _inflight.pop(key, None)

    @staticmethod
    def _check_close_map(closes: Dict[str, Dict[str, Any]]) -> None:
        """Refuse a rebuilt map that cannot be right. Raises `CloseMapRefused` (ERROR)."""
        if not closes:
            logger.error(
                "movers: rebuilt close map is EMPTY — refused (%s); market_close_snapshot "
                "has no rows or the read lost them",
                "the live map stays in service" if _CLOSES_KEY in _cache
                else "no map in memory, day changes read as unknown",
            )
            raise CloseMapRefused("rebuilt close map is empty")
        live = _cache.get(_CLOSES_KEY)
        if live is None:
            return
        live_ts, live_map = live
        live_age = time.time() - live_ts
        floor = len(live_map) * _CLOSES_MIN_KEEP_SHARE
        if len(closes) >= floor:
            return
        if live_age >= _CLOSES_MAX_AGE:
            logger.error(
                "movers: rebuilt close map has %d rows against %d live — accepted anyway "
                "because the live map is %.0f s old (past the %.0f s max age); if rows "
                "were not deliberately deleted, market_close_snapshot lost data",
                len(closes), len(live_map), live_age, _CLOSES_MAX_AGE,
            )
            return
        logger.error(
            "movers: rebuilt close map has %d rows against %d live (below %.0f%%) — "
            "refused as a bad read; keeping the live map (%.0f s old)",
            len(closes), len(live_map), _CLOSES_MIN_KEEP_SHARE * 100, live_age,
        )
        raise CloseMapRefused(
            f"rebuilt close map shrank from {len(live_map)} to {len(closes)} rows"
        )

    @staticmethod
    def _select_all_closes() -> Dict[str, Dict[str, Any]]:
        """The whole `market_close_snapshot`, keyed by symbol — complete or raising.

        `fetch_all_rows_concurrent` reads `_CLOSE_PAGE_WORKERS` pages at a time against an
        exact count and raises `PagedReadIncomplete` rather than return a map with a hole
        in it. ORDER BY symbol (the primary key) is what makes `.range()` pages stable:
        without it Postgres may return rows in a different physical order between
        statements, and the hourly upsert rewrites every tuple.
        """
        supabase = get_supabase()
        rows = fetch_all_rows_concurrent(
            lambda: supabase.table("market_close_snapshot").select(
                "symbol,close,previous_close,trade_date"
            ),
            count_query=lambda: supabase.table("market_close_snapshot").select(
                "symbol", count=CountMethod.exact, head=True
            ),
            order_by="symbol",
            what="market_close_snapshot",
            workers=_CLOSE_PAGE_WORKERS,
        )
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            sym = str(r.get("symbol") or "").strip().upper() if isinstance(r, dict) else ""
            if sym:
                out[sym] = r
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
        degraded = _cache_get(f"{key}:degraded", _DEGRADED_UNIVERSE_TTL)
        if degraded is not None:
            return degraded

        ps = price_source()
        rows, closes = await asyncio.gather(
            ps._get_universe(), self._all_closes(), return_exceptions=True,
        )
        if isinstance(rows, Exception):
            logger.warning("movers: screener universe unavailable: %s: %s",
                           type(rows).__name__, rows)
            return {}
        closes_failed = isinstance(closes, Exception)
        if closes_failed:
            logger.warning("movers: close map unavailable: %s: %s — universe served "
                           "with unknown day changes and NOT cached, so the next call "
                           "retries the close map",
                           type(closes).__name__, closes)
            closes = {}

        out: Dict[str, Dict[str, Any]] = {}
        stale: Dict[str, str] = {}
        for symbol, r in rows.items():
            price = _finite(r.get("price"))
            if price is None or price <= 0:
                continue
            snap = closes.get(symbol)
            stale_date = PriceService._stale_trade_date(snap)
            if stale_date:
                stale[symbol] = stale_date
            prev = PriceService._pick_denominator(price, snap)
            change_pct = None
            if prev is not None and prev > 0:
                change_pct = (price / prev - 1) * 100.0
            # WHICH SESSION this change describes. `_pick_denominator` already decides it:
            # a price that has moved off the stored close belongs to a LATER session (so
            # the change is the current one), while a price still equal to the stored
            # close means the change is the one that close ENDED — `trade_date`.
            #
            # It matters premarket. At 07:00 ET on a Monday the screener still reports
            # Friday's close, so this is FRIDAY's move; without the stamp the widget
            # printed it as "Aerospace & Defense fell 1.2% today", the exact cross-session
            # claim `widget_movers_service.industry_for`'s age gate exists to suppress —
            # and that gate was inert because these rows carried no date at all.
            # ONE derivation, shared with `price_service._from_screener` (which stamps the
            # same key on batch quotes for the widget), so the two can never disagree
            # about which session a change belongs to.
            change_session: Optional[str] = None
            if change_pct is not None:
                # WITH the live price — the one thing that tells the live session from
                # the stored close's. Without it every row carried `trade_date` all day:
                # from the open until the ~20:00 ET close ingest the change was today's
                # move stamped with YESTERDAY's session, so the snapshot told the model
                # to say "on Wed" about Thursday's live tape and the widget's industry
                # attribution (which refuses a stamp older than the batch's) blanked.
                change_session = PriceService._change_session(prev, snap, price)
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
                "changeSession": change_session,
                "sector": r.get("sector"),
                "industry": r.get("industry"),
                "exchange": r.get("exchangeShortName") or r.get("exchange"),
                "isEtf": bool(r.get("isEtf")),
                "isFund": bool(r.get("isFund")),
            }
        if not closes_failed:
            PriceService._report_stale_snapshots(stale, len(out))
            _cache_set(key, out)
        else:
            # A Supabase blip produces a universe whose every `changePercentage` is None —
            # byte-identical to "the snapshot table is empty". Caching it under the normal
            # key would freeze Home movers, the sector cards and Overview's sector rank on
            # a transient for the whole TTL. It is memoised under its OWN short key
            # instead: a herd guard for the outage, retried within seconds of its end.
            _cache_set(f"{key}:degraded", out)
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
        # Which sector each industry sits in. `stock_overview_service` ranks a company's
        # industry WITHIN its sector, and FMP's retired `industry-performance-snapshot`
        # carried `sector` on every row. Dropping it here made that filter match nothing,
        # so "Industry Rank" on the Overview tab was permanently "--". Counted rather than
        # last-write-wins because the taxonomy is not guaranteed 1:1 and a single
        # mislabelled constituent should not reassign the whole industry.
        sectors: Dict[str, Counter] = defaultdict(Counter)
        # Which session each group's members describe — see `changeSession` above. The
        # MODE, because a handful of thinly-traded names can lag the rest of the group.
        sessions: Dict[str, Counter] = defaultdict(Counter)
        for row in universe.values():
            # Companies only. A leveraged ETF's 3x move would swamp the average of the
            # sector it nominally tracks.
            if row.get("isEtf") or row.get("isFund"):
                continue
            name = (row.get(field) or "").strip()
            change = row.get("changePercentage")
            if name and change is not None:
                buckets[name].append(change)
                sess = row.get("changeSession")
                if sess:
                    sessions[name][sess] += 1
                if field == "industry":
                    sec = (row.get("sector") or "").strip()
                    if sec:
                        sectors[name][sec] += 1

        out: List[Dict[str, Any]] = []
        for name, changes in buckets.items():
            if len(changes) < _MIN_GROUP_MEMBERS:
                # Dropped, not published with a caveat: a one-line card has nowhere to
                # show "n=2", so the honest option is absence.
                logger.debug("movers: dropping %s %r — only %d members",
                             field, name, len(changes))
                continue
            entry: Dict[str, Any] = {
                field: name,
                # Equal-weighted, matching what FMP's snapshot returned (`averageChange`),
                # so every downstream consumer reads the same magnitude it always did.
                "changesPercentage": round(sum(changes) / len(changes), 4),
                "constituents": len(changes),
            }
            if sessions[name]:
                # `date` is the key FMP's retired snapshot used, so the age gate in
                # `widget_movers_service.industry_for` reads it unchanged.
                entry["date"] = sessions[name].most_common(1)[0][0]
            if field == "industry" and sectors[name]:
                entry["sector"] = sectors[name].most_common(1)[0][0]
            out.append(entry)
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
