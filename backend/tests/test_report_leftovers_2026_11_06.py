"""Six small things the JPMorgan audit confirmed (report 64c03303) that the first fix pass left.

1. The PDF printed "1.27x sector avg" for an INDUSTRY median.
2. A critical factor read FY2025's EPS growth as today's: "if EPS growth moderates from +1.4%".
3. EPS read $5.75 in the narrative and $5.57 on the Growth chart with no word on why.
4. Ally and Capital One lost their first forecast chip with no sentence saying why.
5. The bear case called P/B 1.47x "stretched" while the bull case cited ROE at 1.42x.
6. The forecast insight said growth "decelerates" into +4% off a +54% year, and said segments
   are "expected to be key contributors" with no segment estimate behind it.

Bank margins on gross revenue are the owner's call and are not touched here.
"""

from __future__ import annotations

import copy
import math
from types import SimpleNamespace

import pytest

import test_jpm_report_fixes_2026_11_06 as jpm
import test_pdf_report_render as pdf_render
from app.services import pdf_report_service as pdf
from app.services.agents import narrative_prompts as np_
from app.services.agents import ticker_report_data_collector as C
from test_ticker_report_schema_parity import _no_live_benchmark_lookup  # noqa: F401

BUFFETT = jpm.BUFFETT
JPM_CARDS = [
    {
        "title": "Profitability", "star_rating": 4,
        "metrics": [
            {"label": "Operating Margin (1.27x sector avg 22.2%)", "value": "28.25%",
             "peer_level": "industry", "trend": "up"},
            {"label": "Return on Equity (ROE) (1.42x sector avg 12.6%)", "value": "17.91%",
             "peer_level": "industry", "trend": "up"},
        ],
    },
    {
        "title": "Growth", "star_rating": 2,
        "metrics": [
            {"label": "Revenue Growth (YoY)", "value": "+3.3%",
             "annual_history": [{"period": "2024", "value": 14.6}, {"period": "2025", "value": 3.3}]},
            {"label": "EPS Growth", "value": "+1.4%",
             "annual_history": [{"period": "2024", "value": 21.7}, {"period": "2025", "value": 1.4}]},
            {"label": "Free Cash Flow Growth (YoY)", "value": "N/M"},
        ],
    },
    {
        "title": "Valuation", "star_rating": 3,
        "metrics": [
            {"label": "P/B (1.47x sector avg 1.66)", "value": "2.43", "peer_level": "industry"},
        ],
    },
    {
        "title": "Health", "star_rating": 0,
        "metrics": [
            {"label": "Debt-to-Equity (vs sector 2.77)", "value": "3.32",
             "peer_level": "industry"},
        ],
    },
]


def _report(cards=None, excluded=True):
    signals = {"excluded_rows": ["pfcf"]} if excluded else {}
    return {
        "fundamental_metrics": JPM_CARDS if cards is None else cards,
        "_scoring_inputs": {"_style_signals": signals},
    }


def _digest(cards=None, excluded=True):
    return "\n".join(np_._digest_fundamentals(_report(cards, excluded)))


# ── 1. The PDF names an industry median as the industry's ────────────


def _labels(cards):
    return [m["label"] for card in cards for m in card["metrics"]]


def test_the_pdf_says_industry_for_an_industry_median():
    labels = _labels(pdf._peer_worded_cards(JPM_CARDS))
    assert "Operating Margin (1.27x industry avg 22.2%)" in labels
    assert "Debt-to-Equity (vs industry 2.77)" in labels
    assert "P/B (1.47x industry avg 1.66)" in labels


def test_a_sector_median_stays_a_sector_median():
    cards = [{"title": "T", "metrics": [
        {"label": "P/E (1.10x sector avg 20.1)", "value": "22", "peer_level": "sector"},
        {"label": "P/S (1.20x sector avg 3.0)", "value": "3.6"},    # frozen before peer_level
    ]}]
    assert _labels(pdf._peer_worded_cards(cards)) == [
        "P/E (1.10x sector avg 20.1)", "P/S (1.20x sector avg 3.0)",
    ]


