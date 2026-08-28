"""
ResearchAgent — Deep-research pipeline for the `/research/generate`
("Generate Analysis") flow.

Phases:
  1. **Collect** — `TickerReportDataCollector` fetches FMP +
     AnalystService + HoldersService in parallel, computes metrics,
     and pre-builds deterministic real-data report sections.
  2. **Agentic deep research** — Gemini with FMP tool declarations
     runs up to MAX_AGENTIC_ROUNDS rounds of autonomous data
     gathering through the persona's investment lens. Returns a
     free-text research synthesis fed into Stage A.
  3. **Stage A (structural shell)** — single Gemini JSON call for
     scoring + categorization fields only. Narrative slots come back
     as empty strings.
  4. **Assemble** — `collector.assemble_report` merges deterministic
     sections with the Stage-A shell. Real-data wins for numerics.
  5. **Stage B (parallel narratives)** — N parallel
     `gemini.generate_text` calls write the persona-styled prose.
     Each job is independent; one failure falls back to an honest
     sentinel without breaking the others.

Compared to `TickerReportService` (direct path), only Phase 2 is
extra here — the agentic loop is exactly the depth premium that
Generate Analysis credits buy.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from google.genai import types

from app.integrations.fmp import FMPClient
from app.integrations.gemini import GeminiClient, _call_with_timeout
from app.services.agents.fmp_tools import (
    build_fmp_tool_declarations,
    build_tool_handlers,
)
from app.services.report_degradation import (
    REPORT_DEGRADED_KEY,
    _degraded_reason,
    _mark_degraded,
)
from app.services.agents.narrative_prompts import (
    build_narrative_jobs,
    build_stage_a_prompt,
    parse_stage_a_response,
    run_narrative_jobs,
    stage_a_fallback,
    stage_a_thinking_budget,
    synthesize_core_thesis,
    synthesize_critical_factors,
)
from app.services.agents.persona_config import PersonaConfig, get_persona_config
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    TickerReportDataCollector,
    build_financial_context,
)

logger = logging.getLogger(__name__)

# Max tool-calling rounds before forcing synthesis
MAX_AGENTIC_ROUNDS = 4


def _response_parts(response: Any) -> List[Any]:
    """Parts of the first candidate. The unified SDK has no top-level
    `response.parts`; parts live under `candidates[0].content.parts`."""
    try:
        cand = (response.candidates or [None])[0]
        if cand and cand.content and cand.content.parts:
            return list(cand.content.parts)
    except (AttributeError, TypeError, IndexError):
        pass
    return []


def _safe_response_text(response: Any) -> str:
    """Defensive accessor for Gemini's `response.text`.

    The SDK's `.text` property raises ValueError when the candidate has no text
    Part (e.g. only function calls, or `finish_reason=STOP` with empty content),
    so we wrap the access itself. Falls back to walking the candidate's parts and
    concatenating any text parts, then returns "" as a last resort.
    """
    try:
        return response.text or ""
    except (ValueError, AttributeError):
        pass
    try:
        chunks: List[str] = []
        for p in _response_parts(response):
            try:
                t = p.text
            except (ValueError, AttributeError):
                continue
            if t:
                chunks.append(t)
        return "\n".join(chunks)
    except (ValueError, AttributeError, TypeError):
        return ""


class ResearchAgent:
    """Autonomous research agent for the Generate Analysis flow.

    Produces the complete `TickerReportResponse` JSON for the iOS
    frontend, with real FMP-grounded numbers and AI narrative scored
    through the chosen persona's investment philosophy.
    """

    def __init__(
        self,
        persona_key: str,
        fmp: FMPClient,
        gemini: GeminiClient,
    ):
        self.persona: PersonaConfig = get_persona_config(persona_key)
        self.fmp = fmp
        self.gemini = gemini
        self.collector = TickerReportDataCollector(fmp=fmp)
        # Free-text agent findings from phase 2; surfaced to research_service
        # as the legacy `full_report` column.
        self.research_findings: str = ""

    # ── Main pipeline ─────────────────────────────────────────────────

    async def run(
        self,
        ticker: str,
        progress_cb: Optional[Callable[..., Any]] = None,
    ) -> Dict[str, Any]:
        """Execute the full research pipeline.

        Returns a `TickerReportResponse`-shaped dict ready to store in
        `research_reports.ticker_report_data`.
        """
        ticker = ticker.upper().strip()

        # ── Phase 1: collect ─────────────────────────────────────────
        if progress_cb:
            await progress_cb(5, "Gathering market data...")

        out = await self.collector.collect(ticker, self.persona.key)
        evidence = build_financial_context(out)

        if progress_cb:
            await progress_cb(20, f"{self.persona.agent_label} analyzing data...")

        # ── Phase 2: agentic deep research ───────────────────────────
        research_text = await self._agentic_research(out, evidence)
        self.research_findings = research_text

        if progress_cb:
            await progress_cb(55, "Deep research complete, synthesizing...")

        # ── Phase 3: Stage A (structural shell) ──────────────────────
        shell = await self._generate_stage_a(out, evidence, research_text)

        if progress_cb:
            await progress_cb(75, "Building report...")

        # ── Phase 4: assemble (real-data + Stage A merge) ────────────
        report = self.collector.assemble_report(out, shell)

        # Carry the degradation marker across the merge. `assemble_report` builds a fresh
        # dict from the real-data sections, so the shell-level key does NOT survive on its
        # own — and an untagged degraded report is charged 20 credits, marked completed
        # (so the `except`-only refund never fires) and written to the SHARED cache, where
        # it is then served free to everyone else and re-sold to the next research buyer.
        # The direct path has done this since 2026-08-05; this door never did.
        stage_a_degradation = _degraded_reason(shell)
        if stage_a_degradation:
            report[REPORT_DEGRADED_KEY] = stage_a_degradation

        # ── Phase 5: Stage B narratives + cross-module thesis synthesis ──
        # Both mutate `report` in place on disjoint keys (Stage B fills
        # per-field prose; synthesize_core_thesis rewrites core_thesis from
        # every FINAL module verdict), so they run concurrently.
        if progress_cb:
            await progress_cb(85, "Writing narrative insights...")

        jobs = build_narrative_jobs(self.persona, evidence, report)
        await asyncio.gather(
            # Pass `evidence` so Stage B can hoist it into a single Gemini
            # context cache shared across all N parallel narrative calls.
            run_narrative_jobs(jobs, self.gemini, self.persona, evidence),
            synthesize_core_thesis(report, self.persona, self.gemini, evidence),
        )

        # Critical Factors — after the thesis so it reads the FINAL bear case;
        # distinct Deep Dive areas + broad watch triggers (Fed / war / earnings
        # / analyst / market). Stage A/B factors stay as the fallback.
        await synthesize_critical_factors(
            report, self.persona, self.gemini, evidence,
        )

        if progress_cb:
            await progress_cb(95, "Validating and finalizing...")

        return report

    # ── Phase 2: agentic deep research ────────────────────────────────

    async def _agentic_research(
        self, out: CollectedTickerData, evidence: str,
    ) -> str:
        """Multi-round agentic loop. Gemini autonomously requests
        additional FMP data through function calling, returning a
        free-text research synthesis.

        On failure, falls back to a single-pass plain-text analysis
        that still yields useful narrative context for Stage A.
        """
        ticker = out.ticker
        company_name = out.profile.get("companyName", ticker)
        tools = build_fmp_tool_declarations()
        handlers = build_tool_handlers(self.fmp)

        persona_instruction = (
            f"{self.persona.system_prompt}\n\n"
            f"You are conducting deep research on {ticker}. You have access "
            f"to tools that can fetch additional financial data. Review the "
            f"provided data, identify gaps in your analysis, and use the "
            f"tools to fetch any additional data you need. When you have "
            f"enough data, call the 'research_complete' tool with a summary "
            f"of your key findings.\n\n"
            f"IMPORTANT: Focus your research on what matters most for your "
            f"investment philosophy. You don't need to call every tool — "
            f"only fetch data that will materially improve your analysis."
        )

        research_prompt = (
            f"Analyze {company_name} ({ticker}) for potential investment.\n\n"
            f"FINANCIAL DATA AVAILABLE:\n{evidence}\n\n"
            f"Review this data through your investment lens. If you need "
            f"additional data (quarterly trends, dividends, sector context, "
            f"more news, extended history), use the available tools. When "
            f"done, call research_complete."
        )

        try:
            chat = self.gemini.create_tool_chat(
                system_instruction=persona_instruction,
                tools=[tools],
                temperature=0.7,
                max_output_tokens=8192,
            )

            final_text = ""
            response = None
            for round_num in range(MAX_AGENTIC_ROUNDS):
                logger.info(
                    f"Agent {self.persona.key} round {round_num + 1}/"
                    f"{MAX_AGENTIC_ROUNDS}"
                )

                if round_num == 0:
                    response = await _call_with_timeout(
                        chat.send_message(research_prompt),
                        what=f"Stage-A agentic round 1 ({self.persona.key})",
                    )

                # Walk parts: handle function calls; collect tool responses
                has_function_call = False
                response_parts: List[types.Part] = []
                for part in _response_parts(response):
                    fc = part.function_call
                    if not (fc and fc.name):
                        continue
                    has_function_call = True

                    if fc.name == "research_complete":
                        args = dict(fc.args) if fc.args else {}
                        text_from_parts = _safe_response_text(response)
                        final_text = (
                            text_from_parts if text_from_parts
                            else args.get("summary", "Research complete.")
                        )
                        return final_text

                    handler = handlers.get(fc.name)
                    args = dict(fc.args) if fc.args else {}
                    logger.info(f"Agent calling tool: {fc.name}({args})")

                    if handler is None:
                        result = {"error": f"Unknown tool: {fc.name}"}
                    else:
                        try:
                            result = await handler(args)
                        except Exception as e:
                            logger.warning(
                                f"Tool {fc.name} failed: "
                                f"{type(e).__name__}: {e}"
                            )
                            result = {"error": str(e)}

                    response_parts.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response={
                                "result": json.dumps(result, default=str)[:5000]
                            },
                        )
                    )

                if has_function_call and response_parts:
                    response = await _call_with_timeout(
                        chat.send_message(response_parts),
                        what=f"Stage-A tool follow-up ({self.persona.key})",
                    )
                    continue

                # No function call this round — extract whatever text the
                # model emitted and break the loop. If Gemini returned an
                # empty candidate (finish_reason=STOP with no parts), this
                # yields "" and we fall through to the fallback path below.
                final_text = _safe_response_text(response)
                if not final_text:
                    logger.warning(
                        f"Agent {self.persona.key} round {round_num + 1}: "
                        f"Gemini returned empty response — falling back to "
                        f"single-pass analysis."
                    )
                    return await self._fallback_text_analysis(out, evidence)
                break

            if final_text:
                return final_text

            # Every round ended in a tool call and the budget ran out before the model
            # ever called `research_complete` — so there IS no synthesis. This used to
            # return the literal string "Research analysis complete.", which is not a
            # sentinel anything checks: it flows into `build_stage_a_prompt` as
            # `deep_findings` and becomes the entire deep-research premium the user paid
            # 20 credits for, on a report that is otherwise indistinguishable from a
            # good one (not degraded, delivered, cached, and re-sold to the next buyer
            # through the shared cross-user lookup). The agentic loop is the ONLY thing
            # this path has over the free direct path, so silently shipping nothing from
            # it is the most expensive no-op in the product.
            #
            # Do the synthesis the loop never got to instead. The single-pass fallback
            # already exists for the "loop blew up" case and sees the same evidence.
            logger.warning(
                "Agent %s exhausted all %d rounds without calling research_complete — "
                "running the single-pass synthesis so Stage A gets real findings "
                "instead of a placeholder",
                self.persona.key, MAX_AGENTIC_ROUNDS,
            )
            return await self._fallback_text_analysis(out, evidence)

        except Exception as e:
            logger.error(
                f"Agentic research failed for {ticker}: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            return await self._fallback_text_analysis(out, evidence)

    async def _fallback_text_analysis(
        self, out: CollectedTickerData, evidence: str,
    ) -> str:
        """Single-pass narrative when the agentic loop blows up."""
        prompt = (
            f"Produce a comprehensive investment research analysis for "
            f"{out.profile.get('companyName', out.ticker)} ({out.ticker}).\n\n"
            f"FINANCIAL DATA:\n{evidence}\n\n"
            f"Cover: business quality, competitive moat, financial health, "
            f"growth prospects, valuation, risks, and your investment thesis."
        )
        try:
            result = await self.gemini.generate_text(
                prompt=prompt,
                system_instruction=self.persona.system_prompt,
            )
            return result.get("text", "Analysis unavailable.")
        except Exception as e:
            logger.error(
                f"Fallback analysis failed for {out.ticker}: "
                f"{type(e).__name__}: {e}"
            )
            return f"Analysis for {out.ticker} could not be completed."

    # ── Phase 3: Stage A (structural shell) ──────────────────────────

    async def _generate_stage_a(
        self,
        out: CollectedTickerData,
        evidence: str,
        deep_findings: str,
    ) -> Dict[str, Any]:
        """One Gemini JSON call for scoring + categorization. Uses the
        agentic-loop's research synthesis as additional context."""
        ticker = out.ticker
        company_name = out.profile.get("companyName", ticker)
        prompt = build_stage_a_prompt(
            self.persona,
            company_name,
            ticker,
            evidence,
            deep_findings=deep_findings,
        )

        try:
            # Stage A runs with a thinking cap (SYSTEM_DESIGN_GUIDELINES 9b.7).
            # MEASURED on MSFT/warren_buffett via scripts/eval_report_thinking.py
            # (2026-08-27): default 1,715-2,295 thought tokens and 18.1s, versus
            # 0 and 6.9s at budget 0 — same 11 top-level keys, parseable at every
            # budget. Thought tokens bill at the OUTPUT rate.
            #
            # Its OWN setting, separate from Stage B's, because this is the call
            # that decides thesis / pros / cons / moat / valuation on a 20-credit
            # product and "valid JSON with 11 keys" does not certify that the
            # judgement survived. Set REPORT_STAGE_A_THINKING_BUDGET negative in
            # the environment to restore the model default without touching
            # Stage B, and re-run the eval before trusting either way.
            result = await self.gemini.generate_json(
                prompt=prompt,
                system_instruction=self.persona.system_prompt,
                thinking_budget=stage_a_thinking_budget(),
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
