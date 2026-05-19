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
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.integrations.gemini import GeminiClient
from app.services.agents.persona_config import PersonaConfig

logger = logging.getLogger(__name__)


# ── Honest fallbacks (visible to the user when Gemini fails) ──────────


FALLBACK = {
    "executive_summary_text": (
        "Live commentary unavailable. Numbers below reflect the latest filings."
    ),
    "executive_summary_bullet": "Insight refreshed; commentary unavailable.",
    "overall_assessment_text": "Quality scoring updated; narrative unavailable.",
    "guidance_quote": None,
    "revenue_analysis_note": None,
    "moat_durability_note": "Data unavailable for this ticker.",
    "moat_competitive_insight": "Data unavailable for this ticker.",
    "macro_headline": "Macro overview unavailable",
    "macro_intelligence_brief": (
        "Macro commentary unavailable. Risk factors above are sourced from "
        "filings and macro data."
    ),
    "price_action_narrative": (
        "Price chart shown above; commentary unavailable."
    ),
    "key_management_insight": "Data unavailable for this ticker.",
    "insider_ownership_note": None,
    "insider_key_insight": (
        "Insider data refreshed; commentary unavailable."
    ),
    "fundamental_quality_label": "—",
    "critical_factor_description": "Detail unavailable for this risk factor.",
    "hedge_fund_note": None,
}


# ── Style brief shared across all narrative prompts ───────────────────


_STYLE_BRIEF = """STYLE: Catchy, punchy, plain-English. Sound like a sharp portfolio manager talking to a smart friend — confident, specific, never marketing-speak. NEVER use clichés ("strong tailwinds", "well-positioned", "going forward", "in the long run", "robust", "best-in-class"). NEVER hedge ("could potentially", "may possibly", "it remains to be seen"). Cite a concrete number when one is available in the data.

PERSONA VOICE: Stay in character as {persona_name}. Apply your lens — {narrative_lens} — but never name yourself, never mention the underlying technology, model, or AI provider, and never say "as an AI".

OUTPUT: Return ONLY the requested text. No JSON, no markdown, no quotes, no preamble like "Here is" or "The narrative is". Just the prose."""


def _style_block(persona: PersonaConfig) -> str:
    return _STYLE_BRIEF.format(
        persona_name=persona.display_name,
        narrative_lens=persona.narrative_lens or "your investment philosophy",
    )


def _length_brief(sentences: int, word_cap: int) -> str:
    s = "1 sentence" if sentences == 1 else f"exactly {sentences} sentences"
    return f"LENGTH: Write {s}, total under {word_cap} words."


# ── Output post-processing ────────────────────────────────────────────


_QUOTE_CHARS = ("“", "”", "‘", "’", '"', "'", "`")


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
    # Some fields (guidance_quote, hedge_fund_note, ownership_note) are
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
    bull = "; ".join(shell.get("core_thesis", {}).get("bull_case", [])[:4])
    bear = "; ".join(shell.get("core_thesis", {}).get("bear_case", [])[:4])
    return f"""Write the headline investment thesis for this report.

EVIDENCE:
{evidence}

KEY BULLISH POINTS (already structured): {bull or "none"}
KEY BEARISH POINTS (already structured): {bear or "none"}

{_style_block(persona)}
{_length_brief(2, 40)}

Open with a hook (a number, a contrast, a status). Close with what makes this stock a buy/hold/avoid in your view. Do NOT just restate the bullish/bearish points — give the *thesis* that ties them together."""


def _executive_summary_bullet_prompt(
    persona: PersonaConfig,
    evidence: str,
    bullet: Dict[str, Any],
    index: int,
) -> str:
    category = bullet.get("category") or "Insight"
    sentiment = bullet.get("sentiment") or "neutral"
    seed = bullet.get("text") or ""
    return f"""Write one catchy bullet for the executive summary.

CATEGORY: {category}
SENTIMENT: {sentiment}
SEED THOUGHT (rewrite or replace, don't pad): "{seed}"

EVIDENCE:
{evidence}

{_style_block(persona)}
LENGTH: 1 short fragment under 12 words. NO leading bullet symbol, NO period at the end.

This is bullet #{index + 1} of three — make it distinct from a generic summary. Lead with a number when possible."""


def _overall_assessment_text_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    avg = shell.get("overall_assessment", {}).get("average_rating", 0.0)
    strong = shell.get("overall_assessment", {}).get("strong_count", 0)
    weak = shell.get("overall_assessment", {}).get("weak_count", 0)
    return f"""Write the one-line overall quality verdict for the fundamentals section.

CONTEXT: Average rating {avg}/5 across four cards ({strong} strong, {weak} weak).

EVIDENCE:
{evidence}

{_style_block(persona)}
{_length_brief(1, 25)}

Capture the gestalt — is this a quality compounder, a fixer-upper, a value trap, or something else? Use one concrete metric to anchor it.

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
    return f"""Write a one-line judgment on how durable this company's moat is.

