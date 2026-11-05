"""
Ticker Report Service — orchestrates the **direct path** for the
`/stocks/{ticker}/report` endpoint.

Pipeline (Phase 2):
  1. Check `ticker_report_cache` (24h Supabase TTL) — return on hit.
  2. Run `TickerReportDataCollector` to fetch every FMP / service
     dependency in parallel and build the deterministic real-data
     sections (insider transactions, wall street consensus, segments,
     management roster, price-action event, etc.).
  3. **Stage A** — one Gemini JSON call asking only for *structural
     and scoring* fields (quality_score, bull/bear case, moat
     dimensions, risk factors, fundamental star ratings, etc.).
     Narrative slots are explicit empty strings.
  4. `collector.assemble_report` merges deterministic real-data with
     the Stage A shell — real numerics always win.
  5. **Stage B** — N parallel `gemini.generate_text` calls (one per
     narrative field) write the persona-styled prose. The runner
     mutates the report in place; per-job exceptions fall back to an
     honest sentinel string instead of breaking the whole response.
  6. Upsert the assembled report back into `ticker_report_cache`.

The two-stage approach makes a single bad narrative non-fatal — only
Stage A can break the response shape, and it's the simpler call.
Direct-path latency: ~15-25s (Stage A ~5-8s + Stage B's slowest job
~3-5s in parallel + collector ~2-4s).

The 4-round agentic FMP-tool-calling loop stays in `ResearchAgent`
and fires only for `/research/generate`.
"""

import asyncio
import logging
from typing import Any, Dict

from app.integrations.gemini import get_gemini_client
from app.services.agents.narrative_prompts import (
    build_narrative_jobs,
    build_stage_a_prompt,
    STAGE_B_PARTIAL_SECTION,
    generate_stage_a_shell,
    run_narrative_jobs,
    stage_b_is_partial,
    stage_b_outage_reason,
    synthesize_core_thesis,
    synthesize_critical_factors,
    thesis_empty_reason,
)
from app.services.agents.persona_config import PersonaConfig, get_persona_config
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    TickerReportDataCollector,
    build_financial_context,
    get_collector,
    record_degraded_section,
    report_degraded_sections,
)
from app.services.ticker_report_cache import (
    get_cached_report,
    report_schema_problem,
    upsert_cached_report,
)

logger = logging.getLogger(__name__)


# Degradation marker — now shared with the deep path (`ResearchAgent`), which had the
# identical bug open on the more expensive door. Re-exported here because ~5 tests and the
# rest of this module reference `ticker_report_service._mark_degraded` by name.
# See `app/services/report_degradation.py` for the full reasoning.
from app.services.report_degradation import (  # noqa: E402
    _DEGRADED_KEY,
    REPORT_DEGRADED_KEY,
    _degraded_reason,
    _mark_degraded,
)


