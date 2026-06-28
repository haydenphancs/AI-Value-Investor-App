"""Validate the 4-persona quality score against REAL market data.

The persona weights (persona_scoring.PERSONA_WEIGHTS) were tuned by judgment. This
harness checks they're realistic: it scores a curated, archetype-labeled universe
through the EXACT production scoring path and asks whether the persona an investor-
archetype belongs to actually scores it relatively highest (NVDA -> Wood, KO ->
Buffett, etc.).

How it stays faithful to production
-----------------------------------
`TickerReportDataCollector._collect_fresh(ticker)` is persona-NEUTRAL and all
FMP/FRED (no Gemini). `assemble_report(out, {})` (empty AI dict) then builds the
real 10-vital `_scoring_inputs` deterministically. We re-run `compute_quality_score`
for all 4 personas on that one inputs dict. So we validate exactly what ships.

Three Gemini touchpoints inside `_collect_fresh` are monkeypatched to no-ops for the
DETERMINISTIC run (competitor intel, geopolitical precompute, moat grounded fallback).
For the `--spotcheck` names we leave the moat fallback ON (real Gemini) and diff the
Buffett/Ackman scores to size how much moat-grounding matters.

Important framing: the score is a quality-FIT (0-100), the same base re-weighted +
a bounded +/-10 style nudge. High-quality names score high for EVERYONE; the test is
RELATIVE ordering and factor tilts, not absolute level. No objective ground truth
exists -> this is face-validity + factor correlation + (weak) 13F membership.

Usage:
    cd backend && ./venv/bin/python -m scripts.validate_persona_scoring \
        --universe data/persona_validation_universe.json --concurrency 4 \
        --spotcheck NVDA,TSLA,KO,AAPL,GOOGL,BN,COST,HOOD,CMG,F

    # smoke test (2 names, no spend beyond FMP):
    ./venv/bin/python -m scripts.validate_persona_scoring --tickers NVDA,KO
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from dotenv import load_dotenv

load_dotenv(REPO / "backend" / ".env")

from app.services.agents.persona_scoring import compute_quality_score
from app.services.agents.ticker_report_data_collector import TickerReportDataCollector

_OUT_DIR = REPO / "backend" / "scripts" / "out"
_UNIVERSE = REPO / "backend" / "data" / "persona_validation_universe.json"

PERSONAS = ["warren_buffett", "cathie_wood", "peter_lynch", "bill_ackman", "michael_burry"]
SHORT = {"warren_buffett": "buffett", "cathie_wood": "wood",
         "peter_lynch": "lynch", "bill_ackman": "ackman", "michael_burry": "burry"}
VITALS = ["valuation", "moat", "financial_health", "profitability", "revenue",
          "insider", "macro", "forecast", "wall_street", "capital_allocation"]


# ── Gemini neutralization ─────────────────────────────────────────────

async def _noop_async(*a, **k):
    return None


def _patch_competitor_intel_off() -> None:
    """Module-level: competitor intel (lazy-imported inside _collect_fresh) -> []."""
    import app.services.competitor_intel_service as cis

    class _StubIntel:
        async def get_competitors(self, *a, **k):
            return []

    cis.get_competitor_intel_service = lambda: _StubIntel()


def _force_fresh_snapshots() -> None:
    """Force the 4 Fundamentals snapshot services to RECOMPUTE — skip the 24h Supabase
    `snapshot_cache` (and clear the in-memory tier) — so the validation reflects the
    CURRENT scoring code, not stale cached cards from a prior run. Without this, a
    growth/profitability/etc. scorer change is invisible end-to-end for up to 24h."""
    import app.services.growth_snapshot_service as gs
    import app.services.profitability_snapshot_service as ps
    import app.services.valuation_snapshot_service as vs
    import app.services.health_snapshot_service as hs
    for mod, cls in ((gs, gs.GrowthSnapshotService), (ps, ps.ProfitabilitySnapshotService),
                     (vs, vs.ValuationSnapshotService), (hs, hs.HealthSnapshotService)):
        cls._check_supabase_cache = lambda self, ticker: None   # type: ignore[assignment]
        if hasattr(mod, "_cache"):
            mod._cache.clear()


def _make_collector(*, moat_gemini: bool) -> TickerReportDataCollector:
    """Fresh collector with the 3 Gemini touchpoints neutralized. When
    moat_gemini=True we leave the moat grounded fallback ON (real Gemini) so the
    spot-check can isolate moat's effect."""
    c = TickerReportDataCollector()
    c._precompute_geopolitical = _noop_async          # type: ignore[assignment]
    if not moat_gemini:
        c._precompute_moat_grounded = _noop_async      # type: ignore[assignment]
    return c


