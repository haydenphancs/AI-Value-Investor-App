"""A/B eval for the report-path thinking budgets — the evidence behind 9b.7.

`REPORT_NARRATIVE_THINKING_BUDGET` (Stage B) and `REPORT_STAGE_A_THINKING_BUDGET`
(Stage A) both default to 0 (no thinking). gemini-2.5-flash reasons by default
and those thought tokens bill at the OUTPUT rate while producing nothing the user
reads — which is what pulled worst-case report COGS from a documented $0.05-0.06
toward $0.09-0.15. This script is how that claim gets a number instead of an
estimate, and how the Stage-A quality risk gets checked rather than assumed.

Deliberately a SCRIPT and not a pytest test: `.claude/rules/testing.md` forbids
hitting live Gemini from the suite, and a quality judgement needs a human reading
the answers. The suite covers the POLICY (tests/test_report_thinking_budget.py,
tests/test_narrative_context_cache.py); this covers the ANSWERS.

Run from backend/ (no need to source .env — Settings reads it):

    PYTHONPATH=. ./venv/bin/python scripts/eval_report_thinking.py
    PYTHONPATH=. ./venv/bin/python scripts/eval_report_thinking.py --full
    PYTHONPATH=. ./venv/bin/python scripts/eval_report_thinking.py \
        --tickers MSFT,KO,MRNA --personas warren_buffett,cathie_wood

Exit 0 = PASS. Read a sample with --full before believing the counters.

── THREE TRAPS THIS SCRIPT EXISTS TO AVOID ──────────────────────────────────

1. **Our own response cache.** `generate_text` / `generate_json` memoize on
   (prompt, system_instruction, model, max_out, tb) for GEMINI_CACHE_TTL = 1h.
   Different budgets are different keys, so the A/B itself is safe — but a
   RE-RUN inside the hour replays the first run's token counts and the second
   run looks free. The cache is neutered at startup and the script says so.

2. **thoughts vs candidates.** `config.py` has claimed "gemini-2.5-flash counts
   thinking in output_tok", i.e. that `candidates_token_count` already includes
   thoughts. The SDK's own docs say `total = prompt + candidates + tool_use +
   thoughts`, which only holds if candidates EXCLUDES them. The verdict is
   computed from the arithmetic on a REAL uncapped call and printed first —
   with thoughts == 0 the two branches are indistinguishable, which is exactly
   how a wrong claim survives.

3. **A budget the API ignored.** If thinking does not actually fall, everything
   downstream measures nothing. The thinking-token delta is a PASS criterion,
   not a footnote.

── RESIDUAL GAP, documented rather than chased ──────────────────────────────

Stage B is measured on the INLINE path (`generate_text`), while production takes
the CACHED path (`generate_text_cached`) whenever the evidence clears the model's
~1024-token floor. Prompt-side counts here are therefore a CEILING. The thinking
and output side — which is all this change touches — is unaffected by the
context cache and is measured exactly.

The one thing that gap COULD have hidden — the API rejecting `thinking_config`
alongside `cached_content`, which would send every Stage-B call down the
fail-safe inline path and silently double cost and latency — was probed
directly against the live API (2026-08-27): a real CachedContent served
`cached_tok=2543` identically at `budget=None` (52 thought tokens) and at
`budget=0` (none), same answer both times. The two optimisations compose.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.integrations import gemini as gem
from app.integrations.gemini import get_gemini_client, _TTLCache, _response_usage
from app.services.agents.narrative_prompts import (
    _post_process,
    build_narrative_jobs,
    build_stage_a_prompt,
    parse_stage_a_response,
)
from app.services.agents.persona_config import get_persona_config
from app.services.agents.ticker_report_data_collector import (
    build_financial_context,
    get_collector,
)

# gemini-2.5-flash, and the reason thinking is a cost line at all: thought
# tokens bill at the OUTPUT rate.
USD_IN_PER_1M = 0.30
USD_OUT_PER_1M = 2.50


# ── plumbing ─────────────────────────────────────────────────────────────────

def _disarm_response_cache() -> None:
    """Trap 1. Without this a re-run inside the hour reports a free lunch."""
    client = get_gemini_client()
    client._response_cache = _TTLCache(max_size=1, ttl_seconds=0)
    print("• response cache DISARMED (ttl=0) — token counts are from real calls\n")


async def _call(prompt: str, system: str, budget: Optional[int],
                json_mode: bool) -> Tuple[str, Dict[str, Any], float]:
    """One raw generation, returning (text, usage, seconds).

    Goes through the real client so retry/timeout/circuit behaviour is the
    production one, then reads usage off the response the same way the client
    does.
    """
    client = get_gemini_client()
    started = time.monotonic()
    captured: Dict[str, Any] = {}

    real_usage = gem._response_usage

    def _spy(response):
        usage = real_usage(response)
        captured.update(usage)
        return usage

    gem._response_usage = _spy
    try:
        if json_mode:
            result = await client.generate_json(
                prompt=prompt, system_instruction=system, thinking_budget=budget,
            )
        else:
            result = await client.generate_text(
                prompt=prompt, system_instruction=system, thinking_budget=budget,
            )
    finally:
        gem._response_usage = real_usage

    return result.get("text") or "", dict(captured), time.monotonic() - started


def _cost_usd(usage: Dict[str, Any]) -> float:
    """Thinking bills at the OUTPUT rate — that is the whole 9b.7 premise."""
    prompt = usage.get("prompt") or 0
    output = usage.get("output") or 0
    thoughts = usage.get("thoughts") or 0
    return (prompt * USD_IN_PER_1M + (output + thoughts) * USD_OUT_PER_1M) / 1_000_000


def _accounting_verdict(usage: Dict[str, Any]) -> str:
    """Trap 2. Settle it from the arithmetic on a real uncapped call."""
    total = usage.get("total")
    prompt = usage.get("prompt")
    output = usage.get("output")
    thoughts = usage.get("thoughts")
    if None in (total, prompt, output) or not thoughts:
        return "INDETERMINATE"
    if total - prompt - output - thoughts == 0:
        return "EXCLUDES"          # candidates excludes thoughts (SDK docs)
    if total - prompt - output == 0:
        return "INCLUDES"          # candidates already includes thoughts
    return "INDETERMINATE"


_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def _ungrounded_numerals(text: str, evidence: str) -> List[str]:
    """Numbers in the answer that do not appear in the evidence.

    Stage-B prompts demand "cite a number from the CARD VALUES block", and a
    model that stopped reasoning inventing a figure is the specific quality risk
    here — a length heuristic cannot see it. KNOWN FALSE POSITIVE: percentages
    the model recomputed correctly. Treat hits as a read-this list, not a verdict.
    """
    out = []
    for raw in _NUM.findall(text):
        token = raw.rstrip(".").replace(",", "")
        if len(token.lstrip("-").replace(".", "")) < 2:
            continue                       # single digits are prose, not data
        if token not in evidence.replace(",", ""):
            out.append(token)
    return out


# ── Stage B ──────────────────────────────────────────────────────────────────

async def _eval_stage_b(persona, evidence: str, shell: Dict[str, Any],
                        budgets: List[Optional[int]], full: bool) -> Dict[str, Any]:
    jobs = build_narrative_jobs(persona, evidence, shell)
    print(f"  Stage B — {len(jobs)} narrative jobs")

    totals = {b: {"thoughts": 0, "output": 0, "prompt": 0, "cost": 0.0} for b in budgets}
    problems: List[str] = []
    baseline_text: Dict[str, str] = {}

    for job in jobs:
        for budget in budgets:
            text, usage, secs = await _call(
                job.prompt, persona.system_prompt, budget, json_mode=False,
            )
            cleaned = _post_process(text, word_cap=job.word_cap)
            agg = totals[budget]
            agg["thoughts"] += usage.get("thoughts") or 0
            agg["output"] += usage.get("output") or 0
            agg["prompt"] += usage.get("prompt") or 0
            agg["cost"] += _cost_usd(usage)

            label = "default" if budget is None else str(budget)
            print(f"    {job.label:38s} cap={job.word_cap:>3} budget={label:>7} "
                  f"thoughts={usage.get('thoughts')} out={usage.get('output')} "
                  f"{secs:.2f}s")
            if full:
                print(f"        {cleaned}")

            if budget is None:
                baseline_text[job.label] = cleaned
                continue

            base = baseline_text.get(job.label, "")
            if not cleaned.strip():
                problems.append(f"{job.label}: EMPTY at budget={budget} "
                                f"(ships the honest sentinel to a paying user)")
            elif base and len(cleaned) < 0.4 * len(base):
                problems.append(f"{job.label}: {len(cleaned)} chars vs {len(base)} "
                                f"uncapped at budget={budget}")
            if cleaned.endswith("…") and not base.endswith("…"):
                problems.append(f"{job.label}: word-cap TRUNCATED at budget={budget} "
                                f"but not uncapped — less thinking, less compression")
            new_nums = set(_ungrounded_numerals(cleaned, evidence)) - set(
                _ungrounded_numerals(base, evidence))
            if new_nums:
                problems.append(f"{job.label}: ungrounded numeral(s) {sorted(new_nums)} "
                                f"at budget={budget} that the uncapped run did not produce")

    return {"totals": totals, "problems": problems, "jobs": len(jobs)}


# ── Stage A ──────────────────────────────────────────────────────────────────

_SCORED = ("overall_score", "rating", "verdict", "moat_score", "valuation",
           "fair_value", "confidence")


async def _eval_stage_a(persona, company: str, ticker: str, evidence: str,
                        budgets: List[Optional[int]], full: bool) -> Dict[str, Any]:
    prompt = build_stage_a_prompt(persona, company, ticker, evidence)
    shells: Dict[Any, Optional[Dict[str, Any]]] = {}
    totals = {b: {"thoughts": 0, "output": 0, "prompt": 0, "cost": 0.0} for b in budgets}
    unparseable = {b: 0 for b in budgets}
    first_uncapped_usage: Dict[str, Any] = {}

    for budget in budgets:
        text, usage, secs = await _call(prompt, persona.system_prompt, budget,
                                        json_mode=True)
        shell = parse_stage_a_response(text)
        shells[budget] = shell
        if shell is None:
            unparseable[budget] += 1
        agg = totals[budget]
        agg["thoughts"] += usage.get("thoughts") or 0
        agg["output"] += usage.get("output") or 0
        agg["prompt"] += usage.get("prompt") or 0
        agg["cost"] += _cost_usd(usage)
        if budget is None and not first_uncapped_usage:
            first_uncapped_usage = usage

        label = "default" if budget is None else str(budget)
        keys = len(shell) if isinstance(shell, dict) else 0
        print(f"    Stage A  budget={label:>7} thoughts={usage.get('thoughts')} "
              f"out={usage.get('output')} {secs:.2f}s keys={keys} "
              f"{'UNPARSEABLE' if shell is None else ''}")

    # Field-by-field. "Valid JSON with N keys" does not certify the JUDGEMENT.
    base = shells.get(None)
    diffs: List[str] = []
    if isinstance(base, dict):
        for budget, shell in shells.items():
            if budget is None or not isinstance(shell, dict):
                continue
            missing = sorted(set(base) - set(shell))
            added = sorted(set(shell) - set(base))
            if missing or added:
                diffs.append(f"budget={budget}: key set moved "
                             f"(missing={missing}, added={added})")
            for key in sorted(set(base) & set(shell)):
                if not any(s in key for s in _SCORED):
                    continue
                if base[key] != shell[key]:
                    diffs.append(f"budget={budget}: {key} {base[key]!r} -> {shell[key]!r}")
                    if full:
                        print(f"      DIFF {key}: {base[key]!r} -> {shell[key]!r}")

    return {"totals": totals, "diffs": diffs, "unparseable": unparseable,
            "uncapped_usage": first_uncapped_usage}


# ── main ─────────────────────────────────────────────────────────────────────

async def _run(args) -> int:
    _disarm_response_cache()

    budgets: List[Optional[int]] = [None] + [int(b) for b in args.budgets.split(",")]
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    personas = [p.strip() for p in args.personas.split(",") if p.strip()]

    b_totals = {b: {"thoughts": 0, "output": 0, "prompt": 0, "cost": 0.0} for b in budgets}
    a_totals = {b: {"thoughts": 0, "output": 0, "prompt": 0, "cost": 0.0} for b in budgets}
    unparseable = {b: 0 for b in budgets}
    problems: List[str] = []
    diffs: List[str] = []
    verdict = "INDETERMINATE"
    job_counts: List[int] = []

    collector = get_collector()
    for ticker in tickers:
        for persona_key in personas:
            persona = get_persona_config(persona_key)
            print(f"\n=== {ticker} / {persona_key} ===")
            out = await collector.collect(ticker, persona_key)
            evidence = build_financial_context(out)
            company = out.profile.get("companyName", ticker)

            a = await _eval_stage_a(persona, company, ticker, evidence, budgets, args.full)
            if verdict == "INDETERMINATE" and a["uncapped_usage"]:
                verdict = _accounting_verdict(a["uncapped_usage"])
            for budget in budgets:
                for key in ("thoughts", "output", "prompt", "cost"):
                    a_totals[budget][key] += a["totals"][budget][key]
                unparseable[budget] += a["unparseable"][budget]
            diffs.extend(f"{ticker}/{persona_key} {d}" for d in a["diffs"])

            base_shell = collector.assemble_report(out, {})
            b = await _eval_stage_b(persona, evidence, base_shell, budgets, args.full)
            for budget in budgets:
                for key in ("thoughts", "output", "prompt", "cost"):
                    b_totals[budget][key] += b["totals"][budget][key]
            problems.extend(f"{ticker}/{persona_key} {p}" for p in b["problems"])
            job_counts.append(b["jobs"])

    runs = max(1, len(tickers) * len(personas))

    print("\n" + "=" * 78)
    print(f"TOKEN ACCOUNTING VERDICT: candidates_token_count {verdict} thoughts")
    if verdict == "EXCLUDES":
        print("  -> the SDK is right; config.py's 'flash counts thinking in output_tok'")
        print("     is WRONG, and GEMINI_USAGE output_tok under-reports billed output.")
    elif verdict == "INCLUDES":
        print("  -> config.py is right; thoughts are already inside output_tok.")
    else:
        print("  -> COULD NOT SETTLE IT. Either thoughts were 0 (so the two branches")
        print("     are indistinguishable) or a count was missing. Nothing below is")
        print("     trustworthy until this reads EXCLUDES or INCLUDES.")

    print("\nPER-REPORT MODEL (mean over "
          f"{runs} run(s), ~{sum(job_counts)//max(1,len(job_counts))} Stage-B jobs each)")
    print(f"  {'budget':>8}  {'A thoughts':>10} {'B thoughts':>10} "
          f"{'thoughts':>9} {'$/report':>9}")
    baseline_cost = None
    for budget in budgets:
        label = "default" if budget is None else str(budget)
        think = (a_totals[budget]["thoughts"] + b_totals[budget]["thoughts"]) / runs
        cost = (a_totals[budget]["cost"] + b_totals[budget]["cost"]) / runs
        if budget is None:
            baseline_cost = cost
        delta = "" if baseline_cost in (None, 0) or budget is None else \
            f"  ({(cost - baseline_cost) / baseline_cost * 100:+.0f}%)"
        print(f"  {label:>8}  {a_totals[budget]['thoughts']/runs:10.0f} "
              f"{b_totals[budget]['thoughts']/runs:10.0f} {think:9.0f} "
              f"${cost:8.4f}{delta}")

    base_think = (a_totals[None]["thoughts"] + b_totals[None]["thoughts"])
    capped = [b for b in budgets if b == 0]
    cut = 0.0
    if capped and base_think:
        zero_think = a_totals[0]["thoughts"] + b_totals[0]["thoughts"]
        cut = (base_think - zero_think) / base_think
        print(f"\n  thinking-token reduction at budget=0: {cut * 100:.1f}%")

    failures: List[str] = []
    if verdict == "INDETERMINATE":
        failures.append("token accounting could not be settled")
    if capped and cut < 0.80:
        failures.append(f"thinking fell only {cut*100:.0f}% — the API may have "
                        f"ignored the budget, so nothing here measured the change")
    for budget in budgets:
        if budget is not None and unparseable[budget] > unparseable[None]:
            failures.append(f"Stage A unparseable {unparseable[budget]}x at budget="
                            f"{budget} vs {unparseable[None]} uncapped — a silently "
                            f"degraded report, not an error")
    failures.extend(problems)
    failures.extend(diffs)

    print("\n" + "=" * 78)
    if failures:
        print(f"FAIL — {len(failures)} finding(s):")
        for f in failures:
            print(f"  • {f}")
        print("\nStage-A field diffs are NOT automatically fatal — read them. A moved")
        print("score on a 20-credit product is the risk this eval exists to surface.")
        return 1
    print("PASS — no empty/truncated/ungrounded Stage-B output, no Stage-A key-set or")
    print("scored-field movement, no extra degradation. Read a sample with --full.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", default="MSFT,KO,MRNA",
                        help="mega-cap, steady payer, loss-maker — evidence size "
                             "and word caps all differ")
    parser.add_argument("--personas", default="warren_buffett,cathie_wood")
    parser.add_argument("--budgets", default="0",
                        help="comma-separated; 'default' is always included. "
                             "Use 0,512,1024 to reproduce the identical-output claim.")
    parser.add_argument("--full", action="store_true",
                        help="print every answer and every Stage-A field diff")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
