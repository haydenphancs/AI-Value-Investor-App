"""
Caydex Fair Value Estimate — a 2-stage free-cash-flow-to-equity DCF, model ``dcf-v1``.

The methodology is FROZEN in documents/research/dcf-methodology-v1.md, and this module must match
it line for line (hard rule 5: the methodology text must match the code). Change a rule or a
constant here and you have made a new model: bump ``MODEL_VERSION`` and update the spec in the
same change.

Why the rules look the way they do — every refusal, the SBC treatment, the timing convention, the
rate choices — is in documents/research/dcf-fair-value.md §§1-9 (prototype results, a 54-ticker
sweep, a comparison with ~75 published Simply Wall St values).

🔒 HARD RULE 1 — one value per (ticker, date, model version), identical for EVERY caller. Nothing
in this module may take, read or branch on a user, tier, persona, profile, watchlist or portfolio.
The US publisher exclusion (Lowe v. SEC) that lets Caydex publish this without being an
investment adviser depends on the value being impersonal. `tests/test_dcf_fair_value.py` scans
this file for those names.

Layout:
  * pure model  — ``build_inputs`` (FMP payloads → ``DcfInputs``) and ``value_company``
                  (``DcfInputs`` → ``DcfResult``). No I/O. The historical replay
                  (scripts/dcf_historical_replay.py) calls these with point-in-time payloads.
  * service     — ``DcfFairValueService.get_fair_value``: two-tier cache-aside + ``_inflight``
                  dedup (template: profit_power_service.py). Tier-2 reads/writes and the
                  append-only history write happen only when ``settings.DCF_ENABLED`` or
                  ``settings.DCF_SHADOW``. Caches are keyed to the ET date.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.config import settings
from app.database import get_supabase
from app.integrations.fmp import FMPUnavailableException, get_fmp_client
from app.integrations.fred import get_fred_client
from app.schemas.dcf_fair_value import DcfFairValueResponse
from app.utils.inflight import fail_shared_future
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# ── Model constants (spec §§1-3). Changing ANY of these = a new MODEL_VERSION. ─────────────
MODEL_VERSION = "dcf-v1"
METHOD_LABEL = "2-stage free cash flow to equity"

FORECAST_YEARS = 10
MAX_ANALYST_YEARS = 5
MIN_ANALYSTS = 5
HISTORY_YEARS = 5
MIN_HISTORY_YEARS = 3

# Damodaran implied ERP (FCFE-based), 1 Jan 2026 (T-bond 4.18 %). FMP's DCF uses 4.72 %, which
# is Damodaran's END-2020 figure. Source: pages.stern.nyu.edu/~adamodar/.../histimpl.html
ERP_PCT = 4.23
ERP_VINTAGE = "Damodaran implied ERP, 1 Jan 2026"
RF_AT_ERP_PCT = 4.18            # the 10-year T-bond rate Damodaran measured that ERP against
RF_AVERAGE_YEARS = 5
# Which risk-free rate enters the DISCOUNT RATE (spec §2). v1 = "erp_vintage": the T-bond rate
# Damodaran measured the ERP against (RF_AT_ERP_PCT), so rate and premium are one coherent pair.
# "avg5" (Simply Wall St's 5-year average) and "recent" (3-month average) are RESEARCH VARIANTS,
# measured in documents/research/dcf-fair-value.md §9 and never published. Terminal growth
# always uses the 5-year average.
RATE_RF_BASIS = "erp_vintage"
RF_RECENT_DAYS = 91
BETA_MIN, BETA_MAX = 0.8, 2.0
TERMINAL_G_CAP = 0.04
MIN_SPREAD = 0.04               # g never above r − 4 pts (terminal multiple ≲ 26×)
SENSITIVITY_STEP = 0.01         # range: discount rate ± 1 pt

MARGIN_BOUNDS = (0.01, 0.60)    # method R: (FCF − SBC) / revenue (WMT is a real ~2 %)
CONVERSION_BOUNDS = (0.35, 1.60)  # method E: (FCF − SBC) / (NI + SBC)
GROWTH_BOUNDS = (-0.15, 0.30)   # trend growth across the analyst years
MAX_TREND_DEVIATION = 0.25      # an analyst year > 25 % off the analyst-years trend → unstable
MAX_RATIO_SPREAD = 1.8          # trailing ratios: 2nd-highest ÷ 2nd-lowest (max ÷ min for 3 yrs)
MAX_DISAGREEMENT = 1.5          # method E / method R must sit in [1/1.5, 1.5]
MIN_SBC_YEARS = 3               # SBC > 0 in ≥ 3 of the last 5 years = "reported"
MAX_DEBT_TO_MARKET_CAP = 0.75
LENDER_INTEREST_SHARE = 0.08    # Credit Services with interest expense > 8 % of revenue
SHAPE_MAX_YOY_DROP = 0.25       # revenue falls > 25 % in one year: reported OR forecast
SHARE_CONFLICT = 0.15           # diluted shares vs market cap ÷ price (a 5:4 split = 20 %)
ROLL_BLEND_DAYS = 91            # the forecast window crossfades over a quarter after a FY end

# Consecutive fiscal-year ends must be a year apart (52/53-week years land at 364/371 days).
_FY_GAP_DAYS = (330, 400)
SAME_FY_DAYS = 20

FINANCIAL_INDUSTRY_PREFIXES = (
    "Banks", "Insurance", "Asset Management", "Financial - Capital Markets",
    "Financial - Mortgages", "Financial - Diversified", "Financial - Conglomerates",
    "Investment - Banking & Investment Services", "Shell Companies",
)
REIT_INDUSTRY_PREFIX = "REIT"
UTILITIES_SECTOR = "Utilities"
CREDIT_SERVICES_INDUSTRY = "Financial - Credit Services"
# Industrial / auto groups whose finance arm (Ford Credit, GM Financial, John Deere Financial,
# Cat Financial, ...) sits inside consolidated FCF, debt and interest. No FMP field flags this,
# so the list is maintained by hand; a new member is a model change.
CAPTIVE_FINANCE_TICKERS = frozenset({
    "F", "GM", "DE", "CAT", "CNH", "PCAR", "TM", "HMC", "STLA", "HOG",
})

REFUSAL_REASONS: Dict[str, str] = {
    "missing_data": "Not enough reported data to build the model.",
    "currency_mismatch": "This company reports in a currency other than US dollars.",
    "share_count_conflict": "Our share-count sources disagree too much to value one share.",
    "financial_company": (
        "Banks, insurers, asset managers and shell companies don't fit a cash-flow model."
    ),
    "reit": "REITs are valued on funds from operations, which this model doesn't use.",
    "regulated_utility": (
        "Regulated utilities are valued on their rate base and dividends, not free cash flow."
    ),
    "lender": "A lender's cash flow is its loan book, so a cash-flow model doesn't fit it.",
    "captive_finance": "This company's finance arm distorts its cash flow and debt.",
    "high_leverage": (
        "Debt is large relative to the company's market value, so an estimate would be unreliable."
    ),
    "company_changed_shape": (
        "The company changed shape recently (for example a spin-off), so its history doesn't "
        "describe the business being forecast."
    ),
    "negative_fcf": "Free cash flow has been negative or unstable.",
    "forecast_losses": "Analysts forecast a loss next year, so there is no cash flow to value.",
    "thin_coverage": "Too few analysts cover this company for a consensus forecast.",
    "unusual_margin": "The company's cash flow doesn't line up with its revenue well enough to model.",
    "unusual_conversion": (
        "The company's cash flow and earnings don't line up well enough to model."
    ),
    "unstable_consensus": (
        "Analysts' year-by-year forecasts swing too much to extend with confidence."
    ),
    "growth_out_of_range": "Forecast growth is too extreme to extrapolate with confidence.",
    "models_disagree": "Our two methods disagree too much to publish one estimate.",
    "model_error": "The model could not produce a valid estimate.",
}


# ── Pure model ───────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FiscalYear:
    end: date
    revenue: float
    net_income: float
    fcf: float
    sbc: Optional[float]             # None = not reported (FMP 0 or missing)
    interest_expense: Optional[float]


@dataclass(frozen=True)
class ConsensusYear:
    end: date
    revenue: Optional[float]
    net_income: Optional[float]
    analysts_revenue: int
    analysts_eps: int


@dataclass
class DcfInputs:
    ticker: str
    as_of: date
    price: Optional[float] = None
    market_cap: Optional[float] = None
    listing_currency: Optional[str] = None
    statement_currency: Optional[str] = None
    sector: str = ""
    industry: str = ""
    beta_raw: Optional[float] = None
    history: List[FiscalYear] = field(default_factory=list)        # oldest → newest
    consensus: List[ConsensusYear] = field(default_factory=list)   # after last reported FY
    # The consensus row for the LAST REPORTED year (within SAME_FY_DAYS of its end): the old
    # forecast window during the crossfade that follows a fiscal year-end (spec §1.3).
    consensus_reported: Optional[ConsensusYear] = None
    statement_currencies: Set[str] = field(default_factory=set)
    shares_diluted: Optional[float] = None
    total_debt: Optional[float] = None
    rf_avg_pct: Optional[float] = None
    rf_recent_pct: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    @property
    def last_fy_end(self) -> Optional[date]:
        return self.history[-1].end if self.history else None


@dataclass
class MethodValue:
    per_share: float
    pv_flows: float
    pv_terminal: float
    flows: List[float]
    end_dates: List[date]


@dataclass
class DcfResult:
    ticker: str
    as_of: date
    status: str                      # "ok" | "refused"
    refusal_code: Optional[str] = None
    fair_value: Optional[float] = None
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    alternative_value: Optional[float] = None
    discount_rate: Optional[float] = None
    terminal_growth: Optional[float] = None
    beta: Optional[float] = None
    rf_avg_pct: Optional[float] = None
    rf_rate_pct: Optional[float] = None       # the risk-free rate inside the discount rate
    analyst_years: Optional[int] = None
    analysts_min: Optional[int] = None
    terminal_share: Optional[float] = None
    conversion: Optional[float] = None
    margin: Optional[float] = None
    sbc_status: Optional[str] = None
    shares_diluted: Optional[float] = None
    last_fy_end: Optional[date] = None
    notes: List[str] = field(default_factory=list)

    @property
    def refusal_reason(self) -> Optional[str]:
        return REFUSAL_REASONS.get(self.refusal_code) if self.refusal_code else None


def _num(v: Any) -> Optional[float]:
    """Finite float or None. FMP sends numbers, numeric strings, nulls and the odd NaN."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _parse_date(v: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _rows(payload: Any) -> List[Dict[str, Any]]:
    return [r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []


def _filed_by(row: Dict[str, Any], as_of: date) -> bool:
    """True if a statement row was public on `as_of`. Live FMP only returns filed statements, so
    this matters for the replay; a row with no filing date is assumed public 90 days after its
    period end (conservative)."""
    filed = _parse_date(row.get("filingDate")) or _parse_date(row.get("acceptedDate"))
    end = _parse_date(row.get("date"))
    if filed is None:
        return end is not None and end + timedelta(days=90) <= as_of
    return filed <= as_of


def _add_year(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:            # 29 Feb
        return d.replace(year=d.year + 1, day=28)


def _dedupe_by_period(rows) -> List[Dict[str, Any]]:
    """One row per period end, the newest filing winning, oldest period first. FMP has served
    the same fiscal year twice (an original and an amendment); two copies of a year would
    count twice in every median and in the 2-of-5 negative-FCF rule."""
    def _filed_key(r: Dict[str, Any]) -> Tuple[str, str]:
        return (str(r.get("filingDate") or "")[:10], str(r.get("acceptedDate") or ""))

    best: Dict[date, Dict[str, Any]] = {}
    for r in rows:
        end = _parse_date(r.get("date"))
        if end not in best or _filed_key(r) >= _filed_key(best[end]):
            best[end] = r
    return [best[k] for k in sorted(best)]


def build_inputs(
    ticker: str,
    as_of: date,
    *,
    profile: Any,
    income_annual: Any,
    income_quarter: Any,
    cash_flow_annual: Any,
    balance_annual: Any,
    estimates: Any,
    rf_avg_pct: Optional[float],
    rf_recent_pct: Optional[float] = None,
    price: Optional[float] = None,
    market_cap: Optional[float] = None,
) -> DcfInputs:
    """FMP payloads → DcfInputs, using only rows public on `as_of`.

    `price` / `market_cap` override the profile's (the replay passes the historical ones).
    """
    prof = profile[0] if isinstance(profile, list) and profile else profile
    prof = prof if isinstance(prof, dict) else {}
    inp = DcfInputs(ticker=ticker.upper(), as_of=as_of, rf_avg_pct=rf_avg_pct,
                    rf_recent_pct=rf_recent_pct)
    inp.price = price if price is not None else _num(prof.get("price"))
    inp.market_cap = market_cap if market_cap is not None else _num(prof.get("marketCap"))
    inp.listing_currency = (str(prof.get("currency") or "") or None)
    inp.sector = str(prof.get("sector") or "")
    inp.industry = str(prof.get("industry") or "")
    # FMP sends beta 0 (not null) when it has too little price history: that is MISSING, and
    # flooring it to 0.8 would publish a value ~15 % too high with no note (spec §2).
    beta = _num(prof.get("beta"))
    inp.beta_raw = beta if beta not in (None, 0.0) else None

    cf_by_end: Dict[date, Dict[str, Any]] = {
        _parse_date(r.get("date")): r
        for r in _dedupe_by_period(
            r for r in _rows(cash_flow_annual) if _parse_date(r.get("date")) and _filed_by(r, as_of))
    }

    income = _dedupe_by_period(
        r for r in _rows(income_annual) if _parse_date(r.get("date")) and _filed_by(r, as_of))
    history: List[FiscalYear] = []
    for r in income:
        end = _parse_date(r.get("date"))
        cf = cf_by_end.get(end)
        revenue, ni = _num(r.get("revenue")), _num(r.get("netIncome"))
        if cf is None or revenue is None or ni is None:
            continue
        fcf = _num(cf.get("freeCashFlow"))
        if fcf is None:
            cfo, capex = _num(cf.get("operatingCashFlow")), _num(cf.get("capitalExpenditure"))
            if cfo is None or capex is None:
                continue
            fcf = cfo - abs(capex)
        sbc = _num(cf.get("stockBasedCompensation"))
        history.append(FiscalYear(
            end=end, revenue=revenue, net_income=ni, fcf=fcf,
            sbc=sbc if sbc is not None and sbc > 0 else None,
            interest_expense=_num(r.get("interestExpense")),
        ))
    inp.history = history[-HISTORY_YEARS:]
    kept = {y.end for y in inp.history}
    inp.statement_currencies = {
        str(r.get("reportedCurrency")) for r in income
        if _parse_date(r.get("date")) in kept and r.get("reportedCurrency")
    }
    if income:
        inp.statement_currency = str(income[-1].get("reportedCurrency") or "") or None

    # Shares: the latest QUARTER's diluted weighted average. The latest FISCAL-YEAR figure goes
    # stale with buybacks/issuance (CRM: 0.956 B FY vs 0.821 B latest quarter, a 16 % per-share
    # error in the prototype).
    # The NEWEST row with a POSITIVE count: a preliminary filing can carry 0 (COST's FY2026
    # row, filed 2026-09-24, has weightedAverageShsOutDil = 0).
    quarters = _dedupe_by_period(
        r for r in _rows(income_quarter) if _parse_date(r.get("date")) and _filed_by(r, as_of))

    def _latest_shares(rows: List[Dict[str, Any]]) -> Optional[float]:
        for r in reversed(rows):
            v = _num(r.get("weightedAverageShsOutDil"))
            if v and v > 0:
                return v
        return None

    q_shares, fy_shares = _latest_shares(quarters), _latest_shares(income)
    if q_shares:
        inp.shares_diluted = q_shares
    elif fy_shares:
        inp.shares_diluted = fy_shares
        inp.notes.append("latest quarterly share count unavailable; fiscal-year count used")
    # No reported diluted count at all → shares stay None → missing_data (spec §3). Deriving
    # them from market cap ÷ price would make share_count_conflict unable to fire.

    balances = sorted(
        (r for r in _rows(balance_annual) if _parse_date(r.get("date")) and _filed_by(r, as_of)),
        key=lambda r: str(r.get("date")),
    )
    if balances:
        inp.total_debt = _num(balances[-1].get("totalDebt"))

    # A consensus row within SAME_FY_DAYS of the last reported year-end IS that year: FMP dates
    # estimates on the nominal month-end while 52/53-week filers report the actual week-end
    # (COST: consensus 2026-08-31 vs statements 2026-08-30).
    last_end = inp.last_fy_end
    consensus: List[ConsensusYear] = []
    for r in _rows(estimates):
        end = _parse_date(r.get("date"))
        if end is None or last_end is None:
            continue
        row = ConsensusYear(
            end=end,
            revenue=_num(r.get("revenueAvg")),
            net_income=_num(r.get("netIncomeAvg")),
            analysts_revenue=int(_num(r.get("numAnalystsRevenue")) or 0),
            analysts_eps=int(_num(r.get("numAnalystsEps")) or 0),
        )
        if abs((end - last_end).days) <= SAME_FY_DAYS:
            inp.consensus_reported = row
        elif end > last_end:
            consensus.append(row)
    inp.consensus = sorted(consensus, key=lambda c: c.end)
    return inp


def bounded_beta(beta: Optional[float]) -> float:
    b = beta if beta is not None and math.isfinite(beta) else 1.0
    return min(max(b, BETA_MIN), BETA_MAX)


def cost_of_equity(rf_pct: float, beta: float) -> float:
    return rf_pct / 100.0 + beta * ERP_PCT / 100.0


def terminal_growth(rf_pct: float, rate: float) -> float:
    """rf, capped at 4 %, and never above r − 4 pts (spec §2)."""
    return min(rf_pct / 100.0, TERMINAL_G_CAP, rate - MIN_SPREAD)


def usable_consensus(
    consensus: Sequence[ConsensusYear], last_fy_end: date, basis: str,
) -> List[ConsensusYear]:
    """Contiguous fiscal years right after the last reported one, each with a positive value
    covered by ≥ MIN_ANALYSTS, capped at MAX_ANALYST_YEARS. The first gap ends the run."""
    out: List[ConsensusYear] = []
    prev = last_fy_end
    for c in consensus:
        gap = (c.end - prev).days
        value = c.net_income if basis == "earnings" else c.revenue
        analysts = c.analysts_eps if basis == "earnings" else c.analysts_revenue
        if not (_FY_GAP_DAYS[0] <= gap <= _FY_GAP_DAYS[1]):
            break
        if value is None or value <= 0 or analysts < MIN_ANALYSTS or len(out) >= MAX_ANALYST_YEARS:
            break
        out.append(c)
        prev = c.end
    return out


@dataclass(frozen=True)
class ConsensusTrend:
    growth: float           # fitted annual growth across the analyst years
    last_fitted: float      # the trend's value at the last analyst year
    max_deviation: float    # largest |actual / trend − 1| across the analyst years


def consensus_trend(
    values: Sequence[float], weights: Optional[Sequence[float]] = None,
) -> Optional[ConsensusTrend]:
    """Weighted least-squares line through ln(value) over the analyst years (spec §1 step 3),
    each year weighted by the number of analysts behind it.

    The first version seeded the extrapolation from the END-POINT CAGR and grew it from the RAW
    last analyst year, so one noisy far year (thin coverage, one-off gains) set the whole tail and
    the terminal value: the replay found UNH doubling in a quarter on a far year at 25.1 B after
    14.1 B, and QCOM +60 % overnight when its window picked up a 6-analyst year. Weighting by
    coverage lets a 20-analyst year outweigh a 6-analyst one. None with fewer than two positive
    years."""
    if len(values) < 2 or any(v <= 0 for v in values):
        return None
    ws = [max(float(w), 1.0) for w in weights] if weights else [1.0] * len(values)
    if len(ws) != len(values):
        ws = [1.0] * len(values)
    xs = list(range(len(values)))
    ys = [math.log(v) for v in values]
    tw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs)) / tw
    my = sum(w * y for w, y in zip(ws, ys)) / tw
    slope = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys)) / \
        sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    intercept = my - slope * mx
    fitted = [intercept + slope * x for x in xs]
    return ConsensusTrend(
        growth=math.exp(slope) - 1,
        last_fitted=math.exp(fitted[-1]),
        max_deviation=max(abs(math.exp(y - f) - 1) for y, f in zip(ys, fitted)),
    )


