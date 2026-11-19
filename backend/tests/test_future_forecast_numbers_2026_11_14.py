"""The numbers AROUND the report's Future Forecast chart (2026-11-14).

The chart's timing was measured correct on 14 live tickers (each fiscal-year column holds
its own window of prices). What a PLUG report got wrong was the text and labels around it:

* the insight's "22% annually" was a consensus CAGR over FY2026E-FY2029E — the legacy 4-bar
  chart's window, drawn nowhere once the chart became the timeline (5 reported + 5 forecast
  years). Owner: measure it over the drawn bars, last REPORTED year → last forecast year
  ("Last reported → last forecast"), and "Keep scores unchanged";
* the prompt's "FORWARD PROJECTIONS (as charted)" were not what is charted (+17% vs +16%);
* PLUG FY2024's chip read -30% for a -29.45% change (rounded to 1 dp, then to 0 dp);
* PLUG FY2021 read +639% against a NEGATIVE prior revenue;
* BABA's yuan figures were labelled "$";
* FMP's blank `epsAvg = 0` drew a real $0.00 EPS;
* NVDA's $26.9B bar was stored as 0.03T (11% too tall);
* the frozen price series could end on the in-progress session's partial bar.
Hermetic: every input is built here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.agents import ticker_report_data_collector as C
from app.services.agents import narrative_prompts as np_
from app.services.agents.persona_config import get_persona_config
from app.services import chat_context_resolver as chat
from app.services.pdf_report_service import build_context, render_html

PERSONA = get_persona_config("warren_buffett")


# ── Fixtures: a PLUG-shaped December filer ───────────────────────────────────

def _income(rows, currency="USD"):
    """[(fiscal year, revenue, GAAP EPS)] → FMP annual income rows (newest first)."""
    return [
        {"date": f"{fy}-12-31", "fiscalYear": str(fy), "period": "FY",
         "revenue": rev, "epsDiluted": eps, "reportedCurrency": currency, "symbol": "PLUG"}
        for fy, rev, eps in reversed(rows)
    ]


def _estimates(rows):
    """[(fiscal year, revenueAvg, epsAvg)] → FMP annual analyst-estimate rows."""
    return [
        {"date": f"{fy}-12-31", "revenueAvg": rev, "epsAvg": eps,
         "numAnalystsRevenue": 9, "numAnalystsEps": 8, "symbol": "PLUG"}
        for fy, rev, eps in rows
    ]


PLUG_INCOME = _income([
    (2020, -93.2e6, -1.68), (2021, 502.3e6, -0.82), (2022, 701.4e6, -1.25),
    (2023, 891.3e6, -2.30), (2024, 628.8e6, -2.68), (2025, 710.0e6, -1.01),
])
PLUG_ESTIMATES = _estimates([
    (2026, 821.0e6, -0.45), (2027, 976.0e6, -0.30), (2028, 1150.0e6, -0.15),
    (2029, 1392.0e6, -0.05), (2030, 1800.0e6, 0.02),
])


def _cagr(start, end, years):
    return round(((end / start) ** (1 / years) - 1) * 100, 1)


# ── 1. the rate the text cites is measured over the drawn bars ───────────────

def test_plug_revenue_rate_spans_last_reported_to_last_forecast():
    growth = C._timeline_growth(PLUG_INCOME, PLUG_ESTIMATES)
    assert growth["revenue_span"] == "FY2025–FY2030E"
    assert growth["revenue_cagr"] == pytest.approx(_cagr(710.0e6, 1800.0e6, 5))
    # The old scored window (FY2026E-FY2029E) is a different number: the bug.
    assert growth["revenue_cagr"] != pytest.approx(_cagr(821.0e6, 1392.0e6, 3), abs=0.05)


def test_loss_making_eps_has_no_growth_rate():
    growth = C._timeline_growth(PLUG_INCOME, PLUG_ESTIMATES)
    assert growth["eps_cagr"] is None and growth["eps_span"] is None


def test_eps_rate_is_consensus_to_consensus_only():
    income = _income([(2024, 10e9, 2.0), (2025, 11e9, 2.2)])
    estimates = _estimates([(2026, 12e9, 3.0), (2027, 13e9, 3.3), (2028, 14e9, 3.63)])
    growth = C._timeline_growth(income, estimates)
    assert growth["eps_span"] == "FY2026E–FY2028E"   # never from the GAAP 2.2
    assert growth["eps_cagr"] == pytest.approx(10.0)
    assert growth["revenue_span"] == "FY2025–FY2028E"


def test_a_bank_measures_revenue_between_consensus_years_only():
    growth = C._timeline_growth(PLUG_INCOME, PLUG_ESTIMATES, revenue_crosses_basis=True)
    assert growth["revenue_span"] == "FY2026E–FY2030E"
    assert growth["revenue_cagr"] == pytest.approx(_cagr(821.0e6, 1800.0e6, 4))


def test_one_forecast_year_is_a_one_year_rate_and_no_eps_rate():
    income = _income([(2024, 10e9, 2.0), (2025, 11e9, 2.2)])
    growth = C._timeline_growth(income, _estimates([(2026, 12.1e9, 3.0)]))
    assert growth["revenue_span"] == "FY2025–FY2026E"
    assert growth["revenue_cagr"] == pytest.approx(10.0)
    assert growth["eps_cagr"] is None   # first == last forecast year


@pytest.mark.parametrize("income, estimates", [
    (PLUG_INCOME, []),                                      # no forecast column
    ([], []),
    (_income([(2025, -5e6, -1.0)]), _estimates([(2026, 10e6, -0.5)])),   # base <= 0
    (_income([(2025, 10e6, 1.0)]), _estimates([(2026, 0, 1.0)])),        # blank end (0 → None)
])
def test_unmeasurable_revenue_rates_are_none(income, estimates):
    growth = C._timeline_growth(income, estimates)
    assert growth["revenue_cagr"] is None and growth["revenue_span"] is None


def test_a_gap_in_the_years_has_no_single_horizon():
    estimates = _estimates([(2026, 821e6, 0.1), (2027, 976e6, 0.2), (2029, 1392e6, 0.3)])
    growth = C._timeline_growth(PLUG_INCOME, estimates)
    assert growth["revenue_cagr"] is None and growth["eps_cagr"] is None


def test_span_cagr_exponent_is_the_fiscal_year_difference():
    R = C._TimelineRow
    rows = [R(2025, 100.0, 1.0, False, None, None, "2025-12-31"),
            R(2026, 110.0, 1.1, True, None, None, "2026-12-31"),
            R(2027, 121.0, 1.21, True, None, None, "2027-12-31")]
    assert C._span_cagr(rows, 0, 2, "revenue") == (10.0, "FY2025–FY2027E")
    gapped = rows[:2] + [R(2028, 133.1, 1.331, True, None, None, "2028-12-31")]
    assert C._span_cagr(gapped, 0, 2, "revenue") == (None, None)
    assert C._span_cagr(rows, 2, 2, "revenue") == (None, None)
    assert C._span_cagr(rows, 0, 9, "revenue") == (None, None)


# ── 2. the wire carries the drawn rate + its span; scores keep theirs ────────

def test_the_wire_rate_is_the_timelines_not_the_scored_one():
    partial = C._build_revenue_forecast_partial(PLUG_ESTIMATES, 99.0, 88.0, PLUG_INCOME)
    assert partial["cagr"] == pytest.approx(_cagr(710.0e6, 1800.0e6, 5))
    assert partial["cagr_span"] == "FY2025–FY2030E"
    assert partial["eps_growth"] == 0.0 and partial["eps_growth_span"] is None


def test_without_a_forecast_column_the_scored_rate_stays_unlabelled():
    partial = C._build_revenue_forecast_partial([], 12.5, 7.0, PLUG_INCOME)
    assert (partial["cagr"], partial["cagr_span"]) == (12.5, None)
    assert (partial["eps_growth"], partial["eps_growth_span"]) == (7.0, None)


def test_the_scored_rates_are_untouched():
    """`c["revenue_cagr"]` (forecast vital, persona inputs) still comes from the 4-estimate
    window; only the wire's printed rate moved (owner: "Keep scores unchanged")."""
    src = C.__file__ and open(C.__file__, encoding="utf-8").read()
    block = src[src.index("# ── Analyst forecast CAGRs"):src.index("# ── Historical price chart data")]
    assert '_select_visible_forecast_window(' in block
    assert 'c["revenue_cagr"] = _safe_cagr(est0_rev, estn_rev, n)' in block


