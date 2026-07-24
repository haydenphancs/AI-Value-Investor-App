"""
Updates-screen AI Insight sweeper.

Runs inside the FastAPI lifespan (registered in ``app/main.py``). Two passes on
different cadences, both gated on ``is_market_active()``:

  * PRICE pass  (every 5 min)  — one ``batch-quote`` call for the whole universe,
    then re-evaluate the materiality gate for every scope. Catches a big move
    before any headline lands.
  * NEWS pass   (every 15 min) — force-refresh recent articles from FMP into
    ``ticker_news_cache`` (bypassing its 6h read TTL), then re-evaluate. Catches
    a breaking story.

WHY A BACKGROUND SWEEPER RATHER THAN A REQUEST-TIME GATE
--------------------------------------------------------
1. Freshness does not depend on someone having the app open. A −12% collapse at
   09:35 refreshes the card whether or not anyone is looking.
2. The read path stays a pure cache read — no HTTP handler can ever reach Gemini,
   so the Updates tab is sub-100 ms regardless of LLM latency.
3. Decisions are traceable. One loop, one cadence, one structured log line per
   sweep, plus a persisted skip reason per scope — so "why didn't AAPL refresh
   at 14:32?" is answerable without having logged every user request.

SPEND CEILINGS (defence in depth)
---------------------------------
  per-scope cooldown  (updates_materiality.COOLDOWN_*)
  per-scope daily cap (updates_materiality.daily_cap_for — 16 for the market
                      scope, 6 per ticker; enforced BOTH in the pure gate and,
                      authoritatively, by `claim_updates_insight_scope`. Feed
                      both from `daily_cap_for` or the DB silently overrules
                      the gate.)
  pre-market reserve  (updates_materiality.premarket_cap_for — keeps two thirds
                      of the allowance for 09:30-16:00 ET)
  per-cycle cap       (_PER_CYCLE_REGEN_CAP, priority-ordered by move size)
  concurrency         (_GEN_CONCURRENCY)
  durable global cap  (_GLOBAL_DAILY_CAP, enforced in Postgres)
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.services.news_cache_service import MARKET_SCOPE, get_news_cache_service
from app.services.news_insight_service import (
    INSIGHT_MODEL,
    get_news_insight_service,
    select_recent_corpus,
)
from app.services.ticker_report_cache import current_close_cycle_start
from app.services.volatility_cache_service import get_volatility_cache_service
from app.config import settings
from app.services.price_catalyst_service import get_price_catalyst_service
from app.services.updates_materiality import (
    ACTION_GENERATE,
    ACTION_TOUCH,
    BAND_EXTREME,
    PER_SCOPE_ATTEMPT_CAP,
    PER_SCOPE_DAILY_CAP,
    TIER_EXTREME,
    TIER_UNUSUAL,
    Decision,
    daily_cap_for,
    decide,
    finite,
)
from app.utils.market_hours import ET, is_market_active, session_phase

logger = logging.getLogger(__name__)

# The market index whose move drives the market card and the anti-stampede guard.
MARKET_INDEX_SYMBOL = "^GSPC"

# How many watchlist tickers to sweep. Ordered by watcher count, so the cap
# drops the least-watched names first.
_MAX_UNIVERSE = 200

# Most regenerations allowed in a single cycle. Bounds the blast radius of a
# broad market event; anything not admitted re-trips next cycle.
_PER_CYCLE_REGEN_CAP = 8
_GEN_CONCURRENCY = 3

# Hard daily ceiling across ALL scopes, enforced by an atomic Postgres RPC so two
# Railway instances cannot both slip past it. At Flash-Lite pricing this caps
# spend at roughly $17/month.
_GLOBAL_DAILY_CAP = 1500

# A claim older than this is assumed orphaned (the holder crashed / was
# redeployed mid-generation) and may be stolen. Same two-threshold pattern as
# ``processing_started_at`` in research_reconciliation_service.
_CLAIM_STALE_SECONDS = 120

# Max grounded "why did it move" web searches per ET day, across ALL scopes, so a
# broad-volatility day cannot fan out unbounded (paid) searches. The cap bounds
# DISTINCT big movers per day: a scope already explained today does not re-consume
# a unit (it just re-reads its 24h-cached catalyst), so a churning or repeatedly-
# failing single ticker cannot exhaust the budget and starve genuinely-new movers.
# In-process v1 (bounded blast radius 2×cap across two Railway instances); a durable
# cross-instance RPC is a documented follow-up.
_CATALYST_DAILY_CAP = 30

# The move tiers that earn a grounded "why it moved" catalyst. Includes the fixed-
# band BAND_EXTREME so a thin-history / newly-listed name (σ unavailable → fallback
# band) — precisely the population most prone to violent moves — is not silently
# denied a catalyst that an established ticker with the same move would get.
_CATALYST_TIERS = (TIER_UNUSUAL, TIER_EXTREME, BAND_EXTREME)

# ── Proactive per-article enrichment (news pass) ──────────────────────
# After building each scope's windowed corpus for the card, the news pass also
# AI-enriches those individual articles (bullets + sentiment) so the whole 24h/48h
# feed shows summaries on scroll — not just the top few. Shared cache: enriching a
# scope's window also pre-summarises its detail News tab for every user.
_ENRICH_CONCURRENCY = 4          # simultaneous Gemini batch calls (cf. _refresh_news' Semaphore(5))
_ENRICH_SCOPES_PER_CYCLE = 40    # per-news-pass blast-radius bound (cf. _PER_CYCLE_REGEN_CAP)
_ENRICH_WINDOW_CAP = 25          # per scope; == news get_cached_bulk(scopes, 25) window
# In-process ET-day ceiling on enrichment BATCH CALLS (defense-in-depth vs a runaway
# news day). Self-limiting already bounds this — a fully-enriched scope makes zero
# calls — so this only bites on a broad, sustained influx. In-process v1 (2× across
# two Railway instances); a durable RPC is a follow-up, same as _CATALYST_DAILY_CAP.
_ENRICH_DAILY_CAP = 1200

_STATE_TABLE = "updates_insight_state"


class InsightSweeper:
    def __init__(self) -> None:
        self.supabase = get_supabase()
        self.fmp = get_fmp_client()
        self.news = get_news_cache_service()
        self.insights = get_news_insight_service()
        self.vol = get_volatility_cache_service()
        # In-process ET-day cap on grounded catalyst web searches. `_catalyst_scopes`
        # dedups so the cap counts DISTINCT movers, not attempts.
        self._catalyst_day = None
        self._catalyst_count = 0
        self._catalyst_scopes: set = set()
        # In-process ET-day cap on proactive enrichment batch calls.
        self._enrich_day = None
        self._enrich_count = 0

    # ── Universe ──────────────────────────────────────────────────────

    async def _universe(self) -> List[str]:
        """Scopes to sweep: the market feed plus the most-watched tickers."""
        def _query() -> List[str]:
            try:
                result = self.supabase.rpc(
                    "get_top_watchlist_tickers", {"n": _MAX_UNIVERSE}
                ).execute()
                return [
                    str(r["ticker"]).upper()
                    for r in (result.data or [])
                    if r.get("ticker")
                ]
            except Exception as e:
                logger.warning(
                    "Insight sweeper could not read the watchlist universe: %s: %s",
                    type(e).__name__, e,
                )
                return []

        tickers = await asyncio.to_thread(_query)
        # MARKET_SCOPE first: it backs the Updates screen's default tab, so it
        # must never be the one dropped by a cap.
        return [MARKET_SCOPE] + [t for t in tickers if t != MARKET_SCOPE]

    # ── State ─────────────────────────────────────────────────────────

    def _load_state(self, scopes: List[str]) -> Dict[str, Dict[str, Any]]:
        try:
            result = (
                self.supabase.table(_STATE_TABLE)
                .select("*")
                .in_("scope", scopes)
                .execute()
            )
            return {r["scope"]: r for r in (result.data or []) if r.get("scope")}
        except Exception as e:
            logger.warning(
                "Insight state load failed: %s: %s", type(e).__name__, e
            )
            return {}

    def _record_skips(
        self, skips: List[Tuple[str, Decision]], now: datetime
    ) -> None:
        """Persist why each scope was NOT regenerated, in ONE upsert.

        Batched deliberately: at ~200 scopes, a write per skip was ~200
        sequential round-trips per 5-minute sweep, and a slow-DB day could push
        a sweep past `_CLAIM_STALE_SECONDS`, letting another instance steal a
        live claim.

        Best-effort and explicitly non-fatal, but never silent: without these
        rows the only record of a skip is a log line nobody kept.
        """
        if not skips:
            return
        rows = [
            {
                "scope": scope,
                "last_skip_reason": decision.reason,
                "last_evaluated_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
            for scope, decision in skips
        ]
        try:
            self.supabase.table(_STATE_TABLE).upsert(
                rows, on_conflict="scope"
            ).execute()
        except Exception as e:
            logger.warning(
                "Could not persist %d skip reasons: %s: %s",
                len(rows), type(e).__name__, e,
            )

    def _claim(self, scope: str, now: datetime, is_market_scope: bool) -> bool:
        """Atomically claim the right to generate ``scope``'s card.

        Delegates to a single Postgres statement (`claim_updates_insight_scope`,
        migration 088) which performs the day roll, both cap checks, the
        stale-claim steal, and the attempt increment under one row lock.

        ``is_market_scope`` MUST be threaded through: the daily cap is enforced
        TWICE — once in the pure gate (`decide`) and once here, authoritatively,
        inside the RPC. Passing a flat `PER_SCOPE_DAILY_CAP` while the gate used
        `daily_cap_for()` made the raised market cap inert AND silent: the gate
        admitted `__MARKET__` at 6/16, the RPC's `regen_count_today < p_daily_cap`
        predicate matched no row, and the whole sweep returned False with nothing
        logged and no state row written — so the scope re-tripped every 5 minutes
        forever, burning a per-cycle admission slot each time, and the one
        diagnostic that explained the freeze (`last_skip_reason = 'daily_cap'`)
        disappeared. The two ceilings must be fed from the same function.

        This CANNOT be done client-side. Read-then-write has an ABA bug that
        silently defeats the daily cap: another instance can complete a whole
        claim→generate→release cycle while we are in Gemini, and because
        `claim_at` returns to NULL our conditional write still matches and
        stamps stale counters over its increment. PostgREST also cannot express
        a column-relative update (`attempts_today = attempts_today + 1`).

        The claim is taken BEFORE the Gemini call: losing it costs one missed
        cycle (≤5 min); winning it twice costs a duplicate paid call — so we
        fail toward the cheaper error.
        """
        # Stamp claim_at + evaluate stale-steal with a FRESH timestamp, NOT the
        # sweep-start `now`. A slow pre-claim phase (news refresh of ~200 scopes,
        # gate eval) can exceed _CLAIM_STALE_SECONDS; reusing the stale sweep-start
        # time would stamp claim_at in the past, so a second instance sees this
        # just-taken claim as already-stale and steals it → duplicate paid
        # generation + an under-counted daily cap. (The sweep-start `now` is still
        # used for gate consistency elsewhere; the claim clock must be real-time.)
        claim_now = datetime.now(timezone.utc)
        try:
            result = self.supabase.rpc(
                "claim_updates_insight_scope",
                {
                    "p_scope": scope,
                    "p_now": claim_now.isoformat(),
                    "p_stale_seconds": _CLAIM_STALE_SECONDS,
                    "p_attempt_cap": PER_SCOPE_ATTEMPT_CAP,
                    "p_daily_cap": daily_cap_for(is_market_scope),
                },
            ).execute()
            granted = result.data
            if isinstance(granted, list):
                granted = granted[0] if granted else False
            granted = bool(granted)
            if not granted:
                # The gate already said GENERATE, so a denial here is the
                # authoritative ceiling (or a live claim on another instance)
                # overruling it. Never silent: this is the difference between
                # "capped as designed" and "the two caps disagree", and without
                # the line the second is invisible — no state row is written on
                # this path either.
                logger.info(
                    "Insight claim denied for %s (daily_cap=%d attempt_cap=%d) — "
                    "already at ceiling, or claimed by another instance",
                    scope, daily_cap_for(is_market_scope), PER_SCOPE_ATTEMPT_CAP,
                )
            return granted
        except Exception as e:
            # Fail CLOSED: without a claim we might double-bill a Gemini call
            # across instances. Skipping costs at most one 5-minute cycle.
            logger.warning(
                "Insight claim RPC failed for %s (%s: %s) — skipping this cycle. "
                "Is migration 088 applied?",
                scope, type(e).__name__, e,
            )
            return False

    def _finish_claim(
        self,
        scope: str,
        now: datetime,
        decision: Decision,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Release the claim and record the outcome."""
        patch: Dict[str, Any] = {
            "claim_at": None,
            "last_evaluated_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        if success:
            patch.update({
                "last_inputset_id": decision.inputset_id,
                "last_price_band": decision.price_band,
                "last_trigger_reason": decision.reason,
                "last_skip_reason": None,
                "last_generated_at": now.isoformat(),
                "close_cycle": current_close_cycle_start(now).isoformat(),
                "last_error": None,
            })
        else:
            patch.update({
                "last_failure_at": now.isoformat(),
                "last_error": (error or "generation returned no card")[:500],
            })
        try:
            self.supabase.table(_STATE_TABLE).upsert(
                {"scope": scope, **patch}, on_conflict="scope"
            ).execute()
            if success:
                # regen_count_today must be incremented from the CURRENT value,
                # not from the stale read taken before the Gemini call.
                res = self.supabase.rpc(
                    "increment_updates_insight_success", {"p_scope": scope}
                ).execute()
                count = res.data[0] if isinstance(res.data, list) and res.data else res.data
                if not count:
                    # 0 means the state row vanished between the upsert above and
                    # this call — the success is now uncounted, so the daily cap
                    # is running loose for this scope. Never let that be silent.
                    logger.warning(
                        "Insight success counter did not increment for %s "
                        "(state row missing) — daily cap may under-count", scope,
                    )
        except Exception as e:
            logger.warning(
                "Could not finalise insight claim for %s: %s: %s",
                scope, type(e).__name__, e,
            )

    def _mark_cycle_touched(
        self, scopes: List[str], now: datetime
    ) -> None:
        """Advance close_cycle for scopes whose card was re-stamped, not regenerated."""
        try:
            self.supabase.table(_STATE_TABLE).update({
                "close_cycle": current_close_cycle_start(now).isoformat(),
                "last_skip_reason": "cycle_touch",
                "last_evaluated_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }).in_("scope", scopes).execute()
        except Exception as e:
            logger.warning(
                "Could not advance close_cycle for %d touched scopes: %s: %s",
                len(scopes), type(e).__name__, e,
            )

    def _release_claim(self, scope: str, now: datetime, reason: str) -> None:
        """Give the claim back without recording a generation.

        Used when we hold the claim but decide not to spend (global budget
        exhausted). Leaving it set would park the scope for the full 120s stale
        window for no reason.
        """
        try:
            self.supabase.table(_STATE_TABLE).update({
                "claim_at": None,
                "last_skip_reason": reason,
                "last_evaluated_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }).eq("scope", scope).execute()
        except Exception as e:
            # Non-fatal: the claim self-heals after _CLAIM_STALE_SECONDS.
            logger.warning(
                "Could not release insight claim for %s: %s: %s",
                scope, type(e).__name__, e,
            )

    def _claim_catalyst_budget(self, now: datetime, scope: str) -> bool:
        """Take one unit of today's grounded-catalyst budget for ``scope``
        (in-process ET-day counter).

        Bounds DISTINCT movers, not attempts: a scope already explained today
        does not re-consume a unit — its catalyst is 24h-cached, so a churning or
        repeatedly-failing single ticker cannot drain the budget and starve
        genuinely-new movers. Returns False once ``_CATALYST_DAILY_CAP`` distinct
        scopes have been admitted today (and the scope is not already among them).
        """
        day = now.astimezone(ET).date()
        if self._catalyst_day != day:
            self._catalyst_day = day
            self._catalyst_count = 0
            self._catalyst_scopes = set()
        if scope in self._catalyst_scopes:
            return True  # already counted today → re-read the cached catalyst, no new unit
        if self._catalyst_count >= _CATALYST_DAILY_CAP:
            return False
        self._catalyst_count += 1
        self._catalyst_scopes.add(scope)
        return True

    def _claim_enrich_budget(self, now: datetime) -> bool:
        """Take one unit of today's proactive-enrichment BATCH-CALL budget
        (in-process ET-day counter). Returns False once ``_ENRICH_DAILY_CAP`` batch
        calls are spent — the remaining scopes defer to the next day/cycle."""
        day = now.astimezone(ET).date()
        if self._enrich_day != day:
            self._enrich_day = day
            self._enrich_count = 0
        if self._enrich_count >= _ENRICH_DAILY_CAP:
            return False
        self._enrich_count += 1
        return True

    async def _enrich_windows(
        self,
        corpora: Dict[str, List[Dict[str, Any]]],
        scopes: List[str],
        now: datetime,
    ) -> Tuple[int, int]:
        """AI-enrich each scope's whole in-memory windowed corpus (bullets +
        sentiment on the individual articles) so the feed shows summaries on scroll,
        not just the top few. Returns ``(rows_enriched, scopes_deferred)``.

        Reuses the corpus the card already fetched/windowed (no extra read). It is
        SELF-LIMITING: a scope whose window is already fully enriched yields no ids
        and is never admitted, so a steady-state pass makes ~0 calls. Admission is
        bounded three ways — per-cycle scope cap, per-ET-day batch-call cap, and
        bounded concurrency — and prioritised MARKET-first (``scopes`` arrives in
        ``_universe`` order). Best-effort throughout: ``enrich_window`` swallows
        every ``Exception``, so only ``CancelledError`` (shutdown) propagates.
        """
        admitted: List[str] = []
        deferred = 0
        for scope in scopes:
            ids = self.news._enrichable_ids(
                corpora.get(scope) or [], _ENRICH_WINDOW_CAP
            )
            if not ids:
                continue
            if len(admitted) >= _ENRICH_SCOPES_PER_CYCLE or not self._claim_enrich_budget(now):
                deferred += 1
                continue
            admitted.append(scope)

        if not admitted:
            return 0, deferred

        sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

        async def _one(scope: str) -> int:
            async with sem:
                return await self.news.enrich_window(
                    scope, corpora.get(scope) or [], cap=_ENRICH_WINDOW_CAP
                )

        # No return_exceptions: enrich_window catches every Exception, so only a
        # shutdown CancelledError can propagate — which we WANT (it bubbles to the
        # loop's cancel handler and stops the sweeper). Enrichment holds no claim,
        # budget row-lock, or card-budget unit, so there is nothing to release.
        counts = await asyncio.gather(*[_one(s) for s in admitted])
        return sum(counts), deferred

    async def _maybe_price_move(
        self,
        scope: str,
        decision: Decision,
        now: datetime,
        quote: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Grounded "why did it move" for a per-ticker Unusual/Extreme session
        move, folded onto the card as a separate ``price_move`` block.

        Gated to non-market scopes at tier Unusual/Extreme (incl. fixed-band
        extreme); kill-switched; capped per ET day; the reason itself is 24h-cached
        + inflight-deduped by ``price_catalyst_service``. Returns None on any
        miss/failure — it must NEVER fabricate a driver, block, or fail the news
        card.
        """
        if scope == MARKET_SCOPE:
            return None
        if decision.price_band not in _CATALYST_TIERS:
            return None
        # The move must be measurable and non-trivial IN THE CURRENT QUOTE. When
        # the quote is unusable the gate carries a STALE σ-tier (last_price_band),
        # and the old `finite(...) or 0.0` would then fetch a paid catalyst for a
        # phantom "+0.0% move" and store a self-contradictory {tier:Unusual,
        # change_percent:0.0} card. A move that rounds to 0.00% is not worth
        # explaining.
        cp = finite((quote or {}).get("changePercentage"))
        if cp is None or round(cp, 2) == 0.0:
            return None
        if not getattr(settings, "PRICE_CATALYST_AI_ENABLED", True):
            return None
        if not self._claim_catalyst_budget(now, scope):
            logger.info(
                "Price-move catalyst day cap (%d distinct movers) reached — skipping %s",
                _CATALYST_DAILY_CAP, scope,
            )
            return None

        try:
            grounded = await get_price_catalyst_service().get_catalyst(
                scope, cp, "today",
            )
        except Exception as e:
            logger.warning(
                "Price-move catalyst failed for %s (%s: %s)",
                scope, type(e).__name__, e,
            )
            return None
        if grounded is None:
            return None
        # Normalise the fixed-band 'extreme' to the tier vocabulary so the stored
        # `tier` is always one of the documented Notable/Unusual/Extreme labels.
        tier = TIER_EXTREME if decision.price_band == BAND_EXTREME else decision.price_band
        return {
            "tier": tier,
            "change_pct": cp,
            "catalyst_tag": grounded.get("tag"),   # None ⇒ "no clear catalyst"
            "reason": grounded.get("reason") or "",
            # The OUTSIDE web sources the grounded search consulted
            # (``[{title, uri, publisher}]``). Carried alongside the block so the
            # caller can MERGE them into the card's `sources` list; stripped by
            # `_sanitize_price_move` (whitelist) so it never lands in the stored
            # `price_move` column.
            "web_sources": grounded.get("sources") or [],
        }

    def _consume_global_budget(self, now: datetime) -> bool:
        """Atomically take one unit of today's global generation budget.

        Keyed on the ET trading date, matching the per-scope cap. A UTC key
        rolls at 19:00 ET under EST — inside the 04:00-20:00 sweep window — so
        the global ceiling handed itself a second full day's budget every winter
        evening. A spend ceiling that resets mid-session is not a ceiling. The
        day is a plain parameter here, so this needs no migration.
        """
        try:
            result = self.supabase.rpc(
                "increment_ai_insight_budget",
                {
                    "p_day": now.astimezone(ET).date().isoformat(),
                    "p_limit": _GLOBAL_DAILY_CAP,
                },
            ).execute()
            count = result.data
            if isinstance(count, list):
                count = count[0] if count else None
            if count is None:
                # RPC unavailable (migration not applied yet). Fail OPEN but say
                # so loudly — silently disabling the spend ceiling is worse than
                # a noisy log, and the per-scope caps still bound the damage.
                logger.error(
                    "Global insight budget RPC returned nothing — spend ceiling "
                    "NOT enforced this cycle. Is migration 088 applied?"
                )
                return True
            if int(count) < 0:
                logger.warning(
                    "Global insight budget exhausted for today (cap=%d) — "
                    "skipping remaining regenerations", _GLOBAL_DAILY_CAP,
                )
                return False
            return True
        except Exception as e:
            logger.error(
                "Global insight budget check failed (%s: %s) — spend ceiling NOT "
                "enforced this cycle", type(e).__name__, e,
            )
            return True

    # ── The sweep ─────────────────────────────────────────────────────

    async def run_sweep(self, refresh_news: bool) -> Dict[str, int]:
        """One pass. ``refresh_news=True`` also force-pulls recent FMP articles."""
        now = datetime.now(timezone.utc)
        market_active = is_market_active()
        # Read ONCE per sweep so every scope in this pass is judged against the
        # same session, even if the pass straddles the 09:30 bell.
        phase = session_phase(now)
        scopes = await self._universe()
        if not scopes:
            return {}

        # 1. Quotes — ONE batch-quote call for the whole universe (plus the index).
        symbols = [s for s in scopes if s != MARKET_SCOPE] + [MARKET_INDEX_SYMBOL]
        quotes_by_symbol: Dict[str, Dict[str, Any]] = {}
        try:
            for row in await self.fmp.get_batch_quotes_bulk(symbols):
                sym = row.get("symbol")
                if sym:
                    quotes_by_symbol[str(sym).upper()] = row
        except Exception as e:
            logger.warning(
                "Insight sweep quote fetch failed (%s: %s) — continuing with "
                "news-only signals", type(e).__name__, e,
            )
        market_quote = quotes_by_symbol.get(MARKET_INDEX_SYMBOL, {})
        market_change = market_quote.get("changePercentage")

        # 1b. σ (daily-return volatility) for the volatility-relative move tier —
        #     read from the daily-precomputed cache (keyed on ^GSPC for the market
        #     scope). Best-effort: a miss yields None → the gate falls back to the
        #     fixed band for that scope. get_sigmas_bulk never raises into the sweep.
        sigmas = await self.vol.get_sigmas_bulk(symbols)

        # 2. News — force-refresh so a story that broke minutes ago is visible.
        #    The 6h read TTL on ticker_news_cache would otherwise hide it.
        if refresh_news:
            await self._refresh_news(scopes)

        # 3. Corpora — ONE Supabase query for every scope.
        corpora = await asyncio.to_thread(self.news.get_cached_bulk, scopes, 25)
        # Bound every scope's corpus to its DYNAMIC window (prefer 24h, fall back to
        # 48h only when the scope has no news in 24h) via the shared selector. The
        # window is applied HERE, before both the materiality fingerprint (via
        # `decide`) and generation (via `generate_and_store`), so the card content
        # matches the badge the endpoint derives from the SAME selector: a scope
        # with fresh news summarises just the 24h corpus and is badged "24h". A
        # scope with no news in 48h yields an empty corpus → `no_corpus` → the
        # deterministic "Latest headlines" fallback, not an over-claiming AI card.
        corpora = {
            scope: select_recent_corpus(rows, now)[0]
            for scope, rows in corpora.items()
        }

        # 4. Evaluate the gate for every scope.
        states = await asyncio.to_thread(self._load_state, scopes)
        cycle_start = current_close_cycle_start(now)

        pending: List[Tuple[str, Decision]] = []
        touches: List[str] = []
        skips: List[Tuple[str, Decision]] = []
        reasons: Counter = Counter()

        for scope in scopes:
            is_market = scope == MARKET_SCOPE
            decision = decide(
                scope=scope,
                corpus=corpora.get(scope, []),
                quote=market_quote if is_market else quotes_by_symbol.get(scope),
                state=states.get(scope),
                market_change_percent=market_change,
                close_cycle_start=cycle_start,
                now=now,
                model=INSIGHT_MODEL,
                market_active=market_active,
                is_market_scope=is_market,
                sigma_daily=(
                    sigmas.get(MARKET_INDEX_SYMBOL) if is_market
                    else sigmas.get(scope)
                ),
                session_phase=phase,
            )
            reasons[decision.reason] += 1
            if decision.action == ACTION_GENERATE:
                pending.append((scope, decision))
            elif decision.action == ACTION_TOUCH:
                touches.append(scope)
            else:
                skips.append((scope, decision))

        await asyncio.to_thread(self._record_skips, skips, now)

        # A scope we just re-verified as unchanged is NOT stale — the card is
        # provably still correct. Extend its freshness so the UI doesn't flip to
        # "Catching up…" on every quiet ticker. Costs one batched UPDATE, no LLM.
        verified = [
            scope for scope, d in skips if d.reason == "fingerprint_unchanged"
        ]
        await self.insights.mark_verified_current(verified, market_active)

        # 5. Ceiling touches — free, no LLM.
        for scope in touches:
            await self.insights.touch(scope, market_active)
        if touches:
            # The state row's close_cycle MUST advance too. Without it the gate
            # re-reads the old cycle every pass and re-touches the same scopes
            # every 5 minutes forever — the "once per trading day" ceiling would
            # not actually exist, and the reasons histogram would be permanently
            # `cycle_touch`. One batched write, not one per scope.
            await asyncio.to_thread(self._mark_cycle_touched, touches, now)

        # 6. Admit by priority: the biggest moves first, then the market card.
        pending.sort(key=lambda p: (p[0] != MARKET_SCOPE, -p[1].score))
        admitted = pending[:_PER_CYCLE_REGEN_CAP]
        dropped = len(pending) - len(admitted)
        if dropped > 0:
            # Never silently truncate: a dropped scope re-trips next cycle, but
            # the operator must be able to see that the cap is binding.
            logger.warning(
                "Insight sweep per-cycle cap hit: %d/%d regenerations admitted, "
                "%d deferred to the next cycle", len(admitted), len(pending), dropped,
            )

        sem = asyncio.Semaphore(_GEN_CONCURRENCY)
        generated = 0

        async def _run(scope: str, decision: Decision) -> bool:
            async with sem:
                # Order matters: CLAIM FIRST, then spend budget.
                # The reverse leaks the global budget — every scope whose claim
                # is lost (another instance won it, a cap bound, or the RPC is
                # unavailable) would still have debited a generation it never
                # performed, so the daily ceiling would exhaust itself without
                # producing a single card.
                is_market = scope == MARKET_SCOPE
                if not await asyncio.to_thread(
                    self._claim, scope, now, is_market
                ):
                    return False
                if not await asyncio.to_thread(self._consume_global_budget, now):
                    # Budget exhausted after we took the claim: release it so the
                    # scope isn't parked for the full stale window, and re-trip
                    # next cycle.
                    await asyncio.to_thread(
                        self._release_claim, scope, now, "global_budget_exhausted"
                    )
                    return False
                card = None
                error = None
                try:
                    # Explain-the-move: for a per-ticker Unusual/Extreme move, fetch
                    # the grounded "why" (web search) and fold it into the card.
                    # Gated + day-capped + kill-switched + 24h-cached; None on any
                    # failure, and never blocks or fails the news card. Called
                    # INSIDE the try so a shutdown-cancel during the (seconds-long)
                    # catalyst search still reaches the finally that releases the
                    # claim — outside it, the claim parked for the full stale window
                    # after every deploy.
                    price_move = await self._maybe_price_move(
                        scope, decision, now, quotes_by_symbol.get(scope),
                    )
                    # When the move is STILL big but the catalyst was merely
                    # unavailable this cycle (budget spent / transient error /
                    # kill-switch), PRESERVE any existing "why it moved" block
                    # rather than wiping a still-valid, still-24h-cached explanation
                    # to NULL. Clear it only when the move is no longer big.
                    move_still_big = decision.price_band in _CATALYST_TIERS
                    preserve_price_move = price_move is None and move_still_big
                    card = await self.insights.generate_and_store(
                        scope=scope,
                        corpus=corpora.get(scope, []),
                        inputset_id=decision.inputset_id or "",
                        price_band=decision.price_band,
                        trigger_reason=decision.reason,
                        quote=(
                            market_quote if scope == MARKET_SCOPE
                            else quotes_by_symbol.get(scope)
                        ),
                        market_active=market_active,
                        price_move=price_move,
                        preserve_price_move=preserve_price_move,
                        # Merge the catalyst's outside web sources into the card's
                        # `sources` list (None when no big-move catalyst ran).
                        catalyst_sources=(price_move or {}).get("web_sources"),
                    )
                except asyncio.CancelledError:
                    # A deploy/shutdown cancels the sweeper mid-generation.
                    # CancelledError is a BaseException, so `except Exception`
                    # missed it and the claim + one budget unit leaked until
                    # migration 088's 120s stale-claim steal reclaimed them.
                    # Release explicitly, then re-raise so cancellation still
                    # propagates and the loop actually stops.
                    error = "cancelled"
                    logger.info("Insight generation cancelled for %s (shutdown)", scope)
                    raise
                except Exception as e:
                    error = f"{type(e).__name__}: {e}"
                    logger.error(
                        "Insight generation raised for %s: %s",
                        scope, error, exc_info=True,
                    )
                finally:
                    # `finally`, not the happy path: the claim must be released
                    # even when the generation is cancelled, or the scope stays
                    # locked for the full stale window after every deploy.
                    # Shielded so the release itself survives the cancellation
                    # that triggered it.
                    await asyncio.shield(
                        asyncio.to_thread(
                            self._finish_claim, scope, now, decision,
                            card is not None, error,
                        )
                    )
                return card is not None

        if admitted:
            results = await asyncio.gather(
                *[_run(s, d) for s, d in admitted], return_exceptions=True
            )
            generated = sum(1 for r in results if r is True)

        # 7. Proactive per-article enrichment (news pass only — new rows arrive
        #    only via _refresh_news). Enrich each scope's whole windowed corpus so
        #    the feed shows bullets + sentiment on scroll, not just the top few.
        #    After generation so card latency is untouched; self-limiting + bounded.
        enriched_rows = enrich_deferred = 0
        if refresh_news:
            enriched_rows, enrich_deferred = await self._enrich_windows(
                corpora, scopes, now
            )

        logger.info(
            "Insight sweep (%s) scopes=%d generated=%d touched=%d deferred=%d "
            "enriched=%d enrich_deferred=%d "
            "market=%s active=%s phase=%s reasons=%s",
            "news+price" if refresh_news else "price",
            len(scopes), generated, len(touches), dropped,
            enriched_rows, enrich_deferred,
            f"{market_change:+.2f}%" if isinstance(market_change, (int, float)) else "n/a",
            market_active, phase, dict(reasons.most_common(8)),
        )
        return {
            "scopes": len(scopes), "generated": generated,
            "touched": len(touches), "deferred": dropped,
            "enriched": enriched_rows, "enrich_deferred": enrich_deferred,
        }

    async def _refresh_news(self, scopes: List[str]) -> None:
        """Pull recent articles for every scope, bounded concurrency."""
        sem = asyncio.Semaphore(5)

        async def _one(scope: str) -> int:
            async with sem:
                try:
                    # Defaults on purpose: a narrower limit or lookback than the
                    # cold fetch makes the cache decay to today-only (see
                    # news_cache_service.refresh_scope_news).
                    return await self.news.refresh_scope_news(scope)
                except Exception as e:
                    logger.warning(
                        "News refresh failed for %s: %s: %s",
                        scope, type(e).__name__, e,
                    )
                    return 0

        results = await asyncio.gather(
            *[_one(s) for s in scopes], return_exceptions=True
        )
        written = sum(r for r in results if isinstance(r, int))
        logger.info(
            "Insight sweep refreshed news for %d scopes (%d rows written)",
            len(scopes), written,
        )


# ── Singleton + lifespan loop ─────────────────────────────────────────

_sweeper: Optional[InsightSweeper] = None


def get_insight_sweeper() -> InsightSweeper:
    global _sweeper
    if _sweeper is None:
        _sweeper = InsightSweeper()
    return _sweeper


PRICE_INTERVAL_SECONDS = 300      # 5 min
NEWS_EVERY_N_CYCLES = 3           # => news refresh every 15 min


async def run_insight_sweeper_loop() -> None:
    """Lifespan task. Cancelled on shutdown by ``app/main.py``."""
    # Stagger behind the existing 30/45/120s pre-warmers so startup isn't a
    # thundering herd against FMP.
    await asyncio.sleep(150)
    sweeper = get_insight_sweeper()
    cycle = 0
    logger.info(
        "Insight sweeper started (price=%ds, news every %d cycles, model=%s)",
        PRICE_INTERVAL_SECONDS, NEWS_EVERY_N_CYCLES, INSIGHT_MODEL,
    )
    while True:
        try:
            if is_market_active():
                await sweeper.run_sweep(refresh_news=(cycle % NEWS_EVERY_N_CYCLES == 0))
                cycle += 1
            else:
                logger.debug("Insight sweeper idle — market closed")
        except asyncio.CancelledError:
            logger.info("Insight sweeper cancelled")
            raise
        except Exception as e:
            logger.error(
                "Insight sweep cycle failed: %s: %s", type(e).__name__, e,
                exc_info=True,
            )
        # Jitter so multiple instances don't align their sweeps.
        await asyncio.sleep(PRICE_INTERVAL_SECONDS * random.uniform(0.85, 1.15))