def project_flows(
    analyst_flows: Sequence[float], analyst_ends: Sequence[date], g: float,
    weights: Optional[Sequence[float]] = None,
) -> Tuple[List[float], List[date]]:
    """Analyst flows, then extrapolation to FORECAST_YEARS: growth starts at the analyst-years
    TREND growth (weighted by analyst count) and falls linearly to g; the path starts from the
    trend value at the last analyst year, never the raw last point (spec §1 step 3)."""
    flows, ends = list(analyst_flows), list(analyst_ends)
    trend = consensus_trend(flows, weights)
    start = g if trend is None else trend.growth
    remaining = FORECAST_YEARS - len(flows)
    value = flows[-1] if trend is None else trend.last_fitted
    end = ends[-1]
    for i in range(1, remaining + 1):
        growth = start + (g - start) * (i / remaining)
        value *= 1 + growth
        end = _add_year(end)
        flows.append(value)
        ends.append(end)
    return flows, ends


def present_value(
    flows: Sequence[float], ends: Sequence[date], as_of: date, last_fy_end: date,
    rate: float, g: float,
) -> Tuple[float, float]:
    """(PV of the yearly flows, PV of the terminal value). End-of-period discounting in actual
    days; the fiscal year in progress counts only its unelapsed fraction (spec §1.3)."""
    pv = 0.0
    prev = last_fy_end
    for cf, end in zip(flows, ends):
        length = (end - prev).days
        remaining = (end - as_of).days
        fraction = min(max(remaining / length, 0.0), 1.0) if length > 0 else 0.0
        if fraction > 0:
            pv += cf * fraction / (1 + rate) ** (remaining / 365.25)
        prev = end
    terminal = flows[-1] * (1 + g) / (rate - g)
    t_end = max((ends[-1] - as_of).days, 0) / 365.25
    return pv, terminal / (1 + rate) ** t_end


