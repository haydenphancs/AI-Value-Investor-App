"""Daily-rotating starter questions for the Ask Cay AI chat.

Composes one ET day's suggestion chips from two halves:

* **Live slots** — what is actually hot today, drawn ONLY from data another part of the
  app already keeps warm. Zero additional upstream calls by construction (see
  :meth:`ChatStartersService._collect_sources`).
* **Evergreen slots** — an editorial pool in ``public.chat_starters``, walked by
  :mod:`app.services.daily_rotation` so the selection is a pure function of the date.

Two invariants govern everything here, and both are contract rather than preference:

1. **The response is IMPERSONAL.** One cache entry serves every caller, so nothing may
   depend on who asked — no watchlist, no tier, no holdings. ``signals_v3`` is excluded
   for exactly this reason: its tickers are Pro-gated and ``redact_signals()`` masks them
   per request, so a globally cached set carrying them would hand Free users the tickers
   the paywall hides. `test_chat_starters_endpoint.py` fails the build if this module
   starts reading the caller or importing the signals service.
2. **It never fails.** Every source is optional and every slot degrades to an evergreen
   question. The floor is the bundled catalogue loaded at import, so even a dead database
   and a dead market feed still return a full row of chips.
"""

import asyncio
import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.database import get_supabase
from app.schemas.chat_starters import (
    ChatStarterResponse,
    ChatStartersResponse,
    DetailStarterSetResponse,
)
from app.services.daily_rotation import normalize_pool, pick_for_day

logger = logging.getLogger(__name__)

#: The bundled catalogue, vendored beside the copy the iOS app ships. It is the FLOOR of
#: the degradation chain, not a nicety: without it, a database outage would empty the
#: chip row entirely.
_CATALOGUE_PATH = Path(__file__).resolve().parents[2] / "data" / "chat_starters.json"

_TABLE = "chat_starters"
_GLOBAL_SCOPE = "global"
_DETAIL_SCOPES: Tuple[str, ...] = ("ticker", "etf", "crypto", "commodity", "index")
_ALL_SCOPES: Tuple[str, ...] = (_GLOBAL_SCOPE,) + _DETAIL_SCOPES

#: How many chips the global row carries, and how many templates each detail bar gets.
_GLOBAL_SLOTS = 8
_DETAIL_SLOTS = 4

#: A move has to be a real move before a chip may call it hot. `_MOVERS_MIN_ABS_CHANGE_PCT`
#: in home_dashboard_service is 0.05 — enough to keep a leaderboard row from rendering
#: "0.0%", nowhere near enough to justify the word "today" in a question.
_HOT_MIN_ABS_CHANGE_PCT = 3.0

#: Same idea for a sector or theme: an average that small is noise, and asking "what's
#: driving Energy today?" on a 0.2% day invents a story that does not exist.
_HOT_MIN_GROUP_CHANGE_PCT = 0.75

_POOL_TTL_SECONDS = 3600
#: An empty-but-successful pool load is cached for seconds, not the hour — the same
#: reseed-window trap `money_moves_content_service` documents. Caching an empty read for
#: an hour would pin every client to the bundled fallback long after the table recovered.
_POOL_EMPTY_TTL_SECONDS = 30
#: The live half moves intraday, so the composed response is short-lived even though the
#: evergreen half only changes at ET midnight.
_RESPONSE_TTL_SECONDS = 900
#: A cold build must never hold up a screen whose whole job is to paint instantly.
_BUILD_TIMEOUT_SECONDS = 2.5
_POOL_MAX_ROWS = 2000


def _load_bundled_catalogue() -> Dict[str, List[str]]:
    """Read the vendored JSON once at import. Never raises."""
    try:
        raw = json.loads(_CATALOGUE_PATH.read_text(encoding="utf-8"))
        detail = raw.get("detail") or {}
        pools = {_GLOBAL_SCOPE: normalize_pool(raw.get("global") or [])}
        for scope in _DETAIL_SCOPES:
            pools[scope] = normalize_pool(detail.get(scope) or [])
        return pools
    except Exception as exc:  # noqa: BLE001 — the fallback must not be able to break boot
        logger.error(
            "chat_starters: bundled catalogue unreadable at %s (%s: %s) — the chip row "
            "now depends entirely on Supabase",
            _CATALOGUE_PATH, type(exc).__name__, exc,
        )
        return {scope: [] for scope in _ALL_SCOPES}


_BUNDLED: Dict[str, List[str]] = _load_bundled_catalogue()


