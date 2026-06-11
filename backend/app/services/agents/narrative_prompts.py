"""
Narrative Prompts — Stage B per-field micro-prompts.

After Stage A returns the structured shell (scores, categorized
bullets, moat dimensions, risk factors, etc.), Stage B fires N
parallel `gemini.generate_text` calls — one per narrative field —
each with:
  - The persona system prompt (carries the Cay AI identity rule)
  - The persona's narrative_lens injected into the field instruction
  - A style brief (anti-cliché, anti-hedging, sharp PM voice)
  - A length brief (sentence count + word cap)
  - The specific data points relevant to *that* field (no need to
    dump the whole financial context into every micro-prompt)

This separation means one bad narrative does NOT break the report —
each gather() call falls back to a clearly honest sentinel string,
and the structural shell from Stage A is the only single point of
failure for the response shape.

Total wall time for Stage B ≈ slowest single Gemini call (~3-5s)
since every job runs concurrently. The Gemini client's prompt cache
de-dupes when the same (prompt, system_instruction) pair repeats
across two persona runs of the same ticker.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.integrations.gemini import GeminiClient
from app.services.agents.persona_config import PersonaConfig
# Reuse the headline scorer's per-vital reader so the Bull/Bear point count is
# driven by the EXACT same 0-10 module substrate that drives the quality score.
from app.services.agents.persona_scoring import _vital_score

logger = logging.getLogger(__name__)


# ── Honest fallbacks (visible to the user when Gemini fails) ──────────


FALLBACK = {
    "executive_summary_text": (
        "Live commentary unavailable. Numbers below reflect the latest filings."
    ),
    "overall_assessment_text": "Quality scoring updated; narrative unavailable.",
    "guidance_quote": None,
    "revenue_analysis_note": None,
    "revenue_forecast_insight": (
        "Forecast reflects analyst estimates; commentary unavailable."
    ),
    "moat_durability_note": "Data unavailable for this ticker.",
    "moat_competitive_insight": "Data unavailable for this ticker.",
    "macro_intelligence_brief": (
        "Macro commentary unavailable. Risk factors above are sourced from "
        "filings and macro data."
    ),
    "price_action_narrative": (
        "Price chart shown above; commentary unavailable."
    ),
    "key_management_insight": "Data unavailable for this ticker.",
    "insider_key_insight": (
        "Insider data refreshed; commentary unavailable."
    ),
    "fundamental_quality_label": "—",
    "critical_factor_description": "Signal unavailable for this factor.",
    "critical_factor_watch": None,
    "wall_street_insight": None,
    "hidden_market_signals_insight": "",
}


# ── Style brief shared across all narrative prompts ───────────────────


_STYLE_BRIEF = """STYLE: Catchy, punchy, plain English. Sound like a sharp portfolio manager talking to a smart friend: confident, specific, never marketing speak. NEVER use clichés ("strong tailwinds", "well positioned", "going forward", "in the long run", "robust", "best in class"). NEVER hedge ("could potentially", "may possibly", "it remains to be seen"). Cite a concrete number when one is available in the data.

PUNCTUATION: Never use a dash as punctuation: no em dash, no en dash, no hyphen. Replace any dash break with a comma, semicolon, or period. Write compound terms as two words (e.g. "short selling"). Write ranges and negative numbers in words (e.g. "10 to 20 percent", "down 8%", "fell 3%"), never with a minus sign.

PERSONA VOICE: Stay in character as {persona_name}. Apply your lens ({narrative_lens}) but never name yourself, never mention the underlying technology, model, or AI provider, and never say "as an AI".

OUTPUT: Return ONLY the requested text. No JSON, no markdown, no quotes, no preamble like "Here is" or "The narrative is". Just the prose."""


def _style_block(persona: PersonaConfig) -> str:
    return _STYLE_BRIEF.format(
        persona_name=persona.display_name,
        narrative_lens=persona.narrative_lens or "your investment philosophy",
    )


def _length_brief(sentences: int, word_cap: int) -> str:
    s = "1 sentence" if sentences == 1 else f"exactly {sentences} sentences"
    return f"LENGTH: Write {s}, total under {word_cap} words."


# ── Cross-section coherence + grounding (shared across insight prompts) ─

# Canonical anti-fabrication rule for any insight that cites values rendered in
# its own section (overall_assessment, wall_street, …). Keeps the "cite what the
# user sees, never invent or recompute" discipline worded identically everywhere.
def _displayed_values_grounding(block_name: str) -> str:
    return (
        f"Grounding rule — STRICT: any number you cite MUST come verbatim from the "
        f"{block_name} below. NEVER invent or recompute a value shown there; if a "
        f"value reads '—' / 'N/A' / 'no coverage', pick a different anchor rather "
        f"than fabricating one."
    )


def _related_context_block(topic: str, context: str) -> str:
    """Wrap a cross-section digest slice in a clearly-labeled, anchored block so
    the insight can stay consistent with related sections WITHOUT drifting onto
    them. Returns "" when there's no context (the insight then stays siloed)."""
    if not context.strip():
        return ""
    return (
        f"\nRELATED CONTEXT (for coherence only — your insight is still about "
        f"{topic}; use these numbers only to stay consistent with the rest of the "
        f"report, do NOT pivot to them as your subject):\n{context}\n"
    )


# ── Output post-processing ────────────────────────────────────────────


_QUOTE_CHARS = ("“", "”", "‘", "’", '"', "'", "`")


def _strip_dashes(text: str) -> str:
    """Bulletproof backstop for the STYLE no-dash rule: guarantee no em dash,
    en dash, figure dash, horizontal bar, or spaced connector hyphen survives
    in narrative output, whatever the model does. Em/figure/bar dashes are
    clause connectors and become a comma; an en dash between digits is a range
    and becomes "to" (else a comma); a spaced ASCII hyphen becomes a comma.
    Unspaced hyphens ("10-20", "Coca-Cola") and a minus inside a number ("-8%")
    are LEFT intact so numbers and proper nouns are never mangled."""
    if not text:
        return text
    out = re.sub(r"\s*[‒—―]\s*", ", ", text)
    out = re.sub(r"(?<=\d)\s*–\s*(?=\d)", " to ", out)
    out = re.sub(r"\s*–\s*", ", ", out)
    out = re.sub(r"\s+-\s+", ", ", out)
    out = re.sub(r"\s*,(?:\s*,)+", ",", out)   # collapse doubled commas
    out = re.sub(r"\s+,", ",", out)            # no space before a comma
    out = re.sub(r"\s{2,}", " ", out)
    return re.sub(r"^[\s,]+", "", out).strip()


def _post_process(text: str, word_cap: Optional[int] = None) -> str:
    """Strip stray markdown, leading labels, enclosing quotes; cap words."""
    if not text:
        return ""
    out = text.strip()

    # Drop a leading "Foo:" label if Gemini ignored the no-preamble rule.
    out = re.sub(r"^[A-Za-z][A-Za-z ]{1,30}:\s+", "", out, count=1)

    # Strip enclosing quotes (matched or mismatched smart quotes).
    while out and out[0] in _QUOTE_CHARS and out[-1] in _QUOTE_CHARS:
        out = out[1:-1].strip()

    # Strip any markdown bullets/asterisks/code fences that leaked through.
    out = re.sub(r"^[*\-•]+\s*", "", out)
    out = out.replace("```", "").strip()

    # Bulletproof backstop for the STYLE no-dash rule (see _strip_dashes).
    out = _strip_dashes(out)

    if word_cap and word_cap > 0:
        words = out.split()
        if len(words) > word_cap:
            out = " ".join(words[:word_cap]).rstrip(",;:") + "…"

    return out


# ── NarrativeJob: a single Stage-B call ───────────────────────────────


@dataclass
class NarrativeJob:
    """One Stage-B Gemini call. `apply` writes the result into the shell.

    Each job is independent — failures fall back to `fallback_value`
    so a single Gemini timeout never breaks the overall response.
    """
    label: str
    prompt: str
    word_cap: int
    apply: Callable[[Any], None]
    fallback_value: Any
    # Some fields (guidance_quote, wall_street_insight, ownership_note) are
    # legitimately optional. When True, an empty/whitespace response
    # becomes None instead of the fallback string.
    nullable: bool = False


async def run_narrative_jobs(
    jobs: List[NarrativeJob],
    gemini: GeminiClient,
    persona: PersonaConfig,
) -> None:
    """Execute every job in parallel. Each job's result lands in-place
    via `apply`; failures use the job's `fallback_value`.

    Never raises — even a total Gemini outage just leaves every
    narrative field on its honest fallback.
    """

    async def _one(job: NarrativeJob) -> None:
        try:
            result = await gemini.generate_text(
                prompt=job.prompt,
                system_instruction=persona.system_prompt,
            )
            raw = (result.get("text") or "")
            cleaned = _post_process(raw, word_cap=job.word_cap)
            if not cleaned:
                job.apply(None if job.nullable else job.fallback_value)
                return
            job.apply(cleaned)
        except Exception as e:
            logger.warning(
                f"Stage-B narrative {job.label} failed: "
                f"{type(e).__name__}: {e}"
            )
            job.apply(job.fallback_value)

    if not jobs:
        return
    await asyncio.gather(*(_one(j) for j in jobs))


# ── Per-field prompt builders ─────────────────────────────────────────
# Each builder returns the prompt text. Persona system_prompt is
# attached separately by the runner via `system_instruction`.


def _executive_summary_text_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    return f"""Write the Executive Summary — a GENERAL, plain-English overview that orients the reader before the detailed sections below.

EVIDENCE:
{evidence}

{_style_block(persona)}
LENGTH: Write 3-4 sentences, total under 65 words.

Cover, in order:
1. What the company is and does — its business and sector, in one plain sentence (use the company profile/description above).
2. How it's doing overall — the big-picture trajectory and financial health, in broad strokes.
3. The report's bottom-line take — the overall verdict in general terms.

Keep it GENERAL — this is the orientation, not the argument. Do NOT dump metrics or list pros/cons; the Bull/Bear case below carries the specific numbers. Use at most ONE light anchor number, and only if it genuinely helps."""


def _overall_assessment_text_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    avg = shell.get("overall_assessment", {}).get("average_rating", 0.0)
    strong = shell.get("overall_assessment", {}).get("strong_count", 0)
    weak = shell.get("overall_assessment", {}).get("weak_count", 0)
    # Cross-section coherence: the Fundamentals & Growth verdict should agree with
    # the forward trajectory (Future Forecast) and the revenue mix (Revenue
    # Engine) — e.g. don't call a business "stalling" when the forecast shows
    # strong forward growth. Surgical: only these two related modules.
    related = _related_context_block(
        "the Fundamentals & Growth quality verdict",
        cross_section_context(shell, ["forecast", "revenue_engine"]),
    )
    return f"""Write the overall quality read for the Fundamentals & Growth section — the whole-picture verdict across all four cards.

CONTEXT: Average rating {avg}/5 across four cards ({strong} strong, {weak} weak).
{related}
EVIDENCE:
{evidence}

{_style_block(persona)}
LENGTH: Write 3-4 sentences, total under 70 words.

Cover the whole picture of this section:
- Open with the gestalt verdict — is this a quality compounder, a fixer-upper, a value trap, or something else? Anchor it to one concrete metric.
- Then name the standout STRENGTH and the main WEAKNESS across the four cards (Profitability, Growth, Valuation, Financial Health), each tied to a specific card metric.
- Close with the bottom-line takeaway for an investor weighing this stock — and keep it consistent with the forward trajectory and revenue mix in the RELATED CONTEXT (don't call it stalling if the forecast shows strong forward growth, or a runaway compounder if growth is decelerating). Stay anchored to the fundamentals verdict; use the related context only to stay coherent, not as your subject.

Grounding rules — STRICT:
1. Any number you cite MUST come verbatim from the "CARD VALUES (AS DISPLAYED TO USER)" block in the evidence. NEVER invent or recompute a different value for a metric listed there.
2. If you reference Altman Z-Score, quote it exactly as shown in the Financial Health card (e.g. "Z-Score of 2.7"), not a separately-computed value from raw inputs.
3. If a metric shows "—" or "N/A" in the cards, do not invent a number for it — pick a different metric to anchor on."""


