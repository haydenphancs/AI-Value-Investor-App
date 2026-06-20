"""
Outlier / strange-input tests for the Fundamentals & Growth tap-to-expand
history (computed in ticker_report_data_collector). These pin the behaviour
the edge-case audit surfaced: quarterly-period collisions, sparse/unsorted
data, restatements, malformed rows, reconstruction blow-ups, and partial
failures must never silently corrupt or wipe the charted series.

Pure math — no network, no Supabase. Build inputs inline.
"""

import pytest

from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    TickerReportDataCollector,
    _aligned_sector_series,
    _build_fundamental_metrics_from_snapshots,
    _build_fundamentals_history,
    _fundamentals_history_for_period,
    _history_period_id,
    _hist_ev_ebitda,
    _hist_pfcf,
    _parse_history_label,
    _sector_period_map,
)
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.schemas.ticker_report import DeepDiveMetricCardResponse


# ── helpers ────────────────────────────────────────────────────────────

def _series(hist, key):
    """[(period, value), …] for a metric key, or [] if absent."""
    return [(p["period"], p["value"]) for p in hist.get(key, [])]


def _periods(hist, key):
    return [p["period"] for p in hist.get(key, [])]


# ── _history_period_id (the join-key / label primitive) ────────────────

def test_period_id_non_dict_row_returns_none():
    assert _history_period_id(None, quarterly=False) is None
    assert _history_period_id("ERR", quarterly=True) is None
    assert _history_period_id(123, quarterly=False) is None
    assert _history_period_id(["x"], quarterly=True) is None


def test_period_id_int_year_normalizes_to_string_label():
    key, label, year, q = _history_period_id(
        {"calendarYear": 2024, "date": "2024-09-30"}, quarterly=False)
    assert key == "2024" and label == "2024" and year == 2024 and q is None


def test_period_id_quarter_derived_from_date_when_period_missing():
    # No 'period' field → quarter must come from the date month (Jun → Q2).
    key, label, year, q = _history_period_id(
        {"calendarYear": 2024, "date": "2024-06-30"}, quarterly=True)
    assert q == 2 and key == "2024-Q2" and label == "Q2 '24"


# ── Quarterly collisions (THE headline bug) ────────────────────────────

def test_quarterly_period_collision_keeps_all_four_quarters():
    """Two of the four quarters lack the 'period' field. They must NOT
    collapse onto the same key — all four quarters survive, distinct."""
    income = [
        {"calendarYear": 2024, "period": "Q1", "date": "2024-03-31", "revenue": 100},
        {"calendarYear": 2024, "date": "2024-06-30", "revenue": 110},  # Q2 from date
        {"calendarYear": 2024, "period": "Q3", "date": "2024-09-30", "revenue": 120},
        {"calendarYear": 2024, "date": "2024-12-31", "revenue": 130},  # Q4 from date
    ]
    ratios = [
        {"calendarYear": 2024, "period": p, "date": d, "priceToEarningsRatio": pe}
        for p, d, pe in [
            ("Q1", "2024-03-31", 10.0), ("", "2024-06-30", 11.0),
            ("Q3", "2024-09-30", 12.0), ("", "2024-12-31", 13.0),
        ]
    ]
    hist = _fundamentals_history_for_period(income, [], [], [], ratios, {}, quarterly=True)
    assert _periods(hist, "pe") == ["Q1 '24", "Q2 '24", "Q3 '24", "Q4 '24"]
    assert [v for _, v in _series(hist, "pe")] == [10.0, 11.0, 12.0, 13.0]


def test_quarterly_same_quarter_distinct_across_years():
    income = [
        {"calendarYear": 2022, "period": "Q1", "date": "2022-03-31", "revenue": 80},
        {"calendarYear": 2023, "period": "Q1", "date": "2023-03-31", "revenue": 90},
        {"calendarYear": 2024, "period": "Q1", "date": "2024-03-31", "revenue": 100},
    ]
    ratios = [dict(r, priceToEarningsRatio=float(i)) for i, r in enumerate(income)]
    hist = _fundamentals_history_for_period(income, [], [], [], ratios, {}, quarterly=True)
    assert _periods(hist, "pe") == ["Q1 '22", "Q1 '23", "Q1 '24"]


