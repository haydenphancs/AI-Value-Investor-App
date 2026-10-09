"""Report-side Financials deep check (W6b) — outlier tests for the paid report's copies of
the Financials-tab data: the Revenue Engine, the Earnings Timeline, the EPS Track Record,
the Altman-Z vital and the frozen Growth / Profit Power / Earnings sections.

Every test asserts the CORRECT DEGRADED behaviour (None / [] / "n/a"), never only the
happy path. Pure functions and in-memory stubs only: no FMP, no Supabase, no Gemini.
"""

from __future__ import annotations

import inspect
import math
import re
from datetime import date, timedelta

import pytest

from app.schemas.earnings import EarningsQuarterSchema, EarningsResponse
from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
from app.schemas.profit_power import ProfitPowerResponse
from app.schemas.revenue_breakdown import RevenueBreakdownResponse, RevenueSourceSchema
from app.schemas.ticker_report import (
    EarningsTrackRecordPointResponse,
    RevenueEngineResponse,
    RevenueForecastResponse,
)
from app.services.agents import narrative_prompts as np_
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    _attach_earnings_track_record,
    _build_annual_timeline,
    _build_health_vital,
    _build_revenue_engine,
    _build_timeline_prices,
    _forecast_analyst_count,
    _int_or_none,
    _refuse_degraded_financials,
    _revenue_engine_inputs,
    _top_named_segment,
    _track_record_result,
)


# ═══════════════════════════════════════════════════════════════════════════
# A. A degraded Financials build is never frozen into a report (#33, #49)
# ═══════════════════════════════════════════════════════════════════════════


def _growth(degraded=None) -> GrowthResponse:
    return GrowthResponse(
        symbol="AAPL",
        eps_annual=[GrowthDataPointSchema(period="2025", value=6.1)],
        eps_quarterly=[],
        revenue_annual=[GrowthDataPointSchema(period="2025", value=4.0e11)],
        revenue_quarterly=[],
        degraded=list(degraded or []),
    )


def _earnings_resp(quarters, degraded=None) -> EarningsResponse:
    return EarningsResponse(
        symbol="AAPL", eps_quarters=quarters, revenue_quarters=[], price_history=[],
        degraded=list(degraded or []),
    )


def _q(label, actual, estimate, surprise, fiscal_date, has_estimate=None):
    return EarningsQuarterSchema(
        quarter=label, actual_value=actual, estimate_value=estimate,
        surprise_percent=surprise, fiscal_date=fiscal_date, has_estimate=has_estimate,
    )


def test_a_degraded_growth_build_keeps_its_healthy_legs_and_is_recorded():
    """Round 2 (R2): a quarterly-income failure no longer drops the ANNUAL chart — only the
    series that leg fed are empty — and the loss is still recorded, which keeps the report
    out of every shared cache."""
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.growth_chart = _growth(["quarterly_income"])
    _refuse_degraded_financials(out)
    assert out.growth_chart is not None
    assert [p.value for p in out.growth_chart.eps_annual] == [6.1]
    assert out.growth_chart.eps_quarterly == [] and out.growth_chart.revenue_quarterly == []
    assert out.degraded_sections == ["growth_chart:quarterly_income"]


def test_a_complete_growth_build_is_kept():
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.growth_chart = _growth([])
    _refuse_degraded_financials(out)
    assert out.growth_chart is not None
    assert out.degraded_sections == []


def test_degraded_earnings_feed_is_dropped_but_a_price_leg_failure_is_not():
    """The report reads only `eps_quarters`: a failed PRICE leg leaves that intact, a
    failed earnings feed does not."""
    quarters = [_q("Q1 '25", 1.5, 1.4, 7.14, "2025-03-31")]
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.earnings = _earnings_resp(quarters, ["prices"])
    _refuse_degraded_financials(out)
    assert out.earnings is not None and out.degraded_sections == []

    out.earnings = _earnings_resp(quarters, ["prices", "earnings_feed"])
    _refuse_degraded_financials(out)
    assert out.earnings is None
    assert out.degraded_sections == ["earnings:earnings_feed"]


def test_degraded_profit_power_is_dropped_and_none_sections_are_ignored():
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.profit_power = ProfitPowerResponse(
        symbol="AAPL", annual=[], quarterly=[], degraded=["annual_income", "profile"],
    )
    _refuse_degraded_financials(out)  # growth_chart / earnings are None: no-op for them
    assert out.profit_power is None
    assert out.degraded_sections == ["profit_power:annual_income+profile"]


def test_a_malformed_degraded_value_never_raises():
    """A non-list `degraded` (an old cached object, a mock) is treated as complete — the
    gate must never take the collection down."""

    class _Odd:
        degraded = "quarterly_income"  # a string, not a list

    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.growth_chart = _Odd()  # type: ignore[assignment]
    _refuse_degraded_financials(out)
    assert out.growth_chart is not None and out.degraded_sections == []


@pytest.mark.asyncio
async def test_growth_status_rides_onto_the_response(monkeypatch):
    """`get_growth_with_status` returns the failed legs BESIDE the response; the collector
    copies them on so the gate reads one field."""
    import app.services.growth_service as growth_mod

    class _Svc:
        async def get_growth_with_status(self, ticker):
            return _growth([]), ["annual_cashflow"]

    monkeypatch.setattr(growth_mod, "get_growth_service", lambda: _Svc())
    resp = await C._growth_chart_with_status("AAPL")
    assert resp.degraded == ["annual_cashflow"]


def test_the_gate_runs_on_the_settled_pass1_results():
    """Source pin (comment-stripped): `_fetch_all` fetches growth WITH its status and runs
    the gate after the settle loop and before pass 2."""
    src = inspect.getsource(C.TickerReportDataCollector._fetch_all)
    src = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "_growth_chart_with_status(ticker)" in src
    assert "get_growth_service().get_growth(ticker)" not in src
    settle = src.index("_settle_pass1_result(out, attr, result, default, ticker)")
    gate = src.index("_refuse_degraded_financials(out)")
    pass2 = src.index("await self._fetch_dependent(out)")
    assert settle < gate < pass2