def test_the_collector_passes_the_reporting_currency():
    src = open(C.__file__, encoding="utf-8").read()
    call = src[src.index("out.revenue_forecast_partial = _build_revenue_forecast_partial("):]
    call = call[:call.index("\n        )\n")]
    assert "currency=_engine_reporting_currency(out.income, None, out.ticker)" in call


# ── 3. chips: rounding, negative bases, blanks, bar heights ──────────────────

def _row(timeline, period):
    return next(r for r in timeline if r["period"] == period)


def test_plug_fy2024_chip_prints_minus_29_not_minus_30():
    timeline = C._build_annual_timeline(PLUG_INCOME, PLUG_ESTIMATES)
    yoy = _row(timeline, "2024")["revenue_yoy_pct"]
    assert yoy == pytest.approx(-29.451, abs=0.001)   # (628.8 - 891.3) / 891.3
    assert f"{yoy:.0f}" == "-29"           # iOS "%.0f"; it read "-30" at 1 dp


def test_a_3_49_percent_change_prints_3():
    income = _income([(2021, 100e6, 1.0), (2022, 103.491e6, 1.0)])
    timeline = C._build_annual_timeline(income, [])
    assert f"{_row(timeline, '2022')['revenue_yoy_pct']:.0f}" == "3"


def test_revenue_change_against_a_negative_base_is_blank():
    income = _income([(2019, 230e6, -2.0), (2020, -93.2e6, -2.0), (2021, 502.3e6, -1.0),
                      (2022, 701.4e6, -1.25), (2023, 891.3e6, -2.3), (2024, 628.8e6, -2.68),
                      (2025, 710.0e6, -1.01)])
    timeline = C._build_annual_timeline(income, [])
    assert _row(timeline, "2021")["revenue_yoy_pct"] is None      # was +639%
    # EPS keeps the abs(prior) rule: a narrowing loss reads positive.
    assert _row(timeline, "2021")["eps_yoy_pct"] == pytest.approx(50.0)