def test_the_stored_report_is_not_touched():
    before = copy.deepcopy(JPM_CARDS)
    worded = pdf._peer_worded_cards(JPM_CARDS)
    assert JPM_CARDS == before
    assert worded[0] is not JPM_CARDS[0] and worded[0]["metrics"][0] is not JPM_CARDS[0]["metrics"][0]


def test_everything_but_the_label_survives():
    metric = pdf._peer_worded_cards(JPM_CARDS)[0]["metrics"][0]
    assert (metric["value"], metric["trend"], metric["peer_level"]) == ("28.25%", "up", "industry")
    assert pdf._peer_worded_cards(JPM_CARDS)[0]["star_rating"] == 4


@pytest.mark.parametrize("cards,expected", [
    (None, []), ([], []), ({}, []), ("cards", []), (["junk", None], []),
    ([{"title": "T"}], [{"title": "T", "metrics": []}]),
    ([{"title": "T", "metrics": None}], [{"title": "T", "metrics": []}]),
    ([{"title": "T", "metrics": ["junk"]}], [{"title": "T", "metrics": ["junk"]}]),
])
def test_malformed_cards_never_break_the_pdf(cards, expected):
    assert pdf._peer_worded_cards(cards) == expected


def test_a_metric_with_no_label_prints_nothing():
    cards = [{"title": "T", "metrics": [{"label": None, "value": "1"}]}]
    assert pdf._peer_worded_cards(cards)[0]["metrics"][0]["label"] == ""


def test_the_rendered_pdf_says_industry():
    report = pdf_render._sample()
    report["fundamental_metrics"] = copy.deepcopy(JPM_CARDS)
    html = pdf.render_html(pdf.build_context(report, fair_value_estimate=196.0))
    assert "1.27x industry avg 22.2%" in html and "vs industry 2.77" in html
    assert "sector avg" not in html and "vs sector" not in html


# ── 2. A Growth row says which year it is ────────────────────────────


def test_a_growth_row_names_its_fiscal_year():
    digest = _digest()
    assert "EPS Growth FY2025 +1.4%" in digest
    assert "Revenue Growth (YoY) FY2025 +3.3%" in digest


def test_the_critical_factors_digest_carries_the_year():
    assert "EPS Growth FY2025 +1.4%" in np_.build_module_digest(_report())


def test_only_the_growth_card_is_tagged():
    """The other cards are TTM even when a row's rate matches an annual point."""
    row = {"value": "+1.4%", "annual_history": [{"period": "2025", "value": 1.4}]}
    assert np_._growth_year_tag("Growth", row) == " FY2025"
    for title in ("Profitability", "Valuation", "Health", None):
        assert np_._growth_year_tag(title, row) == ""
    digest = _digest()
    assert "Operating Margin (1.27x industry avg 22.2%) 28.25%" in digest
    assert "P/B (1.47x industry avg 1.66) 2.43" in digest
    assert digest.count(" FY20") == 2


def test_a_row_with_no_figure_stays_plain():
    """A year on a blank would claim a measurement that does not exist."""
    history = [{"period": "2024", "value": -100.0}, {"period": "2025", "value": None}]
    for value in ("—", "N/M", "", None):
        assert np_._growth_year_tag("Growth", {"value": value, "annual_history": history}) == ""


def test_a_blank_new_year_never_borrows_last_years_label():
    """EPS 2.0, 0.0, 3.0: FY2024 was measured at -100%, the blank is FY2025."""
    cards = [{"title": "Growth", "metrics": [{
        "label": "EPS Growth", "value": "—",
        "annual_history": [{"period": "2024", "value": -100.0}, {"period": "2025", "value": None}],
    }]}]
    assert "EPS Growth —" in _digest(cards, excluded=False)
    assert "FY2024" not in _digest(cards, excluded=False)


