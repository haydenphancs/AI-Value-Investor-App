"""
Research Service — Multi-Agent Deep Research Orchestrator.

Upgraded from single-pass Gemini prompt to a true agentic pipeline:
  1. Spawn a ResearchAgent with the chosen investor persona
  2. Agent autonomously gathers FMP data via Gemini function calling
  3. Agent produces the full TickerReportResponse JSON (matching Swift UI)
  4. Service stores the result in research_reports + ticker_report_data JSONB
  5. Service also extracts legacy fields (title, executive_summary, etc.) for
     backward compatibility with the research reports list view

On ANY failure → status = "failed", error_message saved to DB.
"""

import asyncio
import math
import copy
import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

from app.services.dcf_report_gate import report_dcf_source_matches
from app.config import settings
from app.database import get_supabase
from app.integrations.gemini import get_gemini_client
from app.integrations.fmp import get_fmp_client
from app.services.agents.research_agent import ResearchAgent
from app.services.agents.persona_config import get_persona_config
from app.services.agents.persona_scoring import compute_quality_score
from app.services.report_degradation import (
    DegradedReportError,
    report_degraded_reason,
)
from app.services.ticker_report_cache import (
    CACHE_SCHEMA_FLOOR,
    current_close_cycle_start,
    upsert_cached_report,
    _normalize_key,
)

logger = logging.getLogger(__name__)

# Shared cross-user cache for deep ticker_report_data. When any user has
# completed a Generate Analysis for the same (ticker, persona) within this
# window, subsequent Generate Analysis runs reuse that JSONB instead of
# re-running the agent. Each user still gets their own research_reports
# row and is still charged credits — credits buy access to premium
# analysis, not raw compute. Backed by idx_reports_ticker_persona_completed
# (added in migration 039).
SHARED_CACHE_TTL_HOURS = 6

# The only statuses a running worker may still write to. 'completed', 'failed' and
# 'deleted' are TERMINAL and owned by someone else (the completion write, the
# reconciliation sweep, the user's delete) — writing over them resurrects a row that
# has already been resolved and, in the 'deleted' case, already refunded. Mirrors the
# same guard on the conditional completion write in `generate_report`.
_ACTIVE_STATUSES = ["pending", "processing"]


# ── Global bounded concurrency + same-(ticker,persona) agent-run dedup ──────
# /research/generate fires reports fire-and-forget with only a per-user cap, so
# a multi-user burst would otherwise spawn an unbounded number of agent runs all
# hitting Gemini/FMP at once. Two module-level guards bound the blast radius:
#
#   * _AGENT_SEMAPHORE — at most settings.MAX_CONCURRENT_AGENT_RUNS agent runs
#     execute concurrently process-wide. This is THE knob that pins Gemini/FMP
#     load to your API tier: size N ≈ tier_TPM / per-report-tokens. Followers
#     (below) do NOT consume a slot.
#
#   * _AGENT_INFLIGHT — concurrent requests for the SAME (ticker, persona) share
#     ONE agent run; followers await the leader's result and return an
#     independent deep copy. Collapses the hot-ticker "everyone opens AAPL after
#     earnings" Gemini herd to a single pipeline in the window BEFORE the shared
#     cross-user cache is populated. (The persona-neutral FMP collection was
#     already deduped by ticker via ticker_data_cache._INFLIGHT; this adds the
#     missing dedup for the per-persona Gemini agent run — the agentic loop + Stage A
#     + 12-22 Stage-B narratives (12 fixed + 2 per critical factor, factors capped at 5)
#     + 2 syntheses.)
_AGENT_SEMAPHORE: Optional[asyncio.Semaphore] = None
_AGENT_INFLIGHT: Dict[str, "asyncio.Future"] = {}


class _AgentRun:
    """Per-leader bookkeeping the followers share: whether the leader holds its slot yet,
    and how many followers are attached. Held by REFERENCE (never re-read by key) so a
    follower finishing after its leader's `_AGENT_INFLIGHT.pop` can never touch the
    record of a NEW leader under the same key."""

    __slots__ = ("started", "followers", "members")

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.followers = 0
        # The report ids riding on this run (leader + followers that passed one), so an
        # abandonment check can ask whether ANY of their rows is still live — not merely
        # whether followers are attached (F08-6): a follower deleted while queued is
        # still attached, and the leader used to run a full pipeline for it.
        self.members: set = set()


_AGENT_RUNS: Dict[str, _AgentRun] = {}


class ReportAbandonedError(Exception):
    """The report was deleted (or otherwise left the active statuses) while it sat queued
    for an agent slot, and nobody else is waiting on the run.

    Raised by the deep path's `before_run` hook right after the slot is acquired so the
    slot is released immediately instead of after a full ~17-Gemini / ~20-FMP run whose
    completion write is a guaranteed no-op. NOT a failure: the row is already terminal
    and the DELETE already refunded it, so callers log it at info and skip the
    mark-failed / refund / Sentry path.
    """


