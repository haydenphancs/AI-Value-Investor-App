"""
PILOT — TTM (trailing-twelve-month) industry-median benchmarks.

Standalone validation tool (does NOT touch the production benchmark pipeline).
For each industry in `benchmark_universe.json`, fetch every constituent's TTM
ratios (FMP /ratios-ttm + /key-metrics-ttm — the same TTM the company card shows,
so company-vs-peer is apples-to-apples), apply the SAME filters as the live
benchmark job (positive-only + caps for the multiples, MIN_SAMPLE_SIZE), and
print the median per metric. Every company contributes a complete rolling 12
months as of its latest filing, so there is no partial-fiscal-year spike.

Usage (from backend/):
    source .env && python -m scripts.pilot_ttm_benchmark
    source .env && python -m scripts.pilot_ttm_benchmark --industry "Semiconductors"
"""

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.integrations.fmp import get_fmp_client

_UNIVERSE = Path(__file__).resolve().parent.parent / "data" / "benchmark_universe.json"
MIN_SAMPLE_SIZE = 5
_CONCURRENCY = 12

# metric -> (source, field, positive_only, cap, trim_above)  ── mirrors live METRIC_CONFIGS
# source: "r" = ratios-ttm, "k" = key-metrics-ttm
#   cap        = OLD rule (winsorize ceiling — clamp to this value)
#   trim_above = NEW rule (drop values strictly above this — near-zero-denominator
#                artifacts are excluded, not clamped). None = no trim.
# Trim only the EARNINGS/CASH-FLOW multiples + interest coverage (denominators that
# can be near-zero → absurd ratios). P/S, P/B, margins, balance-sheet ratios are
# left untrimmed: their denominators (sales, book, assets) are rarely near zero,
# so their medians are real, not artifacts.
_METRICS = [
    # name                src  field                            pos    cap    trim
    ("pe_ratio",          "r", "priceToEarningsRatioTTM",      True,  200.0, 100.0),
    ("ps_ratio",          "r", "priceToSalesRatioTTM",         True,  200.0, None),
    ("pb_ratio",          "r", "priceToBookRatioTTM",          True,  200.0, None),
    ("pfcf_ratio",        "r", "priceToFreeCashFlowRatioTTM",  True,  None,  100.0),
    ("ev_ebitda",         "k", "evToEBITDATTM",                True,  None,  75.0),
    ("earnings_yield",    "k", "earningsYieldTTM",             False, None,  None),
    ("dividend_yield",    "r", "dividendYieldTTM",             False, None,  None),
    ("gross_margin",      "r", "grossProfitMarginTTM",         False, None,  None),
    ("operating_margin",  "r", "operatingProfitMarginTTM",     False, None,  None),
    ("net_margin",        "r", "netProfitMarginTTM",           False, None,  None),
    ("interest_coverage", "r", "interestCoverageRatioTTM",     True,  100.0, 100.0),
    ("current_ratio",     "r", "currentRatioTTM",              False, None,  None),
    ("quick_ratio",       "r", "quickRatioTTM",                False, None,  None),
    ("debt_to_equity",    "r", "debtToEquityRatioTTM",         False, None,  None),
]


def _num(d: Dict[str, Any], field: str) -> Optional[float]:
    v = d.get(field)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


async def _fetch_ticker(fmp, sem, ticker: str) -> Dict[str, Optional[float]]:
    """One ticker's TTM metric values (raw, pre-filter)."""
    async with sem:
        try:
            r, k = await asyncio.gather(
                fmp.get_ratios_ttm(ticker),
                fmp.get_key_metrics_ttm(ticker),
                return_exceptions=True,
            )
        except Exception:
            return {}
    r0 = (r[0] if isinstance(r, list) and r else {}) or {}
    k0 = (k[0] if isinstance(k, list) and k else {}) or {}
    out: Dict[str, Optional[float]] = {}
    for name, src, field, _pos, _cap, _trim in _METRICS:
        out[name] = _num(r0 if src == "r" else k0, field)
    return out


def _capped_median(raw, positive_only, cap):
    """OLD rule: positive-only + clamp to cap (winsorize ceiling)."""
    vals = [v for v in raw if v is not None]
    if positive_only:
        vals = [v for v in vals if v > 0]
    if cap is not None:
        vals = [min(v, cap) for v in vals]
    if len(vals) < MIN_SAMPLE_SIZE:
        return None, len(vals)
    return round(statistics.median(vals), 4), len(vals)


def _trimmed_median(raw, positive_only, trim_above):
    """NEW rule: positive-only + DROP values above trim_above (exclude artifacts)."""
    vals = [v for v in raw if v is not None]
    if positive_only:
        vals = [v for v in vals if v > 0]
    if trim_above is not None:
        vals = [v for v in vals if v <= trim_above]
    if len(vals) < MIN_SAMPLE_SIZE:
        return None, len(vals)
    return round(statistics.median(vals), 4), len(vals)


async def run_industry(fmp, sem, name: str, tickers: List[str]) -> None:
    rows = await asyncio.gather(*[_fetch_ticker(fmp, sem, t) for t in tickers])
    print(f"\n{'='*72}\n{name}   ({len(tickers)} constituents)\n{'='*72}")
    print(f"  {'metric':20s} {'OLD capped':>12s} {'NEW trimmed':>14s} {'n':>5s}  {'dropped':>8s}")
    print("  " + "-" * 64)
    for name_m, _src, _field, pos, cap, trim in _METRICS:
        col = [r.get(name_m) for r in rows]
        old_med, old_n = _capped_median(col, pos, cap)
        new_med, new_n = _trimmed_median(col, pos, trim)
        old_s = "—" if old_med is None else f"{old_med:.4g}"
        new_s = "—" if new_med is None else f"{new_med:.4g}"
        dropped = (old_n - new_n) if trim is not None else 0
        drop_s = f"-{dropped}" if dropped else ""
        print(f"  {name_m:20s} {old_s:>12s} {new_s:>14s} {new_n:>5d}  {drop_s:>8s}")


async def main(industries: List[str]) -> None:
    uni = json.loads(_UNIVERSE.read_text())
    by_name = {e["industry"]: e for e in uni["industries"]}
    fmp = get_fmp_client()
    sem = asyncio.Semaphore(_CONCURRENCY)
    try:
        for ind in industries:
            entry = by_name.get(ind)
            if not entry:
                print(f"!! industry not in universe: {ind!r}")
                continue
            await run_industry(fmp, sem, ind, entry["tickers"])
    finally:
        await fmp.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Pilot TTM industry-median benchmarks")
    p.add_argument(
        "--industry", action="append",
        help="Industry name (repeatable). Default: 2 pilot industries.",
    )
    args = p.parse_args()
    inds = args.industry or ["Software - Infrastructure", "Semiconductors"]
    asyncio.run(main(inds))
