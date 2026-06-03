"""Evaluate the FMP-news "core reason" detector vs a Gemini web-search oracle.

The Recent Price Movement section explains *why* a stock moved using only the
FMP news feed (cheap, no web search). This harness measures whether that cheap
method actually finds the right reason — and gives a head-to-head FMP-vs-Gemini
number so we can decide "keep FMP or switch the primary to web search?".

Method (no ground-truth labels exist, so we use an oracle + LLM judge):
  1. Sample liquid tickers from data/industry_universe.json.
  2. Keep only BIG moves (|z| >= 1 via _compute_price_volatility) — the only
     cases worth explaining.
  3. Run the FMP method (_build_price_action) → "system reason".
  4. Run Gemini web-search grounding → grounded "reference reason" + sources.
  5. An LLM judge grades system-vs-reference: Correct / Partial / Wrong / Missed,
     plus a head-to-head winner.
  6. Print coverage / precision / recall-vs-oracle / direction-agreement /
     win-rate + a decision verdict, and write per-case JSON to scripts/out/.

Coverage note: the harness always fetches the WIDE windowed news set + FMP
sentiment (what production uses after the move-first upgrade), so the eval
isolates the *method logic*. It bypasses the 24h report cache by calling
_build_price_action directly.

Examples:
    # Smoke test — no Gemini spend, proves wiring (still hits FMP):
    backend/venv/bin/python -m scripts.eval_price_catalyst --n 3 --no-oracle

    # Full eval (~$2-4 in Gemini grounding; needs backend/.env keys):
    backend/venv/bin/python -m scripts.eval_price_catalyst --n 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from dotenv import load_dotenv

load_dotenv(REPO / "backend" / ".env")

from app.integrations.fmp import get_fmp_client
from app.integrations.gemini import get_gemini_client
from app.services.agents.ticker_report_data_collector import (
    _build_price_action,
    _compute_price_volatility,
    _hist_list,
)

_UNIVERSE = REPO / "backend" / "data" / "industry_universe.json"
_OUT_DIR = REPO / "backend" / "scripts" / "out"

# Rough per-grounded-request fee (Google Search grounding). Only for the cost
# line — verify against current Google pricing.
_ORACLE_FEE_USD = 0.035

# Tag → polarity, for the direction-agreement proxy (does the catalyst's sign
# match the move's sign?). Ambiguous tags (M&A, generic) map to 0 = skip.
_TAG_POLARITY: Dict[str, int] = {
    "Earnings Beat": 1, "Earnings Miss": -1, "Earnings Reaction": 0,
    "FDA Approval": 1, "FDA Rejection": -1,
    "Analyst Upgrade": 1, "Analyst Downgrade": -1,
    "Guidance Raised": 1, "Guidance Cut": -1,
    "Legal/Regulatory": -1, "Layoffs": -1,
    "Buyback": 1, "Dividend": 1,
    "Bullish News": 1, "Bearish News": -1,
    "M&A": 0,
}

# Big ETFs slip past the symbol regex (3-4 letter tickers that look like
# stocks) — they have no company-specific catalyst, so deny the AUM-dominant
# ones that pollute the top of the market-cap-ranked pool.
_ETF_DENYLIST = {
    "SPY", "IVV", "VOO", "VTI", "QQQ", "QQQM", "DIA", "IWM", "VEA", "VWO",
    "EFA", "EEM", "AGG", "BND", "BNDX", "TLT", "LQD", "HYG", "GLD", "SLV",
    "IAU", "VIG", "VYM", "SCHD", "VUG", "VTV", "IWF", "IWD", "VXUS", "VT",
    "IEFA", "IEMG", "BIL", "SHV", "SMH", "SOXX", "ARKK",
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLP", "XLU", "XLB", "XLRE",
}


# ── Case building (FMP method) ────────────────────────────────────────


def _sample_tickers(n: int, seed: int) -> List[str]:
    """Liquid-name candidate pool (top market caps), shuffled. We over-sample
    because many tickers won't have a big move in the window."""
    data = json.loads(_UNIVERSE.read_text())
    caps: Dict[str, float] = {}
    for ind in data.get("industries", []):
        for t, c in (ind.get("market_caps") or {}).items():
            try:
                caps[t] = max(caps.get(t, 0.0), float(c or 0))
            except (TypeError, ValueError):
                continue
    # US common stock only — drop mutual funds (AAAAX), foreign dual-listings
    # (dots/numbers), and indices. Funds have no company-specific catalyst, so
    # they'd pollute coverage/recall.
    def _is_common(t: str) -> bool:
        return (
            bool(re.fullmatch(r"[A-Z]{1,5}", t))
            and not re.fullmatch(r"[A-Z]{4}X", t)
            and t not in _ETF_DENYLIST
        )

    ranked = [
        t for t, _ in sorted(caps.items(), key=lambda kv: kv[1], reverse=True)
        if _is_common(t)
    ]
    pool = ranked[: max(n * 8, 400)]
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool


