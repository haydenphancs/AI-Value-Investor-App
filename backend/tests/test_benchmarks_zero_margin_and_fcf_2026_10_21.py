"""Two zero-artifact rules of 2026-10-21.

1. A MARGIN median of exactly 0 is never served (`sector_benchmark_lookup.
   servable_benchmark_rows`). The fiscal rows the 2026-10-04 run wrote counted FMP's "0 when
   there is no revenue" as a figure: 1,113 annual / calendar-quarter margin medians of exactly
   0.0 (Healthcare's SECTOR net and operating margin read 0.0 for every year 2016-2025, n ~440;
   Biotechnology's 2025 net margin 0.0 against a revenue-backed -74.8%). Only the Profitability
   card dropped them (`_usable_median`); the Profit Power line and tooltip, the report
   drill-down, Stage B ("above sector 0.0%") and Cay AI's grounding ("Sector peer-group median
   net margin 0.0%") printed them for ~446 companies (LLY, JNJ, UNH, BHP, IONS …). Verified
   read-only on production, 2026-10-20.
2. A free cash flow of exactly 0 is no reading (`app.utils.margins.free_cash_flow_reading`):
   FMP zero-fills a cash-flow statement it does not have (HSBC's every quarter, JPM's unfiled
   newest quarter), which set 77 calendar-quarter FCF-margin medians to exactly 0 on the
   2026-10-21 recompute and drew a flat 0.0% FCF line for the company.

Every lookup here is the REAL `SectorBenchmarkLookup` over a PostgREST fake, so the serving
gate runs exactly as in production (`_RowLookup`-style doubles override `_fetch_rows` and
would skip it).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import test_growth_deepcheck_backend as gd
import test_non_lender_members_peer_free_2026_10_09 as nl
import test_report_moat_index_benchmarks_2026_10_07 as rmi
from app.services import profit_power_service as pp
from app.services import sector_benchmark_lookup as sbl
from app.services.chat_service import ChatService
from app.utils.margins import MARGIN_METRICS, is_zero_median

_NOW = datetime.now(timezone.utc)
_COMPUTED = (_NOW - timedelta(days=17)).isoformat()   # a fiscal run, well past 75 days
_FRESH = (_NOW - timedelta(days=2)).isoformat()       # this week's TTM run
_YEARS = [str(y) for y in range(_NOW.year - 6, _NOW.year - 1)]   # complete years
_DONE = _YEARS[-1]
_HC = "Healthcare"
_FS = "Financial Services"


@pytest.fixture(autouse=True)
def _clean_cache():
    sbl._cache.clear()
    yield
    sbl._cache.clear()


def _row(sector, industry, metric, period_type, label, value, n, computed=_COMPUTED):
    return rmi._row(sector, industry, metric, period_type, label, value, n, computed)


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. The zero-margin gate
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("value", [0, 0.0, -0.0, "0", "0.000000"])
@pytest.mark.parametrize("metric", sorted(MARGIN_METRICS))
@pytest.mark.parametrize("period_type,label", [
    ("annual", "2023"), ("calendar_quarter", "Q2'24"), ("ttm", "TTM"),
])
def test_an_exact_zero_margin_median_is_never_served(metric, value, period_type, label):
    computed = _FRESH if period_type == "ttm" else _COMPUTED
    kept, dropped = sbl.servable_benchmark_rows(
        [_row(_HC, "", metric, period_type, label, value, 443, computed)], period_type,
    )
    assert kept == [] and dropped == {"zero_margin": 1}


@pytest.mark.parametrize("metric,value", [
    ("net_margin", -0.7475),        # Biotechnology's real (revenue-backed) median
    ("operating_margin", 0.0001),   # a real near-zero median stays
    ("gross_margin", 0.855),
    ("dividend_yield", 0.0),        # not a margin: a 0.0 yield median is real
    ("pe_ratio", 0.0),
    ("roe", 0.0),
])
def test_everything_else_is_kept(metric, value):
    kept, dropped = sbl.servable_benchmark_rows(
        [_row(_HC, "", metric, "annual", "2023", value, 200)], "annual",
    )
    assert len(kept) == 1 and dropped == {}


def test_a_row_without_a_metric_name_is_untouched():
    """`index_service` passes pe rows that select no metric_name."""
    kept, dropped = sbl.servable_benchmark_rows(
        [{"period_label": "TTM", "median_value": 0.0, "sample_size": 300,
          "computed_at": _FRESH}], "ttm",
    )
    assert len(kept) == 1 and dropped == {}


@pytest.mark.parametrize("value,expected", [
    (0, True), (0.0, True), (-0.0, True), ("0.000000", True), ("-0", True),
    (1e-9, False), (-0.2, False), (None, False), ("n/a", False), (float("nan"), False),
    (False, False), (True, False), ([], False),
])
def test_is_zero_median(value, expected):
    assert is_zero_median(value) is expected


def _healthcare_rows(industry="Drug Manufacturers - General", industry_n=17):
    """The 2026-10-04 production shape: sector net margin 0.0 every year (n ~440), gross
    margin a real 57.5%; a thin industry (17 companies) whose companies get the SECTOR line."""
    rows = []
    for y in _YEARS:
        rows += [
            _row(_HC, "", "net_margin", "annual", y, 0.0, 440),
            _row(_HC, "", "operating_margin", "annual", y, 0.0, 440),
            _row(_HC, "", "gross_margin", "annual", y, 0.575, 440),
            _row(_HC, industry, "net_margin", "annual", y, 0.21, industry_n),
            _row(_HC, industry, "gross_margin", "annual", y, 0.74, industry_n),
        ]
    for q in ("Q1'25", "Q2'25", "Q3'25", "Q4'25", "Q1'26", "Q2'26"):
        rows += [
            _row(_HC, "", "net_margin", "calendar_quarter", q, 0.0, 430),
            _row(_HC, "", "gross_margin", "calendar_quarter", q, 0.58, 430),
        ]
    return rows


def test_the_series_never_draws_a_zero_margin_point(monkeypatch):
    """The 0.0 sector cells are not served, so the net-margin line is the industry's own
    (the lookup's documented "or the industry's, when the sector has none") — a real
    17-company median, never 0.0. The gross-margin line is still the sector's."""
    lookup, _ = rmi._real_lookup(monkeypatch, _healthcare_rows())
    series = lookup.get_benchmark_series(
        "Drug Manufacturers - General", _HC, ["net_margin", "gross_margin"], "annual")
    assert {(c["value"], c["level"]) for c in series["net_margin"].values()} == {(0.21, "industry")}
    assert {c["value"] for c in series["gross_margin"].values()} == {0.575}
    assert {c["level"] for c in series["gross_margin"].values()} == {"sector"}
    sbl._cache.clear()
    no_industry = [r for r in _healthcare_rows() if r["industry"] == ""]
    lookup, _ = rmi._real_lookup(monkeypatch, no_industry)
    series = lookup.get_benchmark_series(
        "Drug Manufacturers - General", _HC, ["net_margin"], "annual")
    assert series["net_margin"] == {}, "no 0.0 line is drawn in its place"


def test_a_dropped_newest_cell_hands_the_line_to_the_next_period(monkeypatch):
    """Pinned side effect: the line's population is chosen at its NEWEST period, so a
    mature industry whose newest cell was a dropped 0.0 keeps its own line from the period
    before (Biotechnology: 2025 net 0.0 at n=212, 2024 -1.2% at n=206)."""
    rows = [
        _row(_HC, "Biotechnology", "net_margin", "annual", _YEARS[-2], -0.0121, 206),
        _row(_HC, "Biotechnology", "net_margin", "annual", _DONE, 0.0, 212),
        _row(_HC, "", "net_margin", "annual", _YEARS[-2], -0.05, 437),
    ]
    lookup, _ = rmi._real_lookup(monkeypatch, rows)
    line = lookup.get_benchmark_series("Biotechnology", _HC, ["net_margin"], "annual")
    assert {k: c["level"] for k, c in line["net_margin"].items()} == {_YEARS[-2]: "industry"}


def test_the_current_benchmark_skips_a_zero_year(monkeypatch):
    """No TTM row: the annual fallback takes the newest complete MATURE year that is not a
    dropped 0.0 — an older real median, never 0.0."""
    rows = [
        _row(_HC, "", "net_margin", "annual", _YEARS[-2], 0.011, 437),
        _row(_HC, "", "net_margin", "annual", _DONE, 0.0, 443),
    ]
    lookup, _ = rmi._real_lookup(monkeypatch, rows)
    cell = lookup.get_current_benchmarks("", _HC, ["net_margin"])["net_margin"]
    assert cell["value"] == 0.011 and cell["level"] == "sector"


@pytest.mark.asyncio
async def test_profit_power_draws_no_zero_peer_point_through_the_real_lookup(monkeypatch):
    """LLY-shaped: Drug Manufacturers - General (n=17) gets the Healthcare sector line. The
    0.0 net-margin line is not drawn, the real gross-margin line is; Cay AI's peer sentence
    has no 0.0% to quote. Mutation: removing the gate draws peer net margin 0.0 on every
    point and the chat line says "Sector peer-group median net margin 0.0%"."""
    lookup, _ = rmi._real_lookup(monkeypatch, _healthcare_rows())
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    svc.supabase = None
    svc.fmp = nl._PPFake(profile={"symbol": "LLY", "sector": _HC,
                                  "industry": "Drug Manufacturers - General"})
    resp, _next, degraded = await svc._build_profit_power("LLY")
    assert degraded == []
    points = [*resp.annual, *resp.quarterly]
    assert points, "the fixture must draw company points"
    for field in ("sector_average_net_margin", "sector_average_operating_margin"):
        assert all(getattr(p, field) != 0.0 for p in points), [
            (p.period, getattr(p, field)) for p in points]
    assert all(p.sector_average_net_margin is None for p in resp.quarterly)
    assert any(p.sector_average_gross_margin == 57.5 for p in resp.annual), [
        (p.period, p.sector_average_gross_margin) for p in resp.annual]
    summary = ChatService._format_profit_summary("LLY", resp)
    assert "0.0%" not in (summary or ""), summary


@pytest.mark.asyncio
async def test_the_report_drill_down_draws_no_zero_peer_point(monkeypatch):
    lookup, _ = rmi._real_lookup(monkeypatch, _healthcare_rows())
    hist = await rmi._collector()._fetch_sector_benchmark_history(
        "Drug Manufacturers - General", _HC)
    for tag in ("annual", "quarterly"):
        for metric in ("net_margin", "operating_margin"):
            line = hist[tag].get(metric) or {}
            assert all(v != 0.0 for v in line.values()), (tag, metric, line)
    assert hist["quarterly"].get("net_margin") in (None, {}), hist["quarterly"]
    assert hist["annual"]["gross_margin"], "the real line is still drawn"


@pytest.mark.parametrize("peer_points,quoted", [
    ([0.0] * 5, True),        # control: a 0.0 point that reached it WAS quoted
    ([], False),              # what the guarded lookup hands it now
])
def test_the_stage_b_trajectory_quotes_whatever_peer_point_reaches_it(peer_points, quoted):
    """Stage B has no guard of its own — it quotes the line's newest peer point ("above
    sector 0.0%"), which is why the 0.0 cells are dropped at the lookup."""
    from app.services.agents import narrative_prompts as npmt

    metric = {"label": "Net Margin *", "history_unit": "percent",
              "annual_history": [{"period": y, "value": 30.0 + i} for i, y in enumerate(_YEARS)],
              "sector_annual_history": [{"period": y, "value": v}
                                        for y, v in zip(_YEARS, peer_points)]}
    line = npmt._metric_trajectory_line(metric, "sector")
    assert line is not None
    assert ("sector 0.0%" in line) is quoted, line


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. Free cash flow of exactly 0 is FMP's fill for a statement it does not have
# ══════════════════════════════════════════════════════════════════════════════════════
# HSBC (a half-year filer) reads freeCashFlow = operatingCashFlow = capex = 0 for every
# quarter, and JPM's unfiled newest quarter read the same (live 2026-10-21). The
# 2026-10-21 recompute wrote 77 calendar-quarter FCF-margin medians of exactly 0 from them.


@pytest.mark.parametrize("value,expected", [
    (0, None), (0.0, None), (-0.0, None), (None, None), (float("nan"), None),
    (float("inf"), None), (12.0, 12.0), (-30.0, -30.0), (1e-6, 1e-6),
])
def test_free_cash_flow_reading(value, expected):
    from app.utils.margins import free_cash_flow_reading

    assert free_cash_flow_reading(value) == expected


def test_the_fiscal_fcf_margin_median_skips_a_zero_filled_statement():
    from app.services.sector_benchmark_service import _compute_ratio_values

    companies = [
        {"income_annual": [{"calendarYear": "2024", "revenue": 100.0}],
         "cashflow_annual": [{"calendarYear": "2024", "freeCashFlow": 0.0}]},     # HSBC-shaped
        {"income_annual": [{"calendarYear": "2024", "revenue": 100.0}],
         "cashflow_annual": [{"calendarYear": "2024", "freeCashFlow": -30.0}]},
        {"income_annual": [{"calendarYear": "2024", "revenue": 100.0}],
         "cashflow_annual": [{"calendarYear": "2024", "freeCashFlow": 12.0}]},
    ]
    out = _compute_ratio_values(companies, "fcf_margin", "annual")
    assert sorted(out["2024"]) == pytest.approx([-0.30, 0.12])


def test_the_ttm_fcf_margin_skips_a_zero_filled_statement():
    from app.services import industry_benchmark_service as ibs

    zero = ibs._extract_ttm({"freeCashFlowPerShareTTM": 0.0, "revenuePerShareTTM": 20.0}, {})
    real = ibs._extract_ttm({"freeCashFlowPerShareTTM": 2.0, "revenuePerShareTTM": 20.0}, {})
    assert zero["fcf_margin"] is None
    assert real["fcf_margin"] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_profit_power_draws_no_zero_fcf_margin_for_a_missing_statement(monkeypatch):
    """HSBC-shaped: every quarterly cash-flow row is zero-filled. The company's FCF margin
    is no reading (None), never a flat 0.0% line; its other margins are untouched.
    Mutation: reading the raw freeCashFlow draws 0.0 on every quarter."""
    lookup, _ = rmi._real_lookup(monkeypatch, [])
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup", lambda: lookup)
    zero_cf = [dict(r, freeCashFlow=0.0, operatingCashFlow=0.0, capitalExpenditure=0.0)
               for r in gd._cf(gd._CAL_QUARTERS)]
    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    svc.supabase = None
    svc.fmp = nl._PPFake(cf_quarterly=zero_cf,
                         profile={"symbol": "HSBC", "sector": _FS, "industry": "Banks - Diversified"})
    resp, _next, _degraded = await svc._build_profit_power("HSBC")
    assert resp.quarterly, "the fixture must draw company quarters"
    assert all(p.fcf_margin is None for p in resp.quarterly), [
        (p.period, p.fcf_margin) for p in resp.quarterly]
    assert any(p.net_margin is not None for p in resp.quarterly)