def _moat_durability_note_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    moat = shell.get("moat_competition", {})
    dims = moat.get("dimensions", [])
    top = max(dims, key=lambda d: float(d.get("score") or 0.0), default={})
    max_score = float(top.get("score") or 0.0)

    # Mirror the iOS rating thresholds (MoatOverallRating.from(dimensions:)
    # in TickerReportModels.swift) so the AI's qualitative tone matches
    # the WIDE/NARROW/NONE badge users see on the moat header. The
    # iOS app no longer renders a separate tagline — this insight now
    # carries the whole qualitative signal.
    if max_score >= 8.5:
        strength_hint = (
            "WIDE moat — convey ELITE, durable defense. "
            "Phrases like 'fortress', 'wonderfully sticky', 'hard to dislodge' fit."
        )
    elif max_score >= 7.0:
        strength_hint = (
            "NARROW moat — convey STRONG-BUT-BEATABLE defense. "
            "Phrases like 'sticky but beatable', 'credible threats remain', "
            "'durable but not impregnable' fit."
        )
    else:
        strength_hint = (
            "NO moat — convey LACK of structural advantage. "
            "Phrases like 'exposed', 'no real defense', 'commodity-like' fit."
        )

    # Competitor + relative-moat context so the insight can speak to HOW the
    # company competes, not just how strong its moat is in isolation. At
    # Stage B time `shell` is the assembled report, so competitors and the
    # per-dimension peer_score baselines are already populated.
    competitors = moat.get("competitors", []) or []
    comp_str = ", ".join(
        f"{c.get('name')} ({c.get('ticker')}, threat={c.get('threat_level')})"
        for c in competitors[:4]
    ) or "none listed"
    edges = [
        d for d in dims
        if d.get("peer_score") is not None
        and float(d.get("score") or 0.0) - float(d.get("peer_score") or 0.0) >= 1.0
    ]
    gaps = [
        d for d in dims
        if d.get("peer_score") is not None
        and float(d.get("peer_score") or 0.0) - float(d.get("score") or 0.0) >= 1.0
    ]
    edge_str = ", ".join(
        f"{d.get('name')} ({d.get('score')} vs peer {d.get('peer_score')})"
        for d in edges[:3]
    ) or "no dimension clearly beats peers"
    gap_str = ", ".join(
        f"{d.get('name')} ({d.get('score')} vs peer {d.get('peer_score')})"
        for d in gaps[:3]
    ) or "no material gaps"

    return f"""Write the moat & competitive read for this company — how durable its moat is AND how it competes with its rivals.

TOP MOAT SOURCE: {top.get('name', 'unknown')} (score {top.get('score', 0)}/10)
ALL DIMENSIONS: {", ".join(f"{d.get('name')} {d.get('score')}/10" for d in dims) or "none"}
MOAT STRENGTH: {strength_hint}
WHERE IT OUT-DEFENDS PEERS (focal vs peer-avg): {edge_str}
WHERE IT TRAILS PEERS: {gap_str}
KEY COMPETITORS: {comp_str}

{_style_block(persona)}
LENGTH: Write 2-3 sentences, total under 55 words.

Sentence 1 — judge how durable the moat is (its staying power or the specific threat to it); weave the moat-strength tone naturally, don't just restate the score.
Sentence 2-3 — how it competes with the named rivals: where its moat out-defends them (use the edge above) and which competitor is the real threat (the highest threat_level). Name actual competitors, not "peers" in the abstract."""


def _moat_competitive_insight_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    moat = shell.get("moat_competition", {})
    competitors = moat.get("competitors", [])
    comp_str = ", ".join(
        f"{c.get('name')} ({c.get('ticker')}, threat={c.get('threat_level')})"
        for c in competitors[:4]
    ) or "none listed"
    return f"""Summarize the competitive landscape for this stock in one sentence.

COMPETITORS: {comp_str}

EVIDENCE:
{evidence}

{_style_block(persona)}
{_length_brief(1, 25)}

Lead with the actual market dynamic (winner-take-most, fragmented, two-horse race, etc.), not the company name."""


def _macro_intelligence_brief_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    macro = shell.get("macro_data", {})
    threat = macro.get("overall_threat_level", "low")
    factors = macro.get("risk_factors", [])
    factor_str = "; ".join(
        f"{f.get('title')} ({f.get('severity')}, {f.get('trend')})"
        for f in factors[:6]
    ) or "no significant macro risks tripping thresholds"
    return f"""Write the Macro "Insight" — a tight read that captures the WHOLE macro backdrop and lands it on how it hits THIS company.

OVERALL THREAT LEVEL: {threat}
ACTIVE MACRO RISK FACTORS (deterministic data + web-grounded geopolitical events): {factor_str}

EVIDENCE (company fundamentals):
{evidence}

{_style_block(persona)}
LENGTH: Write 2-3 sentences, total under 65 words.

Synthesize the set into a verdict, then make it company-specific:
- Open by naming the overall threat level and the 1-2 DOMINANT drivers from the factors above — name the real driver (rate policy, a war / tariff / sanctions event, inflation, credit stress, etc.), never a vague "macro headwinds".
- Then explain how those drivers actually hit THIS business and cite ONE concrete number from EVIDENCE that proves the exposure (debt/equity, interest coverage, foreign-revenue %, capex intensity, margin, etc.).
- If a sentence remains, end with the concrete macro shift that would flip the picture.
The goal is the company impact — don't just list the factors back."""


def _price_action_narrative_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    pa = shell.get("price_action", {}) or {}
    event = pa.get("event") or {}
    event_str = (
        f"{event.get('tag')} on {event.get('date')}"
        if event else "no specific catalyst flagged in the window"
    )

    # Up to 20 most-recent matched headlines (collector bumped FMP news
    # count 5→20 for this section). Ground the narrative in real articles
    # rather than letting the AI speculate.
    headlines = pa.get("_news_headlines") or []
    if headlines:
        headlines_str = "\n".join(
            f"- [{h.get('date')}] {h.get('tag')}: {h.get('title')} "
            f"({h.get('site') or 'wire'})"
            for h in headlines[:20]
        )
        headlines_block = (
            f"\nRECENT MATCHED HEADLINES (within the chart window):\n{headlines_str}\n"
        )
    else:
        headlines_block = "\nRECENT MATCHED HEADLINES: none in window\n"

    # Web-grounded reason (primary evidence when present): a current web search
    # found WHY the stock moved (price_catalyst_service, big moves only). When
    # set, the narrative should LEAD with this over the FMP headlines.
    grounded_reason = pa.get("_grounded_reason")
    grounded_block = (
        f"\nWEB-GROUNDED REASON (PRIMARY — current web search; lead with this):\n"
        f"  {grounded_reason}\n"
        if grounded_reason else ""
    )

    # Macro context — fed stance, sector rotation, geopolitics. Reuse the
    # report's own DETERMINISTIC macro_data (threat level + risk factors, incl.
    # web-grounded geopolitical events) so the AI can attribute moves like
    # "sector rotation" or "war" without paying for a second grounded search.
    # NOTE: we deliberately do NOT read macro_data["intelligence_brief"] — that
    # is a Stage-B prose field written in PARALLEL with this one, so at build
    # time it still holds the assemble-time placeholder ("Data unavailable for
    # this ticker."). Surfacing it injected that junk sentence into this prompt.
    # The threat level + risk_factors ARE populated at assemble time. Ungated on
    # threat (unlike the thesis digest): an "elevated" war/tariff/Fed factor is a
    # legitimate driver for a price move.
    macro = shell.get("macro_data") or {}
    threat = str(macro.get("overall_threat_level") or "low")
    risk_factors = macro.get("risk_factors") or []
    macro_block = ""
    if risk_factors:
        top_risks = "\n".join(
            f"  - {r.get('title')} ({r.get('category')}, "
            f"severity={r.get('severity')}, trend={r.get('trend')})"
            for r in risk_factors[:4]
        )
        macro_block = (
            f"\nMACRO / GEOPOLITICAL CONTEXT (deterministic — current):\n"
            f"  Overall threat level: {threat}\n{top_risks}\n"
        )
    elif threat.lower() not in ("low", ""):
        macro_block = (
            f"\nMACRO / GEOPOLITICAL CONTEXT (deterministic — current):\n"
            f"  Overall threat level: {threat}\n"
        )

    # Ground truth — direction / magnitude / window are computed
    # deterministically in _build_price_action. iOS renders the same
    # numbers, so the narrative cannot contradict the chart.
    direction = pa.get("direction", "flat")
    change_pct = pa.get("change_pct", 0.0)
    window_label = pa.get("window_label", "Last 30 Days")
    if direction == "flat":
        ground_truth = (
            f"The stock is roughly FLAT ({change_pct:+.1f}%) over "
            f"{window_label.lower()}."
        )
    else:
        ground_truth = (
            f"The stock is {direction.upper()} {change_pct:+.1f}% over "
            f"{window_label.lower()}."
        )

    # Volatility posture — biases the conclusion sentence without forcing
    # the AI to quote σ/z numbers. User wants the prose to focus on WHY,
    # not on math; the σ math lives in the iOS sub-label.
    tier = pa.get("tier") or "Typical"
    z_score = pa.get("z_score")
    if tier == "Extreme":
        posture = (
            "This move is statistically extreme (≈3σ+) for this stock's own "
            "history. Treat it as a real, meaningful signal — the conclusion "
            "should lean toward a fundamental change unless the catalyst is "
            "clearly macro/external (war, fed, oil shock)."
        )
    elif tier == "Unusual":
        posture = (
            "This move is unusual (≈2σ) for this stock — outside its normal "
            "monthly range. Lean toward a meaningful read; let the catalyst "
            "data decide whether it's fundamental or external."
        )
    elif tier == "Notable":
        posture = (
            "This move is above average but still within ~2σ of normal. "
            "Let the catalyst data decide — it could be either a real "
            "business signal or amplified noise."
        )
    else:  # Typical
        posture = (
            "This move is within the stock's normal range (within ±1σ). "
            "The default read is short-term noise unless the catalyst data "
            "above strongly says otherwise."
        )

    # Branch the sentence template on tier. Python-side branching keeps
    # the LLM-facing prompt tight and removes the implicit pressure for
    # the AI to attribute a Typical (within-±1σ) move to a single driver.
    if tier == "Typical":
        write_block = """WRITE 2-3 SENTENCES (TYPICAL MOVE — within ±1σ):

Sentence 1 — STATE PLAINLY that this move is within the stock's normal
  monthly range. Do NOT open by assigning a catalyst. Use phrasing like
  "This is a normal-range move — well within the stock's typical monthly
  band" or "Trading inside its usual envelope; nothing statistically
  unusual here."

Sentence 2 (OPTIONAL) — If a strong headline is in the window, mention it
  as CONTEXT only ("came alongside X" / "X was in the news"), NOT as the
  driver of the move. SKIP this sentence entirely if no notable headline.

Final sentence — Classify this as routine trading inside the stock's normal
  range, not a real change in the business. You may name a macro or sector
  backdrop if relevant.

NEVER contradict GROUND TRUTH direction. DO NOT quote σ, z-score, or any
volatility math — that lives in the chart sub-label. DO NOT write phrases
like "pushing the stock up X%" or "spurred investor confidence" for a
within-range move — the whole point is that this magnitude is unremarkable
for this stock."""
    else:
        # Notable / Unusual / Extreme — catalyst-led, classify at the end.
        write_block = """WRITE 2-3 SENTENCES:

Sentence 1-2 — Name the primary reason for the move. Use the catalysts above:
  - if there's an EVENT (earnings/news catalyst), lead with it (cite the headline if available)
  - otherwise lead with the strongest macro/sector driver (sector rotation, fed, yields, war, oil, etc.)
  - otherwise say plainly that no single catalyst explains it — broader market drift
  Cite a specific source from the headlines or macro block when you can.

Final sentence — Judge what KIND of move this is, decided from the evidence
(do NOT default to one verdict): either a genuine, durable shift in the
company's own business/economics (e.g. a major contract or customer win, a
secular tailwind it is capturing, accelerating revenue, raised guidance, a
strengthening moat — or the negative mirror of any of these), OR a temporary,
market-driven move (macro fears, sector rotation, broad risk-off, sentiment,
or no clear cause). Be specific about WHAT changed, in plain language.

NEVER contradict GROUND TRUTH direction. If headlines suggest the opposite of
the chart, treat the move as market-driven (a repricing through this name).
If everything is FLAT, write one sentence saying so and skip the catalyst hunt.

DO NOT quote σ, z-score, or any volatility math (it lives in the chart
sub-label), and DO NOT shout ALL-CAPS labels like "FUNDAMENTAL"/"NOISE" — write
natural prose. Focus on the WHY and whether it's a real business shift or a
temporary market move."""

    return f"""Explain WHY the stock moved and what kind of signal this is.

GROUND TRUTH: {ground_truth}
CATALYST EVENT IN WINDOW: {event_str}
{grounded_block}{headlines_block}{macro_block}
POSTURE: {posture}

EVIDENCE:
{evidence}

{_style_block(persona)}
{_length_brief(3, 60)}

{write_block}"""