TOP MOAT SOURCE: {top.get('name', 'unknown')} (score {top.get('score', 0)}/10)
ALL DIMENSIONS: {", ".join(f"{d.get('name')} {d.get('score')}/10" for d in dims) or "none"}

{_style_block(persona)}
{_length_brief(1, 20)}

Name the specific threat or staying power — don't just restate the score."""


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


def _macro_headline_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    threat = shell.get("macro_data", {}).get("overall_threat_level", "low")
    factors = shell.get("macro_data", {}).get("risk_factors", [])
    titles = ", ".join(f.get("title", "") for f in factors[:3])
    return f"""Write the one-phrase macro headline shown above the risk factor list.

OVERALL THREAT: {threat}
TOP RISK TITLES: {titles or "none"}

{_style_block(persona)}
LENGTH: One phrase, under 8 words. NO period at the end.

Capture the dominant macro flavor (e.g., "Rate sensitivity meets margin compression"). NOT a complete sentence."""


def _macro_intelligence_brief_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    macro = shell.get("macro_data", {})
    factors = macro.get("risk_factors", [])
    factor_str = "; ".join(
        f"{f.get('title')} ({f.get('severity')}, {f.get('trend')})"
        for f in factors[:5]
    ) or "no significant macro risks"
    return f"""Write the macro intelligence brief — three sentences setting the macro context for this stock.

RISK FACTORS: {factor_str}

EVIDENCE:
{evidence}

{_style_block(persona)}
{_length_brief(3, 70)}

Sentence 1: the macro environment for this company. Sentence 2: which specific risk hits this business hardest, and why. Sentence 3: what would change the picture (ratecut, election, regulation, etc.)."""


def _price_action_narrative_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    pa = shell.get("price_action", {}) or {}
    event = pa.get("event") or {}
    event_str = (
        f"event: {event.get('tag')} on {event.get('date')}"
        if event else "no notable catalyst event in the last ~30 days"
    )
    # Real news headlines from `out.news` matched against the keyword
    # classifier, surfaced via `price_action._news_headlines` in the
    # collector. Ground the narrative in these so the AI cites real
    # articles instead of speculating about why the stock moved.
    headlines = pa.get("_news_headlines") or []
    if headlines:
        headlines_str = "\n".join(
            f"- [{h.get('date')}] {h.get('tag')}: {h.get('title')} "
            f"({h.get('site') or 'wire'})"
            for h in headlines[:5]
        )
        headlines_block = f"\nRECENT MATCHED HEADLINES (within the chart window):\n{headlines_str}\n"
    else:
        headlines_block = ""

    # Ground truth — direction/magnitude computed deterministically in
    # _build_price_action. iOS renders these same fields, so the AI
    # narrative cannot contradict what the user sees on screen.
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

    return f"""Explain WHY the stock moved this way in two sentences.

GROUND TRUTH: {ground_truth}
CHART CONTEXT: {event_str}
{headlines_block}
EVIDENCE:
{evidence}

{_style_block(persona)}
{_length_brief(2, 45)}

Step 1 — accept the ground-truth direction above. Never contradict it. Do not write "dip", "fell", "decline" when the ground truth is UP; do not write "rally", "surge", "gained" when the ground truth is DOWN. If matched headlines suggest the opposite of the ground truth, treat the move as broader/market-driven rather than stock-specific.

Step 2 — pick the single best catalyst that explains the move's direction and magnitude:
  - prefer the event (earnings / catalyst) above if listed,
  - otherwise cite the most relevant matched headline by name,
  - otherwise attribute it to a broader market/sector move.

If the move is FLAT, say so directly and skip the catalyst hunt. Never fabricate a catalyst that isn't in the data above."""


def _revenue_engine_analysis_note_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    re_section = shell.get("revenue_engine", {}) or {}
    segs = re_section.get("segments", [])
    seg_str = ", ".join(
        f"{s.get('name')} {s.get('current_revenue')} (prior {s.get('previous_revenue')})"
        for s in segs[:5]
    ) or "no segment breakdown available"
    unit = re_section.get("revenue_unit", "Millions")
    return f"""Write a one-line takeaway on the revenue mix.

SEGMENTS ({unit}): {seg_str}

{_style_block(persona)}
{_length_brief(1, 20)}

Name where the engine is shifting (which segment is winning or losing) — don't just list the segments."""


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