def test_negative_current_revenue_has_no_change_either():
    income = _income([(2024, 100e6, 1.0), (2025, -20e6, 1.0)])
    assert _row(C._build_annual_timeline(income, []), "2025")["revenue_yoy_pct"] is None


def test_a_blank_consensus_is_na_not_zero():
    estimates = _estimates([(2026, 821e6, -0.45), (2027, 976e6, 0.0), (2028, 1150e6, -0.15)])
    timeline = C._build_annual_timeline(PLUG_INCOME, estimates)
    blank = _row(timeline, "2027")
    assert blank["eps_label"] == "N/A" and blank["eps_yoy_pct"] is None
    assert _row(timeline, "2028")["eps_yoy_pct"] is None
    assert blank["revenue_label"] == "$976M"


def test_small_bars_keep_their_height_beside_a_trillion():
    income = _income([(2022, 26.9e9, 1.0), (2023, 27.0e9, 1.0), (2024, 1.21e12, 1.0)])
    timeline = C._build_annual_timeline(income, [])
    assert _row(timeline, "2022")["revenue"] == pytest.approx(0.0269, rel=1e-3)


# ── 4. currency ──────────────────────────────────────────────────────────────

def test_non_usd_labels_carry_the_code():
    income = _income([(2024, 941e9, 6.5), (2025, 996e9, -2.0)], currency="CNY")
    estimates = _estimates([(2026, 1.05e12, 44.0)])
    timeline = C._build_annual_timeline(income, estimates, currency="CNY")
    assert _row(timeline, "2026")["revenue_label"] == "CNY 1.1T"
    assert _row(timeline, "2026")["eps_label"] == "CNY 44.00"
    assert _row(timeline, "2025")["eps_label"] == "-CNY 2.00"
    partial = C._build_revenue_forecast_partial(estimates, None, None, income, currency="CNY")
    assert all(p["revenue_label"].startswith("CNY ") for p in partial["projections"])