# ── Per-ticker scoring ────────────────────────────────────────────────

def _vscore(si: Dict[str, Any], name: str) -> Optional[float]:
    """Raw 0-10 sub-score for a vital, or None when unmeasured/renormalized out."""
    v = si.get(name)
    if isinstance(v, dict):
        s = v.get("score")
        if isinstance(s, dict) and isinstance(s.get("value"), (int, float)):
            return round(float(s["value"]), 2)
    return None


async def _score(collector: TickerReportDataCollector, ticker: str) -> Dict[str, Any]:
    out = await collector._collect_fresh(ticker)
    report = collector.assemble_report(out, {})
    si = report.get("_scoring_inputs", {})
    scores = {pk: compute_quality_score(pk, {"_scoring_inputs": si}) for pk in PERSONAS}

    c = out.computed or {}
    fcf, mkt = c.get("fcf"), c.get("mkt_cap")
    fcf_yield = round(fcf / mkt, 4) if (fcf and mkt) else None
    moat10 = _vscore(si, "moat")

    rec: Dict[str, Any] = {
        "symbol": ticker,
        "sector": (out.profile or {}).get("sector") or c.get("sector"),
        "scores": {SHORT[p]: scores[p] for p in PERSONAS},
        "argmax": SHORT[max(scores, key=scores.get)],
        "vitals": {v: _vscore(si, v) for v in VITALS},
        "moat_measured": moat10 is not None,
        "factors": {
            "rev_growth_yoy": c.get("revenue_growth_yoy"),
            "fwd_rev_cagr": c.get("revenue_cagr"),
            "eps_cagr": c.get("eps_cagr"),
            "pe_ratio": c.get("pe_ratio"),
            "roe": c.get("roe"),
            "debt_equity": c.get("debt_equity"),
            "fcf_yield": fcf_yield,
            "upside_pct": c.get("upside_pct"),
        },
    }
    return rec


async def _bounded(sem: asyncio.Semaphore, collector, ticker, label) -> Dict[str, Any]:
    async with sem:
        try:
            rec = await _score(collector, ticker)
            sb = rec["scores"]
            print(f"  {label} {ticker:<6} buffett={sb['buffett']:>5} wood={sb['wood']:>5} "
                  f"lynch={sb['lynch']:>5} ackman={sb['ackman']:>5}  win={rec['argmax']}"
                  f"  moat={'Y' if rec['moat_measured'] else '-'}")
            return rec
        except Exception as e:  # noqa: BLE001 — one bad ticker must not kill the batch
            print(f"  {label} {ticker:<6} ERROR {type(e).__name__}: {e}")
            return {"symbol": ticker, "error": f"{type(e).__name__}: {e}"}


# ── Lightweight stats (no scipy dependency) ───────────────────────────

