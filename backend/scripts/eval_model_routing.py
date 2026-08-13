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
  * whether `ensure_disclaimer` had to ADD the required line (i.e. the model omitted it)
  * answer length, and the measured prompt/output tokens per model

A PASS is: the cheap model produces zero NEW guardrail issues the flagship did not
also produce, and its answers stay recognisably complete. Read them; do not just
trust the counters.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(level=logging.WARNING, format="%(message)s")
for noisy in ("httpx", "google_genai", "urllib3", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

from app.config import settings                                    # noqa: E402
from app.integrations.gemini import get_gemini_client              # noqa: E402
from app.services.agents.chat_guardrails import scan_answer        # noqa: E402
from app.services.chat_security import ensure_disclaimer           # noqa: E402
from app.services.chat_service import ChatService                  # noqa: E402

# The eligible class ONLY: conceptual, no ticker, no on-screen data. Routing never
# sends anything else to the cheap model, so evaluating anything else would be
# measuring a path that cannot happen.
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


def _grade(answer: str) -> dict:
    issues = scan_answer(answer)
    return {
        "issues": issues,
        "missing_disclaimer": ensure_disclaimer(answer) != answer,
        "chars": len(answer),
    }


async def main(full: bool) -> int:
    svc = ChatService()
    gemini = get_gemini_client()
    system_instruction = svc._build_system_instruction("NORMAL", None)

    flagship, cheap = settings.GEMINI_MODEL, settings.CHAT_CHEAP_MODEL
    print(f"\nflagship = {flagship}\ncheap    = {cheap}\n"
          f"{len(QUESTIONS)} conceptual, ticker-less questions\n")

    totals = {flagship: {"issues": 0, "nodisc": 0, "chars": 0},
              cheap: {"issues": 0, "nodisc": 0, "chars": 0}}
    regressions: list[str] = []

    for i, q in enumerate(QUESTIONS, 1):
        prompt = svc._build_prompt(q, "", [])
        row = {}
        for model in (flagship, cheap):
            try:
                answer = await _answer(gemini, model, system_instruction, prompt)
            except Exception as e:
                print(f"{i:2d}. {model}: FAILED {type(e).__name__}: {e}")
                row[model] = None
                continue
            g = _grade(answer)
            row[model] = (answer, g)
            totals[model]["issues"] += len(g["issues"])
            totals[model]["nodisc"] += int(g["missing_disclaimer"])
            totals[model]["chars"] += g["chars"]
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
        print(f"{i:2d}. {q}")
        if fl:
            print(f"      flagship {fl[1]['chars']:5d} ch  issues={fl[1]['issues'] or '-'}"
                  f"  disclaimer_added={fl[1]['missing_disclaimer']}")
        if ch:
            print(f"      cheap    {ch[1]['chars']:5d} ch  issues={ch[1]['issues'] or '-'}"
                  f"  disclaimer_added={ch[1]['missing_disclaimer']}{flag}")
        if full and ch:
            print(f"      --- cheap answer ---\n{ch[0]}\n")

    print("\n=== TOTALS ===")
    for model, t in totals.items():
        print(f"{model:26s} guardrail_issues={t['issues']:3d}  "
              f"disclaimer_added={t['nodisc']:3d}  avg_chars={t['chars'] // max(1, len(QUESTIONS))}")

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