def _key_management_insight_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    return f"""Write the one or two-sentence note on insider/exec ownership alignment.

EVIDENCE:
{evidence}

{_style_block(persona)}
LENGTH: 1-2 sentences, total under 30 words.

Lead with whether management has skin in the game (or doesn't). If the roster is sparse or unavailable, say so directly."""


def _insider_ownership_note_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    insider = shell.get("insider_data", {}) or {}
    sentiment = insider.get("sentiment", "neutral")
    txns = insider.get("transactions", [])
    return f"""Write a one-line context note on the 90-day insider activity.

SENTIMENT: {sentiment}
TRANSACTIONS: {txns}

{_style_block(persona)}
{_length_brief(1, 25)}

If insiders aren't doing anything noteworthy, write the literal word: NULL"""


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
    title = factor.get("title", "Risk")
    severity = factor.get("severity", "medium")
    return f"""Write the one-line description for this critical factor.

FACTOR: {title}
SEVERITY: {severity}

EVIDENCE:
{evidence}

{_style_block(persona)}
{_length_brief(1, 25)}

Be concrete about *what* the risk is — not just that it exists."""


def _hedge_fund_note_prompt(
    persona: PersonaConfig, evidence: str, shell: Dict[str, Any]
) -> str:
    return f"""Write a one-line read on institutional positioning (hedge funds, big asset managers).

EVIDENCE:
{evidence}

{_style_block(persona)}
{_length_brief(1, 25)}

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

    # ── executive_summary_text ────────────────────────────────────────
    jobs.append(NarrativeJob(
        label="executive_summary_text",
        prompt=_executive_summary_text_prompt(persona, evidence, shell),
        word_cap=44,
        apply=lambda v: shell.__setitem__("executive_summary_text", v),
        fallback_value=FALLBACK["executive_summary_text"],
    ))

    # ── executive_summary_bullets[i].text (fan out) ───────────────────
    bullets = shell.get("executive_summary_bullets") or []
    for i, bullet in enumerate(bullets):
        if not isinstance(bullet, dict):
            continue
        jobs.append(NarrativeJob(
            label=f"executive_summary_bullet_{i}",
            prompt=_executive_summary_bullet_prompt(persona, evidence, bullet, i),
            word_cap=14,
            apply=_setter_for_dict_key(bullet, "text"),
            fallback_value=bullet.get("text") or FALLBACK["executive_summary_bullet"],
        ))

    # ── overall_assessment.text ───────────────────────────────────────
    if isinstance(shell.get("overall_assessment"), dict):
        oa = shell["overall_assessment"]
        jobs.append(NarrativeJob(
            label="overall_assessment_text",
            prompt=_overall_assessment_text_prompt(persona, evidence, shell),
            word_cap=28,
            apply=_setter_for_dict_key(oa, "text"),
            fallback_value=FALLBACK["overall_assessment_text"],
        ))

    # ── moat: durability + competitive insight ────────────────────────
    moat = shell.get("moat_competition")
    if isinstance(moat, dict):
        jobs.append(NarrativeJob(
            label="moat_durability_note",
            prompt=_moat_durability_note_prompt(persona, evidence, shell),
            word_cap=22,
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

    # ── macro: headline + intelligence_brief ──────────────────────────
    macro = shell.get("macro_data")
    if isinstance(macro, dict):
        jobs.append(NarrativeJob(
            label="macro_headline",
            prompt=_macro_headline_prompt(persona, evidence, shell),
            word_cap=10,
            apply=_setter_for_dict_key(macro, "headline"),
            fallback_value=FALLBACK["macro_headline"],
        ))
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
            prompt=_price_action_narrative_prompt(persona, evidence, shell),
            word_cap=50,
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
            word_cap=34,
            apply=_setter_for_dict_key(km, "ownership_insight"),
            fallback_value=FALLBACK["key_management_insight"],
        ))

    # ── insider_data.ownership_note + insider_vital.key_insight ──────
    insider = shell.get("insider_data")
    if isinstance(insider, dict):
        jobs.append(NarrativeJob(
            label="insider_ownership_note",
            prompt=_insider_ownership_note_prompt(persona, evidence, shell),
            word_cap=28,
            apply=_setter_with_null(insider, "ownership_note"),
            fallback_value=FALLBACK["insider_ownership_note"],
            nullable=True,
        ))

    iv = (shell.get("key_vitals") or {}).get("insider")
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
            apply=_setter_for_dict_key(card, "quality_label"),
            fallback_value=FALLBACK["fundamental_quality_label"],
        ))

    # ── critical_factors[i].description (fan out) ─────────────────────
    factors = shell.get("critical_factors") or []
    for i, factor in enumerate(factors):
        if not isinstance(factor, dict):
            continue
        jobs.append(NarrativeJob(
            label=f"critical_factor_description_{i}",
            prompt=_critical_factor_description_prompt(persona, evidence, factor),
            word_cap=28,
            apply=_setter_for_dict_key(factor, "description"),
            fallback_value=factor.get("description") or FALLBACK["critical_factor_description"],
        ))

    # ── wall_street_consensus.hedge_fund_note ────────────────────────
    ws = shell.get("wall_street_consensus")
    if isinstance(ws, dict):
        jobs.append(NarrativeJob(
            label="hedge_fund_note",
            prompt=_hedge_fund_note_prompt(persona, evidence, shell),
            word_cap=28,
            apply=_setter_with_null(ws, "hedge_fund_note"),
            fallback_value=FALLBACK["hedge_fund_note"],
            nullable=True,
        ))

    return jobs


# ── Setter helpers: capture the dict ref so closures don't share state


def _setter_for_dict_key(target: Dict[str, Any], key: str) -> Callable[[Any], None]:
    def _apply(value: Any) -> None:
        target[key] = value
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
  "executive_summary_bullets": [
    {{"category": "Catalyst|Valuation|Risk|Growth|Moat", "sentiment": "positive|neutral|negative", "text": ""}}
  ],
  "core_thesis": {{
    "bull_case": ["<2-4 sentences, each ≤18 words. Count matches the strength of the case — use 2 when only two distinct strong points exist, 4 only when four genuinely non-overlapping points exist>"],
    "bear_case": ["<2-4 sentences, each ≤18 words. Count matches the strength of the case — use 2 when only two distinct strong points exist, 4 only when four genuinely non-overlapping points exist>"]
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
      {{"name": "Switching Costs",  "score": 0.0, "peer_score": 0.0}},
      {{"name": "Network Effects",  "score": 0.0, "peer_score": 0.0}},
      {{"name": "Brand Power",      "score": 0.0, "peer_score": 0.0}},
      {{"name": "Cost Advantage",   "score": 0.0, "peer_score": 0.0}},
      {{"name": "Intangible Assets","score": 0.0, "peer_score": 0.0}}
    ],
    "durability_note": "",
    "competitors": [
      {{"name": "Competitor", "ticker": "TICK", "moat_score": 0.0, "market_share_percent": 0.0, "threat_level": "low|moderate|high"}}
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
    "hedge_fund_note": null
  }},
  "critical_factors": [
    {{"title": "Factor Title", "severity": "high|medium|low", "description": ""}}
  ]
}}

RULES:
- quality_score: integer 0-100 reflecting your conviction on this stock
- moat dimension scores 0.0-10.0
- macro impact 0.0-1.0
- 3-4 executive_summary_bullets (don't pad to 5)
- 2-4 bull_case + 2-4 bear_case — count matches the case's strength. Bull and bear can have different counts (e.g. 4 bull + 2 bear when the bull case is rich and the bear thin). Do NOT default to 3 — pick the count that fits the data, not the layout.
- 3-6 macro risk_factors (skip ones that don't materially affect this company)
- 2-4 critical_factors
- 3-5 competitors, ranked by relevance
- DO NOT include fundamental_metrics or overall_assessment — both are now built deterministically from snapshot services and any AI version is discarded.
- Leave every "text" / "narrative" / "headline" / "ownership_insight" / "key_insight" / "description" / "intelligence_brief" / "competitive_insight" / "durability_note" / "analysis_note" / "guidance_quote" / "ownership_note" / "hedge_fund_note" field as the placeholder shown above. Those will be written by a separate prose pass.
- moat_competition.market_dynamics.concentration AND moat_competition.competitors are RECOMPUTED downstream from real FMP peer + sector data — your values for those fields are discarded. You may still fill them as best-guess for sanity, but accuracy doesn't matter there.
- moat_competition.market_dynamics.current_tam / future_tam: STRICT EXTRACTION ONLY. Set to a USD-denominated number (e.g. 150000000000 for $150B) **only when the EARNINGS-CALL TRANSCRIPT EXCERPT or the company description above contains an explicit, quoted TAM/addressable-market figure**. Set both to 0 when no figure is disclosed. Do NOT estimate from sector context, competitor data, or your training-data knowledge of the industry. Forced fabrication here is the highest-cost failure mode for this product.
- moat_competition.market_dynamics.tam_source_quote: when current_tam > 0, paste the verbatim sentence from the transcript / description that contains the figure (≤ 200 chars). When current_tam = 0, return "".
- moat_competition.market_dynamics.future_year: when future_tam > 0, set to the year in which the projection is stated (e.g. "2030"). Otherwise leave the default.
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
        "executive_summary_bullets": [
            {"category": "Notice", "sentiment": "neutral", "text": ""}
        ],
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
        "wall_street": {"hedge_fund_note": None},
        "critical_factors": [],
    }
