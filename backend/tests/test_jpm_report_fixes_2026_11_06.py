"""The first live bank report (JPMorgan, 2026-11-06, report 64c03303) printed five things that
were false or contradicted each other. The figures below are that report's and FMP's.

1. A bank's first forecast bar read -25.5%: reported revenue is gross of the interest it pays,
   the analyst consensus is net. On a like basis 2026 is about +14.6%.
2. The Growth card said "Rapid Sales Growth, Falling Earnings" over +3.3% and +1.4%.
3. The Insight's trends read two end points: "revenue growth is rising" on 54, 15, 3, and "ROE
   flat at 16%" beside a 17.91% card.
4. The bull case claimed "a durable competitive advantage" beside "lacking a truly durable moat".
5. The Revenue Engine's largest bar was $101.2B "Unallocated": the interest the bank pays.

And seven smaller ones, at the end of this file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
from types import SimpleNamespace

import pytest

import test_revenue_breakdown_reconciliation as reconciliation
from test_ios_revenue_reconciliation import _decl_body, _strip_comments
import test_ticker_report_schema_parity as parity
from app.schemas.revenue_breakdown import RevenueBreakdownResponse, RevenueSourceSchema
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.schemas.ticker_report import RevenueEngineResponse
from app.services import chat_context_resolver as chat
from app.services import chat_financials_tool as chat_tool
from app.services import growth_snapshot_service as gss
from app.services import revenue_breakdown_service as rb
from app.services._insider_common import is_ceo_role
from app.services.agents import fmp_tools
from app.services.agents import narrative_prompts as np_
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.card_verdict import generate_card_verdict
from app.services.agents.persona_config import get_persona_config
from test_ticker_report_schema_parity import _no_live_benchmark_lookup  # noqa: F401

BANK = "Banks - Diversified"
INSURER = "Insurance - Property & Casualty"
BUFFETT = get_persona_config("warren_buffett")


def _income_row(year, revenue, interest=0.0, eps=20.0, interest_income=None):
    return {
        "date": f"{year}-12-31", "fiscalYear": str(year), "period": "FY", "revenue": revenue,
        "interestExpense": interest, "epsDiluted": eps,
        "interestIncome": interest * 2 if interest_income is None else interest_income,
    }


JPM_INCOME = [
    _income_row(2025, 279_745e6, 97_898e6, 20.02, 193_341e6),
    _income_row(2024, 270_789e6, 100e9, 19.75),
    _income_row(2023, 236_273e6, 80e9, 16.23),
    _income_row(2022, 153_824e6, 30e9, 12.09),
    _income_row(2021, 127_238e6, 8e9, 15.36),
    _income_row(2020, 129_843e6, 10e9, 8.88),
]
JPM_ESTIMATES = [
    {"date": "2026-12-31", "revenueAvg": 208.354e9, "epsAvg": 24.69},
    {"date": "2027-12-31", "revenueAvg": 217.09e9, "epsAvg": 25.59},
    {"date": "2028-12-31", "revenueAvg": 228.17e9, "epsAvg": 27.43},
]


def _company(industry=BANK, income=None, ticker="JPM", estimates=None):
    return SimpleNamespace(
        ticker=ticker, non_lender_member=False, profile={"industry": industry},
        income=JPM_INCOME if income is None else income,
        estimates=JPM_ESTIMATES if estimates is None else estimates,
    )


def _next_year(revenue, year=2026):
    return [{"date": f"{year}-12-31", "revenueAvg": revenue, "epsAvg": 2.0}]


def _bank_report(industry=BANK, income=None, estimates=None):
    """The real `_build_sections` + `assemble_report`, on JPMorgan's rows."""
    out = parity._make_collected_data(ticker="JPM")
    out.profile["industry"] = industry
    out.profile["sector"] = "Financial Services"
    out.income = JPM_INCOME if income is None else income
    out.estimates = JPM_ESTIMATES if estimates is None else estimates
    coll = C.TickerReportDataCollector()
    coll._compute_metrics(out)
    coll._build_sections(out)
    return out, coll.assemble_report(out, parity.stage_a_fallback())


def _first_forecast(rows):
    return next(row for row in rows if row["is_forecast"])


# ── 1. the forecast basis ────────────────────────────────────────────────────

def test_a_banks_first_forecast_bar_has_no_revenue_change():
    rows = C._build_annual_timeline(JPM_INCOME, JPM_ESTIMATES, revenue_crosses_basis=True)
    first = _first_forecast(rows)
    assert first["period"] == "2026"
    assert first["revenue_yoy_pct"] is None
    assert first["revenue_label"] == "$208.4B", "the bar itself stays: the consensus is honest"
    later = [r["revenue_yoy_pct"] for r in rows if r["is_forecast"]][1:]
    assert later == [4.2, 5.1], "estimate against estimate is one basis"
    assert [r["revenue_yoy_pct"] for r in rows if not r["is_forecast"]][-1] == 3.3


def test_the_default_is_exactly_what_it_was():
    rows = C._build_annual_timeline(JPM_INCOME, JPM_ESTIMATES)
    assert _first_forecast(rows)["revenue_yoy_pct"] == -25.5


def test_a_real_decline_at_an_operating_company_still_shows():
    """The gate is the company type, never the size of the fall."""
    income = [_income_row(2025, 100e9), _income_row(2024, 90e9)]
    estimates = [{"date": "2026-12-31", "revenueAvg": 60e9, "epsAvg": 2.0}]
    partial = C._build_revenue_forecast_partial(estimates, None, None, income)
    assert _first_forecast(partial["annual_timeline"])["revenue_yoy_pct"] == -40.0
    assert partial["projections"][0]["revenue_yoy_pct"] == -40.0


def test_the_projections_carry_no_cross_basis_change_either():
    """3cde8d59 reads the first projection against the last report: Stage B was told -26%."""
    partial = C._build_revenue_forecast_partial(
        JPM_ESTIMATES, 4.6, 5.4, JPM_INCOME, revenue_crosses_basis=True,
    )
    assert [p["revenue_yoy_pct"] for p in partial["projections"]] == [None, 4.2, 5.1]
    assert _first_forecast(partial["annual_timeline"])["revenue_yoy_pct"] is None


def test_the_growth_rate_never_crosses_the_bases():
    _, report = _bank_report()
    assert report["revenue_forecast"]["cagr"] == pytest.approx(4.6, abs=0.1), "estimates only"


def test_the_collector_blanks_it_for_a_bank_and_only_for_one():
    _, report = _bank_report()
    timeline = report["revenue_forecast"]["annual_timeline"]
    assert _first_forecast(timeline)["revenue_yoy_pct"] is None
    assert report["revenue_forecast"]["projections"][0]["revenue_yoy_pct"] is None
    _, other = _bank_report("Software - Infrastructure")
    assert _first_forecast(other["revenue_forecast"]["annual_timeline"])["revenue_yoy_pct"] == -25.5


def test_an_insurers_chip_goes_but_it_is_not_marked_gross():
    """Its estimates may be on another basis too, so no chip; no sentence, which would be false."""
    income = [_income_row(2025, 80e9, 0.3e9), _income_row(2024, 70e9, 0.3e9)]
    _, report = _bank_report(INSURER, income=income)
    assert _first_forecast(report["revenue_forecast"]["annual_timeline"])["revenue_yoy_pct"] is None
    assert "revenue_gross_of_interest" not in report["_scoring_inputs"]["_style_signals"]
    assert np_._revenue_forecast_basis_note(report) == ""


@pytest.mark.parametrize("income,estimate,expected", [
    ([_income_row(2025, 100.0, 10.0)], 90.0, True),
    ([_income_row(2025, 100.0, 9.9)], 90.0, False),
    ([_income_row(2025, 100.0, 0.0)], 90.0, False),      # FMP zero-fills real lenders
    ([{"date": "2025-12-31", "revenue": 100.0}], 90.0, False),
    ([_income_row(2025, 0.0, 10.0)], 90.0, False),
    ([_income_row(2025, -5.0, 10.0)], 90.0, False),
    ([{"date": "2025-12-31", "revenue": "n/a", "interestExpense": "x"}], 90.0, False),
    ([], 90.0, False),
    (["junk", None], 90.0, False),
    # Unsorted: the newest year decides.
    ([_income_row(2023, 100.0, 50.0), _income_row(2025, 100.0, 1.0)], 90.0, False),
    ([_income_row(2023, 100.0, 1.0), _income_row(2025, 100.0, 50.0)], 60.0, True),
    # The consensus has to agree. A row already net: its revenue is near the estimate.
    ([_income_row(2025, 81e9, 54e9)], 86e9, False),
    # A lender whose analysts use the gross figure.
    ([_income_row(2025, 50e9, 15e9)], 52e9, False),
    ([_income_row(2025, 100.0, 40.0)], 80.0, False),     # exactly midway proves nothing
])
def test_what_reads_as_gross_of_interest(income, estimate, expected):
    company = _company(income=income, estimates=_next_year(estimate))
    assert C._revenue_gross_of_interest(company) is expected


def test_jpmorgan_reads_as_gross():
    assert C._revenue_gross_of_interest(_company()) is True


def test_no_estimate_is_no_mark():
    assert C._revenue_gross_of_interest(_company(estimates=[{"date": "2020-12-31"}])) is False