def test_degraded_sections_round_trip_the_collection_cache():
    from app.services.ticker_data_cache import _deserialize, _serialize
    import dataclasses

    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.profile = {"companyName": "Apple"}
    out.computed = {"current_price": 1.0}
    out.degraded_sections = ["growth_chart:quarterly_income"]
    blob = _serialize(out)
    assert blob is not None and blob["degraded_sections"] == ["growth_chart:quarterly_income"]
    back = _deserialize(blob, {f.name for f in dataclasses.fields(CollectedTickerData)})
    assert back.degraded_sections == ["growth_chart:quarterly_income"]


# ═══════════════════════════════════════════════════════════════════════════
# B. EPS Track Record: beat / miss / MET from the raw values (#79, #90)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("actual,estimate,expected", [
    (1.50, 1.50, "met"),
    (1.5001, 1.50, "beat"),     # surprise rounds to 0.0 — still a beat
    (1.4999, 1.50, "miss"),     # surprise rounds to -0.0 — still a miss
    (0.05, -0.10, "beat"),      # loss-maker turning profitable
    (-0.20, -0.10, "miss"),     # a wider loss than expected
    (None, 1.0, None),
    (1.0, float("nan"), None),
    (float("inf"), 1.0, None),
])
def test_track_record_result_compares_raw_values(actual, estimate, expected):
    assert _track_record_result(actual, estimate) == expected


def test_a_met_quarter_is_neither_a_beat_nor_a_miss():
    quarters = [
        _q("Q1 '25", 1.60, 1.50, 6.67, "2025-03-31"),
        _q("Q2 '25", 1.50, 1.50, 0.0, "2025-06-30"),
        _q("Q3 '25", 1.40, 1.50, -6.67, "2025-09-30"),
    ]
    rf: dict = {}
    _attach_earnings_track_record(rf, _earnings_resp(quarters))
    results = [r["result"] for r in rf["earnings_track_record"]]
    assert results == ["beat", "met", "miss"]
    assert [r["beat"] for r in rf["earnings_track_record"]] == [True, False, False]
    # Met counts in N, not in X — and is named.
    assert rf["beat_summary"] == "Beat 1 of 3 · 1 met"
    RevenueForecastResponse.model_validate({
        "cagr": 0.0, "eps_growth": 0.0, "management_guidance": "unknown",
        "projections": [], **rf,
    })


def test_a_tiny_beat_that_rounds_to_zero_is_still_a_beat():
    rf: dict = {}
    _attach_earnings_track_record(rf, _earnings_resp([_q("Q1 '25", 1.5001, 1.50, 0.0, "2025-03-31")]))
    point = rf["earnings_track_record"][0]
    assert point["result"] == "beat" and point["beat"] is True
    assert rf["beat_summary"] == "Beat 1 of 1"


def test_quarters_without_a_comparable_estimate_are_not_scored():
    quarters = [
        _q("Q1 '25", 1.60, 1.60, None, "2025-03-31", has_estimate=False),  # no consensus
        _q("Q2 '25", 1.60, 1.60, 0.0, "2025-06-30", has_estimate=False),   # defensive
        _q("Q3 '25", 1.70, 1.50, float("nan"), "2025-09-30"),              # bad surprise
        _q("Q4 '25", 1.70, 1.50, 13.33, "2025-12-31", has_estimate=True),
    ]
    rf: dict = {}
    _attach_earnings_track_record(rf, _earnings_resp(quarters))
    assert [r["period"] for r in rf["earnings_track_record"]] == ["Q4 '25"]
    assert rf["beat_summary"] == "Beat 1 of 1"


def test_the_ten_quarter_window_counts_only_scorable_quarters():
    quarters = [
        _q(f"Q{(i % 4) + 1} '{20 + i // 4}", 1.1, 1.0, 10.0, f"20{20 + i // 4}-{(i % 4) * 3 + 3:02d}-28")
        for i in range(12)
    ]
    # The newest quarter's estimate is unusable — it must not shrink the window to 9.
    quarters.append(_q("Q1 '23", 1.1, float("nan"), 10.0, "2023-03-31"))
    rf: dict = {}
    _attach_earnings_track_record(rf, _earnings_resp(quarters))
    assert len(rf["earnings_track_record"]) == 10


def test_track_record_point_schema_accepts_old_and_new_shapes():
    old = EarningsTrackRecordPointResponse.model_validate(
        {"period": "Q1 '24", "surprise_percent": 0.0, "beat": False}
    )
    assert old.result is None  # a cached report without the key still validates
    new = EarningsTrackRecordPointResponse.model_validate(
        {"period": "Q1 '24", "surprise_percent": 0.0, "beat": False, "result": "met"}
    )
    assert new.model_dump()["result"] == "met"
    with pytest.raises(Exception):
        EarningsTrackRecordPointResponse.model_validate(
            {"period": "Q1 '24", "surprise_percent": 0.0, "beat": False, "result": "inline"}
        )


# ═══════════════════════════════════════════════════════════════════════════
# C. The forecast prompt summarises surprises by the MEDIAN (#64)
# ═══════════════════════════════════════════════════════════════════════════


def _forecast_shell(track):
    return {"revenue_forecast": {
        "cagr": 10.0, "eps_growth": 12.0, "management_guidance": "unknown",
        "projections": [], "annual_timeline": [],
        "earnings_track_record": track, "beat_summary": "Beat 10 of 10",
    }}


def _persona():
    from app.services.agents.persona_config import get_persona_config
    return get_persona_config("warren_buffett")


def test_one_near_zero_estimate_quarter_cannot_dominate_the_surprise_summary():
    track = [{"surprise_percent": 3.0}] * 9 + [{"surprise_percent": 1100.0}]
    prompt = np_._revenue_forecast_insight_prompt(_persona(), "EVIDENCE", _forecast_shell(track))
    assert "median EPS surprise +3.0% over the last 10 quarters" in prompt
    assert "112.7" not in prompt and "avg EPS surprise" not in prompt