@pytest.mark.parametrize("value,history,expected", [
    ("+1.4%", [{"period": "2024", "value": 21.7}, {"period": "2025", "value": 1.4}], "2025"),
    ("+1.4%", [{"period": "2025", "value": 1.4}, {"period": "2024", "value": 21.7}], "2025"),
    ("-3.3%", [{"period": "2025", "value": -3.3}], "2025"),
    ("+0.0%", [{"period": "2025", "value": 0.0}], "2025"),                  # 0.0 is a measurement
    ("1,234.5%", [{"period": "2025", "value": 1234.5}], "2025"),
    (" +1.4% ", [{"period": "2025", "value": 1.4}], "2025"),
    ("+1.4%", [{"period": 2025, "value": 1.4}], "2025"),
    ("+1.4%", [{"period": " 2025 ", "value": 1.4}], "2025"),
    # Rounded apart: the same rate.
    ("+1.4%", [{"period": "2025", "value": 1.5}], "2025"),
    # GrowthService dropped a null newest year, so the row really prints the year before.
    ("+25.0%", [{"period": "2024", "value": 25.0}, {"period": "2025", "value": None}], "2024"),
    # A day-old snapshot beside fresh income: last year's rate keeps last year's label.
    ("+1.5%", [{"period": "2024", "value": 1.5}, {"period": "2025", "value": 50.0}], "2024"),
    # The same rate twice: the newest year.
    ("+2.0%", [{"period": "2024", "value": 2.0}, {"period": "2025", "value": 2.0}], "2025"),
    # No point holds the printed rate: no year is better than a wrong one.
    ("+25.0%", [{"period": "2024", "value": 20.0}, {"period": "2025", "value": 30.0}], None),
    ("+1.4%", [{"period": "2025", "value": 1.6}], None),
    ("+1.4%", [{"period": "2025", "value": "1.4"}], None),
    ("+1.0%", [{"period": "2025", "value": True}], None),
    ("+1.4%", [{"period": "Q3 '26", "value": 1.4}], None),
    ("+1.4%", [{"period": "FY25", "value": 1.4}], None),
    ("+1.4%", [{"period": "20250", "value": 1.4}], None),
    ("+1.4%", [{"period": None, "value": 1.4}], None),
    ("+1.4%", ["junk", None], None),
    ("+1.4%", [], None), ("+1.4%", None, None),
    ("1.4", [{"period": "2025", "value": 1.4}], None),                       # not a printed rate
    (1.4, [{"period": "2025", "value": 1.4}], None),
])
def test_which_year_a_growth_row_reports(value, history, expected):
    assert np_._growth_row_year({"value": value, "annual_history": history}) == expected


def test_the_evidence_says_the_growth_card_is_annual():
    def snap(rating, name):
        return SimpleNamespace(rating=rating, metrics=[
            SimpleNamespace(name=name, value="+1.4%", peer_level=None, metric_key=None),
        ])

    out = SimpleNamespace(
        ticker="JPM", non_lender_member=False, profile={"industry": "Software"},
        snap_profitability=snap(4, "Gross Margin"), snap_growth=snap(2, "EPS Growth"),
        snap_valuation=None, snap_health=snap(0, "Debt-to-Equity"),
    )
    text = C._format_snapshot_card_values(out)
    assert "\nGrowth (2/5, latest full fiscal year):" in text
    assert "\nProfitability (4/5):" in text
    assert "\nFinancial Health (unrated):" in text
    out.snap_growth = snap(0, "EPS Growth")
    assert "\nGrowth (unrated, latest full fiscal year):" in C._format_snapshot_card_values(out)


# ── 3. The figure estimates are measured against says so ─────────────


JPM_EARNINGS = {
    "date": "2026-10-13", "position": "inside_window", "reported": True, "eps_actual": 5.75,
    "eps_estimate": 5.93, "eps_result": "miss", "eps_surprise_pct": -3.0,
}


def _chart(*values):
    return {"growth_chart": {"eps_quarterly": [{"period": "Q", "value": v} for v in values]}}


def test_two_eps_figures_get_a_label():
    line = np_._price_action_earnings_line({"_earnings": JPM_EARNINGS}, _chart(7.70, 5.57))
    assert "EPS 5.75 vs 5.93 estimate (missed by 3%)." in line
    assert 'if you give 5.75, follow it with the words "on the basis estimates use"' in line
    assert "5.57" not in line       # the model is never handed the second figure to print


