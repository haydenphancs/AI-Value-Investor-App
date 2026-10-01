"""Round 2 (G2a) — report-side numbers that read as facts but were not measured.

  * R18 — a dropped-digit EPS (feed 0.169 for 1.69) answered 200 with no `degraded` reason
          and became a permanent "-89.8% miss" in the EPS Track Record.
  * R19 — the forecast `projections` (the Stage B insight's source) were labelled
          `date[:4]` while the Earnings Timeline labels by fiscal year: HD's insight cited
          a year one ahead of the bar it sits under.
  * R21/R24 — an unmeasured share-count change (fewer than two reported counts) reached
          the prompts as "roughly flat" / "+0.0%" and the PDF as "Share Δ +0.0%".
  * R25 — a newest quarter with no cash-flow row froze "Buybacks: $0" into the report.
  * R44 — "Other"'s prior year summed a renamed member as 0 (a fabricated +49% YoY).
  * R46 — the estimate-0 exclusion from the Track Record is pinned as a choice.
  * R33 — the PDF growth legend said "Sector avg" for an industry-median line.

Each assertion fails on the first-pass code. Pure functions and stubs only.
"""

from __future__ import annotations

import pytest

from app.schemas.earnings import EarningsQuarterSchema, EarningsResponse
from app.schemas.signal_of_confidence import (
    SignalOfConfidenceDataPointSchema,
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)
from app.schemas.ticker_report import CapitalAllocationResponse
from app.services.agents import narrative_prompts as np_
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    _attach_earnings_track_record,
    _build_capital_allocation_block,
    _build_revenue_forecast_partial,
    _eps_digit_shift_suspect,
    _screen_eps_digit_glitches,
    _segment_rows,
)


# ═══════════════════════════════════════════════════════════════════════════
# R18 — dropped-digit EPS
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "actual, estimate, gaap, suspect",
    [
        (0.169, 1.66, 0.90, True),    # the dropped digit; the filing disagrees with it
        (0.169, 1.66, None, True),    # no filing to ask: the signature alone
        (16.9, 1.66, None, True),     # an ADDED digit
        (0.169, 1.66, 0.17, False),   # the 10-Q says 0.17 too: a real -90% miss
        (0.05, -0.10, None, False),   # sign flip — a turnaround, not a digit
        (0.15, 0.02, None, False),    # |estimate| < 0.05: penny-scale noise
        (1.46, 1.66, None, False),    # a genuine -12% miss
        (0.40, 1.66, None, False),    # -76%: outside the normal band but not a power of 10
        (-0.169, -1.66, None, True),  # a loss quarter's dropped digit
        (None, 1.66, None, False),
        (0.169, 0.0, None, False),
        # Round 3 (P15): the filing is a sign-aware tie-break, not a [0.67, 1.5] veto.
        (0.169, 1.69, 1.40, True),     # GAAP sits at the estimate's magnitude
        (0.03, 0.28, 0.25, True),      # a small feed actual's dropped digit
        (0.05, 0.50, -0.30, False),    # opposite-sign GAAP: a real near-break-even miss
        (0.01, 0.10, -0.04, False),
        (0.02, 0.20, -0.10, False),
        (0.01, 0.09, 0.03, False),     # an exact tie is not evidence
    ],
)
def test_digit_shift_signature(actual, estimate, gaap, suspect):
    assert _eps_digit_shift_suspect(actual, estimate, gaap) is suspect


def _eq(label, actual, estimate, surprise, fiscal_date):
    return EarningsQuarterSchema(
        quarter=label, actual_value=actual, estimate_value=estimate,
        surprise_percent=surprise, fiscal_date=fiscal_date, has_estimate=True,
    )


def _earnings_out(gaap_q2):
    out = CollectedTickerData(ticker="AVGO", persona_key="warren_buffett")
    out.earnings = EarningsResponse(
        symbol="AVGO",
        eps_quarters=[
            _eq("Q1 '26", 1.62, 1.57, 3.18, "2026-02-01"),
            _eq("Q2 '26", 0.169, 1.66, -89.82, "2026-05-03"),
        ],
        revenue_quarters=[], price_history=[], degraded=[],
    )
    out.income_q = [  # transient attr, as in `_fetch_all`
        {"date": "2026-05-03", "epsDiluted": gaap_q2},
        {"date": "2026-02-01", "epsDiluted": 1.10},
    ]
    return out


def test_a_dropped_digit_quarter_never_reaches_the_track_record():
    out = _earnings_out(gaap_q2=0.90)
    _screen_eps_digit_glitches(out)
    q2 = next(q for q in out.earnings.eps_quarters if q.quarter == "Q2 '26")
    assert q2.surprise_percent is None and q2.actual_value == 0.169, (
        "the actual is kept as served (never 'corrected'); only its surprise is withheld"
    )
    rf: dict = {}
    _attach_earnings_track_record(rf, out.earnings)
    assert [r["period"] for r in rf["earnings_track_record"]] == ["Q1 '26"]
    assert rf["beat_summary"] == "Beat 1 of 1"


