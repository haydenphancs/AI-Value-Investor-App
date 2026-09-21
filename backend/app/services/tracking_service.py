"""
Tracking Service — aggregates watchlist + FMP market data for the Assets tab.

Design (mirrors home_service.py):
- All external calls (FMP, Supabase) run concurrently via asyncio.gather.
- Each section degrades gracefully: if one data source fails, the rest
  still return so the Assets tab always loads.
- Sparkline data is cached per-ticker for 5 minutes.
- Full feed is cached per-user for 30 seconds.
"""

import asyncio
import math
import time as _time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
import logging

from app.integrations.fmp import get_fmp_client, FMPClient
from app.services.chart_helper import (
    FULL_SPAN,
    fetch_chart_data,
    intraday_span,
    sparkline_precision,
    _finite_or_none,
)
from app.services.asset_class import resolve_asset_class, symbol_trades_extended_hours
from app.services._classification_common import is_placeholder_text
from app.utils.postgrest_paging import fetch_all_rows
from app.services.crypto_names import display_name_for_row
from app.database import get_supabase
from app.utils.supabase_errors import retry_idempotent_async
from app.schemas.tracking import (
    TrackedAssetResponse,
    AlertResponse,
    TrackingFeedResponse,
    WhaleTradeItemResponse,
    AnalystRatingItemResponse,
    InsiderTransactionItemResponse,
)
from app.services._insider_common import classify_for_alerts
from app.services._analyst_common import (
    analyst_section_available,
    classify_for_alerts as classify_analyst_for_alerts,
)
from app.services._whale_common import (
    # Was a character-for-character clone below; the roll-up rule lives in ONE place.
    format_amount_short as _format_amount,
    parse_congress_amount_bounds,
    sum_amount_bounds,
    format_amount_range,
)
from app.services._earnings_common import (
    parse_fmp_timing,
    timing_sentence,
    alert_report_time,
)
from app.config import settings
from app.services.asset_class import uses_coingecko_price
from app.services.price_service import price_source

logger = logging.getLogger(__name__)

#: Asset classes whose `company_profile_cache` row can actually carry a `sector`.
#: Everything else (crypto, indices, commodities, FX) has no sector by construction, so
#: leaving it in the backfill's "missing" set re-queries it on every single request.
_CLASSIFIABLE_ASSET_TYPES = {"stock", "etf"}


class WatchlistUnavailableError(Exception):
    """The user's watchlist rows could not be READ from Supabase.

    Deliberately NOT degraded into an empty feed. The iOS Assets tab purges any
    portfolio ticker that is absent from this feed, so an unreadable watchlist
    laundered into a successful empty response makes the client permanently
    delete every ticker — and every hand-entered shares/market_value — from every
    portfolio. Raising lets the endpoint answer 503 WATCHLIST_UNAVAILABLE, which
    the client can tell apart from "you have nothing on your watchlist".
    """


# ── Simple TTL Caches ───────────────────────────────────────────────

_feed_cache: Dict[str, Tuple[float, Any]] = {}
FEED_CACHE_TTL = 30  # 30 seconds per-user

# Hard cap on the number of cached feeds.
#
# The key space became caller-influenced with migration 108: guests are keyed per INSTALL
# (uuid5 of the X-Guest-Id header), so anyone can mint unlimited distinct keys just by varying
# that header. Each entry holds a full TrackingFeedResponse — every asset with quote, sparkline
# floats, and the earnings/whale/analyst/insider alert lists — and entries were only ever
# removed on a READ that found them expired. A key never read again was never freed, so the
# dict grew without bound for the life of the process: a slow OOM on a 512 MB Railway dyno,
# reachable by anyone with curl.
#
# The TTL is what keeps data fresh; this cap is what keeps the process alive.
_FEED_CACHE_MAX_ENTRIES = 500

# Value is the whole drawable series: closes PLUS the (from, to) session span iOS
# positions them with. Cached together because they describe the same bars — a
# cache that held only the closes would let a fresh span be paired with a stale
# series, drawing the line to a time its last bar never reached.
_sparkline_cache: Dict[str, Tuple[float, Tuple[List[float], float, float]]] = {}
# 2 minutes per-ticker. Bars arrive every 5 minutes, so this only ever costs the
# user half a bar of lag — but at the old 300s the line's end could sit a full
# bar behind the 30s-fresh quote beside it, which now READS as staleness because
# the span makes "where the data stops" visible instead of stretching it away.
SPARKLINE_CACHE_TTL = 120

# In-flight dedup on the per-user feed BUILD (CLAUDE.md invariant 4). Two clients on one
# account (phone + iPad, or a scripted pair) hitting the feed inside the same 30 s window
# used to run TWO full fan-outs — every per-ticker sparkline and insider call twice —
# because the cache is only written at the end. Keyed by user; a joiner awaits the
# leader's future and gets the same response (or the same WatchlistUnavailableError).
_feed_inflight: Dict[str, asyncio.Future] = {}

# Per-user WRITE generation, bumped by `invalidate_feed_cache`. A build captures the
# generation it started under and `_feed_cache_set` refuses to pin a result from an
# older one; a joiner that adopted such a leader rebuilds instead of returning it.
#
# Why the pop in `invalidate_feed_cache` alone was not enough: the iOS 30 s price timer
# keeps a build running for as long as the Tracking tab is on screen — including while a
# detail screen is pushed over it — so a star tap's POST/DELETE routinely lands while a
# build that read the PRE-write watchlist is still in flight. That build then re-cached
# the stale list for another 30 s, and the client's post-confirm reconcile read it: a row
# the user had just removed came back, and a just-added one looked like an orphan to the
# client's portfolio purge (the client marker protects the add; nothing protected the
# remove).
_feed_generation: Dict[str, int] = {}
_feed_inflight_generation: Dict[str, int] = {}
# One int per user who ever wrote; bounded so a long-lived process does not keep every
# guest install that ever added a ticker. Evicting a user resets them to generation 0,
# which only matters if a build for that user is in flight at that instant — those are
# skipped.
_FEED_GENERATION_MAX_ENTRIES = 10_000


def _release_feed_inflight(user_id: str, future: "asyncio.Future") -> None:
    """Drop the in-flight entry for *user_id* — but only if it is still OURS.

    Identity-checked as defence in depth: a joiner that takes over after a cancelled
    leader installs its own future, and a leader's late `finally` must never pop a
    successor's entry (that would let a third caller start a duplicate fan-out and lose
    the generation the successor recorded). Today the cancelled leader's `finally` runs
    before the takeover, so the check is not observable through `get_tracking_feed`;
    it is pinned directly.
    """
    if _feed_inflight.get(user_id) is future:
        _feed_inflight.pop(user_id, None)
        _feed_inflight_generation.pop(user_id, None)


class _InflightLeaderCancelled(RuntimeError):
    """The leader's request went away before the build finished (client disconnect).

    `CancelledError` is a BaseException, so it would skip an `except Exception` and leave
    the future unresolved forever — every joiner would hang for the life of the process
    (`project_report_scaling`). The leader sets THIS on the future instead, and a joiner
    that receives it simply becomes the next leader rather than failing its own request
    for someone else's disconnect.
    """


# Per-ticker tier-1 cache for the insider (Form 4) fan-out, mirroring `_sparkline_cache`.
# This pass had NO cache at all: every feed build re-issued `insider-trading/search` for
# every watchlist ticker, 2× per minute per client. Form 4 data moves daily, so 10 min is
# comfortably fresh. Value is the per-ticker roll-up (or None — most tickers have no
# notable trade, and caching the None is where the saving is).
_insider_cache: Dict[str, Tuple[float, Any]] = {}
INSIDER_CACHE_TTL = 600
# Opportunistic sweep threshold — the key space is the union of every user's watchlist,
# which a scripted account can grow; entries are tiny but must not be unbounded.
_INSIDER_CACHE_SWEEP_AT = 5000

# Concurrency of each per-ticker fan-out (sparklines, insider). Unbounded, a 2,000-row
# watchlist launched 2,000 coroutines per pass; bounded, the same request costs the same
# number of calls but cannot open them all at once against FMP's per-minute window.
_PER_TICKER_FANOUT_CONCURRENCY = 16


def _insider_cache_get(ticker: str) -> Tuple[bool, Any]:
    """(hit, value). A cached None is a HIT — that is the common case and the saving."""
    entry = _insider_cache.get(ticker)
    if entry is None:
        return False, None
    ts, value = entry
    if _time.monotonic() - ts > INSIDER_CACHE_TTL:
        del _insider_cache[ticker]
        return False, None
    return True, value