class ReportPipelineTimeoutError(TimeoutError):
    """The whole agent run overran `RESEARCH_PIPELINE_TIMEOUT_SECONDS`.

    A subclass of the builtin so every `except asyncio.TimeoutError` caller is unaffected
    (the same reasoning as `GeminiTimeoutError`), with a MESSAGE — the bare `TimeoutError`
    `asyncio.wait_for` raises has an empty `str()`, so the failed card said "market data
    provider unavailable" (the generic `"timeout" in cls` arm maps to FMP_UNAVAILABLE) and
    the Sentry line read "TimeoutError: " with nothing after it. `classify_exception` maps
    this class to REPORT_TIMED_OUT.
    """

    def __init__(self, ticker: str, persona_key: str, seconds: float) -> None:
        super().__init__(
            f"report pipeline for {ticker}/{persona_key} exceeded {seconds:.0f}s ceiling"
        )
        self.ticker = ticker
        self.persona_key = persona_key
        self.seconds = seconds


def _get_agent_semaphore() -> asyncio.Semaphore:
    """Lazily build the process-wide semaphore inside the running loop."""
    global _AGENT_SEMAPHORE
    if _AGENT_SEMAPHORE is None:
        _AGENT_SEMAPHORE = asyncio.Semaphore(
            max(1, settings.MAX_CONCURRENT_AGENT_RUNS)
        )
    return _AGENT_SEMAPHORE


