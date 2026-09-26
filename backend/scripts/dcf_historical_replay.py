"""Phase-2 HISTORICAL REPLAY of the Caydex Fair Value Estimate (model dcf-v1) — READ-ONLY.

Values every ticker in the universe at every quarter-end since START, as of that date, using only
what was public then: statements with filingDate ≤ date, the split-adjusted close and market cap
on that date, the FRED 10-year history up to that date, and a beta regressed on the 60 months of
prices before that date. The model is the production one: app.services.dcf_fair_value_service
(build_inputs + value_company), unmodified.

⚠️ OTHER POINT-IN-TIME LEAKS (so the replay is not strictly point-in-time): sector / industry /
listing currency come from TODAY's profile; FMP restates past statements (share counts are
split-adjusted after the fact); and beta is this script's own 60-month regression on SPY, not the
FMP beta production reads.

⚠️ PERFECT-FORESIGHT CONSENSUS. FMP keeps one analyst-consensus value per fiscal year, recorded
near that year's report (AAPL FY2021 consensus EPS 5.61 vs actual 5.61). A 2018 valuation
therefore "forecasts" with numbers analysts only reached in 2019-2023. This replay proves the
MACHINERY; it cannot prove the estimate predicts returns, and no hit rate may ever be quoted
from it. FMP's historical analyst COUNTS are also sparse, so the machinery pass sets the analyst
floor to 1 and reports the real-floor refusal rate separately.

PASS CRITERIA — frozen 2026-09-25, before the first run (documents/research/dcf-fair-value.md §9):
  P1  No exception; every "ok" value finite and > 0; every refusal carries a known code.
  P2  Stability. Between consecutive quarter-ends, each change is attributed to input groups
      (time, annual [reported years + consensus], shares_debt, rates, beta) by reverting one
      group at a time. Pass when:
      (a) the TIME contribution lies in [−5 %, +6 %] for ≥ 95 % of steps (no timing sawtooth),
      (b) the interaction residual is < 2 % of value for ≥ 95 % of steps.
  P3  Refusals fire where they should: F and GM refused at every date (captive_finance);
      HON refused at its 2025-26 dates and GE at its 2023-24 dates (company_changed_shape, or
      another refusal); ORCL refused at its latest date.
  P4  Splits do not break per-share values: at AAPL 2020, NVDA 2021 and 2024, TSLA 2020 and
      2022, GOOGL 2022, AMZN 2022, value ÷ price does not move by more than 2× across the split
      quarter. ⚠️ Review finding: FMP restates both prices and share counts after a split, so
      this is only a CONSISTENCY check on restated data — it cannot fail on the real bug (a
      pre-split count against a post-split price). That case is pinned by unit tests instead
      (share_count_conflict, `test_a_five_for_four_split_is_caught`).
  P5  Consensus-vs-actual error per sector, for the displayed range width. (Expected to be NOT
      MEASURABLE from FMP, whose past consensus is recorded at report time; the report says so.)

Also reported, NOT pass criteria (the model is never tuned toward market price): value ÷ price
by year, for the spec (5-year-average rate) and the research variant (3-month rate).

Run from backend/:
    ./venv/bin/python scripts/dcf_historical_replay.py --out /path/to/replay.md
    ./venv/bin/python scripts/dcf_historical_replay.py --tickers AAPL MSFT --out /tmp/r.md
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, ".")

import app.services.dcf_fair_value_service as dcf  # noqa: E402
from app.integrations.fmp import get_fmp_client  # noqa: E402
from app.integrations.fred import get_fred_client  # noqa: E402

START = date(2016, 3, 31)

UNIVERSE = (
    # the 2026-09-25 sweep set
    "AAPL MSFT NVDA TER ORCL PLUG JPM O CRM GOOGL V CBRE AMZN META TSLA AVGO ADBE KO PG WMT COST "
    "PEP JNJ PFE UNH LLY MRNA XOM CVX CAT DE GE HON NEE DUK VZ T HD NKE MCD SBUX MA BAC GS PGR "
    "BRK-B PLD AMT F GM SNOW RIVN DOW CROX "
    # broader large and mid caps
    "ADP ACN IBM INTC AMD QCOM TXN CSCO ORLY AZO LOW TGT TJX BKNG MAR CMG YUM DPZ EL CL KMB GIS "
    "MDLZ HSY ABT TMO DHR MRK ABBV AMGN GILD ISRG SYK MMM UPS FDX LMT RTX NOC UNP CSX WM SHW ECL "
    "APD LIN NFLX DIS CMCSA TMUS INTU NOW PANW SPGI MCO ICE CME MSCI EFX XYL MRVL SHOP ROST"
).split()

SPLITS = [("AAPL", date(2020, 8, 31)), ("NVDA", date(2021, 7, 20)), ("NVDA", date(2024, 6, 10)),
          ("TSLA", date(2020, 8, 31)), ("TSLA", date(2022, 8, 25)), ("GOOGL", date(2022, 7, 18)),
          ("AMZN", date(2022, 6, 6))]

# "annual" = the reported fiscal years AND the consensus rows: they roll together at each 10-K
# (swapping one without the other misaligns the window and the hybrid refuses).
GROUPS = ("time", "annual", "shares_debt", "rates", "beta")


def quarter_ends(start: date, end: date) -> List[date]:
    out, y, m = [], start.year, start.month
    while True:
        d = date(y, m, 31 if m in (3, 12) else 30)
        if d > end:
            break
        if d >= start:
            out.append(d)
        m += 3
        if m > 12:
            m, y = m - 12, y + 1
    out.append(end)
    return out


def _series(rows: Any, field: str) -> List[Tuple[date, float]]:
    rows = rows if isinstance(rows, list) else (rows or {}).get("historical", [])
    out = []
    for r in rows:
        d, v = dcf._parse_date(r.get("date")), dcf._num(r.get(field))
        if d is not None and v is not None and v > 0:
            out.append((d, v))
    return sorted(out)


def _at(series: List[Tuple[date, float]], d: date) -> Optional[float]:
    """Last value on or before d (≤ 7 days stale)."""
    lo, hi = 0, len(series)
    while lo < hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= d:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    dd, v = series[lo - 1]
    return v if (d - dd).days <= 7 else None


def _month_ends(series: List[Tuple[date, float]]) -> Dict[Tuple[int, int], float]:
    out: Dict[Tuple[int, int], float] = {}
    for d, v in series:
        out[(d.year, d.month)] = v          # sorted ascending → last close of the month wins
    return out


def beta_at(stock: Dict[Tuple[int, int], float], market: Dict[Tuple[int, int], float],
            d: date) -> Optional[float]:
    """60-month regression beta on monthly returns ending the month before d (≥ 36 months)."""
    months = []
    y, m = d.year, d.month
    for _ in range(61):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        months.append((y, m))
    months.reverse()
    rs, rm = [], []
    for a, b in zip(months, months[1:]):
        if a in stock and b in stock and a in market and b in market:
            rs.append(stock[b] / stock[a] - 1)
            rm.append(market[b] / market[a] - 1)
    if len(rs) < 36:
        return None
    mean_s, mean_m = statistics.fmean(rs), statistics.fmean(rm)
    cov = sum((x - mean_s) * (z - mean_m) for x, z in zip(rs, rm))
    var = sum((z - mean_m) ** 2 for z in rm)
    return cov / var if var > 0 else None


async def fetch(ticker: str, fmp, sem: asyncio.Semaphore) -> Dict[str, Any]:
    async with sem:
        today = date.today().isoformat()
        names = ("profile", "income_annual", "income_quarter", "cash_flow_annual",
                 "balance_annual", "estimates", "prices", "mcap")
        res = await asyncio.gather(
            fmp.get_company_profile(ticker),
            fmp.get_income_statement(ticker, period="annual", limit=20),
            fmp.get_income_statement(ticker, period="quarter", limit=80),
            fmp.get_cash_flow_statement(ticker, period="annual", limit=20),
            fmp.get_balance_sheet(ticker, period="annual", limit=20),
            fmp.get_analyst_estimates(ticker, period="annual", limit=40),
            fmp.get_historical_prices(ticker, "2010-01-01", today),
            fmp.get_historical_market_cap(ticker, "2015-01-01", today, limit=5000),
            return_exceptions=True,
        )
        return dict(zip(names, res))


def _hybrid(new: dcf.DcfInputs, old: dcf.DcfInputs, group: str) -> dcf.DcfInputs:
    h = copy.copy(new)
    h.notes = []
    if group == "time":
        h.as_of = old.as_of
    elif group == "annual":
        h.history, h.consensus = old.history, old.consensus
        h.statement_currency = old.statement_currency
    elif group == "shares_debt":
        h.shares_diluted, h.total_debt = old.shares_diluted, old.total_debt
    elif group == "rates":
        h.rf_avg_pct, h.rf_recent_pct = old.rf_avg_pct, old.rf_recent_pct
    elif group == "beta":
        h.beta_raw = old.beta_raw
    return h


def _v(inp: dcf.DcfInputs, **kw) -> Optional[float]:
    r = dcf.value_company(copy.copy(inp), **kw)
    return r.fair_value if r.status == "ok" else None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--out", required=True)
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()
    tickers = args.tickers or list(UNIVERSE)

    fmp, fred = get_fmp_client(), get_fred_client()
    dgs10 = await fred.get_observations("DGS10", limit=6000)
    spy = await fmp.get_historical_prices("SPY", "2010-01-01", date.today().isoformat())
    market = _month_ends(_series(spy, "close"))
    sem = asyncio.Semaphore(args.concurrency)
    raw = await asyncio.gather(*(fetch(t, fmp, sem) for t in tickers))
    dates = quarter_ends(START, date.today())

    real_floor = dcf.MIN_ANALYSTS
    rows: List[Dict[str, Any]] = []
    crashes: List[str] = []
    fetch_fail: Dict[str, List[str]] = {}
    steps: List[Dict[str, Any]] = []
    for t, p in zip(tickers, raw):
        bad = [k for k, v in p.items() if isinstance(v, BaseException)]
        if bad:
            fetch_fail[t] = bad
            continue
        prices = _series(p["prices"], "close")
        mcaps = _series(p["mcap"], "marketCap")
        stock_m = _month_ends(prices)
        prof = p["profile"][0] if isinstance(p["profile"], list) and p["profile"] else p["profile"]
        prev: Optional[dcf.DcfInputs] = None
        prev_val: Optional[float] = None
        for d in dates:
            try:
                price, mcap = _at(prices, d), _at(mcaps, d)
                beta = beta_at(stock_m, market, d)
                profile = {**(prof or {}), "beta": beta, "price": price, "marketCap": mcap}
                inp = dcf.build_inputs(
                    t, d, profile=profile, income_annual=p["income_annual"],
                    income_quarter=p["income_quarter"], cash_flow_annual=p["cash_flow_annual"],
                    balance_annual=p["balance_annual"], estimates=p["estimates"],
                    rf_avg_pct=dcf.rf_average_pct(dgs10, d), rf_recent_pct=dcf.rf_recent_pct(dgs10, d),
                    price=price, market_cap=mcap,
                )
                dcf.MIN_ANALYSTS = real_floor
                real = dcf.value_company(copy.copy(inp))
                dcf.MIN_ANALYSTS = 1            # machinery pass (see module docstring)
                res = dcf.value_company(copy.copy(inp))
                recent = dcf.value_company(copy.copy(inp), rate_basis="recent")
                avg5 = dcf.value_company(copy.copy(inp), rate_basis="avg5")
                ok = res.status == "ok"
                if ok and not (math.isfinite(res.fair_value) and res.fair_value > 0):
                    crashes.append(f"{t} {d}: invalid value {res.fair_value}")
                if not ok and res.refusal_code not in dcf.REFUSAL_REASONS:
                    crashes.append(f"{t} {d}: unknown refusal {res.refusal_code}")
                rows.append({
                    "ticker": t, "date": d.isoformat(), "price": price,
                    "status": res.status, "code": res.refusal_code,
                    "real_floor_code": real.refusal_code,
                    "value": res.fair_value, "low": res.range_low, "high": res.range_high,
                    "recent_value": recent.fair_value if recent.status == "ok" else None,
                    "avg5_value": avg5.fair_value if avg5.status == "ok" else None,
                    "beta": beta, "rf": inp.rf_avg_pct, "rf_recent": inp.rf_recent_pct,
                    "sector": inp.sector,
                })
                if ok and prev is not None and prev_val is not None:
                    total = res.fair_value / prev_val - 1
                    contrib: Dict[str, Optional[float]] = {}
                    for g in GROUPS:
                        hv = _v(_hybrid(inp, prev, g))
                        contrib[g] = None if hv is None else res.fair_value / hv - 1
                    known = [c for c in contrib.values() if c is not None]
                    residual = total - sum(known) if len(known) == len(GROUPS) else None
                    filed = inp.last_fy_end != prev.last_fy_end   # a new annual report went public
                    fy_ends = {y.end for y in inp.history} | {c.end for c in inp.consensus}
                    rolled = any(prev.as_of < e <= inp.as_of for e in fy_ends)
                    steps.append({"ticker": t, "date": d.isoformat(), "total": total,
                                  "residual": residual, "annual_filing": filed,
                                  "fy_roll": rolled, **contrib})
                prev, prev_val = (inp, res.fair_value) if ok else (None, None)
            except Exception as e:                              # P1 counts these
                crashes.append(f"{t} {d}: {type(e).__name__}: {e}")
                prev, prev_val = None, None
    dcf.MIN_ANALYSTS = real_floor

    # ── evaluate ──
    out: List[str] = ["# Caydex Fair Value Estimate — historical replay (model dcf-v1)", ""]
    out.append(f"Run {date.today().isoformat()} · {len(tickers)} tickers · {len(dates)} dates "
               f"({dates[0]} → {dates[-1]}) · {len(rows)} valuations · machinery pass uses "
               f"analyst floor 1 (FMP's historical counts are sparse).")
    if fetch_fail:
        out.append(f"Fetch failures (excluded): {fetch_fail}")
    out.append("")

    p1 = not crashes
    out.append(f"## P1 — no crashes, valid values, known codes: {'PASS' if p1 else 'FAIL'}")
    out += [f"- {c}" for c in crashes[:40]]
    out.append("")

    tim = [s["time"] for s in steps if s["time"] is not None]
    res_ = [abs(s["residual"]) for s in steps if s["residual"] is not None]
    tim_ok = sum(1 for x in tim if -0.05 <= x <= 0.06) / len(tim) if tim else 0
    res_ok = sum(1 for x in res_ if x < 0.02) / len(res_) if res_ else 0
    p2 = tim_ok >= 0.95 and res_ok >= 0.95
    out.append(f"## P2 — stability: {'PASS' if p2 else 'FAIL'}")
    out.append(f"{len(steps)} consecutive-quarter steps. Time contribution within [−5 %, +6 %]: "
               f"{tim_ok:.1%}; |residual| < 2 %: {res_ok:.1%}.")

    def _pct(xs: List[float]) -> str:
        if not xs:
            return "—"
        xs = sorted(xs)
        q = lambda f: xs[min(len(xs) - 1, int(f * len(xs)))]  # noqa: E731
        return f"median {statistics.median(xs):+.1%} · p5 {q(0.05):+.1%} · p95 {q(0.95):+.1%}"

    pure = [s["time"] for s in steps if s["time"] is not None and not s["fy_roll"]]
    roll = [s["time"] for s in steps if s["time"] is not None and s["fy_roll"]]
    pure_ok = sum(1 for x in pure if -0.05 <= x <= 0.06) / len(pure) if pure else 0
    out.append("")
    out.append(f"Breakdown (diagnostic, added after the first run — the frozen criterion above is "
               f"unchanged): time contribution in quarters with NO fiscal-year-end ({len(pure)} "
               f"steps) within [−5 %, +6 %]: {pure_ok:.1%}. In quarters where the forecast window "
               f"rolled at a fiscal year-end ({len(roll)} steps): {_pct(roll)}. A roll replaces "
               f"an extrapolated year with a consensus year — here FMP's PAST per-year consensus, "
               f"which is erratic (see the research doc). Since v1 crossfades the roll over the 91 "
               f"days AFTER a year-end, most of the roll's move now lands in the following quarter, "
               f"i.e. in the \"no fiscal-year-end\" group above; the day-to-day diagnostic below is "
               f"the measure a user experiences.")

    recent_roll = [s_["time"] for s_ in steps if s_["time"] is not None and s_["fy_roll"]
                   and s_["date"] >= "2025-01-01"]
    if recent_roll:
        miss = sum(1 for x in recent_roll if not (-0.05 <= x <= 0.06))
        out.append(f"Roll quarters since 2025 (the most current consensus FMP has): {miss} of "
                   f"{len(recent_roll)} outside [−5 %, +6 %] — {_pct(recent_roll)}.")

    out.append("")
    out.append("| Contribution per quarter | distribution |")
    out.append("|---|---|")
    out.append(f"| total change | {_pct([s['total'] for s in steps])} |")
    for g in GROUPS:
        out.append(f"| {g} | {_pct([s[g] for s in steps if s[g] is not None])} |")
    out.append(f"| annual, quarters with a new annual report | "
               f"{_pct([s['annual'] for s in steps if s['annual_filing'] and s['annual'] is not None])} |")
    full = sum(1 for s in steps if s["residual"] is not None)
    out.append(f"\nAttribution complete (every hybrid valued) on {full} of {len(steps)} steps; the "
               f"rest had a group whose old value alone triggers a refusal.")
    big = sorted(steps, key=lambda s: -abs(s["total"]))[:15]
    out.append("")
    out.append("Largest quarterly moves (and the input group behind each):")
    out.append("")
    out.append("| Ticker | Date | Change | time | annual | shares/debt | rates | beta | residual |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    f = lambda v: "—" if v is None else f"{v:+.0%}"  # noqa: E731
    for s in big:
        out.append(f"| {s['ticker']} | {s['date']} | {s['total']:+.0%} | {f(s['time'])} | "
                   f"{f(s['annual'])} | {f(s['shares_debt'])} | {f(s['rates'])} | {f(s['beta'])} | "
                   f"{f(s['residual'])} |")
    out.append("")

    by_t: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_t[r["ticker"]].append(r)
    checks = []
    for t in ("F", "GM"):
        rs = by_t.get(t, [])
        codes = sorted({r["code"] for r in rs if r["code"]})
        # "refused at every date" is the criterion; which rule refused an individual date is
        # reported (a share-count conflict runs before captive_finance in the spec order).
        checks.append((f"{t} refused at every date (codes: {', '.join(codes)})",
                       bool(rs) and all(r["status"] == "refused" for r in rs)))
    hon = [r for r in by_t.get("HON", []) if r["date"] >= "2025-06"]
    checks.append(("HON refused at 2025-26 dates", bool(hon) and all(r["status"] == "refused" for r in hon)))
    ge = [r for r in by_t.get("GE", []) if "2023-03" <= r["date"] <= "2024-12-31"]
    checks.append(("GE refused at 2023-24 dates", bool(ge) and all(r["status"] == "refused" for r in ge)))
    orcl = by_t.get("ORCL", [])
    checks.append(("ORCL refused at its latest date", bool(orcl) and orcl[-1]["status"] == "refused"))
    p3 = all(ok for _, ok in checks)
    out.append(f"## P3 — expected refusals: {'PASS' if p3 else 'FAIL'}")
    for name, ok in checks:
        out.append(f"- {'✅' if ok else '❌'} {name}")
    for t in ("HON", "GE", "ORCL"):
        out.append(f"  - {t}: " + ", ".join(f"{r['date'][:7]} {r['code'] or 'ok'}" for r in by_t.get(t, [])[-14:]))
    out.append("")

    split_lines, p4 = [], True
    for t, sd in SPLITS:
        allr = by_t.get(t, [])
        before = [r for r in allr if r["date"] < sd.isoformat()][-1:]      # adjacent quarter-ends
        after = [r for r in allr if r["date"] >= sd.isoformat()][:1]
        if not before or not after or not all(r["value"] and r["price"] for r in before + after):
            split_lines.append(f"- {t} {sd}: refused at an adjacent quarter-end — not testable")
            continue
        a = before[0]["value"] / before[0]["price"]
        b = after[0]["value"] / after[0]["price"]
        ok = 0.5 <= b / a <= 2.0
        p4 &= ok
        split_lines.append(f"- {'✅' if ok else '❌'} {t} {sd}: value÷price {a:.2f} → {b:.2f}")
    out.append(f"## P4 — splits (consistency on RESTATED data only; cannot detect a live split "
               f"bug — see unit tests): {'consistent' if p4 else 'INCONSISTENT'}")
    out += split_lines
    out.append("")

    # ── day-to-day stability across the fiscal-year roll (what a user actually sees) ──
    daily_max: List[Tuple[str, str, float, bool]] = []   # ticker, day, signed move, 10-K that day
    for t, p in list(zip(tickers, raw))[:60]:
        if t in fetch_fail or len(daily_max) >= 40:
            continue
        prices = _series(p["prices"], "close")
        mcaps = _series(p["mcap"], "marketCap")
        stock_m = _month_ends(prices)
        prof = p["profile"][0] if isinstance(p["profile"], list) and p["profile"] else p["profile"]
        ends = sorted({d for d in (dcf._parse_date(r.get("date")) for r in dcf._rows(p["income_annual"]))
                       if d and date(2021, 1, 1) <= d <= date(2026, 6, 30)})
        if not ends:
            continue
        fy_end = ends[-1]
        beta = beta_at(stock_m, market, fy_end)
        prev_v, prev_fy, worst = None, None, (None, 0.0, False)
        for k in range(-5, 96):
            d = fy_end + timedelta(days=k)
            price, mcap = _at(prices, d), _at(mcaps, d)
            inp = dcf.build_inputs(
                t, d, profile={**(prof or {}), "beta": beta, "price": price, "marketCap": mcap},
                income_annual=p["income_annual"], income_quarter=p["income_quarter"],
                cash_flow_annual=p["cash_flow_annual"], balance_annual=p["balance_annual"],
                estimates=p["estimates"], rf_avg_pct=dcf.rf_average_pct(dgs10, fy_end),
                price=price, market_cap=mcap)
            dcf.MIN_ANALYSTS = 1
            r = dcf.value_company(inp)
            dcf.MIN_ANALYSTS = real_floor
            v = r.fair_value if r.status == "ok" else None
            if v is None:
                prev_v, prev_fy = None, None
                continue
            if prev_v:
                move = v / prev_v - 1
                if abs(move) > abs(worst[1]):
                    worst = (d.isoformat(), move, prev_fy is not None and inp.last_fy_end != prev_fy)
            prev_v, prev_fy = v, inp.last_fy_end
        if worst[0]:
            daily_max.append((t, worst[0], worst[1], worst[2]))
    if daily_max:
        w = sorted(abs(x[2]) for x in daily_max)
        top = sorted(daily_max, key=lambda x: -abs(x[2]))[:6]
        on_10k = sum(1 for x in daily_max if x[3] and abs(x[2]) > 0.05)
        big = sum(1 for x in daily_max if abs(x[2]) > 0.05)
        out.append("## Day-to-day stability across the fiscal-year roll (diagnostic)")
        out.append(f"{len(daily_max)} tickers valued daily from 5 days before their latest fiscal "
                   f"year-end to 95 days after (rate and beta held fixed): largest single-day move "
                   f"median {statistics.median(w):.2%}, max {w[-1]:.2%}. Of the {big} tickers whose "
                   f"worst day exceeded 5 %, {on_10k} fell on the day their 10-K became public "
                   f"(the five-year trailing ratios take in the new year). Worst: "
                   + ", ".join(f"{t} {d} {x:+.1%}{' (10-K)' if k else ''}" for t, d, x, k in top) + ".")
        out.append("")

    out.append("## P5 — consensus-vs-actual error: NOT MEASURABLE from FMP")
    errs = []
    for t, p in zip(tickers, raw):
        if t in fetch_fail:
            continue
        actual = {dcf._parse_date(r.get("date")): dcf._num(r.get("netIncome"))
                  for r in dcf._rows(p["income_annual"])}
        for e in dcf._rows(p["estimates"]):
            d = dcf._parse_date(e.get("date"))
            ni = dcf._num(e.get("netIncomeAvg"))
            match = next((a for k, a in actual.items() if k and d and abs((k - d).days) <= 20), None)
            if ni and match and match > 0 and d and date(2016, 1, 1) <= d <= date(2025, 12, 31):
                errs.append(abs(ni / match - 1))
    if errs:
        out.append(f"FMP past-year consensus vs reported net income, {len(errs)} company-years: "
                   f"median |error| {statistics.median(errs):.1%}. That is the error of a forecast "
                   f"made at report time, not of the 1-5-year forecasts the model uses, so it cannot "
                   f"set the range width. The range stays method R vs method E at r ± 1 pt.")
    out.append("")

    out.append("## Refusals")
    codes = Counter(r["code"] or "ok" for r in rows)
    real_codes = Counter(r["real_floor_code"] or "ok" for r in rows)
    out.append("| Code | machinery pass (floor 1) | real floor (5 analysts) |")
    out.append("|---|---|---|")
    for c in sorted(set(codes) | set(real_codes), key=lambda c: -codes.get(c, 0)):
        out.append(f"| {c} | {codes.get(c, 0)} | {real_codes.get(c, 0)} |")
    last_date = dates[-1].isoformat()
    latest = [r for r in rows if r["date"] == last_date]
    out.append(f"\nLatest date ({last_date}), real floor: "
               f"{sum(1 for r in latest if not r['real_floor_code'])} valued of {len(latest)}.")
    out.append("")

    out.append("## Informational — value ÷ price by year (NOT a pass criterion; never tuned on)")
    out.append("Caveat: the production rate pair and the ERP are the 1 Jan 2026 figures at EVERY "
               "date, and the consensus is perfect-foresight, so levels are not what the model "
               "would have said at the time; the columns compare rate conventions only.")
    out.append("")
    out.append("| Year | n | production (4.18 % + β×4.23 %) | 5-yr-average variant | 3-month variant |")
    out.append("|---|---|---|---|---|")
    by_year: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["value"] and r["price"]:
            by_year[r["date"][:4]].append(r)
    for y in sorted(by_year):
        vs = [r["value"] / r["price"] - 1 for r in by_year[y]]
        rv = [r["recent_value"] / r["price"] - 1 for r in by_year[y] if r["recent_value"]]
        av = [r["avg5_value"] / r["price"] - 1 for r in by_year[y] if r["avg5_value"]]
        md = lambda xs: f"{statistics.median(xs):+.0%}" if xs else "—"  # noqa: E731
        out.append(f"| {y} | {len(vs)} | {md(vs)} | {md(av)} | {md(rv)} |")
    out.append("")
    verdict = "PASS" if all((p1, p2, p3)) else "FAIL"
    out.insert(2, f"**Overall (P1-P3; P4 is a consistency check only): {verdict}**\n")

    with open(args.out, "w") as fh:
        fh.write("\n".join(out) + "\n")
    with open(args.out.replace(".md", ".json"), "w") as fh:
        json.dump({"rows": rows, "steps": steps, "crashes": crashes}, fh, default=str)
    print("\n".join(out[:12]))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