def test_the_label_never_guesses_the_basis():
    """Saved data can't say which basis the feed uses."""
    line = np_._price_action_earnings_line({"_earnings": JPM_EARNINGS}, _chart(5.57)).lower()
    assert "adjusted" not in line and "non-gaap" not in line


@pytest.mark.parametrize("shell", [
    _chart(7.70, 5.75),                         # the same figure twice
    _chart(5.7549),                             # the same figure to the cent
    _chart(5.57, None), _chart("5.57"), _chart(True), _chart(float("nan")), _chart(float("inf")),
    _chart(), {"growth_chart": {}}, {"growth_chart": None}, {"growth_chart": {"eps_quarterly": "x"}},
    {"growth_chart": {"eps_quarterly": ["junk"]}}, {}, None,
])
def test_one_figure_needs_no_label(shell):
    line = np_._price_action_earnings_line({"_earnings": JPM_EARNINGS}, shell)
    assert line.count("5.75") == 1
    assert "basis estimates use" not in line


@pytest.mark.parametrize("actual", [None, "5.75", True, float("nan")])
def test_a_feed_figure_that_is_not_a_number_needs_no_label(actual):
    assert np_._statement_eps_differs(_chart(5.57), actual) is False


def test_a_pending_report_gets_no_label():
    pending = {**JPM_EARNINGS, "reported": False, "eps_actual": None, "eps_result": None}
    line = np_._price_action_earnings_line({"_earnings": pending}, _chart(5.57))
    assert "basis estimates use" not in line


def test_the_price_prompt_reads_the_chart():
    shell = {**_chart(7.70, 5.57), "price_action": {
        "_earnings": JPM_EARNINGS, "direction": "up", "change_pct": 1.8,
    }}
    prompt = np_._price_action_narrative_prompt(BUFFETT, "EVID", shell)
    assert "on the basis estimates use" in prompt


# ── 4. A lender whose estimates don't show their basis gets a sentence ──


def _lender(revenue, interest, estimate, **kw):
    return jpm._company(
        income=[jpm._income_row(2025, revenue, interest)],
        estimates=jpm._next_year(estimate), **kw,
    )


@pytest.mark.parametrize("ticker,revenue,interest,estimate", [
    ("ALLY", 12.154e9, 6.408e9, 9.095e9), ("COF", 69.252e9, 15.818e9, 64.009e9),
    ("SOFI", 4.770e9, 1.156e9, 5.021e9), ("CACC", 2.317e9, 0.463e9, 2.135e9),
    ("SCHW", 27.675e9, 3.754e9, 28.124e9), ("AXP", 80.464e9, 8.234e9, 79.438e9),
    ("AGNC", 1.912e9, 2.848e9, 4.460e9),
])
def test_lenders_whose_estimates_do_not_show_their_basis(ticker, revenue, interest, estimate):
    """FMP FY2025 revenue, interest paid and next-year consensus, read 2026-11-05."""
    company = _lender(revenue, interest, estimate, ticker=ticker)
    assert C._revenue_basis_unclear(company) is True
    assert C._revenue_gross_of_interest(company) is False


@pytest.mark.parametrize("ticker,revenue,interest,estimate", [
    ("JPM", 279.745e9, 97.898e9, 208.351e9), ("BAC", 191.567e9, 78.470e9, 122.574e9),
    ("SYF", 19.116e9, 4.135e9, 15.134e9),
])
def test_a_lender_whose_estimates_are_net_is_marked_only_as_gross(ticker, revenue, interest, estimate):
    company = _lender(revenue, interest, estimate, ticker=ticker)
    assert C._revenue_gross_of_interest(company) is True
    assert C._revenue_basis_unclear(company) is False