class TickerReportService:
    def __init__(self):
        self.collector: TickerReportDataCollector = get_collector()
        self.gemini = get_gemini_client()

    # ── Main entry point ──────────────────────────────────────────────

    async def generate_ticker_report(
        self, ticker: str, persona_key: str = "warren_buffett"
    ) -> Dict[str, Any]:
        """Generate (or cache-hit) the full ticker report for TickerReportView."""
        ticker = ticker.upper().strip()

        # 1. 24h Supabase cache lookup
        cached = await get_cached_report(ticker, persona_key)
        if cached is not None:
            logger.info(
                f"ticker_report_cache HIT for {ticker}/{persona_key}"
            )
            return cached

        return await self.generate_fresh_report(ticker, persona_key)

    async def generate_fresh_report(
        self, ticker: str, persona_key: str = "warren_buffett"
    ) -> Dict[str, Any]:
        """Generate a FRESH report (no cache read) and cache it on success.

        Split out from `generate_ticker_report` so the endpoint can gate a PAID
        generation on a cache MISS it already detected: the cache lookup is the
        free path, this method is the billable "real AI work" path. Callers that
        don't care about billing should use `generate_ticker_report`, which
        checks the cache first and only falls through to this.

        Runs under the SAME global agent semaphore + same-(ticker, persona) dedup
        that `/research/generate` uses. Without it, "everyone opens AAPL after
        earnings" spawned one full Gemini pipeline PER REQUEST on this path while
        the deep path collapsed the identical herd to one.
        `ticker_data_cache._INFLIGHT` only dedups the persona-neutral FMP
        collection by ticker — not the per-persona Gemini work, which is the
        expensive part (Stage A + 14 Stage-B narratives + 2 synthesis calls).

        The dedup namespace is `"direct"`, NOT the deep path's: the two pipelines
        produce different reports for the same (ticker, persona), so sharing a
        namespace would hand a deep-research caller a shallow report.
        """
        ticker = ticker.upper().strip()

        # Function-local import: research_service does not import this module, and
        # this keeps that one-directional. Mirrors research.py / chat.py.
        from app.services.research_service import run_agent_deduped

        return await run_agent_deduped(
            ticker, persona_key,
            lambda: self._generate_uncontended(ticker, persona_key),
            key_prefix="direct",
        )

    async def _generate_uncontended(
        self, ticker: str, persona_key: str
    ) -> Dict[str, Any]:
        """The actual pipeline. Only ever runs as a dedup LEADER holding a
        semaphore slot — followers get a deep copy of this result, so the cache
        upsert at the end fires exactly once per real generation."""
        # 2. Collect real data
        out = await self.collector.collect(ticker, persona_key)
        persona = get_persona_config(persona_key)
        evidence = build_financial_context(out)

        # 3. Stage A: structural / scoring shell
        shell = await self._generate_stage_a(out, persona, evidence)

        #    A degraded Stage A can never be delivered, so stop before the merge and Stage B.
        #    When Gemini is unavailable (quota circuit open, 429s exhausted, 5xx) or answers
        #    twice with something unusable, the shell is `stage_a_fallback()`: empty bull and
        #    bear cases, blank narrative slots. The merged result would still VALIDATE against
        #    TickerReportResponse, so it used to be charged 20 credits, returned as a success
        #    AND cached, then served free to every other user for the rest of the close
        #    cycle. The caller refunds on this marker, and nothing here reaches the upsert.
        degraded_reason = _degraded_reason(shell)
        if degraded_reason:
            logger.warning(
                "Ticker report for %s/%s NOT generated — Stage A degraded (%s). The caller "
                "is responsible for refunding credits on a degraded report.",
                ticker, persona_key, degraded_reason,
            )
            return {REPORT_DEGRADED_KEY: degraded_reason}

        # 4. Merge deterministic real-data with Stage A shell. On a worker thread:
        #    assemble_report is sync and makes blocking Supabase reads (sector
        #    benchmarks for the competitor set, moat sector medians, peer moats) that
        #    would otherwise stall every other request on the single-worker loop.
        #    `out` is this request's own deep copy, and nothing else runs on it here.
        report = await asyncio.to_thread(self.collector.assemble_report, out, shell)

        # 5. Stage B narratives + cross-module thesis synthesis, in parallel.
        #    Stage B fills per-field prose; synthesize_core_thesis rewrites
        #    core_thesis from every FINAL module verdict (not just the
        #    fundamentals Stage A saw). They mutate disjoint keys of
        #    `report`, so concurrent execution is safe.
        jobs = build_narrative_jobs(persona, evidence, report)
        narrative_outcome, _ = await asyncio.gather(
            # Pass `evidence` so Stage B can hoist it into a single Gemini
            # context cache shared across all N parallel narrative calls.
            # Omitting it leaves `use_cache` False and every call re-sends the
            # full evidence blob inline at full token price.
            run_narrative_jobs(jobs, self.gemini, persona, evidence),
            synthesize_core_thesis(report, persona, self.gemini, evidence),
        )

        #    A report that is mostly fallbacks, or has no thesis, is a non-delivery too, and
        #    the only signal of it is here: every failed job already landed on its sentinel
        #    and validates. Below the refund line a run can still have lost too much to share.
        stage_b_degradation = stage_b_outage_reason(
            narrative_outcome, ticker=ticker, persona=persona_key,
        ) or thesis_empty_reason(report, ticker=ticker, persona=persona_key)
        if stage_b_degradation:
            return {REPORT_DEGRADED_KEY: stage_b_degradation}
        if stage_b_is_partial(narrative_outcome):
            record_degraded_section(report, STAGE_B_PARTIAL_SECTION)

        # 6. Critical Factors — synthesized AFTER the thesis so it reads the
        #    FINAL bear case; spreads across DISTINCT Deep Dive areas with broad
        #    watch triggers (Fed / war / earnings / analyst / market). Overwrites
        #    on success; the Stage A/B factors stay as the fallback otherwise.
        await synthesize_critical_factors(report, persona, self.gemini, evidence)

        #    A report the client cannot decode is a non-delivery, typed like any other: the
        #    endpoint refunds it and answers REPORT_GENERATION_FAILED, not a DATA_INCOMPLETE
        #    that blames the data.
        schema_problem = await asyncio.to_thread(report_schema_problem, report)
        if schema_problem:
            logger.error(
                "[report-schema-invalid] ticker=%s persona=%s: %s",
                ticker, persona_key, schema_problem,
            )
            return {REPORT_DEGRADED_KEY: "report_schema_invalid"}

        # 7. Persist to cache (best-effort; failure logged but doesn't raise).
        #
        #    A report that lost Financials data to a DEGRADED upstream build (a 429 on one
        #    FMP leg — `DEGRADED_SECTIONS_KEY`, set by `assemble_report`) has complete AI
        #    content, so it is DELIVERED and billed like any other report, but never written
        #    here — the next caller regenerates instead of inheriting the hole for the rest
        #    of the close cycle.
        lost_sections = report_degraded_sections(report)
        if lost_sections:
            logger.warning(
                "[report-partial-not-cached] Ticker report for %s/%s NOT cached — "
                "Financials sections lost to a degraded upstream build: %s. Delivered to "
                "this caller only.",
                ticker, persona_key, ", ".join(lost_sections),
            )
        else:
            await upsert_cached_report(ticker, persona_key, report)

        return report

    # ── Stage A: structural shell ─────────────────────────────────────

    async def _generate_stage_a(
        self,
        out: CollectedTickerData,
        persona: PersonaConfig,
        evidence: str,
    ) -> Dict[str, Any]:
        """One Gemini JSON call for scoring + categorization. Narrative
        slots come back as empty strings; Stage B writes those."""
        ticker = out.ticker
        company_name = out.profile.get("companyName", ticker)
        prompt = build_stage_a_prompt(persona, company_name, ticker, evidence)

        return await generate_stage_a_shell(self.gemini, persona, ticker, prompt)
