"""Growth's cash-flow series (2026-11-14): operating cash flow, capital expenditure, and the
zero-filled FCF row as a GAP.

Owner decisions of 2026-11-14:
  3. "Yes, add capex" — CAPITAL EXPENDITURE and OPERATING CASH FLOW reach Ask Cay AI's
     financials tool, built from the cash-flow rows Growth already fetches (no new FMP call,
     no chart, no peer median). Capex is the cash SPENT, a positive amount (FMP files it
     negative); a positive filed value is a sign anomaly or netted disposals — no reading,
     logged (`[growth-capex-sign]`), never shown as spending.
  4. "Fix it too" — FMP zero-fills a cash-flow statement it does not have (HSBC's quarters,
     JPM's newest quarter). That 0 used to chart as FCF 0.00 with a -100% YoY; it is now no
     point at all, and the YoY whose base it was is None — never a comparison with an older row.
  5. "Hide, say why" — for a bank, insurer, lender or broker (`score_excluded_rows` holds
     "fcf_growth") operating cash flow is hidden with FCF and both are named in
     `series_not_meaningful`; capex stays (non-zero only).

Every case asserts the CORRECT DEGRADED behaviour (no point, a None YoY, an empty series, a
MISS), never a wrong number. Hermetic: the fakes of `test_growth_deepcheck_backend`.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List

import pytest

import test_growth_deepcheck_backend as gd
# The parity suite's AUTOUSE stub of every benchmark / moat lookup `assemble_report` reaches
# (four bindings of Supabase-backed singletons): imported, it applies to this module too, so the
# frozen-report test below stays hermetic. Harmless for the rest (their lookups are fakes).
from test_ticker_report_schema_parity import _no_live_benchmark_lookup  # noqa: F401
from app.integrations.fmp import FMPUnavailableException
from app.schemas.growth import GrowthResponse
from app.services import growth_service as gmod
from app.services.growth_service import _compute_growth_points
from app.utils.margins import (
    capital_expenditure_reading,
    free_cash_flow_reading,
    operating_cash_flow_reading,
)

BANK = "Banks - Diversified"
_NEW_SERIES = ("operating_cash_flow_annual", "operating_cash_flow_quarterly",
               "capital_expenditure_annual", "capital_expenditure_quarterly")
_CHARTED_LEVEL_KEYS = {
    "eps_annual", "eps_quarterly", "revenue_annual", "revenue_quarterly", "net_income_annual",
    "net_income_quarterly", "operating_profit_annual", "operating_profit_quarterly",
    "fcf_annual", "fcf_quarterly",
}


def _by(points: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {p["period"]: p for p in points}


# ── the readings ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [None, 0, 0.0, -0.0, float("nan"), float("inf"), float("-inf")])
def test_no_reading_for_a_blank_or_non_finite_cash_flow(raw):
    assert operating_cash_flow_reading(raw) is None
    assert capital_expenditure_reading(raw) is None
    assert free_cash_flow_reading(raw) is None


def test_operating_cash_flow_keeps_a_real_burn_and_huge_values():
    assert operating_cash_flow_reading(-147_782_000_000.0) == -147_782_000_000.0   # a burn is real
    assert operating_cash_flow_reading(136.2e9) == 136.2e9
    assert operating_cash_flow_reading(1e300) == 1e300


@pytest.mark.parametrize("filed,spent", [
    (-2_777_000_000.0, 2_777_000_000.0), (-1.0, 1.0), (-1e300, 1e300), (-5e-324, 5e-324),
])
def test_capex_is_the_cash_spent_a_positive_amount(filed, spent):
    out = capital_expenditure_reading(filed)
    assert out == spent and out > 0 and math.isfinite(out)


@pytest.mark.parametrize("filed", [1.0, 40.0, 3.2e9, 1e300])
def test_a_positive_filed_capex_is_never_spending(filed):
    """A sign anomaly or netted disposals: never flipped into spending, never a negative spend."""
    assert capital_expenditure_reading(filed) is None


# ── the series: zero-filled, positive and missing years ──────────────────────


def _cf(date: str, *, fy: str, period: str = "FY", **vals: Any) -> Dict[str, Any]:
    return {"date": date, "period": period, "fiscalYear": fy, **vals}


_ANNUAL_CF = [
    _cf("2019-12-31", fy="2019", operatingCashFlow=280.0, freeCashFlow=190.0),   # no capex key
    _cf("2020-12-31", fy="2020", capitalExpenditure=-100.0, operatingCashFlow=300.0,
        freeCashFlow=200.0),
    _cf("2021-12-31", fy="2021", capitalExpenditure=-120.0, operatingCashFlow=330.0,
        freeCashFlow=210.0),
    # FMP's zero-fill: the statement it does not have.
    _cf("2022-12-31", fy="2022", capitalExpenditure=0.0, operatingCashFlow=0.0, freeCashFlow=0.0),
    _cf("2023-12-31", fy="2023", capitalExpenditure=-150.0, operatingCashFlow=360.0,
        freeCashFlow=210.0),
    # A sign anomaly: filed POSITIVE.
    _cf("2024-12-31", fy="2024", capitalExpenditure=40.0, operatingCashFlow=400.0,
        freeCashFlow=440.0),
    _cf("2025-12-31", fy="2025", capitalExpenditure=-180.0, operatingCashFlow=-50.0,
        freeCashFlow=-230.0),
]


def test_annual_capex_skips_the_zero_fill_the_sign_anomaly_and_the_missing_key():
    pts = _by(_compute_growth_points(_ANNUAL_CF, "capitalExpenditure", is_quarterly=False,
                                     reading=capital_expenditure_reading))
    assert list(pts) == ["2020", "2021", "2023", "2025"]
    assert all(p["value"] > 0 for p in pts.values()), "capex is never a negative amount"
    assert pts["2020"]["yoy_change_percent"] is None      # base (2019) has no capex figure
    assert pts["2021"]["yoy_change_percent"] == 20.0      # 120 vs 100 spent
    assert pts["2023"]["yoy_change_percent"] is None      # base 2022 is a zero-fill — never vs 2021
    assert pts["2025"]["yoy_change_percent"] is None      # base 2024 is a sign anomaly


def test_the_zero_filled_fcf_and_ocf_year_is_a_gap_never_minus_100():
    """Decision 4: the zero-fill is no point, and the year after it has no YoY."""
    for key, reading in (("freeCashFlow", free_cash_flow_reading),
                         ("operatingCashFlow", operating_cash_flow_reading)):
        pts = _by(_compute_growth_points(_ANNUAL_CF, key, is_quarterly=False, reading=reading))
        assert "2022" not in pts, key
        assert pts["2023"]["yoy_change_percent"] is None, key
        assert all(p["yoy_change_percent"] != -100.0 for p in pts.values()), key
    fcf = _by(_compute_growth_points(_ANNUAL_CF, "freeCashFlow", is_quarterly=False,
                                     reading=free_cash_flow_reading))
    assert fcf["2024"]["yoy_change_percent"] == round((440 - 210) / 210 * 100, 2)
    # A swing into a burn keeps its (sign-corrected) value: real, never dropped.
    assert fcf["2025"]["value"] == -230.0 and fcf["2025"]["yoy_change_percent"] < 0
    ocf = _by(_compute_growth_points(_ANNUAL_CF, "operatingCashFlow", is_quarterly=False,
                                     reading=operating_cash_flow_reading))
    assert ocf["2025"]["value"] == -50.0


def test_without_a_reading_the_income_series_are_byte_identical():
    """`reading=None` keeps every value as filed — a 0 still charts (income series)."""
    rows = [_cf("2023-12-31", fy="2023", revenue=0.0), _cf("2024-12-31", fy="2024", revenue=50.0)]
    pts = _compute_growth_points(rows, "revenue", is_quarterly=False)
    assert [p["value"] for p in pts] == [0.0, 50.0]
    assert pts[1]["yoy_change_percent"] is None           # zero base: undefined, as before


def _q(date: str, period: str, fy: str, **vals: Any) -> Dict[str, Any]:
    return _cf(date, fy=fy, period=period, **vals)


def test_quarterly_capex_pairs_the_same_fiscal_quarter_never_the_adjacent_one():
    rows = [
        _q("2024-03-31", "Q1", "2024", capitalExpenditure=-100.0),
        _q("2024-12-31", "Q4", "2024", capitalExpenditure=-500.0),
        _q("2025-03-31", "Q1", "2025", capitalExpenditure=-120.0),
    ]
    pts = _compute_growth_points(rows, "capitalExpenditure", is_quarterly=True,
                                 reading=capital_expenditure_reading)
    by = {p["quarter"] + p["cal_year"]: p for p in pts}
    assert by["Q12025"]["yoy_change_percent"] == 20.0     # vs Q1 2024, never Q4 2024's 500


def test_a_zero_filled_year_ago_quarter_is_no_base_even_with_a_neighbour():
    rows = [
        _q("2024-06-30", "Q2", "2024", freeCashFlow=0.0, capitalExpenditure=0.0),
        _q("2024-09-30", "Q3", "2024", freeCashFlow=80.0, capitalExpenditure=-30.0),
        _q("2025-06-30", "Q2", "2025", freeCashFlow=90.0, capitalExpenditure=-35.0),
    ]
    for key, reading in (("freeCashFlow", free_cash_flow_reading),
                         ("capitalExpenditure", capital_expenditure_reading)):
        pts = _compute_growth_points(rows, key, is_quarterly=True, reading=reading)
        labels = [(p["quarter"], p["cal_year"]) for p in pts]
        assert ("Q2", "2024") not in labels, key          # the zero-fill is no point
        q2 = next(p for p in pts if p["quarter"] == "Q2")
        assert q2["yoy_change_percent"] is None, key      # never -100%, never vs Q3


def test_a_positive_year_ago_capex_quarter_is_no_base():
    """The base is read through the SAME reading: a positive filed capex (no reading) gives
    no YoY — never 35 spent compared with +30 filed."""
    rows = [
        _q("2024-06-30", "Q2", "2024", capitalExpenditure=30.0),
        _q("2025-06-30", "Q2", "2025", capitalExpenditure=-35.0),
    ]
    pts = _compute_growth_points(rows, "capitalExpenditure", is_quarterly=True,
                                 reading=capital_expenditure_reading)
    assert [(p["value"], p["yoy_change_percent"]) for p in pts] == [(35.0, None)]


@pytest.mark.parametrize("bad", ["N/A", float("nan"), True, False, None, "", [], {}])
def test_malformed_values_are_no_point(bad):
    rows = [_cf("2024-12-31", fy="2024", capitalExpenditure=-100.0, operatingCashFlow=10.0),
            _cf("2025-12-31", fy="2025", capitalExpenditure=bad, operatingCashFlow=bad)]
    for key, reading in (("capitalExpenditure", capital_expenditure_reading),
                         ("operatingCashFlow", operating_cash_flow_reading)):
        pts = _compute_growth_points(rows, key, is_quarterly=False, reading=reading)
        assert [p["period"] for p in pts] == ["2024"], (key, bad)
    missing = [_cf("2025-12-31", fy="2025")]
    assert _compute_growth_points(missing, "capitalExpenditure", is_quarterly=False,
                                  reading=capital_expenditure_reading) == []


def test_a_numeric_string_capex_parses():
    rows = [_cf("2025-12-31", fy="2025", capitalExpenditure="-3000000000")]
    pts = _compute_growth_points(rows, "capitalExpenditure", is_quarterly=False,
                                 reading=capital_expenditure_reading)
    assert pts[0]["value"] == 3_000_000_000.0


def test_a_near_zero_base_never_yields_an_infinite_yoy():
    rows = [_cf("2024-12-31", fy="2024", operatingCashFlow=1e-305),
            _cf("2025-12-31", fy="2025", operatingCashFlow=1e10)]
    pts = _compute_growth_points(rows, "operatingCashFlow", is_quarterly=False,
                                 reading=operating_cash_flow_reading)
    assert pts[-1]["value"] == 1e10 and pts[-1]["yoy_change_percent"] is None


# ── the service ───────────────────────────────────────────────────────────────


def _cash_rows(rows: List[Dict[str, Any]], **vals: Any) -> List[Dict[str, Any]]:
    return [{"date": r["date"], "period": r["period"], "fiscalYear": r["fiscalYear"], **vals}
            for r in rows]


_CASH = {"freeCashFlow": 5.0, "operatingCashFlow": 8.0, "capitalExpenditure": -3.0}


class _CountingFMP(gd._FakeFMP):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls: List[str] = []

    async def get_company_profile(self, ticker):
        self.calls.append("profile")
        return await super().get_company_profile(ticker)

    async def get_income_statement(self, ticker, period="annual", limit=5):
        self.calls.append(f"income:{period}")
        return await super().get_income_statement(ticker, period, limit)

    async def get_cash_flow_statement(self, ticker, period="annual", limit=5):
        self.calls.append(f"cash:{period}")
        return await super().get_cash_flow_statement(ticker, period, limit)


def _fmp(industry: str = "Semiconductors", sector: str = "Technology", **kw) -> _CountingFMP:
    kw.setdefault("cf_annual", _cash_rows(gd._ANNUAL, **_CASH))
    kw.setdefault("cf_quarterly", _cash_rows(gd._CAL_QUARTERS, **_CASH))
    return _CountingFMP(profile={"symbol": "TEST", "sector": sector, "industry": industry}, **kw)


async def _build(monkeypatch, fmp, lookup=None):
    svc = gd._service(monkeypatch, fmp, lookup or gd._yoy_metrics_lookup())
    return await svc._build_growth("TEST")


@pytest.mark.asyncio
async def test_a_build_makes_exactly_five_fmp_calls_and_carries_the_new_series(monkeypatch):
    fmp = _fmp()
    response, degraded = await _build(monkeypatch, fmp)
    assert degraded == []
    assert sorted(fmp.calls) == sorted(["profile", "income:annual", "income:quarter",
                                        "cash:annual", "cash:quarter"])
    assert [p.value for p in response.operating_cash_flow_annual] == [8.0, 8.0, 8.0]
    assert [p.value for p in response.capital_expenditure_annual] == [3.0, 3.0, 3.0]
    assert len(response.capital_expenditure_quarterly) == len(gd._CAL_QUARTERS)
    assert response.series_not_meaningful == []


@pytest.mark.asyncio
async def test_the_new_series_never_carry_a_peer_median_or_a_peer_level(monkeypatch):
    lookup = gd._yoy_metrics_lookup()        # cells for EVERY yoy metric, at every period
    response, _ = await _build(monkeypatch, _fmp(), lookup)
    for name in _NEW_SERIES:
        points = getattr(response, name)
        assert points, name
        assert all(p.sector_average_yoy is None and p.sector_average_qoq is None
                   for p in points), name
    assert set(response.peer_group_levels) <= _CHARTED_LEVEL_KEYS
    assert response.free_cash_flow_annual[0].sector_average_yoy == 7.0, "control: FCF keeps its line"
    asked = {m for _pt, metrics in lookup.calls for m in metrics}
    assert not any("cash_flow" in m or "capex" in m or "capital" in m for m in asked), asked


@pytest.mark.asyncio
async def test_a_bank_hides_fcf_and_operating_cash_flow_and_says_why(monkeypatch):
    cf_annual = _cash_rows(gd._ANNUAL, **_CASH)
    cf_annual[0]["capitalExpenditure"] = 0.0          # a zero-fill: no capex point
    bank, _ = await _build(monkeypatch, _fmp(BANK, "Financial Services", cf_annual=cf_annual))
    assert bank.free_cash_flow_annual == [] and bank.free_cash_flow_quarterly == []
    assert bank.operating_cash_flow_annual == [] and bank.operating_cash_flow_quarterly == []
    assert bank.series_not_meaningful == ["free_cash_flow", "operating_cash_flow"]
    assert [p.value for p in bank.capital_expenditure_annual] == [3.0, 3.0], "non-zero capex stays"
    assert bank.capital_expenditure_quarterly

    other, _ = await _build(monkeypatch, _fmp("Beverages - Non-Alcoholic", "Consumer Defensive"))
    assert other.series_not_meaningful == []
    assert other.operating_cash_flow_annual and other.free_cash_flow_annual


@pytest.mark.asyncio
async def test_a_banks_all_zero_capex_is_an_empty_series(monkeypatch):
    zero = {**_CASH, "capitalExpenditure": 0.0}
    bank, _ = await _build(monkeypatch, _fmp(
        BANK, "Financial Services", cf_annual=_cash_rows(gd._ANNUAL, **zero),
        cf_quarterly=_cash_rows(gd._CAL_QUARTERS, **zero)))
    assert bank.capital_expenditure_annual == [] and bank.capital_expenditure_quarterly == []


@pytest.mark.asyncio
async def test_a_failed_cash_flow_leg_empties_only_its_span(monkeypatch):
    fmp = _fmp(cf_annual=FMPUnavailableException("503"))
    response, degraded = await _build(monkeypatch, fmp)
    assert degraded == ["annual_cashflow"] and response.degraded == ["annual_cashflow"]
    for name in ("free_cash_flow_annual", "operating_cash_flow_annual",
                 "capital_expenditure_annual"):
        assert getattr(response, name) == [], name
    for name in ("free_cash_flow_quarterly", "operating_cash_flow_quarterly",
                 "capital_expenditure_quarterly", "revenue_annual", "eps_quarterly"):
        assert getattr(response, name), name
    assert response.series_not_meaningful == [], "an outage is never 'not meaningful'"


@pytest.mark.asyncio
async def test_positive_capex_rows_are_logged_once_per_span(monkeypatch, caplog):
    cf_q = _cash_rows(gd._CAL_QUARTERS, **_CASH)
    for row in cf_q[:5]:
        row["capitalExpenditure"] = 7.0               # five positive quarters
    cf_a = _cash_rows(gd._ANNUAL, **_CASH)
    with caplog.at_level(logging.WARNING, logger=gmod.__name__):
        response, _ = await _build(monkeypatch, _fmp(cf_quarterly=cf_q, cf_annual=cf_a))
    lines = [r.getMessage() for r in caplog.records if "[growth-capex-sign]" in r.getMessage()]
    assert len(lines) == 1, lines                     # the clean annual span logs nothing
    assert "ticker=TEST span=quarterly: 5 capitalExpenditure row(s) are positive" in lines[0]
    assert lines[0].count("20") == 4, "at most four dates are listed"
    assert [p.value for p in response.capital_expenditure_quarterly] == [3.0], "never shown"


@pytest.mark.asyncio
async def test_the_zero_filled_newest_fcf_year_is_a_gap_on_the_service(monkeypatch):
    cf_annual = _cash_rows(gd._ANNUAL, **_CASH)
    cf_annual[-1].update(freeCashFlow=0.0, operatingCashFlow=0.0, capitalExpenditure=0.0)
    response, _ = await _build(monkeypatch, _fmp(cf_annual=cf_annual))
    for name in ("free_cash_flow_annual", "operating_cash_flow_annual",
                 "capital_expenditure_annual"):
        periods = [p.period for p in getattr(response, name)]
        assert periods == ["2023", "2024"], (name, periods)
    assert all(p.yoy_change_percent != -100.0 for p in response.free_cash_flow_annual)


@pytest.mark.asyncio
async def test_the_growth_snapshot_never_scores_a_zero_filled_year(monkeypatch):
    """The snapshot reads the newest FCF point: the zero-filled year is no longer one, so the
    row is never a -100% FCF growth (v8 of the snapshot)."""
    cf_annual = _cash_rows(gd._ANNUAL, **_CASH)
    cf_annual[1]["freeCashFlow"] = 4.0
    cf_annual[-1]["freeCashFlow"] = 0.0
    response, _ = await _build(monkeypatch, _fmp(cf_annual=cf_annual))
    snap, _degraded = await gd._snapshot(monkeypatch, response)
    fcf = next(m for m in snap.metrics if m.metric_key == "fcf_growth")
    assert fcf.value == "-20.0%" and fcf.period_label == "FY2024"


# ── the 24h tier ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_v8_row_is_a_miss_and_a_v9_row_round_trips(monkeypatch):
    response, _ = await _build(monkeypatch, _fmp())
    svc = gmod.GrowthService.__new__(gmod.GrowthService)
    v8 = response.model_dump()
    for name in _NEW_SERIES + ("series_not_meaningful",):
        v8.pop(name)
    for version in (None, 8):
        stale = dict(v8) if version is None else {**v8, gmod._VERSION_KEY: version}
        svc.supabase = gd._FakeTable([gd._row(stale)])
        assert svc._check_supabase_cache("TEST") is None, version

    table = gd._FakeTable([])
    svc.supabase = table
    svc._upsert_supabase_cache_safe("TEST", response, "2099-01-01")
    stored = table.upserted[0]["response_json"]
    assert stored[gmod._VERSION_KEY] == gmod._GROWTH_PAYLOAD_VERSION == 9
    svc.supabase = gd._FakeTable([gd._row(stored)])
    back = svc._check_supabase_cache("TEST")
    assert isinstance(back, GrowthResponse) and back == response


# ── the report freezes only the charted series ──────────────────────────────


def test_the_frozen_report_chart_leaves_out_the_chat_only_series():
    from test_ticker_report_schema_parity import _make_collected_data

    from app.schemas.growth import GrowthDataPointSchema
    from app.schemas.ticker_report import TickerReportResponse
    from app.services.agents.narrative_prompts import stage_a_fallback
    from app.services.agents.ticker_report_data_collector import (
        GROWTH_CHAT_ONLY_SERIES,
        TickerReportDataCollector,
    )

    assert GROWTH_CHAT_ONLY_SERIES == frozenset(_NEW_SERIES)
    pt = GrowthDataPointSchema(period="2025", value=5.0, yoy_change_percent=1.0)
    out = _make_collected_data()
    out.growth_chart = GrowthResponse(
        symbol="AAPL", eps_annual=[pt], eps_quarterly=[], revenue_annual=[pt],
        revenue_quarterly=[], **{name: [pt] for name in _NEW_SERIES},
    )
    report = TickerReportDataCollector().assemble_report(out, stage_a_fallback())
    frozen = report["growth_chart"]
    assert not set(_NEW_SERIES) & set(frozen), sorted(frozen)
    assert frozen["revenue_annual"][0]["value"] == 5.0
    validated = TickerReportResponse.model_validate(report).growth_chart
    assert validated is not None and validated.capital_expenditure_annual == []


def test_a_narrowed_build_with_only_chat_only_series_left_is_dropped():
    from app.schemas.growth import GrowthDataPointSchema
    from app.services.agents import ticker_report_data_collector as C

    for leg in ("annual_cashflow", "quarterly_cashflow"):
        for name in C._GROWTH_LEG_SERIES[leg]:
            assert name.endswith(leg.split("_")[0]), (leg, name)
    assert set(_NEW_SERIES) <= set(C._GROWTH_SERIES)
    pt = GrowthDataPointSchema(period="2025", value=5.0)
    only_capex = GrowthResponse(
        symbol="BAC", eps_annual=[], eps_quarterly=[], revenue_annual=[], revenue_quarterly=[],
        capital_expenditure_annual=[pt], capital_expenditure_quarterly=[pt],
    )
    assert C._narrow_growth_chart(only_capex, ["annual_income", "quarterly_income"]) is None
    # Control: a charted series left keeps the build, and the failed leg's series are emptied.
    with_rev = only_capex.model_copy(update={"revenue_annual": [pt]})
    narrowed = C._narrow_growth_chart(with_rev, ["annual_cashflow"])
    assert narrowed is not None and narrowed.capital_expenditure_annual == []
    assert narrowed.capital_expenditure_quarterly == [pt]


# ── chat's numeric grounding names capex as a metric ─────────────────────────


@pytest.mark.parametrize("text,hit", [
    ("Capex rose to $64.6B last year.", True),
    ("Capital expenditures were $2.8B.", True),
    ("capital expenditure of 3.1B", True),
    ("Its capital spending doubled.", True),
    ("The capexes", False),                 # word-bounded, never a prefix match
    ("The capital city has 3 parks.", False),
])
def test_the_grounding_metric_pattern_names_capex(text, hit):
    from app.services.chat_numeric_grounding import _METRIC_RE

    assert bool(_METRIC_RE.search(text)) is hit



# ── The iOS route never ships the chat-only series (2026-11-14) ─────────────────


def test_the_growth_route_leaves_the_chat_only_series_out():
    from app.api.v1.endpoints import stocks
    from app.schemas.growth import GROWTH_CHAT_ONLY_SERIES, GrowthResponse
    from app.services.agents import ticker_report_data_collector as collector

    # One source for the set, and every name is a real GrowthResponse field.
    assert collector.GROWTH_CHAT_ONLY_SERIES is GROWTH_CHAT_ONLY_SERIES
    assert GROWTH_CHAT_ONLY_SERIES <= set(GrowthResponse.model_fields)
    routes = [r for r in stocks.router.routes if getattr(r, "path", "").endswith("/{ticker}/growth")]
    assert len(routes) == 1
    assert set(routes[0].response_model_exclude or ()) == set(GROWTH_CHAT_ONLY_SERIES)
    # What that exclusion serialises: the charted series and the bank note stay.
    sample = GrowthResponse(symbol="ORCL", eps_annual=[], eps_quarterly=[], revenue_annual=[],
                            revenue_quarterly=[], series_not_meaningful=["free_cash_flow"])
    dumped = sample.model_dump(mode="json", exclude=set(GROWTH_CHAT_ONLY_SERIES))
    assert not GROWTH_CHAT_ONLY_SERIES & set(dumped)
    assert "free_cash_flow_annual" in dumped and dumped["series_not_meaningful"] == ["free_cash_flow"]



def test_a_cached_collection_never_stores_the_chat_only_series():
    """`ticker_data_cache._serialize` leaves the chat-only series out of the stored collection
    (2026-11-14): nothing reads them back, and they were ~25 KB a row."""
    import dataclasses
    from app.schemas.growth import GROWTH_CHAT_ONLY_SERIES, GrowthDataPointSchema, GrowthResponse
    from app.services import ticker_data_cache as tdc
    from app.services.agents.ticker_report_data_collector import CollectedTickerData

    point = GrowthDataPointSchema(period="2025", value=1.0e9, yoy_change_percent=5.0)
    chart = GrowthResponse(symbol="ORCL", eps_annual=[], eps_quarterly=[], revenue_annual=[point],
                           revenue_quarterly=[], free_cash_flow_annual=[point],
                           capital_expenditure_annual=[point], operating_cash_flow_annual=[point])
    fields = {f.name for f in dataclasses.fields(CollectedTickerData)}
    assert "growth_chart" in fields and "growth_chart" in tdc._PYDANTIC_FIELDS
    out = CollectedTickerData.__new__(CollectedTickerData)
    for f in dataclasses.fields(CollectedTickerData):
        setattr(out, f.name, None)
    out.growth_chart = chart
    row = tdc._serialize(out)
    assert row is not None
    stored = row["growth_chart"]
    assert not GROWTH_CHAT_ONLY_SERIES & set(stored)
    assert stored["free_cash_flow_annual"] and stored["revenue_annual"]
    # It still reads back as a GrowthResponse (the chat-only lists default to []).
    back = GrowthResponse.model_validate(stored)
    assert back.capital_expenditure_annual == [] and back.free_cash_flow_annual