def test_a_miss_the_filing_confirms_is_kept():
    out = _earnings_out(gaap_q2=0.17)
    _screen_eps_digit_glitches(out)
    rf: dict = {}
    _attach_earnings_track_record(rf, out.earnings)
    q2 = next(r for r in rf["earnings_track_record"] if r["period"] == "Q2 '26")
    assert q2["result"] == "miss" and q2["surprise_percent"] == -89.8


def test_the_screen_never_raises_on_odd_input():
    out = CollectedTickerData(ticker="T", persona_key="warren_buffett")
    out.earnings = object()  # type: ignore[assignment]
    _screen_eps_digit_glitches(out)  # no eps_quarters → no-op
    out.earnings = None
    _screen_eps_digit_glitches(out)


def test_the_screen_runs_on_the_fresh_collection_after_the_gate():
    import inspect

    src = inspect.getsource(C.TickerReportDataCollector._fetch_all)
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    gate = code.index("_refuse_degraded_financials(out)")
    screen = code.index("_screen_eps_digit_glitches(out)")
    pass2 = code.index("await self._fetch_dependent(out)")
    assert gate < screen < pass2


# ═══════════════════════════════════════════════════════════════════════════
# R46 — estimate-0 quarters stay out (a pinned, documented choice)
# ═══════════════════════════════════════════════════════════════════════════


def test_an_estimate_zero_quarter_is_left_out_of_the_track_record():
    """`surprise_percent` is a non-Optional Double on shipped iOS — a null would fail the
    whole report decode — so a quarter the service cannot divide (consensus 0.00) is
    excluded, and the divergence from TickerDetail is recorded in the code."""
    earnings = EarningsResponse(
        symbol="T",
        eps_quarters=[
            EarningsQuarterSchema(quarter="Q1 '26", actual_value=0.05, estimate_value=0.0,
                                  surprise_percent=None, fiscal_date="2026-03-31",
                                  has_estimate=True),
            _eq("Q2 '26", 0.12, 0.10, 20.0, "2026-06-30"),
        ],
        revenue_quarters=[], price_history=[], degraded=[],
    )
    rf: dict = {}
    _attach_earnings_track_record(rf, earnings)
    assert [r["period"] for r in rf["earnings_track_record"]] == ["Q2 '26"]
    assert all(isinstance(r["surprise_percent"], float) for r in rf["earnings_track_record"])


# ═══════════════════════════════════════════════════════════════════════════
# R19 — projections labelled like the timeline
# ═══════════════════════════════════════════════════════════════════════════


def _hd_rows():
    """Home Depot shape: FY2025 ends 2026-02-01 and FMP names it fiscalYear 2025 (the
    START year); analyst estimates carry no fiscalYear."""
    income = [
        {"date": "2026-02-01", "fiscalYear": "2025", "revenue": 166.0e9, "epsDiluted": 15.1},
        {"date": "2025-02-02", "fiscalYear": "2024", "revenue": 159.5e9, "epsDiluted": 14.9},
        {"date": "2024-01-28", "fiscalYear": "2023", "revenue": 152.7e9, "epsDiluted": 15.1},
    ]
    estimates = [
        {"date": f"{y}-01-31", "revenueAvg": (170 + 5 * (y - 2027)) * 1e9, "epsAvg": 16.0 + y - 2027}
        for y in range(2027, 2032)
    ]
    return income, estimates


def _cdns_rows():
    """52/53-week filer closing on the Saturday nearest Dec 31: FY2026 ends 2027-01-02."""
    income = [
        {"date": "2026-01-03", "fiscalYear": "2025", "revenue": 5.2e9, "epsDiluted": 4.1},
        {"date": "2024-12-28", "fiscalYear": "2024", "revenue": 4.6e9, "epsDiluted": 3.9},
    ]
    estimates = [
        {"date": "2027-01-02", "revenueAvg": 5.9e9, "epsAvg": 4.8},
        {"date": "2028-01-01", "revenueAvg": 6.6e9, "epsAvg": 5.5},
        {"date": "2028-12-30", "revenueAvg": 7.3e9, "epsAvg": 6.2},
        {"date": "2029-12-29", "revenueAvg": 8.0e9, "epsAvg": 6.9},
    ]
    return income, estimates