def _trading_date_et() -> str:
    """Today's ET calendar date, via the app's one trading-day convention.

    Imported lazily so this module stays importable (and unit-testable) without dragging
    in the notification stack. Deliberately NOT `current_close_cycle_start()`: that
    resolves Saturday and Sunday back to Friday's close, so the questions would visibly
    stop rotating over every weekend — the exact complaint this feature answers.
    """
    from app.services.push_dispatch_service import trading_date_et

    return trading_date_et()


def _finite(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _has_waiters(fut: asyncio.Future) -> bool:
    """Whether anything is awaiting `fut`. Mirrors `money_moves_content_service`."""
    try:
        return bool(getattr(fut, "_callbacks", None))
    except Exception:  # noqa: BLE001
        return True


class ChatStartersService:
    # ── pool tier (editorial rows; near-static) ──────────────────────────────
    _pool_cache: Optional[Tuple[float, Dict[str, List[str]]]] = None
    _pool_inflight: Optional[asyncio.Future] = None

    # ── response tier (live slots folded in; short-lived) ────────────────────
    _response_cache: Optional[Tuple[float, str, ChatStartersResponse]] = None
    _response_inflight: Optional[asyncio.Future] = None

    # ── public entry point ───────────────────────────────────────────────────

    async def get_starters(self) -> ChatStartersResponse:
        """One ET day's starters. Always returns a usable body."""
        today = _trading_date_et()

        cached = ChatStartersService._response_cache
        if (
            cached is not None
            and cached[1] == today
            and time.time() - cached[0] < _RESPONSE_TTL_SECONDS
        ):
            return cached[2]

        inflight = ChatStartersService._response_inflight
        if inflight is not None:
            try:
                return await asyncio.shield(inflight)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "chat_starters: joined build failed (%s: %s) — serving fallback",
                    type(exc).__name__, exc,
                )
                return self._fallback(today)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        ChatStartersService._response_inflight = future
        try:
            # SHIELDED so a caller that times out below cannot cancel the build every
            # other caller is waiting on; the build completes and warms the cache for
            # the next request even though this one already answered from fallback.
            response = await asyncio.wait_for(
                asyncio.shield(self._build(today)), _BUILD_TIMEOUT_SECONDS
            )
            ChatStartersService._response_cache = (time.time(), today, response)
            if not future.done():
                future.set_result(response)
            return response
        except asyncio.TimeoutError:
            logger.warning(
                "chat_starters: build exceeded %.1fs — serving fallback; the shielded "
                "build continues and will warm the cache",
                _BUILD_TIMEOUT_SECONDS,
            )
            fallback = self._fallback(today)
            if not future.done():
                future.set_result(fallback)
            return fallback
        except Exception as exc:  # noqa: BLE001 — never 500 the chip row
            logger.exception(
                "chat_starters: build failed: %s: %s", type(exc).__name__, exc
            )
            fallback = self._fallback(today)
            if not future.done():
                future.set_result(fallback)
            return fallback
        except BaseException as exc:
            # CancelledError is a BaseException, so `except Exception` misses it and the
            # `finally` would clear _inflight while joined waiters hang on a future that
            # is never resolved. Settle it before leaving.
            logger.warning(
                "chat_starters: build aborted (%s) — releasing joined waiter(s)",
                type(exc).__name__,
            )
            if not future.done():
                if _has_waiters(future):
                    future.set_exception(exc)
                else:
                    future.cancel()
            raise
        finally:
            ChatStartersService._response_inflight = None

    def _fallback(self, today: str) -> ChatStartersResponse:
        """The best answer available without a successful build.

        Prefers the last good response, but ONLY within the same ET day. Across a
        rollover a stale body would keep announcing yesterday's top gainer as "hot
        today" for as long as the outage lasted, which is a false statement rather than
        a stale one — so past midnight it drops to evergreen-only.
        """
        cached = ChatStartersService._response_cache
        if cached is not None and cached[1] == today:
            return cached[2]

        pool = ChatStartersService._pool_cache
        pools = pool[1] if pool is not None else _BUNDLED
        return self._assemble(today, pools, live=[])

    # ── the pool tier ────────────────────────────────────────────────────────

    async def _get_pools(self) -> Dict[str, List[str]]:
        """Editorial pools by scope, Supabase-first with the bundled catalogue behind."""
        cached = ChatStartersService._pool_cache
        if cached is not None:
            ttl = (
                _POOL_TTL_SECONDS
                if cached[1].get(_GLOBAL_SCOPE)
                else _POOL_EMPTY_TTL_SECONDS
            )
            if time.time() - cached[0] < ttl:
                return cached[1]

        inflight = ChatStartersService._pool_inflight
        if inflight is not None:
            return await asyncio.shield(inflight)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        ChatStartersService._pool_inflight = future
        try:
            pools = await asyncio.to_thread(self._read_pool_rows)
            # An empty read must never REPLACE a good pool: prefer the stale-but-real
            # one and retry next time. Without this a single reseed window pins every
            # client to the bundled copy for an hour.
            if not pools.get(_GLOBAL_SCOPE) and cached is not None and cached[1].get(_GLOBAL_SCOPE):
                logger.warning(
                    "chat_starters: pool read returned 0 global rows — keeping the "
                    "previous pool of %d and retrying next request",
                    len(cached[1][_GLOBAL_SCOPE]),
                )
                if not future.done():
                    future.set_result(cached[1])
                return cached[1]
            if not pools.get(_GLOBAL_SCOPE):
                logger.warning(
                    "chat_starters: pool read returned 0 global rows and there is no "
                    "prior pool — falling back to the bundled catalogue (%d questions)",
                    len(_BUNDLED.get(_GLOBAL_SCOPE, [])),
                )
                pools = {
                    scope: pools.get(scope) or list(_BUNDLED.get(scope, []))
                    for scope in _ALL_SCOPES
                }
            ChatStartersService._pool_cache = (time.time(), pools)
            if not future.done():
                future.set_result(pools)
            return pools
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "chat_starters: pool read failed (%s: %s) — using %s",
                type(exc).__name__, exc,
                "the stale pool" if cached else "the bundled catalogue",
            )
            pools = cached[1] if cached is not None else dict(_BUNDLED)
            if not future.done():
                future.set_result(pools)
            return pools
        except BaseException as exc:
            if not future.done():
                if _has_waiters(future):
                    future.set_exception(exc)
                else:
                    future.cancel()
            raise
        finally:
            ChatStartersService._pool_inflight = None

    def _read_pool_rows(self) -> Dict[str, List[str]]:
        """Sync Supabase read (called through `to_thread` — the SDK is not async).

        Raises on a Supabase error rather than returning ``{}``: an error and a genuinely
        empty table are different facts, and only the caller can tell whether it has a
        good pool worth keeping.
        """
        result = (
            get_supabase()
            .table(_TABLE)
            .select("text, scope")
            .eq("is_active", True)
            .limit(_POOL_MAX_ROWS)
            .execute()
        )
        by_scope: Dict[str, List[str]] = {scope: [] for scope in _ALL_SCOPES}
        for row in result.data or []:
            scope = (row.get("scope") or "").strip()
            text = row.get("text")
            if scope in by_scope and isinstance(text, str):
                by_scope[scope].append(text)
        # normalize_pool dedupes and sorts; sorting is what makes the walk independent
        # of PostgREST's unspecified row order.
        return {scope: normalize_pool(items) for scope, items in by_scope.items()}

    # ── the live tier ────────────────────────────────────────────────────────

    async def _collect_sources(self) -> Dict[str, Any]:
        """Gather every live source concurrently. Never raises; missing keys are absent.

        Every one of these is already warm for another surface, so this adds no upstream
        calls in the steady state:

        * ``get_scanner_inputs()`` — the 60-second movers universe Home, watchlist and
          price alerts all share.
        * ``get_sector_performance()`` — derived from that same universe, no new fetch.
        * ``get_all_mentions()`` — ApeWisdom's in-process cache; non-blocking by design,
          returns ``{}`` on a cold process rather than awaiting a refresh.
        * the ``trending_themes`` rows — a small Supabase read.

        ⚠️ `HomeDashboardService.get_scanners()` is deliberately NOT used, tempting as the
        pre-ranked leaderboards are: its pre-warmer idles outside 09:30–16:00 ET, so a
        pre-open caller would trigger a cold 8-second build behind a 2.5-second budget.
        """
        from app.services.market_movers_service import get_market_movers_service

        movers = get_market_movers_service()
        results = await asyncio.gather(
            movers.get_scanner_inputs(),
            movers.get_sector_performance(),
            self._read_theme_rows_async(),
            self._mentions(),
            return_exceptions=True,
        )
        names = ("scanner_inputs", "sectors", "themes", "mentions")
        out: Dict[str, Any] = {}
        for name, result in zip(names, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "chat_starters: live source %r unavailable (%s: %s) — that slot "
                    "degrades to an evergreen question",
                    name, type(result).__name__, result,
                )
                continue
            out[name] = result
        return out

    async def _mentions(self) -> Dict[str, Dict[str, Any]]:
        from app.integrations.apewisdom import get_all_mentions

        return await get_all_mentions()

    async def _read_theme_rows_async(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._read_theme_rows)

    def _read_theme_rows(self) -> List[Dict[str, Any]]:
        """Active theme rows INCLUDING `category`.

        `home_dashboard_service._read_theme_rows` deliberately does not select
        `category` — migration 081 keeps it off the wire because the Home card shows only
        `title`. That still holds: only the derived question TEXT ships from here, never
        the column.
        """
        result = (
            get_supabase()
            .table("trending_themes")
            .select("category, tickers")
            .eq("is_active", True)
            .limit(50)
            .execute()
        )
        return list(result.data or [])

    # ── slot builders (each returns None instead of raising) ─────────────────

    def _hot_ticker_slots(self, sources: Dict[str, Any]) -> List[ChatStarterResponse]:
        """Up to two chips naming a company that genuinely moved today."""
        inputs = sources.get("scanner_inputs")
        if not inputs:
            return []
        try:
            profile_map, change_map = inputs
        except (TypeError, ValueError):
            return []
        if not profile_map or not change_map:
            return []

        # Reuse Home's ranker rather than re-deriving it. It already applies the quality
        # gate, joins class-share symbols whose profile spelling differs from the mover
        # list, and drops the signed zero that paints a loser green on iOS.
        from app.services.home_dashboard_service import _movers_from_universe

        out: List[ChatStarterResponse] = []
        seen: set[str] = set()
        for positive, template in (
            (True, "Why is {symbol} up {pct} today?"),
            (False, "Why is {symbol} down {pct} today?"),
        ):
            try:
                rows = _movers_from_universe(
                    profile_map, change_map, positive=positive, rows=5
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "chat_starters: mover ranking failed (%s: %s)",
                    type(exc).__name__, exc,
                )
                continue
            for row in rows:
                symbol = (getattr(row, "symbol", "") or "").strip().upper()
                change = _finite(getattr(row, "change_percent", None))
                if not symbol or symbol in seen or change is None:
                    continue
                if abs(change) < _HOT_MIN_ABS_CHANGE_PCT:
                    continue
                seen.add(symbol)
                out.append(
                    ChatStarterResponse(
                        text=template.format(symbol=symbol, pct=f"{abs(change):.0f}%"),
                        kind="hot_ticker",
                        symbol=symbol,
                    )
                )
                break
        return out

    def _trending_slot(self, sources: Dict[str, Any]) -> Optional[ChatStarterResponse]:
        """A chip naming the most-discussed ticker — but only if it is a real, moving company.

        ⚠️ **A ticker being popular on social media does not make it the security that
        ticker names.** ApeWisdom's crypto feed is full of symbols that are also listed
        equities: ``ADA`` is Cardano to Reddit and Adams Resources & Energy to the NYSE.
        A membership test alone therefore does not protect anything — it *passes* for
        exactly that collision and would attach "everyone is talking about it" to an
        unrelated oil-logistics microcap.

        So the gate is threefold: the symbol is in the movers universe, it clears the
        quality bar, AND it is independently moving today. A coin ticker colliding with a
        sleepy small-cap fails the third test, which is the one that actually discriminates.
        """
        mentions = sources.get("mentions") or {}
        inputs = sources.get("scanner_inputs")
        if not mentions or not inputs:
            return None
        try:
            profile_map, change_map = inputs
        except (TypeError, ValueError):
            return None

        from app.services.home_dashboard_service import _canonical_symbol, _is_quality_company

        ranked = sorted(
            (
                (sym, data) for sym, data in mentions.items()
                if isinstance(data, dict) and _finite(data.get("rank")) is not None
            ),
            key=lambda kv: _finite(kv[1].get("rank")) or float("inf"),
        )
        for symbol, _data in ranked[:25]:
            clean = (str(symbol) or "").strip().upper()
            if not clean:
                continue
            profile = profile_map.get(clean)
            if profile is None or not _is_quality_company(profile):
                continue
            change = _finite(change_map.get(_canonical_symbol(clean)))
            if change is None or abs(change) < _HOT_MIN_ABS_CHANGE_PCT:
                continue
            return ChatStarterResponse(
                text=f"Why is everyone talking about {clean}?",
                kind="trending",
                symbol=clean,
            )
        return None

    def _hot_sector_slot(self, sources: Dict[str, Any]) -> Optional[ChatStarterResponse]:
        """The sector with the largest absolute move today, in either direction."""
        sectors = sources.get("sectors") or []
        best_name, best_change = None, 0.0
        for row in sectors:
            if not isinstance(row, dict):
                continue
            name = (row.get("sector") or "").strip()
            change = _finite(row.get("changesPercentage"))
            if not name or change is None:
                continue
            if abs(change) > abs(best_change):
                best_name, best_change = name, change
        if best_name is None or abs(best_change) < _HOT_MIN_GROUP_CHANGE_PCT:
            return None
        direction = "leading" if best_change > 0 else "lagging"
        return ChatStarterResponse(
            text=f"Why is {best_name} {direction} today?", kind="hot_sector"
        )

    def _hot_topic_slot(self, sources: Dict[str, Any]) -> Optional[ChatStarterResponse]:
        """The editorial theme whose basket moved most today.

        ⚠️ The theme's `category` alone is NOT a "hot today" signal — those are eight
        static strings seeded by migration 081. Rotating them by date would produce
        "What's driving Rare Earth Mining today?" on a day rare earths did nothing, which
        is an affirmatively false claim rather than a stale one. Ranking them by the
        basket's live move is what earns the word "today"; if nothing moved enough, this
        slot yields to an evergreen question instead.
        """
        themes = sources.get("themes") or []
        inputs = sources.get("scanner_inputs")
        if not themes or not inputs:
            return None
        try:
            _profile_map, change_map = inputs
        except (TypeError, ValueError):
            return None

        best_name, best_change = None, 0.0
        for row in themes:
            if not isinstance(row, dict):
                continue
            category = (row.get("category") or "").strip()
            tickers = row.get("tickers") or []
            if not category or not isinstance(tickers, (list, tuple)):
                continue
            changes = [
                c for c in (_finite(change_map.get(str(t).strip().upper())) for t in tickers)
                if c is not None
            ]
            if not changes:
                continue
            mean = sum(changes) / len(changes)
            if abs(mean) > abs(best_change):
                best_name, best_change = category, mean
        if best_name is None or abs(best_change) < _HOT_MIN_GROUP_CHANGE_PCT:
            return None
        return ChatStarterResponse(
            text=f"What's driving {best_name} today?", kind="hot_topic"
        )

    # ── assembly ─────────────────────────────────────────────────────────────

    async def _build(self, today: str) -> ChatStartersResponse:
        pools, sources = await asyncio.gather(
            self._get_pools(), self._collect_sources()
        )
        live: List[ChatStarterResponse] = []
        live.extend(self._hot_ticker_slots(sources))
        for slot in (self._hot_sector_slot(sources), self._hot_topic_slot(sources)):
            if slot is not None:
                live.append(slot)
        if len(live) < 4:
            trending = self._trending_slot(sources)
            if trending is not None:
                live.append(trending)
        return self._assemble(today, pools, live)

    def _assemble(
        self,
        today: str,
        pools: Dict[str, List[str]],
        live: Sequence[ChatStarterResponse],
    ) -> ChatStartersResponse:
        """Fold live slots, the two fixed asks and evergreen filler into one row.

        Order is deliberate: the two fixed questions are what the tester literally asked
        for and are unconditional, which also makes "the row is never empty" true before
        any fallback logic runs.
        """
        chips: List[ChatStarterResponse] = []
        seen: set[str] = set()

        def add(chip: ChatStarterResponse) -> None:
            key = " ".join(chip.text.split()).casefold()
            if not key or key in seen or len(chips) >= _GLOBAL_SLOTS:
                return
            seen.add(key)
            chips.append(chip)

        for chip in live:
            add(chip)
        add(ChatStarterResponse(text="What tickers are hot today?", kind="fixed"))
        add(ChatStarterResponse(text="What topics are hot today?", kind="fixed"))

        # Top up from the day's walk. Ask for the full row's worth rather than the exact
        # shortfall so cross-slot dedupe cannot leave the row short, then let `add`'s cap
        # do the trimming — never a second draw, which would break determinism.
        for text in pick_for_day(
            pools.get(_GLOBAL_SCOPE) or _BUNDLED.get(_GLOBAL_SCOPE, []),
            _GLOBAL_SLOTS,
            today,
        ):
            add(ChatStarterResponse(text=text, kind="evergreen"))

        detail = DetailStarterSetResponse(
            **{
                scope: pick_for_day(
                    pools.get(scope) or _BUNDLED.get(scope, []),
                    _DETAIL_SLOTS,
                    today,
                    # Without a per-scope salt all five detail bars would sit at the same
                    # phase of the walk and rotate in lockstep.
                    salt=scope,
                )
                for scope in _DETAIL_SCOPES
            }
        )
        return ChatStartersResponse(
            trading_date=today, global_starters=chips, detail_starters=detail
        )


_service: Optional[ChatStartersService] = None


def get_chat_starters_service() -> ChatStartersService:
    global _service
    if _service is None:
        _service = ChatStartersService()
    return _service