@pytest.mark.parametrize("currency", ["USD", None, "garbage"])
def test_usd_and_unknown_keep_the_dollar(currency):
    timeline = C._build_annual_timeline(PLUG_INCOME, PLUG_ESTIMATES, currency=currency)
    assert _row(timeline, "2025")["revenue_label"] == "$710M"
    assert _row(timeline, "2025")["eps_label"] == "-$1.01"


# ── 5. the frozen price series stops at the last SETTLED close ───────────────

def _hist(days):
    return [{"date": d, "close": 1.0 + i} for i, d in enumerate(days)]


@pytest.mark.parametrize("now_et_hour, last", [(11, "2026-11-12"), (18.5, "2026-11-13")])
def test_the_in_progress_session_is_not_frozen(now_et_hour, last):
    from app.utils.market_hours import ET
    timeline = C._build_annual_timeline(PLUG_INCOME, PLUG_ESTIMATES)
    hist = _hist(["2026-11-10", "2026-11-12", "2026-11-13"])       # Fri 11-13 = "today"
    hours, minutes = int(now_et_hour), int(round((now_et_hour % 1) * 60))
    now = datetime(2026, 11, 13, hours, minutes, tzinfo=ET).astimezone(timezone.utc)
    prices = C._build_timeline_prices(hist, timeline, now=now)
    assert prices[-1]["date"] == last


# ── 6. what the model is told ────────────────────────────────────────────────

def _shell():
    partial = C._build_revenue_forecast_partial(PLUG_ESTIMATES, 22.0, 0.0, PLUG_INCOME)
    partial["earnings_track_record"] = [{"surprise_percent": -80.1}]
    partial["beat_summary"] = "Beat 1 of 10"
    return {"revenue_forecast": partial}


def test_the_prompt_names_the_span_and_lists_the_drawn_bars():
    prompt = np_._revenue_forecast_insight_prompt(PERSONA, "EVIDENCE", _shell())
    rate = _cagr(710.0e6, 1800.0e6, 5)
    assert f"PROJECTED REVENUE CAGR: {rate:+.1f}% a year over FY2025–FY2030E" in prompt
    assert "PROJECTED EPS CAGR: n/a" in prompt
    line = next(l for l in prompt.splitlines() if l.startswith("FORWARD PROJECTIONS (as charted)"))
    assert line.count("rev ") == 5                      # all five drawn forecast bars
    assert "2026: rev $821M (+16% YoY), EPS -$0.45;" in line   # the chip, not +17%
    assert "2026: rev $821M (+16% YoY), EPS -$0.45 (" not in line   # no GAAP→consensus EPS change
    assert "22%" not in prompt                          # the scored figure never leaks in
    assert "acceleration or a slowdown against a single year's YoY" in prompt
    assert "-30%" not in prompt and "-29%" in prompt    # the HISTORICAL TREND line


def test_without_a_timeline_the_fallback_is_not_called_as_charted():
    shell = {"revenue_forecast": {
        "cagr": 12.0, "eps_growth": 0.0, "management_guidance": "unknown",
        "projections": [{"period": "2026", "revenue_label": "$5.1B", "revenue_yoy_pct": 61.0,
                         "eps_label": "$6.20", "eps_yoy_pct": None}],
        "annual_timeline": [], "earnings_track_record": [],
    }}
    prompt = np_._revenue_forecast_insight_prompt(PERSONA, "EVIDENCE", shell)
    assert "(as charted)" not in prompt and "FORWARD PROJECTIONS: 2026: rev $5.1B" in prompt
    assert "PROJECTED REVENUE CAGR: +12.0% a year " in prompt     # no span → no "over"
    assert "+12.0% a year over" not in prompt