@pytest.mark.parametrize("estimate", [0.0, -5.0])
def test_a_consensus_that_does_not_exist_proves_nothing(estimate):
    company = _company(income=[_income_row(2025, 100.0, 40.0)], estimates=_next_year(estimate))
    assert C._revenue_gross_of_interest(company) is False


def test_real_companies_land_on_the_right_side():
    """FMP FY2025 revenue, interest paid and next-year consensus, read 2026-11-05 ($B)."""
    marked = {
        "JPM": (279.7, 97.9, 208.4), "BAC": (191.6, 78.5, 122.6), "C": (168.3, 83.1, 96.1),
        "USB": (42.9, 14.3, 31.1), "SYF": (19.1, 4.1, 15.1),
    }
    unmarked = {
        "SCHW": (27.7, 3.8, 28.1), "AXP": (80.5, 8.2, 79.4), "SOFI": (4.8, 1.2, 5.0),
        "CACC": (2.3, 0.5, 2.1), "RKT": (6.9, 0.8, 11.1), "AGNC": (1.9, 2.8, 4.5),
    }
    for expected, table in ((True, marked), (False, unmarked)):
        for ticker, (revenue, interest, estimate) in table.items():
            company = _company(
                income=[_income_row(2025, revenue, interest)], estimates=_next_year(estimate),
            )
            assert C._revenue_gross_of_interest(company) is expected, ticker


CUSTODIAN = "Asset Management"


def test_a_custody_bank_filed_as_an_asset_manager_is_read_like_a_bank():
    income = [_income_row(2025, 22.6e9, 8.7e9, 9.0), _income_row(2024, 20.0e9, 7.0e9, 8.0)]
    custodian = _company(CUSTODIAN, income=income, ticker="STT", estimates=_next_year(15.9e9))
    assert C._revenue_crosses_basis(custodian) is True
    assert C._revenue_gross_of_interest(custodian) is True
    assert C._score_excluded(custodian) == frozenset(), "the owner's row decision stands"


def test_a_real_asset_managers_fall_still_shows():
    income = [_income_row(2025, 20e9, 2.5e9)]
    manager = _company(CUSTODIAN, income=income, ticker="BLK", estimates=_next_year(15e9))
    assert C._revenue_crosses_basis(manager) is False
    assert C._revenue_gross_of_interest(manager) is False


def test_the_collector_treats_a_custody_bank_as_crossing():
    out = parity._make_collected_data(ticker="STT")
    out.profile.update(industry=CUSTODIAN, sector="Financial Services")
    out.income = [_income_row(2025, 22.6e9, 8.7e9, 9.0), _income_row(2024, 20.0e9, 7.0e9, 8.0)]
    out.estimates = _next_year(15.9e9)
    coll = C.TickerReportDataCollector()
    coll._build_sections(out)
    report = coll.assemble_report(out, parity.stage_a_fallback())
    assert _first_forecast(report["revenue_forecast"]["annual_timeline"])["revenue_yoy_pct"] is None
    assert report["_scoring_inputs"]["_style_signals"]["revenue_gross_of_interest"] is True
    assert "excluded_rows" not in report["_scoring_inputs"]["_style_signals"]


def test_the_estimate_read_is_the_year_after_the_report():
    """FMP's list runs newest first and holds past years. JPMorgan's far year sits only 1.2%
    under the midpoint, so reading the wrong row is one step from the wrong answer."""
    income = [_income_row(2025, 100.0, 40.0)]
    newest_first = [
        {"date": "2028-12-31", "revenueAvg": 99.0, "epsAvg": 2.0},
        {"date": "2027-12-31", "revenueAvg": 98.0, "epsAvg": 2.0},
        {"date": "2026-12-31", "revenueAvg": 65.0, "epsAvg": 2.0},
        {"date": "2024-12-31", "revenueAvg": 99.5, "epsAvg": 2.0},
    ]
    assert C._revenue_gross_of_interest(_company(income=income, estimates=newest_first)) is True


@pytest.mark.parametrize("company", [
    _company("Utilities - Regulated Electric"),
    _company(""),
    SimpleNamespace(profile=None, income=JPM_INCOME, non_lender_member=False, ticker="JPM"),
    SimpleNamespace(profile={"industry": BANK}, income=None, non_lender_member=False, ticker="JPM"),
])
def test_an_indebted_operating_company_is_not_gross(company):
    assert C._revenue_gross_of_interest(company) is False


def test_the_report_marks_a_lender():
    _, report = _bank_report()
    assert report["_scoring_inputs"]["_style_signals"]["revenue_gross_of_interest"] is True


def _forecast_shell(flag=True, timeline=None):
    signals = {"revenue_gross_of_interest": True} if flag else {}
    return {
        "_scoring_inputs": {"_style_signals": signals},
        "revenue_forecast": {
            "cagr": 4.6, "eps_growth": 5.4, "management_guidance": "unknown",
            "projections": [
                {"period": "2026", "revenue_label": "$208.4B", "revenue_yoy_pct": None, "eps_label": "$24.69"},
                {"period": "2027", "revenue_label": "$217.1B", "revenue_yoy_pct": 4.2, "eps_label": "$25.59"},
            ],
            "annual_timeline": timeline if timeline is not None else [
                {"period": "2023", "is_forecast": False, "revenue_yoy_pct": 53.6},
                {"period": "2024", "is_forecast": False, "revenue_yoy_pct": 14.6},
                {"period": "2025", "is_forecast": False, "revenue_yoy_pct": 3.3},
                {"period": "2026", "is_forecast": True, "revenue_yoy_pct": None},
            ],
        },
    }


def test_the_forecast_note_needs_both_a_mark_and_both_kinds_of_bar():
    assert "can't be compared" in np_._revenue_forecast_basis_note(_forecast_shell())
    assert np_._revenue_forecast_basis_note(_forecast_shell(flag=False)) == ""
    only_actuals = [{"is_forecast": False}]
    assert np_._revenue_forecast_basis_note(_forecast_shell(timeline=only_actuals)) == ""
    assert np_._revenue_forecast_basis_note(_forecast_shell(timeline=[])) == ""
    assert np_._revenue_forecast_basis_note({}) == ""


def test_the_note_never_says_it_is_not_a_decline():
    """On a bank whose net revenue really falls, that sentence would be false."""
    note = np_._revenue_forecast_basis_note(_forecast_shell()).lower()
    assert "decline" not in note and "fall" not in note and "drop" not in note


def test_a_lenders_forecast_prompt_hides_the_history_it_cannot_compare():
    prompt = np_._revenue_forecast_insight_prompt(BUFFETT, "EVID", _forecast_shell())
    assert "+54%" not in prompt and "+15%" not in prompt
    assert "never compare a forecast year with a reported year" in prompt
    assert "never set them beside a forecast" in prompt
    for gone in ("recent ACTUAL trend", "HISTORICAL TREND", "historical trend"):
        assert gone not in prompt


def test_a_marked_report_tells_every_prompt_the_two_do_not_compare():
    out, _ = _bank_report()
    text = C.build_financial_context(out)
    assert "these estimates usually leave it out" in text
    assert text.index("Analyst Estimates:") < text.index("these estimates usually leave it out")


def test_an_unmarked_report_is_told_nothing():
    out, _ = _bank_report("Software - Infrastructure")
    assert "interest paid out" not in C.build_financial_context(out)


def test_every_other_forecast_prompt_is_unchanged():
    prompt = np_._revenue_forecast_insight_prompt(BUFFETT, "EVID", _forecast_shell(flag=False))
    assert "HISTORICAL TREND (for contrast): recent actual revenue YoY +54%, +15%, +3%" in prompt
    assert "how that reads against the recent ACTUAL trend" in prompt
    assert "against the track record or the historical trend" in prompt
    assert "never compare a forecast year" not in prompt


def _jobs(shell, persona=BUFFETT):
    shell.setdefault("overall_assessment", None)
    return {job.label: job for job in np_.build_narrative_jobs(persona, "EVID", shell)}


def test_the_note_rides_on_model_text_and_on_the_fallback():
    job = _jobs(_forecast_shell())["revenue_forecast_insight"]
    note = np_._revenue_forecast_basis_note(_forecast_shell())
    assert job.postprocess("Model text.") == f"Model text. {note}"
    assert job.fallback_value.endswith(note)


def test_no_other_companys_forecast_job_gains_a_note():
    job = _jobs(_forecast_shell(flag=False))["revenue_forecast_insight"]
    assert job.postprocess is None
    assert job.fallback_value == np_.FALLBACK["revenue_forecast_insight"]


# ── 2. the Growth card label ─────────────────────────────────────────────────

def _growth_verdict(rows, rating=2, **kw):
    scored = [(key, score) for key, score, _ in rows]
    values = {key: value for key, _, value in rows if value is not None}
    return generate_card_verdict("Growth", rating, "industry", scored, row_values=values, **kw)


def test_jpmorgans_card_says_what_its_rows_say():
    label, sentiment = _growth_verdict([
        ("revenue_growth", 4, 3.3), ("eps_growth", 1, 1.4), ("operating_income_growth", 1, -3.3),
    ])
    assert label == "Sales Lead Peers, Earnings Lag Peers"
    assert sentiment == "neutral"