def _revenue_engine_analysis_note_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    re_section = shell.get("revenue_engine", {}) or {}
    segs = re_section.get("segments", []) or []
    unit = re_section.get("revenue_unit", "Millions")

    # Pre-compute YoY % and share-of-total per segment so the model
    # doesn't do arithmetic (it's bad at it) and so the prompt can
    # pick a frame deterministically.
    enriched: List[Dict[str, Any]] = []
    for s in segs[:5]:
        name = s.get("name") or "Unknown"
        curr = float(s.get("current_revenue") or 0)
        prior = float(s.get("previous_revenue") or 0)
        total = float(s.get("total_revenue") or 0)
        yoy_pct = ((curr - prior) / prior * 100) if prior > 0 else None
        share_pct = (curr / total * 100) if total > 0 else 0
        if yoy_pct is None:
            yoy_label = "YoY n/a"
        else:
            yoy_label = f"{yoy_pct:+.1f}% YoY"
        enriched.append({
            "name": name, "curr": curr, "prior": prior,
            "yoy_pct": yoy_pct, "yoy_label": yoy_label,
            "share_pct": share_pct,
        })

    if not enriched:
        seg_str = "no segment breakdown available"
        frame_hint = "Note the breakdown is unavailable and keep it short."
    else:
        seg_str = "; ".join(
            f"{s['name']} {s['curr']:.0f} ({s['share_pct']:.0f}% of total, {s['yoy_label']})"
            for s in enriched
        )

        # Frame hint — pick the most interesting story based on the data.
        # Rising threshold sits at 10% so mature-large-cap segments (Apple
        # Services, Oracle Cloud) get tagged; fading at -5% catches a real
        # decline without flagging noise.
        rising = [s for s in enriched if s["yoy_pct"] is not None and s["yoy_pct"] >= 10]
        fading = [s for s in enriched if s["yoy_pct"] is not None and s["yoy_pct"] <= -5]
        top = enriched[0]

        if rising and fading:
            frame_hint = (
                f"There's a clear mix rotation: {rising[0]['name']} is rising "
                f"({rising[0]['yoy_label']}) while {fading[0]['name']} is fading "
                f"({fading[0]['yoy_label']}). Lead with the rotation."
            )
        elif rising:
            frame_hint = (
                f"{rising[0]['name']} is the engine pulling growth forward "
                f"({rising[0]['yoy_label']}). Lead with what that means for the mix."
            )
        elif fading:
            frame_hint = (
                f"{fading[0]['name']} is dragging the mix ({fading[0]['yoy_label']}). "
                f"Lead with the drag and what's compensating."
            )
        elif top["share_pct"] >= 70:
            frame_hint = (
                f"The engine is concentrated — {top['name']} carries "
                f"{top['share_pct']:.0f}% of revenue. Name the concentration."
            )
        else:
            frame_hint = (
                "No segment is breaking out or breaking down. Say the mix is "
                "steady and what that means for predictability."
            )

    return f"""Write a one-line takeaway on the revenue engine.

SEGMENTS ({unit}): {seg_str}

FRAME: {frame_hint}

{_style_block(persona)}
{_length_brief(1, 25)}

Use the YoY numbers above — don't invent them, don't restate them as a list.
Name what is shifting (or what is concentrated) and what it means for the business."""


def _revenue_forecast_guidance_quote_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    guidance = (
        shell.get("revenue_forecast", {}).get("management_guidance")
        or "maintained"
    )
    return f"""Paraphrase what management has effectively been telling the Street about forward growth, in their voice.

OFFICIAL GUIDANCE STANCE: {guidance}

EVIDENCE:
{evidence}

{_style_block(persona)}
LENGTH: 1-2 sentences, total under 30 words.

If there's no real guidance signal in the data, write the literal word: NULL"""


def _revenue_forecast_insight_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    """The Future Forecast insight — explains WHY the forward revenue / EPS
    trajectory looks the way it does, grounded in the projection bars the
    user sees on the chart plus management's guidance stance."""
    rf = shell.get("revenue_forecast", {}) or {}
    cagr = rf.get("cagr")
    eps_growth = rf.get("eps_growth")
    guidance = rf.get("management_guidance") or "maintained"
    guidance_quote = rf.get("guidance_quote")
    projections = rf.get("projections") or []

    # Compact the chart-visible projections so the model anchors on the same
    # numbers the user sees on the bars (no re-deriving arithmetic). Defensive
    # against the fallback-shell path where projections is empty.
    if projections:
        proj_str = "; ".join(
            f"{p.get('period')}: rev {p.get('revenue_label')}"
            + (
                f" ({p.get('revenue_yoy_pct'):+.0f}% YoY)"
                if p.get("revenue_yoy_pct") is not None else ""
            )
            + f", EPS {p.get('eps_label')}"
            + (
                f" ({p.get('eps_yoy_pct'):+.0f}% YoY)"
                if p.get("eps_yoy_pct") is not None else ""
            )
            for p in projections[:4]
        )
    else:
        proj_str = "no analyst projections available"

    cagr_str = f"{cagr:+.0f}%" if isinstance(cagr, (int, float)) else "n/a"
    eps_str = f"{eps_growth:+.0f}%" if isinstance(eps_growth, (int, float)) else "n/a"

    # EPS beat/miss history (the EPS Track Record) → lets the read weigh the
    # forecast's CREDIBILITY, not just its shape: steady beats back an
    # accelerating curve, chronic misses undercut it.
    track = rf.get("earnings_track_record") or []
    if track:
        avg_surprise = sum(q.get("surprise_percent", 0.0) for q in track) / len(track)
        track_str = (
            f"{rf.get('beat_summary') or 'mixed'}, "
            f"avg EPS surprise {avg_surprise:+.1f}% over the last {len(track)} quarters"
        )
    else:
        track_str = "no reported beat/miss history"

    # Recent ACTUAL revenue YoY (the timeline's historical years) so the read can
    # frame the forecast as a step-up or a fade vs the real, recent trend.
    actuals = [t for t in (rf.get("annual_timeline") or []) if not t.get("is_forecast")]
    hist_yoys = [t.get("revenue_yoy_pct") for t in actuals if t.get("revenue_yoy_pct") is not None]
    hist_str = (
        "recent actual revenue YoY " + ", ".join(f"{y:+.0f}%" for y in hist_yoys[-3:])
        if hist_yoys else "recent actual history unavailable"
    )

    quote_line = (
        f'\nMANAGEMENT GUIDANCE QUOTE: "{guidance_quote}"' if guidance_quote else ""
    )
    # Cross-section coherence: the forward trajectory's WHY is sharpest when it
    # can name the actual revenue driver — so hand it the Revenue Engine's
    # per-segment YoY mix. Surgical: only this one related module.
    related = _related_context_block(
        "the forward revenue/EPS trajectory",
        cross_section_context(shell, ["revenue_engine"]),
    )

    return f"""Write the Future Forecast insight — explain WHY the forward revenue and earnings trajectory looks the way it does.

PROJECTED REVENUE CAGR: {cagr_str}    PROJECTED EPS GROWTH: {eps_str}
MANAGEMENT GUIDANCE STANCE: {guidance} (raised / maintained / lowered)
EPS BEAT/MISS TRACK RECORD: {track_str}
HISTORICAL TREND (for contrast): {hist_str}
FORWARD PROJECTIONS (as charted): {proj_str}{quote_line}
{related}
EVIDENCE:
{evidence}

{_style_block(persona)}
LENGTH: Write 3-4 sentences, total under 70 words. Density over length — every clause must earn its place; do not pad to hit the count.

Focus on the WHY and on whether to BELIEVE it, not just the numbers:
- What is driving the projected growth (or the slowdown) — name the actual driver, and when the RELATED CONTEXT shows a segment leading or dragging the mix, tie the forward curve to it (e.g. "cloud, already +33% YoY, carries the forward curve"). Think demand, mix shift, margin leverage, pricing, a maturing base, or headwinds.
- Whether the forward curve accelerates or decelerates — and how that reads against the recent ACTUAL trend (a sharp step-up vs history is a bolder claim than more of the same).
- How much to TRUST the curve: read the guidance stance ({guidance}) together with the EPS beat/miss track record — a raise backed by steady beats is credible; an ambitious forecast from a chronic misser is suspect.
Anchor on ONE concrete projected number; you may sharpen it against the track record or the historical trend. Do NOT just list the projections back."""


def _hidden_market_signals_insight_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    """2-4 sentence synthesis of the Hidden Market Signals module — what the
    congressional trades and short selling TOGETHER imply for the stock, with a
    severity read on the short-selling level. Grounded only in the numbers the
    user sees on the card (via the hidden-signals digest)."""
    signals = _related_context_block(
        "the hidden positioning signals",
        cross_section_context(shell, ["hidden_signals"]),
    )
    return f"""Write the Hidden Market Signals insight: in 2 to 4 sentences, what congressional trading and short selling TOGETHER imply for the stock, and the likely effect on the shares.
{signals}
{_style_block(persona)}
LENGTH: 2 to 4 sentences. Lean to 2 if only one signal is notable, 3 to 4 only if both carry weight. If both are quiet, 1 to 2 sentences saying positioning looks unremarkable.

Cover BOTH signals and connect them to the stock:
* SHORT SELLING: state the % of float and what that LEVEL means (the bracketed tag above is authoritative). Under 10% is low, little bearish conviction. 10 to 20% is elevated, a real bearish camp forming. 20 to 30% is high, a serious short squeeze watch. 30% or more is extreme. If "squeeze fuel" is flagged (days to cover at least 5), note shorts cannot exit quickly. ALWAYS read the vs 3mo change: a rising value means bearish bets are BUILDING, a falling value means shorts are COVERING (pressure easing, often bullish).
* CONGRESS: the net of buyers versus sellers and which way it leans.
* EFFECT: say what it means for the shares. For example, high or rising short selling is a sentiment headwind and downward pressure (but elevated squeeze risk if good news lands), shorts covering is a tailwind, and politicians buying while shorts build is a tension worth flagging.
* If the two signals conflict, name that tension, because the contrast IS the signal.

RULES:
* Ground EVERY claim in the numbers above and cite the key ones (e.g. "11% of float", "up 8% vs 3mo", "4 sellers"). NEVER invent a number.
* No hype. If a level is low, say so plainly and do not manufacture drama."""


def _key_management_insight_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    """2 to 3 sentence section insight for "Insider & Management" that
    synthesizes THREE topics into one read on alignment + capital stewardship:
    1. Ownership / management: the dominant 10%+ holder's stake (structural
       alignment).
    2. Insider activity: recent buy/sell flow, applying the Lynch BUY/SELL
       signal asymmetry (insiders buy for one reason, sell for many).
    3. Capital allocation: dividends plus whether the company is returning
       capital or NET-diluting (stock-comp issuance outpacing buybacks).
    """
    km = shell.get("key_management", {}) or {}
    # `top_holders` is the curated 10%+ list from `_build_key_management`
    # (sorted by % desc). When non-empty, the recent 90-day flow has to
    # be judged *against* that base — a $2.6M director sale is rounding
    # error on a $215B founder stake, but it still happened and should
    # be named, not dismissed.
    top_holders = km.get("top_holders") or []
    major = top_holders[0] if top_holders else None
    if major:
        anchor_line = (
            f"DOMINANT HOLDER: {major.get('name')} holds "
            f"{major.get('percent_ownership')}% "
            f"({major.get('ownership_value')}). Judge recent activity "
            f"against this base — but name the activity, don't dismiss it."
        )
    else:
        anchor_line = (
            "DOMINANT HOLDER: none holds 5%+. Judge by aggregate "
            "buy/sell flow alone."
        )

    insider = shell.get("insider_data") or {}
    sentiment = insider.get("sentiment", "neutral")
    transactions = insider.get("transactions") or []
    buys = next(
        (t for t in transactions if (t.get("type") or "").lower() == "buys"),
        {},
    )
    sells = next(
        (t for t in transactions if (t.get("type") or "").lower() == "sells"),
        {},
    )
    has_buy = (buys.get("count") or 0) > 0
    has_sell = (sells.get("count") or 0) > 0

    if has_buy and has_sell:
        flow_state = "BOTH buys and sells in window — lead with the buy (higher signal)"
    elif has_buy:
        flow_state = "BUYS only — high-conviction signal, lead with it"
    elif has_sell:
        flow_state = (
            "SELLS only — acknowledge but qualify the motive "
            "(tax, diversification, estate planning, scheduled 10b5-1 plan)"
        )
    else:
        flow_state = "NO transactions — focus on structural alignment, not flow"

    # Topic 3: capital allocation (now part of this section). INTERPRET the
    # stewardship signal, don't restate the card's figures. Net share-count
    # change is the tell — a company can buy back stock yet still dilute when
    # stock-comp issuance outpaces it.
    ca = insider.get("capital_allocation") or {}
    if not ca:
        capital_line = "Data unavailable; cover only ownership and insider flow."
    else:
        scc = ca.get("share_count_change")
        div_yield = ca.get("dividend_yield") or 0
        dps = ca.get("data_points") or []
        newest_bb = (dps[-1].get("buyback_amount") if dps else 0) or 0
        div_desc = (
            f"pays a dividend (about {div_yield}% yield)"
            if div_yield > 0 else "pays no dividend"
        )
        if scc is not None and scc > 2.0:
            steward = (
                "is NET-DILUTING: the share count is rising"
                + (
                    " even though it buys back some stock, so stock-comp "
                    "issuance is outpacing repurchases"
                    if newest_bb > 0
                    else " and it is not repurchasing stock"
                )
                + ", eroding per-share ownership"
            )
        elif scc is not None and scc < -2.0:
            steward = (
                "is RETURNING capital: real buybacks are shrinking the share "
                "base and concentrating per-share ownership"
            )
        else:
            steward = (
                "keeps the share count roughly flat, neither meaningfully "
                "diluting nor shrinking it"
            )
        capital_line = f"The company {div_desc} and {steward}."

    return f"""Write a 2 to 3 sentence insight for the "Insider & Management" section. Synthesize the THREE topics below into ONE read on how aligned management is with shareholders and how well they steward capital. Weave them; do NOT list them separately.

TOPIC 1 (OWNERSHIP / MANAGEMENT):
{anchor_line}

TOPIC 2 (INSIDER ACTIVITY, recent flow):
Sentiment: {sentiment}. {flow_state}

TOPIC 3 (CAPITAL ALLOCATION):
{capital_line}

EVIDENCE:
{evidence}

{_style_block(persona)}
LENGTH: 2 to 3 sentences, under 60 words.

SIGNAL ASYMMETRY (most important rule, Peter Lynch):
- BUYS carry HIGH signal. Insiders buy for one reason only: they expect the stock to rise. Even a small buy alongside a dominant holder is a strong endorsement.
- SELLS carry LOW to medium signal. They happen for many reasons (tax, diversification, estate, scheduled 10b5-1); name selling but qualify the motive, never treat it as proof of a bearish view.
- A dominant holder's large stake structurally aligns interests but does NOT erase selling or dilution.

HOW TO WEAVE (synthesis, not a list):
- Open on ownership alignment plus the insider buy/sell signal.
- Then capital-allocation stewardship: are they returning capital or net-diluting shareholders? Dilution despite buybacks (stock-comp heavy) is the key tell; say it plainly.
- Close on the combined implication: aligned and shareholder-friendly, aligned but diluting, misaligned, or too mixed to act on.

RULES:
- INTERPRET; do NOT restate exact buy/sell counts, share totals, dollar values, the dividend yield, or the dilution percentage already shown in the cards.
- If capital-allocation data is unavailable, cover only ownership and insider flow.

FORBIDDEN PHRASES (dismissive or restating the visible cards):
- "suggesting management isn't increasing their stake"
- "indicates a lack of skin in the game"
- "minor insider selling"
- "only a $X million sale"
- "no buys and N sells"
"""