def _median(values: Sequence[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def ratio_spread(values: Sequence[float]) -> Optional[float]:
    """How far apart a company's own trailing ratios are: 2nd-highest ÷ 2nd-lowest with 4-5 years
    (one odd year is tolerated), max ÷ min with 3. A 5-year median of a history that disagrees
    with itself flips when one old year leaves the window: in the Phase-2 replay CSX's conversion
    went 0.41 → 0.88 at one annual report (old years ~0.3, recent ~0.9) and its value doubled."""
    xs = sorted(v for v in values if v > 0)
    if len(xs) < 3:
        return None
    lo, hi = (xs[1], xs[-2]) if len(xs) >= 4 else (xs[0], xs[-1])
    return hi / lo


def _refuse(inp: DcfInputs, code: str, **extra: Any) -> DcfResult:
    # A refusal carries no parameters of an estimate that does not exist (the sheet would
    # otherwise list a discount rate and "estimated on" for nothing) — only its code and date.
    res = DcfResult(ticker=inp.ticker, as_of=inp.as_of, status="refused", refusal_code=code,
                    last_fy_end=inp.last_fy_end)
    for k, v in extra.items():
        setattr(res, k, v)
    return res


def _basis_values(analyst: Sequence[ConsensusYear], basis: str) -> Tuple[List[float], List[float]]:
    """(values, analyst counts) of the usable consensus years for one basis."""
    if basis == "earnings":
        return [float(c.net_income) for c in analyst], [float(c.analysts_eps) for c in analyst]
    return [float(c.revenue) for c in analyst], [float(c.analysts_revenue) for c in analyst]


def _method_value(
    inp: DcfInputs, analyst: List[ConsensusYear], basis: str, factor: float,
    rate: float, g: float, start: date,
) -> MethodValue:
    values, counts = _basis_values(analyst, basis)
    analyst_flows = [v * factor for v in values]
    flows, ends = project_flows(analyst_flows, [c.end for c in analyst], g, counts)
    pv, pv_tv = present_value(flows, ends, inp.as_of, start, rate, g)
    return MethodValue(per_share=(pv + pv_tv) / inp.shares_diluted, pv_flows=pv,
                       pv_terminal=pv_tv, flows=flows, end_dates=ends)


@dataclass
class _Window:
    start: date
    analyst_e: List[ConsensusYear]
    analyst_r: List[ConsensusYear]


def _check_window(start: date, forward: Sequence[ConsensusYear]) -> Tuple[Optional[_Window], Optional[str]]:
    """The forecast-window rules of spec §3: forecast_losses, thin_coverage, then
    unstable_consensus for BOTH bases before growth_out_of_range for either."""
    nxt = forward[0] if forward else None
    if (nxt is not None and _FY_GAP_DAYS[0] <= (nxt.end - start).days <= _FY_GAP_DAYS[1]
            and nxt.analysts_eps >= MIN_ANALYSTS
            and nxt.net_income is not None and nxt.net_income <= 0):
        return None, "forecast_losses"
    analyst_e = usable_consensus(forward, start, "earnings")
    analyst_r = usable_consensus(forward, start, "revenue")
    if not analyst_e or not analyst_r:
        return None, "thin_coverage"
    trends = [consensus_trend(*_basis_values(a, b))
              for b, a in (("earnings", analyst_e), ("revenue", analyst_r))]
    for t, a in zip(trends, (analyst_e, analyst_r)):
        if t is not None and len(a) >= 3 and t.max_deviation > MAX_TREND_DEVIATION:
            return None, "unstable_consensus"
    for t in trends:
        if t is not None and not (GROWTH_BOUNDS[0] <= t.growth <= GROWTH_BOUNDS[1]):
            return None, "growth_out_of_range"
    return _Window(start=start, analyst_e=analyst_e, analyst_r=analyst_r), None


def value_company(inp: DcfInputs, *, rate_basis: str = RATE_RF_BASIS) -> DcfResult:
    """The whole of spec §§1-3. Returns a refusal rather than a number whenever a rule fires.

    Never mutates `inp` (the replay values the same inputs several times). `rate_basis` exists
    only for the Phase-2 replay's research variants; production callers pass nothing."""
    notes = list(inp.notes)
    # ── eligibility (spec §3 order; first match wins) ──
    if not inp.price or inp.price <= 0 or len(inp.history) < MIN_HISTORY_YEARS \
            or inp.rf_avg_pct is None:
        return _refuse(inp, "missing_data")
    if not inp.shares_diluted or inp.shares_diluted <= 0:
        return _refuse(inp, "missing_data")
    currencies = set(inp.statement_currencies)
    if inp.statement_currency:
        currencies.add(inp.statement_currency)
    if not inp.listing_currency and not currencies:
        return _refuse(inp, "missing_data")          # unknown currency: fail closed
    if (inp.listing_currency and inp.listing_currency != "USD") or currencies - {"USD"}:
        return _refuse(inp, "currency_mismatch")
    if inp.market_cap and inp.market_cap > 0:
        implied = inp.market_cap / inp.price
        drift = abs(inp.shares_diluted / implied - 1)
        if drift > SHARE_CONFLICT:
            return _refuse(inp, "share_count_conflict")
        if drift > 0.10:
            notes.append(f"share count differs from market cap ÷ price by {drift:.0%}")
    if inp.industry.startswith(FINANCIAL_INDUSTRY_PREFIXES):
        return _refuse(inp, "financial_company")
    if inp.industry.startswith(REIT_INDUSTRY_PREFIX):
        return _refuse(inp, "reit")
    if inp.sector == UTILITIES_SECTOR:
        return _refuse(inp, "regulated_utility")
    last = inp.history[-1]
    if inp.industry == CREDIT_SERVICES_INDUSTRY:
        if last.interest_expense is None:
            return _refuse(inp, "missing_data")      # cannot tell a network from a lender
        interest = abs(last.interest_expense)         # FMP's sign varies by issuer
        if last.revenue > 0 and interest / last.revenue > LENDER_INTEREST_SHARE:
            return _refuse(inp, "lender")
    if inp.ticker in CAPTIVE_FINANCE_TICKERS:
        return _refuse(inp, "captive_finance")
    market_cap = inp.market_cap if inp.market_cap and inp.market_cap > 0 \
        else inp.price * inp.shares_diluted
    if inp.total_debt is not None and inp.total_debt > MAX_DEBT_TO_MARKET_CAP * market_cap:
        return _refuse(inp, "high_leverage")
    # Revenue path: the reported years, then the contiguous consensus years. A fall of more than
    # SHAPE_MAX_YOY_DROP anywhere on it means the history does not describe the company being
    # forecast (a spin-off: HON's 2026 consensus is half its 2025 revenue) or a collapse.
    path = [y.revenue for y in inp.history]
    prev_end = last.end
    for c in inp.consensus:
        if not (_FY_GAP_DAYS[0] <= (c.end - prev_end).days <= _FY_GAP_DAYS[1]) or c.revenue is None:
            break
        path.append(c.revenue)
        prev_end = c.end
    for a, b in zip(path, path[1:]):
        if a > 0 and b < (1 - SHAPE_MAX_YOY_DROP) * a:
            return _refuse(inp, "company_changed_shape")

    # ── stock-based compensation (spec §1.2) and negative FCF over EVERY year ──
    sbc_years = sum(1 for y in inp.history if y.sbc is not None)
    deduct = sbc_years >= MIN_SBC_YEARS
    sbc_status = "deducted" if deduct else "not_reported"
    # FCF − SBC where SBC is known, raw FCF where it is not (raw ≤ 0 already means FCF − SBC ≤ 0):
    # a year with no SBC figure must not drop out of this rule (a preliminary latest row).
    cash = [y.fcf - (y.sbc or 0.0) if deduct else y.fcf for y in inp.history]
    if cash[-1] <= 0 or sum(1 for c in cash if c <= 0) >= 2:
        return _refuse(inp, "negative_fcf")
    # Only the MEDIANS are restricted to years with a reported SBC figure.
    usable = [y for y in inp.history if y.sbc is not None] if deduct else list(inp.history)
    adj = [(y, y.fcf - (y.sbc or 0.0) if deduct else y.fcf) for y in usable]
    if len(adj) < MIN_HISTORY_YEARS:
        return _refuse(inp, "missing_data")

    # ── the forecast window, and its crossfade after a fiscal year-end (spec §1.3) ──
    # A fiscal year that has ENDED but is not yet reported carries no weight and takes no
    # analyst-year slot, so the window moves forward at the fiscal year-end. That move swaps an
    # extrapolated far year for a consensus one, which on its own made QCOM +60 % overnight; the
    # value therefore crossfades from the old window to the new over ROLL_BLEND_DAYS. The old
    # window is the one that still holds the ended year (as a zero-weight first slot), before and
    # after that year's 10-K — so the filing itself does not move the window either.
    new_start, forward = last.end, list(inp.consensus)
    popped: List[ConsensusYear] = []
    while forward and forward[0].end <= inp.as_of \
            and _FY_GAP_DAYS[0] <= (forward[0].end - new_start).days <= _FY_GAP_DAYS[1]:
        popped.append(forward.pop(0))
        new_start = popped[-1].end
    old: Optional[Tuple[date, List[ConsensusYear]]] = None
    rolled_at: Optional[date] = None
    if popped:
        # The window in use just before the LATEST roll: it starts at the previous ended year
        # (or the last reported one) and still holds the latest ended year as its first slot.
        old_start = popped[-2].end if len(popped) >= 2 else last.end
        old, rolled_at = (old_start, [popped[-1]] + forward), popped[-1].end
    elif inp.consensus_reported is not None and len(inp.history) >= 2:
        # After the 10-K: same old window, rebuilt from that year's consensus row. The clock is
        # anchored on the CONSENSUS row's date in both cases, so a 52/53-week filer (statement
        # 08-30, consensus 08-31) does not shift the crossfade weight on its filing day.
        old = (inp.history[-2].end, [inp.consensus_reported] + list(inp.consensus))
        rolled_at = inp.consensus_reported.end
    weight_new = 1.0
    if rolled_at is not None:
        weight_new = min(max((inp.as_of - rolled_at).days / ROLL_BLEND_DAYS, 0.0), 1.0)
    window, code = _check_window(new_start, forward)
    if window is None:
        return _refuse(inp, code or "thin_coverage")
    old_window = None
    if old is not None and weight_new < 1.0:
        old_window, _ = _check_window(*old)          # an old window that fails its rules is ignored
    if old_window is None:
        weight_new = 1.0

    margins = [a / y.revenue for y, a in adj if y.revenue > 0]
    margin = _median(margins)
    spread = ratio_spread(margins)
    if margin is None or not (MARGIN_BOUNDS[0] <= margin <= MARGIN_BOUNDS[1]) \
            or (spread is not None and spread > MAX_RATIO_SPREAD):
        return _refuse(inp, "unusual_margin")
    # Consensus net income is on the analysts' ADJUSTED basis (SBC added back), statements are
    # GAAP: measure conversion against NI + SBC. With SBC unreported this degrades to FCF / NI.
    ratios: List[float] = []
    for y, a in adj:
        earnings = y.net_income + (y.sbc or 0.0) if deduct else y.net_income
        if a > 0 and earnings > 0:
            ratios.append(a / earnings)
    conversion = _median(ratios) if len(ratios) >= MIN_HISTORY_YEARS else None
    c_spread = ratio_spread(ratios)
    if conversion is None or not (CONVERSION_BOUNDS[0] <= conversion <= CONVERSION_BOUNDS[1]) \
            or (c_spread is not None and c_spread > MAX_RATIO_SPREAD):
        return _refuse(inp, "unusual_conversion")

    # ── rates (spec §2) ──
    beta = bounded_beta(inp.beta_raw)
    if inp.beta_raw is None:
        notes.append("beta unavailable; market beta 1.0 used")
    rate_rf = {"avg5": inp.rf_avg_pct, "recent": inp.rf_recent_pct,
               "erp_vintage": RF_AT_ERP_PCT}.get(rate_basis)
    if rate_rf is None:
        return _refuse(inp, "missing_data")
    rate = cost_of_equity(rate_rf, beta)
    g = terminal_growth(inp.rf_avg_pct, rate)
    up, down = rate + SENSITIVITY_STEP, rate - SENSITIVITY_STEP
    g_up, g_down = terminal_growth(inp.rf_avg_pct, up), terminal_growth(inp.rf_avg_pct, down)

    # ── the two methods (spec §1.1), each blended across the roll ──
    def _values(w: _Window) -> Dict[str, MethodValue]:
        return {
            "e": _method_value(inp, w.analyst_e, "earnings", conversion, rate, g, w.start),
            "r": _method_value(inp, w.analyst_r, "revenue", margin, rate, g, w.start),
            "e_up": _method_value(inp, w.analyst_e, "earnings", conversion, up, g_up, w.start),
            "e_down": _method_value(inp, w.analyst_e, "earnings", conversion, down, g_down, w.start),
        }

    new_v = _values(window)
    old_v = _values(old_window) if old_window is not None else None

    def blend(key: str, attr: str = "per_share") -> float:
        v = getattr(new_v[key], attr)
        return v if old_v is None else weight_new * v + (1 - weight_new) * getattr(old_v[key], attr)

    e, r, e_up, e_down = blend("e"), blend("r"), blend("e_up"), blend("e_down")
    pv_flows, pv_terminal = blend("e", "pv_flows"), blend("e", "pv_terminal")
    if old_v is not None:
        notes.append(f"forecast window rolling forward after the fiscal year ended {rolled_at.isoformat()} "
                     f"({weight_new:.0%} of the way)")
    if not deduct:
        notes.append("stock-based pay not reported by the data source; not deducted")
    common = dict(sbc_status=sbc_status, conversion=conversion, margin=margin, beta=beta,
                  discount_rate=rate, terminal_growth=g, rf_rate_pct=rate_rf, notes=notes)
    if not all(math.isfinite(v) and v > 0 for v in (e, r, e_up, e_down)):
        return _refuse(inp, "model_error")
    if not (1 / MAX_DISAGREEMENT <= e / r <= MAX_DISAGREEMENT):
        return _refuse(inp, "models_disagree")

    return DcfResult(
        ticker=inp.ticker, as_of=inp.as_of, status="ok",
        fair_value=e,
        range_low=min(r, e_up),
        range_high=max(r, e_down),
        alternative_value=r,
        rf_avg_pct=inp.rf_avg_pct,
        analyst_years=len(window.analyst_e),
        analysts_min=min(c.analysts_eps for c in window.analyst_e),
        terminal_share=pv_terminal / (pv_flows + pv_terminal),
        shares_diluted=inp.shares_diluted,
        last_fy_end=last.end,
        **common,
    )


def rf_average_pct(observations: Sequence[Any], as_of: date) -> Optional[float]:
    """Mean of the 10-year yield over the RF_AVERAGE_YEARS before `as_of` (FRED DGS10 rows with
    `.date` / `.value`, any order). None when the window is under 80 % covered."""
    start = as_of - timedelta(days=round(365.25 * RF_AVERAGE_YEARS))
    values = [
        o.value for o in observations
        if (d := _parse_date(getattr(o, "date", None))) is not None and start < d <= as_of
        and getattr(o, "value", None) is not None and math.isfinite(o.value)
    ]
    expected = 252 * RF_AVERAGE_YEARS
    if len(values) < 0.8 * expected:
        return None
    return statistics.fmean(values)


def rf_recent_pct(observations: Sequence[Any], as_of: date) -> Optional[float]:
    """Mean of the 10-year yield over the RF_RECENT_DAYS before `as_of` (research variant)."""
    start = as_of - timedelta(days=RF_RECENT_DAYS)
    values = [
        o.value for o in observations
        if (d := _parse_date(getattr(o, "date", None))) is not None and start < d <= as_of
        and getattr(o, "value", None) is not None and math.isfinite(o.value)
    ]
    return statistics.fmean(values) if len(values) >= 40 else None


def _round(v: Optional[float], nd: int = 2) -> Optional[float]:
    return None if v is None else round(v, nd)


def to_response(res: DcfResult) -> DcfFairValueResponse:
    pct = lambda v: None if v is None else round(v * 100, 2)  # noqa: E731
    return DcfFairValueResponse(
        symbol=res.ticker,
        status=res.status,  # type: ignore[arg-type]
        model_version=MODEL_VERSION,
        refusal_code=res.refusal_code,
        refusal_reason=res.refusal_reason,
        fair_value=_round(res.fair_value),
        range_low=_round(res.range_low),
        range_high=_round(res.range_high),
        alternative_value=_round(res.alternative_value),
        currency="USD" if res.status == "ok" else None,
        method=METHOD_LABEL if res.status == "ok" else None,
        discount_rate_pct=pct(res.discount_rate),
        terminal_growth_pct=pct(res.terminal_growth),
        risk_free_pct=_round(res.rf_rate_pct),
        equity_risk_premium_pct=ERP_PCT if res.status == "ok" else None,
        beta=_round(res.beta),
        analyst_years=res.analyst_years,
        analysts_min=res.analysts_min,
        terminal_share_pct=pct(res.terminal_share),
        cash_conversion=_round(res.conversion, 3),
        fcf_margin_pct=pct(res.margin),
        sbc_status=res.sbc_status,  # type: ignore[arg-type]
        shares_diluted=res.shares_diluted,
        last_reported_fiscal_year_end=res.last_fy_end.isoformat() if res.last_fy_end else None,
        as_of=res.as_of.isoformat(),
        notes=res.notes or None,
    )


# ── Service: two-tier cache-aside + in-flight dedup ─────────────────────────────────────────

_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")
_CACHE_TTL = 300
_CACHE_MAX_ENTRIES = 1024
_STORED_TTL = timedelta(hours=24)
_cache: Dict[str, Tuple[float, DcfFairValueResponse]] = {}
_inflight: Dict[str, asyncio.Future] = {}
# A FAILURE memo, not a cached value: while an input is unavailable (FRED down, FRED_API_KEY
# unset, an FMP leg failing), callers re-raise for this long instead of re-spending the six FMP
# calls on every Analysis-tab view (review round: shadow mode would otherwise burn quota).
_FAILURE_TTL = 180
_failed_at: Dict[str, float] = {}


class DcfInputsUnavailableError(FMPUnavailableException):
    """An input the model cannot do without (a statement, the consensus, the Treasury average)
    could not be fetched. Transient: nothing is cached, and it maps to the existing retryable
    "data provider unavailable" error. It is NOT a refusal, and must never be stored as one."""


def _cache_get(key: str) -> Optional[DcfFairValueResponse]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: DcfFairValueResponse) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(old, None)


def _today_et() -> date:
    return datetime.now(tz=ET).date()


class DcfFairValueService:
    """Takes a ticker and nothing else — see HARD RULE 1 in the module docstring."""

    def __init__(self) -> None:
        self.fmp = get_fmp_client()
        self.fred = get_fred_client()
        self._supabase = None

    @property
    def supabase(self):
        if self._supabase is None:
            self._supabase = get_supabase()
        return self._supabase

    async def get_fair_value(self, ticker: str) -> DcfFairValueResponse:
        ticker = ticker.upper().strip()
        if not _TICKER_RE.match(ticker):
            raise ValueError(f"Invalid ticker symbol: {ticker!r}")
        # Keyed to the ET DATE: every cached value is today's, so every surface that reads it
        # on a given day (Analysis tab, report, chat, PDF) shows the same number, and the
        # history table gets one row per ticker per day it is viewed.
        key = f"dcf:{MODEL_VERSION}:{_today_et().isoformat()}:{ticker}"

        cached = _cache_get(key)
        if cached is not None:
            return cached
        persist = bool(settings.DCF_ENABLED or settings.DCF_SHADOW)
        if persist:
            stored = await asyncio.to_thread(self._read_stored, ticker)
            if stored is not None:
                _cache_set(key, stored)
                return stored

        failed = _failed_at.get(key)
        if failed is not None and time.time() - failed < _FAILURE_TTL:
            raise DcfInputsUnavailableError(f"DCF inputs recently unavailable for {ticker}")
        if key in _inflight:
            return await asyncio.shield(_inflight[key])
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        _inflight[key] = future
        try:
            response, inputs_log = await self._compute(ticker)
            if persist:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, self._write_stored, ticker, response)
                loop.run_in_executor(None, self._append_history, response, inputs_log)
            _cache_set(key, response)
            if not future.done():
                future.set_result(response)
            return response
        except asyncio.CancelledError:
            fail_shared_future(future, RuntimeError("in-flight DCF computation was cancelled"))
            raise
        except Exception as e:
            if isinstance(e, DcfInputsUnavailableError):
                _failed_at[key] = time.time()
            fail_shared_future(future, e)
            raise
        finally:
            _inflight.pop(key, None)

    async def _compute(self, ticker: str) -> Tuple[DcfFairValueResponse, Dict[str, Any]]:
        as_of = _today_et()
        # The Treasury average first: without it nothing can be valued, so do not spend the six
        # FMP calls (FRED answers [] at once when its key is unset or it failed recently).
        dgs10 = await self.fred.get_observations("DGS10", limit=1400)
        rf = rf_average_pct(dgs10, as_of)
        if rf is None:
            raise DcfInputsUnavailableError(f"10-year Treasury average unavailable for {ticker}")
        results = await asyncio.gather(
            self.fmp.get_company_profile(ticker),
            self.fmp.get_income_statement(ticker, period="annual", limit=HISTORY_YEARS + 1),
            self.fmp.get_income_statement(ticker, period="quarter", limit=4),
            self.fmp.get_cash_flow_statement(ticker, period="annual", limit=HISTORY_YEARS + 1),
            self.fmp.get_balance_sheet(ticker, period="annual", limit=1),
            self.fmp.get_analyst_estimates(ticker, period="annual", limit=12),
            return_exceptions=True,
        )
        names = ("profile", "income_annual", "income_quarter", "cash_flow_annual",
                 "balance_annual", "estimates")
        payload = dict(zip(names, results))
        # The quarterly income statement only refines the share count (build_inputs falls back
        # to the fiscal-year figure); every other leg is essential. A missing essential leg is a
        # FAILURE, not a refusal: a refusal is cached for a day and recorded in the history.
        if isinstance(payload["income_quarter"], BaseException):
            logger.warning("dcf: quarterly income fetch failed for %s (%s: %s) — using FY shares",
                           ticker, type(payload["income_quarter"]).__name__, payload["income_quarter"])
            payload["income_quarter"] = []
        failed = [n for n in names if isinstance(payload[n], BaseException)]
        if failed:
            for n in failed:
                logger.warning("dcf: %s fetch failed for %s (%s: %s)", n, ticker,
                               type(payload[n]).__name__, payload[n])
            raise DcfInputsUnavailableError(f"DCF inputs unavailable for {ticker}: {', '.join(failed)}")
        inp = build_inputs(
            ticker, as_of,
            profile=payload["profile"], income_annual=payload["income_annual"],
            income_quarter=payload["income_quarter"], cash_flow_annual=payload["cash_flow_annual"],
            balance_annual=payload["balance_annual"], estimates=payload["estimates"],
            rf_avg_pct=rf, rf_recent_pct=rf_recent_pct(dgs10, as_of),
        )
        result = value_company(inp)
        logger.info("dcf: %s %s %s", ticker, result.status,
                    result.refusal_code or f"{result.fair_value:.2f}")
        return to_response(result), _inputs_log(inp)

    # ── Supabase (sync; always called off the event loop) ──

    def _read_stored(self, ticker: str) -> Optional[DcfFairValueResponse]:
        try:
            rows = (
                self.supabase.table("dcf_fair_value_cache")
                .select("response_json, model_version, computed_at")
                .eq("ticker", ticker).limit(1).execute()
            ).data
            if not rows:
                return None
            row = rows[0]
            if row.get("model_version") != MODEL_VERSION:
                return None
            computed = datetime.fromisoformat(str(row["computed_at"]).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - computed > _STORED_TTL:
                return None
            stored = DcfFairValueResponse.model_validate(row["response_json"])
            if stored.as_of != _today_et().isoformat():
                return None                     # yesterday's value: recompute (and record) today
            return stored
        except Exception as e:
            logger.warning("dcf: tier-2 read failed for %s (%s: %s)", ticker, type(e).__name__, e)
            return None

    def _write_stored(self, ticker: str, response: DcfFairValueResponse) -> None:
        try:
            self.supabase.table("dcf_fair_value_cache").upsert({
                "ticker": ticker,
                "model_version": MODEL_VERSION,
                "response_json": response.model_dump(),
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="ticker").execute()
        except Exception as e:
            logger.warning("dcf: tier-2 write failed for %s (%s: %s)", ticker, type(e).__name__, e)

    def _append_history(self, response: DcfFairValueResponse, inputs_log: Dict[str, Any]) -> None:
        """Append-only: one row per (ticker, ET date, model version); a repeat the same day is a
        no-op. This table is the only record of what the forecast was on a given day."""
        try:
            self.supabase.table("dcf_fair_value_history").upsert({
                "ticker": response.symbol,
                "as_of_date": response.as_of,
                "model_version": MODEL_VERSION,
                "status": response.status,
                "refusal_code": response.refusal_code,
                "fair_value": response.fair_value,
                "range_low": response.range_low,
                "range_high": response.range_high,
                "alternative_value": response.alternative_value,
                "price": inputs_log.get("price"),
                "inputs": inputs_log,
            }, on_conflict="ticker,as_of_date,model_version", ignore_duplicates=True).execute()
        except Exception as e:
            logger.warning("dcf: history append failed for %s (%s: %s)",
                           response.symbol, type(e).__name__, e)


def _inputs_log(inp: DcfInputs) -> Dict[str, Any]:
    """Everything the value depended on, for the history row (JSON-safe)."""
    return {
        "price": inp.price,
        "market_cap": inp.market_cap,
        "beta_raw": inp.beta_raw,
        "rf_avg_pct": inp.rf_avg_pct,
        "rf_recent_pct": inp.rf_recent_pct,
        "rate_basis": RATE_RF_BASIS,
        "rf_at_erp_pct": RF_AT_ERP_PCT,
        "erp_pct": ERP_PCT,
        "erp_vintage": ERP_VINTAGE,
        "shares_diluted": inp.shares_diluted,
        "total_debt": inp.total_debt,
        "sector": inp.sector,
        "industry": inp.industry,
        "history": [
            {"end": y.end.isoformat(), "revenue": y.revenue, "net_income": y.net_income,
             "fcf": y.fcf, "sbc": y.sbc, "interest_expense": y.interest_expense}
            for y in inp.history
        ],
        "consensus": [_consensus_log(c) for c in inp.consensus],
        # The old forecast window after a 10-K is rebuilt from this row (spec §1.3 crossfade);
        # without it a crossfaded value could not be reproduced from the history row.
        "consensus_reported": _consensus_log(inp.consensus_reported) if inp.consensus_reported else None,
        "listing_currency": inp.listing_currency,
        "statement_currencies": sorted(inp.statement_currencies),
    }


def _consensus_log(c: ConsensusYear) -> Dict[str, Any]:
    return {"end": c.end.isoformat(), "revenue": c.revenue, "net_income": c.net_income,
            "analysts_revenue": c.analysts_revenue, "analysts_eps": c.analysts_eps}


_service: Optional[DcfFairValueService] = None


def get_dcf_fair_value_service() -> DcfFairValueService:
    global _service
    if _service is None:
        _service = DcfFairValueService()
    return _service