@pytest.mark.parametrize("key,score,value,expected", [
    ("revenue_growth", 4, 10.1, "Rapid Sales Growth"),
    ("revenue_growth", 5, 25.0, "Rapid Sales Growth"),
    ("revenue_growth", 4, 10.0, "Rapid Sales Growth"),
    ("revenue_growth", 4, 9.9, "Sales Lead Peers"),
    ("revenue_growth", 4, 3.3, "Sales Lead Peers"),
    ("revenue_growth", 5, -1.0, "Sales Lead Peers"),         # the sector fell further
    ("revenue_growth", 2, -4.0, "Slowing Sales"),
    ("revenue_growth", 1, 2.0, "Sales Lag Peers"),
    ("revenue_growth", 2, 0.0, "Flat Sales"),
    ("eps_growth", 4, 14.0, "Surging Earnings"),
    ("eps_growth", 4, 6.0, "Earnings Lead Peers"),
    ("eps_growth", 1, -2.4, "Falling Earnings"),
    ("eps_growth", 1, 0.0, "Flat Earnings"),
    ("eps_growth", 1, -0.0, "Flat Earnings"),
    ("eps_growth", 1, 1.4, "Earnings Lag Peers"),
    ("operating_income_growth", 4, 0.5, "Rising Operating Profit"),
    ("operating_income_growth", 4, 0.1, "Rising Operating Profit"),
    ("operating_income_growth", 4, 0.0, "Op Profit Leads Peers"),
    ("operating_income_growth", 4, -2.0, "Op Profit Leads Peers"),
    ("operating_income_growth", 1, -3.3, "Shrinking Operating Profit"),
    ("operating_income_growth", 1, 0.0, "Flat Operating Profit"),
    ("operating_income_growth", 2, 1.0, "Op Profit Lags Peers"),
    ("fcf_growth", 4, 30.0, "Cash Flow Surging"),
    ("fcf_growth", 4, 4.0, "Cash Flow Leads Peers"),
    ("fcf_growth", 1, -17.8, "Falling Cash Flow"),           # PYPL: a decline, not a burn
    ("fcf_growth", 2, 0.0, "Flat Cash Flow"),
    ("fcf_growth", 2, 5.0, "Cash Flow Lags Peers"),
])
def test_each_phrase_needs_its_row_to_agree(key, score, value, expected):
    assert _growth_verdict([(key, score, value)])[0] == expected


def test_without_row_values_every_phrase_is_as_it_was():
    assert generate_card_verdict(
        "Growth", 2, "industry",
        [("revenue_growth", 4), ("fcf_growth", 1)],
    )[0] == "Rapid Sales Growth, Burning Cash"


def test_a_row_with_no_value_keeps_its_phrase():
    assert _growth_verdict([("eps_growth", 1, None)])[0] == "Falling Earnings"


def test_the_sentiment_does_not_move():
    rows = [("revenue_growth", 4), ("eps_growth", 1)]
    with_values = generate_card_verdict(
        "Growth", 2, "industry", rows, row_values={"revenue_growth": 3.3, "eps_growth": 1.4},
    )
    assert with_values[1] == generate_card_verdict("Growth", 2, "industry", rows)[1]


@pytest.mark.parametrize("key", ["revenue_growth", "eps_growth", "fcf_growth", "operating_income_growth"])
@pytest.mark.parametrize("value", [
    -100.0, -40.0, -10.0, -9.9, -0.1, -0.04, -0.01, 0.0, 0.01, 0.04, 0.05, 0.1, 3.3, 9.9, 9.96,
    10.0, 10.01, 10.04, 10.1, 19.9, 20.0, 40.0, 500.0,
])
def test_no_comparison_never_reads_as_one(key, value):
    """With no median the score is absolute. A "Peers" phrase there would claim one nobody made.
    Through the printed row: 10.04 scores 4, prints +10.0% and must stay out of it."""
    score = gss._growth_score(value, None)
    row = SimpleNamespace(metric_key=key, value=gss._fmt_growth(value))
    printed = C._growth_row_values([row])[key]
    label, _ = _growth_verdict([(key, score, printed)], rating=3)
    assert "Peers" not in label


@pytest.mark.parametrize("text,expected", [
    ("+3.3%", 3.3), ("-3.3%", -3.3), ("3.3%", 3.3), ("+1,234.5%", 1234.5), (" +0.0% ", 0.0),
])
def test_a_growth_row_prints_a_percentage(text, expected):
    row = SimpleNamespace(metric_key="eps_growth", value=text)
    assert C._growth_row_values([row]) == {"eps_growth": expected}


@pytest.mark.parametrize("text", ["N/M", "—", "", "N/A", None, 3.3, "$1.2B", "+%", "1.2x"])
def test_a_row_that_prints_none_gives_none(text):
    assert C._growth_row_values([SimpleNamespace(metric_key="eps_growth", value=text)]) == {}


def test_the_card_reads_its_own_rows():
    rows = [
        SnapshotMetricResponse(name="Revenue Growth (YoY)", value="+3.3%", metric_key="revenue_growth", score=4),
        SnapshotMetricResponse(name="EPS Growth", value="+1.4%", metric_key="eps_growth", score=1),
        SnapshotMetricResponse(name="Free Cash Flow Growth (YoY)", value="N/M", metric_key="fcf_growth"),
        SnapshotMetricResponse(name="Operating Income Growth", value="-3.3%", metric_key="operating_income_growth", score=1),
    ]
    snap = SnapshotItemResponse(category="Growth", rating=2, metrics=rows)
    card = C._snapshot_to_card("Growth", snap, peer_group_level="industry", per_row_peers=True)
    assert card["quality_label"] == "Sales Lead Peers, Earnings Lag Peers"


# ── 3. the trajectory ────────────────────────────────────────────────────────

@pytest.mark.parametrize("values,expected", [
    ([-2.0, 20.9, 53.6, 14.6, 3.3], "uneven"),    # JPM revenue growth, "rising" on its ends
    ([16.4, 12.9, 15.1, 17.0, 15.7], "uneven"),   # JPM ROE, "flat"
    ([46.8, 30.0, 26.1, 27.7, 26.0], "uneven"),   # JPM operating margin, flat since 2023
    ([38.0, 24.5, 21.0, 21.6, 20.4], "uneven"),
    ([1.3, 1.0, 1.3, 1.5, 1.3], "uneven"),
    ([10.29, 11.08, 10.47, 12.11, 16.07], "rising"),
    ([1.23, 1.5, 1.29, 2.0, 2.47], "rising"),
    ([73.0, -21.3, 34.2, 21.7, 1.4], "falling"),
    ([66.3, -22.5, 33.5, 21.9, -3.3], "falling"),
    ([9.72, 9.03, 9.55, 8.26, 6.22], "falling"),
    ([-2.3, -16.8, 36.9, -61.0, 115.6], "uneven"),  # BRK FCF growth: reverses at every step
    ([10.0, 10.1, 10.0, 10.1, 10.0], "flat"),       # a wiggle is not a swing
    # A smooth drift keeps its direction, however slow.
    ([100, 102, 104.04, 106.12, 108.24], "rising"),
    ([100, 98, 96.04, 94.12, 92.24], "falling"),
    ([40, 41, 42, 42.5, 43], "rising"),
    ([0.26, 0.6, 0.63, 0.68, 0.71], "rising"),
    # A rise with a small dip stays a rise.
    ([12.7, 13.8, 13.2, 16.0, 15.9], "rising"),     # BRK operating margin
    ([10, 12, 11, 14, 13.5], "rising"),
    ([10, 13, 12, 15], "rising"),
    ([18, 20, 19, 25], "rising"),
    ([25, 21, 22, 18], "falling"),
    # The false "rising" on short paths and after a collapse.
    ([-2, 54, 3.3], "uneven"),
    ([10, 50, 12], "uneven"),
    ([10, 2, 11], "uneven"),
    ([10, 20, 5, 25], "uneven"),
    ([5, 10, 20, 60, 25], "uneven"),
    ([-2, 10, 15, 54, 21], "uneven"),
    ([10, 30, 40, 40.5, 41], "uneven"),             # a real two-year stall
    ([100, 110, 105, 115, 110], "rising"),          # zigzag, but inside a narrow range
    ([10.0, 12.0], "rising"),
    ([12.0, 10.0], "falling"),
    ([10.0, 10.0], "flat"),
    ([10.0, 11.0, 12.0], "rising"),
    ([0.0, 0.0, 0.0], "flat"),
    ([0.0, 5.0, 10.0], "rising"),
])
def test_the_direction_of_a_path(values, expected):
    assert np_._path_direction(values) == expected


def _history(values, start=2021):
    return [{"period": str(start + i), "value": v} for i, v in enumerate(values)]


def test_an_uneven_line_prints_every_year_and_a_steady_one_its_ends():
    uneven = {"label": "Revenue Growth (YoY)", "history_unit": "percent", "value": "+3.3%",
              "annual_history": _history([-2.0, 20.9, 53.6, 14.6, 3.3])}
    assert np_._metric_trajectory_line(uneven, "industry") == (
        "Revenue Growth -2.0%→21%→54%→15%→3.3% over 4y (uneven)"
    )
    steady = {"label": "P/B", "history_unit": "x",
              "annual_history": _history([1.2, 1.5, 1.3, 2.0, 2.5])}
    assert np_._metric_trajectory_line(steady, "industry") == "P/B 1.2×→2.5× over 4y (rising)"