@pytest.mark.parametrize("value, text", [(-0.47, "0%"), (0.4, "0%"), (-29.45, "-29%"),
                                         (16.8, "+17%"), (-0.5, "0%"), (-0.51, "-1%")])
def test_signed_pct_never_prints_minus_zero(value, text):
    assert np_._signed_pct(value) == text


# ── 7. PDF and chat print the same rate, with its span ───────────────────────

def test_the_pdf_strip_shows_the_span():
    partial = _shell()["revenue_forecast"]
    html = render_html(build_context({"revenue_forecast": partial}, None))
    assert "Revenue CAGR (FY2025–FY2030E)" in html
    assert "EPS CAGR" in html and "EPS Growth" not in html
    assert "EPS CAGR (" not in html           # no rate → no span


def test_chat_states_the_stored_rate_with_its_span():
    partial = _shell()["revenue_forecast"]
    figures = chat._forecast_figures({"revenue_forecast": partial})
    head, items, _cap = figures[0]
    rate = _cagr(710.0e6, 1800.0e6, 5)
    assert f"revenue CAGR FY2025–FY2030E {rate:.1f}%/yr" in items   # chat's unsigned style
    assert not any("FY2026" in i and "CAGR" in i for i in items)


def test_chat_keeps_the_old_window_label_for_reports_without_a_span():
    partial = _shell()["revenue_forecast"]
    partial.pop("cagr_span")
    partial["cagr"] = 22.0
    items = chat._forecast_figures({"revenue_forecast": partial})[0][1]
    assert any(i.startswith("revenue CAGR ") and "22" in i for i in items)


def test_the_evidence_block_prints_the_shown_rate_with_its_span(monkeypatch):
    monkeypatch.setattr(C.settings, "DCF_ENABLED", False)
    out = C.CollectedTickerData(ticker="PLUG", persona_key="buffett")
    out.profile = {"price": 1.37, "companyName": "Plug Power"}
    out.quote = {"price": 1.37}
    out.computed = {"revenue_cagr": 22.0, "eps_cagr": None}
    out.revenue_forecast_partial = _shell()["revenue_forecast"]
    text = C.build_financial_context(out)
    assert "Revenue CAGR (analyst est.), FY2025–FY2030E: " in text
    assert "22.0%" not in text


# ── 8. review round (2026-11-14) ─────────────────────────────────────────────

BABA_INCOME = _income([(2024, 941e9, 6.5), (2025, 996e9, 5.0)], currency="CNY")
BABA_ESTIMATES = _estimates([(2026, 1.05e12, 40.0), (2027, 1.15e12, 44.0),
                             (2028, 1.25e12, 48.0), (2029, 1.35e12, 52.0),
                             (2030, 1.45e12, 0.0)])          # FMP's blank last-year epsAvg


def _evidence(partial, monkeypatch, *, estimates=None, income=None, scored=(22.0, 14.5)):
    monkeypatch.setattr(C.settings, "DCF_ENABLED", False)
    out = C.CollectedTickerData(ticker="XYZ", persona_key="buffett")
    out.profile = {"price": 1.0, "companyName": "X"}
    out.quote = {"price": 1.0}
    out.computed = {"revenue_cagr": scored[0], "eps_cagr": scored[1]}
    out.estimates = estimates or []
    out.income = income or []
    if partial is not None:
        out.revenue_forecast_partial = partial
    return C.build_financial_context(out)