def test_quarterly_yoy_pairs_same_quarter_prior_year():
    """Dense 8 quarters → YoY pairs every quarter with the SAME quarter a
    year earlier (not the immediately-preceding quarter)."""
    income = []
    for y in (2023, 2024):
        for q, m in ((1, "03"), (2, "06"), (3, "09"), (4, "12")):
            income.append({"calendarYear": y, "period": f"Q{q}",
                           "date": f"{y}-{m}-28", "revenue": 100 + (y - 2023) * 40 + q})
    hist = _fundamentals_history_for_period(income, [], [], [], [], {}, quarterly=True)
    rg = _series(hist, "revenue_growth")
    # 4 YoY points (the 2024 quarters); each compares to the same 2023 quarter.
    assert [p for p, _ in rg] == ["Q1 '24", "Q2 '24", "Q3 '24", "Q4 '24"]
    # Q1'24 rev=141 vs Q1'23 rev=101 → +39.6%
    assert rg[0][1] == 39.6


def test_quarterly_missing_intermediate_quarter_no_spurious_yoy():
    """Q2-2024 missing: Q2-2025 must NOT YoY against Q1-2024 (the old
    positional-gap bug). With no Q2-2024 prior, Q2-2025 has no growth point."""
    income = [
        {"calendarYear": 2024, "period": "Q1", "date": "2024-03-31", "revenue": 100},
        {"calendarYear": 2024, "period": "Q3", "date": "2024-09-30", "revenue": 120},
        {"calendarYear": 2024, "period": "Q4", "date": "2024-12-31", "revenue": 130},
        {"calendarYear": 2025, "period": "Q1", "date": "2025-03-31", "revenue": 150},
        {"calendarYear": 2025, "period": "Q2", "date": "2025-06-30", "revenue": 160},
    ]
    hist = _fundamentals_history_for_period(income, [], [], [], [], {}, quarterly=True)
    rg = dict(_series(hist, "revenue_growth"))
    # Q1'25 pairs with Q1'24 (present) → 50%. Q2'25 has no Q2'24 → absent.
    assert rg.get("Q1 '25") == 50.0
    assert "Q2 '25" not in rg


# ── Ordering / sparsity (annual) ───────────────────────────────────────

def test_unsorted_annual_input_emits_oldest_first():
    income = [
        {"calendarYear": "2023", "date": "2023-09-30", "revenue": 380},
        {"calendarYear": "2024", "date": "2024-09-30", "revenue": 400},
        {"calendarYear": "2022", "date": "2022-09-30", "revenue": 350},
    ]
    hist = _fundamentals_history_for_period(income, [], [], [], [], {}, quarterly=False)
    assert _series(hist, "revenue_growth") == [("2023", 8.6), ("2024", 5.3)]


def test_annual_multi_year_gap_no_spurious_growth():
    """2024 and 2020 only (a 4-year hole). 2024 must NOT show growth vs 2020."""
    income = [
        {"calendarYear": "2024", "date": "2024-09-30", "revenue": 400},
        {"calendarYear": "2020", "date": "2020-09-30", "revenue": 300},
    ]
    hist = _fundamentals_history_for_period(income, [], [], [], [], {}, quarterly=False)
    assert "revenue_growth" not in hist  # no adjacent prior year → dropped


def test_single_period_has_absolutes_no_growth():
    income = [{"calendarYear": "2024", "date": "2024-09-30", "revenue": 400}]
    ratios = [{"calendarYear": "2024", "date": "2024-09-30", "priceToEarningsRatio": 35.8}]
    hist = _fundamentals_history_for_period(income, [], [], [], ratios, {}, quarterly=False)
    assert _series(hist, "pe") == [("2024", 35.8)]
    assert "revenue_growth" not in hist and "eps_growth" not in hist


# ── Restatements / duplicates ──────────────────────────────────────────