def _insider_key_insight_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    insider = shell.get("insider_data", {}) or {}
    sentiment = insider.get("sentiment", "neutral")
    txns = insider.get("transactions", [])
    return f"""Write the catchy headline takeaway shown next to the insider score.

SENTIMENT: {sentiment}
TRANSACTIONS: {txns}

{_style_block(persona)}
LENGTH: One short fragment under 15 words. NO period.

Lead with the action (who/what), e.g., "CFO selling into the rip" or "Net buying despite the selloff"."""


def _fundamental_quality_label_prompt(
    persona: PersonaConfig,
    evidence: str,
    card: Dict[str, Any],
) -> str:
    title = card.get("title", "Card")
    rating = card.get("star_rating", 3)
    metrics = card.get("metrics", [])
    metric_str = ", ".join(
        f"{m.get('label')}={m.get('value')}" for m in metrics
    ) or "no metrics"
    return f"""Write a 3-5 word verdict label for this fundamentals card.

CARD: {title}
RATING: {rating}/5 stars
METRICS: {metric_str}

{_style_block(persona)}
LENGTH: 3 to 5 words. NO period. Title-case if it reads like a noun phrase, sentence-case otherwise.

PLAIN ENGLISH — STRICT:
Write for a non-finance reader. Do NOT use these jargon terms or their close synonyms: "Owner Earnings", "Margin of Safety", "Free Cash Flow", "FCF", "Operating Leverage", "Compounding", "Premium Multiple", "Capital Allocation". Translate the concept instead:
  - "Burning Cash" — not "Negative FCF" / "Owner Earnings Vanishing"
  - "Pricey Stock" / "Too Pricey vs. Sector" — not "Premium Multiple" / "Rich Price"
  - "Heavy Debt Load" — not "High Leverage"
  - "Slowing Sales" — not "Top-Line Decel"
  - "Cheap For A Reason" — not "Value Trap"

Examples of good output:
  - "Fat Margins, High Debt"
  - "Slowing Sales, Steady Profits"
  - "Pricey Stock, Real Growth"
  - "Heavy Debt Load"
  - "Burning Cash Quarterly"
  - "Too Pricey vs. Sector"

Pick something specific to the metrics above, not generic praise. Anchor on the most striking number in the card."""


def _critical_factor_description_prompt(
    persona: PersonaConfig,
    evidence: str,
    factor: Dict[str, Any],
) -> str:
    title = factor.get("title", "Factor")
    severity = factor.get("severity", "medium")
    return f"""Write the SIGNAL line for this watch item — what's notable about it right now and why it matters — in one tight sentence.

FACTOR: {title}
PRIORITY: {severity}

EVIDENCE:
{evidence}

{_style_block(persona)}
LENGTH: Write 1 sentence, under 20 words.

State the concrete situation (the number or fact) and why it matters. This is the SIGNAL only — do NOT give a verdict or advice; the "what to watch next" is written separately.

Grounding rule — STRICT: any number you cite MUST appear verbatim in the EVIDENCE above. If the figure isn't in the evidence, describe the situation qualitatively — NEVER invent a number."""


def _critical_factor_watch_prompt(
    persona: PersonaConfig,
    evidence: str,
    factor: Dict[str, Any],
) -> str:
    title = factor.get("title", "Factor")
    severity = factor.get("severity", "medium")
    return f"""Write the WATCH line for this item — the forward-looking next step the investor should monitor.

FACTOR: {title}
PRIORITY: {severity}

EVIDENCE:
{evidence}

{_style_block(persona)}
LENGTH: Write 1 sentence, under 20 words. Do NOT begin with the word "Watch" — the UI already prints a "Watch:" label, so lead with the thing to track or another imperative verb (Track / Confirm / Check).

Say specifically WHAT to track and WHEN, and what would confirm improvement or deterioration. Be concrete and time-bound when you can ("next earnings", "the next 10-Q", "Q3 guidance"). Shape examples: "Next earnings — is operating cash flow catching up to capex?"; "Track whether net debt falls as the buildout slows."

If there is genuinely nothing actionable to monitor, write the literal word: NULL"""


# The Wall Street Consensus "Insight" — a big-picture synthesis across all three
# sub-sections the user sees in that card: Analyst Price Target, Institutions
# (FMP 13F flow), and Momentum (12-month analyst upgrades/maintains/downgrades).
def _wall_street_insight_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    # Surface the EXACT values rendered in the Wall Street Consensus card so the
    # insight reflects what the user sees — not a re-derivation from the dense
    # evidence text. Mirrors the "cite displayed values" discipline used by the
    # Overall Assessment insight. All numbers are computed Python-side (the model
    # is bad at arithmetic); the prompt only synthesizes.
    ws = shell.get("wall_street_consensus") or {}

    # ── Analyst Price Target (+ Python-computed analyst-target upside) ──
    cur = ws.get("current_price")
    tgt = ws.get("target_price")  # None when FMP returned no real consensus
    if tgt:
        try:
            upside = (
                (float(tgt) - float(cur)) / float(cur) * 100.0 if cur else None
            )
        except (TypeError, ValueError, ZeroDivisionError):
            upside = None
        lo, hi = ws.get("low_target"), ws.get("high_target")
        range_str = (
            f", range ${float(lo):.0f}–${float(hi):.0f}"
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo and hi
            else ""
        )
        upside_str = (
            f" ({upside:+.0f}% vs ${float(cur):.0f} current)"
            if upside is not None else ""
        )
        target_line = f"Analyst price target: ${float(tgt):.0f}{upside_str}{range_str}"
    else:
        target_line = "Analyst price target: no analyst coverage (no published consensus)"

    # ── Consensus rating + Buy/Hold/Sell distribution ──────────────────
    rating = str(ws.get("rating") or "").replace("_", " ") or "n/a"
    dist_parts = []
    for label, key in (
        ("Strong Buy", "analyst_strong_buy"), ("Buy", "analyst_buy"),
        ("Hold", "analyst_hold"), ("Sell", "analyst_sell"),
        ("Strong Sell", "analyst_strong_sell"),
    ):
        n = ws.get(key)
        if isinstance(n, int) and n > 0:
            dist_parts.append(f"{n} {label}")
    dist_str = (" — " + ", ".join(dist_parts)) if dist_parts else ""
    rating_line = f"Consensus rating: {rating}{dist_str}"

    # ── DCF valuation lens — DISTINCT from the analyst-target upside ────
    val_status = str(ws.get("valuation_status") or "").replace("_", " ")
    disc = ws.get("discount_percent")
    if val_status:
        disc_str = (
            f", {disc:.0f}% below DCF fair value"
            if isinstance(disc, (int, float)) and disc else ""
        )
        valuation_line = (
            f"DCF valuation (model-implied, distinct from the analyst target): "
            f"{val_status}{disc_str}"
        )
    else:
        valuation_line = "DCF valuation: n/a"

    # ── Institutions (13F net informative flow) ────────────────────────
    smart = ws.get("hedge_fund_smart_money") or {}
    summ = (smart.get("summary") if isinstance(smart, dict) else None) or {}
    net = summ.get("total_net_flow")
    if isinstance(net, (int, float)) and net:
        direction = "net buying" if summ.get("is_positive") else "net selling"
        period = summ.get("period_description") or "recent quarters"
        institutions_line = (
            f"Institutions (13F, {period}): {direction}, "
            f"{net:+.1f}M shares net informative flow"
        )
    else:
        institutions_line = "Institutions (13F): no institutional flow data"

    # ── Analyst rating momentum (last 12 months) ───────────────────────
    up, maint, down = (
        ws.get("momentum_upgrades"),
        ws.get("momentum_maintains"),
        ws.get("momentum_downgrades"),
    )
    momentum_line = (
        f"Analyst momentum (12mo): {up} upgrades / {maint} maintains / {down} downgrades"
        if up is not None else "Analyst momentum: no recent rating changes"
    )

    return f"""Write the Wall Street Consensus insight — the big-picture read that ties together the analyst price target, institutional (13F) positioning, and analyst rating momentum.

DISPLAYED VALUES (exactly what the user sees in the Wall Street Consensus card):
- {target_line}
- {rating_line}
- {valuation_line}
- {institutions_line}
- {momentum_line}

EVIDENCE (for catalyst context only — financings, acquisitions, guidance changes):
{evidence}

{_style_block(persona)}
{_length_brief(2, 45)}

Synthesize the THREE signals — price target, institutions (13F), and rating momentum: where do they AGREE or DIVERGE? Lead with the dominant signal and cite a concrete number from the DISPLAYED VALUES (the target, the upside %, net institutional shares, or an upgrade count). If a specific catalyst sits in the evidence (a financing, acquisition, guidance change), name it. Do NOT just list the three — give the verdict that ties them together.

{_displayed_values_grounding("DISPLAYED VALUES block")}
The analyst-target upside and the DCF valuation are DIFFERENT lenses — do not merge them; if they disagree, that divergence IS worth calling out.
If analyst coverage is absent (target shows "no analyst coverage"), do not fabricate a target — pivot the read to institutions + momentum.

If the data doesn't show a clear pattern, write the literal word: NULL"""


# ── Job builder: turn a structured shell into a list of jobs ─────────