def test_an_unmeasurable_chart_rate_is_na_in_the_evidence_too(monkeypatch):
    """The scored 4-estimate EPS rate (14.5%) leaked into EVIDENCE, unlabelled, while the
    insight header said n/a and the PDF a dash."""
    partial = C._build_revenue_forecast_partial(BABA_ESTIMATES, 22.0, 14.5, BABA_INCOME,
                                                currency="CNY")
    assert partial["eps_growth"] == 0.0 and partial["eps_growth_span"] is None
    text = _evidence(partial, monkeypatch)
    assert "EPS CAGR (analyst est.): N/A" in text
    assert "14.5%" not in text


def test_an_older_collection_without_span_keys_keeps_the_scored_rate(monkeypatch):
    text = _evidence({"cagr": 22.0, "eps_growth": 14.5}, monkeypatch)
    assert "Revenue CAGR (analyst est.): 22.0%" in text


def test_the_evidence_estimates_carry_the_reporting_currency(monkeypatch):
    text = _evidence(None, monkeypatch, estimates=BABA_ESTIMATES[:2], income=BABA_INCOME)
    block = text[text.index("Analyst Estimates:"):]
    lines = block.splitlines()[1:3]
    assert all("CNY " in line and "$" not in line for line in lines), lines


def _flat_partial():
    income = _income([(2024, 47.0e9, 2.0), (2025, 47.0e9, 2.0)])
    estimates = _estimates([(2026, 47.01e9, 2.1), (2027, 47.02e9, 2.2), (2028, 47.03e9, 2.3)])
    return C._build_revenue_forecast_partial(estimates, 3.0, 5.0, income)


def test_a_measured_flat_rate_is_stated_everywhere(monkeypatch):
    partial = _flat_partial()
    assert partial["cagr"] == 0.0 and partial["cagr_span"] == "FY2025–FY2028E"
    prompt = np_._revenue_forecast_insight_prompt(
        PERSONA, "E", {"revenue_forecast": {**partial, "earnings_track_record": []}})
    assert "PROJECTED REVENUE CAGR: +0.0% a year over FY2025–FY2028E" in prompt
    html = render_html(build_context({"revenue_forecast": partial}, None))
    assert "Revenue CAGR (FY2025–FY2028E)" in html and "+0.0%" in html
    items = chat._forecast_figures({"revenue_forecast": partial})[0][1]
    assert "revenue CAGR FY2025–FY2028E 0.0%/yr" in items
    assert "Revenue CAGR (analyst est.), FY2025–FY2028E: 0.0%" in _evidence(partial, monkeypatch)


def test_older_reports_still_read_zero_as_no_rate():
    shell = {"revenue_forecast": {"cagr": 0.0, "eps_growth": 0.0, "management_guidance": "x",
                                  "projections": [], "annual_timeline": [],
                                  "earnings_track_record": []}}
    prompt = np_._revenue_forecast_insight_prompt(PERSONA, "E", shell)
    assert "PROJECTED REVENUE CAGR: n/a" in prompt
    html = render_html(build_context(shell, None))
    assert "Revenue CAGR</div>" in html


def test_the_digest_names_each_span_and_forbids_comparing_them():
    digest = np_._digest_forecast({"revenue_forecast": {
        "cagr": 12.8, "cagr_span": "FY2025–FY2030E",
        "eps_growth": 5.0, "eps_growth_span": "FY2026E–FY2030E"}})
    assert digest == ["FUTURE FORECAST: revenue CAGR +12.8% over FY2025–FY2030E, EPS CAGR "
                      "+5.0% over FY2026E–FY2030E, the two CAGRs cover different years: never "
                      "compare them"]
    same = np_._digest_forecast({"revenue_forecast": {
        "cagr": 5.0, "cagr_span": "FY2026E–FY2030E",
        "eps_growth": 5.0, "eps_growth_span": "FY2026E–FY2030E"}})
    assert "never compare" not in same[0]
    assert np_._digest_forecast({"revenue_forecast": {"cagr": 0.0, "eps_growth": 0.0}}) == []


def test_the_insight_rule_forbids_setting_revenue_against_eps_rates():
    prompt = np_._revenue_forecast_insight_prompt(PERSONA, "EVIDENCE", _shell())
    assert "never set one against the other" in prompt
