"""DCF fair-value PROTOTYPE — research for the report's Wall Street card (E3, 2026-09-19).

Read-only: entitled FMP endpoints + FRED, no Supabase, no Gemini, no credits. Not
imported by any test (the suite is socket-blocked). Run from `backend/`:

    ./venv/bin/python scripts/dcf_fair_value_prototype.py AAPL MSFT NVDA TER ORCL PLUG JPM O
    ./venv/bin/python scripts/dcf_fair_value_prototype.py --json out.json AAPL

For each ticker it computes two Caydex models from the data we are licensed for and
prints them beside FMP's own four DCF outputs and the price:

  A. "Street FCFE" — Simply Wall St's shape: a 2-stage free-cash-flow-to-EQUITY DCF.
     Years with analyst NET-INCOME consensus (`analyst-estimates`, entitled) become
     levered FCF via the company's own trailing FCF/NI conversion; the rest of the
     10-year window is extrapolated with a growth rate that decays linearly to the
     terminal rate; terminal value by Gordon growth; discounted at the cost of
     EQUITY (CAPM: 5-year-average 10-year Treasury + bounded beta × ERP).
  B. "Enterprise FCFF" — the textbook WACC model in FMP's shape but driven by the
     analyst REVENUE / EBITDA consensus instead of the historical revenue CAGR:
     UFCF = EBITDA − cash taxes on EBIT − capex − ΔNWC (ratios from the last FYs),
     discounted at WACC, EV − net debt = equity.

Both refuse (return a reason, never a number) when the model does not apply: negative
or collapsing FCF, a bank/insurer (excess-returns territory), a REIT (AFFO), missing
estimates, or missing shares. The sensitivity grid varies the discount rate ±1pt and
the terminal growth ±0.5pt around model A.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, ".")

from app.integrations.fmp import FMPClient  # noqa: E402
from app.integrations.fred import get_fred_client  # noqa: E402

# ── assumptions (all named so the doc can cite them) ────────────────────────
FORECAST_YEARS = 10           # Simply Wall St: 10 years of levered FCF
BETA_MIN, BETA_MAX = 0.8, 2.0  # Simply Wall St bounds
DEFAULT_ERP = 4.72            # Damodaran-style ERP; FMP's custom DCF reports the same value
DEFAULT_RF = 4.25             # only if FRED is unreachable
RF_AVG_YEARS = 5              # 5-year average of the 10y Treasury (SWS), not the spot
CONVERSION_CLIP = (0.35, 1.60)  # FCF / net income conversion sanity bounds
MARGIN_CLIP = (0.02, 0.60)      # FCF / revenue margin sanity bounds
MAX_ANALYST_YEARS = 5           # Simply Wall St uses ≤5 consensus years, then extrapolates
MIN_ANALYSTS = 5                # a far year covered by fewer analysts is noise, not consensus
GROWTH_CLIP = (-0.15, 0.30)   # per-year growth sanity bounds for the extrapolation
# Refuse on INDUSTRY, never on sector: "Financial Services" also holds V / MA
# (payment networks with 50 % FCF margins) and "Real Estate" holds CBRE (services),
# for which a cash-flow DCF is exactly right (review finding, 2026-09-19). Banks,
# insurers, asset managers, mortgage / capital-markets houses earn on the balance
# sheet — excess returns, not FCF. Lenders filed as "Credit Services" (COF, SYF)
# still slip through; a phase-2 rule needs loan-book detection.
FINANCIAL_INDUSTRY_PREFIXES = (
    "Banks", "Insurance", "Asset Management", "Financial - Capital Markets",
    "Financial - Mortgages", "Financial - Diversified", "Financial - Conglomerates",
    "Shell Companies",
)
REIT_INDUSTRY_PREFIX = "REIT"


@dataclass
class Inputs:
    ticker: str
    price: Optional[float] = None
    shares: Optional[float] = None
    beta_raw: Optional[float] = None
    sector: str = ""
    industry: str = ""
    # trailing (oldest → newest)
    fy: List[str] = field(default_factory=list)
    revenue: List[float] = field(default_factory=list)
    net_income: List[float] = field(default_factory=list)
    ebitda: List[float] = field(default_factory=list)
    ebit: List[float] = field(default_factory=list)
    da: List[float] = field(default_factory=list)
    capex: List[float] = field(default_factory=list)      # positive numbers (outflow)
    fcf: List[float] = field(default_factory=list)        # levered FCF (CFO − capex)
    sbc: List[float] = field(default_factory=list)
    tax_rate: Optional[float] = None
    interest_expense: Optional[float] = None
    total_debt: Optional[float] = None
    cash: Optional[float] = None
    nwc: List[float] = field(default_factory=list)
    # forward consensus (year → value)
    est_revenue: Dict[int, float] = field(default_factory=dict)
    est_ebitda: Dict[int, float] = field(default_factory=dict)
    est_net_income: Dict[int, float] = field(default_factory=dict)
    est_analysts: Dict[int, int] = field(default_factory=dict)
    share_note: str = ""
    # rates
    rf_spot: Optional[float] = None
    rf_avg5: Optional[float] = None
    erp: float = DEFAULT_ERP
    # FMP's own
    fmp_dcf: Optional[float] = None
    fmp_levered_dcf: Optional[float] = None
    fmp_custom: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelResult:
    name: str
    fair_value: Optional[float] = None
    reason: Optional[str] = None
    discount_rate: Optional[float] = None
    terminal_growth: Optional[float] = None
    path: List[Tuple[int, float, str]] = field(default_factory=list)  # (year, cash flow, source)
    pv_flows: Optional[float] = None
    pv_terminal: Optional[float] = None
    equity_value: Optional[float] = None
    notes: List[str] = field(default_factory=list)


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _year(s: Any) -> Optional[int]:
    try:
        return int(str(s)[:4])
    except (TypeError, ValueError):
        return None


# ── collection ───────────────────────────────────────────────────────────────

async def collect(ticker: str, fmp: FMPClient) -> Inputs:
    inp = Inputs(ticker=ticker)
    prof, inc, bal, cfs, est, dcf, ldcf, custom, shares = await asyncio.gather(
        fmp.get_company_profile(ticker),
        fmp.get_income_statement(ticker, "annual", 6),
        fmp.get_balance_sheet(ticker, "annual", 2),
        fmp.get_cash_flow_statement(ticker, "annual", 6),
        fmp.get_analyst_estimates(ticker, "annual", 10),
        fmp._make_request("discounted-cash-flow", params={"symbol": ticker}),
        fmp._make_request("levered-discounted-cash-flow", params={"symbol": ticker}),
        fmp._make_request("custom-discounted-cash-flow", params={"symbol": ticker}),
        fmp.get_shares_float(ticker),
        return_exceptions=True,
    )
    if isinstance(prof, dict):
        inp.price = _f(prof.get("price"))
        inp.beta_raw = _f(prof.get("beta"))
        inp.sector = str(prof.get("sector") or "")
        inp.industry = str(prof.get("industry") or "")
    float_shares = _f(shares.get("outstandingShares")) if isinstance(shares, dict) else None
    if isinstance(inc, list) and inc:
        rows = sorted([r for r in inc if isinstance(r, dict)], key=lambda r: str(r.get("date")))
        for r in rows:
            inp.fy.append(str(r.get("date"))[:4])
            inp.revenue.append(_f(r.get("revenue")) or 0.0)
            inp.net_income.append(_f(r.get("netIncome")) or 0.0)
            inp.ebitda.append(_f(r.get("ebitda")) or 0.0)
            inp.ebit.append(_f(r.get("operatingIncome")) or 0.0)
            inp.da.append(_f(r.get("depreciationAndAmortization")) or 0.0)
        last = rows[-1]
        pretax, tax = _f(last.get("incomeBeforeTax")), _f(last.get("incomeTaxExpense"))
        if pretax and tax is not None and pretax > 0:
            inp.tax_rate = min(max(tax / pretax, 0.0), 0.35)
        inp.interest_expense = _f(last.get("interestExpense"))
        # Share count: the DILUTED weighted average from the latest income statement,
        # cross-checked against `shares-float`; when the two disagree by >5 % keep
        # the diluted one and say so (CRM: 0.819 B float vs 0.96 B diluted — a 15 %
        # per-share error that the first run attributed to cash-flow basis).
        diluted = _f(last.get("weightedAverageShsOutDil"))
        if diluted and diluted > 0:
            inp.shares = diluted
            if float_shares and abs(float_shares / diluted - 1) > 0.05:
                inp.share_note = f"shares-float {float_shares/1e9:.3f}B vs diluted {diluted/1e9:.3f}B — using diluted"
        elif float_shares:
            inp.shares = float_shares
    if isinstance(cfs, list) and cfs:
        rows = sorted([r for r in cfs if isinstance(r, dict)], key=lambda r: str(r.get("date")))
        for r in rows:
            capex = abs(_f(r.get("capitalExpenditure")) or 0.0)
            cfo = _f(r.get("operatingCashFlow")) or 0.0
            fcf = _f(r.get("freeCashFlow"))
            inp.capex.append(capex)
            inp.fcf.append(fcf if fcf is not None else cfo - capex)
            inp.sbc.append(_f(r.get("stockBasedCompensation")) or 0.0)
    if isinstance(bal, list) and bal:
        rows = sorted([r for r in bal if isinstance(r, dict)], key=lambda r: str(r.get("date")))
        last = rows[-1]
        inp.total_debt = _f(last.get("totalDebt")) or 0.0
        inp.cash = _f(last.get("cashAndShortTermInvestments")) or _f(last.get("cashAndCashEquivalents")) or 0.0
        for r in rows:
            ca = _f(r.get("totalCurrentAssets")) or 0.0
            cl = _f(r.get("totalCurrentLiabilities")) or 0.0
            c = _f(r.get("cashAndShortTermInvestments")) or 0.0
            d = _f(r.get("shortTermDebt")) or 0.0
            inp.nwc.append((ca - c) - (cl - d))
    if isinstance(est, list):
        for r in est:
            if not isinstance(r, dict):
                continue
            y = _year(r.get("date"))
            if y is None:
                continue
            rev = _f(r.get("revenueAvg"))
            if rev:
                inp.est_revenue[y] = rev
            e = _f(r.get("ebitdaAvg"))
            if e:
                inp.est_ebitda[y] = e
            ni = _f(r.get("netIncomeAvg"))
            if ni:
                inp.est_net_income[y] = ni
            n = _f(r.get("numAnalystsRevenue")) or _f(r.get("numAnalystsEps")) or 0
            inp.est_analysts[y] = int(n)
    if isinstance(dcf, list) and dcf and isinstance(dcf[0], dict):
        inp.fmp_dcf = _f(dcf[0].get("dcf"))
        if inp.price is None:
            inp.price = _f(dcf[0].get("Stock Price"))
    if isinstance(ldcf, list) and ldcf and isinstance(ldcf[0], dict):
        inp.fmp_levered_dcf = _f(ldcf[0].get("dcf"))
    if isinstance(custom, list) and custom and isinstance(custom[0], dict):
        c0 = custom[0]
        inp.fmp_custom = {k: c0.get(k) for k in (
            "beta", "riskFreeRate", "marketRiskPremium", "costOfEquity", "wacc",
            "longTermGrowthRate", "revenuePercentage", "equityValuePerShare",
            "dilutedSharesOutstanding", "netDebt")}
        erp = _f(c0.get("marketRiskPremium"))
        if erp:
            inp.erp = erp
        if inp.shares is None:
            inp.shares = _f(c0.get("dilutedSharesOutstanding"))
    return inp


async def collect_rates(inp_list: List[Inputs]) -> None:
    fred = get_fred_client()
    obs = await fred.get_observations("DGS10", limit=RF_AVG_YEARS * 262)
    if obs:
        values = [o.value for o in obs if o.value is not None]
        spot = values[0]
        avg = statistics.fmean(values)
    else:
        spot = avg = DEFAULT_RF
    for inp in inp_list:
        inp.rf_spot, inp.rf_avg5 = spot, avg


# ── shared maths ─────────────────────────────────────────────────────────────

def bounded_beta(beta: Optional[float]) -> float:
    b = beta if beta is not None and math.isfinite(beta) else 1.0
    return min(max(b, BETA_MIN), BETA_MAX)


def cost_of_equity(inp: Inputs) -> float:
    rf = (inp.rf_avg5 if inp.rf_avg5 is not None else DEFAULT_RF) / 100.0
    return rf + bounded_beta(inp.beta_raw) * inp.erp / 100.0


def terminal_growth(inp: Inputs) -> float:
    # Simply Wall St: the 5-year average of the 10-year government bond yield.
    g = (inp.rf_avg5 if inp.rf_avg5 is not None else DEFAULT_RF) / 100.0
    return min(g, 0.04)


def is_financial(inp: Inputs) -> bool:
    return inp.industry.startswith(FINANCIAL_INDUSTRY_PREFIXES)


def is_reit(inp: Inputs) -> bool:
    return inp.industry.startswith(REIT_INDUSTRY_PREFIX)


def gordon_pv(flows: List[float], rate: float, g: float) -> Tuple[float, float, float]:
    """PV of the explicit flows, the terminal value, and its PV."""
    pv = sum(cf / (1 + rate) ** (i + 1) for i, cf in enumerate(flows))
    tv = flows[-1] * (1 + g) / (rate - g) if rate > g else float("nan")
    pv_tv = tv / (1 + rate) ** len(flows) if math.isfinite(tv) else float("nan")
    return pv, tv, pv_tv


def seed_growth(first: float, later: List[float], g_terminal: float) -> float:
    """Growth to start the decay from: the CAGR from the FIRST consensus year to the
    LAST (clipped). One consensus year → no span → the terminal rate."""
    if not later or first <= 0 or later[-1] <= 0:
        return g_terminal
    n = len(later)
    try:
        cagr = (later[-1] / first) ** (1.0 / n) - 1
    except (ZeroDivisionError, ValueError, OverflowError):
        return g_terminal
    return min(max(cagr, GROWTH_CLIP[0]), GROWTH_CLIP[1])


def decayed_path(start_value: float, start_growth: float, years: int, g_terminal: float) -> List[Tuple[float, float]]:
    """Extrapolate `years` values from `start_value`, the growth decaying linearly
    from `start_growth` to `g_terminal` (Simply Wall St's "growth rate slows")."""
    out = []
    v = start_value
    for i in range(1, years + 1):
        gr = start_growth + (g_terminal - start_growth) * (i / years)
        v = v * (1 + gr)
        out.append((v, gr))
    return out


# ── model A: Street FCFE (Simply Wall St shape) ───────────────────────────────

def _usable_estimate_years(series: Dict[int, float], analysts: Dict[int, int], years: List[int]) -> List[int]:
    """Contiguous forward years with a positive consensus covered by ≥ MIN_ANALYSTS,
    capped at MAX_ANALYST_YEARS."""
    out: List[int] = []
    for y in years:
        v = series.get(y)
        if v is None or v <= 0 or analysts.get(y, 0) < MIN_ANALYSTS or len(out) >= MAX_ANALYST_YEARS:
            break
        out.append(y)
    return out


def model_street_fcfe(inp: Inputs, basis: str = "revenue") -> ModelResult:
    """basis="revenue": FCF_t = revenue consensus × trailing FCF margin (basis-safe).
    basis="net_income": FCF_t = net-income consensus × trailing FCF/NI conversion —
    kept ONLY to show the GAAP-vs-adjusted trap (see the research doc)."""
    names = {"revenue": "A Street FCFE (rev × margin)", "net_income": "A′ FCFE via GAAP NI (trap)",
             "adjusted_ni": "A″ FCFE via NI+SBC conversion"}
    res = ModelResult(name=names[basis])
    if is_financial(inp):
        res.reason = "financial company — cash-flow DCF does not apply (excess-returns model needed)"
        return res
    if is_reit(inp):
        res.reason = "REIT — needs AFFO, not FCF"
        return res
    if not inp.shares or not inp.price:
        res.reason = "no share count / price"
        return res
    fcf_hist = inp.fcf[-5:]
    ni_hist = inp.net_income[-5:]
    if not fcf_hist or fcf_hist[-1] <= 0 or sum(1 for x in fcf_hist if x <= 0) >= 2:
        res.reason = "negative or unstable free cash flow — no valuation"
        return res
    this_year = date.today().year
    last_fy = int(inp.fy[-1]) if inp.fy else this_year - 1
    years = list(range(last_fy + 1, last_fy + 1 + FORECAST_YEARS))
    rate = cost_of_equity(inp)
    g = terminal_growth(inp)

    if basis == "revenue":
        rev_hist = inp.revenue[-3:]
        margins = [f / r for f, r in zip(inp.fcf[-3:], rev_hist) if r > 0]
        if not margins or statistics.median(margins) <= 0:
            res.reason = "no positive trailing FCF margin"
            return res
        factor = min(max(statistics.median(margins), MARGIN_CLIP[0]), MARGIN_CLIP[1])
        series, label = inp.est_revenue, f"analyst revenue × FCF margin {factor*100:.1f}%"
        res.notes.append(f"FCF margin {factor*100:.1f}% (median of last {len(margins)} FYs)")
    else:
        # Consensus net income is on the ANALYSTS' basis (adjusted — SBC and
        # acquired-intangible amortisation added back for most software names),
        # while the statements are GAAP. Dividing GAAP-basis FCF by GAAP NI and
        # applying it to adjusted consensus double-counts the add-backs (CRM
        # 2026: GAAP NI $7.5B vs consensus $11.3B). `adjusted_ni` measures the
        # conversion against NI + SBC, the closest entitled proxy for the
        # analysts' basis, and applies it to the same basis.
        sbc_hist = inp.sbc[-5:] if basis == "adjusted_ni" else [0.0] * len(ni_hist)
        ratios = [f / (n + b) for f, n, b in zip(fcf_hist, ni_hist, sbc_hist) if (n + b) > 0 and f > 0]
        if not ratios:
            res.reason = "no positive FCF/net-income pairs to derive a conversion"
            return res
        factor = min(max(statistics.median(ratios), CONVERSION_CLIP[0]), CONVERSION_CLIP[1])
        series, label = inp.est_net_income, f"analyst NI × {factor:.2f}"
        res.notes.append(f"FCF/{'(NI+SBC)' if basis == 'adjusted_ni' else 'NI'} conversion {factor:.2f} (median of last {len(ratios)} FYs)")

    use_years = _usable_estimate_years(series, inp.est_analysts, years)
    path: List[Tuple[int, float, str]] = []
    prev = fcf_hist[-1]
    for y in use_years:
        v = series[y] * factor
        path.append((y, v, f"{label} (n={inp.est_analysts.get(y, 0)})"))
        prev = v
    n_est = len(path)
    if n_est == 0:
        res.reason = f"no forward {basis} consensus with ≥{MIN_ANALYSTS} analysts"
        return res
    # Seed the decay from the CAGR ACROSS the consensus years (first → last), never
    # from the last single-year step (far-year consensus is thin — 11 analysts vs 30
    # on AAPL 2029 vs 2026 — and one thin year can print a +25 % jump) and never from
    # the last GAAP actual (a capex-depressed FY2025 FCF inflated GOOGL's seed to
    # +24.7 %/yr against a +4.8 % consensus path — review finding, 2026-09-19).
    start_growth = seed_growth(path[0][1], [v for _, v, _ in path[1:]], g)
    remaining = FORECAST_YEARS - n_est
    for (v, gr), y in zip(decayed_path(prev, start_growth, remaining, g), years[n_est:]):
        path.append((y, v, f"extrapolated @ {gr * 100:.1f}%"))
    flows = [v for _, v, _ in path]
    pv, tv, pv_tv = gordon_pv(flows, rate, g)
    if not math.isfinite(pv_tv):
        res.reason = "discount rate ≤ terminal growth"
        return res
    equity = pv + pv_tv
    res.fair_value = equity / inp.shares
    res.discount_rate, res.terminal_growth = rate, g
    res.path, res.pv_flows, res.pv_terminal, res.equity_value = path, pv, pv_tv, equity
    res.notes.append(f"{n_est} analyst years, {remaining} extrapolated; beta {bounded_beta(inp.beta_raw):.2f} (raw {inp.beta_raw})")
    return res


# ── model B: Enterprise FCFF / WACC on analyst revenue ────────────────────────

def model_enterprise_fcff(inp: Inputs) -> ModelResult:
    res = ModelResult(name="B Enterprise FCFF")
    if is_financial(inp):
        res.reason = "financial company — EV/WACC DCF does not apply"
        return res
    if is_reit(inp):
        res.reason = "REIT — needs AFFO"
        return res
    if (not inp.shares or not inp.price or len(inp.revenue) < 3
            or len(inp.capex) < 3 or len(inp.ebitda) < 3 or len(inp.da) < 3):
        res.reason = "insufficient statements"
        return res
    rev_hist = inp.revenue[-3:]
    ebitda_m = statistics.fmean(e / r for e, r in zip(inp.ebitda[-3:], rev_hist) if r > 0)
    da_m = statistics.fmean(d / r for d, r in zip(inp.da[-3:], rev_hist) if r > 0)
    capex_m = statistics.fmean(c / r for c, r in zip(inp.capex[-3:], rev_hist) if r > 0)
    nwc_m = (statistics.fmean(n / r for n, r in zip(inp.nwc[-2:], rev_hist[-2:]) if r > 0)
             if len(inp.nwc) >= 2 else 0.0)
    if ebitda_m <= 0 or (ebitda_m - da_m) <= 0:
        res.reason = "negative EBITDA/EBIT margin — no valuation"
        return res
    # A capex super-cycle (ORCL FY2026: capex ≈ 40% of revenue vs a 10% history)
    # is not a run-rate; carrying it into ten forecast years prints a nonsense
    # value ($20 on a $148 stock). Refuse until capex is normalised explicitly.
    capex_ratios = [c / r for c, r in zip(inp.capex[-5:], inp.revenue[-5:]) if r > 0]
    if len(capex_ratios) >= 3 and capex_ratios[-1] > 1.5 * statistics.median(capex_ratios):
        res.reason = (f"capex/revenue {capex_ratios[-1]*100:.0f}% is >1.5× its 5-year median "
                      f"({statistics.median(capex_ratios)*100:.0f}%) — capex needs normalising")
        return res
    # Consensus EBITDA is on the analysts' ADJUSTED basis (SBC added back). Treat
    # SBC as the real cost it is (Damodaran) by deducting the trailing SBC/revenue.
    sbc_m = statistics.fmean(b / r for b, r in zip(inp.sbc[-3:], rev_hist) if r > 0) if inp.sbc else 0.0
    tax = inp.tax_rate if inp.tax_rate is not None else 0.21
    coe = cost_of_equity(inp)
    debt = inp.total_debt or 0.0
    mcap = inp.price * inp.shares
    kd = ((inp.interest_expense or 0.0) / debt) if debt > 0 else 0.0
    kd = min(max(kd, 0.02), 0.12) if debt > 0 else 0.0
    wacc = (mcap * coe + debt * kd * (1 - tax)) / (mcap + debt)
    g = terminal_growth(inp)

    last_fy = int(inp.fy[-1])
    years = list(range(last_fy + 1, last_fy + 1 + FORECAST_YEARS))
    path: List[Tuple[int, float, str]] = []
    prev_rev = rev_hist[-1]
    prev_nwc = nwc_m * prev_rev
    revenues: List[Tuple[int, float, str]] = []
    for y in _usable_estimate_years(inp.est_revenue, inp.est_analysts, years):
        r = inp.est_revenue[y]
        revenues.append((y, r, f"analyst revenue (n={inp.est_analysts.get(y, 0)})"))
        prev_rev = r
    n_est = len(revenues)
    if n_est == 0:
        res.reason = f"no forward revenue consensus with ≥{MIN_ANALYSTS} analysts"
        return res
    start_growth = seed_growth(revenues[0][1], [r for _, r, _ in revenues[1:]], g)
    for (v, gr), y in zip(decayed_path(prev_rev, start_growth, FORECAST_YEARS - n_est, g), years[n_est:]):
        revenues.append((y, v, f"extrapolated @ {gr * 100:.1f}%"))
    # Analyst EBITDA where the consensus has it; extrapolated years carry the LAST
    # consensus margin forward (not the trailing one — that step-change printed a
    # 2030→2031 cash-flow DROP on AAPL and TER in the first run).
    margin_fwd = ebitda_m
    for y, r, src in revenues:
        if src.startswith("analyst") and inp.est_ebitda.get(y):
            ebitda = inp.est_ebitda[y]
            margin_fwd = ebitda / r if r > 0 else margin_fwd
        else:
            ebitda = r * margin_fwd
        ebit = ebitda - r * da_m
        nwc = nwc_m * r
        ufcf = ebit * (1 - tax) + r * da_m - r * capex_m - r * sbc_m - (nwc - prev_nwc)
        prev_nwc = nwc
        path.append((y, ufcf, src.replace("revenue", "rev→UFCF")))
    flows = [v for _, v, _ in path]
    if flows[-1] <= 0:
        res.reason = "terminal UFCF ≤ 0"
        return res
    pv, tv, pv_tv = gordon_pv(flows, wacc, g)
    if not math.isfinite(pv_tv):
        res.reason = "WACC ≤ terminal growth"
        return res
    ev = pv + pv_tv
    equity = ev - (debt - (inp.cash or 0.0))
    res.fair_value = equity / inp.shares
    res.discount_rate, res.terminal_growth = wacc, g
    res.path, res.pv_flows, res.pv_terminal, res.equity_value = path, pv, pv_tv, equity
    res.notes.append(f"EBITDA margin {ebitda_m*100:.1f}%, D&A {da_m*100:.1f}%, capex {capex_m*100:.1f}%, SBC {sbc_m*100:.1f}% (deducted), tax {tax*100:.0f}%, Kd {kd*100:.1f}%, WACC {wacc*100:.2f}%")
    return res


def sensitivity(inp: Inputs, base: ModelResult) -> List[List[Optional[float]]]:
    """Rows: discount −1 / 0 / +1 pt; cols: terminal growth −0.5 / 0 / +0.5 pt."""
    if base.fair_value is None or not inp.shares:
        return []
    flows = [v for _, v, _ in base.path]
    grid: List[List[Optional[float]]] = []
    for dr in (-0.01, 0.0, 0.01):
        row: List[Optional[float]] = []
        for dg in (-0.005, 0.0, 0.005):
            rate = (base.discount_rate or 0) + dr
            g = (base.terminal_growth or 0) + dg
            pv, tv, pv_tv = gordon_pv(flows, rate, g)
            row.append((pv + pv_tv) / inp.shares if math.isfinite(pv_tv) else None)
        grid.append(row)
    return grid


# ── reproductions (the §6 checks in documents/research/dcf-fair-value.md) ────

def fmp_consistency_line(inp: Inputs) -> str:
    """FMP's `discounted-cash-flow` value must equal its own `custom-discounted-cash-flow`
    `equityValuePerShare` (same model, default inputs) — the arithmetic identity the
    research doc relies on when it reads FMP's inputs off the custom endpoint."""
    c = inp.fmp_custom.get("equityValuePerShare")
    if inp.fmp_dcf is None or c is None:
        return "FMP consistency: n/a (one endpoint empty)\n"
    try:
        gap = abs(float(c) - float(inp.fmp_dcf)) / max(abs(float(inp.fmp_dcf)), 1e-9)
    except (TypeError, ValueError):
        return "FMP consistency: n/a\n"
    verdict = "OK" if gap < 0.005 else "MISMATCH"
    return f"FMP consistency: dcf {inp.fmp_dcf} vs custom equityValuePerShare {c} → {verdict}\n"


SWS_APPLE_MAY_2025 = {
    "flows_b": [109.3, 125.8, 139.0, 163.6, 179.3, 191.5, 202.2, 212.0, 221.0, 229.5],
    "discount": 0.081, "terminal_g": 0.029, "published_per_share": 218.0,
    "published_pv_flows_t": 1.1, "published_pv_terminal_t": 2.1, "published_equity_t": 3.2,
    "source": "simplywall.st via finance.yahoo.com/news/look-fair-value-apple-inc-120110424.html",
}


def reproduce_simply_wall_st_apple() -> str:
    """Recompute Simply Wall St's published Apple path with `gordon_pv` — the check that
    our formulas match their shape (their inputs, our arithmetic)."""
    d = SWS_APPLE_MAY_2025
    pv, tv, pv_tv = gordon_pv(d["flows_b"], d["discount"], d["terminal_g"])
    equity = pv + pv_tv
    shares_implied = equity / d["published_per_share"]
    return (
        "## Reproduction — Simply Wall St's Apple DCF (May 2025 inputs, our formulas)\n"
        f"PV flows {pv:,.0f}B (published ≈ {d['published_pv_flows_t']*1000:,.0f}B) · "
        f"PV terminal {pv_tv:,.0f}B (published ≈ {d['published_pv_terminal_t']*1000:,.0f}B) · "
        f"equity {equity:,.0f}B (published ≈ {d['published_equity_t']*1000:,.0f}B) · "
        f"implied shares at ${d['published_per_share']:.0f} = {shares_implied:.2f}B (Apple had ~14.7B)\n"
        f"source: {d['source']}\n"
    )


# ── output ───────────────────────────────────────────────────────────────────

def _pct(fv: Optional[float], price: Optional[float]) -> str:
    if fv is None or not price:
        return "—"
    return f"{(fv / price - 1) * 100:+.0f}%"


def _money(v: Optional[float]) -> str:
    return "—" if v is None else f"${v:,.2f}"


def render(inp: Inputs, a: ModelResult, a_ni: ModelResult, a_adj: ModelResult, b: ModelResult, grid: List[List[Optional[float]]]) -> str:
    out = [f"## {inp.ticker} — price {_money(inp.price)} · {inp.sector} / {inp.industry}"]
    shares_str = f"{inp.shares/1e9:.3f}B" if inp.shares else "—"
    out.append(f"rf spot {inp.rf_spot:.2f}% · rf 5y-avg {inp.rf_avg5:.2f}% · ERP {inp.erp:.2f}% · beta raw {inp.beta_raw} → bounded {bounded_beta(inp.beta_raw):.2f} · shares {shares_str}"
               + (f" ({inp.share_note})" if inp.share_note else ""))
    out.append("")
    out.append("| Model | Fair value | vs price | Rate | g |")
    out.append("|---|---|---|---|---|")
    for m in (a, a_ni, a_adj, b):
        if m.fair_value is not None:
            out.append(f"| {m.name} | {_money(m.fair_value)} | {_pct(m.fair_value, inp.price)} | {m.discount_rate*100:.2f}% | {m.terminal_growth*100:.2f}% |")
        else:
            out.append(f"| {m.name} | — | refused: {m.reason} | | |")
    c = inp.fmp_custom
    out.append(f"| FMP `discounted-cash-flow` (FCFF/WACC, 5y hist-growth) | {_money(inp.fmp_dcf)} | {_pct(inp.fmp_dcf, inp.price)} | {c.get('wacc')}% | {c.get('longTermGrowthRate')}% |")
    out.append(f"| FMP `levered-discounted-cash-flow` | {_money(inp.fmp_levered_dcf)} | {_pct(inp.fmp_levered_dcf, inp.price)} | {c.get('wacc')}% | {c.get('longTermGrowthRate')}% |")
    for m in (a_adj, b):
        if m.path:
            out.append("")
            out.append(f"**{m.name} path** — {'; '.join(m.notes)}")
            out.append("| Year | Cash flow | Source |")
            out.append("|---|---|---|")
            for y, v, src in m.path:
                out.append(f"| {y} | {v/1e9:,.2f}B | {src} |")
            out.append(f"PV flows {m.pv_flows/1e9:,.1f}B · PV terminal {m.pv_terminal/1e9:,.1f}B ({m.pv_terminal/(m.pv_flows+m.pv_terminal)*100:.0f}% of value) · equity {m.equity_value/1e9:,.1f}B")
    if grid:
        out.append("")
        out.append("**Sensitivity (model A″, or A when it refuses)** — rows: discount rate −1 / base / +1 pt · cols: terminal g −0.5 / base / +0.5 pt")
        out.append("| | g−0.5 | g | g+0.5 |")
        out.append("|---|---|---|---|")
        for label, row in zip(("r−1", "r", "r+1"), grid):
            out.append(f"| {label} | " + " | ".join(_money(v) for v in row) + " |")
    out.append("")
    return "\n".join(out)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--json", help="also write the raw inputs + results here")
    args = ap.parse_args()
    fmp = FMPClient()
    inputs = await asyncio.gather(*(collect(t.upper(), fmp) for t in args.tickers))
    await collect_rates(list(inputs))
    dump: Dict[str, Any] = {}
    for inp in inputs:
        try:
            a = model_street_fcfe(inp, "revenue")
            a_ni = model_street_fcfe(inp, "net_income")
            a_adj = model_street_fcfe(inp, "adjusted_ni")
            b = model_enterprise_fcff(inp)
            grid = sensitivity(inp, a_adj if a_adj.fair_value is not None else a)
            print(render(inp, a, a_ni, a_adj, b, grid))
            print(fmp_consistency_line(inp))
            dump[inp.ticker] = {"inputs": asdict(inp), "A": asdict(a), "A_ni": asdict(a_ni),
                                "A_adj": asdict(a_adj), "B": asdict(b), "grid": grid}
        except Exception as exc:  # one bad ticker must not lose the others' output
            print(f"## {inp.ticker} — refused: {type(exc).__name__}: {exc}\n")
            dump[inp.ticker] = {"error": f"{type(exc).__name__}: {exc}"}
    print(reproduce_simply_wall_st_apple())
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(dump, fh, indent=1, default=str)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