@pytest.mark.parametrize("rows", [_hd_rows, _cdns_rows])
def test_projection_labels_equal_the_timeline_labels_for_the_same_estimates(rows):
    income, estimates = rows()
    rf = _build_revenue_forecast_partial(estimates, 5.0, 5.0, income)
    timeline_fy_by_end = {
        r["period_end"]: r["period"] for r in rf["annual_timeline"] if r["is_forecast"]
    }
    by_date = {e["date"]: e for e in estimates}
    assert rf["projections"]
    for p in rf["projections"]:
        # Each projection is one estimate row; find its period end through its revenue.
        est = next(e for e in by_date.values()
                   if C._format_revenue(C._est_revenue(e)) == p["revenue_label"])
        assert timeline_fy_by_end[est["date"]] == p["period"], (est["date"], p["period"])


def test_hd_projection_years_are_fiscal_not_calendar():
    income, estimates = _hd_rows()
    rf = _build_revenue_forecast_partial(estimates, 5.0, 5.0, income)
    # The estimate ending 2027-01-31 is FY2026 (HD's naming), not "2027".
    assert [p["period"] for p in rf["projections"]][:1] == ["2026"]


def test_projections_without_income_keep_the_old_labels():
    estimates = [
        {"date": "2024-12-31", "estimatedRevenueAvg": 100e9, "estimatedEpsAvg": 5.0},
        {"date": "2025-12-31", "estimatedRevenueAvg": 110e9, "estimatedEpsAvg": 5.5},
    ]
    rf = _build_revenue_forecast_partial(estimates, 10.0, 9.5)
    assert [p["period"] for p in rf["projections"]] == ["2024", "2025"]


# ═══════════════════════════════════════════════════════════════════════════
# R21 / R24 / R25 — capital allocation
# ═══════════════════════════════════════════════════════════════════════════


def _dp(period, *, div=200.0, bb=500.0, dy=0.9, by=1.6, shares=1000.0):
    return SignalOfConfidenceDataPointSchema(
        period=period, dividend_yield=dy, buyback_yield=by, dividend_amount=div,
        buyback_amount=bb, shares_outstanding=shares,
    )


def _soc(points, *, change=0.0, known=True, degraded=(), status="Moderate"):
    return SignalOfConfidenceResponse(
        symbol="T",
        data_points=list(points),
        summary=SignalOfConfidenceSummarySchema(
            total_yield=2.5, dividend_yield=0.9, buyback_yield=1.6,
            share_count_change=change, share_count_change_known=known,
            buyback_status=status,
        ),
        degraded=list(degraded),
    )


def _one_reported_count_soc():
    return _soc(
        [_dp("Q1 '26", shares=None), _dp("Q2 '26", shares=1000.0)], change=0.0, known=False,
    )


def test_unmeasured_share_change_is_flagged_on_the_block():
    block = _build_capital_allocation_block(_one_reported_count_soc())
    assert block["share_count_change_known"] is False
    assert block["share_count_change"] == 0.0  # the non-Optional wire placeholder
    CapitalAllocationResponse.model_validate(block)  # still a valid wire block


def _insight_prompt_with(ca):
    from app.services.agents.persona_config import get_persona_config

    shell = {"insider_data": {"sentiment": "Neutral", "transactions": [],
                              "capital_allocation": ca},
             "key_management": {"top_holders": [], "officers": []}}
    return np_._key_management_insight_prompt(
        get_persona_config("warren_buffett"), "evidence", shell,
    )


def test_the_insight_prompt_never_narrates_an_unmeasured_share_count_as_flat():
    prompt = _insight_prompt_with(_build_capital_allocation_block(_one_reported_count_soc()))
    assert "roughly flat" not in prompt
    assert "NOT REPORTED" in prompt


def test_a_missing_share_change_is_not_roughly_flat_either():
    ca = {"dividend_yield": 0.0, "data_points": []}  # no share_count_change at all
    prompt = _insight_prompt_with(ca)
    assert "roughly flat" not in prompt and "NOT REPORTED" in prompt


def test_a_measured_flat_share_count_still_reads_flat():
    ca = _build_capital_allocation_block(
        _soc([_dp("Q1 '26", shares=1000.0), _dp("Q2 '26", shares=1005.0)], change=0.5)
    )
    assert ca["share_count_change_known"] is True
    assert "roughly flat" in _insight_prompt_with(ca)


def test_the_digest_omits_an_unmeasured_share_count():
    block = _build_capital_allocation_block(_one_reported_count_soc())
    lines = np_._digest_insider({"insider_data": {"capital_allocation": block}})
    joined = "\n".join(lines)
    assert "share count" not in joined and "+0.0%" not in joined


def test_the_pdf_prints_a_dash_for_an_unmeasured_share_count():
    from app.services.pdf_report_service import build_context, render_html

    block = _build_capital_allocation_block(_one_reported_count_soc())
    html = render_html(build_context({"insider_data": {"capital_allocation": block}}))
    seg = html[html.index("Share Δ"):][:200]
    assert "—" in seg and "+0.0%" not in seg