def test_duplicate_period_keeps_first_under_newest_first():
    """FMP returns newest-first; the first (newest) filing for a period wins
    deterministically over a stale restatement appearing later in the list."""
    ratios = [
        {"calendarYear": "2024", "date": "2024-11-15", "priceToEarningsRatio": 36.0},  # newest
        {"calendarYear": "2024", "date": "2024-09-30", "priceToEarningsRatio": 35.8},  # stale dup
    ]
    hist = _fundamentals_history_for_period([], [], [], [], ratios, {}, quarterly=False)
    assert _series(hist, "pe") == [("2024", 36.0)]


# ── Malformed rows ─────────────────────────────────────────────────────

def test_malformed_non_dict_rows_are_skipped_not_fatal():
    income = [
        None, "ERR", 42,
        {"calendarYear": "2024", "date": "2024-09-30", "revenue": 400},
        {"calendarYear": "2023", "date": "2023-09-30", "revenue": 380},
    ]
    hist = _fundamentals_history_for_period(income, [], [], [], [], {}, quarterly=False)
    assert _series(hist, "revenue_growth") == [("2024", 5.3)]


def test_all_malformed_input_yields_empty_dict():
    hist = _fundamentals_history_for_period([None, "x"], [], [], [], [], {}, quarterly=False)
    assert hist == {}


# ── Cross-array join failures ──────────────────────────────────────────

def test_missing_key_metrics_drops_roe_roa_keeps_ratios():
    income = [{"calendarYear": "2024", "date": "2024-09-30", "revenue": 400}]
    ratios = [{"calendarYear": "2024", "date": "2024-09-30",
               "priceToEarningsRatio": 35.8, "netProfitMargin": 0.25}]
    hist = _fundamentals_history_for_period(income, [], [], [], ratios, {}, quarterly=False)
    assert "pe" in hist and "net_margin" in hist
    assert "roe" not in hist and "roa" not in hist


# ── Reconstruction blow-ups (the outlier clamps) ───────────────────────

def test_pfcf_near_zero_fcf_returns_none():
    assert _hist_pfcf({"marketCap": 100e9}, {"freeCashFlow": 0.01}) is None
    # A sane multiple still passes through.
    assert _hist_pfcf({"marketCap": 1000.0}, {"freeCashFlow": 40.0}) == 25.0


def test_pfcf_non_positive_fcf_returns_none():
    assert _hist_pfcf({"marketCap": 1e9}, {"freeCashFlow": -5e6}) is None
    assert _hist_pfcf({"marketCap": 1e9}, {"freeCashFlow": 0}) is None


def test_ev_ebitda_negative_operating_income_fallback_returns_none():
    # ebitda not directly available; op income is a loss → must NOT fabricate.
    assert _hist_ev_ebitda(
        {"enterpriseValue": 500e9},
        {"depreciationAndAmortization": 100e6},
        {"ebitda": 0, "operatingIncome": -50e6},
    ) is None


def test_ev_ebitda_direct_and_positive_fallback():
    # Direct ebitda used.
    assert round(_hist_ev_ebitda(
        {"enterpriseValue": 3200}, {"depreciationAndAmortization": 20},
        {"ebitda": 140, "operatingIncome": 120}), 2) == 22.86
    # Fallback with POSITIVE op income: ebitda = 80 + 20 = 100 → 300/100 = 3.0
    assert _hist_ev_ebitda(
        {"enterpriseValue": 300}, {"depreciationAndAmortization": 20},
        {"ebitda": 0, "operatingIncome": 80}) == 3.0


def test_ev_ebitda_artifact_grade_multiple_clamped():
    # Tiny EBITDA → astronomical multiple → dropped.
    assert _hist_ev_ebitda(
        {"enterpriseValue": 100e9}, {}, {"ebitda": 1.0}) is None


# ── Truthful pass-through for direct ratios ────────────────────────────