def test_unusable_surprises_are_skipped_in_the_summary():
    track = [{"surprise_percent": None}, {"surprise_percent": "x"}, {"surprise_percent": True},
             {"surprise_percent": float("nan")}, {"surprise_percent": -2.0}, "junk"]
    prompt = np_._revenue_forecast_insight_prompt(_persona(), "EVIDENCE", _forecast_shell(track))
    assert "median EPS surprise -2.0% over the last 1 quarters" in prompt


def test_a_track_record_with_no_usable_surprise_says_so():
    prompt = np_._revenue_forecast_insight_prompt(
        _persona(), "EVIDENCE", _forecast_shell([{"surprise_percent": None}]),
    )
    assert "no reported beat/miss history" in prompt


# ═══════════════════════════════════════════════════════════════════════════
# D. Altman Z zones: Distress <= 1.8, Grey (1.8, 3.0], Safe > 3.0 (#74)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("z,level,label_start", [
    (1.79, "critical", "Distress"),
    (1.80, "critical", "Distress"),
    (1.81, "weak", "Grey"),
    (2.99, "moderate", "Grey"),
    (3.00, "moderate", "Grey"),
    (3.01, "strong", "Safe"),
])
def test_report_altman_zone_boundaries(z, level, label_start):
    h = _build_health_vital(altman_z=z, debt_equity=None, fcf_negative=False)
    assert h["level"] == level
    assert h["altman_z_label"].startswith(label_start)


@pytest.mark.parametrize("z", [1.79, 1.80, 1.81, 2.99, 3.00, 3.01])
def test_report_zone_matches_the_health_check_card(z):
    from app.services.health_check_service import _zscore_status

    report_level = _build_health_vital(altman_z=z, debt_equity=None, fcf_negative=False)["level"]
    card = _zscore_status(z)
    expected = {"negative": {"critical"}, "neutral": {"weak", "moderate"}, "positive": {"strong"}}
    assert report_level in expected[card], (z, report_level, card)


def test_moving_the_boundary_moves_no_score():
    """The base is continuous at both cut points, so the score is identical on either
    side of the boundary (to rounding) — only the label moved."""
    def score(z):
        return _build_health_vital(altman_z=z, debt_equity=None, fcf_negative=False)["score"]["value"]
    assert score(1.8) == pytest.approx(score(1.8001), abs=0.05)
    assert score(3.0) == pytest.approx(score(3.0001), abs=0.05)


# ═══════════════════════════════════════════════════════════════════════════
# E. Revenue Engine (#7, #11, #46, #47, #50, #51, #52)
# ═══════════════════════════════════════════════════════════════════════════


def _breakdown(sources, *, fy="2025", reported=None, elim=None, degraded=None):
    return RevenueBreakdownResponse(
        symbol="T", fiscal_year=fy,
        revenue_sources=[RevenueSourceSchema(name=n, value=v) for n, v in sources],
        cost_of_sales=0.0, operating_expense=0.0, tax=0.0,
        reported_revenue=reported, intersegment_eliminations=elim,
        degraded=list(degraded or []),
    )


_INTC_SOURCES = [
    ("Client Computing Group", 32.228e9),
    ("Intel Foundry", 17.826e9),
    ("Data Center and AI", 16.9e9),
    ("All Other", 3.582e9),
]
_INTC_REPORTED = 52.853e9


def _intc_breakdown():
    gross = sum(v for _, v in _INTC_SOURCES)
    return _breakdown(_INTC_SOURCES, reported=_INTC_REPORTED, elim=gross - _INTC_REPORTED)


def test_intc_gross_stack_uses_reported_revenue_and_carries_the_eliminations():
    inputs = _revenue_engine_inputs(_intc_breakdown(), [], [], ticker="INTC")
    engine = _build_revenue_engine(
        inputs["segments"], fiscal_year=inputs["fiscal_year"],
        total_revenue=inputs["total_revenue"],
        intersegment_eliminations=inputs["intersegment_eliminations"],
    )
    assert engine["total_revenue"] == 52853.0
    assert engine["intersegment_eliminations"] == 17683.0
    assert engine["period"] == "FY 2025"
    assert engine["revenue_unit"] == "Millions"
    client = next(s for s in engine["segments"] if s["name"] == "Client Computing Group")
    assert round(client["current_revenue"] / client["total_revenue"] * 100) == 61
    # The shares add to 134%; the eliminations line brings them back to 100%.
    shares = sum(s["current_revenue"] for s in engine["segments"]) - engine["intersegment_eliminations"]
    assert shares == pytest.approx(engine["total_revenue"], abs=0.1)
    RevenueEngineResponse.model_validate(engine)


def test_the_gross_breakdown_from_the_real_reconciler_feeds_the_same_numbers():
    from app.services.revenue_breakdown_service import _reconcile_segments

    srcs = [RevenueSourceSchema(name=n, value=v) for n, v in _INTC_SOURCES]
    out, elim, outcome = _reconcile_segments(srcs, _INTC_REPORTED, 17.683e9, ticker="INTC")
    assert outcome == "gross"
    bd = _breakdown([(s.name, s.value) for s in out], reported=_INTC_REPORTED, elim=elim)
    inputs = _revenue_engine_inputs(bd, [], [], ticker="INTC")
    assert inputs["total_revenue"] == _INTC_REPORTED
    assert inputs["intersegment_eliminations"] == pytest.approx(17.683e9)