def _insider_cache_set(ticker: str, value: Any) -> None:
    if len(_insider_cache) >= _INSIDER_CACHE_SWEEP_AT:
        now = _time.monotonic()
        for key in [k for k, (ts, _v) in _insider_cache.items() if now - ts > INSIDER_CACHE_TTL]:
            _insider_cache.pop(key, None)
        if len(_insider_cache) >= _INSIDER_CACHE_SWEEP_AT:
            # Still full of live entries: evict oldest-written first, like the feed cache.
            overflow = len(_insider_cache) - _INSIDER_CACHE_SWEEP_AT + 1
            for key in list(_insider_cache.keys())[:overflow]:
                _insider_cache.pop(key, None)
    _insider_cache[ticker] = (_time.monotonic(), value)


def _fanout_tickers(tickers: List[str], user_id: str) -> List[str]:
    """The subset of the watchlist that gets the PER-TICKER enrichment this request.

    Bounds the READ, which a write cap cannot: rows already in production can exceed any
    cap added today. The feed still carries EVERY row (the client purges portfolio tickers
    missing from it); rows past the cap just come back with an empty sparkline and no
    insider alert. Newest-first, since that is the watchlist's own order.
    """
    cap = int(settings.TRACKING_FEED_MAX_TICKERS or 0)
    if cap <= 0 or len(tickers) <= cap:
        return tickers
    logger.warning(
        "[Tracking] user=%s has %d watchlist tickers — per-ticker enrichment capped at %d "
        "(TRACKING_FEED_MAX_TICKERS); the rest render without a sparkline/insider alert",
        user_id, len(tickers), cap,
    )
    return tickers[:cap]


def watchlist_is_full(supabase, user_id: str, ticker: str) -> bool:
    """True when adding `ticker` would push the user past `WATCHLIST_MAX_ITEMS`.

    Shared by BOTH insert paths — `POST /watchlist` and `POST /tracking/holdings` (an
    upsert into the same table) — because a cap on one is bypassable through the other.
    Counts rows OTHER than `ticker`, so a re-add / holdings edit of a ticker already on
    the list is never refused at the cap. Sync (postgrest is sync): call via `to_thread`.

    Fails OPEN with a WARNING: the cap is an abuse bound, not a correctness invariant, and
    refusing every add during a datastore blip would turn a 30 s Supabase hiccup into a
    user-visible "watchlist full". The insert itself still fails loudly if the store is
    really down.
    """
    cap = int(settings.WATCHLIST_MAX_ITEMS or 0)
    if cap <= 0:
        return False
    try:
        res = (
            supabase.table("watchlist_items")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .neq("ticker", ticker)
            .limit(1)
            .execute()
        )
        count = res.count
        if count is None:
            # `count="exact"` unsupported by the client in use → cannot enforce; say so.
            logger.warning(
                "[Watchlist] row-cap check for user=%s got no count from PostgREST — "
                "cap not enforced for this add", user_id,
            )
            return False
        if int(count) >= cap:
            logger.warning(
                "[Watchlist] user=%s is at the watchlist cap (%d rows, cap %d) — refusing %s",
                user_id, count, cap, ticker,
            )
            return True
        return False
    except Exception as e:  # noqa: BLE001 — fail open, loudly
        logger.warning(
            "[Watchlist] row-cap check failed for user=%s (%s: %s) — cap not enforced "
            "for this add", user_id, type(e).__name__, e,
        )
        return False


def _feed_cache_get(user_id: str) -> Optional[TrackingFeedResponse]:
    entry = _feed_cache.get(user_id)
    if entry is None:
        return None
    ts, value = entry
    if _time.monotonic() - ts > FEED_CACHE_TTL:
        del _feed_cache[user_id]
        return None
    return value


def _feed_cache_set(
    user_id: str, value: TrackingFeedResponse, generation: Optional[int] = None
) -> None:
    # A build that started BEFORE a watchlist write must not pin what it read. `None`
    # (direct callers, tests) skips the check.
    if generation is not None and generation != _feed_generation.get(user_id, 0):
        logger.info(
            "[Tracking] feed build for user %s predates a watchlist write (gen %d < %d) "
            "— served, not cached", user_id, generation, _feed_generation.get(user_id, 0),
        )
        return
    # Move-to-end on write so the dict head is the least-recently-written, then evict from the
    # head past the cap. Mirrors `stock_overview_service._cache_set`. Expired entries are also
    # swept opportunistically here, because eviction on read alone never reclaims a key that
    # is never read again — which is every abandoned guest install.
    _feed_cache.pop(user_id, None)
    _feed_cache[user_id] = (_time.monotonic(), value)

    if len(_feed_cache) > _FEED_CACHE_MAX_ENTRIES:
        now = _time.monotonic()
        stale = [k for k, (ts, _v) in _feed_cache.items() if now - ts > FEED_CACHE_TTL]
        for key in stale:
            _feed_cache.pop(key, None)
        # Still over after dropping the expired ones → evict oldest-written first.
        if len(_feed_cache) > _FEED_CACHE_MAX_ENTRIES:
            overflow = len(_feed_cache) - _FEED_CACHE_MAX_ENTRIES
            for key in list(_feed_cache.keys())[:overflow]:
                _feed_cache.pop(key, None)
            logger.warning(
                "[Tracking] feed cache at cap (%d) — evicted %d least-recently-written entries",
                _FEED_CACHE_MAX_ENTRIES, overflow,
            )


def _text_or_none(value: Any, *, iso_code: bool = False) -> Optional[str]:
    """A stored/cached classification string, or None when it is a placeholder.
    ``iso_code=True`` for `country` ("NA" is Namibia, not a placeholder)."""
    if is_placeholder_text(value, iso_code=iso_code):
        return None
    return str(value).strip()


def invalidate_feed_cache(user_id: str) -> None:
    """Drop a user's cached feed after a watchlist/portfolio membership write.

    Load-bearing, not an optimisation. The Assets tab purges any portfolio ticker
    that is missing from this feed, and `addTickerFromSearch` refreshes the feed
    immediately after adding. Without this, that refresh reads the PRE-ADD cached
    response for up to `FEED_CACHE_TTL`, the just-added ticker looks like an
    orphan, and the client deletes it again server-side — the add silently undoes
    itself. The 30s price-refresh timer keeps the entry warm, so this is the
    common path, not a rare race.
    """
    # Bump FIRST and unconditionally: the entry to defeat may not exist yet — it is the
    # build in flight right now, which will try to write after this returns.
    _feed_generation[user_id] = _feed_generation.pop(user_id, 0) + 1   # move-to-end
    if len(_feed_generation) > _FEED_GENERATION_MAX_ENTRIES:
        for stale in list(_feed_generation.keys()):
            if len(_feed_generation) <= _FEED_GENERATION_MAX_ENTRIES:
                break
            if stale == user_id or stale in _feed_inflight:
                continue
            _feed_generation.pop(stale, None)
    if _feed_cache.pop(user_id, None) is not None:
        logger.debug("[Tracking] feed cache invalidated for user %s", user_id)


def _sparkline_cache_key(ticker: str, extended_hours: bool) -> str:
    """Cache key for one ticker's sparkline series.

    MUST include the session window: the same ticker fetched with
    `extended_hours=True` and `False` yields two DIFFERENT series (the regular
    -hours variant is clipped to 09:30–16:00 ET). Keying on the ticker alone lets
    whichever request lands first pin its variant for every other caller.
    """
    return f"{ticker}:{'ext' if extended_hours else 'reg'}"


def _sparkline_cache_get(
    ticker: str, extended_hours: bool = False
) -> Optional[Tuple[List[float], float, float]]:
    key = _sparkline_cache_key(ticker, extended_hours)
    entry = _sparkline_cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if _time.monotonic() - ts > SPARKLINE_CACHE_TTL:
        del _sparkline_cache[key]
        return None
    return value


def _sparkline_cache_set(
    ticker: str,
    value: List[float],
    extended_hours: bool = False,
    span: Tuple[float, float] = FULL_SPAN,
) -> None:
    _sparkline_cache[_sparkline_cache_key(ticker, extended_hours)] = (
        _time.monotonic(), (value, span[0], span[1])
    )


# Re-exported under the module-private name the sparkline builder uses, so both
# sparkline paths share ONE precision rule (see chart_helper.sparkline_precision).
_sparkline_precision = sparkline_precision