def test_a_card_on_the_last_twelve_months_is_named_as_the_latest():
    roe = {"label": "Return on Equity (ROE) (1.42x sector avg 12.6%)", "history_unit": "percent",
           "value": "17.91%", "annual_history": _history([16.4, 12.9, 15.1, 17.0, 15.7]),
           "sector_annual_history": _history([11.0, 11.0, 11.0, 11.0, 11.0])}
    line = np_._metric_trajectory_line(roe, "industry")
    assert line == (
        "Return on Equity 16%→13%→15%→17%→16% over 4y (uneven), "
        "above industry 11% at fiscal year-end, latest 17.91%"
    )


def test_a_peer_gap_is_not_read_off_the_ends_of_an_uneven_path():
    row = {"label": "Net Margin", "history_unit": "percent",
           "annual_history": _history([38.0, 24.5, 21.0, 21.6, 20.4]),
           "sector_annual_history": _history([10.0, 12.0, 14.0, 15.0, 15.0])}
    assert "gap" not in np_._metric_trajectory_line(row, "industry")


@pytest.mark.parametrize("value,fiscal,unit,expected", [
    ("17.91%", 15.7, "percent", "17.91%"),
    ("14.13", 16.07, "x", "14.13"),
    ("14.13x", 16.07, "x", "14.13x"),
    ("+3.3%", 3.31, "percent", None),     # a Growth row IS that fiscal year
    ("15.7%", 15.72, "percent", None),    # reads the same once formatted
    ("—", 15.7, "percent", None),
    ("N/M", 15.7, "percent", None),
    ("N/A", 15.7, "percent", None),
    ("$1.2B", 15.7, "percent", None),
    ("", 15.7, "percent", None),
    (None, 15.7, "percent", None),
    (17.91, 15.7, "percent", None),
])
def test_the_latest_figure(value, fiscal, unit, expected):
    assert np_._card_latest({"value": value}, fiscal, unit) == expected


def test_the_insight_prompt_explains_both_marks():
    shell = {"overall_assessment": {}, "fundamental_metrics": []}
    prompt = np_._overall_assessment_text_prompt(BUFFETT, "EVID", shell)
    assert "marked (uneven) has no single direction" in prompt
    assert 'ends with "latest"' in prompt


# ── 4. the moat verdict ──────────────────────────────────────────────────────

def _pillar(name, score, peer=None, source="deterministic"):
    row = {"name": name, "score": score, "source": source}
    if peer is not None:
        row.update(peer_score=peer, peer_source="industry")
    return row


JPM_MOAT = {"dimensions": [
    _pillar("Switching Costs", 9.0, source="ai_legacy"),
    _pillar("Network Effects", 3.9, 3.6),
    _pillar("Brand Power", 5.6, 4.7),
    _pillar("Cost Advantage", 4.8, 4.4),
    _pillar("Intangible Assets", 8.0, source="ai_legacy"),
]}


def test_an_ai_estimate_never_lifts_the_verdict():
    assert np_._moat_is_none(JPM_MOAT) is True
    assert "NO MOAT (top measured pillar 5.6, a narrow moat needs 7.0)" in np_._moat_verdict_line(JPM_MOAT)


def test_no_edge_means_no_bull_point():
    assert "the moat is no bull point" in np_._moat_verdict_line(JPM_MOAT)


def test_a_real_edge_stays_citable():
    moat = {"dimensions": [_pillar("Cost Advantage", 6.8, 4.0), _pillar("Brand Power", 5.0, 4.7)]}
    line = np_._moat_verdict_line(moat)
    assert "The only moat point allowed: Cost Advantage above its industry peer." in line
    assert "no bull point" not in line


@pytest.mark.parametrize("moat", [
    {"dimensions": [_pillar("Brand Power", 7.0)]},                # narrow
    {"dimensions": [_pillar("Brand Power", 9.0)]},                # wide
    {"dimensions": [_pillar("Switching Costs", 3.0, source="ai_legacy")]},  # nothing measured
    {"dimensions": []},
    {"dimensions": None},
    {},
    None,
    "junk",
    {"dimensions": [_pillar("Brand Power", "n/a")]},
    {"dimensions": [_pillar("Brand Power", float("nan"))]},
    {"dimensions": [_pillar("Brand Power", 10 ** 400)]},          # float() overflows
])
def test_no_verdict_line_unless_the_verdict_is_none(moat):
    assert np_._moat_verdict_line(moat) is None
    assert np_._moat_is_none(moat) is False


def test_a_broken_score_does_not_hide_a_real_one():
    moat = {"dimensions": [_pillar("Brand Power", float("nan")), _pillar("Cost Advantage", 3.0)]}
    assert np_._moat_is_none(moat) is True
    assert "top measured pillar 3.0" in np_._moat_verdict_line(moat)


def test_only_an_industry_peer_makes_an_edge():
    pillar = _pillar("Cost Advantage", 6.8, 4.0)
    pillar["peer_source"] = "ai_estimate"
    assert "no bull point" in np_._moat_verdict_line({"dimensions": [pillar]})


def test_the_digest_carries_the_verdict_under_the_pillars():
    lines = np_._digest_moat({"moat_competition": JPM_MOAT})
    assert lines[0].startswith("MOAT (0-10, measured")
    assert lines[1].startswith("  Moat verdict: NO MOAT")


def test_a_narrow_moats_digest_is_unchanged():
    moat = {"dimensions": [_pillar("Brand Power", 7.4, 5.0)]}
    assert not any("verdict" in line for line in np_._digest_moat({"moat_competition": moat}))


def test_the_verdict_agrees_with_the_durability_note():
    shell = {"moat_competition": JPM_MOAT}
    assert "NO moat" in np_._moat_durability_note_prompt(BUFFETT, "EVID", shell)
    assert np_._moat_is_none(shell["moat_competition"])
    narrow = {"moat_competition": {"dimensions": [_pillar("Brand Power", 7.4, 5.0)]}}
    assert "NARROW moat" in np_._moat_durability_note_prompt(BUFFETT, "EVID", narrow)
    assert not np_._moat_is_none(narrow["moat_competition"])


def test_only_a_no_moat_company_gets_the_thesis_rule():
    def prompt(**kw):
        return np_.build_thesis_synthesis_prompt(BUFFETT, "JPM Co", "JPM", "E", "D", 2, 3, **kw)

    assert "the digest's Moat verdict outranks your lens" in prompt(no_moat=True)
    assert "Moat verdict" not in prompt()


@pytest.mark.parametrize("point", [
    "Brand Power 5.6 (peer 4.7) and multiple moat pillars suggest a durable competitive advantage.",
    "A wide moat protects its returns.",
    "Strong competitive edge in payments.",
    "Lasting moat from its scale.",
    "durable economic moat",
    "A deep competitive advantage",
    "Brand Power 5.6 (peer 4.7) and multiple moat pillars suggest a durable advantage.",
    "A wide-moat franchise: ROE 17.9%.",
    "Its moat is durable",
    "Moat remains strong",
    "A durable edge in deposits",
    "A durable franchise",
    "A lasting brand",
    "Sustained competitive advantage",
    "Entrenched competitive advantage",
    "Robust moat",
])
def test_these_claim_a_moat(point):
    assert np_._MOAT_CLAIM_RE.search(point)


@pytest.mark.parametrize("point", [
    "Brand Power 5.6 (peer 4.7) beats its industry peer.",
    "Cost Advantage moat 6.8 vs peer 4.0",
    "High 17.9% Return on Equity demonstrates superior profitability.",
    "Share count -5.6% since Q4 '24",
    "Strong capital allocation with buybacks",
    "Strong Cost Advantage 6.8 beats its peer 4.0 by 2.8.",     # the one point the verdict allows
    "Strong Network Effects 6.8 vs peer 4.0.",
    "Strong Brand Power 6.2 vs peer 4.0",
    "Wide branch network of 5,000 sites supports 17.9% ROE.",
    "Strong ROE advantage: 17.9% vs industry 12.6%.",
    "Deep client franchise produced $1.2B of fees.",
    "Wide margin over its peers",
])
def test_these_do_not(point):
    assert not np_._MOAT_CLAIM_RE.search(point)


def test_a_claim_is_dropped_and_logged(caplog):
    points = ["A durable competitive advantage.", "High 17.9% ROE."]
    with caplog.at_level(logging.WARNING):
        assert np_._without_moat_claims(points, "JPM") == ["High 17.9% ROE."]
    assert "[thesis-moat-claim] JPM: dropped 1 of 2" in caplog.text


class _ThesisGemini:
    def __init__(self, bull, *, text=None, raises=None):
        self.bull = bull
        self.text = text
        self.raises = raises
        self.prompt = None

    async def generate_json(self, prompt, **kw):
        self.prompt = prompt
        if self.raises is not None:
            raise self.raises
        if self.text is not None:
            return {"text": self.text}
        return {"text": json.dumps({"bull_case": self.bull, "bear_case": ["P/B 2.43 is stretched."]})}


def _thesis_report(moat):
    return {
        "symbol": "JPM", "company_name": "JPMorgan", "moat_competition": moat,
        "core_thesis": {"bull_case": ["stage a"], "bear_case": ["stage a"]},
    }