def test_ford_thin_placeholder_is_unavailable_never_the_raw_feed():
    """The service rejected Ford's 7% "Ford Credit" feed and served Total Revenue. The
    report must agree — not resurrect the raw feed as "Ford Credit 100% of total"."""
    bd = _breakdown([("Total Revenue", 185.0e9)], reported=185.0e9)
    raw = [{"date": "2025-12-31", "fiscalYear": 2025, "data": {"Ford Credit": 13.27e9}}]
    income = [{"date": "2025-12-31", "fiscalYear": "2025", "revenue": 185.0e9}]
    inputs = _revenue_engine_inputs(bd, raw, income, ticker="F")
    assert inputs["segments"] == []
    engine = _build_revenue_engine(inputs["segments"], fiscal_year=inputs["fiscal_year"])
    assert engine["segments"] == [] and engine["total_revenue"] == 0.0
    assert _top_named_segment(inputs["segments"]) is None


def test_a_deliberate_placeholder_is_respected_even_when_the_raw_feed_looks_healthy():
    """The service chose Total Revenue (e.g. no segmentation year paired with ITS income
    years). The report must not second-guess it with its own raw fetch — the two views of
    one company would disagree. Only a FAILED breakdown falls back to raw."""
    bd = _breakdown([("Total Revenue", 100.0e9)], reported=100.0e9)
    raw = [{"date": "2025-12-31", "fiscalYear": 2025, "data": {"Cloud": 60.0e9, "Devices": 40.0e9}}]
    income = [{"date": "2025-12-31", "fiscalYear": "2025", "revenue": 100.0e9}]
    assert _revenue_engine_inputs(bd, raw, income, ticker="T")["segments"] == []
    # …while the same raw feed IS used once the breakdown itself failed.
    assert len(_revenue_engine_inputs(None, raw, income, ticker="T")["segments"]) == 2


def test_a_failed_breakdown_with_a_thin_raw_feed_is_still_unavailable():
    raw = [{"date": "2025-12-31", "fiscalYear": 2025, "data": {"Ford Credit": 13.27e9}}]
    income = [{"date": "2025-12-31", "fiscalYear": "2025", "revenue": 185.0e9}]
    assert _revenue_engine_inputs(None, raw, income, ticker="F")["segments"] == []


def test_a_failed_breakdown_rebuilds_from_raw_through_the_service_rules():
    """CAT-shaped raw feed: a "Reportable Subsegments" TOTAL line and a positively signed
    eliminations row must not become segments; the result reconciles to income revenue."""
    raw = [{
        "date": "2025-12-31", "fiscalYear": 2025, "data": {
            "Construction Industries": 25.0e9,
            "Resource Industries": 12.0e9,
            "Energy & Transportation": 27.0e9,
            "Reportable Subsegments": 64.0e9,
            "Intersegment Eliminations": 4.0e9,
        },
    }]
    income = [{"date": "2025-12-31", "fiscalYear": "2025", "revenue": 64.0e9}]
    inputs = _revenue_engine_inputs(None, raw, income, ticker="CAT")
    names = {s["name"] for s in inputs["segments"]}
    assert names == {"Construction Industries", "Resource Industries", "Energy & Transportation"}
    assert inputs["total_revenue"] == 64.0e9 and inputs["fiscal_year"] == "2025"


def test_a_degraded_segment_leg_is_treated_as_a_failure_not_a_placeholder():
    bd = _breakdown([("Total Revenue", 100.0e9)], reported=100.0e9,
                    degraded=["segmentation_unavailable"])
    raw = [{"date": "2025-12-31", "fiscalYear": 2025, "data": {"Cloud": 60.0e9, "Devices": 40.0e9}}]
    income = [{"date": "2025-12-31", "fiscalYear": "2025", "revenue": 100.0e9}]
    inputs = _revenue_engine_inputs(bd, raw, income, ticker="T")
    assert [s["name"] for s in inputs["segments"]] == ["Cloud", "Devices"]


def test_raw_rebuild_without_a_matching_income_year_is_unavailable():
    raw = [{"date": "2025-12-31", "fiscalYear": 2025, "data": {"Cloud": 60.0e9}}]
    income = [{"date": "2023-12-31", "fiscalYear": "2023", "revenue": 50.0e9}]
    assert _revenue_engine_inputs(None, raw, income, ticker="T")["segments"] == []
    assert _revenue_engine_inputs(None, raw, None, ticker="T")["segments"] == []
    assert _revenue_engine_inputs(None, ["junk", None], [None], ticker="T")["segments"] == []


def test_prior_year_is_chosen_by_fiscal_year_not_position():
    """Thin newest year (FY2025), so the breakdown is FY2024. The raw feed arrives
    UNSORTED. The prior must be FY2023 — never FY2024 paired with itself (+0.0%)."""
    bd = _breakdown([("Cloud", 60.0e9), ("Devices", 40.0e9)], fy="2024", reported=100.0e9)
    raw = [
        {"date": "2023-12-31", "fiscalYear": 2023, "data": {"Cloud": 45.0e9, "Devices": 40.0e9}},
        {"date": "2025-12-31", "fiscalYear": 2025, "data": {"Credit": 7.0e9}},
        {"date": "2024-12-31", "fiscalYear": 2024, "data": {"Cloud": 60.0e9, "Devices": 40.0e9}},
    ]
    segs = _revenue_engine_inputs(bd, raw, [], ticker="T")["segments"]
    cloud = next(s for s in segs if s["name"] == "Cloud")
    assert cloud["previous_revenue"] == 45.0e9
    assert C._segment_growth_pct([cloud]) == pytest.approx(33.3, abs=0.1)


def test_a_missing_prior_year_leaves_yoy_unmeasured():
    bd = _breakdown([("Cloud", 60.0e9), ("Devices", 40.0e9)], fy="2025", reported=100.0e9)
    raw = [
        {"date": "2025-12-31", "fiscalYear": 2025, "data": {"Cloud": 60.0e9, "Devices": 40.0e9}},
        {"date": "2023-12-31", "fiscalYear": 2023, "data": {"Cloud": 45.0e9, "Devices": 40.0e9}},
    ]
    segs = _revenue_engine_inputs(bd, raw, [], ticker="T")["segments"]
    assert all(s["previous_revenue"] == 0.0 for s in segs)
    assert C._segment_growth_pct(segs) is None