def _downsample(values: List[float], target: int) -> List[float]:
    """Evenly downsample to at most *target* points, always keeping the FIRST
    and LAST (the iOS SparklineView colors green/red off values[0] and dots
    values[-1], so the open baseline and end point must survive)."""
    if len(values) <= target:
        return values
    step = (len(values) - 1) / (target - 1)
    idxs = sorted({round(i * step) for i in range(target)} | {0, len(values) - 1})
    return [values[i] for i in idxs]


# ── Service ─────────────────────────────────────────────────────────


class _Rows:
    """A `.data`-shaped wrapper so a paged read fits the retry helper's result contract."""

    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class TrackingService:
    """Builds the enriched tracking feed from Supabase watchlist + FMP data."""

    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()

    async def get_tracking_feed(self, user_id: str) -> TrackingFeedResponse:
        """Return complete tracking feed for the Assets tab.

        Cache → in-flight join → build. The join is what stops two clients on one account
        (or two requests inside the 30 s TTL from one) from each running the full
        per-ticker fan-out; see `_feed_inflight`.
        """
        while True:
            cached = _feed_cache_get(user_id)
            if cached is not None:
                logger.debug("Tracking feed served from cache for user %s", user_id)
                return cached
            leader = _feed_inflight.get(user_id)
            if leader is None:
                break
            leader_generation = _feed_inflight_generation.get(user_id)
            try:
                # `shield`: a joiner that is itself cancelled must not cancel the shared
                # build out from under the leader and every other joiner.
                feed = await asyncio.shield(leader)
            except _InflightLeaderCancelled:
                # The leader's client went away mid-build. Take over rather than fail.
                continue
            if (
                leader_generation is not None
                and leader_generation != _feed_generation.get(user_id, 0)
            ):
                # A watchlist write landed while that build ran, so its result predates
                # it. The leader has not cached it (see `_feed_cache_set`) and has left
                # `_feed_inflight` by now; loop back and build a current one.
                continue
            return feed

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        generation = _feed_generation.get(user_id, 0)
        _feed_inflight[user_id] = future
        _feed_inflight_generation[user_id] = generation
        try:
            feed = await self._build_tracking_feed(user_id, generation=generation)
            if not future.done():
                future.set_result(feed)
            return feed
        except asyncio.CancelledError:
            # BaseException — it skips the handler below. Resolve the joiners with a
            # normal exception they can recover from (they re-loop and rebuild), or they
            # hang for the life of the process.
            if not future.done():
                future.set_exception(_InflightLeaderCancelled("tracking feed leader cancelled"))
                future.exception()  # mark retrieved — see the arm below
            raise
        except BaseException as e:
            if not future.done():
                future.set_exception(e)
                # Mark retrieved (ticker_data_cache.py idiom): the leader re-raises `e`
                # on its own frame, and in the COMMON case — one client, no concurrent
                # request inside the build window — no joiner ever awaits this future,
                # so it was garbage-collected unread and asyncio logged "Future exception
                # was never retrieved" at ERROR (a second Sentry event per failed build).
                # A joiner awaiting `asyncio.shield(future)` still receives the exception.
                future.exception()
            raise
        finally:
            _release_feed_inflight(user_id, future)

    async def _build_tracking_feed(
        self, user_id: str, *, generation: Optional[int] = None
    ) -> TrackingFeedResponse:
        """One uncached build of the feed. Only ever entered via `get_tracking_feed`.

        ``generation`` is the write generation the build started under; the cache write
        at the end is skipped when a watchlist write bumped it meanwhile.
        """

        # 1. Fetch user's watchlist from Supabase
        sb = get_supabase()

        def _read_watchlist():
            # PAGED past PostgREST's ~1,000-row clamp: a truncated read here dropped the
            # user's OLDEST tickers from the feed, and the client's `purgeTickers` then
            # removed them from every group. Paged on the unique id, ordered by
            # `added_at` in Python; wrapped so the retry helper's `.data` contract holds.
            rows = fetch_all_rows(
                lambda: sb.table("watchlist_items").select("*").eq("user_id", user_id),
                order_by="id",
                what=f"tracking feed watchlist user={user_id}",
            )
            rows.sort(key=lambda r: str(r.get("added_at") or ""), reverse=True)
            return _Rows(rows)

        try:
            # Idempotent (a pure read), so a Supabase gateway blip is RETRIED rather
            # than 503'd at the user. Supabase sits behind Cloudflare; a 520/525 edge
            # page made postgrest raise APIError('JSON could not be generated') and
            # this route was the only site where that reached a real person.
            # Via to_thread so the sync postgrest call stops blocking the event loop
            # on a hot request path (CLAUDE.md: never call Supabase synchronously
            # inside an async def).
            result = await retry_idempotent_async(
                _read_watchlist,
                what=f"watchlist read user={user_id}",
                logger=logger,
            )
            watchlist = result.data or []
        except Exception as exc:
            # NO LOGGING HERE — deliberately. This used to logger.exception AND the
            # endpoint re-logged with exc_info=True, so ONE datastore blip opened TWO
            # Sentry issues (`APIError` and `WatchlistUnavailableError`) that always
            # moved in lockstep. api/v1/endpoints/tracking.py is now the single
            # reporter: it is the layer that can tell a transient blip (WARNING) from
            # a genuine bug (ERROR + stack). `raise ... from exc` keeps the postgrest
            # APIError as __cause__, so the endpoint's exc_info still prints the
            # original traceback and the shared classifier can still read its .code.
            #
            # Still RAISES — never degrade a failed READ into an empty feed. The two
            # are indistinguishable on the wire, and the iOS Assets tab purges every
            # portfolio ticker missing from this feed, so a laundered read failure
            # permanently deletes the user's portfolios (tickers AND hand-entered
            # shares/market_value). The endpoint maps this to 503
            # WATCHLIST_UNAVAILABLE so the client can tell the two apart.
            raise WatchlistUnavailableError(
                f"watchlist read failed for user {user_id}: {type(exc).__name__}: {exc}"
            ) from exc

        if not watchlist:
            return TrackingFeedResponse()

        tickers = [item["ticker"] for item in watchlist]
        # Ticker → asset_type so the sparkline fetch can keep 24/7 assets (crypto,
        # continuously-quoted commodity futures) on their full intraday series
        # instead of clipping them to the US equity session. The stored column is
        # only a hint — it defaults to 'Stock' and `POST /api/v1/watchlist` (the
        # path the iOS add flow uses) never writes it — so `resolve_asset_class`
        # falls back to symbol detection. See services/asset_class.py.
        asset_types = {
            item["ticker"]: str(item.get("asset_type") or "").lower()
            for item in watchlist
            if item.get("ticker")
        }

        # 1b. Heal rows whose classification was never written.
        #
        # `sector`/`country` live on the watchlist row, but for most of this table's
        # life the only writers were `POST /tracking/holdings` and
        # `PortfolioInsightsService._enrich_missing` — and the latter only ever sees
        # tickers that already carry shares/market_value. A ticker added from the
        # watchlist star was therefore never classified by anything and reported
        # `"sector": null` forever. `POST /api/v1/watchlist` now persists it going
        # forward; this backfills the rows already stored NULL, so the fix does not
        # need a migration and does not wait for the user to re-add anything.
        #
        # Read from the SHARED `company_profile_cache` for the same reason
        # `widget_movers_service._industries` does: it is ticker-keyed, already warm,
        # and gives every user the same answer for the same stock.
        await self._backfill_classification(user_id, watchlist)

        # 2. Fetch data concurrently.
        #
        # Quotes, earnings and whale trades are ONE upstream read each whatever the list
        # size; sparklines and insider alerts are one FMP call PER TICKER, so those two
        # take the capped subset (`TRACKING_FEED_MAX_TICKERS`). Every row is still in the
        # feed either way — see `_fanout_tickers`.
        fanout = _fanout_tickers(tickers, user_id)
        quotes_task = self._get_batch_quotes(tickers)
        sparklines_task = self._get_all_sparklines(fanout, asset_types)
        earnings_task = self._get_earnings_alerts(tickers)
        whale_task = self._get_whale_trade_alerts(tickers)
        analyst_task = self._get_analyst_rating_alerts(tickers)
        insider_task = self._get_insider_transaction_alerts(fanout, asset_types)

        results = await asyncio.gather(
            quotes_task,
            sparklines_task,
            earnings_task,
            whale_task,
            analyst_task,
            insider_task,
            return_exceptions=True,
        )

        quotes_map: Dict[str, Dict] = (
            results[0] if not isinstance(results[0], BaseException) else {}
        )
        sparklines_map: Dict[str, Tuple[List[float], float, float]] = (
            results[1] if not isinstance(results[1], BaseException) else {}
        )
        earnings_alerts: List[AlertResponse] = (
            results[2] if not isinstance(results[2], BaseException) else []
        )
        whale_alerts: List[AlertResponse] = (
            results[3] if not isinstance(results[3], BaseException) else []
        )
        analyst_alerts: List[AlertResponse] = (
            results[4] if not isinstance(results[4], BaseException) else []
        )
        insider_alerts: List[AlertResponse] = (
            results[5] if not isinstance(results[5], BaseException) else []
        )

        section_names = [
            "batch_quotes",
            "sparklines",
            "earnings_alerts",
            "whale_trade_alerts",
            "analyst_rating_alerts",
            "insider_transaction_alerts",
        ]
        for idx, res in enumerate(results):
            if isinstance(res, BaseException):
                logger.error("[Tracking] %s failed: %s", section_names[idx], res)

        alerts: List[AlertResponse] = (
            earnings_alerts + whale_alerts + analyst_alerts + insider_alerts
        )

        # 3. Merge watchlist + quotes + sparklines into TrackedAssetResponse
        assets: List[TrackedAssetResponse] = []
        for item in watchlist:
            ticker = item.get("ticker", "")
            if not ticker:
                logger.warning("[Tracking] Skipping watchlist item with no ticker: %s", item)
                continue
            try:
                quote = quotes_map.get(ticker, {})
                # A missing entry (the whole sparkline gather failed) degrades to
                # no series at full span — the same thing the card drew before
                # spans existed, rather than a zero-width line.
                sparkline, spark_from, spark_to = sparklines_map.get(
                    ticker, ([], *FULL_SPAN)
                )

                # EVERY numeric goes through `_finite_or_none`. A bare float() is
                # the trap here: `float("nan")` is TRUTHY so a NaN survives the
                # `or` fallbacks below, `round(nan, 2)` doesn't raise so the
                # per-row `except` never fires, and Pydantic accepts it on the
                # REQUIRED `price`/`change_percent` fields. Starlette then renders
                # with allow_nan=False and 500s the WHOLE feed — one bad cell on
                # one ticker blanks the entire Assets tab, and the poisoned object
                # is already in `_feed_cache`, so it re-500s for the full TTL.
                # (Same guard commodity_service applies to this exact payload.)
                #
                # FMP spells this `changePercentage` for equities but
                # `changesPercentage` (plural) for crypto / indices /
                # commodities on the SAME /quote path — read both or those
                # non-stock rows always report a flat +0.00%. Mirrors the
                # defensive read in stock_overview_service / index_service.
                change_pct = _finite_or_none(quote.get("changePercentage"))
                if change_pct is None:
                    change_pct = _finite_or_none(quote.get("changesPercentage"))
                price_f = _finite_or_none(quote.get("price"))
                prev_close_f = _finite_or_none(quote.get("previousClose"))
                market_cap_f = _finite_or_none(quote.get("marketCap"))

                if quote and (price_f is None or change_pct is None):
                    # Never silent: a quote that arrived but carried an unusable
                    # number is exactly the case that used to take the tab down.
                    logger.warning(
                        "[Tracking] %s: dropped non-finite/missing quote field(s) "
                        "(price=%r change=%r) — row degrades instead of 500ing the feed",
                        ticker, quote.get("price"), quote.get("changePercentage"),
                    )

                price_known = price_f is not None
                change_known = change_pct is not None
                price = price_f if price_f is not None else 0
                # `+ 0.0` collapses signed zero: round(-0.001, 2) is -0.0, and on
                # iOS `-0.0 >= 0` is true, so the row would show a green up-arrow
                # while formatting the value as "-0.00". Same normalization the
                # movers scanner applies for the same reason.
                change_pct = (change_pct if change_pct is not None else 0.0) + 0.0

                # Holding info — these columns live on watchlist_items and
                # are populated by the Portfolio Insights config sheet. iOS
                # uses them to pre-fill the inputs and decide which rows
                # count toward the diversification score.
                shares = item.get("shares")
                stored_value = item.get("market_value")

                assets.append(
                    TrackedAssetResponse(
                        ticker=ticker,
                        company_name=display_name_for_row(
                            ticker, item.get("company_name") or quote.get("name"),
                        ),
                        price=round(float(price), 2),
                        change_percent=round(float(change_pct), 2) + 0.0,
                        price_known=price_known,
                        change_known=change_known,
                        previous_close=round(prev_close_f, 2) if prev_close_f else None,
                        sparkline_data=sparkline,
                        spark_from=spark_from,
                        spark_to=spark_to,
                        logo_url=item.get("logo_url"),
                        # Sector/country live on the watchlist row (seeded on
                        # holdings-add and lazy-enriched by PortfolioInsights).
                        # The FMP *quote* endpoint doesn't return these, so the
                        # old `quote.get(...)` fallback was always null.
                        # Normalised on READ as well: rows that already hold a placeholder
                        # ("N/A") stay that way until migration 172 nulls them, and the
                        # Assets rows must not show it meanwhile.
                        sector=_text_or_none(item.get("sector")),
                        country=item.get("country"),
                        market_cap=market_cap_f if market_cap_f else None,
                        shares=_finite_or_none(shares),
                        market_value=_finite_or_none(stored_value),
                        # RESOLVED, not the raw column: the 'Stock' default was published
                        # verbatim for every coin/ETF row (see home_dashboard_service).
                        asset_type=resolve_asset_class(ticker, item.get("asset_type")),
                    )
                )
            except Exception as exc:
                logger.error("[Tracking] Failed to enrich ticker %s: %s", ticker, exc)
                # Still include the asset with minimal data so it shows in the list
                assets.append(
                    TrackedAssetResponse(
                        ticker=ticker,
                        company_name=item.get("company_name") or ticker,
                        price_known=False,
                        change_known=False,
                        # The class is pure symbol/column logic and cannot be what failed
                        # above. Without it this row went out as `asset_type: null`, iOS
                        # defaulted it to "stock", and a stored 'etf'/'index'/'commodity'
                        # row whose enrichment threw was pushed to the EQUITY screen on
                        # that refresh — the E3 DOGE routing bug through a side door.
                        asset_type=resolve_asset_class(ticker, item.get("asset_type")),
                    )
                )

        feed = TrackingFeedResponse(assets=assets, alerts=alerts)
        # Don't PIN a fully-degraded feed. If the quote fan-out resolved nothing at
        # all, every row carries a placeholder price; caching that for the full TTL
        # makes one transient FMP blip look like a 30-second outage on the tab and
        # suppresses the retry the 30s client timer would otherwise perform.
        # Mirrors get_scanners' "empty, uncached → retries" posture.
        if quotes_map or not tickers:
            _feed_cache_set(user_id, feed, generation=generation)
        else:
            logger.warning(
                "[Tracking] all %d quotes unresolved for user %s — serving degraded "
                "feed UNCACHED so the next request retries",
                len(tickers), user_id,
            )
        return feed

    # ── Classification backfill ─────────────────────────────────────

    async def _backfill_classification(
        self, user_id: str, watchlist: List[Dict[str, Any]]
    ) -> None:
        """Fill missing `sector`/`country` on watchlist rows from the shared profile cache.

        MUTATES `watchlist` in place so this request already serves the healed values,
        and writes them back so the next request does not have to look again.

        Best-effort by design: this is cosmetic enrichment on a hot read path, so every
        failure degrades to "leave it null" and is logged rather than raised. A tracking
        feed that renders without a sector badge is fine; a 500 because a cache lookup
        blipped is not.
        """
        # EQUITIES ONLY. A coin, an index or a commodity has no `sector` in
        # `company_profile_cache` and never will, so an unfiltered list kept every one of
        # them permanently "missing": the same symbols were looked up on EVERY tracking
        # request, forever, and every lookup came back empty. `resolve_asset_class` is the
        # same classifier the rest of this file already uses, so a row's stored
        # `asset_type` is honoured and a ticker-shape fallback covers a row written before
        # the column existed.
        #
        # ETFs are included deliberately: FMP's profile DOES carry a sector for many of
        # them, and an ETF that has none simply stays unclassified as before.
        missing = [
            item for item in watchlist
            if item.get("ticker")
            and not item.get("sector")
            and resolve_asset_class(
                str(item["ticker"]), item.get("asset_type")
            ).lower() in _CLASSIFIABLE_ASSET_TYPES
        ]
        if not missing:
            return

        syms = sorted({str(item["ticker"]).upper() for item in missing})

        def _read_profiles() -> Dict[str, Dict[str, Any]]:
            res = (
                get_supabase()
                .table("company_profile_cache")
                .select("ticker, profile_json")
                .in_("ticker", syms)
                .execute()
            )
            return {
                str(r["ticker"]).upper(): (r.get("profile_json") or {})
                for r in (res.data or [])
                if r.get("ticker")
            }

        try:
            profiles = await asyncio.to_thread(_read_profiles)
        except Exception as exc:
            logger.warning(
                "[Tracking] sector backfill: profile cache read failed for %d ticker(s) "
                "(%s: %s) — serving rows unclassified",
                len(syms), type(exc).__name__, exc,
            )
            return

        healed: Dict[str, Dict[str, Any]] = {}
        for item in missing:
            prof = profiles.get(str(item["ticker"]).upper())
            if not prof:
                continue
            # Placeholder-aware, not just falsiness: `profile_json` has TWO writers —
            # `stock_overview_service` stores its formatted dict, whose empty sector is the
            # literal "N/A", and `whale_service` the raw FMP shape. "N/A" used to pass the
            # old `or ""` test, land in `watchlist_items.sector`, and (because both healers
            # test falsiness) could never be re-healed — it then rendered as a legend row
            # named "N/A" on the Diversification card.
            sector = _text_or_none(prof.get("sector"))
            if not sector:
                continue
            patch: Dict[str, Any] = {"sector": sector}
            # Only fill country when it is genuinely absent — the column has a 'US'
            # default, so an existing value is real data and must not be overwritten.
            if not item.get("country"):
                country = _text_or_none(prof.get("country"), iso_code=True)
                if country:
                    patch["country"] = country
            item.update(patch)
            healed[str(item["ticker"])] = patch

        if not healed:
            logger.info(
                "[Tracking] sector backfill: no cached profile for %d ticker(s): %s",
                len(syms), ", ".join(syms[:10]),
            )
            return

        def _persist() -> None:
            sb = get_supabase()
            for ticker, patch in healed.items():
                sb.table("watchlist_items").update(patch).eq(
                    "user_id", user_id
                ).eq("ticker", ticker).execute()

        try:
            await asyncio.to_thread(_persist)
            logger.info(
                "[Tracking] sector backfill: healed %d watchlist row(s): %s",
                len(healed), ", ".join(sorted(healed)[:10]),
            )
        except Exception as exc:
            # Non-fatal: the in-memory patch above still serves this request correctly,
            # we just pay the lookup again next time. Logged so a permanently failing
            # write (e.g. a permissions regression) is visible rather than silent.
            logger.warning(
                "[Tracking] sector backfill: write-back failed for %d row(s) (%s: %s)",
                len(healed), type(exc).__name__, exc,
            )

    # ── Batch Quotes ────────────────────────────────────────────────

    async def _get_batch_quotes(
        self, tickers: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch real-time quotes for all tickers in a single FMP call."""
        try:
            quotes = await price_source(self).get_quotes_list(tickers)
            return {q["symbol"]: q for q in quotes if q.get("symbol")}
        except Exception as exc:
            logger.warning("Batch quotes failed: %s", exc)
            return {}

    # ── Sparklines ──────────────────────────────────────────────────

    async def _get_all_sparklines(
        self, tickers: List[str], asset_types: Optional[Dict[str, str]] = None
    ) -> Dict[str, Tuple[List[float], float, float]]:
        """Fetch sparkline data for all tickers concurrently.

        Each value is ``(closes, span_from, span_to)`` — the series plus where it
        sits inside that asset's session, so the card can leave the un-traded rest
        of the day blank instead of stretching the morning across the whole tile.

        ``asset_types`` maps ticker → the STORED ``asset_type`` column, used only
        as a hint: it defaults to ``'Stock'`` and ``POST /api/v1/watchlist`` (the
        path the iOS add-ticker flow uses) never writes it, so a Bitcoin row
        claims to be an equity. ``resolve_asset_class`` therefore falls back to
        symbol detection, and any 24/7 asset (crypto + continuously-quoted
        commodity futures) keeps its full intraday series instead of being
        clipped to the US equity session — which would fold most of its day away
        and diverge from the detail 1D chart one tap later.
        """
        asset_types = asset_types or {}
        # Bounded fan-out: the cache check stays outside the gate (free), only the
        # upstream fetch takes a slot.
        gate = asyncio.Semaphore(_PER_TICKER_FANOUT_CONCURRENCY)

        async def _fetch_one(ticker: str) -> Tuple[str, List[float], float, float]:
            # Resolve the session window FIRST — it is part of the cache identity
            # (the same ticker yields a different series under each window).
            extended_hours = symbol_trades_extended_hours(
                ticker, asset_types.get(ticker)
            )

            cached = _sparkline_cache_get(ticker, extended_hours)
            if cached is not None:
                return (ticker, *cached)

            async with gate:
                return await _fetch_uncached(ticker, extended_hours)

        async def _fetch_uncached(
            ticker: str, extended_hours: bool
        ) -> Tuple[str, List[float], float, float]:
            try:
                # Use the SAME series the TickerDetailView 1D chart draws:
                # 5-min intraday bars, oldest-first, via the shared chart_helper,
                # with the SAME session window that asset's detail chart uses
                # (crypto_service and commodity_service both pass
                # extended_hours=True). This keeps the holdings-card sparkline
                # consistent with the chart the user sees when they open the
                # ticker — the old path drew a ~1-month daily-EOD line, which
                # looked nothing like the 1D chart.
                # ── Source gate ──────────────────────────────────────────
                # FMP 402s every crypto pair, so this raised
                # FMPNotEntitledException for a coin and the row fell to the
                # except below — an empty sparkline beside a live price. The FMP
                # branch is preserved and reachable via `CRYPTO_PRICE_SOURCE=fmp`.
                #
                # `market_chart?days=1` is the 5-minute series, the same one the
                # crypto detail 1D chart now draws, so the card and the chart one
                # tap away agree. The adapter emits ET wall-clock timestamps,
                # which is what the `last_day` prefix match below assumes.
                if (
                    uses_coingecko_price(ticker)
                    and str(settings.CRYPTO_PRICE_SOURCE or "").lower() != "fmp"
                ):
                    from app.services.crypto_service import get_crypto_service
                    from app.services.coingecko_adapter import crypto_base_symbol

                    bars = await get_crypto_service()._cg_history(
                        crypto_base_symbol(ticker), 1, intraday=True
                    )
                else:
                    bars = await fetch_chart_data(
                        self.fmp, ticker, "1D", extended_hours=extended_hours
                    )
                if not bars:
                    # Honest empty — never fabricate. iOS SparklineView draws
                    # nothing for an empty/1-point series.
                    _sparkline_cache_set(ticker, [], extended_hours)
                    return (ticker, [], *FULL_SPAN)

                # Keep only the most recent trading day — mirrors the iOS
                # TradingDayHelper.filterToLatestDay step, so the multi-day
                # warm-up bars don't fold several sessions into one mini-chart.
                last_day = str(bars[-1].get("date", ""))[:10]  # "YYYY-MM-DD"
                day_bars = [
                    b for b in bars if str(b.get("date", "")).startswith(last_day)
                ]

                # `_finite_or_none` (not bare float()): chart_helper already drops
                # non-finite closes on the intraday path, but this list feeds a
                # REQUIRED `List[float]` that Starlette renders with
                # allow_nan=False — keep the guard local so a future chart_helper
                # branch can't silently reopen the hole.
                #
                # Bars and closes are kept in LOCKSTEP so the span below is
                # computed from the rows that actually survived. Deriving it from
                # `day_bars` instead would let a dropped final bar push the line's
                # end past the last price it really has.
                usable: List[Dict[str, Any]] = []
                closes: List[float] = []
                for b in day_bars:
                    c = _finite_or_none(b.get("close"))
                    if c is not None and c > 0:
                        usable.append(b)
                        closes.append(c)
                if len(closes) < 2:
                    _sparkline_cache_set(ticker, [], extended_hours)
                    return (ticker, [], *FULL_SPAN)

                # Where these bars sit inside their own session, as (from, to)
                # fractions. Without it iOS spreads the series across the FULL tile
                # width, so a 10:15 chart is pixel-identical to a closed day.
                span = intraday_span(usable, extended_hours=extended_hours)

                # ~78 five-min bars per session → downsample so the card payload
                # stays small and the tiny chart reads cleanly. Precision scales to
                # the series' own magnitude: a flat round(c, 2) collapses a $0.20
                # holding to 1–4 distinct levels, drawing a dead-flat line next to
                # a live "+0.39%".
                sampled = _downsample(closes, 30)
                digits = _sparkline_precision(sampled)
                sparkline = [round(c, digits) for c in sampled]
                _sparkline_cache_set(ticker, sparkline, extended_hours, span)
                return (ticker, sparkline, *span)
            except Exception as exc:
                logger.warning(
                    "Sparkline (1D intraday) for %s failed: %s: %s",
                    ticker, type(exc).__name__, exc,
                )
                return (ticker, [], *FULL_SPAN)

        results = await asyncio.gather(*[_fetch_one(t) for t in tickers])
        return {t: (series, lo, hi) for t, series, lo, hi in results}

    # ── Earnings Alerts ─────────────────────────────────────────────

    async def _get_earnings_alerts(
        self, watchlist_tickers: List[str]
    ) -> List[AlertResponse]:
        """Fetch upcoming earnings from FMP, filtered to user's watchlist."""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
            calendar = await self.fmp.get_earnings_calendar(
                from_date=today, to_date=future
            )
            if not calendar:
                return []

            ticker_set = {t.upper() for t in watchlist_tickers}
            alerts: List[AlertResponse] = []

            for entry in calendar:
                symbol = (entry.get("symbol") or "").upper()
                if symbol not in ticker_set:
                    continue

                # Parse date for day/month
                date_str = entry.get("date", "")
                day = None
                month = None
                if date_str:
                    try:
                        dt = datetime.strptime(date_str, "%Y-%m-%d")
                        day = dt.day
                        month = dt.strftime("%b").upper()
                    except ValueError:
                        pass

                # Determine report time (shared parser keeps this in sync
                # with the Financials Earnings section's next_earnings_date).
                timing_token = parse_fmp_timing(entry.get("time"))
                report_time = alert_report_time(timing_token)

                # Consensus numbers — emitted as structured fields so the
                # iOS detail view shows "EPS Est: $X | Rev Est: $YB" in the
                # Consensus row without repeating the sentence.
                # Same NaN trap as the insider bucket below: `float("nan")` does not
                # raise, so the try/except lets it through into `AlertResponse`, and the
                # feed 500s at serialization. `_finite_or_none` returns None for
                # NaN/Inf/non-numeric alike, and both fields are Optional on the wire, so
                # an unknown consensus renders as absent rather than as a fabricated 0.
                eps_est = _finite_or_none(entry.get("epsEstimated"))
                rev_est = _finite_or_none(entry.get("revenueEstimated"))

                # One-line description for the card. iOS rebuilds its own
                # version for the alert card, but keep a sane fallback here.
                date_phrase = f"on {month} {day}" if day and month else ""
                sentence = timing_sentence(timing_token)
                pieces = [f"{symbol} reports earnings"]
                if date_phrase:
                    pieces.append(date_phrase)
                if sentence:
                    pieces.append(sentence)
                full_desc = " ".join(pieces) + "."

                alerts.append(
                    AlertResponse(
                        type="earnings",
                        ticker=symbol,
                        company_name=entry.get("companyName") or symbol,
                        title="Earnings Alert",
                        description=full_desc,
                        day=day,
                        month=month,
                        report_time=report_time,
                        eps_estimate=eps_est,
                        revenue_estimate=rev_est,
                    )
                )

            return alerts

        except Exception as exc:
            logger.warning("Earnings alerts failed: %s", exc)
            return []

    # ── Whale Trade Alerts ──────────────────────────────────────────

    async def _get_whale_trade_alerts(
        self, watchlist_tickers: List[str]
    ) -> List[AlertResponse]:
        """Aggregate recent whale trades on watchlist tickers.

        Returns at most two rolled-up alerts: one "Whales Bought" and one
        "Whales Sold", each carrying a per-ticker breakdown in
        `whale_trade_items`.
        """
        if not watchlist_tickers:
            return []

        ticker_list = [t.upper() for t in watchlist_tickers]
        cutoff_iso = (datetime.now() - timedelta(days=7)).isoformat()
        # Same 7-day window as a DATE string, to gate 13F rows on their own
        # trade/filing date (see the backfill guard in the bucket loop).
        cutoff_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        sb = get_supabase()
        try:
            # OFF THE LOOP. This sits inside the tracking feed's `asyncio.gather`, so a
            # blocking PostgREST round trip here does not just stall this coroutine — it
            # stalls every sibling in the fan-out AND every other request on the single
            # uvicorn worker, which is the opposite of what gathering them is for.
            result = await asyncio.to_thread(
                lambda: sb.table("whale_trades")
                .select("ticker, company_name, action, amount, amount_range, date, created_at, whale_id, whales(name, avatar_url, firm_name)")
                .in_("ticker", ticker_list)
                .gte("created_at", cutoff_iso)
                .order("created_at", desc=True)
                .limit(500)
                .execute()
            )
            rows = result.data or []
        except Exception as exc:
            logger.warning("[Tracking] whale_trades query failed: %s", exc)
            return []

        # First pass: bucket by (ticker, action, is_congress) — each bucket
        # becomes one item. Institutional (13F) and congressional trades on the
        # SAME ticker/action are kept in SEPARATE buckets so a precise 13F
        # figure is never flipped into a fuzzy STOCK-Act range (and mislabeled
        # is_congress) just because a congressperson also traded it that week.
        # Each row contributes (low, high) dollar bounds: for congress trades the
        # STOCK Act range; for institutional trades an exact point (low == high).
        # Summing the bounds yields an honest range for congress and collapses to
        # a single figure when everything is exact (13F only).
        buckets: Dict[Tuple[str, str, bool], Dict[str, Any]] = {}
        for row in rows:
            ticker = (row.get("ticker") or "").upper()
            action = (row.get("action") or "").upper()
            if not ticker or action not in ("BOUGHT", "SOLD"):
                continue
            is_congress_row = bool(row.get("amount_range"))
            # BACKFILL GUARD (13F only): the query windows on created_at, but a
            # newly added whale's FIRST hydration inserts months-old filings
            # with created_at=now — without this, the "this week" alert would
            # present May filings as this week's activity. A 13F row's `date`
            # IS the filing date, so require it inside the same 7-day window.
            # Congress rows (amount_range set) keep the created_at window:
            # their `date` is the TRANSACTION date, which legitimately lags
            # the disclosure that makes the trade newsworthy. Missing/blank
            # date → keep (degrade to the old created_at-only behavior).
            if not is_congress_row:
                trade_date = str(row.get("date") or "")[:10]
                if trade_date and trade_date < cutoff_date:
                    continue
            key = (ticker, action, is_congress_row)
            bucket = buckets.setdefault(
                key,
                {
                    "company_name": row.get("company_name") or ticker,
                    "total_amount": 0.0,
                    "bounds": [],
                    "has_congress": False,
                    "whale_ids": set(),
                    "lead_whale_id": None,
                    "lead_whale_name": None,
                    "lead_whale_avatar": None,
                    "lead_whale_firm": None,
                },
            )
            try:
                amt = float(row.get("amount") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            bucket["total_amount"] += amt
            amount_range = row.get("amount_range")
            if amount_range:
                # Congress row: honest STOCK Act range (explicit flag — do NOT
                # infer congress-ness from bound spread, since a malformed or
                # single-value bucket collapses to low==high and would leak a
                # precise dollar).
                bucket["has_congress"] = True
                bucket["bounds"].append(
                    parse_congress_amount_bounds(amount_range)
                )
            else:
                bucket["bounds"].append((amt, amt))
            whale_id = row.get("whale_id")
            if whale_id:
                bucket["whale_ids"].add(whale_id)
            if bucket["lead_whale_name"] is None:
                whale = row.get("whales") or {}
                if isinstance(whale, dict):
                    bucket["lead_whale_id"] = whale_id
                    bucket["lead_whale_name"] = whale.get("name")
                    bucket["lead_whale_avatar"] = whale.get("avatar_url")
                    bucket["lead_whale_firm"] = (
                        (whale.get("firm_name") or "").strip() or None
                    )

        # Second pass: group items by action → one rolled-up alert per action.
        action_groups: Dict[str, List[WhaleTradeItemResponse]] = {
            "bought": [],
            "sold": [],
        }
        action_totals: Dict[str, float] = {"bought": 0.0, "sold": 0.0}
        action_bounds: Dict[str, List[Tuple[float, Optional[float]]]] = {
            "bought": [],
            "sold": [],
        }
        action_has_congress: Dict[str, bool] = {"bought": False, "sold": False}
        for (ticker, action, _is_congress_key), bucket in buckets.items():
            whale_count = len(bucket["whale_ids"])
            if whale_count == 0:
                continue
            action_word = "bought" if action == "BOUGHT" else "sold"
            is_congress = bucket["has_congress"]
            amount_label = _format_amount_or_range(
                bucket["bounds"], bucket["total_amount"], is_congress
            )
            # Per-item summed bounds so iOS can re-aggregate an honest RANGE after
            # trimming to the active portfolio (13F: low == high == exact).
            item_low, item_high = sum_amount_bounds(bucket["bounds"])
            action_groups[action_word].append(
                WhaleTradeItemResponse(
                    ticker=ticker,
                    company_name=bucket["company_name"],
                    whale_count=whale_count,
                    amount=amount_label,
                    raw_amount=bucket["total_amount"],
                    raw_amount_low=item_low,
                    raw_amount_high=item_high,
                    is_congress=is_congress,
                    lead_whale_id=bucket["lead_whale_id"],
                    lead_whale_name=bucket["lead_whale_name"],
                    lead_whale_avatar_name=bucket["lead_whale_avatar"],
                    lead_whale_firm=bucket["lead_whale_firm"],
                )
            )
            action_totals[action_word] += bucket["total_amount"]
            action_bounds[action_word].extend(bucket["bounds"])
            if is_congress:
                action_has_congress[action_word] = True

        alerts: List[AlertResponse] = []
        for action_word in ("bought", "sold"):
            items = action_groups[action_word]
            if not items:
                continue
            # Largest position first (by midpoint magnitude, not the label)
            items.sort(key=lambda it: it.raw_amount, reverse=True)
            total_label = _format_amount_or_range(
                action_bounds[action_word],
                action_totals[action_word],
                action_has_congress[action_word],
            )
            title = "Whales Bought" if action_word == "bought" else "Whales Sold"
            # Dedup tickers for the sentence — a ticker can now appear as two
            # items (a congress bucket + a 13F bucket) in the same action group.
            desc_tickers = list(dict.fromkeys(it.ticker for it in items))
            description = (
                f"{_join_tickers(desc_tickers)} this week"
                f" — totaling {total_label}."
            )
            alerts.append(
                AlertResponse(
                    type="whale_trade",
                    title=title,
                    description=description,
                    action=action_word,
                    total_amount=total_label,
                    time_window_label="this week",
                    whale_trade_items=items,
                )
            )

        return alerts

    # ── Analyst Rating Alerts ───────────────────────────────────────

    async def _get_analyst_rating_alerts(
        self, watchlist_tickers: List[str]
    ) -> List[AlertResponse]:
        """Roll all recent analyst grade changes into a single alert.

        Gated on the entitlement manifest. `grades` is outside the signed FMP Order Form
        and answers 402, so this used to fan out one guaranteed-to-fail call PER WATCHLIST
        TICKER on every refresh, each one caught and logged as a warning — a 50-ticker
        watchlist produced 50 log lines and 50 wasted coroutines to arrive at `[]`.
        Skipping is not a behaviour change for the user (the alert could never fire), it
        just makes the skip deliberate and quiet. Flips back on by itself if the package is
        ever purchased.
        """
        if not watchlist_tickers:
            return []

        if not analyst_section_available():
            logger.debug(
                "tracking: analyst rating alerts skipped for %d ticker(s) — the grades "
                "endpoint is outside the FMP licence", len(watchlist_tickers),
            )
            return []

        cutoff = datetime.now() - timedelta(days=14)

        async def _fetch_one(ticker: str) -> List[AnalystRatingItemResponse]:
            """Return EVERY material grade change on this ticker within the
            window (deduped to one per firm), not just the first — otherwise a
            ticker with two firm actions surfaces only one and the rolled-up
            'N rating changes' count silently under-reports."""
            try:
                grades = await self.fmp.get_grades(ticker, limit=20)
            except Exception as exc:
                logger.warning("Analyst grades for %s failed: %s", ticker, exc)
                return []
            if not isinstance(grades, list) or not grades:
                return []

            matches: List[AnalystRatingItemResponse] = []
            seen_firms: set = set()
            for entry in grades:
                date_str = entry.get("publishedDate") or entry.get("date") or ""
                dt = _parse_date(date_str)
                if dt is None or dt < cutoff:
                    continue

                firm = (
                    entry.get("gradingCompany")
                    or entry.get("analystCompany")
                    or entry.get("newsPublisher")
                    or "Analyst"
                )
                new_rating = entry.get("newGrade") or ""
                previous_rating = entry.get("previousGrade") or None

                # Shared normalizer keeps this in lockstep with the
                # Analysis tab's Actions screen — same row will be
                # classified the same way in both views.
                rating_action, material = classify_analyst_for_alerts(
                    entry.get("action"), previous_rating, new_rating
                )
                if not material:
                    # Maintain / reiterate — firm didn't change its view.
                    # Keep scanning for a material action within the window.
                    continue

                # One entry per firm — grades come newest-first, so the first
                # material row from a firm is its latest action. Guards against
                # a firm double-counting when it appears twice in the window.
                firm_key = firm.strip().lower()
                if firm_key in seen_firms:
                    continue
                seen_firms.add(firm_key)

                matches.append(
                    AnalystRatingItemResponse(
                        ticker=ticker.upper(),
                        firm_name=firm,
                        rating_action=rating_action,
                        new_rating=new_rating,
                        previous_rating=previous_rating,
                        price_target=_opt_float(entry.get("priceTarget")),
                        previous_price_target=_opt_float(
                            entry.get("previousPriceTarget")
                        ),
                        day=dt.day,
                        month=dt.strftime("%b").upper(),
                    )
                )
                # Bound the card: a ticker rarely has >5 material changes in
                # two weeks and the alert only shows a handful.
                if len(matches) >= 5:
                    break
            return matches

        results = await asyncio.gather(
            *[_fetch_one(t) for t in watchlist_tickers], return_exceptions=True
        )
        items: List[AnalystRatingItemResponse] = [
            it for r in results if isinstance(r, list) for it in r
        ]
        if not items:
            return []

        # Most notable first: upgrades/downgrades above reiterations
        rank = {"upgrade": 0, "downgrade": 1, "initiate": 2, "reiterate": 3}
        items.sort(key=lambda it: rank.get(it.rating_action, 4))

        # Dedup tickers for the sentence (a single ticker can now carry several
        # firm changes) while the count reflects the true number of changes.
        unique_tickers = list(dict.fromkeys(it.ticker for it in items))
        count_label = (
            "1 rating change" if len(items) == 1 else f"{len(items)} rating changes"
        )
        description = (
            f"{count_label} on {_join_tickers(unique_tickers)} this week."
        )
        return [
            AlertResponse(
                type="analyst_rating",
                title="Analyst Ratings",
                description=description,
                time_window_label="this week",
                analyst_rating_items=items,
            )
        ]

    # ── Insider Transaction Alerts ──────────────────────────────────

    async def _get_insider_transaction_alerts(
        self,
        watchlist_tickers: List[str],
        asset_types: Optional[Dict[str, str]] = None,
    ) -> List[AlertResponse]:
        """Roll up recent notable insider (Form 4) transactions into at most
        two alerts: one "Insider Bought" and one "Insider Sold".

        One `insider-trading/search` call per EQUITY ticker, through a 10-min per-ticker
        cache and a bounded gate. Coins, indices and commodities are skipped before the
        call: that endpoint is entitled and NOT symbol-gated (`fmp_entitlements`), so a
        BTCUSD row used to make a real HTTP round trip to be told nothing — not a pre-HTTP
        `FMPNotEntitledException`. `asset_types` is the stored column, a hint only;
        `resolve_asset_class` falls back to the symbol's shape.
        """
        if not watchlist_tickers:
            return []
        asset_types = asset_types or {}

        cutoff = datetime.now() - timedelta(days=14)
        MIN_AMOUNT = 100_000  # $100K threshold to reduce noise
        gate = asyncio.Semaphore(_PER_TICKER_FANOUT_CONCURRENCY)

        async def _fetch_one(
            ticker: str,
        ) -> Optional[Tuple[str, InsiderTransactionItemResponse, float]]:
            """Return the most notable insider transaction per ticker, if any.

            Returns (action_word, item, raw_amount).
            """
            key = (ticker or "").upper()
            if not key:
                return None
            if resolve_asset_class(key, asset_types.get(ticker)).lower() not in _CLASSIFIABLE_ASSET_TYPES:
                return None
            hit, cached = _insider_cache_get(key)
            if hit:
                return cached
            async with gate:
                result, measured = await _fetch_uncached(ticker)
            # Only a MEASURED answer is cached (a genuine "no notable Form 4 in the window"
            # included). A failed fetch — an FMP 429 while the cold fan-out is over the
            # minute budget, a 5xx — used to be stored as `None` for the full TTL,
            # process-wide, so every user's feed read "no insider activity" for those
            # tickers for 10 minutes (W2 regress-C-3).
            if measured:
                _insider_cache_set(key, result)
            return result

        async def _fetch_uncached(
            ticker: str,
        ) -> Tuple[Optional[Tuple[str, InsiderTransactionItemResponse, float]], bool]:
            """(alert or None, measured). `measured` is False when the fetch FAILED —
            including `get_insider_trading`'s own `[]`-on-rate-limit degradation, which
            surfaces here as an `EmptyAfterFailure`-style marker or a raise."""
            try:
                trades = await self.fmp.get_insider_trading(ticker, limit=30)
            except Exception as exc:
                logger.warning("Insider trading for %s failed: %s", ticker, exc)
                return None, False
            if getattr(trades, "fetch_failed", False):
                return None, False
            if not isinstance(trades, list) or not trades:
                return None, True

            # Aggregate by (insider_name, transaction_date, action) because
            # Form 4 filings often split one decision across many small rows.
            buckets: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
            for tx in trades:
                date_str = tx.get("transactionDate") or tx.get("filingDate") or ""
                dt = _parse_date(date_str)
                if dt is None or dt < cutoff:
                    continue

                # Shared classifier keeps this in lockstep with the Holders
                # tab — only surface trades the Holders tab would label
                # "Informative Buy/Sell". Option exercises, tax withholding,
                # and composite S+OE sales are filtered out.
                action_word, informative = classify_for_alerts(
                    tx.get("transactionType") or ""
                )
                if not informative:
                    continue

                # `_finite_or_none`, NOT bare float() — the rest of this file already
                # does this and these two lines were the gap.
                #
                # A bare `NaN` token in FMP's body parses cleanly (Python's json accepts
                # it), `NaN or 0` KEEPS the NaN because NaN is truthy, `float(nan)` does
                # not raise so `except (TypeError, ValueError)` never fires, and
                # `nan <= 0` is False so the row is not skipped. The NaN then lands in
                # `InsiderTransactionItemResponse.raw_amount`, which is a REQUIRED float,
                # and Starlette renders with allow_nan=False → the WHOLE Tracking feed
                # 500s from inside the renderer, past this function's own error handling.
                shares = _finite_or_none(tx.get("securitiesTransacted"))
                price = _finite_or_none(tx.get("price"))
                if shares is None or price is None:
                    continue
                amount = shares * price
                if not math.isfinite(amount) or amount <= 0:
                    continue

                insider_name = (tx.get("reportingName") or "Insider").strip()
                insider_title = (tx.get("typeOfOwner") or "Officer").strip()
                key = (insider_name, date_str, action_word)
                bucket = buckets.setdefault(
                    key,
                    {
                        "insider_title": insider_title,
                        "amount": 0.0,
                        "dt": dt,
                    },
                )
                bucket["amount"] += amount

            if not buckets:
                return None, True

            best: Optional[Tuple[Tuple[str, str, str], Dict[str, Any]]] = None
            for key, bucket in buckets.items():
                if bucket["amount"] < MIN_AMOUNT:
                    continue
                if best is None or bucket["amount"] > best[1]["amount"]:
                    best = (key, bucket)
            if best is None:
                return None, True

            (insider_name, _, action_word), bucket = best
            item = InsiderTransactionItemResponse(
                ticker=ticker.upper(),
                insider_name=insider_name,
                insider_title=bucket["insider_title"],
                amount=_format_amount(bucket["amount"]),
                raw_amount=bucket["amount"],
                day=bucket["dt"].day,
                month=bucket["dt"].strftime("%b").upper(),
            )
            return (action_word, item, bucket["amount"]), True

        results = await asyncio.gather(
            *[_fetch_one(t) for t in watchlist_tickers], return_exceptions=True
        )

        action_groups: Dict[str, List[InsiderTransactionItemResponse]] = {
            "bought": [],
            "sold": [],
        }
        action_totals: Dict[str, float] = {"bought": 0.0, "sold": 0.0}
        for res in results:
            if not isinstance(res, tuple):
                continue
            action_word, item, raw_amount = res
            action_groups[action_word].append(item)
            action_totals[action_word] += raw_amount

        alerts: List[AlertResponse] = []
        for action_word in ("bought", "sold"):
            items = action_groups[action_word]
            if not items:
                continue
            # Largest first by the EXACT numeric amount — never by re-parsing the
            # formatted label (rounding collisions like "$1000K" vs "$1.0M" can
            # otherwise order a smaller position above a larger one).
            items.sort(key=lambda it: it.raw_amount, reverse=True)
            total_label = _format_amount(action_totals[action_word])
            title = "Insider Bought" if action_word == "bought" else "Insider Sold"
            description = (
                f"{_join_tickers([it.ticker for it in items])} this week"
                f" — totaling {total_label}."
            )
            alerts.append(
                AlertResponse(
                    type="insider_transaction",
                    title=title,
                    description=description,
                    action=action_word,
                    total_amount=total_label,
                    time_window_label="this week",
                    insider_transaction_items=items,
                )
            )

        return alerts


# ── Helpers ─────────────────────────────────────────────────────────




def _format_amount_or_range(
    bounds: List[Tuple[float, Optional[float]]],
    midpoint_sum: float,
    is_congress: bool = False,
) -> str:
    """Format a rolled-up whale amount honestly.

    ``bounds`` is a list of ``(low, high)`` per underlying trade. Institutional
    (13F) trades are exact points (``low == high``); congressional trades are
    STOCK Act ranges.

    - ``is_congress`` True → ALWAYS render as a range/estimate, never a precise
      dollar (a malformed/single-value congress bucket collapses to
      ``low == high`` but must still not fake precision).
    - Otherwise → collapse to the single exact figure (13F-only bucket).
    """
    if not bounds:
        return _format_amount(midpoint_sum)
    if is_congress:
        low, high = sum_amount_bounds(bounds)
        # Malformed / single-value congress bounds collapse to low == high;
        # format_amount_range would then emit a bare precise dollar. Never show
        # false precision for a congressperson — mark it an estimate (or "—").
        if high is not None and abs(high - low) < 1.0:
            return f"~{_format_amount(low)}" if low >= 1.0 else "—"
        return format_amount_range(low, high)
    return _format_amount(midpoint_sum)


def _amount_sort_key(label: str) -> float:
    """Convert a $X.XB / $X.XM / $XK label back to a float for sorting."""
    if not label:
        return 0.0
    s = label.replace("$", "").replace(",", "").strip()
    mult = 1.0
    if s.endswith("B"):
        mult, s = 1_000_000_000.0, s[:-1]
    elif s.endswith("M"):
        mult, s = 1_000_000.0, s[:-1]
    elif s.endswith("K"):
        mult, s = 1_000.0, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


def _join_tickers(tickers: List[str], max_visible: int = 4) -> str:
    """Join tickers for card descriptions, truncating long lists."""
    if not tickers:
        return ""
    if len(tickers) <= max_visible:
        return ", ".join(tickers)
    head = ", ".join(tickers[:max_visible])
    return f"{head} and {len(tickers) - max_visible} more"


def _opt_float(value: Any) -> Optional[float]:
    """Return ``float(value)`` when possible, else ``None``."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse a date string in common FMP formats."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


_RATING_RANK = {
    "strong sell": 0, "sell": 1, "underperform": 1, "underweight": 1,
    "hold": 2, "neutral": 2, "market perform": 2, "equal-weight": 2, "equal weight": 2,
    "buy": 3, "overweight": 3, "outperform": 3, "accumulate": 3,
    "strong buy": 4, "conviction buy": 4,
}


def _infer_rating_action(previous: str, new: str) -> str:
    """Infer upgrade/downgrade/reiterate from two rating labels."""
    prev = _RATING_RANK.get(previous.strip().lower())
    curr = _RATING_RANK.get(new.strip().lower())
    if prev is None or curr is None:
        return "reiterate"
    if curr > prev:
        return "upgrade"
    if curr < prev:
        return "downgrade"
    return "reiterate"