async def _run_agent_deduped(
    ticker: str, persona_key: str, run_callable, on_started=None, key_prefix: str = "",
    before_run=None, member_id: Optional[str] = None,
):
    """Run the agent pipeline under the global semaphore, sharing ONE execution
    across concurrent same-(ticker, persona) callers.

    Leader: acquire a semaphore slot, run `run_callable()`, publish the result
    to any followers. Follower: await the leader's result and return a deep copy
    (each caller then stamps its own persona-weighted quality_score and writes
    its own research_reports row). A leader failure propagates to its followers
    — they fail + refund, and the next attempt re-leads — mirroring
    ticker_data_cache.get_or_collect. Followers never hold a semaphore slot, so
    a 300-deep hot-ticker herd consumes exactly one unit of Gemini/FMP work.

    `on_started` (async, optional) fires right after the leader acquires its slot —
    i.e. when real agent work begins, NOT while queued — so the caller can stamp
    processing_started_at and the reconciliation sweep can age the report off
    work-start rather than enqueue time. A FOLLOWER's `on_started` fires at the same
    moment (or at attach time if the leader is already running): its result arrives
    when the leader's does, ≤ the pipeline ceiling after that stamp, so the started
    clock is exactly as true for it. It used to stay NULL for followers, which put
    them on the client's 1,800 s queued clock while the server kept them for 11,400 s
    — under queue pressure a healthy follower was shown "failed" and its Retry
    deleted a report that then completed, unrefunded.

    `before_run` (async, optional, LEADER only) fires after `on_started` and may raise
    `ReportAbandonedError` to give the slot straight back. It receives the frozen set of
    `member_id`s currently riding on the run (the leader's and every attached follower's
    that passed one) so it can judge liveness across ALL of them in one read; a raise is
    honoured only while no follower is attached, because an attached follower that the
    hook could not see still needs the run.

    `member_id` (optional) is this caller's report id, registered on the run for the
    duration of its participation — see `_AgentRun.members`.

    `key_prefix` namespaces the dedup key. This is a CORRECTNESS requirement, not
    a nicety: the deep `/research/generate` pipeline and the shallower direct
    `/stocks/{ticker}/report` pipeline produce DIFFERENT reports for the same
    (ticker, persona). Sharing one namespace would let a deep-research caller
    attach to a direct-path leader and receive the shallow report while being
    charged DEEP_RESEARCH_COST and having it stamped into research_reports.
    The deep path passes "" and keeps its historical key format byte-for-byte,
    so its dedup behaviour is provably unchanged; the direct path passes "direct".
    """
    key = f"{ticker.upper().strip()}::{persona_key}"
    if key_prefix:
        key = f"{key_prefix}::{key}"

    inflight = _AGENT_INFLIGHT.get(key)
    if inflight is not None:
        run = _AGENT_RUNS.get(key)
        if run is not None:
            run.followers += 1
            if member_id:
                run.members.add(member_id)
        shielded = asyncio.shield(inflight)
        try:
            if on_started is not None and run is not None:
                # Stamp when the LEADER holds its slot — never while it is still queued
                # (a follower's row must not read "started" ahead of the work), and not
                # at all if the leader dies queued (the shared future settles first and
                # the follower fails through the normal refund path with a NULL stamp,
                # exactly as before).
                waiter = asyncio.ensure_future(run.started.wait())
                try:
                    await asyncio.wait(
                        {waiter, shielded}, return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    waiter.cancel()
                if run.started.is_set():
                    await on_started()
            shared = await shielded
            return copy.deepcopy(shared)
        finally:
            if run is not None:
                run.followers = max(0, run.followers - 1)
                if member_id:
                    run.members.discard(member_id)
            if shielded.done() and not shielded.cancelled():
                # Mark a leader failure as retrieved when this follower was cancelled
                # before awaiting it, or asyncio logs "exception was never retrieved".
                shielded.exception()

    loop = asyncio.get_running_loop()
    fut: "asyncio.Future" = loop.create_future()
    run = _AgentRun()
    if member_id:
        run.members.add(member_id)
    _AGENT_INFLIGHT[key] = fut
    _AGENT_RUNS[key] = run
    try:
        async with _get_agent_semaphore():
            run.started.set()
            if on_started is not None:
                await on_started()
            if before_run is not None:
                judged = frozenset(run.members)
                try:
                    await before_run(judged)
                except ReportAbandonedError:
                    # The hook judged every member it was shown. Run anyway only for a
                    # follower it could NOT have judged: one attached without a
                    # `member_id`, or one that arrived after the snapshot was taken.
                    follower_members = run.members - ({member_id} if member_id else set())
                    unseen = run.followers - len(follower_members)
                    newcomers = run.members - judged
                    if unseen > 0 or newcomers:
                        logger.info(
                            "agent run %s: leader abandoned but %d follower(s) attached "
                            "that the liveness check could not see — running for them",
                            key, unseen + len(newcomers),
                        )
                    else:
                        raise
            result = await run_callable()
        if not fut.done():
            fut.set_result(result)
        return result
    except asyncio.CancelledError:
        # CancelledError is BaseException (NOT Exception), so it would skip the
        # handler below and leave `fut` unresolved — every follower's
        # `await inflight` would then hang forever (report stuck "processing",
        # credits never refunded) when the fire-and-forget leader task is
        # cancelled on a Railway redeploy / GC. Hand followers a NORMAL
        # exception so they fail through the standard refund path, then re-raise
        # to honor our own cancellation.
        if not fut.done():
            fut.set_exception(RuntimeError("leader agent run was cancelled"))
            fut.exception()  # mark retrieved — see the Exception arm
        raise
    except Exception as e:
        if not fut.done():
            fut.set_exception(e)
            # Mark retrieved (ticker_data_cache.py idiom). The leader re-raises `e` on
            # its own frame and is the only party that has to see it; with NO follower
            # attached the future is garbage-collected unread and asyncio logs "Future
            # exception was never retrieved" at ERROR — a second, duplicate Sentry
            # event per abandoned / timed-out / failed leader on top of the caller's own
            # logger.error. Followers are unaffected: they await `asyncio.shield(fut)`,
            # whose outer future re-reads the stored exception.
            fut.exception()
        raise
    finally:
        _AGENT_INFLIGHT.pop(key, None)
        _AGENT_RUNS.pop(key, None)


class ResearchService:
    def __init__(self):
        self.supabase = get_supabase()
        self.gemini = get_gemini_client()
        self.fmp = get_fmp_client()

    # ── Main Pipeline ─────────────────────────────────────────────────────

    async def generate_report(
        self,
        report_id: str,
        ticker: str,
        persona_key: str,
        user_id: str,
    ) -> bool:
        """
        Full multi-agent pipeline:
          1. Spawn ResearchAgent with persona
          2. Agent runs agentic loop (data gathering + analysis)
          3. Store full TickerReportResponse + legacy fields
          4. Decrement user credits

        Returns True when the row was written 'completed' (delivered), False when the
        result was dropped because the row had already been reconciled or deleted — the
        caller must not render a PDF onto a row that never completed.
        """
        start = datetime.now(timezone.utc)

        try:
            # Mark as processing
            await self._update_status_async(report_id, "processing", 2, "Initializing research agent...")

            persona = get_persona_config(persona_key)

            # ── Shared cross-user cache lookup ────────────────────────
            # If any user has a fresh completed report for this exact
            # (ticker, persona), reuse the ticker_report_data JSONB
            # instead of running the agent again. The new row is still
            # owned by `user_id` and credits still get decremented below
            # — only the expensive AI/FMP work is deduplicated.
            await self._update_status_async(
                report_id, "processing", 5, "Checking shared cache..."
            )
            cached = await self._lookup_shared_cache(ticker, persona_key)

            if cached is not None:
                logger.info(
                    f"Shared cache HIT for {ticker}/{persona_key} — "
                    f"reusing existing analysis (report {report_id}, "
                    f"user {user_id})"
                )
                await self._update_status_async(
                    report_id, "processing", 90, "Loading cached analysis..."
                )
                ticker_report_data = cached
            else:
                logger.info(
                    f"Shared cache MISS for {ticker}/{persona_key} — "
                    f"running fresh agent (report {report_id})"
                )

                # Create the agent
                agent = ResearchAgent(
                    persona_key=persona_key,
                    fmp=self.fmp,
                    gemini=self.gemini,
                )

                # Progress callback bound to this report
                async def on_progress(progress: int, step: str):
                    await self._update_status_async(report_id, "processing", progress, step)

                # Run the full agentic pipeline under a hard ceiling. A hung
                # Gemini/FMP read would otherwise park this task forever,
                # leaving the report stuck in "processing" — charged but never
                # refunded. On timeout, asyncio.TimeoutError propagates to the
                # except below → _run_research_task refunds the user's credits.
                #
                # _run_agent_deduped bounds global concurrency (semaphore) and
                # collapses a concurrent same-(ticker,persona) herd to ONE run;
                # followers get a deep copy and proceed to write their own row.
                async def _run_agent():
                    try:
                        return await asyncio.wait_for(
                            agent.run(
                                ticker=ticker,
                                progress_cb=on_progress,
                            ),
                            timeout=settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError as e:
                        # Typed and worded: see `ReportPipelineTimeoutError`.
                        raise ReportPipelineTimeoutError(
                            ticker, persona_key, settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS
                        ) from e

                async def _on_started():
                    # Stamp when this report ACTUALLY starts running (slot
                    # acquired) so the reconciliation sweep ages it off
                    # work-start, not the queue-inflated created_at.
                    await asyncio.to_thread(self._mark_processing_started, report_id)

                async def _before_run(members):
                    # Deleted while queued? A DELETE only flips the row (and refunds);
                    # nothing told this task. It used to hold a slot for a full run
                    # whose completion write was a guaranteed no-op — thirty users
                    # giving up on a deep queue cost paying users ~20 min more of it.
                    # Judged over EVERY row riding on this run (F08-6): a follower that
                    # was deleted while queued is still attached, and "somebody is
                    # attached" used to be enough to run the pipeline for nobody.
                    ids = set(members) | {report_id}
                    if not await asyncio.to_thread(self._any_still_active, ids):
                        raise ReportAbandonedError(
                            f"report {report_id} ({ticker}/{persona_key}) and every row "
                            f"sharing its run ({len(ids)}) left the active statuses while queued"
                        )

                # Truthful while QUEUED: the row used to keep "Checking shared cache..."
                # from here until the slot came, which under a deep queue is minutes
                # (F08-7). Progress stays at 5 so the agent's first tick (also 5) never
                # regresses the bar.
                await self._update_status_async(
                    report_id, "processing", 5, "Waiting for an analysis slot...",
                )
                ticker_report_data = await _run_agent_deduped(
                    ticker, persona_key, _run_agent,
                    on_started=_on_started, before_run=_before_run, member_id=report_id,
                )

            # A DEGRADED report is a non-delivery. When Gemini is unavailable — quota
            # circuit open, 429s exhausted, a 5xx — or simply returns something
            # unparseable, Stage A falls back to an empty shell and every Stage B narrative
            # uses its sentinel. The deterministic numerics still merge in, so the result
            # VALIDATES against TickerReportResponse and renders: which is why, untagged,
            # it was charged 20 credits, written status='completed' (so the except-only
            # refund in _run_research_task never fired), AND seeded into
            # ticker_report_cache — from where it was served FREE to every other user for
            # the rest of the close cycle and RE-SOLD at 20 credits to the next research
            # buyer via _lookup_shared_cache.
            #
            # Raising is what makes the refund fire: _run_research_task only refunds inside
            # `except Exception`. The `except` below stamps status='failed' and re-raises;
            # 'failed' is a claimable status, so claim_and_mark_failed still refunds
            # exactly once. This mirrors the direct path (ticker_report.py's `_degraded`
            # check leaving `delivered=False`).
            degraded = report_degraded_reason(ticker_report_data)
            if degraded:
                logger.warning(
                    "Report %s DEGRADED (%s) for %s/%s — not delivering, not caching; "
                    "credits will be refunded",
                    report_id, degraded, ticker, persona_key,
                )
                raise DegradedReportError(degraded, ticker=ticker, persona=persona_key)

            # Extract legacy fields for backward compatibility
            await self._update_status_async(report_id, "processing", 92, "Saving report...")

            # Persona-weighted overall score (deterministic, server-side).
            # Overrides whatever quality_score the AI emitted in Stage A so
            # the headline number is reproducible and reflects this persona's
            # weighting philosophy, not LLM variance. Mutating
            # ticker_report_data here keeps the cached JSONB consistent —
            # iOS reads quality_score from the same dict.
            persona_score = compute_quality_score(persona_key, ticker_report_data)
            ticker_report_data["quality_score"] = persona_score

            generation_time = int((datetime.now(timezone.utc) - start).total_seconds())

            # Build update payload
            update_data = {
                "status": "completed",
                "progress": 100,
                "current_step": "Complete",

                # Full TickerReportResponse stored as JSONB
                "ticker_report_data": ticker_report_data,

                # Legacy fields for list view + backward compatibility.
                # `full_report` is the raw research_findings from the agent's
                # tool-calling phase. On a cache hit we don't have an agent
                # instance to read from, so fall back to None — the cached
                # ticker_report_data is the source of truth either way.
                "title": self._extract_title(ticker_report_data, ticker, persona),
                "executive_summary": ticker_report_data.get("executive_summary_text"),
                "full_report": (
                    agent.research_findings[:10000]
                    if cached is None and agent.research_findings
                    else None
                ),

                # Extract structured components from the report
                "investment_thesis": self._extract_thesis(ticker_report_data),
                "pros": ticker_report_data.get("core_thesis", {}).get("bull_case", []),
                "cons": ticker_report_data.get("core_thesis", {}).get("bear_case", []),
                "moat_analysis": self._extract_moat(ticker_report_data),
                "valuation_analysis": self._extract_valuation(ticker_report_data),
                "risk_assessment": self._extract_risk(ticker_report_data),
                "key_takeaways": self._extract_takeaways(ticker_report_data),
                # `action_recommendation` is deliberately NO LONGER WRITTEN (2026-08-14).
                # It emitted a literal "Buy"/"Sell"/"Hold"/"Watch" verdict on a named
                # security, was persisted and served, and was rendered by no View — so it
                # carried the full App Review 5.1.1(ix)/3.1.5 "this is advice, not
                # information" risk with zero product benefit, and was one `Text(...)` away
                # from shipping that verdict undisclaimered. The DB column still exists and
                # simply goes NULL on new rows; drop it in a later migration if you want.
                # If a Buy/Sell verdict is ever wanted again, it needs a disclaimer surface
                # first — see the Technical Meter, which has one.

                # Scoring
                "overall_score": ticker_report_data.get("quality_score"),
                "fair_value_estimate": (
                    (
                        ticker_report_data.get("_scoring_inputs")
                        or ticker_report_data.get("key_vitals")
                        or {}
                    ).get("valuation") or {}
                ).get("fair_value"),

                # Generation metadata
                "generation_time_seconds": generation_time,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

            # CONDITIONAL completion write. Guard against a charge-refund-AND-
            # deliver race: a report can sit queued (status='processing',
            # is_refunded=False) behind the agent semaphore while its created_at
            # ages past the reconciliation sweep's 900s threshold; the sweep then
            # refunds it + flips status='failed'. Without this guard, the
            # unconditional update below would revive that already-refunded row to
            # 'completed' → the user keeps their refunded credits AND gets the
            # report. The eq/in_ filters make the write a no-op once reconciled.
            def _write_completion():
                # Off the loop (F08-8): a sync UPDATE carrying the ~200 KB report JSONB,
                # plus httpx's json.dumps of it, used to park the event loop for the
                # whole round trip on every delivery.
                return (
                    self.supabase.table("research_reports")
                    .update(update_data)
                    .eq("id", report_id)
                    .eq("is_refunded", False)
                    .in_("status", ["pending", "processing"])
                    .execute()
                )
            result = await asyncio.to_thread(_write_completion)
            if not result.data:
                # The sweep already claimed + refunded this report. Do NOT
                # deliver (no cache seed) — the user was made whole; dropping the
                # result is the correct outcome.
                logger.warning(
                    f"Report {report_id} already reconciled (refunded) before "
                    f"completion — skipping delivery to avoid double-resolve "
                    f"(persona={persona_key}, ticker={ticker})"
                )
                return False

            # Seed the direct-path cache so /stocks/{ticker}/report users
            # benefit from this expensive agentic run for the next 24h.
            # Best-effort — failures inside upsert_cached_report are logged
            # but never raised, so a Supabase blip can't fail the report.
            await upsert_cached_report(ticker, persona_key, ticker_report_data)

            # Credits were charged upfront in /research/generate
            # (CreditService.try_charge). No deduction here. Refunds on
            # failure are handled by _run_research_task in research.py —
            # this function only ever signals success.

            logger.info(
                f"Report {report_id} completed in {generation_time}s "
                f"(persona={persona_key}, ticker={ticker})"
            )

            await self._notify_report_ready(
                report_id=report_id,
                user_id=user_id,
                ticker=ticker,
                persona=persona,
                persona_key=persona_key,
            )
            return True

        except ReportAbandonedError as e:
            # Not a failure: the row is already terminal and its DELETE refunded it. No
            # status stamp (it would be a no-op on a deleted row anyway), no ERROR log.
            logger.info("Report %s abandoned before its run: %s", report_id, e)
            raise
        except Exception as e:
            logger.error(f"Report generation failed: {e}", exc_info=True)
            # The STRUCTURED, REDACTED blob — the same shape `_run_research_task`'s
            # terminal CAS writes — not `str(e)`. Two things were wrong with the raw
            # string: (1) an FMP httpx error carries `…&apikey=<key>` in its URL and
            # `_make_request` re-raises the original for any status it does not type, so
            # this write put the production key in `research_reports.error_message`, and
            # the 3 s `/status` poll served it to the phone until the CAS overwrote it;
            # (2) a Gemini failure named the vendor ("stage_a_GeminiQuotaError") on the
            # user's screen in the same window. `error_body_from_exception` redacts
            # BEFORE truncating and yields a `user_message` the splitter serves instead.
            from app.api.error_response import error_body_from_exception
            await self._update_status_async(
                report_id, "failed", 0,
                error_message=json.dumps(error_body_from_exception(
                    e, ticker=ticker, persona=persona_key, step="generate_report",
                )),
            )
            raise

    def _any_still_active(self, report_ids) -> bool:
        """Whether ANY of the rows is still in an active status and unrefunded — one read
        for a whole dedup run (sync; call via `asyncio.to_thread`). Same fail-open contract
        as `_is_still_active`: a read error, or no rows at all (hard-deleted / stale
        replica), answers True."""
        ids = sorted({str(r) for r in (report_ids or []) if r})
        if not ids:
            return True
        try:
            result = (
                self.supabase.table("research_reports")
                .select("id, status, is_refunded")
                .in_("id", ids)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "any_still_active read failed for %d report(s) (%s: %s) — assuming active",
                len(ids), type(e).__name__, e,
            )
            return True
        rows = list(getattr(result, "data", None) or [])
        if not rows:
            return True
        return any(
            (row or {}).get("status") in _ACTIVE_STATUSES and not bool((row or {}).get("is_refunded"))
            for row in rows
        )

    def _is_still_active(self, report_id: str) -> bool:
        """Whether the row is still in an active status and unrefunded (sync; call via
        `asyncio.to_thread`). FAILS OPEN: a read error answers True, because the cost of
        a wrong "abandon" is a report the user paid for and never receives, while the
        cost of a wrong "active" is one wasted run."""
        try:
            result = (
                self.supabase.table("research_reports")
                .select("status, is_refunded")
                .eq("id", report_id)
                .limit(1)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "is_still_active read failed for %s (%s: %s) — assuming active",
                report_id, type(e).__name__, e,
            )
            return True
        rows = list(getattr(result, "data", None) or [])
        if not rows:
            # Hard-deleted, or a read against a stale replica. Treat as active for the
            # same fail-open reason.
            return True
        row = rows[0] or {}
        return (row.get("status") in _ACTIVE_STATUSES) and not bool(row.get("is_refunded"))

    # ── Report-ready push ─────────────────────────────────────────────────

    async def _notify_report_ready(
        self,
        *,
        report_id: str,
        user_id: str,
        ticker: str,
        persona: Any,
        persona_key: str,
    ) -> None:
        """Tell the user their report is done.

        A deep report takes minutes. The client polls `/research/reports/{id}/status`
        every 3s for at most 3 minutes and then gives up — so a user who backgrounds the
        app mid-run has no way to learn it finished except by opening the app and
        looking. This is the notification with the clearest justification in the whole
        system: they pressed a button, paid 20 credits, and asked to be told.

        PLACEMENT IS THE CORRECTNESS ARGUMENT, and it is why this is not a scheduled job:

          * AFTER the conditional completion write. If the reconciliation sweep already
            claimed and refunded this report, that write is a no-op and the function has
            already returned — so a refunded report can never notify.
          * AFTER the `DegradedReportError` raise. A degraded report is refunded, not
            delivered, and never reaches here.

        Do NOT move this earlier "to notify sooner". Earlier means notifying about
        reports that are about to be refunded.

        Never raises: a push failure must not turn a delivered report into a failed one.
        The `except` in `generate_report` stamps status='failed' and re-raises, which
        triggers the refund — so an escaping push error would refund a report the user
        actually received.
        """
        try:
            from app.services.notification_kinds import KIND_RESEARCH_COMPLETE
            from app.services.push_dispatch_service import get_push_dispatch_service

            symbol = (ticker or "").upper()
            # `display_name` is the LENS label ("The Quality Compounder"), not a person's
            # name — "Your The Quality Compounder report" does not parse, so it is used
            # as a standalone prefix rather than inlined into a possessive.
            lens = (getattr(persona, "display_name", "") or "").strip()
            await get_push_dispatch_service().notify_users(
                [user_id],
                kind=KIND_RESEARCH_COMPLETE,
                title=f"{symbol} analysis is ready",
                # Informational, never directive. This copy is a surface a regulator
                # reads (FINRA/SEC name push notifications explicitly as a digital-
                # engagement practice), so it states what happened and says nothing
                # about what to do with it — no "consider", no "act now", no verdict.
                body=(
                    f"{lens} — tap to read the full report."
                    if lens else "Tap to read your full report."
                ),
                # The report id is unique, so this is once-EVER with no date component —
                # unlike the market-event keys, a regenerated report is a different row
                # and legitimately notifies again.
                dedup_key=f"report:{report_id}",
                route={
                    # FLAT SCALARS ONLY — iOS AnyCodable yields "" for anything nested.
                    "route": "report",
                    "report_id": str(report_id),
                    # WHICH report. A ticker can hold one report per persona — six PLUG rows
                    # in the tester's feed — so `report_id` alone is not enough for the
                    # client, whose report screen is keyed by (ticker, persona). Without this
                    # every one of those six opened the same default persona's report.
                    "persona": persona_key,
                    "ticker": symbol,
                    "asset_type": "stock",
                },
            )
        except Exception as e:
            logger.warning(
                "Report-ready push failed for report=%s user=%s ticker=%s (%s: %s) — "
                "the report itself was delivered normally",
                report_id, user_id, ticker, type(e).__name__, e,
            )

    # ── Status Helper ─────────────────────────────────────────────────────

    async def _update_status_async(
        self,
        report_id: str,
        status: str,
        progress: int,
        current_step: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """`_update_status` off the event loop. Every progress tick, the 'Saving report...'
        stamp and the failure stamp are Supabase round trips; called synchronously from
        `generate_report` each one parked the ONE event loop (every SSE frame, every
        poll, every other report's tick) for a full round trip — ~6 stalls per report,
        ~1,800 across a 300-deep herd (F08-8). The sync form stays for the tests that
        drive it directly and for callers already inside a thread."""
        await asyncio.to_thread(
            self._update_status, report_id, status, progress, current_step, error_message,
        )

    def _update_status(
        self,
        report_id: str,
        status: str,
        progress: int,
        current_step: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        """Advance a report that is STILL IN THE PIPELINE. Never resurrect a terminal one.

        The `.in_(_ACTIVE_STATUSES)` filter is the whole point, and it closes two
        distinct resurrections that an unconditional `UPDATE ... WHERE id = ?` caused:

          1. **The user deleted an in-flight report.** `delete_report` sets
             status='deleted' (a TERMINAL state no sweep can reach) and refunds. If the
             worker then failed, this wrote status='failed' over it and the row came
             BACK in the Reports list — as a failed card with a Retry button, for a
             report the user had already deleted and been refunded for.
          2. **The reconciliation sweep already refunded it.** The sweep marks an
             orphan 'failed' + is_refunded=True. A still-running worker's next progress
             callback then wrote status='processing' straight back over that terminal
             state, so the row looked live again, re-entered the sweep's candidate set,
             and its own completion write (guarded on is_refunded=False) could never
             land — a permanently "processing" row that no longer owed anything.

        Both are silent: no exception, no log, just a row in a state nothing owns.
        """
        update: Dict[str, Any] = {"status": status, "progress": progress}
        if current_step:
            update["current_step"] = current_step
        if error_message:
            # Choke point: every writer of this column passes here, and the column is
            # served verbatim by `/status`. Redaction at the log boundary does not
            # cover the database, so it is applied to the persisted text itself.
            from app.log_redaction import redact_secrets
            update["error_message"] = redact_secrets(error_message)
        try:
            self.supabase.table("research_reports").update(update).eq(
                "id", report_id
            ).in_("status", _ACTIVE_STATUSES).execute()
        except Exception as e:
            logger.error(f"Status update failed for {report_id}: {e}")

    def _mark_processing_started(self, report_id: str) -> None:
        """Stamp processing_started_at = now when the report acquires its agent
        slot (real work begins). The reconciliation sweep ages a STARTED report
        off this, not the queue-inflated created_at. Guarded `is null` so a
        re-entry can't move it. Best-effort: swallow errors (incl. the column
        not existing before migration 070 is applied) — never break generation."""
        try:
            # The step rides along in the same write: a FOLLOWER's row (and a queued
            # leader's, for a moment) otherwise sat at "Checking shared cache..." for the
            # whole run, because only the leader's agent ticks its own row (F08-7). The
            # leader's copy is overwritten milliseconds later by agent.run's first tick.
            self.supabase.table("research_reports").update({
                "processing_started_at": datetime.now(timezone.utc).isoformat(),
                "current_step": "Analysis in progress...",
            }).eq("id", report_id).in_(
                "status", _ACTIVE_STATUSES
            ).is_("processing_started_at", "null").execute()
        except Exception as e:
            logger.warning(
                "mark_processing_started failed for %s: %s: %s",
                report_id, type(e).__name__, e,
            )

    # ── Shared Cross-User Cache ───────────────────────────────────────────

    async def _lookup_shared_cache(
        self, ticker: str, persona_key: str
    ) -> Optional[Dict[str, Any]]:
        """Return any user's completed ticker_report_data for (ticker, persona)
        within SHARED_CACHE_TTL_HOURS, or None.

        This is the cross-user dedup path: when User B requests Generate
        Analysis for the same ticker+persona that User A just paid for,
        User B reuses A's expensive AI/FMP output instead of re-running
        the agent. User B still gets a fresh research_reports row owned
        by them, and is still charged credits.

        Backed by `idx_reports_ticker_persona_completed` (migration 039).
        Runs the synchronous Supabase call in a thread to avoid blocking
        the event loop.
        """
        # Normalize the lookup key the same way the write side
        # (upsert_cached_report / the inserted research_reports row) does, so
        # a non-normalized caller can never miss a cache row that exists under
        # the canonical (UPPER ticker, lower persona) key. No-op for the
        # current callers (ticker is already .upper(), persona validated
        # lowercase) — purely defensive.
        ticker, persona_key = _normalize_key(ticker, persona_key)
        # Honor the same schema floor as ticker_report_cache so a payload-
        # shape change (e.g. new required price_action fields) invalidates
        # cross-user reuse just like it invalidates the dedicated cache.
        # Without this gate, a stale report from a pre-deploy user would
        # be silently served to every subsequent caller until TTL expiry.
        # Close-aligned (not rolling): reuse only reports completed in the
        # current trading-close cycle, so the first viewer after a new close
        # regenerates instead of inheriting a prior-close report.
        cutoff = max(
            current_close_cycle_start(), CACHE_SCHEMA_FLOOR
        ).isoformat()

        def _query():
            try:
                result = self.supabase.table("research_reports").select(
                    "ticker_report_data, completed_at"
                ).eq(
                    "ticker", ticker
                ).eq(
                    "investor_persona", persona_key
                ).eq(
                    "status", "completed"
                ).gte(
                    "completed_at", cutoff
                ).not_.is_(
                    "ticker_report_data", "null"
                ).order(
                    "completed_at", desc=True
                ).limit(1).execute()

                if result.data and result.data[0].get("ticker_report_data"):
                    blob = result.data[0]["ticker_report_data"]
                    # Never re-sell a degraded shell. Nothing writes one any more (the
                    # generate path raises before the completion write), but rows written
                    # BEFORE that fix are still sitting in the table inside the close
                    # cycle — and this lookup is a paid path, so serving one charges a
                    # second user 20 credits for the same blank report. Treat it as a miss
                    # and regenerate.
                    stale_degraded = report_degraded_reason(blob)
                    if stale_degraded:
                        logger.warning(
                            "Shared cache row for %s/%s is DEGRADED (%s) — treating as a "
                            "miss rather than re-selling it",
                            ticker, persona_key, stale_degraded,
                        )
                        return None
                    if not report_dcf_source_matches(blob):
                        logger.info(
                            "Shared cache row for %s/%s was built under the other "
                            "DCF_ENABLED setting — treating as a miss", ticker, persona_key,
                        )
                        return None
                    return blob
                return None
            except Exception as e:
                # Cache lookup failures should never block generation —
                # log and fall through to a fresh agent run.
                logger.warning(
                    f"Shared cache lookup failed for {ticker}/{persona_key}: "
                    f"{type(e).__name__}: {e}"
                )
                return None

        return await asyncio.to_thread(_query)

    # ── Legacy Field Extractors ───────────────────────────────────────────
    # These extract simplified fields from the full TickerReportResponse
    # for the research reports list view and backward compatibility.

    def _extract_title(
        self, data: Dict[str, Any], ticker: str, persona
    ) -> str:
        """Generate a concise report title."""
        company = data.get("company_name", ticker)
        return f"{persona.display_name} Analysis: {company}"

    def _extract_thesis(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract investment thesis from core_thesis."""
        thesis = data.get("core_thesis", {})
        if not thesis:
            return None
        return {
            "summary": data.get("executive_summary_text", ""),
            "key_drivers": thesis.get("bull_case", [])[:3],
            "risks": thesis.get("bear_case", [])[:3],
            "time_horizon": "3-5 years",
            "conviction_level": self._derive_conviction(data),
        }

    def _extract_moat(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract moat analysis from the internal score inputs + moat_competition."""
        # None-safe: slots are Optional and can be None. Legacy "key_vitals"
        # fallback covers reports cached before the key was renamed.
        moat_vital = (
            data.get("_scoring_inputs") or data.get("key_vitals") or {}
        ).get("moat") or {}
        moat_comp = data.get("moat_competition") or {}
        if not moat_vital:
            return None
        return {
            "moat_rating": (moat_vital.get("overall_rating", "none") or "none").capitalize(),
            "moat_sources": [t.get("label", "") for t in moat_vital.get("tags", [])],
            "moat_sustainability": moat_comp.get("durability_note", ""),
            "competitive_position": moat_comp.get("competitive_insight", ""),
            "barriers_to_entry": [],
        }

    def _extract_valuation(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract valuation analysis from the internal score inputs."""
        # None-safe: slots are Optional and can be None. Legacy "key_vitals"
        # fallback covers reports cached before the key was renamed.
        val = (
            data.get("_scoring_inputs") or data.get("key_vitals") or {}
        ).get("valuation") or {}
        if not val:
            return None
        # NEUTRAL wording only (documents/research/dcf-methodology-v1.md §5, hard rule 4). This
        # used to persist and serve "Undervalued" / "Overvalued" and "N% upside" — a verdict on
        # a model value (and, with no DCF, on a synthetic upside mapped from the snapshot rating).
        # Now: no rating label, and the gap stated as price vs the model value when one exists.
        fair_value = val.get("fair_value")
        price = val.get("current_price")
        gap_text = None
        if (isinstance(fair_value, (int, float)) and isinstance(price, (int, float))
                and not isinstance(fair_value, bool) and fair_value > 0 and price > 0
                and math.isfinite(fair_value) and math.isfinite(price)):
            gap = (price / fair_value - 1) * 100
            gap_text = ("Price in line with the model estimate" if abs(gap) < 0.5 else
                        f"Price {abs(gap):.0f}% {'below' if gap < 0 else 'above'} the model estimate")
        return {
            "valuation_rating": None,
            "key_metrics": {},
            "historical_context": "",
            "margin_of_safety": gap_text,
        }

    def _extract_risk(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract risk assessment from macro_data and critical_factors."""
        macro = data.get("macro_data", {})
        factors = data.get("critical_factors", [])
        threat = macro.get("overall_threat_level", "low")
        risk_map = {"low": "Low", "elevated": "Medium", "high": "High", "severe": "High", "critical": "High"}
        return {
            "overall_risk": risk_map.get(threat, "Medium"),
            "business_risks": [f.get("description", "") for f in factors if f.get("severity") == "high"],
            "financial_risks": [],
            "market_risks": [rf.get("title", "") for rf in macro.get("risk_factors", [])[:3]],
        }

    def _extract_takeaways(self, data: Dict[str, Any]) -> list:
        """Extract key takeaways from executive summary bullets."""
        bullets = data.get("executive_summary_bullets", [])
        return [b.get("text", "") for b in bullets[:5] if b.get("text")]

    def _derive_conviction(self, data: Dict[str, Any]) -> str:
        """Derive conviction level from quality score."""
        score = data.get("quality_score", 50)
        if isinstance(score, str):
            try:
                score = float(score)
            except ValueError:
                score = 50
        if score >= 80:
            return "High"
        elif score >= 55:
            return "Medium"
        else:
            return "Low"


# Public alias for the direct report path (`ticker_report_service`), which needs
# the same semaphore + dedup but must NOT share this module's dedup namespace —
# it passes key_prefix="direct". Imported function-locally there to keep the
# dependency one-directional. Deliberately an alias rather than an extraction:
# tests/test_agent_dedup_concurrency.py patches `research_service._AGENT_SEMAPHORE`
# on this module object, and a re-export from a new module would silently break
# that isolation.
run_agent_deduped = _run_agent_deduped