def build_narrative_jobs(
    persona: PersonaConfig,
    evidence: str,
    shell: Dict[str, Any],
) -> List[NarrativeJob]:
    """Walk the structured shell and emit one NarrativeJob per narrative
    field. Each job's `apply` mutates the shell in place when run."""
    jobs: List[NarrativeJob] = []

    # ── executive_summary_text (general overview; bullets removed) ────
    jobs.append(NarrativeJob(
        label="executive_summary_text",
        prompt=_executive_summary_text_prompt(persona, evidence, shell),
        word_cap=80,
        apply=lambda v: shell.__setitem__("executive_summary_text", v),
        fallback_value=FALLBACK["executive_summary_text"],
    ))

    # ── overall_assessment.text ───────────────────────────────────────
    if isinstance(shell.get("overall_assessment"), dict):
        oa = shell["overall_assessment"]
        jobs.append(NarrativeJob(
            label="overall_assessment_text",
            prompt=_overall_assessment_text_prompt(persona, evidence, shell),
            word_cap=90,
            apply=_setter_for_dict_key(oa, "text"),
            fallback_value=FALLBACK["overall_assessment_text"],
        ))

    # ── moat: durability + competitive insight ────────────────────────
    moat = shell.get("moat_competition")
    if isinstance(moat, dict):
        jobs.append(NarrativeJob(
            label="moat_durability_note",
            prompt=_moat_durability_note_prompt(persona, evidence, shell),
            word_cap=70,
            apply=_setter_for_dict_key(moat, "durability_note"),
            fallback_value=FALLBACK["moat_durability_note"],
        ))
        jobs.append(NarrativeJob(
            label="moat_competitive_insight",
            prompt=_moat_competitive_insight_prompt(persona, evidence, shell),
            word_cap=28,
            apply=_setter_for_dict_key(moat, "competitive_insight"),
            fallback_value=FALLBACK["moat_competitive_insight"],
        ))

    # ── macro: intelligence_brief (the "Insight" — synthesizes the whole
    #    Macro section). The old one-phrase `headline` job was dropped when
    #    the headline was removed from the Macro card UI.
    macro = shell.get("macro_data")
    if isinstance(macro, dict):
        jobs.append(NarrativeJob(
            label="macro_intelligence_brief",
            prompt=_macro_intelligence_brief_prompt(persona, evidence, shell),
            word_cap=78,
            apply=_setter_for_dict_key(macro, "intelligence_brief"),
            fallback_value=FALLBACK["macro_intelligence_brief"],
        ))

    # ── price_action.narrative ────────────────────────────────────────
    pa = shell.get("price_action")
    if isinstance(pa, dict):
        jobs.append(NarrativeJob(
            label="price_action_narrative",
            # Headroom over the prompt's "under 60 words / 3 sentences" so a
            # full 3-sentence Insight is never chopped mid-thought with "…".
            # The cap is a runaway safety net, not the target length.
            word_cap=90,
            prompt=_price_action_narrative_prompt(persona, evidence, shell),
            apply=_setter_for_dict_key(pa, "narrative"),
            fallback_value=FALLBACK["price_action_narrative"],
        ))

    # ── revenue_engine.analysis_note ──────────────────────────────────
    re_section = shell.get("revenue_engine")
    if isinstance(re_section, dict) and (re_section.get("segments") or []):
        jobs.append(NarrativeJob(
            label="revenue_engine_analysis_note",
            prompt=_revenue_engine_analysis_note_prompt(persona, evidence, shell),
            word_cap=22,
            apply=_setter_for_dict_key(re_section, "analysis_note"),
            fallback_value=FALLBACK["revenue_analysis_note"],
            nullable=True,
        ))

    # ── revenue_forecast.insight ─────────────────────────────────────
    rf = shell.get("revenue_forecast")
    if isinstance(rf, dict):
        jobs.append(NarrativeJob(
            label="revenue_forecast_insight",
            prompt=_revenue_forecast_insight_prompt(persona, evidence, shell),
            word_cap=90,
            apply=_setter_for_dict_key(rf, "insight"),
            fallback_value=FALLBACK["revenue_forecast_insight"],
        ))

    # ── hidden_market_signals.insight ────────────────────────────────
    hms = shell.get("hidden_market_signals")
    if isinstance(hms, dict):
        jobs.append(NarrativeJob(
            label="hidden_market_signals_insight",
            prompt=_hidden_market_signals_insight_prompt(persona, evidence, shell),
            # Headroom over the prompt's "2 to 4 sentences" target so the full
            # insight is never chopped mid-thought with "…". Was 24, which
            # truncated the (now multi-sentence) insight; the prompt was upgraded
            # to 2-4 sentences but this cap wasn't. Safety net, not the target.
            word_cap=90,
            apply=_setter_for_dict_key(hms, "insight"),
            fallback_value=FALLBACK["hidden_market_signals_insight"],
        ))

    # ── revenue_forecast.guidance_quote ──────────────────────────────
    # PR 6 — extraction moved to Stage A (verbatim from transcript with
    # speaker / period attribution). The previous Stage B paraphrase
    # job overwrote Stage A's verbatim quote with a free-text rewrite,
    # which defeated the anti-fabrication design. No-op here; Stage A's
    # output flows through `assemble_report` unchanged.

    # ── key_management.ownership_insight ─────────────────────────────
    km = shell.get("key_management")
    if isinstance(km, dict):
        jobs.append(NarrativeJob(
            label="key_management_insight",
            prompt=_key_management_insight_prompt(persona, evidence, shell),
            # 2-3 sentence synthesis (ownership + insider flow + capital
            # allocation), up from the old single-sentence (34).
            word_cap=70,
            apply=_setter_for_dict_key(km, "ownership_insight"),
            fallback_value=FALLBACK["key_management_insight"],
        ))

    # ── insider_vital.key_insight ────────────────────────────────────
    # NOTE: `insider_data.ownership_note` used to be a Gemini-generated
    # one-liner rendered as a red banner above Key Management. It was
    # removed because it just paraphrased the buy/sell table directly
    # below it ("Insiders dumped $2.6M…" — yes, the table already says
    # that). The schema field stays nullable for backward compatibility
    # with older cached reports.

    iv = (
        shell.get("_scoring_inputs") or shell.get("key_vitals") or {}
    ).get("insider")
    if isinstance(iv, dict):
        jobs.append(NarrativeJob(
            label="insider_key_insight",
            prompt=_insider_key_insight_prompt(persona, evidence, shell),
            word_cap=17,
            apply=_setter_for_dict_key(iv, "key_insight"),
            fallback_value=FALLBACK["insider_key_insight"],
        ))

    # ── fundamental_metrics[i].quality_label (fan out) ────────────────
    cards = shell.get("fundamental_metrics") or []
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        jobs.append(NarrativeJob(
            label=f"fundamental_quality_label_{i}",
            prompt=_fundamental_quality_label_prompt(persona, evidence, card),
            word_cap=6,
            apply=_setter_for_label_with_sentiment(card),
            fallback_value=FALLBACK["fundamental_quality_label"],
        ))

    # ── critical_factors[i].description + watch (fan out) ─────────────
    # Each factor gets TWO Stage-B lines: a short SIGNAL (description) and a
    # forward-looking WATCH action. Both mutate the same factor dict.
    factors = shell.get("critical_factors") or []
    for i, factor in enumerate(factors):
        if not isinstance(factor, dict):
            continue
        jobs.append(NarrativeJob(
            label=f"critical_factor_description_{i}",
            prompt=_critical_factor_description_prompt(persona, evidence, factor),
            word_cap=26,
            apply=_setter_for_dict_key(factor, "description"),
            fallback_value=factor.get("description") or FALLBACK["critical_factor_description"],
        ))
        jobs.append(NarrativeJob(
            label=f"critical_factor_watch_{i}",
            prompt=_critical_factor_watch_prompt(persona, evidence, factor),
            word_cap=28,
            apply=_setter_with_null(factor, "watch"),
            fallback_value=FALLBACK["critical_factor_watch"],
            nullable=True,
        ))

    # ── wall_street_consensus.wall_street_insight ────────────────────
    ws = shell.get("wall_street_consensus")
    if isinstance(ws, dict):
        jobs.append(NarrativeJob(
            label="wall_street_insight",
            prompt=_wall_street_insight_prompt(persona, evidence, shell),
            word_cap=45,
            apply=_setter_with_null(ws, "wall_street_insight"),
            fallback_value=FALLBACK["wall_street_insight"],
            nullable=True,
        ))

    return jobs


# ── Setter helpers: capture the dict ref so closures don't share state


def _setter_for_dict_key(target: Dict[str, Any], key: str) -> Callable[[Any], None]:
    def _apply(value: Any) -> None:
        target[key] = value
    return _apply


# ── Fundamentals-card footer sentiment ────────────────────────────────
# The per-card footer label ("Debt 4.21, Far Too High") is AI-written and can
# contradict the card's star rating — which is FMP's snapshot rating, mirrored
# from the Financials tab for cross-view parity, so we don't touch it. iOS
# colors the footer by this derived sentiment instead, so a negative takeaway
# reads red even on a high-starred card. Deterministic + testable; the label
# vocabulary is constrained by `_fundamental_quality_label_prompt`.
_LABEL_NEG_CUES = (
    "too high", "far too high", "heavy debt", "high debt", "deep debt",
    "debt load", "burning cash", "cash burn", "too pricey", "pricey",
    "overvalued", "overpriced", "expensive", "slowing", "declin", "shrinking",
    "weak", "thin margin", "value trap", "cheap for a reason", "distress",
    "stretched", "unprofitable", "losses", "negative",
)
_LABEL_POS_CUES = (
    "exceptional", "strong", "robust", "healthy", "fat margin", "cash machine",
    "accelerat", "outstanding", "solid", "excellent", "dominant", "wide moat",
    "undervalued", "real growth", "growing", "steady profit", "durable",
    "high quality", "bargain", "rock-solid", "rock solid",
)


def _classify_label_sentiment(text: Optional[str]) -> str:
    """positive / negative / neutral for a fundamentals-card footer label.

    Negative-only cues → "negative"; positive-only → "positive"; both (mixed,
    e.g. "Growing Sales, Burning Cash") or neither → "neutral". iOS renders
    neutral with the existing star-based color, so a mixed/quiet card and any
    legacy report missing this field look exactly as they do today.
    """
    t = (text or "").lower()
    neg = any(c in t for c in _LABEL_NEG_CUES)
    pos = any(c in t for c in _LABEL_POS_CUES)
    if neg and not pos:
        return "negative"
    if pos and not neg:
        return "positive"
    return "neutral"


def _setter_for_label_with_sentiment(
    card: Dict[str, Any],
) -> Callable[[Any], None]:
    """Apply for the per-card label job: writes both `quality_label` and the
    derived `quality_sentiment`, so the footer color tracks the takeaway, not
    the (Financials-tab-mirrored) star rating."""
    def _apply(value: Any) -> None:
        card["quality_label"] = value
        card["quality_sentiment"] = _classify_label_sentiment(
            value if isinstance(value, str) else ""
        )
    return _apply


def _setter_with_null(target: Dict[str, Any], key: str) -> Callable[[Any], None]:
    """Same as _setter_for_dict_key but treats the literal token "NULL"
    as None — used for legitimately-optional narrative fields where the
    AI is asked to opt out of writing rather than fabricate."""
    def _apply(value: Any) -> None:
        if isinstance(value, str) and value.strip().upper().rstrip(".") == "NULL":
            target[key] = None
        else:
            target[key] = value
    return _apply


# ── Stage A: structural / scoring shell prompt ────────────────────────


