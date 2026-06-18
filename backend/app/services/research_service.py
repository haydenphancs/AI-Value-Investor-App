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
import copy
import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

from app.config import settings
from app.database import get_supabase
from app.integrations.gemini import get_gemini_client
from app.integrations.fmp import get_fmp_client
from app.services.agents.research_agent import ResearchAgent
from app.services.agents.persona_config import get_persona_config
from app.services.agents.persona_scoring import compute_quality_score
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
#     missing dedup for the per-persona Gemini agent run — Stage A + 15 Stage-B
#     narratives + synthesis.)
_AGENT_SEMAPHORE: Optional[asyncio.Semaphore] = None
_AGENT_INFLIGHT: Dict[str, "asyncio.Future"] = {}


def _get_agent_semaphore() -> asyncio.Semaphore:
    """Lazily build the process-wide semaphore inside the running loop."""
    global _AGENT_SEMAPHORE
    if _AGENT_SEMAPHORE is None:
        _AGENT_SEMAPHORE = asyncio.Semaphore(
            max(1, settings.MAX_CONCURRENT_AGENT_RUNS)
        )
    return _AGENT_SEMAPHORE


async def _run_agent_deduped(
    ticker: str, persona_key: str, run_callable, on_started=None
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

    `on_started` (async, optional) fires ONCE right after the leader acquires its
    slot — i.e. when real agent work begins, NOT while queued — so the caller can
    stamp processing_started_at and the reconciliation sweep can age the report
    off work-start rather than enqueue time.
    """
    key = f"{ticker.upper().strip()}::{persona_key}"

    inflight = _AGENT_INFLIGHT.get(key)
    if inflight is not None:
        shared = await inflight
        return copy.deepcopy(shared)

    loop = asyncio.get_running_loop()
    fut: "asyncio.Future" = loop.create_future()
    _AGENT_INFLIGHT[key] = fut
    try:
        async with _get_agent_semaphore():
            if on_started is not None:
                await on_started()
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
        raise
    except Exception as e:
        if not fut.done():
            fut.set_exception(e)
        raise
    finally:
        _AGENT_INFLIGHT.pop(key, None)


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
    ):
        """
        Full multi-agent pipeline:
          1. Spawn ResearchAgent with persona
          2. Agent runs agentic loop (data gathering + analysis)
          3. Store full TickerReportResponse + legacy fields
          4. Decrement user credits
        """
        start = datetime.now(timezone.utc)

        try:
            # Mark as processing
            self._update_status(report_id, "processing", 2, "Initializing research agent...")

            persona = get_persona_config(persona_key)

            # ── Shared cross-user cache lookup ────────────────────────
            # If any user has a fresh completed report for this exact
            # (ticker, persona), reuse the ticker_report_data JSONB
            # instead of running the agent again. The new row is still
            # owned by `user_id` and credits still get decremented below
            # — only the expensive AI/FMP work is deduplicated.
            self._update_status(
                report_id, "processing", 5, "Checking shared cache..."
            )
            cached = await self._lookup_shared_cache(ticker, persona_key)

            if cached is not None:
                logger.info(
                    f"Shared cache HIT for {ticker}/{persona_key} — "
                    f"reusing existing analysis (report {report_id}, "
                    f"user {user_id})"
                )
                self._update_status(
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
                    self._update_status(report_id, "processing", progress, step)

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
                    return await asyncio.wait_for(
                        agent.run(
                            ticker=ticker,
                            progress_cb=on_progress,
                        ),
                        timeout=settings.RESEARCH_PIPELINE_TIMEOUT_SECONDS,
                    )

                async def _on_started():
                    # Stamp when this report ACTUALLY starts running (slot
                    # acquired) so the reconciliation sweep ages it off
                    # work-start, not the queue-inflated created_at.
                    self._mark_processing_started(report_id)

                ticker_report_data = await _run_agent_deduped(
                    ticker, persona_key, _run_agent, on_started=_on_started
                )

            # Extract legacy fields for backward compatibility
            self._update_status(report_id, "processing", 92, "Saving report...")

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
                "action_recommendation": self._derive_recommendation(ticker_report_data),

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
            result = (
                self.supabase.table("research_reports")
                .update(update_data)
                .eq("id", report_id)
                .eq("is_refunded", False)
                .in_("status", ["pending", "processing"])
                .execute()
            )
            if not result.data:
                # The sweep already claimed + refunded this report. Do NOT
                # deliver (no cache seed) — the user was made whole; dropping the
                # result is the correct outcome.
                logger.warning(
                    f"Report {report_id} already reconciled (refunded) before "
                    f"completion — skipping delivery to avoid double-resolve "
                    f"(persona={persona_key}, ticker={ticker})"
                )
                return

            # Seed the direct-path cache so /stocks/{ticker}/report users
            # benefit from this expensive agentic run for the next 24h.
            # Best-effort — failures inside upsert_cached_report are logged
            # but never raised, so a Supabase blip can't fail the report.
            await upsert_cached_report(ticker, persona_key, ticker_report_data)

            # Credits were charged upfront in /research/generate (5 credits
            # via CreditService.try_charge). No deduction here. Refunds on
            # failure are handled by _run_research_task in research.py —
            # this function only ever signals success.

            logger.info(
                f"Report {report_id} completed in {generation_time}s "
                f"(persona={persona_key}, ticker={ticker})"
            )

        except Exception as e:
            logger.error(f"Report generation failed: {e}", exc_info=True)
            self._update_status(
                report_id, "failed", 0,
                error_message=f"Research failed: {str(e)[:400]}"
            )
            raise

    # ── Status Helper ─────────────────────────────────────────────────────

    def _update_status(
        self,
        report_id: str,
        status: str,
        progress: int,
        current_step: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        update: Dict[str, Any] = {"status": status, "progress": progress}
        if current_step:
            update["current_step"] = current_step
        if error_message:
            update["error_message"] = error_message
        try:
            self.supabase.table("research_reports").update(update).eq(
                "id", report_id
            ).execute()
        except Exception as e:
            logger.error(f"Status update failed for {report_id}: {e}")

    def _mark_processing_started(self, report_id: str) -> None:
        """Stamp processing_started_at = now when the report acquires its agent
        slot (real work begins). The reconciliation sweep ages a STARTED report
        off this, not the queue-inflated created_at. Guarded `is null` so a
        re-entry can't move it. Best-effort: swallow errors (incl. the column
        not existing before migration 070 is applied) — never break generation."""
        try:
            self.supabase.table("research_reports").update(
                {"processing_started_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", report_id).is_("processing_started_at", "null").execute()
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
                    return result.data[0]["ticker_report_data"]
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
        status = val.get("status", "fair_value")
        rating_map = {
            "overpriced": "Overvalued",
            "fair_value": "Fair Value",
            "underpriced": "Undervalued",
            "deep_undervalued": "Undervalued",
        }
        return {
            "valuation_rating": rating_map.get(status, "Fair Value"),
            "key_metrics": {},
            "historical_context": "",
            "margin_of_safety": f"{val.get('upside_potential', 0):.1f}% upside",
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

    def _derive_recommendation(self, data: Dict[str, Any]) -> str:
        """Derive Buy/Hold/Sell from quality score and valuation."""
        score = data.get("quality_score", 50)
        # None-safe: slots are Optional and can be None. Legacy "key_vitals"
        # fallback covers reports cached before the key was renamed.
        val_status = (
            (
                data.get("_scoring_inputs") or data.get("key_vitals") or {}
            ).get("valuation") or {}
        ).get("status", "fair_value")
        if isinstance(score, str):
            try:
                score = float(score)
            except ValueError:
                score = 50
        if score >= 75 and val_status in ("underpriced", "deep_undervalued"):
            return "Buy"
        elif score <= 35 or val_status == "overpriced":
            return "Sell"
        elif score >= 60:
            return "Hold"
        else:
            return "Watch"

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