@pytest.mark.parametrize("company", [
    # Insurers: the interest is a sliver of revenue, so nothing is unclear about it.
    _lender(87.637e9, 0.278e9, 87.911e9, industry=jpm.INSURER, ticker="PGR"),
    _lender(77.084e9, 1.061e9, 79.395e9, industry="Insurance - Life", ticker="MET"),
    # An operating company with debt keeps its chip and gets no sentence.
    _lender(100e9, 40e9, 90e9, industry="Software - Infrastructure", ticker="MSFT"),
    # Nothing to read.
    _lender(100e9, 0.0, 90e9), _lender(100e9, 9.9e9, 90e9), _lender(0.0, 40e9, 90e9),
    jpm._company(income=[], estimates=jpm._next_year(90e9)),
    jpm._company(estimates=[{"date": "2020-12-31"}]),
    jpm._company(income=[jpm._income_row(2025, 100.0, 40.0)], estimates=jpm._next_year(0.0)),
])
def test_who_gets_no_sentence(company):
    assert C._revenue_basis_unclear(company) is False


def test_no_estimate_is_no_read():
    assert C._lender_revenue_read(jpm._company(estimates=[{"date": "2020-12-31"}])) is None
    assert C._lender_revenue_read(jpm._company(estimates=[])) is None


def test_exactly_midway_proves_nothing_either_way():
    assert C._revenue_basis_unclear(_lender(100.0, 40.0, 80.0)) is True


def _ally_report():
    return jpm._bank_report(
        "Financial - Credit Services",
        income=[jpm._income_row(2025, 12.154e9, 6.408e9), jpm._income_row(2024, 11.9e9, 6.5e9)],
        estimates=jpm._next_year(9.095e9),
    )


def test_the_report_marks_such_a_lender():
    out, report = _ally_report()
    signals = report["_scoring_inputs"]["_style_signals"]
    assert signals["revenue_basis_unclear"] is True
    assert "revenue_gross_of_interest" not in signals


def test_a_bank_carries_only_its_own_mark():
    _, report = jpm._bank_report()
    signals = report["_scoring_inputs"]["_style_signals"]
    assert signals["revenue_gross_of_interest"] is True
    assert "revenue_basis_unclear" not in signals


def test_an_operating_company_gets_neither_mark():
    _, report = jpm._bank_report("Software - Infrastructure")
    signals = report["_scoring_inputs"]["_style_signals"]
    assert not {"revenue_gross_of_interest", "revenue_basis_unclear"} & set(signals)


def test_the_evidence_says_the_two_may_differ():
    out, _ = _ally_report()
    text = C.build_financial_context(out)
    caveat = "These estimates may be on a different revenue basis"
    assert text.index("Analyst Estimates:") < text.index(caveat)
    assert "usually leave it out" not in text


def _unclear_shell(timeline=None):
    shell = jpm._forecast_shell(flag=False, timeline=timeline)
    shell["_scoring_inputs"]["_style_signals"]["revenue_basis_unclear"] = True
    return shell


def test_the_sentence_says_only_what_is_known():
    note = np_._revenue_forecast_basis_note(_unclear_shell())
    assert note == (
        "Analyst estimates may be on a different revenue basis from reported revenue, so the "
        "first forecast bar can't be compared with the last reported one."
    )
    assert "interest" not in note.lower()
    for word in ("decline", "fall", "drop"):
        assert word not in note.lower()


def test_the_sentence_needs_both_kinds_of_bar():
    assert np_._revenue_forecast_basis_note(_unclear_shell([{"is_forecast": False}])) == ""
    assert np_._revenue_forecast_basis_note(_unclear_shell([{"is_forecast": True}])) == ""


def test_a_mark_only_counts_when_it_is_exactly_true():
    shell = jpm._forecast_shell(flag=False)
    shell["_scoring_inputs"]["_style_signals"]["revenue_basis_unclear"] = "yes"
    assert np_._revenue_forecast_basis_note(shell) == ""


def test_the_gross_sentence_has_not_moved():
    assert np_._revenue_forecast_basis_note(jpm._forecast_shell()) == (
        "Reported revenue includes interest paid out; analyst estimates usually leave it out, so "
        "the first forecast bar can't be compared with the last reported one."
    )


def test_the_sentence_rides_on_model_text_and_on_the_fallback():
    job = jpm._jobs(_unclear_shell())["revenue_forecast_insight"]
    note = np_._revenue_forecast_basis_note(_unclear_shell())
    assert job.postprocess("Model text.") == f"Model text. {note}"
    assert job.fallback_value.endswith(note)