def test_other_prior_is_the_folded_keys_prior_not_a_raw_other_key():
    """AMZN-shaped: this year's "Other" = Physical Stores (3.3%) + the filer's own
    "Other". Its prior must be the prior year's Physical Stores + Other (24.96B), not the
    raw "Other" alone (4.96B → a fabricated +434%)."""
    cur = {"Online": 247.0e9, "Third-party": 156.0e9, "AWS": 107.0e9, "Ads": 56.0e9,
           "Subscription": 44.0e9, "Physical Stores": 21.2e9, "Other": 5.3e9}
    prior = {"Online": 231.0e9, "Third-party": 140.0e9, "AWS": 90.0e9, "Ads": 47.0e9,
             "Subscription": 40.0e9, "Physical Stores": 20.0e9, "Other": 4.96e9}
    bd = _breakdown(
        [("Online", 247.0e9), ("Third-party", 156.0e9), ("AWS", 107.0e9), ("Ads", 56.0e9),
         ("Subscription", 44.0e9), ("Other", 26.5e9)],
        fy="2024", reported=sum(cur.values()),
    )
    raw = [{"date": "2024-12-31", "fiscalYear": 2024, "data": cur},
           {"date": "2023-12-31", "fiscalYear": 2023, "data": prior}]
    segs = _revenue_engine_inputs(bd, raw, [], ticker="AMZN")["segments"]
    other = next(s for s in segs if s["name"] == "Other")
    assert other["previous_revenue"] == pytest.approx(24.96e9)
    aws = next(s for s in segs if s["name"] == "AWS")
    assert aws["previous_revenue"] == 90.0e9


def test_an_other_bucket_the_raw_record_cannot_reproduce_has_no_prior():
    bd = _breakdown([("Cloud", 80.0e9), ("Other", 20.0e9)], fy="2025", reported=100.0e9)
    raw = [
        # this year's raw record does not add up to the breakdown's "Other"
        {"date": "2025-12-31", "fiscalYear": 2025, "data": {"Cloud": 80.0e9, "Misc": 1.0e9}},
        {"date": "2024-12-31", "fiscalYear": 2024, "data": {"Cloud": 70.0e9, "Misc": 0.9e9}},
    ]
    segs = _revenue_engine_inputs(bd, raw, [], ticker="T")["segments"]
    assert next(s for s in segs if s["name"] == "Other")["previous_revenue"] == 0.0
    assert next(s for s in segs if s["name"] == "Cloud")["previous_revenue"] == 70.0e9


def test_unallocated_has_no_prior_and_is_never_the_top_segment():
    bd = _breakdown([("Unallocated", 45.0e9), ("Cloud", 40.0e9), ("Devices", 15.0e9)],
                    fy="2025", reported=100.0e9)
    raw = [{"date": "2024-12-31", "fiscalYear": 2024,
            "data": {"Cloud": 30.0e9, "Devices": 14.0e9, "Unallocated": 99.0e9}}]
    inputs = _revenue_engine_inputs(bd, raw, [], ticker="T")
    unalloc = next(s for s in inputs["segments"] if s["name"] == "Unallocated")
    assert unalloc["previous_revenue"] == 0.0
    assert _top_named_segment(inputs["segments"])["name"] == "Cloud"
    assert inputs["total_revenue"] == 100.0e9


@pytest.mark.parametrize("reported", [None, 0.0, -5.0e9, float("nan"), float("inf")])
def test_unusable_reported_revenue_falls_back_to_the_segment_sum(reported):
    bd = _breakdown([("Cloud", 60.0e9), ("Devices", 40.0e9)], reported=reported, elim=10.0e9)
    inputs = _revenue_engine_inputs(bd, [], [], ticker="T")
    assert inputs["total_revenue"] == 100.0e9
    # Eliminations need a reported total to be measured against.
    assert inputs["intersegment_eliminations"] is None


def test_a_non_gross_stack_keeps_eliminations_none():
    bd = _breakdown([("Cloud", 60.0e9), ("Devices", 40.0e9)], reported=100.5e9)
    engine_inputs = _revenue_engine_inputs(bd, [], [], ticker="T")
    engine = _build_revenue_engine(
        engine_inputs["segments"], fiscal_year="2025",
        total_revenue=engine_inputs["total_revenue"],
        intersegment_eliminations=engine_inputs["intersegment_eliminations"],
    )
    assert engine["intersegment_eliminations"] is None
    assert engine["total_revenue"] == 100500.0


def test_non_finite_and_non_positive_breakdown_sources_are_ignored():
    bd = _breakdown([("Cloud", 60.0e9), ("Bad", float("nan")), ("Neg", -1.0e9), ("Zero", 0.0)],
                    reported=60.0e9)
    names = [s["name"] for s in _revenue_engine_inputs(bd, [], [], ticker="T")["segments"]]
    assert names == ["Cloud"]


@pytest.mark.parametrize("total", [5.0e8, 5.0e9, 2.0e12])
def test_revenue_unit_is_always_the_true_unit_of_the_values(total):
    segs = [{"name": "A", "current_revenue": total, "previous_revenue": 0.0, "total_revenue": total}]
    engine = _build_revenue_engine(segs, fiscal_year="2025", total_revenue=total)
    assert engine["revenue_unit"] == "Millions"
    assert engine["segments"][0]["current_revenue"] == round(total / 1e6, 2)


def test_period_is_the_fiscal_year_never_the_generation_year():
    segs = [{"name": "A", "current_revenue": 1e9, "previous_revenue": 0.0, "total_revenue": 1e9}]
    assert _build_revenue_engine(segs, fiscal_year="2024")["period"] == "FY 2024"
    assert _build_revenue_engine(segs, fiscal_year=None)["period"] == ""
    assert _build_revenue_engine([], fiscal_year=None)["period"] == ""
    body = inspect.getsource(C._build_revenue_engine)
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    code = re.sub(r'"""[\s\S]*?"""', "", code)
    assert "now.year" not in code and "datetime" not in code