def test_direct_ratios_pass_through_extremes_and_negatives():
    ratios = [
        {"calendarYear": "2024", "date": "2024-09-30",
         "priceToEarningsRatio": 9999.99, "netProfitMargin": -1.50,
         "interestCoverageRatio": -2.5, "debtToEquityRatio": -0.8},
        {"calendarYear": "2023", "date": "2023-09-30",
         "priceToEarningsRatio": 15.0, "netProfitMargin": 0.10},
    ]
    hist = _fundamentals_history_for_period([], [], [], [], ratios, {}, quarterly=False)
    assert dict(_series(hist, "pe"))["2024"] == 9999.99      # outlier P/E truthful
    assert dict(_series(hist, "net_margin"))["2024"] == -150.0  # fraction → %
    assert dict(_series(hist, "interest_coverage"))["2024"] == -2.5  # real distress
    assert dict(_series(hist, "debt_to_equity"))["2024"] == -0.8


def test_margins_fraction_to_percent_conversion():
    ratios = [{"calendarYear": "2024", "date": "2024-09-30",
               "grossProfitMargin": 0.4691, "operatingProfitMargin": 0.3197,
               "earningsYield": 0.0279}]
    hist = _fundamentals_history_for_period([], [], [], [], ratios, {}, quarterly=False)
    assert dict(_series(hist, "gross_margin"))["2024"] == 46.9
    assert dict(_series(hist, "operating_margin"))["2024"] == 32.0
    assert dict(_series(hist, "earnings_yield"))["2024"] == 2.8


def test_negative_fcf_growth_passes_through():
    cash_flow = [
        {"calendarYear": "2024", "date": "2024-09-30", "freeCashFlow": 70},
        {"calendarYear": "2023", "date": "2023-09-30", "freeCashFlow": 100},
    ]
    income = [
        {"calendarYear": "2024", "date": "2024-09-30", "revenue": 1},
        {"calendarYear": "2023", "date": "2023-09-30", "revenue": 1},
    ]
    hist = _fundamentals_history_for_period(income, [], cash_flow, [], [], {}, quarterly=False)
    assert dict(_series(hist, "fcf_growth"))["2024"] == -30.0


# ── _build_fundamentals_history: granularity isolation + merge ─────────

def _valid_annual(out):
    out.profile = {"industry": "X"}
    out.income = [
        {"calendarYear": "2024", "date": "2024-09-30", "revenue": 400, "epsDiluted": 6.0},
        {"calendarYear": "2023", "date": "2023-09-30", "revenue": 380, "epsDiluted": 5.5},
    ]
    out.ratios = [
        {"calendarYear": "2024", "date": "2024-09-30", "priceToEarningsRatio": 35.8},
        {"calendarYear": "2023", "date": "2023-09-30", "priceToEarningsRatio": 30.1},
    ]


def test_build_history_quarterly_failure_does_not_kill_annual():
    """A broken quarterly source (non-iterable) must NOT wipe the valid
    annual history — the two granularities are guarded independently."""
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    _valid_annual(out)
    out.income_q = 12345  # non-iterable → quarterly build raises → caught
    hist = _build_fundamentals_history(out)
    assert "pe" in hist
    assert [p["period"] for p in hist["pe"]["annual"]] == ["2023", "2024"]
    assert hist["pe"]["quarterly"] == []  # quarterly degraded, annual intact


def test_build_history_drops_keys_with_no_values():
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    _valid_annual(out)  # ratios has only P/E → margins etc. have no datapoints
    hist = _build_fundamentals_history(out)
    assert "pe" in hist
    assert "altman_z" not in hist  # no balance sheet provided
    assert "roe" not in hist       # no key_metrics provided


# ── Sector-average overlay ─────────────────────────────────────────────

def test_parse_history_label_forms():
    assert _parse_history_label("2024") == (2024, None)
    assert _parse_history_label("Q1 '24") == (2024, 1)   # our company label
    assert _parse_history_label("Q1'24") == (2024, 1)    # sector table label
    assert _parse_history_label("garbage") is None
    assert _parse_history_label("") is None


def test_sector_period_map_parses_and_drops_bad():
    m = _sector_period_map({"2024": 0.38, "2023": None, "bad": 1.0, "2022": 0.36})
    assert m == {(2024, None): 0.38, (2022, None): 0.36}