def build_stage_a_prompt(
    persona: PersonaConfig,
    company_name: str,
    ticker: str,
    evidence: str,
    deep_findings: str = "",
) -> str:
    """Stage-A prompt: scoring + structural fields ONLY, no prose.

    Narrative slots are explicit empty strings — Stage B fills them in
    parallel after this call returns. Real numeric data is in
    `evidence`; the AI sees it as ground truth and is told NOT to
    contradict it. `deep_findings` is the agentic loop's free-text
    research synthesis (only present in the Generate Analysis path).
    """
    findings_block = (
        f"\nDEEP RESEARCH FINDINGS (from your earlier tool-driven research):\n"
        f"{deep_findings[:6000]}\n"
        if deep_findings else ""
    )

    return f"""You are analyzing {company_name} ({ticker}) as {persona.display_name}.

REAL FINANCIAL DATA (verified — do NOT contradict):
{evidence}
{findings_block}
Produce the **structural and scoring** layer of the investment report
as JSON. Real numbers (insider trades, analyst targets, segment
revenue, executive roster) are already loaded — your job here is
*scoring* and *categorization*. The narrative prose for every field
will be written separately afterward; do NOT write prose here.

PERSONA LENS: {persona.narrative_lens or "your investment philosophy"}

Return ONLY valid JSON (no markdown fences):

{{
  "quality_score": 0,
  "core_thesis": {{
    "bull_case": ["<2-4 points, each ≤22 words and each CITING A CONCRETE NUMBER from the CARD VALUES block (e.g. '70.51% gross margin', '4.21 D/E'). Count matches the strength — 2 when only two distinct strong points exist, 4 only when four genuinely non-overlapping points exist>"],
    "bear_case": ["<2-4 points, each ≤22 words and each CITING A CONCRETE NUMBER from the CARD VALUES block. Count matches the strength — 2 when only two distinct strong points exist, 4 only when four genuinely non-overlapping points exist>"]
  }},
  "revenue_forecast": {{
    "management_guidance": "raised|maintained|lowered",
    "guidance_quote": null,
    "guidance_speaker": null,
    "guidance_period": null
  }},
  "insider_analysis": {{
    "ownership_note": null,
    "key_insight": ""
  }},
  "key_management": {{
    "ownership_insight": ""
  }},
  "price_action": {{
    "narrative": ""
  }},
  "revenue_engine": {{
    "analysis_note": null
  }},
  "moat_competition": {{
    "market_dynamics": {{
      "industry": "Industry Name",
      "concentration": "monopoly|duopoly|oligopoly|fragmented",
      "cagr_5yr": 0.0,
      "current_tam": 0,
      "future_tam": 0,
      "current_year": "2025",
      "future_year": "2030",
      "lifecycle_phase": "emerging|secular_growth|mature|declining",
      "tam_source_quote": ""
    }},
    "dimensions": [
      {{"name": "Switching Costs",  "score": 0.0, "peer_score": 5.0}},
      {{"name": "Network Effects",  "score": 0.0, "peer_score": 5.0}},
      {{"name": "Brand Power",      "score": 0.0, "peer_score": 5.0}},
      {{"name": "Cost Advantage",   "score": 0.0, "peer_score": 5.0}},
      {{"name": "Intangible Assets","score": 0.0, "peer_score": 5.0}}
    ],
    "durability_note": "",
    "competitors": [
      {{"name": "Competitor", "ticker": "TICK", "competitive_score": 0.0, "market_share_percent": 0.0, "threat_level": "low|moderate|high"}}
    ],
    "competitive_insight": ""
  }},
  "macro_data": {{
    "overall_threat_level": "low|elevated|high|severe|critical",
    "headline": "",
    "risk_factors": [
      {{"category": "inflation|interest_rates|geopolitical|currency|regulation|supply_chain|tariffs|energy", "title": "Risk Title", "impact": 0.0, "trend": "improving|stable|worsening", "severity": "low|elevated|high|severe|critical", "description": ""}}
    ],
    "intelligence_brief": ""
  }},
  "wall_street": {{
    "wall_street_insight": null
  }},
  "critical_factors": [
    {{"title": "Factor Title", "severity": "high|medium|low", "description": "", "watch": ""}}
  ]
}}

RULES:
- quality_score: integer 0-100 reflecting your conviction on this stock
- moat dimension scores 0.0-10.0
- macro impact 0.0-1.0
- 2-4 bull_case + 2-4 bear_case — count matches the case's strength. Bull and bear can have different counts (e.g. 4 bull + 2 bear when the bull case is rich and the bear thin). Do NOT default to 3 — pick the count that fits the data, not the layout. EACH point MUST cite a concrete number drawn from the CARD VALUES block / evidence (gross margin %, D/E, FCF, P/E, EV/EBITDA, revenue/EPS CAGR, ROE, Altman Z, moat-dimension scores, forecast growth) — i.e. reflect the Deep Dive cards. GROUNDING — STRICT: any number cited MUST appear verbatim in the CARD VALUES block; never invent or recompute; if a metric shows "—"/"N/A", anchor on a different one. A generic point with no number is NOT acceptable.
- 3-6 macro risk_factors (skip ones that don't materially affect this company)
- 2-3 critical_factors normally (1 is fine when little is truly worth flagging; 4 only for a genuinely complex situation; NEVER more than 5). Each is a forward-looking thing to MONITOR going forward — a concern paired with what to watch next — NOT a static complaint or a restatement of the bear case. Each factor MUST cover a DIFFERENT area (never two on the same theme): spread across balance-sheet, competitive moat, macro/geopolitical/Fed, growth/forecast, valuation, insider. Make the title a neutral monitor-area name (e.g. "Free Cash Flow", "Competitive Moat", "Fed & Rate Policy", "Geopolitical Exposure", "Growth Durability").
- 3-5 competitors, ranked by relevance
- DO NOT include fundamental_metrics or overall_assessment — both are now built deterministically from snapshot services and any AI version is discarded.
- Leave every "text" / "narrative" / "headline" / "ownership_insight" / "key_insight" / "description" / "watch" / "intelligence_brief" / "competitive_insight" / "durability_note" / "analysis_note" / "guidance_quote" / "ownership_note" / "wall_street_insight" field as the placeholder shown above. Those will be written by a separate prose pass.
- moat_competition.market_dynamics.concentration AND moat_competition.competitors are RECOMPUTED downstream from real FMP peer + sector data — your values for those fields are discarded. You may still fill them as best-guess for sanity, but accuracy doesn't matter there.
- moat_competition.market_dynamics.current_tam / future_tam: STRICT EXTRACTION ONLY. Set to a USD-denominated number (e.g. 150000000000 for $150B) **only when the EARNINGS-CALL TRANSCRIPT EXCERPT or the company description above contains an explicit, quoted TAM/addressable-market figure**. Set both to 0 when no figure is disclosed. Do NOT estimate from sector context, competitor data, or your training-data knowledge of the industry. Forced fabrication here is the highest-cost failure mode for this product.
- moat_competition.market_dynamics.tam_source_quote: when current_tam > 0, paste the verbatim sentence from the transcript / description that contains the figure (≤ 200 chars). When current_tam = 0, return "".
- moat_competition.market_dynamics.future_year: when future_tam > 0, set to the year in which the projection is stated (e.g. "2030"). Otherwise leave the default.
- moat_competition.dimensions[*].peer_score: 0-10 estimate of how a typical sector PEER scores on each dimension (not the focal company — that's `score`). Use 5.0 only when you have no basis to differentiate; otherwise raise or lower based on industry norms (e.g., enterprise software peers usually score ~7 on Switching Costs; commodity producers usually score ~2 on Brand Power; semiconductor peers usually score ~7 on Intangible Assets / IP).
- revenue_forecast.management_guidance + guidance_quote: STRICT EXTRACTION ONLY from the EARNINGS-CALL TRANSCRIPT EXCERPT above.
  * Set `management_guidance` to "raised" only when the transcript contains explicit raise language ("we now expect", "we are raising our outlook", "we increased our full-year guidance", "guidance was lifted", etc.).
  * "lowered" only on explicit cut language ("we are lowering", "we now see", "guidance was reduced", "below prior outlook", etc.).
  * "maintained" otherwise (including when the transcript wasn't supplied or didn't mention guidance — this is the safe default).
  * Do NOT infer status from sentiment, tone, or your training-data knowledge.
- revenue_forecast.guidance_quote: when management_guidance is "raised" or "lowered", paste the verbatim sentence containing the guidance language (≤ 280 chars, single line, exact transcript words). When "maintained" or no quote available → return null.
- revenue_forecast.guidance_speaker: when guidance_quote is non-null, set to "CFO" | "CEO" | "IR" based on who said it in the transcript. Null otherwise.
- revenue_forecast.guidance_period: when guidance_quote is non-null and the speaker referenced a period (e.g. "Q4 2025", "FY 2026", "next quarter"), set to that period string ≤ 30 chars. Null when not specified.
- Return raw JSON only — no markdown fences, no commentary
"""


def parse_stage_a_response(raw_text: str) -> Optional[Dict[str, Any]]:
    """Parse Stage-A output, stripping any code fences. Returns None on
    failure so the caller can use `stage_a_fallback()`."""
    import json as _json

    if not raw_text:
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    if text.startswith("json"):
        text = text[4:]
    text = text.strip()

    try:
        parsed = _json.loads(text)
    except _json.JSONDecodeError as e:
        logger.error(f"Stage-A JSON parse failed: {e}")
        return None

    if not isinstance(parsed, dict):
        logger.error(f"Stage-A returned non-dict ({type(parsed).__name__})")
        return None
    return parsed


def stage_a_fallback() -> Dict[str, Any]:
    """Honest-placeholder shell when Stage A fails entirely.

    Every narrative slot is the empty string / None expected by Stage
    B; if Stage B also fails the assembled report still validates
    against TickerReportResponse Pydantic.
    """
    return {
        "quality_score": 50,
        "executive_summary_bullets": [],  # removed from UI; kept empty for contract
        "core_thesis": {"bull_case": [], "bear_case": []},
        "fundamental_metrics": [],
        "overall_assessment": {
            "text": "",
            "average_rating": 3.0,
            "strong_count": 0,
            "weak_count": 0,
        },
        "revenue_forecast": {
            "management_guidance": "maintained",
            "guidance_quote": None,
        },
        "insider_analysis": {
            "ownership_note": None,
            "key_insight": "",
        },
        "key_management": {"ownership_insight": ""},
        "price_action": {"narrative": ""},
        "revenue_engine": {"analysis_note": None},
        "moat_competition": {
            "market_dynamics": None,
            "dimensions": [],
            "durability_note": "",
            "competitors": [],
            "competitive_insight": "",
        },
        "macro_data": {
            "overall_threat_level": "low",
            "headline": "",
            "risk_factors": [],
            "intelligence_brief": "",
        },
        "wall_street": {"wall_street_insight": None},
        "critical_factors": [],
    }


# ── Core-thesis synthesis (post-assembly, cross-module) ───────────────
# The Bull/Bear case (core_thesis) is generated in Stage A from
# `build_financial_context`, whose evidence is fundamentals-only — so
# Stage A bullets can only cite margins / ratios / growth. The synthesis
# below runs AFTER `assemble_report`, when every Deep Dive module's FINAL
# verdict exists on `report` (moat dimension scores, recomputed
# competitors, computed macro threat, price catalyst, Wall Street
# consensus). It reads those verdicts + the same `evidence`, ranks every
# candidate signal by decision-impact, and rewrites core_thesis with the
# 2-4 STRONGEST points per side drawn across ALL modules — not just
# fundamentals. Stage A's thesis stays as the fallback if this fails.


def _f_num(v: Any, fmt: str = "{:.1f}") -> Optional[str]:
    """Format a numeric value, or None when it's missing / non-numeric."""
    try:
        if v is None:
            return None
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return None


# Macro only earns a spot in the thesis when it's genuinely material.
# "low"/"elevated" macro is background noise, not a buy/sell driver — only
# "high" or worse should surface as a bear point. Ranks mirror the
# collector's _SEVERITY_INT ladder (low<elevated<high<severe<critical).
_MATERIAL_MACRO_THREATS = {"high", "severe", "critical"}
_MACRO_SEVERITY_RANK = {
    "low": 1, "elevated": 2, "high": 3, "severe": 4, "critical": 5,
}


# Per-module digest formatters. Each returns the same line(s) it used to
# append inside `build_module_digest`, so the digest is byte-identical. They
# are also reused individually by `cross_section_context` to hand ONE section's
# insight a surgical slice of a RELATED module's final verdict — that way the
# per-section insight and the cross-module thesis cite the exact same numbers.
# The per-module try/except now lives in the two callers (build_module_digest /
# cross_section_context), so a single broken module never sinks the rest.


def _digest_price_action(report: Dict[str, Any]) -> List[str]:
    """Recent Price Movement (+ catalyst)."""
    out: List[str] = []
    pa = report.get("price_action") or {}
    chg = _f_num(pa.get("change_pct"), "{:+.1f}")
    if chg is not None:
        seg = f"PRICE MOVEMENT: {chg}% over {pa.get('window_label', 'recent period')}"
        tier = pa.get("tier")
        z = _f_num(pa.get("z_score"), "{:.1f}")
        if tier:
            seg += f"; {tier}" + (f" (z={z})" if z is not None else "")
        ev = pa.get("event") if isinstance(pa.get("event"), dict) else None
        tag = (ev or {}).get("tag") or pa.get("tag")
        if tag and str(tag).strip().lower() not in ("typical", "normal", ""):
            seg += f"; catalyst: {tag}"
        out.append(seg)
    return out


def _digest_revenue_engine(report: Dict[str, Any]) -> List[str]:
    """Revenue Engine (segments)."""
    out: List[str] = []
    reng = report.get("revenue_engine") or {}
    unit = reng.get("revenue_unit", "")
    parts: List[str] = []
    for s in (reng.get("segments") or [])[:3]:
        if not isinstance(s, dict):
            continue
        cur, prev = s.get("current_revenue"), s.get("previous_revenue")
        yoy = None
        try:
            if prev:
                yoy = (float(cur) - float(prev)) / abs(float(prev)) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            yoy = None
        curs = _f_num(cur, "{:,.0f}")
        piece = f"{s.get('name', '?')} {curs}{unit}" if curs else f"{s.get('name', '?')}"
        yoys = _f_num(yoy, "{:+.0f}")
        if yoys is not None:
            piece += f" ({yoys}% YoY)"
        parts.append(piece)
    if parts:
        out.append("REVENUE ENGINE: " + "; ".join(parts))
    return out


def _digest_fundamentals(report: Dict[str, Any]) -> List[str]:
    """Fundamentals & Growth (cards + overall) — sector-relative framing lives
    in EVIDENCE; here we give the card values + the headline rollup."""
    out: List[str] = []
    card_strs: List[str] = []
    for c in (report.get("fundamental_metrics") or []):
        if not isinstance(c, dict):
            continue
        stars = c.get("star_rating")
        mstr = ", ".join(
            f"{m.get('label', '?')} {m.get('value', '?')}"
            for m in (c.get("metrics") or []) if isinstance(m, dict)
        )
        star_s = f" {stars}/5" if isinstance(stars, int) else ""
        card_strs.append(f"{c.get('title', '?')}{star_s} [{mstr}]")
    if card_strs:
        out.append("FUNDAMENTALS & GROWTH: " + " | ".join(card_strs))
    oa = report.get("overall_assessment") or {}
    avg = _f_num(oa.get("average_rating"), "{:.1f}")
    if avg is not None:
        out.append(
            f"  Overall fundamentals: {avg}/5 avg "
            f"({oa.get('strong_count', 0)} strong / {oa.get('weak_count', 0)} weak metrics)"
        )
    return out


