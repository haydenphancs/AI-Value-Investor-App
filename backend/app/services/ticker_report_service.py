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

from app.integrations.fmp import get_fmp_client
from app.integrations.gemini import get_gemini_client
from app.services.agents.narrative_prompts import (
    build_narrative_jobs,
    build_stage_a_prompt,
    parse_stage_a_response,
    run_narrative_jobs,
    stage_a_fallback,
    synthesize_core_thesis,
    synthesize_critical_factors,
)
from app.services.agents.persona_config import PersonaConfig, get_persona_config
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    TickerReportDataCollector,
    build_financial_context,
    get_collector,
)
from app.services.ticker_report_cache import (
    get_cached_report,
    upsert_cached_report,
)

logger = logging.getLogger(__name__)


# Degradation marker — now shared with the deep path (`ResearchAgent`), which had the
# identical bug open on the more expensive door. Re-exported here because ~5 tests and the
# rest of this module reference `ticker_report_service._mark_degraded` by name.
# See `app/services/report_degradation.py` for the full reasoning.
from app.services.report_degradation import (  # noqa: E402
    _DEGRADED_KEY,
    _degraded_reason,
    _mark_degraded,
)


class TickerReportService:
    def __init__(self):
        self.collector: TickerReportDataCollector = get_collector()
        self.gemini = get_gemini_client()
        self.fmp = get_fmp_client()  # used only by chat_about_ticker

    # ── Chat (shares Stage-B style for tonal consistency) ────────────

    async def chat_about_ticker(
        self, ticker: str, message: str, persona_key: str = "warren_buffett"
    ) -> str:
        """Quick AI Q&A about a ticker — minimal FMP + persona-styled Gemini.

        Uses the same anti-cliché / anti-hedge style brief as the
        report's Stage-B narratives so chat answers read in the same
        voice as the report itself.
        """
        tasks = {
            "profile": self.fmp.get_company_profile(ticker),
            "quote": self.fmp.get_stock_price_quote(ticker),
        }
        keys = list(tasks.keys())
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        data: Dict[str, Any] = {}
        for key, result in zip(keys, results):
            data[key] = {} if isinstance(result, Exception) else result

        profile = data.get("profile", {})
        quote = data.get("quote", {})
        if not profile:
            raise ValueError(f"No company profile found for ticker: {ticker}")

        persona = get_persona_config(persona_key)
        company_name = profile.get("companyName", ticker)
        price = quote.get("price", "N/A")
        pe = quote.get("pe", "N/A")
        mkt_cap = profile.get("mktCap", "N/A")
        sector = profile.get("sector", "N/A")
        industry = profile.get("industry", "N/A")
        mkt_cap_str = (
            f"${mkt_cap:,.0f}" if isinstance(mkt_cap, (int, float))
            else str(mkt_cap)
        )

        prompt = f"""The user is asking about {company_name} ({ticker}).

Quick facts:
- Price: ${price}
- P/E: {pe}
- Market Cap: {mkt_cap_str}
- Sector: {sector} | Industry: {industry}

User question: {message}

STYLE: Catchy, punchy, plain-English. Sound like a sharp portfolio manager
talking to a smart friend — confident, specific, never marketing-speak.
NEVER use clichés ("strong tailwinds", "well-positioned", "going forward").
NEVER hedge ("could potentially", "may possibly"). Cite a concrete number when
available. Apply your lens: {persona.narrative_lens or "your investment philosophy"}.

LENGTH: 2-4 sentences, total under 90 words."""

        try:
            result = await self.gemini.generate_text(
                prompt=prompt,
                system_instruction=persona.system_prompt,
            )
        except Exception as e:
            # RAISE (don't swallow into a polite sentinel): the /report/chat endpoint charges
            # CHAT_CREDIT_COST upfront and refunds on any raised exception. Returning a sentinel
            # string would set delivered=True and BILL the user for a non-answer.
            logger.error(
                f"Chat generation failed for {ticker}: {type(e).__name__}: {e}"
            )
            raise
        reply = (result.get("text") or "").strip()
        if not reply:
            # An empty generation is also a non-delivery → raise so the endpoint refunds.
            raise RuntimeError(f"empty chat generation for {ticker}")
        return reply

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

        # 4. Merge deterministic real-data with Stage A shell
        report = self.collector.assemble_report(out, shell)

        # 5. Stage B narratives + cross-module thesis synthesis, in parallel.
        #    Stage B fills per-field prose; synthesize_core_thesis rewrites
        #    core_thesis from every FINAL module verdict (not just the
        #    fundamentals Stage A saw). They mutate disjoint keys of
        #    `report`, so concurrent execution is safe.
        jobs = build_narrative_jobs(persona, evidence, report)
        await asyncio.gather(
            # Pass `evidence` so Stage B can hoist it into a single Gemini
            # context cache shared across all N parallel narrative calls.
            # Omitting it leaves `use_cache` False and every call re-sends the
            # full evidence blob inline at full token price.
            run_narrative_jobs(jobs, self.gemini, persona, evidence),
            synthesize_core_thesis(report, persona, self.gemini, evidence),
        )

        # 6. Critical Factors — synthesized AFTER the thesis so it reads the
        #    FINAL bear case; spreads across DISTINCT Deep Dive areas with broad
        #    watch triggers (Fed / war / earnings / analyst / market). Overwrites
        #    on success; the Stage A/B factors stay as the fallback otherwise.
        await synthesize_critical_factors(report, persona, self.gemini, evidence)

        # 6. Persist to cache (best-effort; failure logged but doesn't raise) — but ONLY
        #    when the report is real.
        #
        #    When Gemini is unavailable (quota circuit open, 429s exhausted, 5xx) Stage A
        #    silently degrades to `stage_a_fallback()` — empty bull/bear cases, blank
        #    narrative slots — and every Stage B job falls back to its sentinel. The result
        #    still VALIDATES against TickerReportResponse, so it used to be charged 20
        #    credits, returned as a success, AND written here, where it was then served free
        #    to every other user for the rest of the close cycle. One Gemini blip poisoned a
        #    ticker for everyone until the next close.
        degraded_reason = _degraded_reason(shell)
        if degraded_reason:
            logger.warning(
                "Ticker report for %s/%s NOT cached — degraded (%s). The caller is "
                "responsible for refunding credits on a degraded report.",
                ticker, persona_key, degraded_reason,
            )
            report["_degraded"] = degraded_reason
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

        try:
            result = await self.gemini.generate_json(
                prompt=prompt,
                system_instruction=persona.system_prompt,
            )
            shell = parse_stage_a_response(result.get("text") or "")
            if shell is None:
                logger.error(
                    f"Stage A returned unparseable JSON for {ticker}; "
                    f"using honest fallback shell."
                )
                return _mark_degraded(stage_a_fallback(), "stage_a_unparseable")
            return shell
        except Exception as e:
            logger.error(
                f"Stage A generation failed for {ticker}: "
                f"{type(e).__name__}: {e}"
            )
            return _mark_degraded(stage_a_fallback(), f"stage_a_{type(e).__name__}")
