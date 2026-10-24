"""Caydex Fair Value Estimate — model dcf-v2 (app/services/dcf_fair_value_service.py).

Spec: documents/research/dcf-methodology-v2.md (v1 superseded, kept frozen). These tests pin:
  * the arithmetic (Simply Wall St's published Apple case, closed-form checks, monotonicity);
  * the timing convention (no jump at a fiscal-year end or at the annual-report roll);
  * every refusal rule, each with a passing sibling so a rule that fires on everything fails;
  * the v2 rules (S1 recovered drop, S2 growth band + tail cap, S3 recent ratios, the consensus
    share re-base), each with a firing test and a passing sibling, and v2 with all four switched
    off reproducing v1's numbers exactly;
  * SBC handling, point-in-time filtering, share-count and consensus-date edge cases;
  * HARD RULE 1 (nothing per-user can reach the value) and HARD RULE 5 (spec text ↔ constants);
  * the service: flag-gated Supabase access, no caching of a degraded fetch, in-flight dedup.
Hermetic: FMP, FRED and Supabase are stubbed.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import json
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

import app.services.dcf_fair_value_service as dcf
from app.schemas.dcf_fair_value import DcfFairValueResponse

AS_OF = date(2026, 9, 25)
_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / "documents" / "research" / "dcf-methodology-v2.md"
_SPEC_V1 = _ROOT / "documents" / "research" / "dcf-methodology-v1.md"
_SERVICE_SRC = Path(dcf.__file__)


# ── synthetic company ────────────────────────────────────────────────────────────────────────
# Calendar fiscal year, last reported FY2025 (filed 2026-02-15). Revenue 50 B growing 5 %/yr;
# NI 20 %, FCF 18 %, SBC 1 % of revenue → margin (FCF−SBC)/rev = 17 %, conversion
# (FCF−SBC)/(NI+SBC) = 0.81. Consensus: revenue +6 %/yr, NI 20 % of revenue, 20 analysts.
# 1 B shares at $100 (market cap 100 B), debt 10 B.

def _payloads(**tweak: Any) -> Dict[str, Any]:
    revs = {2021: 50e9 / 1.05 ** 4, 2022: 50e9 / 1.05 ** 3, 2023: 50e9 / 1.05 ** 2,
            2024: 50e9 / 1.05, 2025: 50e9}
    income, cash = [], []
    for y, rev in revs.items():
        income.append({"date": f"{y}-12-31", "filingDate": f"{y + 1}-02-15", "revenue": rev,
                       "netIncome": 0.20 * rev, "weightedAverageShsOutDil": 1e9,
                       "interestExpense": 0.01 * rev, "reportedCurrency": "USD"})
        cash.append({"date": f"{y}-12-31", "filingDate": f"{y + 1}-02-15",
                     "freeCashFlow": 0.18 * rev, "stockBasedCompensation": 0.01 * rev})
    est = []
    for i, y in enumerate(range(2026, 2031), start=1):
        rev = 50e9 * 1.06 ** i
        est.append({"date": f"{y}-12-31", "revenueAvg": rev, "netIncomeAvg": 0.20 * rev,
                    "numAnalystsRevenue": 20, "numAnalystsEps": 20})
    # past-year rows FMP also returns — must be ignored
    est += [{"date": "2025-12-31", "revenueAvg": 50e9, "netIncomeAvg": 10e9,
             "numAnalystsRevenue": 30, "numAnalystsEps": 30},
            {"date": "2024-12-31", "revenueAvg": 47e9, "netIncomeAvg": 9e9,
             "numAnalystsRevenue": 30, "numAnalystsEps": 30}]
    p = {
        "ticker": "ACME",
        "profile": {"price": 100.0, "marketCap": 100e9, "currency": "USD",
                    "sector": "Industrials", "industry": "Industrial - Machinery", "beta": 1.0},
        "income_annual": income,
        "income_quarter": [
            {"date": "2026-03-31", "filingDate": "2026-05-01", "weightedAverageShsOutDil": 1e9},
            {"date": "2026-06-30", "filingDate": "2026-08-01", "weightedAverageShsOutDil": 1e9},
        ],
        "cash_flow_annual": cash,
        "balance_annual": [{"date": "2025-12-31", "filingDate": "2026-02-15", "totalDebt": 10e9}],
        "estimates": est,
        "rf": 3.8,
        "rf_recent": 5.2,
    }
    for k, v in tweak.items():
        p[k] = v
    return p


def _inputs(p: Dict[str, Any], as_of: date = AS_OF) -> dcf.DcfInputs:
    return dcf.build_inputs(
        p["ticker"], as_of, profile=p["profile"], income_annual=p["income_annual"],
        income_quarter=p["income_quarter"], cash_flow_annual=p["cash_flow_annual"],
        balance_annual=p["balance_annual"], estimates=p["estimates"], rf_avg_pct=p["rf"],
        rf_recent_pct=p["rf_recent"],
    )


def _value(p: Dict[str, Any], as_of: date = AS_OF, **kw: Any) -> dcf.DcfResult:
    return dcf.value_company(_inputs(p, as_of), **kw)


def _edit(p: Dict[str, Any], key: str, fn) -> Dict[str, Any]:
    q = copy.deepcopy(p)
    for row in q[key]:
        fn(row)
    return q


# ── arithmetic ───────────────────────────────────────────────────────────────────────────────

def test_simply_wall_st_published_apple_case_reproduces():
    """SWS, May 2025: ten levered-FCF values, r 8.1 %, g 2.9 % → PV flows ≈ $1.1 T, PV terminal
    ≈ $2.1 T, equity ≈ $3.2 T, $218/share. Our discounting is in actual days, so allow 1 %."""
    flows = [109.3, 125.8, 139.0, 163.6, 179.3, 191.5, 202.2, 212.0, 221.0, 229.5]
    start = date(2024, 9, 28)
    ends, d = [], start
    for _ in flows:
        d = dcf._add_year(d)
        ends.append(d)
    pv, pv_tv = dcf.present_value(flows, ends, start, start, 0.081, 0.029)
    assert pv == pytest.approx(1126, rel=0.01)
    assert pv_tv == pytest.approx(2084, rel=0.01)
    assert (pv + pv_tv) / 14.73 == pytest.approx(218, rel=0.01)   # implied at 14.73 B shares


def test_present_value_matches_closed_form_for_whole_years():
    flows = [10.0] * 10
    start = date(2021, 1, 1)
    ends, d = [], start
    for _ in flows:
        d = dcf._add_year(d)
        ends.append(d)
    r, g = 0.09, 0.03
    pv, pv_tv = dcf.present_value(flows, ends, start, start, r, g)
    t = [(e - start).days / 365.25 for e in ends]
    assert pv == pytest.approx(sum(10 / (1 + r) ** ti for ti in t), rel=1e-12)
    assert pv_tv == pytest.approx(10 * (1 + g) / (r - g) / (1 + r) ** t[-1], rel=1e-12)


def test_the_year_in_progress_counts_only_its_unelapsed_fraction():
    ends = [date(2026, 12, 31), date(2027, 12, 31)]
    last = date(2025, 12, 31)
    as_of = date(2026, 7, 2)                         # ~half of FY2026 left
    pv, _ = dcf.present_value([100.0, 0.0], ends, as_of, last, 0.0, -0.5)
    frac = (ends[0] - as_of).days / (ends[0] - last).days
    assert pv == pytest.approx(100 * frac, rel=1e-9)
    assert 0.49 < frac < 0.51


def test_value_falls_as_the_discount_rate_rises_and_as_beta_rises():
    base = _value(_payloads())
    assert base.status == "ok", base.refusal_code
    # the avg5 research variant moves r with the 5-year average; the production pair is fixed
    assert _value(_payloads(rf=4.3), rate_basis="avg5").fair_value < \
        _value(_payloads(rf=3.8), rate_basis="avg5").fair_value
    riskier = _payloads()
    riskier["profile"] = {**riskier["profile"], "beta": 1.4}
    assert _value(riskier).fair_value < base.fair_value


def test_terminal_growth_respects_cap_and_minimum_spread():
    assert dcf.terminal_growth(3.0, 0.10) == pytest.approx(0.03)          # rf
    assert dcf.terminal_growth(6.0, 0.12) == pytest.approx(0.04)          # capped at 4 %
    assert dcf.terminal_growth(3.8, 0.072) == pytest.approx(0.032)        # r − 4 pts
    for beta in (0.8, 1.0, 1.5, 2.0):
        r = dcf.cost_of_equity(3.8, beta)
        assert r - dcf.terminal_growth(3.8, r) >= dcf.MIN_SPREAD - 1e-12


def test_beta_is_bounded_and_missing_beta_is_one():
    assert dcf.bounded_beta(0.3) == 0.8
    assert dcf.bounded_beta(3.1) == 2.0
    assert dcf.bounded_beta(None) == 1.0
    assert dcf.bounded_beta(float("nan")) == 1.0


def test_headline_is_method_e_and_range_brackets_it():
    res = _value(_payloads())
    assert res.status == "ok"
    assert res.range_low < res.fair_value < res.range_high
    assert res.range_low <= res.alternative_value <= res.range_high
    assert res.sbc_status == "deducted"
    assert res.conversion == pytest.approx(0.17 / 0.21, rel=1e-9)
    assert res.margin == pytest.approx(0.17, rel=1e-9)
    assert res.analyst_years == 5 and res.analysts_min == 20
    assert 0.3 < res.terminal_share < 0.9


# ── timing: no jumps where nothing economic happened ─────────────────────────────────────────

def test_value_is_continuous_across_a_fiscal_year_end():
    """The prototype discounted year 1 a full year from today, so the value jumped whenever the
    window rolled. Now: one day before vs one day after FY2026 ends (not yet reported)."""
    p = _payloads()
    before = _value(p, date(2026, 12, 30)).fair_value
    after = _value(p, date(2027, 1, 1)).fair_value
    assert after == pytest.approx(before, rel=0.01)


def test_value_is_nearly_continuous_across_the_annual_report_roll():
    """Roll-forward continuity (the check the reviewers said nothing else would catch): on the
    same day, value the company (a) before its FY2026 10-K is public and (b) after, when the
    reported year equals the consensus it replaced. Steady-state inputs → nearly the same value."""
    p = _payloads()
    as_of = date(2027, 2, 20)
    a = _value(p, as_of)                         # 10-K not in the payload yet
    rev26 = 50e9 * 1.06
    q = copy.deepcopy(p)
    q["income_annual"].append({"date": "2026-12-31", "filingDate": "2027-02-15",
                               "revenue": rev26, "netIncome": 0.20 * rev26,
                               "weightedAverageShsOutDil": 1e9, "interestExpense": 0.01 * rev26,
                               "reportedCurrency": "USD"})
    q["cash_flow_annual"].append({"date": "2026-12-31", "filingDate": "2027-02-15",
                                  "freeCashFlow": 0.18 * rev26,
                                  "stockBasedCompensation": 0.01 * rev26})
    q["estimates"].append({"date": "2031-12-31", "revenueAvg": 50e9 * 1.06 ** 6,
                           "netIncomeAvg": 0.20 * 50e9 * 1.06 ** 6,
                           "numAnalystsRevenue": 20, "numAnalystsEps": 20})
    b = _value(q, as_of)
    assert a.status == b.status == "ok"
    assert b.fair_value == pytest.approx(a.fair_value, rel=0.03)


# ── point-in-time + input edge cases ─────────────────────────────────────────────────────────

def test_statements_not_yet_filed_are_invisible():
    inp = _inputs(_payloads(), as_of=date(2026, 2, 1))     # FY2025 10-K filed 2026-02-15
    assert inp.last_fy_end == date(2024, 12, 31)
    assert [c.end.year for c in inp.consensus][:1] == [2025]


def test_consensus_dated_days_after_the_reported_year_end_is_that_year():
    """COST: consensus 2026-08-31 vs statements 2026-08-30 — the reported year, not a forecast."""
    p = _payloads()
    p["estimates"].append({"date": "2026-01-05", "revenueAvg": 1.0, "netIncomeAvg": 1.0,
                           "numAnalystsRevenue": 30, "numAnalystsEps": 30})
    inp = _inputs(p)
    assert all(c.end > date(2026, 1, 20) for c in inp.consensus)
    assert _value(p).status == "ok"


def test_a_zero_share_count_in_the_newest_filing_is_skipped():
    """COST's preliminary FY2026 row carries weightedAverageShsOutDil = 0."""
    p = _payloads()
    p["income_quarter"] = [
        {"date": "2026-03-31", "filingDate": "2026-05-01", "weightedAverageShsOutDil": 0.95e9},
        {"date": "2026-06-30", "filingDate": "2026-08-01", "weightedAverageShsOutDil": 0},
    ]
    assert _inputs(p).shares_diluted == pytest.approx(0.95e9)