def _rank(xs: List[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: List[Optional[float]], ys: List[Optional[float]]) -> Optional[Tuple[float, int]]:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 5:
        return None
    rx, ry = _rank([p[0] for p in pairs]), _rank([p[1] for p in pairs])
    n = len(pairs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    if vx == 0 or vy == 0:
        return None
    return round(cov / (vx * vy), 2), n


# ── Analysis / reporting ──────────────────────────────────────────────

def _analyze(rows: List[Dict[str, Any]], labels: Dict[str, Dict[str, Any]]) -> None:
    ok = [r for r in rows if "error" not in r]
    print("\n" + "=" * 78)
    print(f"ANALYSIS — {len(ok)} scored, {len(rows) - len(ok)} failed")
    print("=" * 78)

    # 1) Face validity per bucket: does expected_top win / expected_not lose?
    print("\n[1] FACE VALIDITY by bucket (mean persona score; ✓ if expectation holds)")
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for r in ok:
        b = labels.get(r["symbol"], {}).get("bucket", "?")
        buckets.setdefault(b, []).append(r)
    for b in sorted(buckets):
        rs = buckets[b]
        means = {p: round(sum(r["scores"][p] for r in rs) / len(rs), 1) for p in SHORT.values()}
        exp_top = labels.get(rs[0]["symbol"], {}).get("expected_top")
        exp_not = labels.get(rs[0]["symbol"], {}).get("expected_not")
        et = SHORT.get(exp_top) if exp_top else None
        en = SHORT.get(exp_not) if exp_not else None
        order = sorted(means, key=means.get, reverse=True)
        tag = []
        if et:
            tag.append(("✓" if order[0] == et else "✗") + f"top={et}")
        if en:
            tag.append(("✓" if order[-1] == en else "✗") + f"not={en}")
        print(f"  {b:<24} n={len(rs):<2} " +
              " ".join(f"{p}={means[p]:>5}" for p in ["buffett", "wood", "lynch", "ackman"]) +
              "   " + " ".join(tag))

    # 2) Cross-sectional factor correlations (Spearman) — expected signs.
    print("\n[2] FACTOR CORRELATION (Spearman persona-score vs factor; expected sign in [])")
    checks = [
        ("wood", "fwd_rev_cagr", "+"), ("wood", "rev_growth_yoy", "+"),
        ("buffett", "roe", "+"), ("buffett", "debt_equity", "-"),
        ("lynch", "peg", "-"),
        ("ackman", "fcf_yield", "+"),
    ]
    for persona, factor, want in checks:
        xs = [r["scores"][persona] for r in ok]
        if factor == "peg":
            ys = []
            for r in ok:
                pe = r["factors"].get("pe_ratio")
                g = max([v for v in (r["factors"].get("eps_cagr"),
                                     r["factors"].get("fwd_rev_cagr"),
                                     r["factors"].get("rev_growth_yoy")) if v is not None] + [0])
                ys.append(pe / g if (pe and pe > 0 and g > 0) else None)
        else:
            ys = [r["factors"].get(factor) for r in ok]
        res = _spearman(xs, ys)
        if res is None:
            print(f"  {persona:<8} vs {factor:<14} [{want}]  insufficient data")
            continue
        rho, n = res
        good = (rho > 0.15 if want == "+" else rho < -0.15)
        print(f"  {persona:<8} vs {factor:<14} [{want}]  rho={rho:>6}  n={n:<3} "
              f"{'✓' if good else '·'}")

    # 3) Marquee headline table.
    print("\n[3] HEADLINE TABLE (all 4 persona scores; bold-ish = winner)")
    print(f"  {'ticker':<7}{'bucket':<22}{'buffett':>8}{'wood':>7}{'lynch':>7}{'ackman':>7}  win")
    for r in ok:
        s = r["scores"]
        b = labels.get(r["symbol"], {}).get("bucket", "?")
        print(f"  {r['symbol']:<7}{b:<22}{s['buffett']:>8}{s['wood']:>7}"
              f"{s['lynch']:>7}{s['ackman']:>7}  {r['argmax']}")

    # 4) Anomalies — expectation contradicted.
    print("\n[4] ANOMALIES (expected_top didn't win OR expected_not won)")
    anomalies = []
    for r in ok:
        lab = labels.get(r["symbol"], {})
        et, en = lab.get("expected_top"), lab.get("expected_not")
        win_full = max(r["scores"], key=r["scores"].get)  # short name
        win = win_full
        if et and SHORT.get(et) != win:
            anomalies.append(f"  {r['symbol']:<6} expected top={SHORT[et]} but won={win}")
        if en and SHORT.get(en) == win:
            anomalies.append(f"  {r['symbol']:<6} expected NOT {SHORT[en]} but it WON")
    print("\n".join(anomalies) if anomalies else "  none — every labeled expectation held")


# ── Driver ────────────────────────────────────────────────────────────

def _load_universe(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text())
    return data["tickers"] if isinstance(data, dict) else data


async def main(args: argparse.Namespace) -> None:
    _patch_competitor_intel_off()
    if not args.use_cache:
        _force_fresh_snapshots()
        print("(snapshot caches force-missed — scoring reflects current code)")
    if args.tickers:
        entries = [{"ticker": t.strip().upper(), "bucket": "adhoc",
                    "expected_top": None, "expected_not": None}
                   for t in args.tickers.split(",") if t.strip()]
    else:
        entries = _load_universe(Path(args.universe))
    labels = {e["ticker"]: e for e in entries}
    tickers = [e["ticker"] for e in entries]
    spot = [t.strip().upper() for t in (args.spotcheck or "").split(",") if t.strip()]

    print(f"Scoring {len(tickers)} tickers (deterministic, no Gemini), concurrency={args.concurrency}")
    det = _make_collector(moat_gemini=False)
    sem = asyncio.Semaphore(args.concurrency)
    rows = await asyncio.gather(*[_bounded(sem, det, t, "det") for t in tickers])

    spot_rows: List[Dict[str, Any]] = []
    if spot:
        print(f"\nSpot-check {len(spot)} names WITH Gemini moat grounding (sequential)")
        sc = _make_collector(moat_gemini=True)
        for t in spot:
            spot_rows.append(await _bounded(asyncio.Semaphore(1), sc, t, "gem"))

    _analyze(rows, labels)

    if spot_rows:
        print("\n[5] MOAT SPOT-CHECK (Gemini moat vs deterministic — Buffett/Ackman delta)")
        det_by = {r["symbol"]: r for r in rows if "error" not in r}
        for sr in spot_rows:
            if "error" in sr:
                continue
            dr = det_by.get(sr["symbol"])
            if not dr:
                continue
            db, da = dr["scores"]["buffett"], dr["scores"]["ackman"]
            gb, ga = sr["scores"]["buffett"], sr["scores"]["ackman"]
            print(f"  {sr['symbol']:<6} buffett {db:>5}->{gb:<5} (Δ{round(gb - db, 1):>+5})   "
                  f"ackman {da:>5}->{ga:<5} (Δ{round(ga - da, 1):>+5})   "
                  f"moat {'Y' if sr['moat_measured'] else '-'}")

    # Persist raw output.
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_json = _OUT_DIR / f"validate_persona_scoring_{stamp}.json"
    out_csv = _OUT_DIR / f"validate_persona_scoring_{stamp}.csv"
    out_json.write_text(json.dumps(
        {"deterministic": rows, "spotcheck": spot_rows, "labels": labels}, indent=2, default=str))

    with out_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "bucket", "expected_top", "expected_not", "sector",
                    *(f"score_{p}" for p in SHORT.values()), "argmax",
                    *(f"v_{v}" for v in VITALS), "moat_measured",
                    "rev_growth_yoy", "fwd_rev_cagr", "pe_ratio", "roe",
                    "debt_equity", "fcf_yield", "upside_pct", "error"])
        for r in rows:
            lab = labels.get(r["symbol"], {})
            if "error" in r:
                w.writerow([r["symbol"], lab.get("bucket"), lab.get("expected_top"),
                            lab.get("expected_not"), "", *[""] * (5 + 10), "", *[""] * 7, r["error"]])
                continue
            s, v, fa = r["scores"], r["vitals"], r["factors"]
            w.writerow([r["symbol"], lab.get("bucket"), lab.get("expected_top"),
                        lab.get("expected_not"), r["sector"],
                        *(s[p] for p in SHORT.values()), r["argmax"],
                        *(v[k] for k in VITALS), r["moat_measured"],
                        fa["rev_growth_yoy"], fa["fwd_rev_cagr"], fa["pe_ratio"], fa["roe"],
                        fa["debt_equity"], fa["fcf_yield"], fa["upside_pct"], ""])

    print(f"\nWrote:\n  {out_json}\n  {out_csv}")


def _parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Validate persona scoring against real data.")
    ap.add_argument("--universe", default=str(_UNIVERSE))
    ap.add_argument("--tickers", default=None, help="CSV override; skips the universe file")
    ap.add_argument("--spotcheck", default=None, help="CSV of names to re-run WITH Gemini moat")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--use-cache", action="store_true",
                    help="allow the 24h snapshot cache (default: force-fresh so results reflect current code)")
    return ap.parse_args()


if __name__ == "__main__":
    asyncio.run(main(_parse()))