def test_the_prompt_stops_setting_reported_years_beside_forecasts():
    prompt = np_._revenue_forecast_insight_prompt(BUFFETT, "EVID", _unclear_shell())
    assert "+54%" not in prompt and "HISTORICAL TREND" not in prompt
    assert "recent ACTUAL trend" not in prompt and "historical trend" not in prompt
    assert "never set them beside a forecast" in prompt
    assert "Analyst estimates may be on a different revenue basis from reported revenue, so " \
           "never compare a forecast year with a reported year." in prompt
    assert "interest paid out" not in prompt


def test_the_gross_prompt_has_not_moved():
    prompt = np_._revenue_forecast_insight_prompt(BUFFETT, "EVID", jpm._forecast_shell())
    assert "Reported revenue includes interest paid out and analyst estimates usually leave it " \
           "out, so never compare a forecast year with a reported year." in prompt


# ── 5. One ROE premium is not two arguments ──────────────────────────


def test_a_pb_premium_that_is_the_roe_premium_says_so():
    digest = _digest()
    assert "P/B is 1.47x peers and ROE is 1.42x peers" in digest
    assert "never call the valuation stretched or overvalued on P/B" in digest


def test_only_a_covered_financial_gets_the_rule():
    assert "P/B is" not in _digest(excluded=False)


def _multiples(pb, roe):
    return [{"title": "x", "metrics": [
        {"label": f"P/B ({pb}x sector avg 1.66)", "value": "2"},
        {"label": f"Return on Equity (ROE) ({roe}x sector avg 12.6%)", "value": "18%"},
    ]}]


@pytest.mark.parametrize("pb,roe,expected", [
    ("1.47", "1.42", True),
    ("1.10", "1.42", True),     # a smaller premium than the return earns
    ("1.32", "1.20", True),     # exactly at the line
    ("1.33", "1.20", False),    # past it: a price premium of its own
    ("1.10", "1.00", False),    # an average return is not a higher one
    ("1.05", "0.96", False),    # a premium on a lower return is the case for "stretched"
    ("2.00", "1.42", False),
    ("1.00", "1.42", False),    # no premium to explain
    ("0.80", "1.42", False),
])
def test_when_the_premium_is_the_roe_premium(pb, roe, expected):
    assert (np_._pb_premium_line(_multiples(pb, roe)) is not None) is expected


def _bank_shell(cards):
    return {
        "fundamental_metrics": cards, "overall_assessment": {},
        "_scoring_inputs": {"_style_signals": {"excluded_rows": ["pfcf"]}},
    }


def test_the_insight_hears_the_rule_too():
    prompt = np_._overall_assessment_text_prompt(BUFFETT, "EVID", _bank_shell(JPM_CARDS))
    assert "never call the valuation stretched" in prompt


def test_an_insight_with_no_matching_premium_hears_nothing():
    plain = _bank_shell(_multiples("2.00", "1.42"))
    assert "stretched" not in np_._overall_assessment_text_prompt(BUFFETT, "EVID", plain)
    operating = {**_bank_shell(JPM_CARDS), "_scoring_inputs": {"_style_signals": {}}}
    assert "stretched" not in np_._overall_assessment_text_prompt(BUFFETT, "EVID", operating)


def test_the_multiple_reads_either_wording():
    cards = [{"metrics": [
        {"label": "P/B (1.47x industry avg 1.66)"},
        {"label": "Return on Equity (ROE) (1.42x industry avg 12.6%)"},
    ]}]
    assert np_._pb_premium_line(cards) is not None


@pytest.mark.parametrize("cards", [
    None, [], "cards", ["junk"], [{"metrics": None}], [{"metrics": ["junk"]}],
    [{"metrics": [{"label": "P/B (1.47x sector avg 1.66)"}]}],                 # no ROE row
    [{"metrics": [{"label": "Return on Equity (ROE) (1.42x sector avg 12.6%)"}]}],
    [{"metrics": [{"label": "P/B"}, {"label": "Return on Equity (ROE)"}]}],    # no peer median
    [{"metrics": [{"label": None}]}],
])
def test_no_pair_of_multiples_no_rule(cards):
    assert np_._pb_premium_line(cards) is None