def _recent_pairs(historical: Any) -> Tuple[List[float], List[date]]:
    """Mirror the collector: newest-first FMP history → chronological closes."""
    hist = _hist_list(historical)
    pairs: List[Tuple[date, float]] = []
    for p in hist[:200]:
        close = p.get("close")
        if close is None:
            continue
        try:
            d = date.fromisoformat((p.get("date") or "")[:10])
        except ValueError:
            continue
        pairs.append((d, float(close)))
    pairs.reverse()
    return [px for _, px in pairs], [d for d, _ in pairs]


async def _fmp_case(fmp: Any, ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch inputs, keep only big moves, run the FMP method. None = skip."""
    historical = await fmp.get_historical_prices(ticker)
    prices, dates = _recent_pairs(historical)
    if len(prices) < 40:
        return None
    vol = _compute_price_volatility(prices, dates)
    chosen_z = vol.get("chosen_z")
    if chosen_z is None or chosen_z < 1.0:
        return None  # not a big move — nothing to explain

    today = dates[-1]
    win_start = (today - timedelta(days=60)).isoformat()
    # FMP /stable has no working news-sentiment endpoint
    # (stock-news-sentiments-rss-feed → 404), so direction is derived from
    # catalyst-tag polarity + the move sign — not an FMP sentiment score.
    news, earnings = await asyncio.gather(
        fmp.get_stock_news(ticker, limit=250, from_date=win_start, to_date=today.isoformat()),
        fmp.get_historical_earnings_dates(ticker),
        return_exceptions=True,
    )
    news = news if isinstance(news, list) else []
    earnings = earnings if isinstance(earnings, list) else []

    pa = _build_price_action(prices, prices[-1], earnings, news, recent_price_dates=dates)
    event = pa.get("event")
    return {
        "ticker": ticker,
        "change_pct": pa["change_pct"],
        "window_label": pa["window_label"],
        "direction": pa["direction"],
        "tier": pa["tier"],
        "found": event is not None,
        "fmp_tag": event.get("tag") if event else None,
        "top_headlines": [h.get("title") for h in (pa.get("_news_headlines") or [])[:5]],
    }


def _direction_ok(case: Dict[str, Any]) -> Optional[bool]:
    """Does the catalyst's polarity match the move sign? None = N/A."""
    if not case["found"]:
        return None
    pol = _TAG_POLARITY.get(case["fmp_tag"] or "", 0)
    if pol == 0 or case["direction"] == "flat":
        return None
    return (pol > 0) == (case["direction"] == "up")


# ── Oracle + judge (Gemini) ───────────────────────────────────────────


def _parse_json(raw: str) -> Dict[str, Any]:
    """generate_json returns {'text': '<json string>', ...} — it does NOT
    parse. Parse it here, tolerating ```json fences / surrounding prose."""
    raw = raw or ""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


async def _oracle(gem: Any, case: Dict[str, Any], model: Optional[str]) -> Dict[str, Any]:
    prompt = (
        f"The stock {case['ticker']} moved {case['change_pct']:+.1f}% over "
        f"{case['window_label'].lower()}. What was the SPECIFIC catalyst or "
        "reason? Name the single most important driver and cite sources. If "
        "there was no clear company-specific catalyst (broad-market/sector "
        "drift only), say so explicitly."
    )
    # Use the method's default 8192 budget: 2.5-flash spends "thinking" tokens
    # against max_output_tokens, so a tight cap truncates the actual answer
    # (reason=MAX_TOKENS) and biases the judge. Cost is per *actual* token
    # generated, not the ceiling, so a higher cap is ~free here.
    res = await gem.generate_grounded_research(prompt, temperature=0.2, model_name=model)
    return {
        "text": (res or {}).get("text", ""),
        "sources": [s.get("uri") for s in (res or {}).get("grounding_sources", [])[:5]],
    }


_JUDGE_SYS = "You are a meticulous financial-research evaluator. Output STRICT JSON only."


async def _judge(gem: Any, case: Dict[str, Any], oracle: Dict[str, Any], model: Optional[str]) -> Dict[str, Any]:
    prompt = f"""A system explains why a stock moved using ONLY a free news feed (no web search).
Grade its answer against a web-search reference (treat the reference as best-effort truth).

MOVE: {case['ticker']} {case['change_pct']:+.1f}% over {case['window_label']}.

SYSTEM ANSWER (free-feed):
  catalyst: {case['fmp_tag'] or 'NONE — no catalyst found'}
  matched headlines: {case['top_headlines'] or 'none'}

WEB-SEARCH REFERENCE:
{(oracle.get('text') or '')[:1500]}
sources: {oracle.get('sources')}

Return ONLY this JSON:
{{
  "oracle_has_catalyst": true | false,
  "verdict": "Correct" | "Partial" | "Wrong" | "Missed",
  "winner": "system" | "reference" | "tie",
  "note": "one short sentence"
}}
Rules: "Missed" = system found nothing but the reference identified a clear catalyst.
"Wrong" = system named a different/contradicting cause. "Correct"/"Partial" = same driver
(Partial = right area but vaguer)."""
    res = await gem.generate_json(prompt, system_instruction=_JUDGE_SYS, model_name=model)
    return _parse_json((res or {}).get("text", ""))


# ── Reporting ─────────────────────────────────────────────────────────


def _pct(num: int, den: int) -> str:
    return f"{(100.0 * num / den):.0f}% ({num}/{den})" if den else "n/a (0)"


def _report(graded: List[Dict[str, Any]], args: argparse.Namespace) -> None:
    n = len(graded)
    found = [g for g in graded if g["found"]]
    dir_checked = [g for g in graded if _direction_ok(g) is not None]
    dir_ok = [g for g in dir_checked if _direction_ok(g)]

    print("\n" + "=" * 64)
    print(f"  PRICE-CATALYST EVAL — {n} big-move cases  (seed={args.seed})")
    print("=" * 64)
    print(f"  Coverage (found any catalyst) : {_pct(len(found), n)}")
    print(f"  Direction-agreement           : {_pct(len(dir_ok), len(dir_checked))}")

    if args.no_oracle:
        no_cat = [g for g in graded if not g["found"]]
        print(f"  No-catalyst on big move       : {_pct(len(no_cat), n)}")
        print("  (--no-oracle: skipped correctness/recall vs web search)")
        print("=" * 64 + "\n")
        return

    judged = [g for g in graded if isinstance(g.get("judge"), dict)]
    correctish = lambda g: g["judge"].get("verdict") in ("Correct", "Partial")
    prec_den = [g for g in judged if g["found"]]
    oracle_cat = [g for g in judged if g["judge"].get("oracle_has_catalyst")]
    wins = {"system": 0, "reference": 0, "tie": 0}
    for g in judged:
        wins[g["judge"].get("winner", "tie")] = wins.get(g["judge"].get("winner", "tie"), 0) + 1

    precision = sum(correctish(g) for g in prec_den)
    recall = sum(correctish(g) for g in oracle_cat)
    print(f"  Precision (found & correct)   : {_pct(precision, len(prec_den))}")
    print(f"  Recall vs oracle  *KEY*       : {_pct(recall, len(oracle_cat))}")
    print(f"  Head-to-head winner           : system={wins['system']} "
          f"reference={wins['reference']} tie={wins['tie']}")
    print(f"  Cost                          : FMP $0 | oracle ≈ ${_ORACLE_FEE_USD * len(judged):.2f}")

    rec = (recall / len(oracle_cat)) if oracle_cat else 0.0
    prec = (precision / len(prec_den)) if prec_den else 0.0
    if rec >= args.keep_threshold and prec >= 0.85:
        verdict = "KEEP FMP  (+ Gemini fallback only for 'Missed' cases)"
    elif rec < 0.60:
        verdict = "SWITCH primary to Gemini web-search (still gated to big moves)"
    else:
        verdict = "BORDERLINE — keep FMP, widen the Gemini fallback"
    print("-" * 64)
    print(f"  DECISION (recall>={args.keep_threshold:.0%} & prec>=85% → keep): {verdict}")
    print("=" * 64 + "\n")


def _write_json(graded: List[Dict[str, Any]], args: argparse.Namespace) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _OUT_DIR / f"eval_price_catalyst_{stamp}.json"
    path.write_text(json.dumps(graded, indent=2, default=str))
    print(f"  per-case detail → {path}")


# ── Main ──────────────────────────────────────────────────────────────


async def main(args: argparse.Namespace) -> None:
    fmp = get_fmp_client()
    gem = None if args.no_oracle else get_gemini_client()
    pool = _sample_tickers(args.n, args.seed)
    sem = asyncio.Semaphore(args.concurrency)

    async def find(t: str) -> Optional[Dict[str, Any]]:
        async with sem:
            try:
                return await _fmp_case(fmp, t)
            except Exception as e:  # noqa: BLE001 — eval tool, log and skip
                print(f"  ! {t}: {type(e).__name__}: {e}")
                return None

    cases: List[Dict[str, Any]] = []
    i = 0
    chunk = max(args.concurrency * 3, 9)
    while len(cases) < args.n and i < len(pool):
        batch = pool[i:i + chunk]
        i += len(batch)
        for c in await asyncio.gather(*[find(t) for t in batch]):
            if c:
                cases.append(c)
        print(f"  scanned {i}/{len(pool)} → {len(cases)}/{args.n} big-move cases")
    cases = cases[:args.n]
    if not cases:
        print("No big-move cases found — try a different --seed or larger --n.")
        return

    if args.no_oracle:
        _report(cases, args)
        return

    async def grade(c: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            # Offline eval → be patient. Retry transient Gemini failures
            # (503 "high demand" storms) with exponential backoff + jitter
            # before dropping the case. The integration also retries 2x
            # internally; this layers on top.
            for attempt in range(args.retries):
                try:
                    o = await _oracle(gem, c, args.model)
                    j = await _judge(gem, c, o, args.model)
                    return {**c, "oracle": o, "judge": j}
                except Exception as e:  # noqa: BLE001 — transient 503s etc.
                    if attempt == args.retries - 1:
                        print(f"  ! gave up {c['ticker']} after {args.retries}: {type(e).__name__}")
                        return {**c, "judge": None}
                    delay = min(60.0, args.retry_base * (2 ** attempt)) + random.uniform(0, 1.0)
                    await asyncio.sleep(delay)
            return {**c, "judge": None}

    graded = list(await asyncio.gather(*[grade(c) for c in cases]))
    _report(graded, args)
    _write_json(graded, args)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Eval the FMP price-catalyst detector vs a Gemini oracle.")
    p.add_argument("--n", type=int, default=60, help="number of big-move cases to grade")
    p.add_argument("--seed", type=int, default=7, help="sampling seed (for reproducible runs)")
    p.add_argument("--concurrency", type=int, default=5, help="max concurrent tickers")
    p.add_argument("--no-oracle", action="store_true", help="skip Gemini oracle+judge (no spend; proxies only)")
    p.add_argument("--keep-threshold", type=float, default=0.80, help="recall bar to keep FMP")
    p.add_argument("--retries", type=int, default=5, help="per-case oracle+judge retry attempts (503 resilience)")
    p.add_argument("--retry-base", type=float, default=3.0, help="base backoff seconds (exponential + jitter)")
    p.add_argument("--model", type=str, default=None,
                   help="override Gemini model for oracle+judge, e.g. gemini-2.0-flash, to dodge congestion")
    asyncio.run(main(p.parse_args()))
