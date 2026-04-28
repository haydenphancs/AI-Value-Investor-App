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
            return (result.get("text") or "").strip()
        except Exception as e:
            logger.error(
                f"Chat generation failed for {ticker}: "
                f"{type(e).__name__}: {e}"
            )
            return f"I'm unable to analyze {ticker} right now. Please try again."

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

        # 2. Collect real data
        out = await self.collector.collect(ticker, persona_key)
        persona = get_persona_config(persona_key)
        evidence = build_financial_context(out)

        # 3. Stage A: structural / scoring shell
        shell = await self._generate_stage_a(out, persona, evidence)

        # 4. Merge deterministic real-data with Stage A shell
        report = self.collector.assemble_report(out, shell)

        # 5. Stage B: parallel narrative writing (mutates `report` in place)
        jobs = build_narrative_jobs(persona, evidence, report)
        await run_narrative_jobs(jobs, self.gemini, persona)

        # 6. Persist to cache (best-effort; failure logged but doesn't raise)
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
                return stage_a_fallback()
            return shell
        except Exception as e:
            logger.error(
                f"Stage A generation failed for {ticker}: "
                f"{type(e).__name__}: {e}"
            )
            return stage_a_fallback()