@pytest.mark.asyncio
async def test_the_bull_case_loses_the_claim_jpmorgan_shipped():
    report = _thesis_report(JPM_MOAT)
    gemini = _ThesisGemini([
        "Brand Power 5.6 (peer 4.7) and multiple moat pillars suggest a durable competitive advantage.",
        "High 17.9% Return on Equity (1.42x industry avg) demonstrates superior profitability.",
    ])
    await np_.synthesize_core_thesis(report, BUFFETT, gemini, "EVID")
    assert report["core_thesis"]["bull_case"] == [
        "High 17.9% Return on Equity (1.42x industry avg) demonstrates superior profitability.",
    ]
    assert "Moat verdict: NO MOAT" in gemini.prompt
    assert "the digest's Moat verdict outranks your lens" in gemini.prompt


STAGE_A_CLAIM = "ROE 17.9% and a durable competitive advantage in deposits."


@pytest.mark.parametrize("gemini", [
    _ThesisGemini([], raises=RuntimeError("boom")),
    _ThesisGemini([], text="not json"),
    _ThesisGemini([]),                                         # an incomplete thesis
    _ThesisGemini(["A wide moat protects returns."]),           # every synthesized point goes
])
@pytest.mark.asyncio
async def test_a_kept_stage_a_thesis_is_read_too(gemini):
    report = _thesis_report(JPM_MOAT)
    report["core_thesis"]["bull_case"] = [STAGE_A_CLAIM, "ROE 17.9% beats its peers."]
    await np_.synthesize_core_thesis(report, BUFFETT, gemini, "EVID")
    assert report["core_thesis"]["bull_case"] == ["ROE 17.9% beats its peers."]
    assert report["core_thesis"]["bear_case"] == ["stage a"], "the whole Stage A thesis stays"


@pytest.mark.asyncio
async def test_stage_a_is_never_emptied_because_that_refunds_the_report(caplog):
    report = _thesis_report(JPM_MOAT)
    report["core_thesis"]["bull_case"] = [STAGE_A_CLAIM]
    with caplog.at_level(logging.ERROR):
        await np_.synthesize_core_thesis(report, BUFFETT, _ThesisGemini([], text="x"), "EVID")
    assert report["core_thesis"]["bull_case"] == [STAGE_A_CLAIM]
    assert "keeping them rather than emptying the side" in caplog.text


@pytest.mark.asyncio
async def test_a_stage_a_claim_stays_where_the_moat_is_real():
    report = _thesis_report({"dimensions": [_pillar("Brand Power", 7.4, 5.0)]})
    report["core_thesis"]["bull_case"] = [STAGE_A_CLAIM]
    await np_.synthesize_core_thesis(report, BUFFETT, _ThesisGemini([], text="x"), "EVID")
    assert report["core_thesis"]["bull_case"] == [STAGE_A_CLAIM]


@pytest.mark.parametrize("thesis", [None, "junk", {"bull_case": None}, {"bull_case": "text"}, {}])
def test_a_broken_thesis_does_not_raise(thesis):
    np_._gate_stage_a_moat_claims({"moat_competition": JPM_MOAT, "core_thesis": thesis}, "JPM")


@pytest.mark.asyncio
async def test_the_left_out_filter_runs_first_and_a_lone_claim_still_goes():
    """Two filters, one side: neither may leave a claim the other kept."""
    report = _thesis_report(JPM_MOAT)
    report["_scoring_inputs"] = {"_style_signals": {"excluded_rows": ["ps"]}}
    bull = ["Gross margin 63% is a durable advantage.", "ROE 17.9% beats its peers."]
    await np_.synthesize_core_thesis(report, BUFFETT, _ThesisGemini(bull), "EVID")
    assert report["core_thesis"]["bull_case"] == ["ROE 17.9% beats its peers."]


@pytest.mark.asyncio
async def test_a_company_with_a_moat_keeps_the_sentence():
    moat = {"dimensions": [_pillar("Brand Power", 7.4, 5.0)]}
    report = _thesis_report(moat)
    bull = ["Brand Power 7.4 (peer 5.0) suggests a durable competitive advantage.", "ROE 30%."]
    await np_.synthesize_core_thesis(report, BUFFETT, _ThesisGemini(bull), "EVID")
    assert report["core_thesis"]["bull_case"] == bull


# ── 5. the interest gap in the revenue breakdown ─────────────────────────────

def _sources(*values):
    return [RevenueSourceSchema(name=f"Seg {i}", value=v) for i, v in enumerate(values)]


JPM_SEGMENTS = [
    ("Commercial And Investment Bank", 78.5e9), ("Consumer & Community Banking", 76.0e9),
    ("Asset and Wealth Management Segment", 24.056e9),
]


def _jpm_sources():
    return [RevenueSourceSchema(name=n, value=v) for n, v in JPM_SEGMENTS]


def test_jpmorgans_gap_is_the_interest_it_pays():
    out, eliminations, outcome = rb._reconcile_segments(
        _jpm_sources(), 279_745e6, ticker="JPM", interest_expense=97_898e6,
        interest_income=193_341e6,
    )
    assert outcome == "interest"
    assert eliminations is None
    assert out[-1].name == "Interest Paid and Other"
    assert sum(s.value for s in out) == pytest.approx(279_745e6), "the waterfall still closes"


@pytest.mark.parametrize("seg,revenue,expense,income,outcome", [
    # Earns less interest than it pays: a REIT's rents and a utility, whose gap merely matches.
    ((800.0,), 1000.0, 200.0, 0.0, "unallocated"),
    ((8.6,), 10.0, 1.4, 0.1, "unallocated"),
    ((60.0,), 100.0, 40.0, 40.0, "unallocated"),
    ((60.0,), 100.0, 40.0, None, "unallocated"),    # FMP zero-fills real lenders: safe either way
    ((60.0,), 100.0, 40.0, float("nan"), "unallocated"),
    ((60.0,), 100.0, 40.0, float("inf"), "unallocated"),
])
def test_only_a_lender_has_an_interest_gap(seg, revenue, expense, income, outcome):
    scaled = None if income is None else income * 1e9
    got = rb._reconcile_segments(
        _sources(*[v * 1e9 for v in seg]), revenue * 1e9,
        interest_expense=expense * 1e9, interest_income=scaled,
    )[2]
    assert got == outcome


@pytest.mark.parametrize("seg,revenue,interest,outcome", [
    ((116.8,), 191.6, 78.6, "interest"),   # BAC: segments on a taxable-equivalent basis, gap below
    ((58.4,), 125.0, 66.0, "interest"),    # below 50% of gross, yet the whole of net: not thin
    ((60.0,), 100.0, 44.0, "interest"),    # gap 10% from the interest, inside
    ((60.0,), 100.0, 36.5, "interest"),    # gap 9.6% above it, inside
    ((60.0,), 100.0, 36.0, "unallocated"), # gap 11% above it: something else is in there
    ((60.0,), 100.0, 45.0, "unallocated"),
    ((60.0,), 100.0, 0.0, "unallocated"),  # FMP zero-fills real lenders
    ((60.0,), 100.0, None, "unallocated"),
    ((60.0,), 100.0, float("nan"), "unallocated"),
    ((60.0,), 100.0, float("inf"), "unallocated"),
    ((60.0,), 100.0, -40.0, "unallocated"),
    ((91.0,), 100.0, 9.0, "unallocated"),   # the gap is the interest, but too little to be a lender
    ((7.0,), 100.0, None, "thin"),          # Ford Credit
    ((7.0,), 100.0, 4.0, "thin"),
    ((99.0,), 100.0, 30.0, "exact"),        # the stack already reaches revenue
    ((140.0,), 100.0, 30.0, "gross"),
])
def test_the_gap_must_be_the_interest(seg, revenue, interest, outcome):
    scaled = None if interest is None else interest * 1e9
    got = rb._reconcile_segments(
        _sources(*[v * 1e9 for v in seg]), revenue * 1e9, interest_expense=scaled,
        interest_income=500e9,
    )[2]
    assert got == outcome


@pytest.mark.parametrize("interest,name", [
    (97_898e6, "Interest Paid and Other"),
    (0.0, "Unallocated"),
])
def test_the_service_reads_the_interest_from_the_paired_year(interest, name):
    service = rb.RevenueBreakdownService.__new__(rb.RevenueBreakdownService)
    service.fmp = reconciliation._FMP(
        [{"fiscalYear": 2025, "date": "2025-12-31", "data": dict(JPM_SEGMENTS)}],
        [{"fiscalYear": 2025, "date": "2025-12-31", "revenue": 279_745e6, "netIncome": 57e9,
          "interestExpense": interest, "interestIncome": 193_341e6}],
    )
    report, _ = asyncio.run(service._build_revenue_breakdown("JPM"))
    assert report.revenue_sources[-1].name == name
    assert sum(s.value for s in report.revenue_sources) == pytest.approx(279_745e6)


def test_with_no_interest_figure_nothing_changes():
    out, _, outcome = rb._reconcile_segments(_sources(60e9), 100e9)
    assert outcome == "unallocated"
    assert out[-1].name == "Unallocated"


def _engine_inputs():
    out, _, _ = rb._reconcile_segments(
        _jpm_sources(), 279_745e6, interest_expense=97_898e6, interest_income=193_341e6,
    )
    return C._engine_inputs_from_sources(
        [(s.name, s.value) for s in out], "2025", 279_745e6, None, [], "JPM",
    )