def _digest_forecast(report: Dict[str, Any]) -> List[str]:
    """Future Forecast."""
    out: List[str] = []
    rf = report.get("revenue_forecast") or {}
    bits: List[str] = []
    cagr = _f_num(rf.get("cagr"), "{:+.1f}")
    eps = _f_num(rf.get("eps_growth"), "{:+.1f}")
    if cagr is not None:
        bits.append(f"revenue CAGR {cagr}%")
    if eps is not None:
        bits.append(f"EPS CAGR {eps}%")
    if rf.get("management_guidance"):
        bits.append(f"guidance {rf['management_guidance']}")
    if rf.get("beat_summary"):
        bits.append(str(rf["beat_summary"]).lower())  # "beat 6 of 8"
    if bits:
        out.append("FUTURE FORECAST: " + ", ".join(bits))
    return out


def _digest_insider(report: Dict[str, Any]) -> List[str]:
    """Insider & Management."""
    out: List[str] = []
    idata = report.get("insider_data") or {}
    sent = idata.get("sentiment")
    tx_strs = [
        f"{t.get('type', '?')} {t.get('count', '?')} ({t.get('value', '?')})"
        for t in (idata.get("transactions") or []) if isinstance(t, dict)
    ]
    if sent or tx_strs:
        seg = f"INSIDER ({idata.get('timeframe', 'recent')}): {sent or 'n/a'}"
        if tx_strs:
            seg += " — " + ", ".join(tx_strs)
        out.append(seg)
    ca = idata.get("capital_allocation")
    if isinstance(ca, dict):
        ca_bits: List[str] = []
        if ca.get("buyback_status"):
            ca_bits.append(f"buybacks {ca['buyback_status']}")
        dy = _f_num(ca.get("dividend_yield"), "{:.1f}")
        if dy is not None and (ca.get("dividend_yield") or 0) > 0:
            ca_bits.append(f"div yield {dy}%")
        scc = _f_num(ca.get("share_count_change"), "{:+.1f}")
        if scc is not None:
            # share_count_change spans the whole series (oldest→newest, up to
            # ~2yr), NOT year-over-year — cite the real window so the narrative
            # doesn't annualize it. Matches the iOS card's window caption.
            dps = ca.get("data_points") or []
            if len(dps) >= 2:
                ca_bits.append(
                    f"share count {scc}% over "
                    f"{dps[0].get('period')} to {dps[-1].get('period')}"
                )
            else:
                ca_bits.append(f"share count {scc}%")
        if ca_bits:
            out.append("  Capital allocation: " + ", ".join(ca_bits))
    return out


def _digest_hidden_signals(report: Dict[str, Any]) -> List[str]:
    """Hidden Market Signals — congressional trades + short interest."""
    out: List[str] = []
    hms = report.get("hidden_market_signals")
    if not isinstance(hms, dict):
        return out
    bits: List[str] = []
    cg = hms.get("congress")
    if isinstance(cg, dict) and (cg.get("num_buyers") or cg.get("num_sellers")):
        bits.append(
            f"Congress {cg.get('num_buyers', 0)} buyer(s) / "
            f"{cg.get('num_sellers', 0)} seller(s), net {cg.get('net_direction', '?')} "
            f"({cg.get('period', '12mo')})"
        )
    si = hms.get("short_interest")
    if isinstance(si, dict):
        sbits: List[str] = []
        pf = _f_num(si.get("percent_of_float"), "{:.1f}")
        if pf is not None:
            # Deterministic severity by % of float (industry convention): <10
            # low, 10-20 elevated, 20-30 high/squeeze-watch, 30+ extreme. Feed
            # the LABEL to the model so flagging doesn't rely on it comparing
            # numbers itself.
            try:
                v = float(si.get("percent_of_float"))
                lvl = ("extreme" if v >= 30 else "high" if v >= 20
                       else "elevated" if v >= 10 else "low")
            except (TypeError, ValueError):
                lvl = "low"
            sbits.append(f"{pf}% of float [{lvl}]")
        dtc = _f_num(si.get("days_to_cover"), "{:.1f}")
        if dtc is not None:
            sbits.append(f"{dtc}d to cover")
            try:
                if float(si.get("days_to_cover")) >= 5:
                    sbits.append("shorts hard to unwind (squeeze fuel)")
            except (TypeError, ValueError):
                pass
        ch = si.get("change_3m")
        if ch is not None:
            # Word form (no minus sign) so the evidence carries no dashes either.
            try:
                v = float(ch)
                sbits.append(f"{'up' if v >= 0 else 'down'} {abs(v):.0f}% vs 3mo")
            except (TypeError, ValueError):
                pass
        if sbits:
            # UI label is "Short Selling" (data field stays short_interest).
            bits.append("Short selling " + ", ".join(sbits))
    if bits:
        out.append("HIDDEN SIGNALS: " + "; ".join(bits))
    return out


def _digest_moat(report: Dict[str, Any]) -> List[str]:
    """Industry & Competitive Moat."""
    out: List[str] = []
    moat = report.get("moat_competition") or {}
    dim_strs: List[str] = []
    for d in (moat.get("dimensions") or []):
        if not isinstance(d, dict):
            continue
        sc = _f_num(d.get("score"), "{:.1f}")
        if sc is None:
            continue
        ps = _f_num(d.get("peer_score"), "{:.1f}")
        piece = f"{d.get('name', '?')} {sc}"
        if ps is not None:
            piece += f" (peer {ps})"
        dim_strs.append(piece)
    if dim_strs:
        out.append("MOAT (0-10, focal vs peer): " + ", ".join(dim_strs))
    md = moat.get("market_dynamics") or {}
    if isinstance(md, dict):
        mbits: List[str] = []
        if md.get("concentration"):
            mbits.append(str(md["concentration"]))
        if md.get("lifecycle_phase"):
            mbits.append(str(md["lifecycle_phase"]).replace("_", " "))
        c5 = _f_num(md.get("cagr_5yr"), "{:+.1f}")
        if c5 is not None:
            mbits.append(f"industry CAGR {c5}%")
        if mbits:
            out.append("  Market structure: " + ", ".join(mbits))
    comp_strs: List[str] = []
    for cp in (moat.get("competitors") or [])[:3]:
        if not isinstance(cp, dict):
            continue
        piece = cp.get("name") or cp.get("ticker") or "?"
        cs = _f_num(cp.get("competitive_score"), "{:.1f}")
        if cs is not None:
            piece += f" {cs}/10"
        if cp.get("threat_level"):
            piece += f" {cp['threat_level']} threat"
        comp_strs.append(piece)
    if comp_strs:
        out.append("  Competitors: " + ", ".join(comp_strs))
    return out


def _digest_macro(report: Dict[str, Any]) -> List[str]:
    """Macro & Geopolitical — only surface when MATERIAL (High or above).
    Low/elevated macro is background noise, never a thesis-driving point.
    (Price Action wants the ungated risk factors, so it reads macro_data
    directly rather than through this gated formatter.)"""
    out: List[str] = []
    macro = report.get("macro_data") or {}
    threat = str(macro.get("overall_threat_level") or "").strip().lower()
    if threat in _MATERIAL_MACRO_THREATS:
        rfs = [r for r in (macro.get("risk_factors") or []) if isinstance(r, dict)]
        # Rank by severity first (the high+ factors are what matter),
        # then by impact as a tiebreaker.
        rfs.sort(
            key=lambda r: (
                _MACRO_SEVERITY_RANK.get(str(r.get("severity") or "").lower(), 0),
                r.get("impact") or 0.0,
            ),
            reverse=True,
        )
        top_rf = rfs[:2]
        seg = f"MACRO threat: {threat}"
        if top_rf:
            seg += " — " + ", ".join(
                f"{r.get('title', '?')} ({r.get('severity', '?')})" for r in top_rf
            )
        out.append(seg)
    return out


def _digest_wall_street(report: Dict[str, Any]) -> List[str]:
    """Wall Street Consensus."""
    out: List[str] = []
    ws = report.get("wall_street_consensus") or {}
    bits = []
    if ws.get("rating"):
        bits.append(f"consensus {str(ws['rating']).replace('_', ' ')}")
    cur, tgt = ws.get("current_price"), ws.get("target_price")
    upside = None
    try:
        if cur and tgt:
            upside = (float(tgt) - float(cur)) / float(cur) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        upside = None
    ups, tgts = _f_num(upside, "{:+.0f}"), _f_num(tgt, "{:.0f}")
    if ups is not None and tgts is not None:
        bits.append(f"target ${tgts} ({ups}% vs current)")
    if ws.get("valuation_status"):
        bits.append(str(ws["valuation_status"]))
    up, down = ws.get("momentum_upgrades"), ws.get("momentum_downgrades")
    if isinstance(up, int) and isinstance(down, int) and (up or down):
        bits.append(f"momentum {up} upgrades / {down} downgrades (12mo)")
    if bits:
        out.append("WALL STREET: " + ", ".join(bits))
    return out


# Stable registry — keys are the module names the cross-section wiring uses.
# `_DIGEST_ORDER` reproduces the original 1→8 top-to-bottom sequence so the
# concatenated digest is byte-identical to the pre-refactor output.
_DIGEST_FORMATTERS: Dict[str, Callable[[Dict[str, Any]], List[str]]] = {
    "price_action": _digest_price_action,
    "revenue_engine": _digest_revenue_engine,
    "fundamentals": _digest_fundamentals,
    "forecast": _digest_forecast,
    "insider": _digest_insider,
    "moat": _digest_moat,
    "macro": _digest_macro,
    "wall_street": _digest_wall_street,
    "hidden_signals": _digest_hidden_signals,
}
_DIGEST_ORDER: List[str] = list(_DIGEST_FORMATTERS)


def build_module_digest(report: Dict[str, Any]) -> str:
    """Compact, number-rich digest of every FINAL Deep Dive module verdict
    on the assembled `report`. Feeds `synthesize_core_thesis` /
    `synthesize_critical_factors` so the thesis can rank signals across ALL
    modules (moat, competition, macro, Wall Street, forecast, price catalyst,
    insiders, segments) and cite the exact numbers the user sees in each
    section.

    Defensive by construction: any module that errors or is empty is simply
    omitted — a partial digest is fine, a crash is not.
    """
    lines: List[str] = []
    for name in _DIGEST_ORDER:
        try:
            lines.extend(_DIGEST_FORMATTERS[name](report) or [])
        except Exception:
            pass
    return "\n".join(lines)


def cross_section_context(report: Dict[str, Any], modules: List[str]) -> str:
    """Return ONLY the requested modules' digest lines, in registry order,
    each formatter independently defensive. Empty/quiet modules contribute
    nothing (the caller's RELATED block then collapses to ""). Used to give a
    per-section insight a SURGICAL slice of a related module's final verdict so
    the report stays internally consistent — the insight cites the SAME numbers
    the cross-module thesis does."""
    lines: List[str] = []
    for name in _DIGEST_ORDER:  # registry order, not caller order → stable
        if name not in modules:
            continue
        fmt = _DIGEST_FORMATTERS.get(name)
        if fmt is None:
            continue
        try:
            lines.extend(fmt(report) or [])
        except Exception:
            pass
    return "\n".join(lines)


def build_thesis_synthesis_prompt(
    persona: PersonaConfig,
    company_name: str,
    ticker: str,
    evidence: str,
    digest: str,
    bull_target: int,
    bear_target: int,
) -> str:
    """Prompt that selects the MOST IMPORTANT bull/bear points across every
    Deep Dive module — ranked by decision-impact, grounded in the data,
    diversified so the thesis isn't four fundamental ratios. `bull_target`/
    `bear_target` (2-5 each) are computed from how many module sub-scores read
    strong/weak, so the count tracks the modules rather than defaulting to 3."""
    return f"""You are assembling the INVESTMENT THESIS — the Bull Case and Bear Case — for {company_name} ({ticker}) as {persona.display_name}.

Two blocks of FINAL, verified data are below. EVIDENCE carries the fundamentals and sector context; MODULE DIGEST carries the headline verdict of every other Deep Dive module (price movement & catalyst, revenue segments, forward forecast & guidance, insider activity, competitive moat, competitors, macro threat, Wall Street consensus).

EVIDENCE (fundamentals, sector, analyst, insider, segments — verified):
{evidence}

MODULE DIGEST (final verdicts the user sees in each Deep Dive section — verified):
{digest}

PERSONA LENS: {persona.narrative_lens or "your investment philosophy"}

YOUR JOB — think hard, then surface ONLY the most important points:
Pick the {bull_target} STRONGEST reasons to OWN this stock (bull_case) and the {bear_target} STRONGEST reasons to AVOID/worry about it (bear_case). Imagine a portfolio manager with 30 seconds: what actually moves the buy/hold/sell decision?

SELECTION RULES (strict):
- Rank EVERY candidate signal across BOTH blocks by decision-impact = magnitude × how far it deviates from sector / peers / its own history × how much it changes the thesis. Keep only the top few. Discard the merely-fine.
- Return EXACTLY {bull_target} bull and {bear_target} bear points — these counts were computed from how many distinct dimensions show genuinely strong (bull) or weak (bear) signals. Fill every slot with a distinct, grounded point; never pad with filler, never repeat a point, never merge two into one to hit the count. Each must be razor-sharp.
- DIVERSIFY across modules — take at most ~2 points from any single module. A bull_case made of four valuation/margin ratios is a FAILURE: when the real signal is a wide moat, a price catalyst, raised guidance, a critical macro threat, hostile insider selling, or a strong Wall Street target, that is what belongs here. A quiet/neutral module contributes nothing — skip it.
- Each point ≤22 words, plain English, no hedging, no clichés. Lead with the number/verdict.
- GROUNDING — STRICT: every number or named verdict you cite MUST appear verbatim in the EVIDENCE or MODULE DIGEST above (e.g. 'Switching Costs moat 8.5 vs peer 7.0', 'analyst target +18%', 'Extreme -12% move on guidance cut', 'Critical macro threat', '70.51% gross margin', '4.21 D/E'). NEVER invent or recompute a number. A generic point with no concrete number/verdict is NOT acceptable.
- Never name yourself, the underlying model, or any AI provider.

Return ONLY valid JSON (no markdown fences, no commentary):

{{
  "bull_case": ["<point 1>", "<point 2>"],
  "bear_case": ["<point 1>", "<point 2>"]
}}"""