def test_aligned_sector_series_percent_x100_and_alignment():
    company = [{"period": "2022", "value": 42.0}, {"period": "2023", "value": 44.0},
               {"period": "2024", "value": 46.0}]
    sector_map = {(2022, None): 0.36, (2024, None): 0.38}  # 2023 missing
    series, has = _aligned_sector_series(company, sector_map, to_percent=True)
    assert has is True
    assert series == [
        {"period": "2022", "value": 36.0},   # ×100
        {"period": "2023", "value": None},    # gap preserved, aligned to company
        {"period": "2024", "value": 38.0},
    ]


def test_aligned_sector_series_plain_for_x_metrics():
    company = [{"period": "2023", "value": 30.1}, {"period": "2024", "value": 35.8}]
    sector_map = {(2023, None): 21.0, (2024, None): 22.0}
    series, has = _aligned_sector_series(company, sector_map, to_percent=False)
    assert has and [p["value"] for p in series] == [21.0, 22.0]  # no ×100


def _out_with_sector(annual_bench, quarterly_bench=None):
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.profile = {"industry": "X"}
    out.income = [
        {"calendarYear": "2024", "date": "2024-09-30", "revenue": 400},
        {"calendarYear": "2023", "date": "2023-09-30", "revenue": 380},
        {"calendarYear": "2022", "date": "2022-09-30", "revenue": 350},
    ]
    out.ratios = [
        {"calendarYear": "2024", "date": "2024-09-30", "grossProfitMargin": 0.46, "priceToEarningsRatio": 35.8},
        {"calendarYear": "2023", "date": "2023-09-30", "grossProfitMargin": 0.44, "priceToEarningsRatio": 30.1},
        {"calendarYear": "2022", "date": "2022-09-30", "grossProfitMargin": 0.42, "priceToEarningsRatio": 28.0},
    ]
    out.sector_benchmark_history = {
        "annual": annual_bench or {},
        "quarterly": quarterly_bench or {},
    }
    return out


def test_build_history_attaches_sector_series_with_units():
    out = _out_with_sector({
        "gross_margin": {"2024": 0.38, "2023": 0.37, "2022": 0.36},  # fractions
        "pe_ratio": {"2024": 22.0, "2023": 21.0, "2022": 20.0},       # plain
    })
    hist = _build_fundamentals_history(out)
    # percent metric → sector ×100, aligned to company labels
    assert [p["value"] for p in hist["gross_margin"]["sector_annual"]] == [36.0, 37.0, 38.0]
    # x metric → mapped via pe→pe_ratio, no conversion
    assert [p["value"] for p in hist["pe"]["sector_annual"]] == [20.0, 21.0, 22.0]


def test_build_history_no_sector_when_benchmark_missing():
    out = _out_with_sector({})  # empty benchmark dict
    hist = _build_fundamentals_history(out)
    assert "sector_annual" not in hist["gross_margin"]
    assert "sector_annual" not in hist["pe"]


def test_build_history_sector_partial_years_kept_if_any_value():
    out = _out_with_sector({"gross_margin": {"2024": 0.38}})  # only one year
    hist = _build_fundamentals_history(out)
    sa = hist["gross_margin"]["sector_annual"]
    assert [p["value"] for p in sa] == [None, None, 38.0]  # aligned, sparse


def test_build_history_non_star_metric_never_gets_sector():
    # revenue_growth (no "*") must NOT get a sector series even if a (bogus)
    # benchmark dict carried one under that name.
    out = _out_with_sector({"revenue_growth": {"2024": 5.0, "2023": 4.0}})
    out.cash_flow = []
    hist = _build_fundamentals_history(out)
    assert "revenue_growth" in hist            # company growth exists
    assert "sector_annual" not in hist["revenue_growth"]


def test_build_history_sector_quarterly_best_effort():
    out = _out_with_sector(
        {"gross_margin": {"2024": 0.38}},
        quarterly_bench={"gross_margin": {"Q1'24": 0.37}},
    )
    # add quarterly company data so the quarterly series exists
    out.ratios_q = [
        {"calendarYear": "2024", "period": "Q1", "date": "2024-03-31", "grossProfitMargin": 0.45},
        {"calendarYear": "2023", "period": "Q1", "date": "2023-03-31", "grossProfitMargin": 0.43},
    ]
    hist = _build_fundamentals_history(out)
    sq = hist["gross_margin"].get("sector_quarterly", [])
    # sector "Q1'24" aligns to company "Q1 '24" → 0.37×100 = 37.0
    assert any(p["value"] == 37.0 for p in sq)


