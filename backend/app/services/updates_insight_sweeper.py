"""
Updates-screen AI Insight sweeper.

Runs inside the FastAPI lifespan (registered in ``app/main.py``). Three passes:

  * PRICE pass  (every 5 min, while ``is_market_active()``)  — one ``batch-quote``
    call for the whole universe, then re-evaluate the materiality gate for every
    scope. Catches a big move before any headline lands.
  * NEWS pass   (every 15 min, while ``is_market_active()``) — force-refresh recent
    articles from FMP into ``ticker_news_cache`` (bypassing its 6h read TTL), then
    re-evaluate. Catches a breaking story.
  * CRYPTO pass (every 30 min, while the market is CLOSED) — the same sweep
    restricted to the coins in the universe, news refresh included. Coins trade
    and make news around the clock; before this pass a coin's card was frozen
    from Friday 20:00 ET to Monday 04:00 ET while its timeline stayed fresh
    (TestFlight, ETH, 2026-09-02). Runs with closed-market semantics (1h
    cooldown, 4h soft TTL) so it can never trip ``is_stale``.

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
                      scope and for a ticker inside its EARNINGS WINDOW
                      (earnings_window_service, D-1..D+2), 10 per ticker
                      otherwise; enforced BOTH in the pure gate and,
                      authoritatively, by `claim_updates_insight_scope`. Feed
                      both from `daily_cap_for` with the SAME `earnings_window`
                      or the DB silently overrules the gate.)
  per-scope attempt cap (updates_materiality.attempt_cap_for — the daily cap plus
                      a failure allowance; derived, so it can never sit below the
                      success ceiling it is meant to protect)
  pre-market reserve  (updates_materiality.premarket_cap_for — keeps two thirds
                      of the allowance for the rest of the trading day, 09:30-
                      20:00 ET; also holds in the small hours before a session,
                      where the crypto pass runs)
  report-day allowance (updates_materiality.AFTER_CLOSE_EARNINGS_RESERVE — +6 on
                      a ticker's earnings date, held back through the regular
                      session while the results are pending, 30-minute cooldown
                      after the close; the same `daily_cap_for(report_day=…)`
                      feeds the gate AND the claim RPC)
  failure cooldown    (a scope whose last generation FAILED waits one cooldown,
                      so a repeatable rejection cannot spend the failure
                      allowance within the hour)
  per-cycle cap       (_PER_CYCLE_REGEN_CAP, priority-ordered by move size)
  concurrency         (_GEN_CONCURRENCY)
  durable global cap  (_GLOBAL_DAILY_CAP, enforced in Postgres)
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.services.coingecko_adapter import crypto_base_symbol
from app.services.crypto_names import display_name_for_row
from app.services.earnings_window_service import (
    earnings_gate_inputs,
    earnings_is_hot,
    earnings_statuses_for,
    et_date,
    symbols_in_earnings_window,
)
from app.services.news_cache_service import (
    MARKET_SCOPE,
    get_news_cache_service,
    is_crypto_scope,
)
from app.services.news_insight_service import (
    INSIGHT_MODEL,
    get_news_insight_service,
    select_recent_corpus,
)
from app.services.asset_class import resolve_asset_class
from app.services.jump_web_search_service import (
    JumpCandidate,
    get_jump_web_search_service,
)
from app.services.ticker_report_cache import current_close_cycle_start
from app.services.volatility_cache_service import get_volatility_cache_service
from app.config import settings
from app.services.updates_materiality import (
    ACTION_GENERATE,
    ACTION_TOUCH,
    JUMP_TIERS,
    Decision,
    attempt_cap_for,
    daily_cap_for,
    decide,
    jump_band,
    move_score,
    mwcb_in_force,
)
from app.utils.market_hours import ET, SESSION_PREMARKET, is_market_active, session_phase
from app.services.price_service import (
    current_session_quote,
    price_source,
    session_change_percent,
)

logger = logging.getLogger(__name__)

# The market instrument whose move drives the market card and the anti-stampede guard.
#
# ⚠️ RE-ARMS THE MWCB GUARD. `^GSPC` is outside the FMP licence, so `market_change` was
# permanently `None` — and the Market-Wide Circuit Breaker check in
# `updates_materiality._decide_inner` reads `mkt is not None and abs(mkt)/100 >= 0.07`,
# which fails OPEN. That guard exists so a ≥7% market crash produces ONE macro card
# instead of N per-ticker restatements of the same story; disarmed, a real crash day
# would emit up to `_PER_CYCLE_REGEN_CAP` (8) Gemini cards per cycle. A live cost AND
# quality regression that no test could see, because "no card" and "guard never fired"
# look identical on a calm tape.
#
# Also the symbol the daily σ precompute writes (`main.py`), which the same gate reads
# for its volatility-relative move tier.
MARKET_INDEX_SYMBOL = "SPY"

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

# The move tiers that earn a price-move ALERT, the jump reserve and the jump's web
# search: `updates_materiality.JUMP_TIERS` (Unusual, Extreme and the fixed-band
# BAND_EXTREME, so a thin-history / newly-listed name — σ unavailable → fallback
# band, precisely the population most prone to violent moves — is not silently
# treated as ordinary). Which scopes are jumping is decided ONCE per sweep by
# `jump_band` from the current-session quote, never from `decision.price_band`
# (a stale `last_price_band` when the quote is unusable, None on `no_corpus`).
_CATALYST_TIERS = JUMP_TIERS

# Skip reasons after which the card is "as current as policy allows": the sweeper
# HAS evaluated the scope and no later cycle will regenerate it until a KNOWN
# boundary — the ET day roll for `daily_cap` / `attempt_cap`, the 09:30 bell for
# `premarket_reserved` (which the crypto pass can now trip from midnight, so a coin
# could otherwise read "checking for updates" for the whole pre-market). Re-stamping
# them (`mark_verified_current`) is what stops such a scope promising a refresh that
# policy has already ruled out (TestFlight, ORCL 2026-09-11: capped at 10:46 ET,
# "checking" at 12:46). `cooldown` is deliberately ABSENT: that scope WILL
# regenerate within 15 minutes, so the flag is telling the truth there. `no_corpus`
# / `mwcb_market_only` / `gate_error` are not verdicts about the card at all.
#
# `earnings_reserved` joins them for the same reason as `premarket_reserved`: a
# report-day ticker held at its ordinary cap through the regular session has been
# checked, and policy has ruled out a refresh until a known moment (the close, or
# the results landing). `failure_cooldown` is ABSENT like `cooldown`: that scope
# WILL retry within one cooldown.
_VERIFIED_CURRENT_REASONS = frozenset({
    "fingerprint_unchanged", "daily_cap", "attempt_cap", "premarket_reserved",
    "earnings_reserved",
})

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
# two Railway instances); a durable RPC is a follow-up.
_ENRICH_DAILY_CAP = 1200

# ── Jump mode (2026-10-28) ────────────────────────────────────────────
# A JUMPER (an Unusual/Extreme move in the current session, `jump_band`) gets its
# FMP news pulled on every 5-minute price tick instead of every third — at most
# this many extra pulls per scope per ET day (one `news/stock` call each: about
# the first hour of a jump).
_JUMP_EXTRA_NEWS_PER_DAY = 12
# All jumpers together, per ET day, biggest move first: a broad sell-off makes most watched
# names jumpers at once, and these pulls are only a head start on the regular news pass (the
# plan's cost bound, +60 FMP calls a day, review 2026-10-29).
_JUMP_EXTRA_NEWS_TOTAL_PER_DAY = 60
# One `ticker_move` alert attempt per jumper per this many seconds in a process
# (`_alert_jumper`); the dispatcher's per-user, per-ET-day dedup is the real
# once-a-day guarantee.
_ALERT_MEMO_SECONDS = 1800
# Gate verdicts after which a web search could not reach a card today, so none is bought.
# The rest (GENERATE, fingerprint_unchanged, cycle_touch, cooldown, failure_cooldown) can:
# a NEW set of pages is the gate's one-shot trigger once the cooldown has passed.
_WEB_NOT_ELIGIBLE = frozenset({
    "daily_cap", "attempt_cap", "premarket_reserved", "earnings_reserved",
    "mwcb_market_only", "gate_error", "no_corpus",
})


def _move_direction(quote: Optional[Dict[str, Any]], now: datetime) -> Optional[str]:
    """"up" / "down" — the sign of the current session's move, as the jump search names it
    (`JumpWebSearchService.context`) — or None when there is no usable move."""
    change = session_change_percent(quote, now)
    if change is None or round(change, 2) == 0.0:
        return None
    return "up" if change > 0 else "down"

_STATE_TABLE = "updates_insight_state"


class InsightSweeper:
    def __init__(self) -> None:
        self.supabase = get_supabase()
        self.fmp = get_fmp_client()
        self.news = get_news_cache_service()
        self.insights = get_news_insight_service()
        self.vol = get_volatility_cache_service()
        # The jump search (2026-10-28). Read with `getattr(self, "web", None)`: the suite's
        # sweeper stubs never run this `__init__`, so they can never reach Brave.
        self.web = get_jump_web_search_service()
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

    def _company_names(self, scopes: List[str]) -> Dict[str, str]:
        """``{TICKER: company name}`` for the swept universe, best-effort.

        Feeds ``select_recent_corpus(company_name=…)``, which decides whether an
        article is ABOUT this ticker. Without a name the subject filter can only
        match the literal SYMBOL in a headline — and headlines print "Oracle", not
        "ORCL" — so the parameter existed, was unit-tested, and was passed by NO
        production caller. Every article about a company whose name is not its
        ticker leaned entirely on FMP's tag ordering.

        ``watchlist_items`` is the right source because it is where the universe
        itself comes from (``get_top_watchlist_tickers``), so a swept ticker always
        has a row. Rows repeat per watcher; the first REAL name wins — a stored
        name that merely echoes the symbol (``"ETHUSD"``, from rows starred before
        the coin-name fix) is replaced by the coin's name or skipped, because a
        symbol as a "name" produced a variant nothing could match and starved the
        corpus to empty. Never raises: a missing name degrades the filter to
        symbol + tag order, which is exactly the pre-existing behaviour.
        """
        symbols = [s for s in scopes if s != MARKET_SCOPE]
        if not symbols:
            return {}
        try:
            result = (
                self.supabase.table("watchlist_items")
                .select("ticker, company_name")
                .in_("ticker", symbols)
                .execute()
            )
        except Exception as e:
            logger.warning(
                "Insight sweeper could not read company names (%s: %s) — the subject "
                "filter falls back to symbol + tag order for this sweep",
                type(e).__name__, e,
            )
            return {}
        names: Dict[str, str] = {}
        for row in (result.data or []):
            ticker = str(row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            # A symbol is not a name. Rows starred before the coin-name fix
            # (2026-09-11) persisted the ticker itself as `company_name`
            # ("ETHUSD"), and `setdefault` took whichever watcher's row came
            # first — so `company_name_variants` became ["ethusd"], nothing in
            # any headline matched, the coin's whole corpus filtered to EMPTY
            # and the card was never generated. `display_name_for_row` swaps a
            # coin's echo for its real name; an equity echo is skipped outright
            # so another watcher's real name can win.
            name = display_name_for_row(
                ticker, str(row.get("company_name") or "").strip()
            )
            if not name or name.upper() in {ticker, crypto_base_symbol(ticker)}:
                continue
            names.setdefault(ticker, name)
        return names

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

    def _claim(
        self,
        scope: str,
        now: datetime,
        is_market_scope: bool,
        earnings_window: bool = False,
        report_day: bool = False,
        jumping: bool = False,
    ) -> bool:
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
        disappeared. The two ceilings must be fed from the same function — and
        with the same ``earnings_window``, ``report_day`` and ``jumping`` (the
        jump reserve, 2026-10-28), which is why all three are threaded here.

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
        daily_cap = daily_cap_for(
            is_market_scope, earnings_window=earnings_window, report_day=report_day,
            jumping=jumping,
        )
        attempt_cap = attempt_cap_for(
            is_market_scope, earnings_window=earnings_window, report_day=report_day,
            jumping=jumping,
        )
        try:
            result = self.supabase.rpc(
                "claim_updates_insight_scope",
                {
                    "p_scope": scope,
                    "p_now": claim_now.isoformat(),
                    "p_stale_seconds": _CLAIM_STALE_SECONDS,
                    "p_attempt_cap": attempt_cap,
                    "p_daily_cap": daily_cap,
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
                    "Insight claim denied for %s (daily_cap=%d attempt_cap=%d "
                    "earnings_window=%s report_day=%s jumping=%s) — already at "
                    "ceiling, or claimed by another instance",
                    scope, daily_cap, attempt_cap, earnings_window, report_day, jumping,
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

    async def _notify_watchers(
        self,
        scope: str,
        decision: "Decision",
        card: Optional[Dict[str, Any]],
        now: datetime,
        quote: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Push a fresh insight to the people watching this ticker.

        ``card`` is None when the price guard refused the card — the alert never reads it
        (the body is the move itself, see `_alert_body`).

        Gated on the Unusual/Extreme materiality tiers (`_CATALYST_TIERS`). That is deliberate: a routine 0.4% drift already
        regenerates a card, and notifying on those would train users to ignore the app
        within a week. Only Unusual/Extreme earns an interruption.

        Dedup is per (user, ticker, ET trading date), so revisiting a scope later the
        same day — a re-trip, a retry, a second Railway instance — cannot buzz anyone
        twice.

        Never raises. A push failure must not turn a successfully generated card into
        a failed sweep.
        """
        if decision.price_band not in _CATALYST_TIERS:
            return
        # The move must be measurable IN THE CURRENT QUOTE. When the quote is
        # unusable, `decision.price_band` carries a STALE σ-tier (last_price_band),
        # so without this a ticker that moved 9% yesterday and has no quote today
        # would interrupt someone about a move that isn't happening — and the dedup
        # key is per DAY, so it would land as a genuinely new alert.
        #
        # Session-aware (`session_change_percent`): a change stamped with a PRIOR
        # session is None. Without this the 04:00 ET pre-market pass read yesterday's
        # whole-session move off a row whose price was still yesterday's close, and —
        # because the dedup key is per ET DAY — minted a second alert for a move that
        # had already fired: TER -10.4% at 09:57 Monday, then "TER -13.3%" at 04:03
        # Tuesday, both the same session (TestFlight, 2026-09-15). `run_sweep` blanks
        # such rows before they reach here; this is the direct-call guard.
        cp = session_change_percent(quote, now)
        if cp is None or round(cp, 2) == 0.0:
            return
        try:
            from app.services.notification_kinds import KIND_TICKER_MOVE, ticker_route
            from app.services.push_dispatch_service import (
                get_push_dispatch_service,
                trading_date_et,
            )

            # THE BODY MUST NEVER CONTRADICT THE MOVE THAT TRIGGERED THE ALERT.
            #
            # This used to send `card["headline"]` — a Gemini one-line synthesis of the
            # ticker's whole 24-48h news corpus — which has no connection to why the
            # price moved. Two failures came out of that, both observed in production:
            #
            #   * PLUG: "Plug Power beats Q2 estimates, raises revenue outlook" sent as
            #     a price-move alert. True of REVENUE (+5.4%), while EPS missed by 80%,
            #     so it read as a flat contradiction of the app's own earnings chart.
            #   * PLUG again: a card built from ONE article — "FuelCell Energy Sinks 8%,
            #     Bloom Energy Falls 3%, Plug Power Drops 3%..." — yielding the headline
            #     "Hydrogen Stocks Face Selloff" on a day the trigger recorded PLUG
            #     **up 4.16%**. A bearish sector claim attached to a bullish move.
            #
            # It then carried the grounded web-search catalyst, which was retired on
            # 2026-10-02 with Google Search grounding (its terms forbid caching a grounded
            # answer and sending it to anyone but the user who asked). The body is now
            # DETERMINISTIC and always true: the size and direction of the move plus a
            # pointer to the ticker, where the news card holds the coverage.
            body = self._alert_body(scope, cp)

            await get_push_dispatch_service().notify_watchers(
                ticker=scope,
                # The MOVE goes in the title, not just the ticker.
                #
                # A TestFlight tester read two CRM alerts on consecutive days as one
                # duplicate. They were not: the dedup key is `move:{TICKER}:{ET-date}`,
                # so those were two separate >=2-sigma sessions. What made them look
                # identical is that the title was the bare ticker on both, while the
                # bodies were two paraphrases of the same earnings story (the grounded
                # catalyst of the time, since retired).
                #
                # The percentage is the one field that always differs between two
                # distinct moves, and it is the fact the alert is actually about. It
                # also makes the lock-screen banner answer "how much?" without opening
                # the app. `cp` is validated non-None and non-zero above.
                title=f"{scope} {cp:+.1f}%",
                # NOT truncated here. This same string becomes `notification_events.body`,
                # which the Activity detail screen shows in full — a slice at the sender made
                # every stored body exactly 180 characters, ending mid-word. The lock-screen
                # bound is applied at the APNs boundary instead (`truncate_for_banner`).
                body=body,
                dedup_key=f"move:{scope}:{trading_date_et()}",
                preference_key="notify_watchlist_changes",
                # Routes the tap straight to this ticker (AppDelegate → deep link).
                #
                # Through `ticker_route`, like every other sender. This used to be a
                # hand-written `{"kind", "ticker"}` dict with NO `route` key at all, so the
                # tap only landed correctly because `NotificationRouter` defaults the family
                # to "ticker" — the app's most common notification working by client
                # accident. The old comment here also claimed `asset_type` was backfilled
                # from `watchlist_items`; that backfill was DELETED (the column is
                # client-writable and cross-tenant), so it was describing a rescue that no
                # longer existed.
                data=ticker_route(KIND_TICKER_MOVE, scope),
            )
        except Exception as e:
            logger.warning(
                "Push notify failed for %s (%s: %s) — the card outcome is unaffected",
                scope, type(e).__name__, e,
            )

    @staticmethod
    def _alert_body(scope: str, cp: float) -> str:
        """The `ticker_move` alert text for a move of `cp` percent: the move plus a
        pointer to the ticker. Never a cause — nothing deterministic knows one, and the
        news card is where the coverage lives. Pure, so testable without a dispatcher.
        """
        move = f"{'Up' if cp > 0 else 'Down'} {abs(cp):.1f}% in today's session"
        return f"{move}. Open {scope} for the latest coverage."

    def _extra_news_due(self, scopes: List[str], now: datetime) -> List[str]:
        """The jumpers whose news may be pulled on this price-only tick, in the order given
        (biggest move first), counted per scope per ET day (at most
        `_JUMP_EXTRA_NEWS_PER_DAY` each, and `_JUMP_EXTRA_NEWS_TOTAL_PER_DAY` across stocks).
        A coin keeps its own 12 but never draws the stocks' pool: before the bell coins are
        the only jumpers, and their pulls used to leave nothing for the stock that jumped at
        14:15 (review 2026-10-29, round 2). The counter is created lazily: the suite's
        sweeper stubs never run `__init__`."""
        ledger = self.__dict__.setdefault("_jump_news_ledger", {"day": None, "counts": {}})
        day = et_date(now)
        if ledger["day"] != day:
            ledger["day"] = day
            ledger["counts"] = {}
        counts = ledger["counts"]
        stock_total = sum(n for s, n in counts.items() if not is_crypto_scope(s))
        due = []
        for scope in scopes:
            coin = is_crypto_scope(scope)
            if not coin and stock_total >= _JUMP_EXTRA_NEWS_TOTAL_PER_DAY:
                continue
            if counts.get(scope, 0) < _JUMP_EXTRA_NEWS_PER_DAY:
                counts[scope] = counts.get(scope, 0) + 1
                stock_total += 0 if coin else 1
                due.append(scope)
        return due

    def _reserve_jumpers(self, jumpers: Dict[str, str], now: datetime) -> frozenset:
        """Every scope that has jumped today in this process: the ones whose caps carry
        the jump reserve, in the gate AND the claim.

        The reserve is a per-ET-day allowance, so it outlives the move: read off the live
        set, a pending reporter that jumped in the session and then faded before the close
        dropped back to its plain cap, and its in-session jump cards ate the after-close
        six — the release card was lost (review 2026-10-29; the ORCL 2026-09-10 failure
        `AFTER_CLOSE_EARNINGS_RESERVE` exists for). The alert, the faster news, the search
        and the MOVE rule follow the LIVE set. A restart forgets it. Created lazily: the
        suite's sweeper stubs never run `__init__`."""
        latch = self.__dict__.setdefault("_jump_reserve_latch", {"day": None, "scopes": set()})
        day = et_date(now)
        if latch["day"] != day:
            latch["day"] = day
            latch["scopes"] = set()
        latch["scopes"].update(jumpers)
        return frozenset(latch["scopes"])

    async def _alert_jumper(
        self,
        scope: str,
        decision: "Decision",
        band: str,
        now: datetime,
        quote: Optional[Dict[str, Any]],
    ) -> bool:
        """Send a jumper's `ticker_move` alert whatever happened to its card.

        The alert used to be sent only from inside a regeneration, so a scope that
        could not regenerate never alerted: ORCL rose ~12% on 2026-10-28 behind a spent
        daily cap and its watchers heard nothing. The body never reads the card (it is
        the move itself), so the card is not needed. The decision carries the LIVE jump
        band, because a skip carries None (`no_corpus`) or a stale band.

        At most one attempt per scope per `_ALERT_MEMO_SECONDS` in this process: the
        dispatcher's per-user, per-ET-day dedup key is the real once-a-day guarantee,
        and `notify_watchers` cannot tell "deduped" from "lookup failed", so the memo
        must expire rather than last the day. Never raises (`_notify_watchers` does not).
        """
        if not self._alert_due(scope, now):
            return False
        await self._notify_watchers(
            scope, replace(decision, price_band=band), None, now, quote=quote,
        )
        return True

    def _alert_due(self, scope: str, now: datetime) -> bool:
        """Claim this process's alert attempt for ``scope`` (see `_alert_jumper`)."""
        memo = self.__dict__.setdefault("_jump_alert_memo", {"day": None, "seen": {}})
        day = et_date(now)
        if memo["day"] != day:
            memo["day"] = day
            memo["seen"] = {}
        last = memo["seen"].get(scope)
        clock = time.monotonic()
        if last is not None and clock - last < _ALERT_MEMO_SECONDS:
            return False
        memo["seen"][scope] = clock
        return True

    def _jump_candidates(
        self,
        jumpers: Dict[str, str],
        quotes_by_symbol: Dict[str, Dict[str, Any]],
        corpora: Dict[str, List[Dict[str, Any]]],
        states: Dict[str, Dict[str, Any]],
        sigmas: Dict[str, Any],
        market_change: Any,
        cycle_start: datetime,
        now: datetime,
        market_active: bool,
        phase: str,
        earnings_scopes: frozenset,
        gate_inputs: Dict[str, Tuple[bool, bool, Optional[datetime]]],
    ) -> List[JumpCandidate]:
        """The jumpers worth a web search this tick, biggest move first.

        Equities only: a coin's change is a rolling 24 h window, and an ETF, fund,
        commodity or index moves on macro news the market card already carries. And
        only a scope that can still REGENERATE today — the gate is asked first, as if a
        new page set had just arrived (so an unchanged fingerprint cannot hide the caps);
        a cap, a reserve, an MWCB day or an empty corpus means a search could never reach
        a card. The company name is the FMP screener's (the quote row), never the
        client-writable watchlist name: it becomes the search query.
        """
        out: List[JumpCandidate] = []
        for scope, band in jumpers.items():
            quote = quotes_by_symbol.get(scope) or {}
            if is_crypto_scope(scope) or quote.get("isEtf") or quote.get("isFund"):
                continue
            if resolve_asset_class(scope) != "stock":
                continue
            change = session_change_percent(quote, now)
            if change is None:
                continue
            pre = decide(
                scope=scope, corpus=corpora.get(scope, []), quote=quote,
                state=states.get(scope), market_change_percent=market_change,
                close_cycle_start=cycle_start, now=now, model=INSIGHT_MODEL,
                market_active=market_active, is_market_scope=False,
                sigma_daily=sigmas.get(scope), session_phase=phase,
                earnings_window=scope in earnings_scopes,
                report_day=gate_inputs[scope][0],
                earnings_pending_today=gate_inputs[scope][1],
                earnings_reported_at=gate_inputs[scope][2],
                jumping=True,
                # "Could NEW pages regenerate the card now?" — without a web input the
                # fingerprint short-circuit answers `fingerprint_unchanged` before the caps
                # are read, and a capped jumper bought units that could never reach a card
                # (review 2026-10-29).
                web_refreshed_at=now,
                web_result_count=1,
            )
            if pre.reason in _WEB_NOT_ELIGIBLE:
                continue
            out.append(JumpCandidate(
                scope=scope, name=quote.get("name"), change_percent=float(change),
                score=move_score(band, change),
            ))
        return out

    @staticmethod
    def _jump_set(
        scopes: List[str],
        quotes_by_symbol: Dict[str, Dict[str, Any]],
        sigmas: Dict[str, Any],
        now: datetime,
    ) -> Dict[str, str]:
        """``{scope: move class}`` for every scope JUMPING in the current session.

        One definition (`updates_materiality.jump_band`) read off the session-aware
        change (`session_change_percent`: a change stamped with a prior session is
        None), so yesterday's move can never make a jump this morning. A row with NO
        stamp (the per-symbol profile fallback: a foreign listing, a sub-$50M microcap,
        a new ticker) cannot say which session it describes, so before the bell its
        equity change is not a jump either — at best it is yesterday's (review
        2026-10-29; pre-market moves are not jumps in v1). Never raises: a malformed
        quote row drops that scope, never the sweep.
        """
        out: Dict[str, str] = {}
        premarket = session_phase(now) == SESSION_PREMARKET
        for scope in scopes:
            if scope == MARKET_SCOPE:
                continue
            quote = quotes_by_symbol.get(scope)
            if not isinstance(quote, dict):
                continue
            if premarket and not quote.get("changeSession") and not is_crypto_scope(scope):
                continue
            try:
                band = jump_band(
                    session_change_percent(quote, now),
                    (sigmas or {}).get(scope),
                    quote.get("marketCap"),
                    is_market_scope=False,
                )
            except Exception as e:  # noqa: BLE001 — one bad row must not stop the sweep
                logger.warning(
                    "Jump check failed for %s (%s: %s) — not treated as jumping",
                    scope, type(e).__name__, e,
                )
                continue
            if band is not None:
                out[scope] = band
        return out

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

    async def run_sweep(
        self, refresh_news: bool, *, crypto_only: bool = False
    ) -> Dict[str, int]:
        """One pass. ``refresh_news=True`` also force-pulls recent FMP articles.

        ``crypto_only=True`` is the off-hours pass: the same sweep over just the
        coins in the universe. No MARKET scope, no equities, no index leg (so
        ``market_change`` is None and the equity-only MWCB guard is inert), no
        earnings lookup. Everything else — closed cooldown and TTLs, CoinGecko
        quotes, crypto-routed news refresh, claims, budgets — is the
        ordinary machinery reading ``is_market_active() == False``.
        """
        now = datetime.now(timezone.utc)
        market_active = is_market_active()
        # Read ONCE per sweep so every scope in this pass is judged against the
        # same session, even if the pass straddles the 09:30 bell.
        phase = session_phase(now)
        scopes = await self._universe()
        if crypto_only:
            scopes = [s for s in scopes if is_crypto_scope(s)]
        if not scopes:
            return {}

        # 1. Quotes — ONE batch-quote call for the whole universe (plus the index,
        #    which the crypto pass has no use for: SPY does not print at 02:00).
        symbols = [s for s in scopes if s != MARKET_SCOPE]
        if not crypto_only:
            symbols.append(MARKET_INDEX_SYMBOL)
        quotes_by_symbol: Dict[str, Dict[str, Any]] = {}
        prior_session = 0
        try:
            for row in await price_source(self).get_quotes_list(symbols):
                sym = row.get("symbol")
                if not sym:
                    continue
                # A change stamped with a PRIOR session is not a current move. Blanked
                # HERE, once, so the materiality gate, the card prompt's price line and
                # the alert gate all agree: pre-market, before a
                # ticker's first print, the screener still carries yesterday's close and
                # the row's change is yesterday's whole session. Read as "today" it
                # re-tripped the gate at 04:03 ET and minted a second `ticker_move`
                # alert for a move that fired the day before (TER, 2026-09-15). The
                # price stays — it is still the live price; only the move is unknown
                # until the session actually prints. Fails open when unstamped (crypto).
                current = current_session_quote(row, now)
                if current is not row:
                    prior_session += 1
                quotes_by_symbol[str(sym).upper()] = current
        except Exception as e:
            logger.warning(
                "Insight sweep quote fetch failed (%s: %s) — continuing with "
                "news-only signals", type(e).__name__, e,
            )
        market_quote = quotes_by_symbol.get(MARKET_INDEX_SYMBOL, {})
        market_change = market_quote.get("changePercentage")

        # 1b. σ (daily-return volatility) for the volatility-relative move tier —
        #     read from the daily-precomputed cache (keyed on SPY for the market
        #     scope). Best-effort: a miss yields None → the gate falls back to the
        #     fixed band for that scope. get_sigmas_bulk never raises into the sweep.
        sigmas = await self.vol.get_sigmas_bulk(symbols)

        # 1c'. JUMPERS — decided ONCE per sweep from the current-session move (2026-10-28,
        #      ORCL +12% behind a spent daily cap). This live set feeds the decoupled alert,
        #      the faster news pass, the web search and the MOVE rule; the gate's and the
        #      claim's jump reserve read `reserve_jumpers` (latched for the ET day). None on
        #      an MWCB day: the whole tape is moving.
        mwcb = mwcb_in_force(market_change)
        jumpers = {} if mwcb else self._jump_set(scopes, quotes_by_symbol, sigmas, now)
        # The caps' view: today's jumpers so far, so a reserve survives a fading move.
        reserve_jumpers = self._reserve_jumpers(jumpers, now)
        # Biggest move first wherever jumpers share an allowance: the faster news pass and the
        # decoupled alerts (a watcher gets a few watchlist alerts a day, and on a broad move
        # they must go to the biggest movers, not the most-watched names).
        jump_order = sorted(
            jumpers,
            key=lambda s: -move_score(
                jumpers[s], session_change_percent(quotes_by_symbol.get(s), now),
            ),
        )
        extra_news = 0

        # 1c. Earnings window — which tickers get the market-sized daily cap this
        #     ET day. ONE licensed calendar call per day, cached in the service;
        #     best-effort (an empty set boosts nobody). Through THIS sweeper's
        #     client, never the singleton: the suite's sweep stubs carry no client,
        #     and the hermetic guard fails the whole session on a real call.
        earnings_scopes: frozenset = frozenset()
        if not crypto_only:
            try:
                earnings_scopes = await symbols_in_earnings_window(now, fmp=self.fmp)
            except Exception as e:
                logger.warning(
                    "Earnings window unavailable this sweep (%s: %s) — no cap boost",
                    type(e).__name__, e,
                )
        boosted = sum(1 for s in scopes if s in earnings_scopes)

        # 1d. Earnings STATUS per equity ticker (reported / due today / upcoming) —
        #     the prompt's EARNINGS line, the report-day allowance and the one-shot
        #     "results just landed" trigger. Same snapshot as the boost; the hot
        #     refresh of today + the previous trading day only runs while a ticker
        #     in THIS universe still has results pending. Best-effort: a failure
        #     means no status, i.e. exactly the pre-2026-09-27 behaviour.
        statuses: Dict[str, Any] = {}
        if not crypto_only:
            equities = [
                s for s in scopes if s != MARKET_SCOPE and not is_crypto_scope(s)
            ]
            if equities:
                try:
                    statuses = await earnings_statuses_for(
                        now, fmp=self.fmp, symbols=equities,
                    )
                except Exception as e:
                    logger.warning(
                        "Earnings statuses unavailable this sweep (%s: %s) — no "
                        "earnings context", type(e).__name__, e,
                    )
                    statuses = {}
        today_et = et_date(now)
        gate_inputs = {
            scope: earnings_gate_inputs(statuses.get(scope), today_et)
            for scope in scopes
        }
        hot = {scope: earnings_is_hot(statuses.get(scope), today_et) for scope in scopes}

        # 2. News — force-refresh so a story that broke minutes ago is visible.
        #    The 6h read TTL on ticker_news_cache would otherwise hide it.
        if refresh_news:
            await self._refresh_news(scopes)
        elif not crypto_only and jumpers:
            # 2b. A JUMPER's news is pulled on every 5-minute tick, not every third: on
            #     2026-10-28 ORCL's press release sat in FMP for up to a news interval
            #     before the card could see it. Bounded per scope and in all per ET day,
            #     biggest move first.
            due = self._extra_news_due(jump_order, now)
            if due:
                extra_news = len(due)
                await self._refresh_news(due)

        # 3. Corpora — ONE Supabase query for every scope.
        corpora = await asyncio.to_thread(self.news.get_cached_bulk, scopes, 25)
        # Bound every scope's corpus to its DYNAMIC window (24h when it holds at
        # least MIN_CORPUS_ARTICLES stories about the scope, else widened to 48h —
        # and across a closed market to 72/96h — whenever widening adds articles)
        # via the shared selector. The window is applied HERE, before both the
        # materiality fingerprint (via `decide`) and generation (via
        # `generate_and_store`), so the card content matches the badge the
        # endpoint derives from the SAME selector: a well-covered scope summarises
        # just the 24h corpus and is badged "24h"; a thin one is badged with the
        # span it was really summarised over. A scope with no news in the widest
        # window yields an empty corpus → `no_corpus` → the deterministic "Latest
        # headlines" fallback, not an over-claiming AI card.
        #
        # `scope=` opts each TICKER corpus into the subject filter, so a sector wrap led
        # by two peers can no longer be the sole input for this ticker's card. MARKET
        # passes None — market coverage is about the market by definition.
        #
        # `company_name` is what lets the filter recognise "Oracle" in a headline as
        # ORCL. One extra query per sweep, shared by every scope.
        #
        # Holdings filings and law-firm solicitations are out of every corpus here (the
        # bulk read skips them before its cap; the selector drops them again), so they
        # are never summarised, cited or proactively enriched (the feed enriches a
        # visible one on demand), and since the fingerprint is the corpus's article ids,
        # a new filler row never re-keys a card. Its first deploy re-keys, once, every
        # scope whose window held filler (each regenerates under the usual caps).
        names = await asyncio.to_thread(self._company_names, scopes)
        corpora = {
            scope: select_recent_corpus(
                rows, now,
                scope=None if scope == MARKET_SCOPE else scope,
                company_name=names.get(scope),
            )[0]
            for scope, rows in corpora.items()
        }

        # 4. Evaluate the gate for every scope.
        states = await asyncio.to_thread(self._load_state, scopes)
        cycle_start = current_close_cycle_start(now)

        # 4a. The jump search (2026-10-28): for every EQUITY jumper that can still
        #     regenerate today, ask the web WHY it is moving. Its kept pages are a gate input
        #     (a new set is a one-shot trigger, never a fingerprint input) and a generation
        #     input (a fenced prompt block, and the first entries of the card's sources).
        #     After `_load_state`, so a capped scope is not searched; before the gate, so
        #     the gate sees this tick's pages. Brave is bounded at its timeout + 2 s.
        web = getattr(self, "web", None)
        web_on = web is not None and not crypto_only and web.enabled()
        web_counts: Dict[str, int] = {}
        if web_on and jumpers:
            candidates = self._jump_candidates(
                jumpers, quotes_by_symbol, corpora, states, sigmas, market_change,
                cycle_start, now, market_active, phase, earnings_scopes, gate_inputs,
            )
            if candidates:
                web_counts = await web.refresh(candidates, now)
        web_context: Dict[str, Tuple[Any, ...]] = {}
        web_refreshed: Dict[str, Optional[datetime]] = {}
        if web_on:
            for scope in scopes:
                if scope == MARKET_SCOPE:
                    continue
                # Pages explain a move in one direction: after a reversal they are not about
                # this move any more (review 2026-10-29).
                pages, refreshed_at = web.context(
                    scope, now,
                    direction=_move_direction(quotes_by_symbol.get(scope), now),
                )
                if pages:
                    web_context[scope] = pages
                    web_refreshed[scope] = refreshed_at

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
                earnings_window=scope in earnings_scopes,
                report_day=gate_inputs[scope][0],
                earnings_pending_today=gate_inputs[scope][1],
                earnings_reported_at=gate_inputs[scope][2],
                jumping=scope in reserve_jumpers,
                web_refreshed_at=web_refreshed.get(scope),
                web_result_count=len(web_context.get(scope, ())),
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
        # A scope CAPPED for the day (or reserved until the bell) was checked too
        # and is final until a known boundary, so it is stamped as well (see
        # _VERIFIED_CURRENT_REASONS).
        verified = [
            scope for scope, d in skips if d.reason in _VERIFIED_CURRENT_REASONS
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

        # 6. Admit by priority: the market card, then the URGENT scopes — tickers
        #    reporting today (or that reported on the previous trading day; the
        #    after-hours move of an after-close report is not in `changePercentage`,
        #    so `score` alone ranks the release behind every ordinary mover) AND big
        #    movers in a catalyst tier (their watchers' `ticker_move` alert waits on
        #    this card, so a peak earnings day must not starve a -15% name) — each
        #    group by move size.
        def _urgent(scope: str, decision: Decision) -> bool:
            # The LIVE jump set, never `decision.price_band`: that is the stale
            # `last_price_band` when the quote is unusable, so a ticker that moved
            # yesterday used to be ranked urgent all morning.
            return bool(hot.get(scope)) or scope in jumpers

        pending.sort(key=lambda p: (p[0] != MARKET_SCOPE, not _urgent(*p), -p[1].score))
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
                    self._claim, scope, now, is_market, scope in earnings_scopes,
                    gate_inputs.get(scope, (False, False, None))[0],
                    scope in reserve_jumpers,
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
                    # No "why it moved" block any more: it came from a grounded Google
                    # search, retired 2026-10-02 for the Grounding terms. `_store`
                    # writes NULL over any block a card still carries, so the next
                    # regeneration clears it.
                    pages = web_context.get(scope, ())
                    reason = decision.reason
                    if pages and "web (" not in reason:
                        reason = f"{reason} + web ({len(pages)})"
                    card = await self.insights.generate_and_store(
                        scope=scope,
                        corpus=corpora.get(scope, []),
                        inputset_id=decision.inputset_id or "",
                        price_band=decision.price_band,
                        trigger_reason=reason,
                        quote=(
                            market_quote if scope == MARKET_SCOPE
                            else quotes_by_symbol.get(scope)
                        ),
                        market_active=market_active,
                        # The prompt's clock is the moment of generation, not the
                        # sweep start (a sweep can run for minutes).
                        now=datetime.now(timezone.utc),
                        earnings=statuses.get(scope),
                        # Lets the price guard read "CoreWeave Gains 4%" as CRWV's price.
                        company_name=names.get(scope),
                        web_context=pages,
                        jumping=scope in jumpers,
                    )
                    if card is None:
                        # "conclusion_guard: figure $5,000" is a very different
                        # diagnosis from a Gemini outage; both used to read
                        # "generation returned no card".
                        pop_reason = getattr(self.insights, "pop_failure_reason", None)
                        if callable(pop_reason):
                            error = pop_reason(scope)
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
                # A fresh card for a WATCHED ticker that moved materially is the one
                # thing in this app worth interrupting someone for. Everything the
                # alert needs was just computed — the tier and the move — so notifying
                # costs one reverse lookup, not another pipeline.
                #
                # Deliberately NOT awaited into the sweep's critical path failure
                # modes: `_notify_watchers` never raises, and a push problem must not
                # mark a successfully generated card as failed.
                # Also after a price_guard rejection: a big mover's coverage is often
                # nothing BUT price talk, which is exactly the day the guard refuses the
                # card — and the alert never reads the card (its body is the move itself).
                price_refused = card is None and str(error or "").startswith("price_guard")
                if (card is not None or price_refused) and not is_market:
                    await self._notify_watchers(
                        scope, decision, card, now,
                        quote=quotes_by_symbol.get(scope),
                    )
                    if scope in jumpers:
                        # Counts as this sweep's alert attempt for the jumper (6b).
                        self._alert_due(scope, now)

                return card is not None

        if admitted:
            results = await asyncio.gather(
                *[_run(s, d) for s, d in admitted], return_exceptions=True
            )
            generated = sum(1 for r in results if r is True)

        # 6b. A JUMPER's alert, whatever happened to its card — capped, cooling down,
        #     refused, claimed by another instance, out of budget, or not admitted this
        #     cycle. 2026-10-28: ORCL rose ~12% behind a spent daily cap and alerted
        #     nobody, because the alert was only ever sent from inside a regeneration.
        #     Coins stay coupled to generation: their change is a rolling 24h window, so
        #     a decoupled alert would fire again after ET midnight for the same move.
        jump_alerts = 0
        if jumpers:
            decided: Dict[str, Decision] = dict(pending)
            decided.update(dict(skips))
            for scope in jump_order:
                band = jumpers[scope]
                if is_crypto_scope(scope):
                    continue
                decision = decided.get(scope) or Decision(
                    action=ACTION_TOUCH, reason="cycle_touch",
                )
                if await self._alert_jumper(
                    scope, decision, band, now, quotes_by_symbol.get(scope),
                ):
                    jump_alerts += 1

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
            "enriched=%d enrich_deferred=%d prior_session=%d earnings_boosted=%d "
            "earnings_status=%d earnings_hot=%d jumpers=%d jump_alerts=%d "
            "extra_news=%d web=%s mwcb=%s market=%s active=%s phase=%s reasons=%s",
            ("crypto " if crypto_only else "") + ("news+price" if refresh_news else "price"),
            len(scopes), generated, len(touches), dropped,
            enriched_rows, enrich_deferred, prior_session, boosted,
            len(statuses), sum(1 for v in hot.values() if v),
            len(jumpers), jump_alerts, extra_news, web_counts or "-", mwcb,
            f"{market_change:+.2f}%" if isinstance(market_change, (int, float)) else "n/a",
            market_active, phase, dict(reasons.most_common(8)),
        )
        return {
            "scopes": len(scopes), "generated": generated,
            "touched": len(touches), "deferred": dropped,
            "enriched": enriched_rows, "enrich_deferred": enrich_deferred,
            "earnings_boosted": boosted,
            "earnings_status": len(statuses),
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
# While the market is closed the loop still ticks every 5 min; the crypto-only
# pass runs on every tick that is at least this long after the previous one.
# 30 min, not 5: coins get the CLOSED cooldown (1h) anyway, so a faster tick
# would spend FMP calls on corpora that cannot regenerate yet.
CRYPTO_OFF_HOURS_INTERVAL_SECONDS = 1800


@dataclass
class _LoopState:
    """What the loop carries between ticks. A dataclass so a test can drive
    `_run_one_tick` directly instead of sleeping through the schedule."""
    cycle: int = 0
    # `time.monotonic()` of the last crypto pass START. -inf ⇒ the first tick
    # after boot that finds the market closed runs one immediately.
    last_crypto_pass: float = float("-inf")


async def _run_one_tick(
    sweeper: InsightSweeper, state: _LoopState, *, market_active: bool, monotonic: float
) -> Optional[str]:
    """One iteration of the loop body. Returns which pass ran ("market" /
    "crypto") or None when idle. Exceptions propagate to the loop's handler."""
    if market_active:
        await sweeper.run_sweep(refresh_news=(state.cycle % NEWS_EVERY_N_CYCLES == 0))
        state.cycle += 1
        return "market"
    if monotonic - state.last_crypto_pass >= CRYPTO_OFF_HOURS_INTERVAL_SECONDS:
        # Stamped BEFORE the sweep: a slow or raising pass must not re-trigger on
        # the very next 5-minute tick.
        state.last_crypto_pass = monotonic
        await sweeper.run_sweep(refresh_news=True, crypto_only=True)
        return "crypto"
    logger.debug("Insight sweeper idle — market closed")
    return None


async def run_insight_sweeper_loop() -> None:
    """Lifespan task. Cancelled on shutdown by ``app/main.py``.

    Ticks every ~5 min. While the market is active that is the price/news
    sweep; while it is closed every 30th minute is the crypto-only pass and the
    other ticks are idle. The equity cycle counter does not advance on a crypto
    pass, so the news cadence resumes exactly where it left off at 04:00 ET.
    """
    # Stagger behind the existing 30/45/120s pre-warmers so startup isn't a
    # thundering herd against FMP.
    await asyncio.sleep(150)
    sweeper = get_insight_sweeper()
    state = _LoopState()
    logger.info(
        "Insight sweeper started (price=%ds, news every %d cycles, crypto off-hours "
        "every %ds, model=%s)",
        PRICE_INTERVAL_SECONDS, NEWS_EVERY_N_CYCLES, CRYPTO_OFF_HOURS_INTERVAL_SECONDS,
        INSIGHT_MODEL,
    )
    while True:
        try:
            await _run_one_tick(
                sweeper, state,
                market_active=is_market_active(), monotonic=time.monotonic(),
            )
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
