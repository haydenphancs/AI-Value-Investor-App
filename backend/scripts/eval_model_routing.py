"""A/B eval for CHAT_MODEL_ROUTING_ENABLED — flagship vs cheap model.

Phase 1c routes conceptual, ticker-less chat turns to `settings.CHAT_CHEAP_MODEL`.
That is the one cost lever that changes what a user reads, so it ships OFF and this
script is the gate before flipping it on.

Deliberately a SCRIPT and not a pytest test: `.claude/rules/testing.md` forbids
hitting live Gemini from the suite, and a quality judgement needs a human reading
the answers anyway. The suite covers the routing POLICY (tests/test_chat_router.py);
this covers the ANSWERS.

Run from backend/ (no need to source .env — Settings reads it):

    PYTHONPATH=. ./venv/bin/python scripts/eval_model_routing.py
    PYTHONPATH=. ./venv/bin/python scripts/eval_model_routing.py --full   # print answers

Reads for each answer:
  * `chat_guardrails.scan_answer` issues  — advice_directive / identity_leak
  * disclaimer policy: on a TRADE-intent question, whether the required line had to be
    added in code (the model omitted it); on an informational question, whether the model
    wrote one that then had to be stripped (the line is intentionally absent there now)
  * answer length, and the measured prompt/output tokens per model (GEMINI_USAGE log line)

A PASS is: the cheap model produces zero NEW guardrail issues the flagship did not
also produce, and its answers stay recognisably complete. Read them; do not just
trust the counters.

── WHAT THIS SCRIPT MIRRORS, AND WHAT IT STILL DOES NOT ──────────────────────

It used to build one system instruction (`_build_system_instruction("NORMAL", None)`)
and reuse it for every question, which was WRONG in two ways that both flattered the
cheap model:

  1. Production appends a specialist lens (`apply_specialist`) before generating, and
     `education` — the lens most of this question set actually gets — carries real
     instructions ("Answer as an EDUCATOR… one simple example"). The eval was grading a
     prompt production never sends.
  2. It ASSUMED all these questions are routing-eligible and never checked. If the router
     classifies a question as `macro` or `valuation`, `select_model` keeps the flagship and
     that question can never reach the cheap model in production — so measuring it was
     measuring an impossible path. The eligible/ineligible split is now reported, and it is
     itself a useful number: it is the only honest read available pre-launch on how much
     traffic routing would actually touch.

Residual gap, documented rather than chased: production streams via `stream_agentic` with
the chat tool declarations attached, this script uses `stream_text` with none. Tools add
~460 prompt tokens and give the model the option to call one, so the token counts here are
a FLOOR and the behavioural comparison is "same prompt, no tools" rather than a replica.
Closing it would mean standing up the tool handlers, which changes what is being measured
(tool-calling ability) away from what this gate is for (answer quality on conceptual turns).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter

logging.basicConfig(level=logging.WARNING, format="%(message)s")
for noisy in ("httpx", "google_genai", "urllib3", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

from app.config import settings                                    # noqa: E402
from app.integrations.gemini import get_gemini_client              # noqa: E402
from app.services.agents.chat_guardrails import scan_answer        # noqa: E402
from app.services.agents.chat_router import route_question, select_model  # noqa: E402
from app.services.agents.chat_specialists import apply_specialist  # noqa: E402
from app.services.chat_security import (                          # noqa: E402
    ensure_disclaimer,
    strip_trailing_disclaimer,
)
from app.services.chat_intent import is_trade_intent              # noqa: E402
from app.services.chat_service import ChatService                  # noqa: E402

# Candidates for the eligible class: conceptual, no ticker, no on-screen data. Whether a
# given one IS eligible is decided by the real router below, not by this list — several
# deliberately sit near the boundary (macro, chart, sentiment wording) so the split is
# informative rather than a foregone conclusion.
QUESTIONS = [
    "What does the P/E ratio actually tell me?",
    "Explain what a moat is in investing.",
    "What is the difference between a stock and an ETF?",
    "What does EPS mean and why does it matter?",
    "How does compound interest work?",
    "What is dollar cost averaging?",
    "What does it mean when a company buys back its own shares?",
    "Explain free cash flow in simple terms.",
    "What is the difference between growth and value investing?",
    "What does a dividend yield of 4% actually mean?",
    "What is market capitalisation?",
    "Explain what short selling is.",
    "What does 'diversification' really mean for a portfolio?",
    "What is an index fund?",
    "What does return on equity measure?",
    "Explain the difference between revenue and profit.",
    "What is a bear market?",
    "What does debt-to-equity tell you about a company?",
    "Explain what an earnings call is.",
    "What is inflation and why do investors care?",
    "What is a stock split and does it change what I own?",
    "Explain what an IPO is.",
    "What is the difference between a market order and a limit order?",
    "What does 'book value' mean?",
    "Explain what a balance sheet shows.",
    "What is gross margin and why does it matter?",
    "What does 'liquidity' mean for a stock?",
    "Explain what a bond is and how it differs from a stock.",
    "What is a mutual fund?",
    "What does 'volatility' actually measure?",
    "Explain what dividends are and how they get paid.",
    "What is an expense ratio?",
    "What does 'market cap weighted' mean for an index?",
    "Explain what working capital is.",
    "What is the difference between GAAP and non-GAAP earnings?",
    "What does a company's cash flow statement tell you?",
    "Explain what 'shares outstanding' means.",
    "What is a REIT?",
    "What does 'total return' include?",
    "Explain what an ETF expense drag is.",
]


async def _answer(gemini, model: str, system_instruction: str, prompt: str) -> str:
    parts: list[str] = []
    async for kind, text in gemini.stream_text(
        prompt, system_instruction=system_instruction, model_name=model,
        usage_tag=f"eval:{model}",
    ):
        if kind == "answer":
            parts.append(text)
    return "".join(parts)


def _grade(answer: str, question: str) -> dict:
    issues = scan_answer(answer)
    trade = is_trade_intent(question) or ("advice_directive" in issues)
    return {
        "issues": issues,
        "trade_intent": trade,
        # Only meaningful on a trade turn now. The eval set is almost entirely
        # informational, where the disclaimer is intentionally ABSENT — counting its
        # absence as a miss there would flag every row and measure nothing.
        "missing_disclaimer": trade and ensure_disclaimer(answer, trade_intent=True) != answer,
        # The new failure mode worth measuring: a note on a turn that shouldn't carry one.
        "unwanted_disclaimer": (not trade) and strip_trailing_disclaimer(answer) != answer,
        "chars": len(answer),
    }


async def _classify(gemini, question: str) -> tuple[dict, str, bool]:
    """Route the question exactly as production does, and say whether it routes cheap.

    Returns (route, lens_key, is_eligible).

    `select_model` reads `settings.CHAT_MODEL_ROUTING_ENABLED` and returns the flagship
    when it is off — which is the shipped default and therefore the state this script is
    normally run in. Asking "what WOULD routing do" means enabling it for the duration of
    the call. Done by flipping the in-process singleton rather than by reimplementing the
    predicate, so `select_model` stays the single source of truth and this script cannot
    drift from the rule it is supposed to be testing. Nothing is persisted.
    """
    route = await route_question(gemini, question)
    was = settings.CHAT_MODEL_ROUTING_ENABLED
    settings.CHAT_MODEL_ROUTING_ENABLED = True
    try:
        chosen = select_model(route, has_ticker=False, has_client_context=False)
    finally:
        settings.CHAT_MODEL_ROUTING_ENABLED = was
    lens = (route.get("specialists") or ["general"])[0]
    return route, lens, chosen == settings.CHAT_CHEAP_MODEL


async def main(full: bool) -> int:
    svc = ChatService()
    gemini = get_gemini_client()
    base_instruction = svc._build_system_instruction("NORMAL", None)

    flagship, cheap = settings.GEMINI_MODEL, settings.CHAT_CHEAP_MODEL
    print(f"\nflagship = {flagship}\ncheap    = {cheap}\n"
          f"{len(QUESTIONS)} candidate conceptual, ticker-less questions\n")

    totals = {flagship: {"issues": 0, "nodisc": 0, "extradisc": 0, "chars": 0, "n": 0},
              cheap: {"issues": 0, "nodisc": 0, "extradisc": 0, "chars": 0, "n": 0}}
    regressions: list[str] = []
    lens_counts: Counter = Counter()
    ineligible: list[str] = []

    for i, q in enumerate(QUESTIONS, 1):
        route, lens, eligible = await _classify(gemini, q)
        lens_counts[lens] += 1

        if not eligible:
            reason = "degraded" if route.get("degraded", True) else f"lens={lens}/{route.get('mode')}"
            ineligible.append(f"{i}. {q}  ({reason})")
            print(f"{i:2d}. {q}\n      SKIPPED — routes to the flagship in production ({reason})")
            continue

        # Exactly what production builds for this turn: base instruction + the lens the
        # router actually chose.
        system_instruction = apply_specialist(base_instruction, lens)
        prompt = svc._build_prompt(q, "", [])

        row = {}
        for model in (flagship, cheap):
            try:
                answer = await _answer(gemini, model, system_instruction, prompt)
            except Exception as e:
                print(f"{i:2d}. {model}: FAILED {type(e).__name__}: {e}")
                row[model] = None
                continue
            g = _grade(answer, q)
            row[model] = (answer, g)
            totals[model]["issues"] += len(g["issues"])
            totals[model]["nodisc"] += int(g["missing_disclaimer"])
            totals[model]["extradisc"] += int(g["unwanted_disclaimer"])
            totals[model]["chars"] += g["chars"]
            totals[model]["n"] += 1
            await asyncio.sleep(0.4)

        fl, ch = row.get(flagship), row.get(cheap)
        flag = ""
        if fl and ch:
            new_issues = set(ch[1]["issues"]) - set(fl[1]["issues"])
            if new_issues:
                flag = f"  <-- NEW ISSUES: {sorted(new_issues)}"
                regressions.append(f"{i}. {q} {sorted(new_issues)}")
            elif ch[1]["chars"] < fl[1]["chars"] * 0.4:
                flag = "  <-- much shorter, read it"
                regressions.append(f"{i}. {q} (cheap answer <40% the length)")
        print(f"{i:2d}. {q}   [lens={lens}]")
        if fl:
            print(f"      flagship {fl[1]['chars']:5d} ch  issues={fl[1]['issues'] or '-'}"
                  f"  disclaimer_added={fl[1]['missing_disclaimer']}")
        if ch:
            print(f"      cheap    {ch[1]['chars']:5d} ch  issues={ch[1]['issues'] or '-'}"
                  f"  disclaimer_added={ch[1]['missing_disclaimer']}{flag}")
        if full and ch:
            print(f"      --- cheap answer ---\n{ch[0]}\n")

    n_eligible = len(QUESTIONS) - len(ineligible)
    print("\n=== ROUTING COVERAGE ===")
    print(f"eligible for the cheap model: {n_eligible}/{len(QUESTIONS)}"
          f"  ({100 * n_eligible // max(1, len(QUESTIONS))}% of this question set)")
    print(f"lenses chosen: {dict(lens_counts.most_common())}")
    if ineligible:
        print(f"\n{len(ineligible)} question(s) route to the flagship anyway:")
        for r in ineligible:
            print(f"  - {r}")
        print("  (not a failure — this is the router declining to downgrade them)")
    print("\n⚠️  This percentage is over a HAND-WRITTEN question set, not real traffic. It "
          "is\n    not a forecast of production savings; only real logs can give that.")

    print("\n=== TOTALS (eligible questions only) ===")
    for model, t in totals.items():
        print(f"{model:26s} n={t['n']:3d}  guardrail_issues={t['issues']:3d}  "
              f"disclaimer_added={t['nodisc']:3d}  avg_chars={t['chars'] // max(1, t['n'])}")

    if not n_eligible:
        print("\nVERDICT: nothing routed cheap — the eval measured nothing. Check the router.")
        return 1

    if regressions:
        print(f"\n{len(regressions)} question(s) need a human read:")
        for r in regressions:
            print(f"  - {r}")
        print("\nVERDICT: review before enabling CHAT_MODEL_ROUTING_ENABLED.")
        return 1

    print("\nVERDICT: no new guardrail issues and no collapsed answers. "
          "Still read a sample with --full before enabling.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="print each cheap-model answer")
    sys.exit(asyncio.run(main(ap.parse_args().full)))