def test_the_engine_drops_the_row_and_shares_the_segments():
    inputs = _engine_inputs()
    rows = {r["name"]: r for r in inputs["segments"]}
    assert "Interest Paid and Other" not in rows
    assert inputs["total_revenue"] == pytest.approx(178_556e6)
    assert inputs["gross_reported_revenue"] == pytest.approx(279_745e6)
    assert inputs["intersegment_eliminations"] is None
    cib = rows["Commercial And Investment Bank"]
    assert cib["current_revenue"] / cib["total_revenue"] == pytest.approx(0.4396, abs=1e-3)
    shares = sum(r["current_revenue"] / r["total_revenue"] for r in rows.values())
    assert shares == pytest.approx(1.0)


def test_an_operating_companys_engine_is_unchanged():
    inputs = C._engine_inputs_from_sources(
        [("Cloud", 60e9), ("Unallocated", 40e9)], "2025", 100e9, None, [], "X",
    )
    assert inputs["total_revenue"] == 100e9
    assert inputs["gross_reported_revenue"] is None
    assert [r["name"] for r in inputs["segments"]] == ["Cloud", "Unallocated"]


def test_a_stack_of_nothing_but_the_row_is_no_breakdown():
    inputs = C._engine_inputs_from_sources(
        [("Interest Paid and Other", 40e9)], "2025", 100e9, None, [], "X",
    )
    assert inputs == C._empty_engine_inputs()


def test_the_raw_path_finds_the_interest_too():
    income = [_income_row(2025, 279_745e6, 97_898e6, interest_income=193_341e6)]
    raw = [{"fiscalYear": 2025, "date": "2025-12-31", "data": dict(JPM_SEGMENTS)}]
    inputs = C._raw_engine_inputs(raw, income, "JPM")
    assert inputs["gross_reported_revenue"] == pytest.approx(279_745e6)
    assert "Interest Paid and Other" not in {r["name"] for r in inputs["segments"]}


def _engine():
    inputs = _engine_inputs()
    return C._build_revenue_engine(
        inputs["segments"], fiscal_year="2025", total_revenue=inputs["total_revenue"],
        gross_reported_revenue=inputs["gross_reported_revenue"], reporting_currency="USD",
    )


def test_the_engine_validates_and_carries_the_gross_figure():
    engine = _engine()
    assert engine["gross_reported_revenue"] == pytest.approx(279_745.0)
    assert engine["total_revenue"] == pytest.approx(178_556.0)
    assert RevenueEngineResponse.model_validate(engine).gross_reported_revenue == pytest.approx(279_745.0)


def test_an_older_report_still_validates():
    engine = _engine()
    del engine["gross_reported_revenue"]
    assert RevenueEngineResponse.model_validate(engine).gross_reported_revenue is None


def test_the_collector_puts_the_gross_figure_on_the_report():
    out = parity._make_collected_data(ticker="JPM")
    out.profile.update(industry=BANK, sector="Financial Services")
    out.income = JPM_INCOME
    out.estimates = JPM_ESTIMATES
    sources = [RevenueSourceSchema(name=n, value=v) for n, v in JPM_SEGMENTS]
    sources.append(RevenueSourceSchema(name="Interest Paid and Other", value=101_189e6))
    out.revenue_breakdown = RevenueBreakdownResponse(
        symbol="JPM", fiscal_year="2025", revenue_sources=sources, reported_revenue=279_745e6,
        cost_of_sales=0.0, operating_expense=0.0, tax=0.0,
    )
    coll = C.TickerReportDataCollector()
    coll._build_sections(out)
    engine = coll.assemble_report(out, parity.stage_a_fallback())["revenue_engine"]
    assert engine["gross_reported_revenue"] == pytest.approx(279_745.0)
    assert engine["total_revenue"] == pytest.approx(178_556.0)
    assert "Interest Paid and Other" not in {s["name"] for s in engine["segments"]}


def test_the_empty_engine_carries_the_key():
    assert C._build_revenue_engine([])["gross_reported_revenue"] is None


def test_the_engine_says_what_its_total_leaves_out():
    assert np_._revenue_engine_basis_note(_engine()) == (
        "The $178.6B total is segment revenue, after interest paid out. "
        "Reported revenue of $279.7B includes that interest."
    )


def test_no_other_engine_gets_a_note():
    assert np_._revenue_engine_basis_note({"total_revenue": 100.0}) == ""
    assert np_._revenue_engine_basis_note({}) == ""


@pytest.mark.parametrize("model_text,expected_start", [
    ("Consumer leads.", "Consumer leads. The $178.6B total is"),
    (None, "The $178.6B total is"),                   # the field opted out: the total still needs it
])
def test_the_note_survives_an_opt_out(model_text, expected_start):
    shell = {"revenue_engine": _engine()}
    job = _jobs(shell)["revenue_engine_analysis_note"]
    job.apply(model_text)
    assert shell["revenue_engine"]["analysis_note"].startswith(expected_start)
    assert shell["revenue_engine"]["analysis_note"].endswith("includes that interest.")


def test_the_note_survives_a_failed_call():
    shell = {"revenue_engine": _engine()}
    job = _jobs(shell)["revenue_engine_analysis_note"]
    job.apply(job.fallback_value)
    assert shell["revenue_engine"]["analysis_note"].endswith("includes that interest.")


def test_another_companys_opt_out_stays_empty():
    inputs = C._engine_inputs_from_sources([("Cloud", 100e9)], "2025", 100e9, None, [], "X")
    engine = C._build_revenue_engine(inputs["segments"], total_revenue=100e9)
    shell = {"revenue_engine": engine}
    job = _jobs(shell)["revenue_engine_analysis_note"]
    job.apply(None)
    assert shell["revenue_engine"]["analysis_note"] is None


def test_the_evidence_names_both_figures():
    out, _ = _bank_report()
    out.revenue_engine_partial = _engine()
    text = C.build_financial_context(out)
    assert "Segment revenue, after interest paid out: $178.6B" in text
    assert "Reported revenue, including that interest: $279.7B" in text
    assert "Total revenue:" not in text


def test_every_other_companys_evidence_keeps_its_line():
    out, _ = _bank_report("Software - Infrastructure")
    inputs = C._engine_inputs_from_sources([("Cloud", 100e9)], "2025", 100e9, None, [], "X")
    out.revenue_engine_partial = C._build_revenue_engine(inputs["segments"], total_revenue=100e9)
    assert "Total revenue: " in C.build_financial_context(out)


def _chat_block(sources, reported=279_745e6):
    resp = SimpleNamespace(
        fiscal_year="2025", reported_revenue=reported, intersegment_eliminations=None, degraded=[],
        revenue_sources=[SimpleNamespace(name=n, value=v) for n, v in sources],
    )
    return chat_tool._segments_block(resp, None)


def test_chat_never_lists_the_interest_as_a_segment():
    block = _chat_block([*JPM_SEGMENTS, ("Interest Paid and Other", 101_189e6)])
    assert not any("Interest Paid" in row for row in block["segments"])
    assert block["segments"][0].endswith("(44.0% of segment revenue)")
    assert block["basis"] == chat_tool._LENDER_SEGMENTS_BASIS
    assert "of interest paid and other items, which no segment earns" in block["interest_paid"]


def test_another_companys_chat_block_is_unchanged():
    block = _chat_block([("Cloud", 60e9), ("Unallocated", 40e9)], reported=100e9)
    assert block["segments"] == [
        f"Cloud: {chat_tool._money(60e9)} (60.0% of reported revenue)",
        f"Unallocated: {chat_tool._money(40e9)} (40.0% of reported revenue)",
    ]
    assert block["basis"] == chat_tool._SEGMENTS_BASIS
    assert "interest_paid" not in block


def test_chat_is_told_too():
    line = chat._segment_figures({"revenue_engine": _engine()})[0][0]
    assert "% = share of segment revenue 178,556, after interest paid out" in line
    assert "reported revenue of 279,745 includes that interest" in line
    assert "total revenue" not in line
    assert "gross_reported_revenue" in chat._LEAD_OWNED_SECTION_KEYS["revenue_engine"]


# ── the smaller items from the same report ───────────────────────────────────

def _peer(ticker, roic, margin=0.3):
    return {"symbol": ticker, "companyName": ticker, "mktCap": 300e9, "sector": "Financial Services"}, {
        "returnOnCapitalEmployed": roic, "operatingProfitMargin": margin, "netProfitMargin": 0.2,
        "returnOnEquity": 0.11,
    }


def _competitors(**kw):
    profiles, ratios = zip(*[_peer("BAC", 0.294), _peer("WFC", 0.05)])
    return C._build_competitors(
        "JPM", {"sector": "Financial Services", "mktCap": 900e9}, [{"operatingProfitMargin": 0.28}],
        3.3, list(profiles), {p["symbol"]: r for p, r in zip(profiles, ratios)},
        peer_ranks={"BAC": 1, "WFC": 2}, n_total_peers=2, focal_roic_ttm=0.033, **kw,
    )


def test_a_banks_roic_never_scores_a_rival():
    bank = {row["ticker"]: row for row in _competitors(roic_comparable=False)}
    other = {row["ticker"]: row for row in _competitors()}
    assert other["BAC"]["competitive_score"] == 10.0, "the 26-point ROIC gap pins it on the old path"
    assert bank["BAC"]["competitive_score"] < 10.0
    assert all(row.get("score_basis") != "relative" for row in bank.values())