def test_a_measured_zero_newest_quarter_is_kept():
    """R25 + R47: the SoC service already trims a newest quarter whose cash-flow row has
    not landed, so a newest all-zero point that reaches the report is MEASURED (buybacks
    paused) and must be shown — even when the build carries `cash_flow_row` (which, since
    P19, names only that already-trimmed newest edge; an interior gap sets no reason and
    ships its point `cash_flow_reported=False`). The report used to trim it a second time
    and drop a real quarter."""
    points = [
        _dp("Q3 '25", shares=1000.0),
        _dp("Q4 '25", shares=990.0),
        _dp("Q1 '26", shares=980.0),
        _dp("Q2 '26", div=0.0, bb=0.0, dy=0.0, by=0.0, shares=950.0),
    ]
    block = _build_capital_allocation_block(
        _soc(points, change=-5.0, degraded=["cash_flow_row"], status="Moderate")
    )
    assert [p["period"] for p in block["data_points"]] == ["Q3 '25", "Q4 '25", "Q1 '26", "Q2 '26"]
    assert block["data_points"][-1]["buyback_amount"] == 0.0
    assert block["share_count_change"] == -5.0  # the service's own measurement
    CapitalAllocationResponse.model_validate(block)


def test_no_cash_flow_row_reason_means_no_trim():
    points = [_dp("Q1 '26"), _dp("Q2 '26", div=0.0, bb=0.0, dy=0.0, by=0.0)]
    block = _build_capital_allocation_block(_soc(points, change=0.0))
    assert len(block["data_points"]) == 2 and block["share_count_change"] == 0.0


def test_a_company_that_returns_nothing_keeps_every_point():
    zero = dict(div=0.0, bb=0.0, dy=0.0, by=0.0)
    points = [_dp(f"Q{i} '25", shares=1000.0 + i, **zero) for i in range(1, 5)]
    block = _build_capital_allocation_block(
        _soc(points, change=0.3, degraded=["cash_flow_row"], status="Low")
    )
    assert [p["period"] for p in block["data_points"]] == ["Q1 '25", "Q2 '25", "Q3 '25", "Q4 '25"]


# ═══════════════════════════════════════════════════════════════════════════
# R44 — "Other"'s prior needs every member
# ═══════════════════════════════════════════════════════════════════════════


def _other_rows(prior_extra):
    cur = {"A": 600.0, "B": 300.0, "C": 40.0, "D": 30.0, "Other": 30.0}
    prior = {"A": 560.0, "B": 280.0, "C": 38.0, "Other": 29.0, **prior_extra}
    raw = [{"date": "2025-12-31", "fiscalYear": 2025, "data": cur},
           {"date": "2024-12-31", "fiscalYear": 2024, "data": prior}]
    rows = _segment_rows([("A", 600.0), ("B", 300.0), ("Other", 100.0)], "2025", raw, 1000.0, "T")
    return {r["name"]: r for r in rows}


def test_a_renamed_other_member_leaves_others_yoy_unmeasured():
    rows = _other_rows({"Dold": 27.0})  # FY2024 named it differently
    assert rows["Other"]["previous_revenue"] == 0.0
    assert rows["A"]["previous_revenue"] == 560.0


def test_other_prior_with_every_member_present_is_unchanged():
    rows = _other_rows({"D": 27.0})
    assert rows["Other"]["previous_revenue"] == pytest.approx(38.0 + 27.0 + 29.0)


# ═══════════════════════════════════════════════════════════════════════════
# R33 — PDF growth legend names the peer group it draws
# ═══════════════════════════════════════════════════════════════════════════


def _growth_legend(levels, sector):
    from app.services.pdf_report_service import build_context, render_html

    pts = [{"period": str(y), "value": 1e9 * (y - 2019), "yoy_change_percent": 10.0,
            "sector_average_yoy": sector} for y in (2022, 2023, 2024)]
    html = render_html(build_context(
        {"growth_chart": {"revenue_annual": pts, "peer_group_levels": levels}}
    ))
    i = html.index("YoY %")
    return html[i:i + 200]


def test_pdf_growth_legend_says_industry_for_an_industry_line():
    legend = _growth_legend({"revenue_annual": "industry"}, 6.0)
    assert "Industry avg" in legend and "Sector avg" not in legend


def test_pdf_growth_legend_says_sector_for_a_sector_line():
    assert "Sector avg" in _growth_legend({"revenue_annual": "sector"}, 6.0)


def test_pdf_growth_legend_hides_the_peer_entry_with_no_peer_values():
    legend = _growth_legend({}, None)
    assert "avg" not in legend.split("</div>")[0]
