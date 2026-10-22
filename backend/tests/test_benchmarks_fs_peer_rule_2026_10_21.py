"""The Financial Services peer rule of 2026-10-20/21, in `sector_benchmark_lookup`.

A Financial Services company with an industry is compared with its OWN industry's median from
10 companies, and with no peer below that — never with the Financial Services SECTOR median,
a bank-dominated pool (owner: "i lean to option 1, but let's use "10 companies" for the
minimum."). Production showed MCO / SPGI / CME reading "Richly valued vs peers" against banks'
P/E of 12.35 and JPM's EV/EBITDA (Banks - Diversified n=19) falling to the sector and deciding
its Price rating (read-only, 2026-10-20).

The lookups are the REAL `SectorBenchmarkLookup` over a PostgREST fake (or `r2._RowLookup`,
which overrides only `_fetch_rows`, so the rule above it runs as in production).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

import test_cards_round2_2026_10_07 as r2
import test_health_check_deepcheck as hcd
import test_non_lender_members_peer_free_2026_10_09 as nl
import test_report_moat_index_benchmarks_2026_10_07 as rmi
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services import financials_metric_gate as gate
from app.services import health_check_service as hc
from app.services import sector_benchmark_lookup as sbl
from app.services import valuation_snapshot_service as vss
from app.services.agents import card_verdict as cv
from app.services.agents import ticker_report_data_collector as C

_NOW = datetime.now(timezone.utc)
_COMPUTED = (_NOW - timedelta(days=17)).isoformat()   # a fiscal run, well past 75 days
_FRESH = (_NOW - timedelta(days=2)).isoformat()       # this week's TTM run
_YEARS = [str(y) for y in range(_NOW.year - 6, _NOW.year - 1)]   # complete years
_DONE = _YEARS[-1]
_FS = "Financial Services"


@pytest.fixture(autouse=True)
def _clean_cache():
    sbl._cache.clear()
    yield
    sbl._cache.clear()


def _row(sector, industry, metric, period_type, label, value, n, computed=_COMPUTED):
    return rmi._row(sector, industry, metric, period_type, label, value, n, computed)


# ══════════════════════════════════════════════════════════════════════════════════════
# The Financial Services rule
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("sector,expected", [
    ("Financial Services", True), ("Financials", True), (" financial  services ", True),
    ("FINANCIAL SERVICES", True), ("Technology", False), ("Real Estate", False),
    ("", False), (None, False), (42, False),
])
def test_is_financial_services_sector(sector, expected):
    assert gate.is_financial_services_sector(sector) is expected


@pytest.mark.parametrize("industry,sector,floor", [
    ("Banks - Diversified", "Financial Services", 10),
    ("Insurance - Life", "Financials", 10),
    ("", "Financial Services", 20),                 # a direct sector read is unchanged
    ("Beverages - Non-Alcoholic", "Consumer Defensive", 20),
    ("REIT - Mortgage", "Real Estate", 20),         # gated, but not the FS sector
])
def test_peer_floor(industry, sector, floor):
    assert sbl.peer_floor(industry, sector) == floor
    assert sbl.fs_industry_only(industry, sector) is (floor == 10)


def _cell(v, n, level):
    return {"value": v, "n": n, "level": level}


def test_merge_never_returns_the_sector_cell_under_the_rule():
    ind10, ind9, sec = _cell(25.0, 10, "industry"), _cell(25.0, 9, "industry"), _cell(12.35, 454, "sector")
    assert sbl.merge_peer_cells(ind10, sec, industry_only_floor=10) is ind10
    assert sbl.merge_peer_cells(ind9, sec, industry_only_floor=10) is None
    assert sbl.merge_peer_cells(None, sec, industry_only_floor=10) is None
    # Without the rule (every other sector): unchanged.
    assert sbl.merge_peer_cells(ind10, sec) is sec
    assert sbl.merge_peer_cells(ind9, None) is ind9


def _fs_rows(industry, n, *, sector_n=454, ttm=True, annual=None):
    rows = []
    if ttm:
        rows += [_row(_FS, industry, "pe_ratio", "ttm", "TTM", 24.8, n, _FRESH),
                 _row(_FS, "", "pe_ratio", "ttm", "TTM", 12.35, sector_n, _FRESH)]
    for label, (v, an) in (annual or {}).items():
        rows.append(_row(_FS, industry, "pe_ratio", "annual", label, v, an))
        rows.append(_row(_FS, "", "pe_ratio", "annual", label, 12.0, 450))
    return rows


@pytest.mark.parametrize("n,expected", [(10, (24.8, "industry")), (9, None), (25, (24.8, "industry"))])
def test_current_benchmark_own_industry_or_none(monkeypatch, n, expected):
    lookup, fake = rmi._real_lookup(monkeypatch, _fs_rows("Financial - Data & Stock Exchanges", n))
    cell = lookup.get_current_benchmarks(
        "Financial - Data & Stock Exchanges", _FS, ["pe_ratio"])["pe_ratio"]
    assert (None if cell is None else (cell["value"], cell["level"])) == expected
    assert all(q.get("industry") != "" for q in fake.log), "the FS sector layer was read"


def test_a_thin_ttm_falls_back_only_to_the_newest_complete_year(monkeypatch):
    """TTM n=9: the industry's NEWEST complete year decides — used at n >= 10, else none;
    never an older year (with no sector behind it the old picker reached back years)."""
    industry = "Insurance - Reinsurance"
    rows = _fs_rows(industry, 9, annual={_YEARS[-3]: (9.1, 30), _DONE: (8.0, 8)})
    lookup, _ = rmi._real_lookup(monkeypatch, rows)
    assert lookup.get_current_benchmarks(industry, _FS, ["pe_ratio"])["pe_ratio"] is None
    sbl._cache.clear()
    rows = _fs_rows(industry, 9, annual={_YEARS[-3]: (9.1, 30), _DONE: (8.4, 12)})
    lookup, _ = rmi._real_lookup(monkeypatch, rows)
    cell = lookup.get_current_benchmarks(industry, _FS, ["pe_ratio"])["pe_ratio"]
    assert (cell["value"], cell["level"]) == (8.4, "industry")


def test_other_sectors_keep_the_sector_fallback(monkeypatch):
    """Control: Beverages at n=13 (KO) still gets the mature Consumer Defensive TTM."""
    rows = [_row("Consumer Defensive", "Beverages - Non-Alcoholic", "pe_ratio", "ttm", "TTM", 28.2, 13, _FRESH),
            _row("Consumer Defensive", "", "pe_ratio", "ttm", "TTM", 21.2, 106, _FRESH)]
    lookup, _ = rmi._real_lookup(monkeypatch, rows)
    cell = lookup.get_current_benchmarks("Beverages - Non-Alcoholic", "Consumer Defensive", ["pe_ratio"])["pe_ratio"]
    assert (cell["value"], cell["level"]) == (21.2, "sector")


def test_a_direct_fs_sector_read_is_unchanged(monkeypatch):
    """`industry=""` (competitor scoring, an FS company FMP gives no industry) still reads
    the sector."""
    lookup, _ = rmi._real_lookup(monkeypatch, _fs_rows("Banks - Regional", 200))
    cell = lookup.get_current_benchmarks("", _FS, ["pe_ratio"])["pe_ratio"]
    assert (cell["value"], cell["level"]) == (12.35, "sector")


def test_the_fs_series_keeps_only_its_own_periods_from_10_companies(monkeypatch):
    """The line is the industry's own, period by period from 10 companies; a newer SECTOR
    period never blanks it (the old picker chose "newest" across both layers)."""
    industry = "Insurance - Life"
    rows = [
        _row(_FS, industry, "net_margin", "annual", _YEARS[0], 0.09, 8),
        _row(_FS, industry, "net_margin", "annual", _YEARS[1], 0.10, 12),
        _row(_FS, industry, "net_margin", "annual", _YEARS[2], 0.11, 16),
        _row(_FS, "", "net_margin", "annual", _YEARS[2], 0.20, 500),
        _row(_FS, "", "net_margin", "annual", _DONE, 0.21, 500),    # newer, sector only
    ]
    lookup, fake = rmi._real_lookup(monkeypatch, rows)
    line = lookup.get_benchmark_series(industry, _FS, ["net_margin"], "annual")["net_margin"]
    assert {k: (c["value"], c["level"]) for k, c in line.items()} == {
        _YEARS[1]: (0.10, "industry"), _YEARS[2]: (0.11, "industry")}
    assert all(q.get("industry") != "" for q in fake.log), "the FS sector layer was read"


def test_a_small_fs_industry_draws_no_line(monkeypatch):
    rows = [_row(_FS, "Financial - Mortgages", "net_margin", "annual", y, 0.2, 5) for y in _YEARS]
    rows += [_row(_FS, "", "net_margin", "annual", y, 0.25, 500) for y in _YEARS]
    lookup, _ = rmi._real_lookup(monkeypatch, rows)
    line = lookup.get_benchmark_series("Financial - Mortgages", _FS, ["net_margin"], "annual")
    assert line["net_margin"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("industry_n,level", [(9, None), (10, "industry")])
async def test_the_health_check_follows_the_rule(monkeypatch, industry_n, level):
    """SPGI-shaped exchange: at n=9 EVERY row (D/E and P/E included, not only the gated
    three) is judged on its absolute bands; at n=10 its own industry decides. Mutation:
    the old fallback compares D/E and P/E with the FS sector (1.0 / 14.0)."""
    lookup = r2._RowLookup(r2._exchange_rows(industry_n=industry_n))
    resp = await r2._hc_build(monkeypatch, hcd._answers(
        profile=r2._profile(_FS, "Financial - Data & Stock Exchanges", "SPGI"),
        ratios=[dict(r2._EXCHANGE_RATIOS)],
    ), lookup)
    by = r2._by_type(resp)
    for kind in ("debt_to_equity", "pe_ratio", *r2._GATED):
        m = by[kind]
        assert m.peer_level == level, (kind, m.peer_level)
        if level is None:
            assert m.comparison_value is None, kind
            assert m.status == hc._absolute_status(kind, m.value), kind
        else:
            assert m.comparison_value is not None, kind
    assert resp.degraded == []
    r2._counts_consistent(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize("value,rated", [(None, False), (0.5, True)])
async def test_a_financial_services_price_card_needs_two_judged_multiples(
    monkeypatch, caplog, value, rated,
):
    """No usable industry median: P/E alone on its bands is one multiple → unrated. With
    the industry's medians every multiple is judged and the card is rated."""
    lookup = nl._Recorder(value=value)
    svc = nl._val_svc(monkeypatch, "AXP", lookup)
    with caplog.at_level(logging.INFO, logger=vss.logger.name):
        snap, degraded = await svc._compute_with_status("AXP")
    assert degraded == []
    assert (snap.rating > 0) is rated and (snap.weighted_score is not None) is rated
    assert bool([r for r in caplog.records if "[valuation-unrated]" in r.getMessage()]) is not rated