def test_build_history_sector_period_outside_company_ignored():
    # Sector has 2019 (which the company doesn't) → no floating endpoint.
    out = _out_with_sector({"gross_margin": {"2024": 0.38, "2019": 0.30}})
    hist = _build_fundamentals_history(out)
    periods = [p["period"] for p in hist["gross_margin"]["sector_annual"]]
    assert "2019" not in periods
    assert periods == ["2022", "2023", "2024"]  # company periods only


def test_build_history_tolerates_bad_sector_history_attr():
    # A legacy / corrupt value (not a dict) must not crash — just no sector line.
    for bad in (None, "oops", 123, []):
        out = _out_with_sector({})
        out.sector_benchmark_history = bad  # type: ignore[assignment]
        hist = _build_fundamentals_history(out)
        assert "sector_annual" not in hist["gross_margin"]
        assert "pe" in hist  # company series unaffected


def test_sector_delta_guard_no_ratio_when_nonpositive():
    # Direct sanity on the alignment when sector value is negative (e.g. neg D/E)
    company = [{"period": "2023", "value": -0.5}, {"period": "2024", "value": -0.8}]
    sector_map = {(2024, None): -0.3}
    series, has = _aligned_sector_series(company, sector_map, to_percent=False)
    assert has and series[-1]["value"] == -0.3  # truthful pass-through


@pytest.mark.asyncio
async def test_fetch_sector_benchmark_history_normalizes_sector(monkeypatch):
    """The collector must look up benchmarks under the CANONICAL sector name
    (same as the snapshot cards), not the raw FMP name — else the sector line
    is silently empty for e.g. 'Information Technology' / 'Financials'."""
    captured: list = []

    class _FakeLookup:
        def get_sector_benchmarks(self, sector, metrics, period_type):
            captured.append(sector)
            return {m: {} for m in metrics}

    monkeypatch.setattr(
        "app.services.sector_benchmark_lookup.get_sector_benchmark_lookup",
        lambda: _FakeLookup(),
    )
    coll = TickerReportDataCollector()
    # FMP GICS-style raw name → must be queried as canonical "Technology".
    await coll._fetch_sector_benchmark_history("Information Technology")
    assert captured and all(s == "Technology" for s in captured), captured


@pytest.mark.asyncio
async def test_fetch_sector_benchmark_history_empty_sector_short_circuits(monkeypatch):
    called = {"n": 0}

    class _FakeLookup:
        def get_sector_benchmarks(self, *a, **k):
            called["n"] += 1
            return {}

    monkeypatch.setattr(
        "app.services.sector_benchmark_lookup.get_sector_benchmark_lookup",
        lambda: _FakeLookup(),
    )
    coll = TickerReportDataCollector()
    out = await coll._fetch_sector_benchmark_history("")
    assert out == {"annual": {}, "quarterly": {}}
    assert called["n"] == 0  # no query for a missing sector


def test_sector_series_survives_card_validation():
    out = _out_with_sector({"gross_margin": {"2024": 0.38, "2023": 0.37, "2022": 0.36}})
    hist = _build_fundamentals_history(out)
    prof = SnapshotItemResponse(
        category="Profitability", rating=4,
        metrics=[SnapshotMetricResponse(name="Gross Margin (1.2x sector avg 38%)", value="46.0%")],
        full_report_available=True,
    )
    card_dict = _build_fundamental_metrics_from_snapshots(
        prof, None, None, None, history_lookup=hist)[0]
    card = DeepDiveMetricCardResponse.model_validate(card_dict)
    m = card.metrics[0]
    assert m.sector_annual_history is not None
    assert [p.value for p in m.sector_annual_history] == [36.0, 37.0, 38.0]