def _clean_thesis_points(raw: Any) -> List[str]:
    """Coerce a model-returned list into clean, capped bullet strings.
    Caps at 5 (matches `_sanitize_thesis` in the collector)."""
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = _post_process(item)  # strips quotes / markdown / stray labels
        if cleaned:
            out.append(cleaned)
    return out[:5]


# Signal thresholds on the 0-10 module sub-scores: a dimension is a "strong"
# (bull) signal at/above _BULL_SIGNAL and a "weak" (bear) signal at/below
# _BEAR_SIGNAL. Everything between is a quiet/neutral module that contributes
# no thesis point. Tunable.
_BULL_SIGNAL = 7.5
_BEAR_SIGNAL = 4.0
_SCORING_VITALS = (
    "valuation", "moat", "financial_health", "revenue",
    "insider", "macro", "forecast", "wall_street", "capital_allocation",
)


def _thesis_target_counts(report: Dict[str, Any]) -> Tuple[int, int]:
    """Return (bull_target, bear_target) for the Bull/Bear thesis, derived from
    the SAME eight module sub-scores that drive the headline score.

    Counts how many of the eight 0-10 vital sub-scores read strong (≥ _BULL_
    SIGNAL → a bull point) or weak (≤ _BEAR_SIGNAL → a bear point). Clamped to
    the UI's 2-5 range: a quiet stock yields 2/2, a stock with many strong/weak
    fronts up to 5/5. This is what makes the number of bullets track the Deep
    Dive modules instead of clustering at 3.

    Recent Price Movement is deliberately EXCLUDED — it is momentum, not a
    quality signal, and (like the headline score) must not drive the count. A
    material price catalyst can still surface as a thesis POINT via the module
    digest; it just doesn't inflate the target count.
    """
    vitals = report.get("_scoring_inputs") or report.get("key_vitals") or {}
    bull = 0
    bear = 0
    for name in _SCORING_VITALS:
        score = _vital_score(vitals, name)
        if score is None:
            continue
        if score >= _BULL_SIGNAL:
            bull += 1
        elif score <= _BEAR_SIGNAL:
            bear += 1

    return (max(2, min(5, bull)), max(2, min(5, bear)))


async def synthesize_core_thesis(
    report: Dict[str, Any],
    persona: PersonaConfig,
    gemini: GeminiClient,
    evidence: str,
) -> None:
    """Rewrite `report['core_thesis']` with the 2-4 strongest bull/bear
    points drawn across ALL Deep Dive modules. Mutates in place.

    Only overwrites when synthesis yields a COMPLETE thesis (both sides
    non-empty); otherwise the Stage A thesis already on `report` stays as
    the fallback. Never raises — a failed synthesis just leaves the
    existing thesis untouched.
    """
    ticker = report.get("symbol", "?")
    try:
        digest = build_module_digest(report)
        if not digest.strip():
            return  # nothing extra to synthesize from; keep Stage A thesis

        company_name = report.get("company_name") or ticker
        bull_target, bear_target = _thesis_target_counts(report)
        prompt = build_thesis_synthesis_prompt(
            persona, company_name, ticker, evidence, digest,
            bull_target, bear_target,
        )
        result = await gemini.generate_json(
            prompt=prompt,
            system_instruction=persona.system_prompt,
        )
        parsed = parse_stage_a_response(result.get("text") or "")
        if not isinstance(parsed, dict):
            logger.warning(
                f"Thesis synthesis returned unparseable JSON for {ticker}; "
                f"keeping Stage A thesis."
            )
            return

        bull = _clean_thesis_points(parsed.get("bull_case"))
        bear = _clean_thesis_points(parsed.get("bear_case"))
        if not bull or not bear:
            # An incomplete thesis is worse than the Stage A one — keep it.
            logger.info(
                f"Thesis synthesis for {ticker} returned an incomplete thesis "
                f"({len(bull)} bull / {len(bear)} bear); keeping Stage A thesis."
            )
            return

        report["core_thesis"] = {"bull_case": bull, "bear_case": bear}
        logger.info(
            f"Core thesis synthesized for {ticker}: "
            f"{len(bull)} bull / {len(bear)} bear (cross-module)."
        )
    except Exception as e:
        logger.warning(
            f"Thesis synthesis failed for {ticker}: "
            f"{type(e).__name__}: {e}"
        )
        # keep Stage A thesis


# ── Critical factors synthesis (post-assembly, cross-module) ──────────
# The "Critical Factors to Watch" used to be generated in Stage A from
# fundamentals-only evidence (its example titles were all debt-themed) and
# the Stage B watch prompt only saw fundamentals — so the factors were
# redundant and the watch defaulted to "next earnings". This synthesis runs
# AFTER assembly + the thesis synthesis, reads the FINAL bear case + every
# module verdict + the macro/geopolitical events, and rewrites the factors so
# each covers a DISTINCT area with a broad, real "watch" trigger.


_VALID_CF_SEVERITY = {"high", "medium", "low"}


def _format_macro_watch_block(report: Dict[str, Any]) -> str:
    """Ungated macro/geopolitical context for critical-factor 'watch' triggers
    — threat level + every active risk factor (incl. web-grounded geopolitical
    events), regardless of the high+ gating the thesis digest uses (a "watch
    the Fed / a war" trigger is relevant even at "elevated")."""
    try:
        macro = report.get("macro_data") or {}
        threat = str(macro.get("overall_threat_level") or "low").strip().lower()
        rfs = [r for r in (macro.get("risk_factors") or []) if isinstance(r, dict)]
        lines = [f"Overall macro threat: {threat}"]
        for r in rfs[:8]:
            lines.append(
                f"- {r.get('title', '?')} "
                f"({r.get('category', '?')}, {r.get('severity', '?')}, {r.get('trend', '?')})"
            )
        return "\n".join(lines)
    except Exception:
        return "Overall macro threat: n/a"


def build_critical_factors_prompt(
    persona: PersonaConfig,
    company_name: str,
    ticker: str,
    evidence: str,
    digest: str,
    bear_block: str,
    macro_watch: str,
) -> str:
    """Prompt that picks the 2-3 most important forward-looking things to
    MONITOR — each on a DISTINCT Deep Dive area, with a real, varied watch
    trigger (earnings AND Fed / war / AI-sector / analyst / market)."""
    return f"""You are choosing the "Critical Factors to Watch" for {company_name} ({ticker}) as {persona.display_name} — the 2-3 most important forward-looking things an investor should MONITOR from here.

Use ALL of the data below.

EVIDENCE (company fundamentals — for grounding numbers):
{evidence}

MODULE DIGEST (final verdict of every Deep Dive module):
{digest}

BEAR CASE (the synthesized key risks — turn these into forward monitors, do NOT restate them):
{bear_block}

MACRO / GEOPOLITICAL (current threat level + active risk events, incl. web-grounded wars / tariffs / Fed / etc.):
{macro_watch}

PERSONA LENS: {persona.narrative_lens or "your investment philosophy"}

Produce 2-3 critical factors. STRICT rules:
- DIVERSITY — each factor MUST cover a DIFFERENT area. NEVER two on the same theme (do NOT give two debt / free-cash-flow factors). Spread across the areas that actually matter here: balance-sheet / fundamentals · competitive moat · macro / geopolitical / Fed · growth / forecast · valuation / Wall-Street · insider activity · price / catalyst. Pick the 2-3 most decision-relevant DISTINCT areas.
- FORWARD-LOOKING — each is a thing to MONITOR going forward, not a static complaint and not a restatement of the bear case. Complement the bear case.
- WATCH TRIGGER (breadth) — the "watch" must name the REAL trigger to track, and across the 2-3 factors SPAN A MIX — do NOT make every watch "next earnings". The trigger can be company-specific (next earnings/guidance, a specific debt/FCF number) OR external: the next Fed / rate decision, an escalation or resolution of a NAMED event from the MACRO block (a war, tariffs, sanctions), a major AI / sector headline, an analyst upgrade / downgrade, or an oil / USD / yield move. Use the macro/geopolitical events above when they are the real swing factor for this company.
- GROUNDING (strict) — any number you cite MUST appear verbatim in EVIDENCE or the MODULE DIGEST. Never invent or recompute a number.
- title = a short neutral monitor-area name (e.g. "Free Cash Flow", "Competitive Moat", "Fed & Rate Policy", "Geopolitical Exposure", "Growth Durability"). description = ONE sentence (≤22 words): what's notable now + why it matters, with a concrete number. watch = ONE sentence (≤22 words): what to track next + the trigger; do NOT begin with the word "Watch".
- severity: "high" | "medium" | "low" (priority to watch).
- Never name yourself, the underlying model, or any AI provider.

Return ONLY valid JSON (no markdown fences, no commentary):

{{
  "critical_factors": [
    {{"title": "...", "severity": "high|medium|low", "description": "...", "watch": "..."}}
  ]
}}"""


def _clean_critical_factors(raw: Any) -> List[Dict[str, Any]]:
    """Validate/normalize model-returned critical factors. Caps at 5
    (mirrors the assemble_report safety net); `watch` is optional."""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = _post_process(str(item.get("title") or "")).strip()
        desc = _post_process(str(item.get("description") or "")).strip()
        if not title or not desc:
            continue
        sev = str(item.get("severity") or "medium").strip().lower()
        if sev not in _VALID_CF_SEVERITY:
            sev = "medium"
        watch: Optional[str] = None
        watch_raw = item.get("watch")
        if isinstance(watch_raw, str):
            w = _post_process(watch_raw).strip()
            if w and w.lower() not in ("null", "none", "n/a"):
                watch = w
        out.append({
            "title": title[:60],
            "description": desc,
            "severity": sev,
            "watch": watch,
        })
    return out[:5]


async def synthesize_critical_factors(
    report: Dict[str, Any],
    persona: PersonaConfig,
    gemini: GeminiClient,
    evidence: str,
) -> None:
    """Rewrite `report['critical_factors']` with 2-3 forward-looking factors,
    each on a DISTINCT Deep Dive area, drawn across all modules + the Bear
    Case + the macro/geopolitical events. Mutates in place.

    Runs AFTER `synthesize_core_thesis` so it reads the FINAL bear case. Only
    overwrites when synthesis yields ≥2 valid factors; otherwise the Stage
    A/B factors already on `report` stay as the fallback. Never raises.
    """
    ticker = report.get("symbol", "?")
    try:
        digest = build_module_digest(report)
        bear = (report.get("core_thesis") or {}).get("bear_case") or []
        bear_block = "\n".join(
            f"- {b}" for b in bear if isinstance(b, str) and b.strip()
        ) or "(none)"
        macro_watch = _format_macro_watch_block(report)
        if not digest.strip() and bear_block == "(none)":
            return  # nothing to synthesize from; keep Stage A/B factors

        company_name = report.get("company_name") or ticker
        prompt = build_critical_factors_prompt(
            persona, company_name, ticker, evidence, digest, bear_block, macro_watch,
        )
        result = await gemini.generate_json(
            prompt=prompt,
            system_instruction=persona.system_prompt,
        )
        parsed = parse_stage_a_response(result.get("text") or "")
        if not isinstance(parsed, dict):
            logger.warning(
                f"Critical-factors synthesis returned unparseable JSON for "
                f"{ticker}; keeping Stage A/B factors."
            )
            return

        factors = _clean_critical_factors(parsed.get("critical_factors"))
        if len(factors) < 2:
            logger.info(
                f"Critical-factors synthesis for {ticker} returned "
                f"{len(factors)} valid factor(s); keeping Stage A/B factors."
            )
            return

        report["critical_factors"] = factors
        logger.info(
            f"Critical factors synthesized for {ticker}: "
            f"{len(factors)} (cross-module, distinct-area)."
        )
    except Exception as e:
        logger.warning(
            f"Critical-factors synthesis failed for {ticker}: "
            f"{type(e).__name__}: {e}"
        )
        # keep Stage A/B factors