def test_the_drill_down_ttm_point_uses_the_floor_of_its_group():
    cell = {"value": 24.8, "n": 10, "level": "industry"}
    assert C._mature_ttm_value("pe_ratio", cell) is None             # 20 elsewhere
    assert C._mature_ttm_value("pe_ratio", cell, 10) == 24.8         # FS industry
    assert C._mature_ttm_value("pe_ratio", dict(cell, n=9), 10) is None


def _snap(category, rows: Dict[str, Any], rating=3):
    return SnapshotItemResponse(
        category=category, rating=rating, weighted_score=float(rating),
        metrics=[SnapshotMetricResponse(name=k, value="1", metric_key=k, score=s,
                                        peer_level=lvl) for k, (s, lvl) in rows.items()],
    )


def test_a_peer_free_row_never_claims_a_comparison():
    """FS Valuation card: P/E judged on its absolute bands (no median), P/B against the
    industry. The P/E phrase is peer-free; a non-FS card's wording is unchanged."""
    snap = _snap("Price", {"pe": (5, None), "pb": (3, "industry")})
    card = C._snapshot_to_card("Valuation", snap, peer_group_level="industry", per_row_peers=True)
    assert card["quality_label"] == "Low P/E", card["quality_label"]
    legacy = C._snapshot_to_card("Valuation", snap, peer_group_level="industry")
    assert legacy["quality_label"] == "Cheaper Than Industry"