def test_the_collector_passes_it_for_a_bank():
    def scores(industry):
        out = parity._make_collected_data(ticker="JPM")
        out.profile["industry"] = industry
        out.profile["sector"] = "Financial Services"
        parity._with_peers(out)
        for row in out.peer_ratios.values():
            row["operatingProfitMargin"] = 0.3
        coll = C.TickerReportDataCollector()
        coll._build_sections(out)
        report = coll.assemble_report(out, parity.stage_a_fallback())
        return {row["score_basis"] for row in report["moat_competition"]["competitors"]}

    assert scores(BANK) == {"absolute"}
    assert scores("Software - Infrastructure") == {"relative"}


def _holders(buyers, sellers, bought, sold):
    summary = SimpleNamespace(
        num_buyers=buyers, num_sellers=sellers, total_buys_in_millions=bought,
        total_sells_in_millions=sold, period_description="Last 12 Months",
    )
    congress = SimpleNamespace(summary=summary, activities=[])
    return SimpleNamespace(recent_activities=SimpleNamespace(congress_activities=congress))


@pytest.mark.parametrize("buyers,sellers,bought,sold,expected", [
    (7, 7, 17.66, 0.5, "balanced"),      # JPMorgan: one $10M-$25M band, read as Net Buy
    (7, 7, 0.5, 17.66, "balanced"),
    (7, 2, 17.66, 0.5, "buy"),
    (2, 7, 0.5, 17.66, "sell"),
    (5, 2, 1.0, 1.0, "balanced"),
])
def test_the_net_pill_never_argues_with_the_counts_above_it(buyers, sellers, bought, sold, expected):
    signals = C._build_hidden_market_signals(_holders(buyers, sellers, bought, sold), None, None)
    assert signals["congress"]["net_direction"] == expected


@pytest.mark.parametrize("title", [
    "officer: CEO CCB",
    "officer: Co-CEO CIB",
    "officer: CEO Asset & Wealth Management",
    "officer: Co-President; CEO CIB",
    "officer: CEO Consumer & Community Banking",
    "officer: Chief Executive Officer Wealth Management",
])
def test_a_business_line_head_is_not_the_ceo(title):
    assert is_ceo_role(title) is False


@pytest.mark.parametrize("title", [
    "officer: Chairman & CEO",
    "officer: CEO",
    "director, officer: CEO and President",
    "officer: Chief Executive Officer",
    "officer: President & CEO ASRV & Bank",
    "officer: CEO Consumer Portfolio Services",
    "officer: CEO, Marriott International",
])
def test_the_companys_own_ceo_still_is(title):
    assert is_ceo_role(title) is True


def _session(prices, sigma=1.36, day="2026-11-05"):
    return {"prices": prices, "sigma_daily_pct": sigma, "_last_bar": day}


def test_a_large_last_session_is_named():
    line = np_._price_action_last_session_line(_session([337.98, 352.11, 337.34]))
    assert "LAST SESSION: down 4.2% on Nov 5, large for this stock." in line
    assert "only from a headline dated that day" in line


@pytest.mark.parametrize("pa", [
    _session([340.0, 341.0]),                     # an ordinary session
    _session([340.0]),
    _session([]),
    _session(None),
    _session([0.0, 5.0]),
    _session(["x", "y"]),
    _session([100.0, 110.0], sigma=None),
    _session([100.0, 110.0], sigma=0.0),
    {},
])
def test_an_ordinary_or_broken_series_says_nothing(pa):
    assert np_._price_action_last_session_line(pa) == ""


def test_the_price_prompt_carries_the_line():
    pa = {**_session([337.98, 352.11, 337.34]), "direction": "up", "change_pct": 1.8}
    prompt = np_._price_action_narrative_prompt(BUFFETT, "EVID", {"price_action": pa})
    assert "LAST SESSION: down 4.2%" in prompt
    quiet = {**_session([340.0, 341.0]), "direction": "up", "change_pct": 1.8}
    assert "LAST SESSION" not in np_._price_action_narrative_prompt(BUFFETT, "EVID", {"price_action": quiet})


@pytest.mark.parametrize("title,text", [
    ("Chipmaker ships a new probe card for advanced packaging", ""),
    ("Medical device maker wins clearance", "The ultrasound probe connects to a tablet."),
    ("Rocket maker signs a launch contract", "The space probe will reach Jupiter in 2031."),
    ("Analysts probe deeper into margins on the earnings call", ""),
    ("Wafer probe demand rises", "Test equipment makers said orders grew."),
])
def test_a_probe_that_is_not_an_inquiry_is_no_catalyst(title, text):
    """The classifier reads the article body too, so the bare word tagged these Legal/Regulatory."""
    assert C._classify_news_catalyst(title, text) is None


@pytest.mark.parametrize("title", [
    "Democrats plan a probe of JPMorgan's Epstein ties",
    "EU opens probe into cloud contracts",
    "Regulators widen probes into bank fees",
    "Senate probe targets the insurer",
    "Federal probe reaches the lender",
    "SEC probe into the accounting continues",
    "FTC probe of the merger deepens",
])
def test_an_inquiry_is_still_a_catalyst(title):
    assert C._classify_news_catalyst(title, "") == "Legal/Regulatory"


@pytest.mark.parametrize("build", [
    lambda ws: np_._institutional_flow_insight_prompt(BUFFETT, "EVID", ws),
    lambda ws: np_._covered_wall_street_insight_prompt(BUFFETT, "EVID", ws),
])
def test_old_13f_flow_is_never_tied_to_new_news(build):
    assert np_._INSTITUTIONS_TIMING_RULE in build({"current_price": 100.0})


def test_a_banks_macro_prompt_makes_no_margin_claim():
    shell = {"_scoring_inputs": {"_style_signals": {"excluded_rows": ["ps"]}}, "macro_data": {}}
    prompt = np_._macro_intelligence_brief_prompt(BUFFETT, "EVID", shell)
    assert "never say how rates move its margins" in prompt
    assert "debt/equity" not in prompt


def test_every_other_macro_prompt_is_unchanged():
    prompt = np_._macro_intelligence_brief_prompt(BUFFETT, "EVID", {"macro_data": {}})
    assert "(debt/equity, interest coverage, foreign-revenue %, capex intensity, margin, etc.)" in prompt
    assert "never say how rates" not in prompt


class _StatementFmp:
    async def get_balance_sheet(self, *a, **kw):
        return [{"date": "2025-12-31", "totalAssets": 10, "totalDebt": 5, "netDebt": 4}]

    async def get_income_statement(self, *a, **kw):
        return [{"date": "2025-12-31", "revenue": 10, "grossProfit": 6, "netIncome": 2}]

    async def get_cash_flow_statement(self, *a, **kw):
        return [{"date": "2025-12-31", "operatingCashFlow": 3, "freeCashFlow": 2, "capitalExpenditure": 1}]


@pytest.mark.parametrize("handler", ["fetch_extended_financials", "fetch_quarterly_financials"])
@pytest.mark.parametrize("statement", ["income", "balance_sheet", "cash_flow"])
@pytest.mark.asyncio
async def test_the_tools_never_hand_over_what_the_report_leaves_out(handler, statement):
    handlers = fmp_tools.build_tool_handlers(
        _StatementFmp(), subject_ticker="JPM", left_out_fields=fmp_tools.SCORE_EXCLUDED_FIELDS,
    )
    result = await handlers[handler]({"ticker": "JPM", "statement_type": statement})
    assert result["data"], "the rest of the statement still comes back"
    assert not fmp_tools.SCORE_EXCLUDED_FIELDS & set(result["data"][0])


@pytest.mark.asyncio
async def test_an_operating_company_keeps_every_field():
    handlers = fmp_tools.build_tool_handlers(_StatementFmp(), subject_ticker="AAPL")
    result = await handlers["fetch_extended_financials"]({"ticker": "AAPL", "statement_type": "balance_sheet"})
    assert result["data"][0]["totalDebt"] == 5


def _agent_kwargs(monkeypatch, industry):
    from app.services.agents import research_agent as ra

    seen = {}

    def spy(fmp, **kw):
        seen.update(kw)
        raise RuntimeError("stop here")

    agent = ra.ResearchAgent.__new__(ra.ResearchAgent)
    agent.gemini = SimpleNamespace(model_name="x", create_tool_chat=lambda **kw: None)
    agent.fmp = object()
    agent.persona = BUFFETT
    monkeypatch.setattr(ra, "build_fmp_tool_declarations", lambda: object())
    monkeypatch.setattr(ra, "build_tool_handlers", spy)
    out = SimpleNamespace(
        ticker="JPM", profile={"industry": industry}, non_lender_member=False,
        signal_of_confidence=None,
    )
    return agent, out, seen


@pytest.mark.parametrize("industry,expected", [
    (BANK, fmp_tools.SCORE_EXCLUDED_FIELDS),
    ("Software - Infrastructure", frozenset()),
])
@pytest.mark.asyncio
async def test_the_agent_passes_them_for_a_financial_company(monkeypatch, industry, expected):
    agent, out, seen = _agent_kwargs(monkeypatch, industry)
    with pytest.raises(RuntimeError):
        await agent._agentic_research(out, "EVID")
    assert seen["left_out_fields"] == expected


# ── the iOS half ─────────────────────────────────────────────────────────────

IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _swift(relative):
    return _strip_comments((IOS / relative).read_text(encoding="utf-8"))