def test_missing_sbc_is_unknown_not_zero():
    p = _edit(_payloads(), "cash_flow_annual", lambda r: r.update(stockBasedCompensation=0))
    res = _value(p)
    assert res.status == "ok" and res.sbc_status == "not_reported"
    assert any("not deducted" in n for n in res.notes)
    two = copy.deepcopy(p)
    for r in two["cash_flow_annual"][-2:]:
        r["stockBasedCompensation"] = 0.01 * 50e9
    assert _value(two).sbc_status == "not_reported"            # 2 of 5 years < 3


def test_deducting_sbc_lowers_the_value():
    with_sbc = _value(_payloads())
    heavy = _edit(_payloads(), "cash_flow_annual",
                  lambda r: r.update(stockBasedCompensation=r["freeCashFlow"] * 0.3))
    assert _value(heavy).fair_value < with_sbc.fair_value


def test_rf_average_needs_most_of_the_window():
    @dataclass
    class Obs:
        date: str
        value: float

    days = [AS_OF - timedelta(days=i) for i in range(1, 365 * 5)]
    obs = [Obs(d.isoformat(), 4.0) for d in days if d.weekday() < 5]
    assert dcf.rf_average_pct(obs, AS_OF) == pytest.approx(4.0)
    assert dcf.rf_average_pct(obs[: len(obs) // 2], AS_OF) is None
    assert dcf.rf_recent_pct(obs, AS_OF) == pytest.approx(4.0)


# ── refusals: each fires, and a passing sibling stays valued ────────────────────────────────

def _prof(p: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    q = copy.deepcopy(p)
    q["profile"] = {**q["profile"], **kw}
    return q


@pytest.mark.parametrize("industry,code", [
    ("Banks - Regional", "financial_company"),
    ("Insurance - Property & Casualty", "financial_company"),
    ("Asset Management", "financial_company"),
    ("Investment - Banking & Investment Services", "financial_company"),
    ("REIT - Retail", "reit"),
])
def test_industry_refusals(industry, code):
    assert _value(_prof(_payloads(), industry=industry)).refusal_code == code


def test_payment_networks_and_exchanges_are_not_refused_as_financials():
    assert _value(_prof(_payloads(), industry="Financial - Data & Stock Exchanges")).status == "ok"
    assert _value(_prof(_payloads(), industry="Financial - Credit Services")).status == "ok"


def test_utilities_are_refused_by_sector():
    assert _value(_prof(_payloads(), sector="Utilities",
                        industry="Regulated Electric")).refusal_code == "regulated_utility"


def test_lenders_are_refused_by_interest_burden():
    p = _prof(_payloads(), industry="Financial - Credit Services")
    lender = _edit(p, "income_annual", lambda r: r.update(interestExpense=-0.20 * r["revenue"]))
    assert _value(lender).refusal_code == "lender"              # sign ignored
    assert _value(p).status == "ok"                              # 1 % of revenue: a network


def test_captive_finance_is_refused_by_ticker():
    assert _value(_payloads(ticker="DE")).refusal_code == "captive_finance"


def test_high_leverage():
    heavy = _payloads(balance_annual=[{"date": "2025-12-31", "filingDate": "2026-02-15",
                                       "totalDebt": 80e9}])
    assert _value(heavy).refusal_code == "high_leverage"
    ok = _payloads(balance_annual=[{"date": "2025-12-31", "filingDate": "2026-02-15",
                                    "totalDebt": 70e9}])
    assert _value(ok).status == "ok"


def test_company_changed_shape_on_a_spin_off_forecast():
    p = copy.deepcopy(_payloads())
    for r in p["estimates"]:
        if r["date"] >= "2026":
            r["revenueAvg"] *= 0.7
            r["netIncomeAvg"] *= 0.7
    assert _value(p).refusal_code == "company_changed_shape"


def test_company_changed_shape_on_a_later_forecast_year():
    """HON: the year-1 consensus looked normal, but the year-2 consensus was post-spin-off —
    half the revenue. Found by the Phase-2 replay; the first rule only checked year 1."""
    p = copy.deepcopy(_payloads())
    for r in p["estimates"]:
        if r["date"] >= "2027":
            r["revenueAvg"] *= 0.5
            r["netIncomeAvg"] *= 0.5
    assert _value(p).refusal_code == "company_changed_shape"


def test_an_ended_unreported_year_does_not_take_an_analyst_slot():
    """After FY2026 ends (before its 10-K) the window is FY2027-2031: five analyst years, not
    four plus the dead one. So the 10-K filing itself does not move the forecast window."""
    p = copy.deepcopy(_payloads())
    p["estimates"].append({"date": "2031-12-31", "revenueAvg": 50e9 * 1.06 ** 6,
                           "netIncomeAvg": 0.2 * 50e9 * 1.06 ** 6,
                           "numAnalystsRevenue": 20, "numAnalystsEps": 20})
    res = _value(p, date(2027, 1, 20))
    assert res.status == "ok" and res.analyst_years == 5


def test_company_changed_shape_on_a_revenue_collapse_in_history():
    """v2 (S1): a reported collapse refuses only while it has NOT recovered. v1 refused this
    fixture too — FY2023 at 70 % of FY2022 — but FY2025 (50 B) is back above 90 % of FY2022, so
    under v2 it is valued with a note; the unrecovered sibling (FY2023-25 all 30 % lower) still
    refuses."""
    p = copy.deepcopy(_payloads())
    p["income_annual"][2]["revenue"] = p["income_annual"][1]["revenue"] * 0.7
    res = _value(p)
    assert res.status == "ok", res.refusal_code
    assert any("since recovered" in n for n in res.notes)
    stuck = copy.deepcopy(_payloads())
    for r in stuck["income_annual"][2:]:
        r["revenue"] *= 0.7
    assert _value(stuck).refusal_code == "company_changed_shape"


def test_negative_fcf():
    p = copy.deepcopy(_payloads())
    p["cash_flow_annual"][-1]["freeCashFlow"] = -1e9
    assert _value(p).refusal_code == "negative_fcf"
    q = copy.deepcopy(_payloads())
    q["cash_flow_annual"][0]["freeCashFlow"] = -1e9               # one old bad year is tolerated
    assert _value(q).status == "ok"


def test_forecast_losses():
    p = copy.deepcopy(_payloads())
    next_year = next(r for r in p["estimates"] if r["date"] == "2026-12-31")
    next_year["netIncomeAvg"] = -1e9
    assert _value(p).refusal_code == "forecast_losses"


def test_thin_coverage():
    p = copy.deepcopy(_payloads())
    for r in p["estimates"]:
        r["numAnalystsEps"] = 4
    assert _value(p).refusal_code == "thin_coverage"
    q = copy.deepcopy(_payloads())
    q["estimates"][3]["numAnalystsEps"] = 2                      # 4th year thin: 3 years used
    res = _value(q)
    assert res.status == "ok" and res.analyst_years == 3


def test_unusual_margin_and_conversion():
    tiny = _edit(_payloads(), "cash_flow_annual",
                 lambda r: r.update(freeCashFlow=r["stockBasedCompensation"] * 1.5))
    assert _value(tiny).refusal_code == "unusual_margin"
    low_ni = _edit(_payloads(), "income_annual", lambda r: r.update(netIncome=0.03 * r["revenue"]))
    assert _value(low_ni).refusal_code == "unusual_conversion"


def test_a_self_contradicting_history_is_refused():
    """CSX in the replay: old years converted ~0.3, recent ~0.9, so the 5-year median flipped at
    one annual report and the value doubled. Two low years out of five → spread > 1.8×.

    v2 (S3): when the two low years are the OLDEST and the newest three agree, the newest three
    decide (no median left to flip: the ratio IS the recent one) — so v1's CSX fixture is now
    valued on the recent margin. Low years among the newest three still refuse."""
    p = copy.deepcopy(_payloads())
    for r in p["cash_flow_annual"][:2]:
        r["freeCashFlow"] *= 0.4
    res = _value(p)
    assert res.status == "ok", res.refusal_code
    assert res.margin == pytest.approx(0.17) and res.conversion == pytest.approx(0.17 / 0.21)
    alternating = copy.deepcopy(_payloads())
    for i in (0, 2, 4):                                          # low, ok, low, ok, low
        alternating["cash_flow_annual"][i]["freeCashFlow"] *= 0.4
    assert _value(alternating).refusal_code == "unusual_margin"
    one_odd = copy.deepcopy(_payloads())
    one_odd["cash_flow_annual"][0]["freeCashFlow"] *= 0.4        # one odd year is tolerated
    assert _value(one_odd).status == "ok"
    assert dcf.ratio_spread([0.3, 0.35, 0.9, 0.95, 0.92]) > dcf.MAX_RATIO_SPREAD
    assert dcf.ratio_spread([0.3, 0.85, 0.9, 0.95, 0.92]) < dcf.MAX_RATIO_SPREAD
    assert dcf.ratio_spread([0.5, 1.0]) is None


def _growing(rate: float) -> Dict[str, Any]:
    p = copy.deepcopy(_payloads())
    for i, r in enumerate(sorted((r for r in p["estimates"] if r["date"] >= "2026"),
                                 key=lambda r: r["date"]), start=1):
        r["revenueAvg"] = 50e9 * rate ** i
        r["netIncomeAvg"] = 0.2 * r["revenueAvg"]
    return p


def test_growth_out_of_range():
    """v2 (S2): the band's top edge is +40 %/yr (v1: +30 %, which this fixture's +40 % used to
    exceed). +41 % refuses; +35 % is the passing sibling (test_growth_inside_the_wider_band…)."""
    assert _value(_growing(1.41)).refusal_code == "growth_out_of_range"
    assert _value(_growing(1.35)).status == "ok"


def test_the_tail_starts_from_the_trend_not_a_noisy_last_year():
    """UNH in the replay: a far analyst year at 25.1 B after 14.1 B doubled the value, because the
    first version grew the tail from the RAW last year at the END-POINT CAGR. Now the tail starts
    from the trend value at the last analyst year, at the trend growth. (A genuine revision still
    moves the value — the last forecast year anchors any DCF's terminal value; the damping only
    applies to a point off the trend, and a point > 25 % off it refuses: test_unstable_consensus.)"""
    ends = [date(2026 + i, 12, 31) for i in range(5)]
    spiky = [100.0, 110.0, 121.0, 133.1, 160.0]
    flows, _ = dcf.project_flows(spiky, ends, 0.03)
    trend = dcf.consensus_trend(spiky)
    raw_cagr = (160.0 / 100.0) ** 0.25 - 1
    assert flows[:5] == spiky                               # analyst years stay as published
    assert flows[5] == pytest.approx(trend.last_fitted * (1 + trend.growth + (0.03 - trend.growth) / 5))
    assert flows[5] < 160.0 * (1 + raw_cagr)                # below the old raw-endpoint start
    assert trend.last_fitted < 160.0 and trend.growth < raw_cagr
    smooth = [100.0, 110.0, 121.0, 133.1, 146.41]           # on-trend: identical to raw
    f2, _ = dcf.project_flows(smooth, ends, 0.03)
    assert f2[5] == pytest.approx(146.41 * (1 + 0.10 + (0.03 - 0.10) / 5))


def test_unstable_consensus():
    p = copy.deepcopy(_payloads())
    rows = sorted((r for r in p["estimates"] if r["date"] >= "2026"), key=lambda r: r["date"])
    rows[3]["netIncomeAvg"] *= 0.6                 # UNH-style dip then recovery
    rows[4]["netIncomeAvg"] *= 1.5
    assert _value(p).refusal_code == "unstable_consensus"


def test_consensus_trend_math():
    t = dcf.consensus_trend([100.0, 110.0, 121.0, 133.1])
    assert t.growth == pytest.approx(0.10, rel=1e-9)
    assert t.last_fitted == pytest.approx(133.1, rel=1e-9)
    assert t.max_deviation == pytest.approx(0.0, abs=1e-12)
    assert dcf.consensus_trend([5.0]) is None
    assert dcf.consensus_trend([5.0, -1.0]) is None


def test_models_disagree():
    p = copy.deepcopy(_payloads())
    for r in p["estimates"]:
        r["netIncomeAvg"] = 0.36 * r["revenueAvg"]
    assert _value(p).refusal_code == "models_disagree"


def test_currency_share_conflict_and_missing_data():
    eur = _edit(_payloads(), "income_annual", lambda r: r.update(reportedCurrency="EUR"))
    assert _value(eur).refusal_code == "currency_mismatch"
    assert _value(_prof(_payloads(), marketCap=200e9)).refusal_code == "share_count_conflict"
    short = copy.deepcopy(_payloads())
    short["income_annual"] = short["income_annual"][-2:]
    assert _value(short).refusal_code == "missing_data"


def test_every_refusal_code_has_user_copy_and_is_reachable_in_source():
    src = _SERVICE_SRC.read_text()
    used = set(re.findall(r'_refuse\(inp, "([a-z_]+)"', src)) | \
        set(re.findall(r'return None, "([a-z_]+)"', src))
    assert used == set(dcf.REFUSAL_REASONS), (used ^ set(dcf.REFUSAL_REASONS))
    for code, text in dcf.REFUSAL_REASONS.items():
        assert text.endswith(".") and len(text) < 130, code
        low = text.lower()
        assert "undervalued" not in low and "overvalued" not in low and "buy" not in low


# ── research variant ─────────────────────────────────────────────────────────────────────────

def test_production_rate_is_the_coherent_damodaran_pair():
    p = _payloads()
    v1 = _value(p)
    assert dcf.RATE_RF_BASIS == "erp_vintage"
    assert v1.rf_rate_pct == dcf.RF_AT_ERP_PCT
    assert v1.discount_rate == pytest.approx((dcf.RF_AT_ERP_PCT + 1.0 * dcf.ERP_PCT) / 100)
    assert dcf.to_response(v1).risk_free_pct == dcf.RF_AT_ERP_PCT
    # terminal growth still follows the 5-year average (3.8 %), below the cap and spread
    assert v1.terminal_growth == pytest.approx(0.038)
    recent = _value(p, rate_basis="recent")          # 5.2 % → higher r, lower value
    assert recent.fair_value < v1.fair_value
    assert recent.discount_rate == pytest.approx(v1.discount_rate + (5.2 - dcf.RF_AT_ERP_PCT) / 100)


# ── response schema ──────────────────────────────────────────────────────────────────────────

def test_response_shapes():
    ok = dcf.to_response(_value(_payloads()))
    assert ok.status == "ok" and ok.currency == "USD" and ok.model_version == dcf.MODEL_VERSION
    assert ok.fair_value and ok.range_low and ok.range_high and ok.equity_risk_premium_pct == 4.23
    assert DcfFairValueResponse.model_validate(ok.model_dump()) == ok
    refused = dcf.to_response(_value(_prof(_payloads(), industry="Banks")))
    assert refused.status == "refused" and refused.refusal_code == "financial_company"
    assert refused.refusal_reason and refused.fair_value is None and refused.range_low is None
    assert refused.currency is None


def test_only_symbol_and_status_are_required():
    fields = DcfFairValueResponse.model_fields
    required = {name for name, f in fields.items() if f.is_required()}
    assert required == {"symbol", "status"}


# ── HARD RULE 1: nothing per-user reaches the value ──────────────────────────────────────────

def test_the_service_never_names_a_user_persona_tier_or_portfolio():
    tree = ast.parse(_SERVICE_SRC.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    # Whole snake_case parts, so `CREDIT_SERVICES_INDUSTRY` (an FMP industry label) passes and
    # `user_id`, `persona`, `tier`, `credits`, `portfolio_items` do not.
    banned = {"user", "users", "persona", "personas", "tier", "tiers", "portfolio", "portfolios",
              "watchlist", "watchlists", "holding", "holdings", "subscription", "subscriptions",
              "credits", "profile_id", "investor"}
    hits = sorted(n for n in names if set(n.lower().strip("_").split("_")) & banned)
    assert not hits, hits


def test_get_fair_value_takes_a_ticker_and_nothing_else():
    import inspect
    params = list(inspect.signature(dcf.DcfFairValueService.get_fair_value).parameters)
    assert params == ["self", "ticker"]


# ── HARD RULE 5: the spec says what the code does ────────────────────────────────────────────

def test_spec_refusal_table_matches_the_code():
    spec = _SPEC.read_text()
    table_codes = set(re.findall(r"^\| `([a-z_]+)` \|", spec, flags=re.M))
    assert table_codes == set(dcf.REFUSAL_REASONS), table_codes ^ set(dcf.REFUSAL_REASONS)


def test_spec_states_every_constant_the_code_uses():
    spec = _SPEC.read_text()
    expected = [
        f"`{dcf.MODEL_VERSION}`",
        f"**{dcf.ERP_PCT:.2f} %**",
        f"**{dcf.RF_AT_ERP_PCT:.2f} %**",
        f"**{dcf.BETA_MIN} – {dcf.BETA_MAX}**",
        f"**{dcf.TERMINAL_G_CAP * 100:.1f} %**",
        f"`r − {dcf.MIN_SPREAD * 100:.1f} pts`",
        f"**≥ {dcf.MIN_ANALYSTS} analysts**",
        f"up to the first **{dcf.MAX_ANALYST_YEARS}**",
        f"{dcf.MARGIN_BOUNDS[0] * 100:.0f} % – {dcf.MARGIN_BOUNDS[1] * 100:.0f} %",
        f"{dcf.CONVERSION_BOUNDS[0]:.2f} – {dcf.CONVERSION_BOUNDS[1]:.2f}",
        f"+{dcf.GROWTH_BOUNDS[1] * 100:.0f} %/yr",
        f"more than {dcf.MAX_TREND_DEVIATION * 100:.0f} % off the analyst-years trend",
        f"spread more than {dcf.MAX_RATIO_SPREAD}×",
        f"−{abs(dcf.GROWTH_BOUNDS[0]) * 100:.0f} %/yr",
        f"0.67 – {dcf.MAX_DISAGREEMENT}",
        f"> {dcf.LENDER_INTEREST_SHARE * 100:.0f} % of revenue",
        f"> {dcf.MAX_DEBT_TO_MARKET_CAP} × market",
        f"> {dcf.SHAPE_MAX_YOY_DROP * 100:.0f} % in one year",
        f"**at least {dcf.MIN_SBC_YEARS}**",
        f"> {dcf.SHARE_CONFLICT * 100:.0f} %",
        f"within {dcf.SAME_FY_DAYS} days",
        f"**{dcf.FORECAST_YEARS}** fiscal years",
        # v2 (owner decision 2026-10-23)
        f"**{dcf.TAIL_START_GROWTH_CAP * 100:.0f} %/yr**",
        f"at least {dcf.SHAPE_RECOVERY_SHARE * 100:.0f} % of",
        f"the last {dcf.RECENT_RATIO_YEARS} fiscal years",
        f"at least {dcf.RECENT_RATIO_MIN_YEARS} usable years",
        f"more than {dcf.CONSENSUS_SHARE_TOLERANCE * 100:.0f} % from the latest diluted share count",
        f"**{dcf.CONSENSUS_SHARE_FULL * 100:.0f} %** or more",
        f"more than **{dcf.CONSENSUS_SHARE_MAX_RATIO:.0f}×** the latest diluted count or less than "
        f"**1/{dcf.CONSENSUS_SHARE_MAX_RATIO:.0f}**",
        f"from **{dcf.MAX_RATIO_SPREAD}×** to **{dcf.RECENT_RATIO_FULL_SPREAD}×**",
        f"w = (s − {dcf.MAX_RATIO_SPREAD}) ÷ ({dcf.RECENT_RATIO_FULL_SPREAD} − {dcf.MAX_RATIO_SPREAD})",
        f"t = (gap − {dcf.CONSENSUS_SHARE_TOLERANCE * 100:.0f} %) ÷ "
        f"({dcf.CONSENSUS_SHARE_FULL * 100:.0f} % − {dcf.CONSENSUS_SHARE_TOLERANCE * 100:.0f} %)",
        f"within the last {dcf.RF_FALLBACK_MAX_AGE_DAYS} days",
        f"fewer than **{dcf._RF_MIN_OBSERVATIONS}**",
        f"**{dcf.ROLL_BLEND_DAYS} days**",
        f"`MODEL_VERSION = \"{dcf.MODEL_VERSION}\"`",
    ]
    missing = [e for e in expected if e not in spec]
    assert not missing, missing
    for t in dcf.CAPTIVE_FINANCE_TICKERS:
        assert re.search(rf"\b{t}\b", spec), t
    for prefix in dcf.FINANCIAL_INDUSTRY_PREFIXES:
        assert prefix in spec, prefix


def test_spec_quotes_the_changed_shape_copy_the_backend_serves():
    """v2 rewrote the one refusal reason STX was shown; the spec quotes it verbatim."""
    assert f'now reads "{dcf.REFUSAL_REASONS["company_changed_shape"]}"' in _SPEC.read_text()


def test_spec_names_every_history_field_v2_added():
    """§4: the log fields the code writes for v2 (consensus eps, rf_source, rf_source_date) are
    named in the spec — derived from the log the code actually writes, not from a list."""
    p = _payloads()
    inp = dcf.build_inputs(
        "TEST", AS_OF, profile=p["profile"], income_annual=p["income_annual"],
        income_quarter=p["income_quarter"], cash_flow_annual=p["cash_flow_annual"],
        balance_annual=p["balance_annual"], estimates=p["estimates"], rf_avg_pct=3.9)
    log = dcf._inputs_log(inp)
    added = {"rf_source", "rf_source_date"} & set(log)
    added |= {"eps"} & set(log["consensus"][0])
    assert added == {"rf_source", "rf_source_date", "eps"}, added
    section4 = _SPEC.read_text().split("## 4.")[1].split("## 5.")[0]
    for name in sorted(added):
        assert f"`{name}`" in section4, name
    assert "`fred` or `history_fallback`" in section4


def test_spec_records_the_owner_decisions_behind_v2():
    spec = _SPEC.read_text()
    notes = spec.split("### 2026-10-23 — model v2")[1]
    for quote in ('"Conservative (Recommended)"', '"Yes, re-base (Recommended)"',
                  # the review round's two cliffs (owner decisions 2026-10-23)
                  '"Blend over 1.8–2.4× (Recommended)"', '"Ramp it in over 5–15% (Recommended)"'):
        assert quote in notes, quote
    review = notes.split("**Review round (2026-10-23")[1]
    for fact in ("share_count_conflict", "cancelled", "`permanent = true`", "crossfade"):
        assert fact in review, fact
    assert "§9.6" in notes and "silent" in notes      # why the seed cap is not the rejected clip


def test_the_v1_spec_is_kept_and_points_to_v2():
    """v1 stays as the record of what `dcf-v1` history rows mean: still the v1 rules (the +30 %
    band, no recovery or recent-years rule), with only a header line pointing to v2."""
    v1 = _SPEC_V1.read_text()
    lines = v1.splitlines()
    assert lines[0] == "# Caydex Fair Value Estimate — methodology spec, model `dcf-v1`"
    assert lines[1] == ""
    assert lines[2].startswith("> **Superseded by [dcf-methodology-v2.md](dcf-methodology-v2.md) "
                               "on 2026-10-23.**"), lines[2]
    assert sum("Superseded" in line for line in lines) == 1
    assert '`MODEL_VERSION = "dcf-v1"`' in v1 and "+30 %/yr" in v1 and "+40 %/yr" not in v1
    assert "dcf-methodology-v2" not in "\n".join(lines[3:])   # frozen below the header
    assert "[dcf-methodology-v1.md](dcf-methodology-v1.md)" in _SPEC.read_text()


# ── service: flags, caching, dedup ───────────────────────────────────────────────────────────

class _FakeFMP:
    def __init__(self, p: Dict[str, Any], fail: str | None = None) -> None:
        self.p, self.fail, self.calls = p, fail, 0

    async def _ret(self, key: str):
        self.calls += 1
        await asyncio.sleep(0)
        if key == self.fail:
            raise RuntimeError("upstream down")
        return copy.deepcopy(self.p[key])

    async def get_company_profile(self, t):
        return await self._ret("profile")

    async def get_income_statement(self, t, period="annual", limit=10):
        return await self._ret("income_annual" if period == "annual" else "income_quarter")

    async def get_cash_flow_statement(self, t, period="annual", limit=10):
        return await self._ret("cash_flow_annual")

    async def get_balance_sheet(self, t, period="annual", limit=10):
        return await self._ret("balance_annual")

    async def get_analyst_estimates(self, t, period="annual", limit=10):
        return await self._ret("estimates")


@dataclass
class _Obs:
    date: str
    value: float


class _FakeFRED:
    async def get_observations(self, series, limit=13, **kw):
        today = dcf._today_et()
        days = [today - timedelta(days=i) for i in range(0, 365 * 5 + 30)]
        return [_Obs(d.isoformat(), 3.8) for d in days if d.weekday() < 5]


@pytest.fixture
def service(monkeypatch):
    dcf._cache.clear()
    dcf._inflight.clear()
    dcf._failed_at.clear()
    dcf._rf_fallback_memo.clear()

    def _no_supabase():
        raise AssertionError("Supabase touched while DCF_ENABLED is False")

    monkeypatch.setattr(dcf, "get_supabase", _no_supabase)
    monkeypatch.setattr(dcf.settings, "DCF_ENABLED", False)
    svc = dcf.DcfFairValueService.__new__(dcf.DcfFairValueService)
    svc.fmp = _FakeFMP(_payloads())
    svc.fred = _FakeFRED()
    svc._supabase = None
    yield svc
    dcf._cache.clear()
    dcf._inflight.clear()
    dcf._failed_at.clear()
    dcf._rf_fallback_memo.clear()


def test_the_switch_is_off_by_default():
    from app.config import Settings
    assert Settings.model_fields["DCF_ENABLED"].default is False


@pytest.mark.asyncio
async def test_disabled_service_computes_without_touching_supabase(service):
    """Record, don't raise: the write helpers swallow exceptions by design (a failed cache
    write must not fail the request), so a raising stub would hide a write."""
    touched: List[str] = []
    for name in ("_read_stored", "_write_stored", "_append_history"):
        setattr(service, name, lambda *a, _n=name, **k: touched.append(_n))
    res = await service.get_fair_value("ACME")
    await asyncio.sleep(0.05)                      # let any executor job run
    assert res.status == "ok" and res.symbol == "ACME"
    assert touched == []


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_computation(service):
    a, b = await asyncio.gather(service.get_fair_value("ACME"), service.get_fair_value("ACME"))
    assert a == b
    assert service.fmp.calls == 6                    # six FMP legs, once


@pytest.mark.asyncio
async def test_a_failed_essential_leg_raises_and_is_not_cached(service):
    service.fmp = _FakeFMP(_payloads(), fail="estimates")
    with pytest.raises(dcf.DcfInputsUnavailableError):
        await service.get_fair_value("ACME")
    # Within the failure memo a retry re-raises WITHOUT spending the FMP calls again.
    healthy = _FakeFMP(_payloads())
    service.fmp = healthy
    with pytest.raises(dcf.DcfInputsUnavailableError):
        await service.get_fair_value("ACME")
    assert healthy.calls == 0
    dcf._failed_at.clear()                               # the memo expires
    res = await service.get_fair_value("ACME")          # recomputed, never a stored refusal
    assert res.status == "ok" and healthy.calls == 6


@pytest.mark.asyncio
async def test_a_failed_quarterly_leg_degrades_to_fiscal_year_shares(service):
    service.fmp = _FakeFMP(_payloads(), fail="income_quarter")
    res = await service.get_fair_value("ACME")
    assert res.status == "ok"
    assert any("fiscal-year count" in n for n in res.notes or [])


@pytest.mark.asyncio
async def test_enabled_service_writes_cache_and_history(monkeypatch, service):
    writes: List[tuple] = []

    class _Q:
        def __init__(self, table):
            self.table = table

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def upsert(self, row, **kw):
            writes.append((self.table, row, kw))
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    class _SB:
        def table(self, name):
            return _Q(name)

    monkeypatch.setattr(dcf.settings, "DCF_ENABLED", True)
    service._supabase = _SB()
    res = await service.get_fair_value("ACME")
    for _ in range(50):
        if len(writes) >= 2:
            break
        await asyncio.sleep(0.01)
    tables = {w[0] for w in writes}
    assert tables == {"dcf_fair_value_cache", "dcf_fair_value_history"}
    hist = next(w for w in writes if w[0] == "dcf_fair_value_history")
    assert hist[2] == {"on_conflict": "ticker,as_of_date,model_version", "ignore_duplicates": True}
    row = hist[1]
    assert row["status"] == "ok" and row["fair_value"] == res.fair_value
    assert row["model_version"] == dcf.MODEL_VERSION and row["inputs"]["erp_pct"] == 4.23
    assert not any(k for k in row if re.search(r"user|persona|tier", k))


@pytest.mark.asyncio
async def test_invalid_ticker_is_rejected(service):
    with pytest.raises(ValueError):
        await service.get_fair_value("not a ticker!")


# ── adversarial-review round (2026-09-25): each test names the defect it pins ────────────────

def _with_row(p: Dict[str, Any], key: str, row: Dict[str, Any]) -> Dict[str, Any]:
    q = copy.deepcopy(p)
    q[key].append(row)
    return q


def _six_year_consensus() -> Dict[str, Any]:
    """Realistic shape: SIX forward rows (FMP usually has them), decelerating growth."""
    p = copy.deepcopy(_payloads())
    p["estimates"] = [r for r in p["estimates"] if r["date"] < "2026"]
    rev = 50e9
    for i, y in enumerate(range(2026, 2032)):
        rev *= 1.09 - 0.01 * i
        p["estimates"].append({"date": f"{y}-12-31", "revenueAvg": rev, "netIncomeAvg": 0.2 * rev,
                               "numAnalystsRevenue": 20 - 2 * i, "numAnalystsEps": 20 - 2 * i})
    return p


def test_a_negative_latest_year_without_an_sbc_figure_still_refuses():
    """C0: SBC reported in 4 of 5 years; the latest (preliminary) row has SBC 0 and FCF −5 B.
    It used to drop out of the rule and the company was valued as if nothing happened."""
    p = copy.deepcopy(_payloads())
    p["cash_flow_annual"][-1].update(freeCashFlow=-5e9, stockBasedCompensation=0)
    assert _value(p).refusal_code == "negative_fcf"


def test_two_old_negative_years_refuse():
    p = copy.deepcopy(_payloads())
    for r in p["cash_flow_annual"][:2]:
        r["freeCashFlow"] = -1e9
    assert _value(p).refusal_code == "negative_fcf"


def test_beta_zero_is_missing_not_a_real_beta():
    """C1: FMP sends 0 with too little price history; 0 → floor 0.8 published ~15 % too high."""
    res = _value(_prof(_payloads(), beta=0))
    assert res.beta == 1.0 and any("beta unavailable" in n for n in res.notes)
    small = _value(_prof(_payloads(), beta=0.3))
    assert small.beta == dcf.BETA_MIN and not any("beta unavailable" in n for n in small.notes)


def test_no_reported_share_count_refuses_instead_of_deriving_one():
    """C2: a market cap ÷ price fallback made share_count_conflict unable to fire."""
    p = copy.deepcopy(_payloads())
    p["income_quarter"] = []
    for r in p["income_annual"]:
        r.pop("weightedAverageShsOutDil")
    assert _inputs(p).shares_diluted is None
    assert _value(p).refusal_code == "missing_data"


def test_a_five_for_four_split_is_caught():
    """Pre-split quarterly count (1.0 B) against a post-split market cap (1.25 B shares × $100)."""
    assert _value(_prof(_payloads(), marketCap=125e9)).refusal_code == "share_count_conflict"
    assert _value(_prof(_payloads(), marketCap=108e9)).status == "ok"          # 8 %: fine


def test_the_range_is_exactly_min_max_of_the_two_readings():
    """C12: pin the displayed range formula and both branches of min/max."""
    for p in (_payloads(), _six_year_consensus()):
        inp = _inputs(p)
        res = dcf.value_company(inp)
        assert res.status == "ok"
        e = res.fair_value
        assert res.range_low == pytest.approx(min(res.alternative_value, res.range_low))
        assert res.range_low <= e <= res.range_high
        up = res.discount_rate + dcf.SENSITIVITY_STEP
        start = inp.history[-1].end
        window, _ = dcf._check_window(start, inp.consensus)
        e_up = dcf._method_value(inp, window.analyst_e, "earnings", res.conversion, up,
                                 dcf.terminal_growth(inp.rf_avg_pct, up), start).per_share
        down = res.discount_rate - dcf.SENSITIVITY_STEP
        e_down = dcf._method_value(inp, window.analyst_e, "earnings", res.conversion, down,
                                   dcf.terminal_growth(inp.rf_avg_pct, down), start).per_share
        assert res.range_low == pytest.approx(min(res.alternative_value, e_up))
        assert res.range_high == pytest.approx(max(res.alternative_value, e_down))
    # R below E(r+1) → low is R; R above E(r−1) → high is R
    low_r = _edit(_payloads(), "cash_flow_annual", lambda r: r.update(freeCashFlow=r["freeCashFlow"] * 0.93))
    for r in low_r["income_annual"]:
        r["netIncome"] *= 0.93
    res = _value(low_r)
    if res.status == "ok" and res.alternative_value < res.fair_value:
        assert res.range_low == pytest.approx(min(res.alternative_value, res.range_low))


@pytest.mark.parametrize("mutate,code", [
    # conversion spread > 1.8× with a flat margin: NI raised in two years. v2: years 1 and 3
    # (was 0 and 1) — two OLD odd years are now absorbed by S3's newest-three median, so the
    # clause needs an odd year among the newest three to fire alone.
    (lambda p: [p["income_annual"][i].update(netIncome=p["income_annual"][i]["netIncome"] * 2.5)
                for i in (1, 3)], "unusual_conversion"),
    # median conversion below 0.35 with the margin in range
    (lambda p: [r.update(netIncome=r["revenue"] * 0.55) for r in p["income_annual"]], "unusual_conversion"),
    # median margin above 60 %
    (lambda p: [r.update(freeCashFlow=r["freeCashFlow"] * 3.6) for r in p["cash_flow_annual"]]
     + [r.update(netIncome=r["netIncome"] * 3.6) for r in p["income_annual"]], "unusual_margin"),
    # E ÷ R below 0.67
    (lambda p: [r.update(netIncomeAvg=r["netIncomeAvg"] * 0.55) for r in p["estimates"]], "models_disagree"),
])
def test_each_refusal_clause_fires_alone(mutate, code):
    """C13: a fixture per clause that trips only that clause."""
    p = copy.deepcopy(_payloads())
    mutate(p)
    assert _value(p).refusal_code == code


@pytest.mark.parametrize("ticker", sorted(dcf.CAPTIVE_FINANCE_TICKERS))
def test_every_captive_finance_ticker_is_refused(ticker):
    assert _value(_payloads(ticker=ticker)).refusal_code == "captive_finance"


def test_the_captive_finance_list_matches_the_spec_both_ways():
    """C14: the old guard only checked code ⊆ spec (a word-boundary search)."""
    row = next(l for l in _SPEC.read_text().splitlines() if l.startswith("| `captive_finance` |"))
    listed = {t.strip() for t in row.split("|")[2].split(",")}
    assert listed == set(dcf.CAPTIVE_FINANCE_TICKERS)


def test_only_years_with_an_sbc_figure_enter_the_medians():
    """C15: SBC in 4 of 5 years; the 5th year's margin is wildly different. With deduction on,
    it must not move the margin at all."""
    base = copy.deepcopy(_payloads())
    base["cash_flow_annual"][1]["stockBasedCompensation"] = 0
    odd = copy.deepcopy(base)
    odd["cash_flow_annual"][1]["freeCashFlow"] *= 1.7
    a, b = _value(base), _value(odd)
    assert a.sbc_status == b.sbc_status == "deducted"
    assert a.margin == pytest.approx(b.margin) and a.conversion == pytest.approx(b.conversion)


@pytest.mark.asyncio
async def test_a_missing_treasury_average_raises_and_is_never_stored(service, monkeypatch):
    """C16: an input failure must never become a stored refusal.

    v2 (R1): with persistence on, a missing FRED average first asks the stored history for a
    recent one (tests/test_dcf_rf_fallback.py); here the history has none, so it still raises."""
    monkeypatch.setattr(dcf.settings, "DCF_ENABLED", True)
    written: List[str] = []
    read: List[date] = []
    service._read_stored = lambda *a, **k: None
    service._read_rf_history = lambda as_of: read.append(as_of) or []
    service._write_stored = lambda *a, **k: written.append("cache")
    service._append_history = lambda *a, **k: written.append("history")

    class _ThinFRED:
        async def get_observations(self, series, limit=13, **kw):
            today = dcf._today_et()
            return [_Obs((today - timedelta(days=i)).isoformat(), 4.0) for i in range(0, 365)]

    service.fred = _ThinFRED()
    with pytest.raises(dcf.DcfInputsUnavailableError):
        await service.get_fair_value("ACME")
    await asyncio.sleep(0.05)
    assert written == [] and not any(k.endswith(":ACME") for k in dcf._cache)
    assert read == [dcf._today_et()], "the stored-average fallback was not consulted"


def test_the_value_is_continuous_across_the_year_end_with_six_consensus_rows():
    """L12 + the roll crossfade: with a 6th forward row the window used to jump at the year-end."""
    p = _six_year_consensus()
    before = _value(p, date(2026, 12, 30))
    after = _value(p, date(2027, 1, 1))
    assert before.status == after.status == "ok"
    assert after.fair_value == pytest.approx(before.fair_value, rel=0.01)
    later = _value(p, date(2027, 4, 5))                  # crossfade complete (> 91 days)
    assert not any("rolling forward" in n for n in later.notes)
    assert any("rolling forward" in n for n in after.notes)


def test_the_10k_filing_during_the_crossfade_does_not_jump():
    p = _six_year_consensus()
    as_of = date(2027, 2, 20)
    before_10k = _value(p, as_of)
    rev26 = next(r for r in p["estimates"] if r["date"] == "2026-12-31")["revenueAvg"]
    q = copy.deepcopy(p)
    q["income_annual"].append({"date": "2026-12-31", "filingDate": "2027-02-15", "revenue": rev26,
                               "netIncome": 0.2 * rev26, "weightedAverageShsOutDil": 1e9,
                               "interestExpense": 0.01 * rev26, "reportedCurrency": "USD"})
    q["cash_flow_annual"].append({"date": "2026-12-31", "filingDate": "2027-02-15",
                                  "freeCashFlow": 0.18 * rev26, "stockBasedCompensation": 0.01 * rev26})
    after_10k = _value(q, as_of)
    assert after_10k.fair_value == pytest.approx(before_10k.fair_value, rel=0.02)


def test_duplicate_fiscal_year_rows_count_once():
    p = copy.deepcopy(_payloads())
    dup = dict(p["income_annual"][2], filingDate="2024-01-10", netIncome=1.0)   # an OLDER filing of FY2023
    p = _with_row(p, "income_annual", dup)
    inp = _inputs(p)
    assert len({y.end for y in inp.history}) == len(inp.history) == 5
    year = next(y for y in inp.history if y.end == date(2023, 12, 31))
    assert year.net_income != 1.0, "the NEWEST filing of a period must win, not the last row seen"
    assert _value(p).fair_value == pytest.approx(_value(_payloads()).fair_value)


def test_unknown_currency_and_a_foreign_year_fail_closed():
    p = _edit(_prof(_payloads(), currency=None), "income_annual", lambda r: r.pop("reportedCurrency"))
    assert _value(p).refusal_code == "missing_data"
    one = copy.deepcopy(_payloads())
    one["income_annual"][1]["reportedCurrency"] = "EUR"        # an OLDER year, not the newest
    assert _value(one).refusal_code == "currency_mismatch"


def test_a_credit_services_company_without_interest_is_not_assumed_a_network():
    p = _prof(_payloads(), industry="Financial - Credit Services")
    for r in p["income_annual"]:
        r.pop("interestExpense")
    assert _value(p).refusal_code == "missing_data"


def test_valuing_never_mutates_the_inputs():
    inp = _inputs(_prof(_payloads(), beta=None))
    before = list(inp.notes)
    for _ in range(3):
        dcf.value_company(inp)
        dcf.value_company(inp, rate_basis="recent")
    assert inp.notes == before


def test_a_refusal_carries_no_estimate_parameters():
    res = _value(_prof(_payloads(), industry="Banks"))
    out = dcf.to_response(res)
    assert out.discount_rate_pct is None and out.beta is None and out.sbc_status is None
    assert out.cash_conversion is None and out.terminal_growth_pct is None


def test_the_trend_is_weighted_by_analyst_count():
    """A thin far year must pull the trend less than a well-covered one (QCOM: a 6-analyst 5th
    year moved the value +60 % overnight)."""
    vals = [100.0, 110.0, 121.0, 133.1, 200.0]
    flat = dcf.consensus_trend(vals)
    thin = dcf.consensus_trend(vals, [20, 20, 20, 20, 5])
    assert thin.growth < flat.growth and thin.last_fitted < flat.last_fitted
    assert dcf.consensus_trend(vals, [7, 7, 7, 7, 7]).growth == pytest.approx(flat.growth)


def test_a_thin_spiky_far_year_moves_the_value_less_than_a_well_covered_one():
    def with_last(analysts: int) -> float:
        p = copy.deepcopy(_payloads())
        last = max((r for r in p["estimates"] if r["date"] >= "2026"), key=lambda r: r["date"])
        last["netIncomeAvg"] *= 1.2
        last["revenueAvg"] *= 1.2
        last["numAnalystsEps"] = last["numAnalystsRevenue"] = analysts
        res = _value(p)
        assert res.status == "ok"
        return res.fair_value
    assert with_last(5) < with_last(20)


def test_a_stored_estimate_from_yesterday_is_not_served(service, monkeypatch):
    """Review round C4: every cached value is TODAY's, so the Analysis tab and a report generated
    today agree, and the history gets a row per day viewed."""
    yesterday = (dcf._today_et() - timedelta(days=1)).isoformat()
    row = dcf.to_response(_value(_payloads())).model_copy(update={"as_of": yesterday})

    class _SB:
        def table(self, _):
            return self

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def execute(self):
            from datetime import datetime, timezone
            return type("R", (), {"data": [{"response_json": row.model_dump(),
                                            "model_version": dcf.MODEL_VERSION,
                                            "computed_at": datetime.now(timezone.utc).isoformat()}]})()

    service._supabase = _SB()
    assert service._read_stored("ACME") is None
    fresh = row.model_copy(update={"as_of": dcf._today_et().isoformat()})
    row = fresh
    assert service._read_stored("ACME") == fresh


def _gap_fixture() -> Dict[str, Any]:
    """A company whose OLD and NEW forecast windows differ a lot: the 6th consensus year sits well
    above the 5-year trend (but inside the stability rule), so the roll matters."""
    p = _six_year_consensus()
    last = max((r for r in p["estimates"] if r["date"] >= "2026"), key=lambda r: r["date"])
    last["netIncomeAvg"] *= 1.18
    last["revenueAvg"] *= 1.18
    return p


def test_the_10k_filing_during_the_crossfade_keeps_the_old_window():
    """Fix-round C1: the previous version of this test passed with the post-10-K old window
    deleted. Here the windows differ by far more than the tolerance, and a control proves it."""
    p = _gap_fixture()
    as_of = date(2027, 2, 20)
    before = _value(p, as_of)
    rev26 = next(r for r in p["estimates"] if r["date"] == "2026-12-31")["revenueAvg"]
    q = copy.deepcopy(p)
    q["income_annual"].append({"date": "2026-12-31", "filingDate": "2027-02-15", "revenue": rev26,
                               "netIncome": 0.2 * rev26, "weightedAverageShsOutDil": 1e9,
                               "interestExpense": 0.01 * rev26, "reportedCurrency": "USD"})
    q["cash_flow_annual"].append({"date": "2026-12-31", "filingDate": "2027-02-15",
                                  "freeCashFlow": 0.18 * rev26, "stockBasedCompensation": 0.01 * rev26})
    inp_after = _inputs(q, as_of)
    after = dcf.value_company(inp_after)
    assert before.status == after.status == "ok"
    assert any("rolling forward" in n for n in after.notes)
    assert after.fair_value == pytest.approx(before.fair_value, rel=0.005)
    control_inp = copy.copy(inp_after)
    control_inp.consensus_reported = None                 # what deleting the branch would do
    control = dcf.value_company(control_inp)
    assert abs(control.fair_value / before.fair_value - 1) > 0.02, "fixture must discriminate"


def test_the_range_takes_the_revenue_method_when_it_lies_outside_the_rate_band():
    """Fix-round C9: both branches of min/max, asserted unconditionally."""
    def run(factor: float) -> dcf.DcfResult:
        p = copy.deepcopy(_payloads())
        for r in p["cash_flow_annual"]:
            r["freeCashFlow"] *= factor           # moves R's margin; E's conversion moves too…
        for r in p["income_annual"]:
            r["netIncome"] *= factor               # …so keep E's conversion where it was
        return _value(p)
    low = run(0.72)                               # R ≈ 158 below E(r+1) ≈ 162, E/R 1.30
    assert low.status == "ok" and low.alternative_value < low.fair_value
    assert low.range_low == pytest.approx(low.alternative_value)
    assert low.range_high != pytest.approx(low.alternative_value)
    high = run(1.25)                              # R ≈ 284 above E(r−1) ≈ 272, E/R 0.77
    assert high.status == "ok" and high.alternative_value > high.fair_value
    assert high.range_high == pytest.approx(high.alternative_value)
    assert high.range_low != pytest.approx(high.alternative_value)


def test_the_history_log_carries_every_crossfade_input():
    """v2 adds the consensus EPS (the share re-base is reproducible from the row) and where the
    Treasury average came from (the rf fallback reads `rf_source_date` back)."""
    p = _edit(_payloads(), "estimates", lambda r: r.update(epsAvg=r["netIncomeAvg"] / 1e9))
    inp = _inputs(p)
    log = dcf._inputs_log(inp)
    assert log["consensus_reported"] and log["consensus_reported"]["end"] == "2025-12-31"
    assert log["statement_currencies"] == ["USD"] and log["listing_currency"] == "USD"
    assert log["consensus_reported"]["eps"] == pytest.approx(10.0)
    assert [c["eps"] for c in log["consensus"]] == [pytest.approx(c.net_income / 1e9) for c in inp.consensus]
    assert log["rf_source"] == "fred" and log["rf_source_date"] is None
    labelled = dcf.build_inputs(
        p["ticker"], AS_OF, profile=p["profile"], income_annual=p["income_annual"],
        income_quarter=p["income_quarter"], cash_flow_annual=p["cash_flow_annual"],
        balance_annual=p["balance_annual"], estimates=p["estimates"], rf_avg_pct=3.9,
        rf_source="history_fallback", rf_source_date=date(2026, 9, 22))
    log = dcf._inputs_log(labelled)
    assert log["rf_source"] == "history_fallback" and log["rf_source_date"] == "2026-09-22"
    json.dumps(log)                       # JSON-safe: it is written to a jsonb column


def test_refusal_precedence_matches_the_spec_table():
    """Fix-round C0: 'the first matching rule wins' in TABLE order. Window rules (here
    unstable_consensus) come before the trailing-ratio rules (here unusual_margin)."""
    p = copy.deepcopy(_payloads())
    for r in p["cash_flow_annual"]:
        r["freeCashFlow"] *= 3.6
    for r in p["income_annual"]:
        r["netIncome"] *= 3.6
    assert _value(p).refusal_code == "unusual_margin"            # margin rule alone
    rows = sorted((r for r in p["estimates"] if r["date"] >= "2026"), key=lambda r: r["date"])
    rows[3]["netIncomeAvg"] *= 0.6
    rows[4]["netIncomeAvg"] *= 1.5
    assert _value(p).refusal_code == "unstable_consensus"        # both fire → table order
    table = [l.split("`")[1] for l in _SPEC.read_text().splitlines() if l.startswith("| `")]
    assert table.index("unstable_consensus") < table.index("unusual_margin")
    assert table.index("model_error") < table.index("models_disagree")


# ── model v2 (owner decision 2026-10-23): each rule fires, and a sibling shows where it stops ──

def _v1_switches(monkeypatch) -> None:
    """Every v2 rule off by its constant: the model must then be v1, number for number."""
    monkeypatch.setattr(dcf, "SHAPE_RECOVERY_SHARE", float("inf"))
    monkeypatch.setattr(dcf, "GROWTH_BOUNDS", (-0.15, 0.30))
    monkeypatch.setattr(dcf, "RECENT_RATIO_MIN_YEARS", 10 ** 9)
    monkeypatch.setattr(dcf, "CONSENSUS_SHARE_TOLERANCE", float("inf"))


def _misbased(p: Dict[str, Any], implied: float = 1.2e9) -> Dict[str, Any]:
    """Consensus EPS on today's 1 B diluted shares, NI on an older count (`implied` shares) — the
    CRM shape. Every estimate row, the reported-year one included."""
    q = copy.deepcopy(p)
    for r in q["estimates"]:
        r["epsAvg"] = r["netIncomeAvg"] / implied
    return q


def _csx(p: Dict[str, Any]) -> Dict[str, Any]:
    """The two OLDEST years convert ~0.3 instead of ~0.8 (spread 2.7×); the newest three agree."""
    q = copy.deepcopy(p)
    for r in q["cash_flow_annual"][:2]:
        r["freeCashFlow"] *= 0.4
    return q


def _collapse(p: Dict[str, Any]) -> Dict[str, Any]:
    q = copy.deepcopy(p)
    q["income_annual"][2]["revenue"] = q["income_annual"][1]["revenue"] * 0.7
    return q


# Frozen-v1 results (git show of the dcf-v1 service, 2026-10-23), the reference for the switches.
_V1_OK = {
    "base": (lambda: (_payloads(), AS_OF),
             (213.97893780496338, 175.37480004444055, 251.4275086638642, 224.67788469521162), []),
    "six": (lambda: (_six_year_consensus(), AS_OF),
            (228.47293988711135, 187.1327007871551, 268.5780193677297, 239.89658688146693), []),
    "gap_roll": (lambda: (_gap_fixture(), date(2027, 1, 1)),
                 (231.39832981214272, 189.56301791287396, 271.76979004367877, 242.9682463027498),
                 ["forecast window rolling forward after the fiscal year ended 2026-12-31 (1% of the way)"]),
    "misbased_roll": (lambda: (_misbased(_payloads()), date(2026, 2, 20)),
                      (207.20969032535226, 169.79924893354683, 243.6161759377453, 217.5701748416196),
                      ["latest quarterly share count unavailable; fiscal-year count used",
                       "forecast window rolling forward after the fiscal year ended 2025-12-31 (56% of the way)"]),
    "beta_none": (lambda: (_prof(_payloads(), beta=None), AS_OF),
                  (213.97893780496338, 175.37480004444055, 251.4275086638642, 224.67788469521162),
                  ["beta unavailable; market beta 1.0 used"]),
}
_V1_REFUSED = {
    "csx": (lambda: _csx(_payloads()), "unusual_margin"),
    "growth_135": (lambda: _growing(1.35), "growth_out_of_range"),
    "collapse": (lambda: _collapse(_payloads()), "company_changed_shape"),
}


@pytest.mark.parametrize("name", sorted(_V1_OK))
def test_with_every_v2_rule_switched_off_the_model_is_v1(monkeypatch, name):
    """The harness attributes every changed value to one rule by switching rules off one at a
    time; that only works if all-off IS v1. Pinned against the frozen v1's own numbers and notes
    (a 3,000-fixture randomised comparison against the frozen module found 0 differences)."""
    _v1_switches(monkeypatch)
    build, (fv, low, high, alt), notes = _V1_OK[name]
    p, as_of = build()
    res = _value(p, as_of)
    assert res.status == "ok", res.refusal_code
    assert (res.fair_value, res.range_low, res.range_high, res.alternative_value) == (
        pytest.approx(fv, rel=1e-12), pytest.approx(low, rel=1e-12),
        pytest.approx(high, rel=1e-12), pytest.approx(alt, rel=1e-12))
    assert res.notes == notes


@pytest.mark.parametrize("name", sorted(_V1_REFUSED))
def test_with_every_v2_rule_switched_off_v1_refusals_return(monkeypatch, name):
    build, code = _V1_REFUSED[name]
    assert _value(build()).status == "ok", "fixture must be one v2 values"
    _v1_switches(monkeypatch)
    assert _value(build()).refusal_code == code


@pytest.mark.parametrize("name", ["base", "six", "gap_roll", "beta_none"])
def test_v1_valued_fixtures_keep_their_value_under_v2(name):
    """No rule may move a value v1 already published when its inputs do not trigger the rule —
    the WHOLE published result: value, range, alternative (method R, on the wire) and notes
    (review F2: pinning the value alone let a stray note or a moved method R through). These
    fixtures carry no EPS, so the re-base cannot act, and their ratio spreads are far below
    1.8×, so S3's blend cannot either. (misbased_roll is left out on purpose: its consensus is
    on a 20 %-off share count, which v2 re-bases in full — t = 1, the ramp's top.)"""
    build, (fv, low, high, alt), notes = _V1_OK[name]
    p, as_of = build()
    res = _value(p, as_of)
    assert res.status == "ok", res.refusal_code
    assert (res.fair_value, res.range_low, res.range_high, res.alternative_value) == (
        pytest.approx(fv, rel=1e-12), pytest.approx(low, rel=1e-12),
        pytest.approx(high, rel=1e-12), pytest.approx(alt, rel=1e-12))
    assert res.notes == notes


# ── S1: a recovered reported drop ──

def _with_revenues(revs: List[float], first_year: int = 2021) -> Dict[str, Any]:
    """The synthetic company with these reported revenues (oldest first, ending FY2025) and every
    other line proportional to revenue, so only the shape rule can react."""
    p = copy.deepcopy(_payloads())
    years = range(2026 - len(revs), 2026)
    p["income_annual"] = [
        {"date": f"{y}-12-31", "filingDate": f"{y + 1}-02-15", "revenue": rev, "netIncome": 0.20 * rev,
         "weightedAverageShsOutDil": 1e9, "interestExpense": 0.01 * rev, "reportedCurrency": "USD"}
        for y, rev in zip(years, revs)]
    p["cash_flow_annual"] = [
        {"date": f"{y}-12-31", "filingDate": f"{y + 1}-02-15", "freeCashFlow": 0.18 * rev,
         "stockBasedCompensation": 0.01 * rev}
        for y, rev in zip(years, revs)]
    return p


def test_a_recovered_drop_is_valued_and_noted():
    """STX: −37 % in FY23, FY26 revenue above FY22's. v1 refused it as a changed company."""
    res = _value(_with_revenues([50e9, 31e9, 40e9, 46e9, 50e9]))
    assert res.status == "ok", res.refusal_code
    assert any("2022: -38%" in n and "since recovered" in n for n in res.notes), res.notes


def test_an_unrecovered_drop_still_refuses():
    assert _value(_with_revenues([50e9, 31e9, 36e9, 40e9, 44e9])).refusal_code == \
        "company_changed_shape"


def test_the_recovery_threshold_is_inclusive_at_ninety_percent():
    a = 50e9
    at = _with_revenues([a, 30e9, 38e9, 42e9, dcf.SHAPE_RECOVERY_SHARE * a])
    below = _with_revenues([a, 30e9, 38e9, 42e9, 0.899 * a])
    assert _value(at).status == "ok", _value(at).refusal_code
    assert _value(below).refusal_code == "company_changed_shape"


def test_a_drop_in_the_first_transition_can_recover():
    res = _value(_with_revenues([50e9, 30e9, 45e9, 48e9, 50e9]))
    assert res.status == "ok" and any("since recovered" in n for n in res.notes)


def test_a_drop_into_the_latest_year_never_passes(monkeypatch):
    """Even with a recovery share low enough that the latest year would 'recover' to it, a drop
    INTO the latest year is not a recovery. Sibling: the same drop one year earlier passes."""
    monkeypatch.setattr(dcf, "SHAPE_RECOVERY_SHARE", 0.5)
    assert _value(_with_revenues([40e9, 42e9, 45e9, 48e9, 30e9])).refusal_code == \
        "company_changed_shape"
    earlier = _with_revenues([40e9, 42e9, 45e9, 30e9, 31e9])
    assert _value(earlier).status == "ok", _value(earlier).refusal_code


def test_a_forecast_drop_after_a_recovered_drop_still_refuses():
    p = _with_revenues([50e9, 31e9, 45e9, 48e9, 50e9])
    for r in p["estimates"]:
        if r["date"] >= "2027":
            r["revenueAvg"] *= 0.6
            r["netIncomeAvg"] *= 0.6
    assert _value(p).refusal_code == "company_changed_shape"


def test_a_three_year_history_recovers_only_before_its_latest_year():
    ok = _with_revenues([50e9, 30e9, 47e9])
    assert _value(ok).status == "ok", _value(ok).refusal_code
    assert _value(_with_revenues([50e9, 48e9, 30e9])).refusal_code == "company_changed_shape"


# ── S2: the wider growth band, and the tail that starts no higher than +30 %/yr ──

def test_growth_inside_the_wider_band_is_valued_with_a_capped_tail(monkeypatch):
    """NVDA: a 33.7 %/yr consensus trend against v1's 30 % band. Now valued — but the years after
    the analysts' start from +30 %/yr, not from the trend (an uncapped tail values it higher)."""
    p = _growing(1.35)
    res = _value(p)
    assert res.status == "ok", res.refusal_code
    assert any("trend of 35.0%/yr" in n and "start from 30%/yr" in n for n in res.notes), res.notes
    monkeypatch.setattr(dcf, "TAIL_START_GROWTH_CAP", 1.0)
    uncapped = _value(p)
    assert uncapped.fair_value > res.fair_value * 1.05
    assert not any("start from" in n for n in uncapped.notes)


def test_the_tail_cap_does_not_touch_a_trend_below_it():
    ends = [date(2026 + i, 12, 31) for i in range(5)]
    vals = [100.0 * 1.25 ** i for i in range(5)]
    flows, _ = dcf.project_flows(vals, ends, 0.03)
    assert flows[5] == pytest.approx(vals[-1] * (1 + 0.25 + (0.03 - 0.25) / 5))
    fast = [100.0 * 1.38 ** i for i in range(5)]
    flows, _ = dcf.project_flows(fast, ends, 0.03)
    assert flows[:5] == fast                                 # analyst years as published
    cap = dcf.TAIL_START_GROWTH_CAP
    assert flows[5] == pytest.approx(fast[-1] * (1 + cap + (0.03 - cap) / 5))
    assert not any("start from" in n for n in _value(_payloads()).notes)


def test_growth_just_outside_the_wider_band_refuses():
    assert _value(_growing(1.41)).refusal_code == "growth_out_of_range"
    assert _value(_growing(1.39)).status == "ok"


# ── S3: the newest three years when five disagree ──

def test_the_csx_shape_is_valued_on_the_newest_three_years():
    res = _value(_csx(_payloads()))
    assert res.status == "ok", res.refusal_code
    assert res.margin == pytest.approx(0.17) and res.conversion == pytest.approx(0.17 / 0.21)
    assert any("cash-flow margin varied" in n for n in res.notes)
    assert any("cash conversion varied" in n for n in res.notes)


def test_alternating_low_years_still_refuse():
    p = copy.deepcopy(_payloads())
    for i in (0, 2, 4):
        p["cash_flow_annual"][i]["freeCashFlow"] *= 0.4
    assert _value(p).refusal_code == "unusual_margin"


def test_the_recent_median_needs_four_usable_ratios_in_all():
    hist = [dcf.FiscalYear(end=date(2020 + i, 12, 31), revenue=1.0, net_income=1.0, fcf=1.0,
                           sbc=None, interest_expense=None) for i in range(4)]
    four = {hist[0].end: 0.3, hist[1].end: 0.8, hist[2].end: 0.9, hist[3].end: 0.85}
    assert dcf._trailing_ratio(four, hist) == pytest.approx(0.85)
    three = {k: v for k, v in four.items() if k != hist[0].end}
    assert dcf._trailing_ratio(three, hist) is None
    assert dcf._trailing_ratio({**four, hist[1].end: -0.2}, hist) is None   # 3 usable (> 0)
    assert dcf._trailing_ratio({**four, hist[2].end: 0.4}, hist) is None    # newest disagree
    # a 3-year history whose ratios disagree still refuses (it has only three ratios)
    p = copy.deepcopy(_payloads())
    for k in ("income_annual", "cash_flow_annual"):
        p[k] = p[k][-3:]
    p["cash_flow_annual"][0]["freeCashFlow"] *= 0.4
    assert _value(p).refusal_code == "unusual_margin"


def test_the_newest_year_without_an_sbc_figure_blocks_the_recent_median():
    p = _csx(_payloads())
    p["cash_flow_annual"][-1]["stockBasedCompensation"] = 0
    assert _value(p).refusal_code == "unusual_margin"
    sibling = copy.deepcopy(_payloads())
    sibling["cash_flow_annual"][-1]["stockBasedCompensation"] = 0
    assert _value(sibling).status == "ok"


def test_a_recent_loss_blocks_the_recent_conversion():
    p = _csx(_payloads())
    p["income_annual"][3]["netIncome"] = -1e9
    assert _value(p).refusal_code == "unusual_conversion"
    assert _value(_csx(_payloads())).status == "ok"


# ── consensus share basis ──

def test_consensus_on_an_older_share_count_is_re_based():
    """CRM: NI ÷ EPS implied ~17 % more shares than today's diluted count. The re-based value is
    the value of the same consensus published on today's basis."""
    rebased = _value(_misbased(_payloads()))
    consistent = _edit(_payloads(), "estimates",
                       lambda r: r.update(netIncomeAvg=r["netIncomeAvg"] / 1.2,
                                          epsAvg=r["netIncomeAvg"] / 1.2 / 1e9))
    want = _value(consistent)
    assert rebased.status == want.status == "ok"
    assert rebased.fair_value == pytest.approx(want.fair_value, rel=1e-9)
    assert any("+20.0%" in n and "re-based 100% of the way" in n for n in rebased.notes), rebased.notes
    assert not any("re-based" in n for n in want.notes)
    assert rebased.fair_value < _value(_payloads()).fair_value * 0.9


def test_a_small_share_basis_gap_is_left_alone():
    """Below the ramp's start (CONSENSUS_SHARE_TOLERANCE, 5 %) NI is used as published."""
    near = _value(_misbased(_payloads(), implied=1.04e9))
    assert near.fair_value == pytest.approx(_value(_payloads()).fair_value, rel=1e-12)
    assert not any("re-based" in n for n in near.notes)
    under = _value(_misbased(_payloads(), implied=1 / 1.04 * 1e9))       # fewer shares implied
    assert under.fair_value == pytest.approx(_value(_payloads()).fair_value, rel=1e-12)


def test_the_re_base_crossfades_both_windows():
    """During the crossfade the OLD window is rebuilt from the reported-year row; it must be
    re-based with the rest, or the two windows sit on different share counts."""
    as_of = date(2026, 2, 20)
    consistent = _edit(_payloads(), "estimates",
                       lambda r: r.update(netIncomeAvg=r["netIncomeAvg"] / 1.2,
                                          epsAvg=r["netIncomeAvg"] / 1.2 / 1e9))
    a, b = _value(_misbased(_payloads()), as_of), _value(consistent, as_of)
    assert any("rolling forward" in n for n in a.notes)
    assert a.fair_value == pytest.approx(b.fair_value, rel=1e-9)
    half = _misbased(_payloads())
    reported = next(r for r in half["estimates"] if r["date"] == "2025-12-31")
    reported.pop("epsAvg")                                # control: the old window NOT re-based
    assert abs(_value(half, as_of).fair_value / b.fair_value - 1) > 0.002, "fixture must discriminate"


def _cy(ni, eps, year=2026):
    return dcf.ConsensusYear(end=date(year, 12, 31), revenue=1.0, net_income=ni,
                             analysts_revenue=20, analysts_eps=20, eps=eps)


def test_rows_without_a_positive_ni_and_eps_are_never_re_based():
    rows = [_cy(120.0, 1.0, 2026), _cy(132.0, 1.1, 2027), _cy(-5.0, 0.5, 2028),
            _cy(144.0, None, 2029), _cy(150.0, 0.0, 2030), _cy(160.0, -1.0, 2031), _cy(None, 1.0, 2032)]
    out, rep, ratio, t = dcf.consensus_on_share_basis(rows, None, 100.0)
    assert ratio == pytest.approx(1.2) and rep is None and t == 1.0
    assert [c.net_income for c in out] == [pytest.approx(100.0), pytest.approx(110.0), -5.0,
                                           144.0, 150.0, 160.0, None]
    assert [c.net_income for c in rows][:2] == [120.0, 132.0], "the inputs were modified"
    # no EPS at all (a row rebuilt from a v1 history log), or no share count: unchanged
    bare = [_cy(120.0, None)]
    assert dcf.consensus_on_share_basis(bare, None, 100.0) == (bare, None, None, 0.0)
    assert dcf.consensus_on_share_basis(rows, None, None)[2:] == (None, 0.0)


def test_a_forecast_loss_with_positive_eps_still_refuses():
    p = _misbased(_payloads())
    nxt = next(r for r in p["estimates"] if r["date"] == "2026-12-31")
    nxt["netIncomeAvg"] = -1e9                                       # eps stays > 0
    assert _value(p).refusal_code == "forecast_losses"


def test_the_share_basis_decision_is_one_per_ticker():
    """Rows straddling the tolerance move together: the median decides for all of them."""
    def rows(*implied):
        return [_cy(100.0 * k, 1.0, 2026 + i) for i, k in enumerate(implied)]
    implied = (1.05, 1.08, 1.12, 1.13, 1.15)
    out, _, ratio, t = dcf.consensus_on_share_basis(rows(*implied), None, 100.0)
    assert ratio == pytest.approx(1.12)
    assert t == pytest.approx((0.12 - dcf.CONSENSUS_SHARE_TOLERANCE)
                              / (dcf.CONSENSUS_SHARE_FULL - dcf.CONSENSUS_SHARE_TOLERANCE))   # 0.7
    # every row moves by the SAME t, the 1.05 and 1.15 rows too
    assert [c.net_income for c in out] == [pytest.approx(100 * k + t * (100 - 100 * k)) for k in implied]
    low = (1.01, 1.02, 1.04, 1.13, 1.15)                     # median 1.04: under the ramp's start
    out, _, ratio, t = dcf.consensus_on_share_basis(rows(*low), None, 100.0)
    assert ratio == pytest.approx(1.04) and t == 0.0
    assert [c.net_income for c in out] == [pytest.approx(100 * k) for k in low]
    # the reported-year row votes and is re-based too
    rep = _cy(130.0, 1.0, 2025)
    _, rep_out, ratio, t = dcf.consensus_on_share_basis(rows(1.25, 1.3), rep, 100.0)
    assert ratio == pytest.approx(1.3) and t == 1.0 and rep_out.net_income == pytest.approx(100.0)
    assert rep.net_income == 130.0


def test_valuing_with_a_re_base_never_mutates_the_inputs():
    inp = _inputs(_misbased(_payloads()))
    before = [(c.net_income, c.eps) for c in inp.consensus], inp.consensus_reported, list(inp.notes)
    for _ in range(2):
        assert dcf.value_company(inp).status == "ok"
    assert ([(c.net_income, c.eps) for c in inp.consensus], inp.consensus_reported, inp.notes) == before


def test_build_inputs_reads_consensus_eps():
    inp = _inputs(_misbased(_payloads()))
    assert all(c.eps == pytest.approx(c.net_income / 1.2e9) for c in inp.consensus)
    assert inp.consensus_reported.eps == pytest.approx(10e9 / 1.2e9)
    assert all(c.eps is None for c in _inputs(_payloads()).consensus)


def test_the_changed_shape_copy_says_what_the_rule_now_checks():
    text = dcf.REFUSAL_REASONS["company_changed_shape"]
    assert "hasn't recovered" in text and "forecast to fall" in text
    assert text.endswith(".") and len(text) < 130


# ── R4: an empty profile is a failed fetch, never a stored refusal ──

@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [{}, [], [{}], None])
async def test_an_empty_profile_raises_instead_of_refusing(service, empty):
    p = _payloads(profile=empty)
    service.fmp = _FakeFMP(p)
    with pytest.raises(dcf.DcfInputsUnavailableError):
        await service.get_fair_value("ACME")
    assert not any(k.endswith(":ACME") for k in dcf._cache)


@pytest.mark.asyncio
async def test_empty_estimates_are_still_a_refusal(service):
    service.fmp = _FakeFMP(_payloads(estimates=[]))
    res = await service.get_fair_value("ACME")
    assert res.status == "refused" and res.refusal_code == "thin_coverage"


# ── v2 review round (2026-10-23): the two cliffs, the share-unit bound, notes, the service ──
# Owner decisions: S3 "Blend over 1.8–2.4× (Recommended)"; re-base "Ramp it in over 5–15%
# (Recommended)". Each rule: a firing test, a passing sibling, and a continuity test across the
# point that used to be a step.

def _with_margins(margins: List[float]) -> Dict[str, Any]:
    """Flat 50 B revenue and these yearly (FCF − SBC) ÷ revenue margins, oldest first. NI is 20 %
    and SBC 1 % of revenue, so conversion = margin ÷ 0.21 and both spreads are the same."""
    p = _with_revenues([50e9] * len(margins))
    for row, m in zip(p["cash_flow_annual"], margins):
        row["freeCashFlow"] = (m + 0.01) * 50e9
    return p


def test_s3_is_continuous_as_the_spread_crosses_its_threshold():
    """dcf-v2-S3-cliff: one 4-year-old margin moves 0.0834 → 0.0830, the spread 1.7986 → 1.807.
    Before the blend the value jumped 113.28 → 188.80 (+67 %); now it moves by about w = 1 %."""
    below = _value(_with_margins([0.05, 0.0834, 0.09, 0.15, 0.16]))
    above = _value(_with_margins([0.05, 0.0830, 0.09, 0.15, 0.16]))
    assert below.status == above.status == "ok", (below.refusal_code, above.refusal_code)
    assert dcf.ratio_spread([0.05, 0.0834, 0.09, 0.15, 0.16]) < dcf.MAX_RATIO_SPREAD \
        < dcf.ratio_spread([0.05, 0.0830, 0.09, 0.15, 0.16])
    assert abs(above.fair_value / below.fair_value - 1) < 0.02
    assert below.margin == pytest.approx(0.09) and not any("varied" in n for n in below.notes)
    s = 0.15 / 0.083
    w = (s - dcf.MAX_RATIO_SPREAD) / (dcf.RECENT_RATIO_FULL_SPREAD - dcf.MAX_RATIO_SPREAD)
    assert above.margin == pytest.approx((1 - w) * 0.09 + w * 0.15, rel=1e-12)
    assert f"cash-flow margin varied {s:.2f}× over the reported years; blended {w:.0%} toward " \
           f"the newest 3 years' median" in above.notes, above.notes


def test_s3_blend_weight_runs_from_the_threshold_to_the_full_spread():
    """w = 0 at 1.8×, linear, 1 from 2.4×: at and beyond the full spread the newest years' median
    is used exactly (the CSX fixture, spread 2.7×, is valued on 0.17)."""
    hist = [dcf.FiscalYear(end=date(2021 + i, 12, 31), revenue=1.0, net_income=1.0, fcf=1.0,
                           sbc=None, interest_expense=None) for i in range(5)]
    by_end = {y.end: v for y, v in zip(hist, (0.1, 0.1, 0.2, 0.2, 0.2))}
    mid = (dcf.MAX_RATIO_SPREAD + dcf.RECENT_RATIO_FULL_SPREAD) / 2
    ratio, note = dcf._blend_recent(0.1, mid, by_end, hist, "x")
    assert ratio == pytest.approx(0.15) and "blended 50%" in note
    assert dcf._blend_recent(0.1, dcf.RECENT_RATIO_FULL_SPREAD, by_end, hist, "x")[0] == 0.2
    assert dcf._blend_recent(0.1, 9.0, by_end, hist, "x")[0] == 0.2
    assert dcf._blend_recent(0.1, dcf.MAX_RATIO_SPREAD, by_end, hist, "x") == (0.1, None)
    assert dcf._blend_recent(0.1, None, by_end, hist, "x") == (0.1, None)
    assert dcf._blend_recent(None, 2.0, by_end, hist, "x") == (None, None)
    # the newest years disagree → refuse (None), whatever the weight would have been
    bad = {**by_end, hist[-1].end: 0.5}
    assert dcf._blend_recent(0.1, 2.0, bad, hist, "x") == (None, None)
    res = _value(_csx(_payloads()))
    assert any("blended 100% toward" in n for n in res.notes), res.notes


def test_s3_bounds_apply_to_the_blended_ratio():
    """A blend whose result is under the 1 % margin floor refuses, though the 5-year median alone
    would not have been the deciding input."""
    p = _with_margins([0.002, 0.004, 0.006, 0.0075, 0.008])       # spread 0.0075/0.004 = 1.875
    res = _value(p)
    assert res.refusal_code == "unusual_margin"


def test_s3_off_switch_still_refuses_inside_the_blend_band(monkeypatch):
    above = _with_margins([0.05, 0.0830, 0.09, 0.15, 0.16])
    monkeypatch.setattr(dcf, "RECENT_RATIO_MIN_YEARS", 10 ** 9)
    assert _value(above).refusal_code == "unusual_margin"


def _rescaled(k: float) -> Dict[str, Any]:
    """Consensus EPS on today's 1 B shares, NI on a count k × that (every row, the reported one
    included): a re-base at t = 1 must give back exactly the base value."""
    q = copy.deepcopy(_payloads())
    for r in q["estimates"]:
        r["epsAvg"] = r["netIncomeAvg"] / 1e9
        r["netIncomeAvg"] *= k
    return q


def test_the_re_base_is_continuous_across_its_old_ten_percent_step():
    """dcf-v2-rebase-cliff: implied 1.0999e9 vs 1.1001e9 moved the value −9.1 % (213.98 → 194.51).
    Inside the ramp it moves by a hair."""
    a = _value(_misbased(_payloads(), implied=1.0999e9))
    b = _value(_misbased(_payloads(), implied=1.1001e9))
    assert a.status == b.status == "ok"
    assert abs(b.fair_value / a.fair_value - 1) < 0.005
    assert abs(b.range_low / a.range_low - 1) < 0.005 and abs(b.range_high / a.range_high - 1) < 0.005


def test_the_re_base_ramps_in_between_five_and_fifteen_percent():
    """At a 10 % gap t = 0.5: NI' = NI × (1 − t + t ÷ 1.1), and method E scales by exactly that."""
    base = _value(_payloads()).fair_value
    res = _value(_misbased(_payloads(), implied=1.1e9))
    t = (0.10 - dcf.CONSENSUS_SHARE_TOLERANCE) / (dcf.CONSENSUS_SHARE_FULL - dcf.CONSENSUS_SHARE_TOLERANCE)
    assert t == pytest.approx(0.5)
    assert res.fair_value == pytest.approx(base * (1 - t + t / 1.1), rel=1e-9)
    assert any("+10.0%" in n and "re-based 50% of the way" in n for n in res.notes), res.notes
    # the ramp's ends: t = 0 at the start (nothing changes), t = 1 from the full gap
    assert dcf.rebase_weight(dcf.CONSENSUS_SHARE_TOLERANCE) == 0.0
    assert dcf.rebase_weight(dcf.CONSENSUS_SHARE_FULL) == pytest.approx(1.0)
    assert dcf.rebase_weight(0.5) == 1.0 and dcf.rebase_weight(0.0) == 0.0
    full = _value(_misbased(_payloads(), implied=(1.0 + dcf.CONSENSUS_SHARE_FULL) * 1e9))
    assert full.fair_value == pytest.approx(base / (1 + dcf.CONSENSUS_SHARE_FULL), rel=1e-9)


def test_the_re_base_switch_is_v1_exact_with_no_nan(monkeypatch):
    monkeypatch.setattr(dcf, "CONSENSUS_SHARE_TOLERANCE", float("inf"))
    assert dcf.rebase_weight(0.3) == 0.0 and dcf.rebase_weight(float("inf")) == 0.0
    rows = [_cy(120.0, 1.0)]
    out, rep, ratio, t = dcf.consensus_on_share_basis(rows, None, 100.0)
    assert (out, rep, ratio, t) == (rows, None, None, 0.0)
    # far outside the share-unit bound: v1 neither re-bases nor refuses it as a share conflict —
    # it reaches the methods on the tripled NI, and the two methods then disagree, as in v1
    assert _value(_rescaled(3.0)).refusal_code == "models_disagree"
    res = _value(_rescaled(1.2))
    assert res.status == "ok" and math.isfinite(res.fair_value)
    assert res.fair_value == pytest.approx(_value(_payloads()).fair_value * 1.2, rel=1e-9)
    assert not any("re-based" in n for n in res.notes)


def test_a_degenerate_ramp_is_a_step_not_a_division_by_zero(monkeypatch):
    monkeypatch.setattr(dcf, "CONSENSUS_SHARE_FULL", dcf.CONSENSUS_SHARE_TOLERANCE)
    assert dcf.rebase_weight(dcf.CONSENSUS_SHARE_TOLERANCE + 1e-9) == 1.0
    assert dcf.rebase_weight(dcf.CONSENSUS_SHARE_TOLERANCE) == 0.0


@pytest.mark.parametrize("k,code", [
    (2.01, "share_count_conflict"),          # BRK-B-like: consensus on another share unit
    (1.99, None),                            # inside: re-based in full, the base value back
    (0.51, None),
    (0.49, "share_count_conflict"),          # XE-like: 0.07× in the live sweep
])
def test_a_share_basis_more_than_twice_off_refuses(k, code):
    res = _value(_rescaled(k))
    if code is None:
        assert res.status == "ok", res.refusal_code
        assert res.fair_value == pytest.approx(_value(_payloads()).fair_value, rel=1e-9)
    else:
        assert res.refusal_code == code


def test_the_share_unit_refusal_sits_at_share_count_conflict_in_the_table():
    """After currency_mismatch, before financial_company — the share_count_conflict row."""
    foreign = _prof(_rescaled(3.0), currency="EUR")
    assert _value(foreign).refusal_code == "currency_mismatch"
    bank = _prof(_rescaled(3.0), industry="Banks - Regional")
    assert _value(bank).refusal_code == "share_count_conflict"
    assert _value(_prof(_payloads(), industry="Banks - Regional")).refusal_code == "financial_company"


def _rapid_crossfade() -> Dict[str, Any]:
    """Consensus +28 %/yr from 2026 (new-window trend 28.0 %), the just-ended 2025 row at
    1/1.12 of the base (old-window trend 31.4 %). Valued on 2026-01-20, 22 % into the crossfade."""
    p = copy.deepcopy(_payloads())
    for r in p["estimates"]:
        y = int(r["date"][:4])
        if y == 2024:
            continue
        r["revenueAvg"] = 50e9 * 1.28 ** (y - 2025) if y >= 2026 else 50e9 / 1.12
        r["netIncomeAvg"] = 0.2 * r["revenueAvg"]
    return p


def test_the_tail_cap_note_fires_when_only_the_old_window_is_capped(monkeypatch):
    """dcf-v2-S2-old-window-note: the cap binds on the OLD window (78 % weight) only; the value
    is lowered (642.13 vs 652.29 uncapped) and the note must say so."""
    as_of = date(2026, 1, 20)
    res = _value(_rapid_crossfade(), as_of)
    assert res.status == "ok", res.refusal_code
    assert any("rolling forward" in n for n in res.notes)
    capped = [n for n in res.notes if "start from 30%/yr" in n]
    assert capped == ["analyst-years earnings trend of 31.4%/yr; the years after them start from 30%/yr",
                      "analyst-years revenue trend of 31.4%/yr; the years after them start from 30%/yr"]
    monkeypatch.setattr(dcf, "TAIL_START_GROWTH_CAP", 1.0)
    uncapped = _value(_rapid_crossfade(), as_of)
    assert uncapped.fair_value > res.fair_value
    assert not any("start from" in n for n in uncapped.notes)


def test_the_tail_cap_note_is_one_per_basis_with_the_higher_trend():
    """Both windows capped: one note per basis, naming the steeper trend."""
    p = copy.deepcopy(_rapid_crossfade())
    for r in p["estimates"]:
        y = int(r["date"][:4])
        if y >= 2026:
            r["revenueAvg"] = 50e9 * 1.33 ** (y - 2025)
            r["netIncomeAvg"] = 0.2 * r["revenueAvg"]
    res = _value(p, date(2026, 1, 20))
    assert res.status == "ok", res.refusal_code
    capped = [n for n in res.notes if "start from" in n]
    assert len(capped) == 2 and capped[0].startswith("analyst-years earnings trend of ")
    old_trend = float(capped[0].split("trend of ")[1].split("%")[0])
    assert old_trend > 33.0                                  # the old window's, not the new 33.0


class _ErrFMP(_FakeFMP):
    """Each named leg raises its own exception."""

    def __init__(self, p: Dict[str, Any], errors: Dict[str, BaseException]) -> None:
        super().__init__(p)
        self.errors = errors

    async def _ret(self, key: str):
        self.calls += 1
        await asyncio.sleep(0)
        if key in self.errors:
            raise self.errors[key]
        return copy.deepcopy(self.p[key])


def _http_error(status: int):
    import httpx
    req = httpx.Request("GET", "https://financialmodelingprep.com/stable/x")
    return httpx.HTTPStatusError(f"HTTP {status}", request=req, response=httpx.Response(status, request=req))


def _permanent_cases():
    from app.integrations.fmp import (FMPAuthException, FMPNotEntitledException,
                                      FMPRateLimitException, FMPUnavailableException)
    return [
        ({"estimates": FMPNotEntitledException("402")}, True),
        ({"income_annual": _http_error(404)}, True),
        ({"cash_flow_annual": _http_error(403), "estimates": FMPNotEntitledException("x")}, True),
        ({"estimates": FMPRateLimitException("429")}, False),
        ({"estimates": _http_error(429)}, False),
        ({"estimates": _http_error(408)}, False),
        ({"estimates": _http_error(500)}, False),
        ({"estimates": FMPUnavailableException("503 after retries")}, False),
        ({"estimates": asyncio.TimeoutError()}, False),
        ({"estimates": FMPAuthException("401")}, False),
        ({"estimates": _http_error(401)}, False),
        ({"estimates": _http_error(400)}, True),
        ({"estimates": FMPNotEntitledException("402"), "balance_annual": _http_error(502)}, False),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", range(13))
async def test_a_permanent_leg_refusal_is_flagged_permanent(service, case):
    """R2-permanent-leg-failure: only when EVERY failed essential leg is a permanent refusal is
    the error permanent; one transient leg makes the whole failure transient. Either way it is
    still DcfInputsUnavailableError, memoised the same way and never stored."""
    errors, permanent = _permanent_cases()[case]
    service.fmp = _ErrFMP(_payloads(), errors)
    with pytest.raises(dcf.DcfInputsUnavailableError) as ei:
        await service.get_fair_value("ACME")
    assert ei.value.permanent is permanent
    assert any(k.endswith(":ACME") for k in dcf._failed_at)          # the memo, unchanged
    assert not any(k.endswith(":ACME") for k in dcf._cache)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [0, 3])
async def test_the_failure_memo_re_raises_with_the_original_permanent_flag(service, case):
    """Within the 180 s memo the second call never reaches FMP, so its error must carry the
    first failure's flag: a permanent refusal re-raised as transient would make the report
    collector record the ticker as degraded (never cached, a fresh 20-credit generation on every
    open) and the Analysis tab say "temporarily unavailable" for a ticker that will not recover."""
    errors, permanent = _permanent_cases()[case]
    service.fmp = _ErrFMP(_payloads(), errors)
    with pytest.raises(dcf.DcfInputsUnavailableError):
        await service.get_fair_value("ACME")
    calls = service.fmp.calls
    with pytest.raises(dcf.DcfInputsUnavailableError) as again:
        await service.get_fair_value("ACME")
    assert service.fmp.calls == calls                                 # answered by the memo
    assert again.value.permanent is permanent


def test_the_unavailable_error_defaults_to_transient():
    assert dcf.DcfInputsUnavailableError("x").permanent is False
    assert dcf.DcfInputsUnavailableError("x", permanent=True).permanent is True
    assert len(_permanent_cases()) == 13


@pytest.mark.asyncio
async def test_a_cancelled_leader_fails_followers_as_unavailable(service):
    """Known item c: a follower of a cancelled leader is told the inputs are temporarily
    unavailable (DcfInputsUnavailableError), not a RuntimeError the callers read as a model bug;
    a cancellation records no failure memo."""
    gate = asyncio.Event()

    class _SlowFMP(_FakeFMP):
        async def _ret(self, key: str):
            await gate.wait()
            return await super()._ret(key)

    service.fmp = _SlowFMP(_payloads())
    leader = asyncio.create_task(service.get_fair_value("ACME"))
    for _ in range(20):
        await asyncio.sleep(0)
        if dcf._inflight:
            break
    assert dcf._inflight, "leader never registered"
    follower = asyncio.create_task(service.get_fair_value("ACME"))
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    with pytest.raises(dcf.DcfInputsUnavailableError, match="in-flight DCF computation was cancelled"):
        await follower
    assert dcf._failed_at == {} and dcf._inflight == {}
    gate.set()
    assert (await service.get_fair_value("ACME")).status == "ok"   # the next caller recomputes