def test_a_card_that_compared_nothing_names_no_peer():
    snap = _snap("Profitability", {"net_margin": (3, None), "roe": (3, None)})
    card = C._snapshot_to_card("Profitability", snap, peer_group_level="industry",
                               per_row_peers=True)
    assert card["quality_label"] == "Neither Strong Nor Weak"


@pytest.mark.parametrize("history,expected", [
    (None, "Neither Strong Nor Weak"),
    ({"growth": {"annual": [{"period": "2025", "value": 5.0}], "sector_annual": [
        {"period": "2025", "value": 4.0}], "sector_annual_level": "industry"}},
     "In Line With Industry"),
])
def test_a_growth_card_reads_its_peer_lines(monkeypatch, history, expected):
    """Growth rows carry no peer_level; the card compared something exactly when a row drew
    a peer line."""
    monkeypatch.setattr(C, "_resolve_history_key", lambda name: "growth")
    snap = _snap("Growth", {"revenue_growth": (3, None)})
    card = C._snapshot_to_card("Growth", snap, history_lookup=history,
                               peer_group_level="industry", per_row_peers=True)
    assert card["quality_label"] == expected


def test_card_verdict_peer_free_metrics_default_changes_nothing():
    args = ("Valuation", 3, "industry", [("pe", 5)])
    assert cv.generate_card_verdict(*args)[0] == "Cheaper Than Industry"
    assert cv.generate_card_verdict(*args, peer_free_metrics=set())[0] == "Cheaper Than Industry"
    assert cv.generate_card_verdict(*args, peer_free_metrics={"pe"})[0] == "Low P/E"
    # A phrase with no "{peer}" is the same either way.
    assert cv.generate_card_verdict("Valuation", 3, "industry", [("pb", 5)],
                                    peer_free_metrics={"pb"})[0] == "Low Price-to-Book"