def test_a_rule_never_reaches_a_left_out_row():
    """Return on Assets holds no "(ROE)": it must never stand in for it."""
    cards = [{"metrics": [
        {"label": "P/B (1.47x sector avg 1.66)"},
        {"label": "Return on Assets (ROA) (1.83x sector avg 0.7%)"},
    ]}]
    assert np_._pb_premium_line(cards) is None


# ── 6. The forecast read says which years, and segments are past results ──


TIMELINE = [
    {"period": "2023", "is_forecast": False, "revenue_yoy_pct": 53.6},
    {"period": "2024", "is_forecast": False, "revenue_yoy_pct": 14.6},
    {"period": "2025", "is_forecast": False, "revenue_yoy_pct": 3.3},
    {"period": "2026", "is_forecast": True, "revenue_yoy_pct": 4.2},
    {"period": "2027", "is_forecast": True, "revenue_yoy_pct": 5.1},
]


def test_the_read_is_the_latest_actual_against_the_first_forecast():
    rule = np_._latest_vs_forward_rule({"annual_timeline": TIMELINE})
    assert "latest actual year (2025: +3%)" in rule
    assert "first forecast year (2026: +4%)" in rule
    assert "not from older years" in rule


def test_a_fall_reads_with_its_sign():
    rows = [{"period": "2025", "is_forecast": False, "revenue_yoy_pct": -8.0},
            {"period": "2026", "is_forecast": True, "revenue_yoy_pct": -2.4}]
    rule = np_._latest_vs_forward_rule({"annual_timeline": rows})
    assert "(2025: -8%)" in rule and "(2026: -2%)" in rule


@pytest.mark.parametrize("change", [None, "4.2", True, float("nan"), math.inf])
def test_no_first_forecast_change_no_rule(change):
    rows = [{"period": "2025", "is_forecast": False, "revenue_yoy_pct": 3.3},
            {"period": "2026", "is_forecast": True, "revenue_yoy_pct": change}]
    assert np_._latest_vs_forward_rule({"annual_timeline": rows}) == ""


@pytest.mark.parametrize("change", [None, "3.3", True, float("nan"), math.inf])
def test_no_latest_actual_change_no_rule(change):
    """A company with one reported year: a crash here would fail the whole report."""
    rows = [{"period": "2025", "is_forecast": False, "revenue_yoy_pct": change},
            {"period": "2026", "is_forecast": True, "revenue_yoy_pct": 4.2}]
    assert np_._latest_vs_forward_rule({"annual_timeline": rows}) == ""


@pytest.mark.parametrize("rows", [
    None, [], ["junk"],
    [{"is_forecast": False, "revenue_yoy_pct": 3.3}],
    [{"is_forecast": True, "revenue_yoy_pct": 3.3}],
])
def test_a_timeline_missing_a_side_gets_no_rule(rows):
    assert np_._latest_vs_forward_rule({"annual_timeline": rows}) == ""


def test_the_prompt_carries_the_rule():
    shell = jpm._forecast_shell(flag=False, timeline=TIMELINE)
    prompt = np_._revenue_forecast_insight_prompt(BUFFETT, "EVID", shell)
    assert "Judge that from the latest actual year (2025: +3%)" in prompt


def test_a_lender_prompt_never_compares_across_the_bases():
    shell = jpm._forecast_shell(timeline=TIMELINE)
    assert "Judge that from" not in np_._revenue_forecast_insight_prompt(BUFFETT, "EVID", shell)


def test_no_prompt_says_a_segment_will_carry_the_forecast():
    prompt = np_._revenue_forecast_insight_prompt(
        BUFFETT, "EVID", jpm._forecast_shell(flag=False),
    )
    assert "carries the forward curve" not in prompt
    assert "tie the forward curve" not in prompt
    assert "there is no segment estimate" in prompt
    assert "never say a segment is expected to lead, contribute to or carry the forecast" in prompt