def test_build_revenue_engine_is_backward_compatible_without_a_total():
    segs = [{"name": "A", "current_revenue": 3e9, "previous_revenue": 2e9, "total_revenue": 0},
            {"name": "B", "current_revenue": 1e9, "previous_revenue": 1e9, "total_revenue": 0}]
    engine = _build_revenue_engine(segs)
    assert engine["total_revenue"] == 4000.0 and engine["intersegment_eliminations"] is None


# ── The prompts never pair a millions value with "Billions" ──


def _engine_report(engine):
    return {"revenue_engine": engine}


def test_analysis_note_prompt_formats_dollars_and_explains_a_gross_stack():
    # A CONFIRMED US-dollar engine (final review 2026-10-09: only USD prints amounts in the note,
    # since the iOS card's rows are "$"; an unknown currency is amount-free — test_segment_money_currency).
    inputs = _revenue_engine_inputs(_intc_breakdown(), [], [], ticker="INTC")
    engine = _build_revenue_engine(
        inputs["segments"], fiscal_year="2025", total_revenue=inputs["total_revenue"],
        intersegment_eliminations=inputs["intersegment_eliminations"], reporting_currency="USD",
    )
    prompt = np_._revenue_engine_analysis_note_prompt(_persona(), "EVIDENCE", _engine_report(engine))
    assert "Client Computing Group $32.2B (61% of revenue" in prompt
    assert "GROSS of $17.7B" in prompt and "Do not sum the shares" in prompt
    assert "Billions" not in prompt and "Millions" not in prompt


def test_analysis_note_prompt_has_no_gross_note_for_a_normal_stack():
    engine = _build_revenue_engine(
        [{"name": "iPhone", "current_revenue": 209.586e9, "previous_revenue": 201.0e9,
          "total_revenue": 416.0e9}],
        fiscal_year="2025", total_revenue=416.0e9, reporting_currency="USD",
    )
    prompt = np_._revenue_engine_analysis_note_prompt(_persona(), "EVIDENCE", _engine_report(engine))
    assert "iPhone $209.6B" in prompt and "GROSS" not in prompt


def test_digest_formats_millions_and_ignores_a_legacy_billions_unit():
    legacy = {"segments": [{"name": "iPhone", "current_revenue": 209586.0,
                            "previous_revenue": 201183.0, "total_revenue": 391035.0}],
              "revenue_unit": "Billions", "total_revenue": 391035.0, "period": "FY 2026"}
    line = " ".join(np_._digest_revenue_engine({"revenue_engine": legacy}))
    # A legacy report carries no `reporting_currency`: formatted from millions, but never dressed
    # as dollars (final review 2026-10-09) — the header says the currency is not confirmed.
    assert "iPhone 209.6B (+4% YoY)" in line and "not confirmed" in line
    assert "Billions" not in line and "209,586" not in line
    usd = " ".join(np_._digest_revenue_engine({"revenue_engine": {**legacy, "reporting_currency": "USD"}}))
    assert "iPhone $209.6B (+4% YoY)" in usd


@pytest.mark.parametrize("v,expected", [
    (209586.0, "$209.6B"), (1_200_000.0, "$1.20T"), (512.0, "$512M"), (0.25, "$250,000"),
    (-394.0, "-$394M"), (None, None), ("x", None), (float("nan"), None),
])
def test_fmt_millions_usd(v, expected):
    assert np_._fmt_millions_usd(v) == expected


def test_financial_context_segments_are_formatted_dollars():
    out = CollectedTickerData(ticker="INTC", persona_key="warren_buffett")
    out.profile = {"companyName": "Intel", "sector": "Technology"}
    inputs = _revenue_engine_inputs(_intc_breakdown(), [], [], ticker="INTC")
    out.revenue_engine_partial = _build_revenue_engine(
        inputs["segments"], fiscal_year="2025", total_revenue=inputs["total_revenue"],
        intersegment_eliminations=inputs["intersegment_eliminations"], reporting_currency="USD",
    )
    text = C.build_financial_context(out)
    block = text.split("Revenue Segments", 1)[1].split("\n\n", 1)[0]
    assert "(FY 2025)" in block
    assert "Client Computing Group: $32.2B (prior year: n/a)" in block
    assert "Total revenue: $52.9B" in block
    assert "Intersegment eliminations: -$17.7B" in block
    assert "Billions" not in block and "32228.0" not in block


# ═══════════════════════════════════════════════════════════════════════════
# F. Earnings Timeline: fiscal years, EPS basis, signed labels, period ends
#    (#45, #48, #80, #81)
# ═══════════════════════════════════════════════════════════════════════════


def _saturday_nearest_dec31_income(with_fiscal_year: bool):
    ends = [("2020", "2021-01-02"), ("2021", "2022-01-01"), ("2022", "2022-12-31"),
            ("2023", "2023-12-30"), ("2024", "2024-12-28"), ("2025", "2026-01-03")]
    rows = []
    for i, (fy, end) in enumerate(ends):
        row = {"symbol": "SNA", "date": end, "revenue": (4.0 + 0.2 * i) * 1e9,
               "epsDiluted": 15.0 + i}
        if with_fiscal_year:
            row["fiscalYear"] = fy
        rows.append(row)
    return list(reversed(rows))  # FMP: newest first


_SNA_ESTIMATES = [
    {"date": "2026-12-31", "revenueAvg": 5.4e9, "epsAvg": 21.5, "numAnalystsRevenue": 8,
     "numAnalystsEps": 9},
    {"date": "2027-12-31", "revenueAvg": 5.7e9, "epsAvg": 23.0, "numAnalystsRevenue": 6,
     "numAnalystsEps": 7},
]