def test_the_drill_down_never_calls_a_closed_period_current():
    """2026-11-13 (owner: "Time labels"): "Current" is the TTM figure only — the header and
    the legend's second line — and is spelled in ONE place; the first line is named by its
    period ("FY2025", "Q4 '26"). The old build called the newest closed period "Current"
    beside a TTM header (ORCL: "Current 41.78x" under "Current: 18.21")."""
    sheet = _swift("Views/Molecules/FundamentalsHistorySheet.swift")
    assert "periodPrefix" not in sheet
    prefix = _decl_body(sheet, "private var currentPrefix: String")
    # Each card's word, as one unit: Health readings are balance-sheet (no TTM), Growth rows
    # are the latest fiscal year (never "Current"), everything else is FMP's TTM multiple.
    assert re.search(r'case "Health":\s*return "Current"\s', prefix), prefix
    assert re.search(r'case "Growth":\s*return "Latest"\s', prefix), prefix
    assert re.search(r'default:\s*return "Current \(TTM\)"\s', prefix), prefix
    assert '"Current' not in sheet.replace(prefix, ""), "a second spelling of Current"
    header = _decl_body(sheet, "private func header(_ m: DeepDiveMetric)")
    assert 'Text("\\(prefix(for: m)): \\(currentValueText(m.value))")' in header
    # A row the backend names a closed period for (a Growth row's fiscal year, or a Price row
    # on a last-fiscal-year basis — owner: "Fallback TTM labels") never says "Current (TTM)".
    word = _decl_body(sheet, "private func prefix(for m: DeepDiveMetric) -> String")
    assert "guard let period = m.currentPeriod, !period.isEmpty else { return currentPrefix }" in word
    assert re.search(r'case "pfcf":\s*return "\\\(period\) FCF basis"', word), word
    assert "TTM" not in word
    legend = sheet[sheet.index("deltaText(prefix:"):]
    assert legend.startswith("deltaText(prefix: periodLabel(pair.period),")
    label = _decl_body(sheet, "private func periodLabel(_ label: String)")
    assert 'period == .annual ? "FY\\(label)" : label' in label
    # The removed helpers quoted a closed year's median after "Current: Negative".
    for gone in ("currentIndustry", "undefinedCurrentText", "currentIsUndefined"):
        assert gone not in sheet, gone


def test_the_current_line_is_the_card_rows_own_comparison():
    """Line 2 (owner decisions 2026-11-13: "2-current vs industry", "Match the card"): the
    row's carried value and median, coloured by the row's own score behind the unrated-card
    gate, drawn only when the report carries a median (never parsed from the label), and
    blind to the tab — no period, series, line level or same-period pair."""
    sheet = _swift("Views/Molecules/FundamentalsHistorySheet.swift")
    text = _decl_body(sheet, "private func currentLineText(_ m: DeepDiveMetric)")
    assert "guard let median = m.currentPeerMedian else { return nil }" in text
    assert "m.currentValue" in text and "prefix(for: m)" in text
    color = _decl_body(sheet, "private func currentLineColor(_ m: DeepDiveMetric)")
    # Each arm as ONE unit (a swapped gain/loss or a dropped muted guard must fail), and the
    # muted guard runs before the unrated-card gate.
    muted = re.search(r"guard m\.currentValue != nil else \{ return AppColors\.textMuted \}", color)
    gate = re.search(r"guard card\.starRating > 0, let score = m\.currentScore else "
                     r"\{ return AppColors\.textPrimary \}", color)
    assert muted and gate and muted.start() < gate.start(), color
    assert re.search(r"if score >= 4 \{ return AppColors\.gain \}", color), color
    assert re.search(r"if score <= 2 \{ return AppColors\.loss \}", color), color
    word = _decl_body(sheet, "private func currentPeerWord(_ m: DeepDiveMetric)")
    assert "(m.peerLevel ?? card.peerGroupLevel)" in word
    for body in (text, color, word):
        for tab_bound in (r"\bperiod\b", r"points\(", r"sectorPoints\(", r"peerWord\(",
                          r"sectorPair\(", r"sectorAnnual", r"sectorQuarterly", r"m\.label"):
            assert not re.search(tab_bound, body), (tab_bound, body)
    legend = _decl_body(sheet, "private func legendAndDelta(_ m: DeepDiveMetric)")
    assert "if usesLineChart, let current = currentLineText(m)" in legend
    assert ".foregroundColor(currentLineColor(m))" in legend
    # One row per comparison (owner 2026-11-13: "i don't want to make new line for the
    # second line"): both lines are single-line, shrinking no further than the 11pt floor.
    for color in ("deltaColor(m)", "currentLineColor(m)"):
        assert re.search(rf"\.foregroundColor\({re.escape(color)}\)\s*"
                         r"\.multilineTextAlignment\(\.center\)\s*\.lineLimit\(1\)\s*"
                         r"\.minimumScaleFactor\(Self\.legendLineMinScale\)", legend), color
    scale = re.search(r"static let legendLineMinScale: CGFloat = ([\d.]+)", sheet)
    assert scale and 11 / 14 <= float(scale.group(1)) < 1, "bodySmall is 14pt; the data floor is 11pt"
    note = _decl_body(sheet, "private func ttmNote(_ m: DeepDiveMetric)")
    assert 'card.title == "Valuation"' in note and "Text(ttmNoteText(m))" in note
    assert re.search(r"legendAndDelta\(m\)\s*ttmNote\(m\)", sheet), "the caption is never drawn"
    text = _decl_body(sheet, "private func ttmNoteText(_ m: DeepDiveMetric)")
    for key, words in (('"ps"', "sales"), ('"pfcf"', "free cash flow"),
                       ('"ev_ebitda"', "EBITDA"), ('"pb"', "book value")):
        assert re.search(rf'case {key}:\s*return "[^"]*{words}', text), key
    assert re.search(r'default:\s*return "TTM = the last 12 months of earnings', text)


def test_the_report_dto_carries_the_current_comparison_optionally():
    dto = _decl_body(_swift("Models/TickerReportResponse.swift"), "struct DeepDiveMetricDTO")
    for decl, key in (("let currentValue: Double?", 'case currentValue = "current_value"'),
                      ("let currentPeerMedian: Double?",
                       'case currentPeerMedian = "current_peer_median"'),
                      ("let currentScore: Int?", 'case currentScore = "current_score"'),
                      ("let peerLevel: String?", 'case peerLevel = "peer_level"'),
                      ("let currentPeriod: String?", 'case currentPeriod = "current_period"')):
        assert decl in dto and key in dto, decl
    response = _swift("Models/TickerReportResponse.swift")
    mapper = response[response.index("return DeepDiveMetric("):]
    mapper = mapper[:mapper.index(")\n")]
    for field in ("currentValue", "currentPeerMedian", "currentScore", "peerLevel", "currentPeriod"):
        assert f"{field}: m.{field}" in mapper, field
    model = _decl_body(_swift("Models/TickerReportModels.swift"), "struct DeepDiveMetric: Identifiable")
    for field, kind in (("currentValue", "Double"), ("currentPeerMedian", "Double"),
                        ("currentScore", "Int"), ("peerLevel", "String"),
                        ("currentPeriod", "String")):
        assert f"let {field}: {kind}?" in model and f"{field}: {kind}? = nil" in model, field
    # The wire names are the backend's.
    from app.schemas.ticker_report import DeepDiveMetricResponse
    for key in ("current_value", "current_peer_median", "current_score", "peer_level",
                "current_period"):
        assert key in DeepDiveMetricResponse.model_fields, key


def test_an_unrated_card_paints_no_verdict():
    sheet = _swift("Views/Molecules/FundamentalsHistorySheet.swift")
    good = _decl_body(sheet, "private func isCurrentlyGood")
    assert "card.starRating > 0" in good
    chart = _decl_body(sheet, "private func chartView")
    assert re.search(r"higherIsBetter:\s*card\.starRating > 0\s*\?", chart)


def test_the_financials_tab_greys_the_interest_row():
    repo = _swift("Core/Repositories/StockRepository.swift")
    dto = _decl_body(repo, "struct RevenueBreakdownDTO")
    assert 'source.name == "Interest Paid and Other"' in dto
    assert rb._INTEREST_GAP_NAME == "Interest Paid and Other", "the name is the wire contract"


def test_the_info_sheet_defines_the_row():
    sheet = _swift("Views/Molecules/RevenueBreakdownInfoSheet.swift")
    assert "Interest Paid and Other" in sheet


def test_the_report_header_names_a_lenders_total():
    dto = _decl_body(_swift("Models/TickerReportResponse.swift"), "struct RevenueEngineDTO")
    assert re.search(r"let grossReportedRevenue:\s*Double\?", dto), "Optional: older reports lack it"
    assert 'case grossReportedRevenue = "gross_reported_revenue"' in dto
    assert "grossReportedRevenue: revenueEngine.grossReportedRevenue" in _swift(
        "Models/TickerReportResponse.swift")
    model = _decl_body(_swift("Models/RevenueEngineModels.swift"), "struct ReportRevenueEngineData")
    assert 'grossReportedRevenue == nil ? "Total Revenue" : "Segment Revenue"' in model
    header = _decl_body(
        _swift("Views/Organisms/ReportRevenueEngineSection.swift"), "private var headerSection",
    )
    assert "Text(data.totalLabel)" in header and '"Total Revenue"' not in header