@pytest.mark.parametrize("with_fiscal_year", [True, False])
def test_52_53_week_years_are_fiscal_with_no_duplicates_or_holes(with_fiscal_year):
    tl = _build_annual_timeline(_saturday_nearest_dec31_income(with_fiscal_year), _SNA_ESTIMATES)
    assert [t["period"] for t in tl] == ["2021", "2022", "2023", "2024", "2025", "2026", "2027"]
    assert [t["is_forecast"] for t in tl] == [False] * 5 + [True] * 2
    fy2025 = next(t for t in tl if t["period"] == "2025")
    fy2026 = next(t for t in tl if t["period"] == "2026")
    assert fy2025["period_end"] == "2026-01-03"
    # FY2026 revenue YoY is measured against FY2025, not two years back.
    assert fy2026["revenue_yoy_pct"] == pytest.approx((5.4 - 5.0) / 5.0 * 100, abs=0.1)
    assert all(t["period_end"] for t in tl)


def test_jan_31_fiscal_years_keep_their_labels():
    income = [{"symbol": "WMT", "date": f"{y}-01-31", "fiscalYear": str(y), "revenue": y * 1e8,
               "epsDiluted": 2.0} for y in (2023, 2024, 2025, 2026)]
    estimates = [{"date": "2027-01-31", "revenueAvg": 2027 * 1e8, "epsAvg": 2.5}]
    tl = _build_annual_timeline(income, estimates)
    assert [t["period"] for t in tl] == ["2023", "2024", "2025", "2026", "2027"]


def test_forecast_analyst_count_uses_the_first_fiscal_forecast_year():
    assert _forecast_analyst_count(_saturday_nearest_dec31_income(True), _SNA_ESTIMATES) == 9
    assert _forecast_analyst_count([], []) is None


def test_eps_yoy_is_none_where_the_basis_changes_but_revenue_yoy_is_kept():
    income = [
        {"date": "2024-11-03", "fiscalYear": "2024", "revenue": 51.6e9, "epsDiluted": 1.23},
        {"date": "2025-11-02", "fiscalYear": "2025", "revenue": 63.9e9, "epsDiluted": 4.77},
    ]
    estimates = [
        {"date": "2026-11-01", "revenueAvg": 80.0e9, "epsAvg": 10.0},
        {"date": "2027-10-31", "revenueAvg": 95.0e9, "epsAvg": 12.5},
    ]
    tl = {t["period"]: t for t in _build_annual_timeline(income, estimates)}
    assert tl["2026"]["eps_yoy_pct"] is None                       # GAAP → consensus
    assert tl["2026"]["revenue_yoy_pct"] == pytest.approx(25.2, abs=0.1)
    assert tl["2027"]["eps_yoy_pct"] == 25.0                       # consensus → consensus
    assert tl["2025"]["eps_yoy_pct"] == pytest.approx(287.8, abs=0.1)  # GAAP → GAAP
    assert [tl[p]["eps_basis"] for p in ("2024", "2025", "2026", "2027")] == [
        "gaap", "gaap", "consensus", "consensus"]


def test_estimates_only_timeline_has_no_first_year_yoy():
    tl = _build_annual_timeline([], [{"date": "2026-12-31", "revenueAvg": 1e9, "epsAvg": 1.0},
                                     {"date": "2027-12-31", "revenueAvg": 2e9, "epsAvg": 2.0}])
    assert [t["period"] for t in tl] == ["2026", "2027"]
    assert tl[0]["eps_yoy_pct"] is None and tl[0]["revenue_yoy_pct"] is None
    assert tl[1]["eps_yoy_pct"] == 100.0


def test_negative_revenue_and_eps_are_labelled_with_their_sign():
    income = [
        {"date": "2023-12-31", "revenue": 0.5e9, "epsDiluted": 1.0},
        {"date": "2024-12-31", "revenue": -1.1e9, "epsDiluted": -2.0},
        {"date": "2025-12-31", "revenue": 1.0e9, "epsDiluted": 0.0},
    ]
    tl = {t["period"]: t for t in _build_annual_timeline(income, [])}
    assert tl["2024"]["revenue_label"] == "-$1.1B" and tl["2024"]["revenue"] < 0
    assert tl["2024"]["eps_label"] == "-$2.00"
    assert tl["2025"]["revenue_label"] == "$1.0B"
    assert tl["2025"]["eps_label"] == "$0.00"
    assert tl["2023"]["revenue_label"] == "$500M"


def test_a_tiny_negative_eps_never_prints_minus_zero():
    tl = _build_annual_timeline([{"date": "2025-12-31", "revenue": 1e9, "epsDiluted": -0.001}], [])
    assert tl[0]["eps_label"] == "$0.00"


def test_a_gap_year_has_no_yoy():
    income = [{"date": "2021-12-31", "revenue": 1e9, "epsDiluted": 1.0},
              {"date": "2023-12-31", "revenue": 2e9, "epsDiluted": 2.0}]
    tl = {t["period"]: t for t in _build_annual_timeline(income, [])}
    assert tl["2023"]["revenue_yoy_pct"] is None and tl["2023"]["eps_yoy_pct"] is None


def test_two_rows_for_one_fiscal_year_keep_the_later_period_end():
    income = [
        {"date": "2024-06-30", "fiscalYear": "2024", "revenue": 1e9, "epsDiluted": 1.0},
        {"date": "2024-12-31", "fiscalYear": "2024", "revenue": 0.6e9, "epsDiluted": 0.4},
        {"date": "2025-12-31", "fiscalYear": "2025", "revenue": 1.3e9, "epsDiluted": 1.1},
    ]
    tl = _build_annual_timeline(income, [])
    assert [t["period"] for t in tl] == ["2024", "2025"]
    assert tl[0]["period_end"] == "2024-12-31"


def test_malformed_rows_are_skipped_and_nan_analyst_counts_do_not_crash():
    income = ["junk", None, {"date": "not-a-date", "revenue": 1e9},
              {"date": "2024-12-31", "revenue": 1e9, "epsDiluted": 1.0}]
    estimates = [None, {"date": "2025-12-31", "revenueAvg": 1.1e9, "epsAvg": 1.1,
                        "numAnalystsRevenue": float("nan"), "numAnalystsEps": float("inf")}]
    tl = _build_annual_timeline(income, estimates)
    assert [t["period"] for t in tl] == ["2024", "2025"]
    assert tl[1]["revenue_analyst_count"] is None and tl[1]["eps_analyst_count"] is None


@pytest.mark.parametrize("v,expected", [
    (7, 7), (7.0, 7), (0, None), (None, None), (float("nan"), None), (float("inf"), None),
    (True, None), ("7", None),
])
def test_int_or_none_is_total(v, expected):
    assert _int_or_none(v) == expected


def test_timeline_rows_validate_with_and_without_the_new_keys():
    tl = _build_annual_timeline(_saturday_nearest_dec31_income(True), _SNA_ESTIMATES)
    rf = {"cagr": 0.0, "eps_growth": 0.0, "management_guidance": "unknown",
          "projections": [], "annual_timeline": tl}
    model = RevenueForecastResponse.model_validate(rf)
    assert model.annual_timeline[0].period_end == "2022-01-01"   # FY2021's 52-week close
    assert model.annual_timeline[-1].eps_basis == "consensus"
    legacy = [{k: v for k, v in row.items() if k not in ("period_end", "eps_basis")} for row in tl]
    legacy_model = RevenueForecastResponse.model_validate({**rf, "annual_timeline": legacy})
    assert legacy_model.annual_timeline[0].period_end is None


# ── The price overlay window and its iOS column mapping ──


_NVDA_TIMELINE = [
    {"period": "2022", "period_end": "2022-01-30", "is_forecast": False},
    {"period": "2023", "period_end": "2023-01-29", "is_forecast": False},
    {"period": "2024", "period_end": "2024-01-28", "is_forecast": False},
    {"period": "2025", "period_end": "2025-01-26", "is_forecast": False},
    {"period": "2026", "period_end": "2026-01-25", "is_forecast": False},
    {"period": "2027", "period_end": "2027-01-31", "is_forecast": True},
]


def test_price_window_opens_at_the_first_fiscal_years_start():
    historical = {"historical": [
        {"date": "2021-01-20", "close": 120.0},   # before FY2022 opened → dropped
        {"date": "2021-02-16", "close": 150.0},   # inside FY2022 (calendar 2021) → KEPT
        {"date": "2025-06-16", "close": 140.0},
    ]}
    pts = _build_timeline_prices(historical, _NVDA_TIMELINE)
    assert [p["date"] for p in pts] == ["2021-02-16", "2025-06-16"]


def test_price_window_falls_back_to_the_calendar_floor_without_period_ends():
    legacy = [{k: v for k, v in r.items() if k != "period_end"} for r in _NVDA_TIMELINE]
    historical = {"historical": [{"date": "2021-02-16", "close": 150.0},
                                 {"date": "2022-03-01", "close": 200.0}]}
    pts = _build_timeline_prices(historical, legacy)
    assert [p["date"] for p in pts] == ["2022-03-01"]


def _ios_price_columns(timeline, prices):
    """Python port of `EarningsTimelineChart.priceColumnsByPeriodEnd` (no lead-in trim):
    column i spans (end[i-1], end[i]]; the first spans 365 days before its end."""
    edges = [date.fromisoformat(r["period_end"]) for r in timeline]
    assert all(b > a for a, b in zip(edges, edges[1:]))
    first_start = edges[0] - timedelta(days=365)
    out = []
    for ds, price in prices:
        d = date.fromisoformat(ds)
        if not (first_start < d <= edges[-1]):
            continue
        i = next(k for k, e in enumerate(edges) if d <= e)
        start = first_start if i == 0 else edges[i - 1]
        out.append((i + (d - start).days / max((edges[i] - start).days, 1), price))
    return out


def test_ios_mapping_puts_a_close_in_its_fiscal_column():
    cols = _ios_price_columns(_NVDA_TIMELINE, [("2025-06-16", 140.0), ("2026-09-30", 180.0),
                                               ("2021-02-16", 150.0), ("2027-02-01", 1.0)])
    # Calendar-2025 June is FY2026 (column 4), ~40% through it — not FY2025 (column 3).
    assert int(cols[0][0]) == 4 and 0.35 < cols[0][0] - 4 < 0.45
    # Today's price sits in the in-progress FORECAST column (FY2027), not the last actual.
    assert int(cols[1][0]) == 5
    assert int(cols[2][0]) == 0
    assert len(cols) == 3  # past the last column's end → dropped


def test_ios_mapping_matches_the_swift_source():
    """The Python port above is only evidence if the Swift still has that shape."""
    from pathlib import Path

    swift = (Path(__file__).resolve().parents[2] / "frontend/ios/ios/Views/Molecules/"
             "EarningsTimelineChart.swift").read_text()
    code = "\n".join(ln.split("//", 1)[0] for ln in swift.splitlines())
    assert "private static let firstColumnDays = 365" in code
    assert C._TIMELINE_FIRST_COLUMN_DAYS == 365


def test_report_altman_z_is_not_applicable_to_a_bank():
    """The report's Z goes through the ONE implementation WITH the profile's sector, so a
    bank's balance sheet is never frozen into a paid report as "Distress"."""
    balance = [{"totalAssets": 3.9e12, "totalLiabilities": 3.6e12, "totalCurrentAssets": 1.0e12,
                "totalCurrentLiabilities": 2.5e12, "retainedEarnings": 3.4e11}]
    income = [{"revenue": 2.7e11, "operatingIncome": 7.0e10}]
    bank = {"sector": "Financial Services", "industry": "Banks - Diversified", "mktCap": 6.8e11}
    assert C._altman_z(balance, income, bank) is None
    vital = _build_health_vital(C._altman_z(balance, income, bank), 1.2, False)
    assert vital["altman_z_label"] == "Data unavailable"
    # The same statements under an industrial profile still score.
    assert C._altman_z(balance, income, {"sector": "Industrials", "mktCap": 6.8e11}) is not None
