"""Which peer median a period shows (replaces the 2026-06 "hold back to the last mature
period" rule; owner decision 2026-10-07).

Three rules, all in `sector_benchmark_lookup`, exercised here through the REAL lookup
code with a fake Supabase client (no network):

1. A period that is not fully reported is never served. Rows are keyed by the period a
   company's statement ENDS in, so right after a period closes its cell holds only the
   early, off-calendar filers. On 2026-10-04 the "2026" annual cells held 6-27% of each
   group (Software-Infrastructure EPS growth 85% from 21 of 77 companies, against 18.8%
   for 2025; Consumer Defensive P/E from 39 of ~140). An annual / calendar-quarter row is
   served only when it was computed at least 75 days after its period ended.
2. Each period shows its OWN median: the industry's when it has >= 20 companies, else
   the SAME period's sector median when that one does, else whichever exists. The old
   hold-back painted a thin cell with an earlier period's mature cell — often an old
   SECTOR cell — so a small industry's line froze for years at one value.
3. The single "current" value (Overview cards, Health Check): industry TTM (n >= 20) →
   sector TTM (n >= 20) → newest complete mature annual year → None. Never a thin cell.
   A TTM row older than 21 days (a group that fell below 5 companies) is not current.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import pytest

from app.services import sector_benchmark_lookup as sbl
from app.services.sector_benchmark_lookup import (
    CALENDAR_QUARTER_PERIOD_TYPE as CQ,
    MATURE_SAMPLE_FLOOR,
    TTM_PERIOD_TYPE as TTM,
    flatten_benchmark_values,
    merge_peer_cells,
    pick_mature_benchmark,
    servable_benchmark_rows,
)

# The 2026-10-04 quarterly run (04:00 UTC) and the weekly TTM run (06:00 UTC).
RUN = "2026-10-04T04:00:06+00:00"
TTM_RUN = "2026-10-04T06:00:00+00:00"
TODAY = datetime(2026, 10, 7, 22, 0, tzinfo=timezone.utc)


def _row(metric, period_type, label, value, n, computed_at=RUN, sector="Consumer Defensive",
         industry=""):
    return {
        "sector": sector, "industry": industry, "metric_name": metric,
        "period_type": period_type, "period_label": label, "median_value": value,
        "sample_size": n, "computed_at": computed_at,
    }


# ── 1. Serving gate ────────────────────────────────────────────────────────────

def test_partial_2026_annual_rows_written_on_2026_10_04_are_not_served():
    rows = [
        _row("eps_yoy", "annual", "2026", 85.26, 20),   # 20 of 77: passes the old n>=20 floor
        _row("eps_yoy", "annual", "2025", 18.785, 76),
        _row("eps_yoy", "annual", "2010", 6.0, 40, computed_at="2026-06-24T02:13:06+00:00"),
    ]
    kept, dropped = servable_benchmark_rows(rows, "annual", now=TODAY)
    assert [r["period_label"] for r in kept] == ["2025", "2010"]   # old history keeps no age limit
    assert dropped == Counter({"incomplete_period": 1})


@pytest.mark.parametrize("computed_at, served", [
    ("2027-03-15T23:59:59+00:00", False),   # 74 days after Dec 31
    ("2027-03-16T00:00:00+00:00", True),    # 75 days: the boundary is inclusive
    ("2027-04-04T04:00:00+00:00", True),    # the first-Sunday-of-April run
])
def test_annual_year_is_served_once_computed_75_days_after_it_ended(computed_at, served):
    rows = [_row("revenue_yoy", "annual", "2026", 9.0, 140, computed_at=computed_at)]
    kept, _ = servable_benchmark_rows(rows, "annual", now=datetime(2027, 5, 1, tzinfo=timezone.utc))
    assert bool(kept) is served


def test_calendar_quarter_gate_matches_the_producer():
    rows = [
        _row("gross_margin", CQ, "Q3'26", 0.4, 90),   # ended 4 days before the run
        _row("gross_margin", CQ, "Q2'26", 0.4, 90),   # 96 days
    ]
    kept, dropped = servable_benchmark_rows(rows, CQ, now=TODAY)
    assert [r["period_label"] for r in kept] == ["Q2'26"]
    assert dropped["incomplete_period"] == 1


def test_a_row_without_computed_at_is_judged_as_computed_now():
    rows = [
        {"metric_name": "pe_ratio", "period_label": "2026", "median_value": 22.0, "sample_size": 40},
        {"metric_name": "pe_ratio", "period_label": "2025", "median_value": 20.0, "sample_size": 140},
    ]
    kept, _ = servable_benchmark_rows(rows, "annual", now=TODAY)
    assert [r["period_label"] for r in kept] == ["2025"]   # the open year stays hidden
    ttm = [{"metric_name": "pe_ratio", "period_label": "TTM", "median_value": 20.0, "sample_size": 140}]
    assert servable_benchmark_rows(ttm, TTM, now=TODAY)[0] == ttm   # age unknown ≠ old


@pytest.mark.parametrize("computed_at, served", [
    ("2026-09-17T00:00:00+00:00", True),    # 20 days 22 h old
    ("2026-09-16T21:59:00+00:00", False),   # just over 21 days
    ("2026-08-09T06:00:00+00:00", False),   # the oldest zombie row found on prod
])
def test_ttm_row_older_than_21_days_is_not_current(computed_at, served):
    rows = [_row("pe_ratio", TTM, "TTM", 27.3, 5, computed_at=computed_at)]
    kept, dropped = servable_benchmark_rows(rows, TTM, now=TODAY)
    assert bool(kept) is served
    assert dropped.get("stale_ttm", 0) == (0 if served else 1)


def test_unparseable_label_is_kept_not_guessed():
    rows = [_row("pe_ratio", "annual", "FY26", 1.0, 30), _row("pe_ratio", CQ, "Q5'26", 1.0, 30)]
    assert len(servable_benchmark_rows(rows[:1], "annual", now=TODAY)[0]) == 1
    assert len(servable_benchmark_rows(rows[1:], CQ, now=TODAY)[0]) == 1


# ── 2. One period, one cell ────────────────────────────────────────────────────

def _c(value, n, level):
    return {"value": value, "n": n, "level": level, "peer_group_name": level}


def test_merge_prefers_a_mature_industry_then_the_same_periods_mature_sector():
    assert merge_peer_cells(_c(1.0, 20, "industry"), _c(2.0, 400, "sector"))["level"] == "industry"
    assert merge_peer_cells(_c(1.0, 19, "industry"), _c(2.0, 400, "sector"))["level"] == "sector"
    assert merge_peer_cells(_c(1.0, 9, "industry"), _c(2.0, 12, "sector"))["level"] == "industry"
    assert merge_peer_cells(None, _c(2.0, 3, "sector"))["level"] == "sector"
    assert merge_peer_cells(_c(1.0, 6, "industry"), None)["level"] == "industry"
    assert merge_peer_cells(None, None) is None
    # a cell with no value is never "mature"
    assert merge_peer_cells({"value": None, "n": 50, "level": "industry"}, _c(2.0, 400, "sector"))["level"] == "sector"


def test_missing_n_counts_as_thin():
    assert merge_peer_cells({"value": 5.0, "level": "industry"}, _c(2.0, 400, "sector"))["level"] == "sector"


# ── Fake Supabase (filters the same way PostgREST would) ───────────────────────

class _Query:
    def __init__(self, rows):
        self._rows = rows
        self._filters = []
        self._in = None
        self._range = (0, 10**9)

    def select(self, columns):
        self.columns = columns
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def in_(self, col, vals):
        self._in = (col, set(vals))
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        out = [r for r in self._rows
               if all(r.get(c) == v for c, v in self._filters)
               and (self._in is None or r.get(self._in[0]) in self._in[1])]
        start, end = self._range
        return type("R", (), {"data": out[start:end + 1]})()


class _FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "sector_benchmarks"
        return _Query(self.rows)


def _lookup(rows, monkeypatch, now=TODAY):
    sbl._cache.clear()
    real = sbl.servable_benchmark_rows
    monkeypatch.setattr(sbl, "servable_benchmark_rows",
                        lambda r, pt, now_=None: real(r, pt, now=now))
    lk = sbl.SectorBenchmarkLookup.__new__(sbl.SectorBenchmarkLookup)
    lk.supabase = _FakeSupabase(rows)
    return lk


def test_a_small_industrys_line_never_freezes_at_an_old_sector_median(monkeypatch):
    # The audit's interior-stretch case: early quarters only exist at sector level
    # (n=150); from Q1'09 the 9-company industry has its own cells, beside the sector's.
    rows = []
    for y in range(6, 9):
        for q in range(1, 5):
            rows.append(_row("revenue_yoy", CQ, f"Q{q}'{y:02d}", 84.0, 150))
    for y in range(9, 12):
        for q in range(1, 5):
            label = f"Q{q}'{y:02d}"
            rows.append(_row("revenue_yoy", CQ, label, float(y * 10 + q), 150))       # sector
            rows.append(_row("revenue_yoy", CQ, label, -1.0, 9, industry="Tobacco"))  # thin industry
    rows.append(_row("revenue_yoy", CQ, "Q1'12", 3.3, 9, industry="Tobacco"))       # no sector cell
    lk = _lookup(rows, monkeypatch)
    rich = lk.get_benchmarks("Tobacco", "Consumer Defensive", ["revenue_yoy"], CQ)
    flat = flatten_benchmark_values(rich)["revenue_yoy"]
    for y in range(9, 12):
        for q in range(1, 5):
            assert flat[f"Q{q}'{y:02d}"] == float(y * 10 + q)   # that period's sector median
            assert rich["revenue_yoy"][f"Q{q}'{y:02d}"]["level"] == "sector"
    assert flat["Q1'12"] == 3.3                                  # own thin value, not 84.0
    assert rich["revenue_yoy"]["Q1'12"]["level"] == "industry"


def test_a_mature_industry_cell_still_wins(monkeypatch):
    rows = [
        _row("eps_yoy", "annual", "2025", 4.86, 77, sector="Technology", industry="Semiconductors"),
        _row("eps_yoy", "annual", "2025", 9.0, 453, sector="Technology"),
    ]
    rich = _lookup(rows, monkeypatch).get_benchmarks("Semiconductors", "Technology", ["eps_yoy"], "annual")
    assert rich["eps_yoy"]["2025"] == {"value": 4.86, "level": "industry",
                                       "peer_group_name": "Semiconductors", "n": 77}


def test_the_newest_annual_year_draws_no_peer_value_until_complete(monkeypatch):
    rows = [
        _row("eps_yoy", "annual", "2026", 85.26, 20, sector="Technology", industry="Software - Infrastructure"),
        _row("eps_yoy", "annual", "2026", 14.0, 120, sector="Technology"),
        _row("eps_yoy", "annual", "2025", 18.785, 76, sector="Technology", industry="Software - Infrastructure"),
    ]
    flat = flatten_benchmark_values(_lookup(rows, monkeypatch).get_benchmarks(
        "Software - Infrastructure", "Technology", ["eps_yoy"], "annual"))
    assert flat == {"eps_yoy": {"2025": 18.785}}   # no 2026 point — neither 85.26 nor 18.785


# ── 3. The single "current" value ───────────────────────────────────────────────

_KO = ("Beverages - Non-Alcoholic", "Consumer Defensive")


def test_ko_p_e_uses_the_complete_sector_ttm_not_a_partial_year(monkeypatch):
    # The prod shape on 2026-10-07: Beverages TTM n=13, Consumer Defensive TTM n=140,
    # Consumer Defensive annual 2026 n=39 (written 10-04, early filers only).
    rows = [
        _row("pe_ratio", TTM, "TTM", 23.83, 13, TTM_RUN, industry=_KO[0]),
        _row("pe_ratio", TTM, "TTM", 20.56, 140, TTM_RUN),
        _row("pe_ratio", "annual", "2026", 22.57, 39),
        _row("pe_ratio", "annual", "2025", 20.47, 139),
        _row("pe_ratio", "annual", "2025", 24.0, 13, industry=_KO[0]),
    ]
    cur = _lookup(rows, monkeypatch).get_current_benchmarks(*_KO, ["pe_ratio"])
    assert cur["pe_ratio"] == {"value": 20.56, "level": "sector",
                               "peer_group_name": "Consumer Defensive", "n": 140}


def test_a_mature_industry_ttm_beats_the_sector_ttm(monkeypatch):
    rows = [
        _row("pe_ratio", TTM, "TTM", 33.28, 59, TTM_RUN, sector="Technology", industry="Software - Infrastructure"),
        _row("pe_ratio", TTM, "TTM", 33.13, 400, TTM_RUN, sector="Technology"),
    ]
    cur = _lookup(rows, monkeypatch).get_current_benchmarks("Software - Infrastructure", "Technology", ["pe_ratio"])
    assert cur["pe_ratio"]["value"] == 33.28 and cur["pe_ratio"]["level"] == "industry"


def test_without_ttm_the_newest_complete_mature_year_is_used(monkeypatch):
    rows = [
        _row("debt_to_equity", "annual", "2026", 0.9, 60, industry=_KO[0]),   # incomplete: hidden
        _row("debt_to_equity", "annual", "2025", 0.38, 25, industry=_KO[0]),
        _row("debt_to_equity", "annual", "2025", 0.5, 139),
        _row("debt_to_equity", "annual", "2024", 0.36, 26, industry=_KO[0]),
    ]
    cur = _lookup(rows, monkeypatch).get_current_benchmarks(*_KO, ["debt_to_equity"])
    assert cur["debt_to_equity"]["value"] == 0.38 and cur["debt_to_equity"]["level"] == "industry"


def test_a_zombie_ttm_row_is_not_current(monkeypatch, caplog):
    # The zombie is MATURE (n=25) so only the 21-day gate can keep it out: without the gate
    # it would win at the TTM step and the answer would be 27.34 (review 2026-10-07: with
    # an n=5 zombie this test passed with the gate removed).
    rows = [
        _row("pe_ratio", TTM, "TTM", 27.34, 25, "2026-08-09T06:00:00+00:00",
             sector="Industrials", industry="Industrial - Pollution & Treatment Controls"),
        _row("pe_ratio", "annual", "2025", 24.0, 300, sector="Industrials"),
    ]
    sbl._serve_log_last.clear()
    with caplog.at_level("WARNING", logger=sbl.logger.name):
        cur = _lookup(rows, monkeypatch).get_current_benchmarks(
            "Industrial - Pollution & Treatment Controls", "Industrials", ["pe_ratio"])
    assert cur["pe_ratio"]["value"] == 24.0
    assert any("stale_ttm" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("computed_at, served", [
    ("2027-03-15T20:00:00-05:00", True),    # = 2027-03-16T01:00Z: day 75 in UTC, as the producer judged it
    ("2027-03-16T02:00:00+05:00", False),   # = 2027-03-15T21:00Z: day 74
])
def test_the_gate_judges_computed_at_in_utc(computed_at, served):
    rows = [_row("revenue_yoy", "annual", "2026", 9.0, 140, computed_at=computed_at)]
    kept, _ = servable_benchmark_rows(rows, "annual", now=datetime(2027, 5, 1, tzinfo=timezone.utc))
    assert bool(kept) is served


# ── One peer group per chart line (`get_benchmark_series`) ───────────────────────

def _solar_rows(newest_n):
    rows = []
    for y in range(2014, 2026):
        rows.append(_row("net_margin", "annual", str(y), 0.08, 300, sector="Technology"))
        n = 15 if y < 2022 else newest_n
        rows.append(_row("net_margin", "annual", str(y), 0.20, n, sector="Technology", industry="Solar"))
    return rows


def test_a_line_whose_industry_is_mature_now_is_the_industry_throughout(monkeypatch):
    # The review's case: Solar n=15 for 2014-2021, n=25 from 2022. The per-period merge
    # drew the sector's 8% until 2021 and the industry's 20% after — a 12-point step that
    # was only a change of population, under one label.
    rich = _lookup(_solar_rows(25), monkeypatch).get_benchmark_series(
        "Solar", "Technology", ["net_margin"], "annual")
    cells = rich["net_margin"]
    assert {c["level"] for c in cells.values()} == {"industry"}
    assert {c["value"] for c in cells.values()} == {0.20}
    assert len(cells) == 12


def test_a_line_whose_industry_is_thin_now_is_the_sector_throughout(monkeypatch):
    rich = _lookup(_solar_rows(19), monkeypatch).get_benchmark_series(
        "Solar", "Technology", ["net_margin"], "annual")
    cells = rich["net_margin"]
    assert {c["level"] for c in cells.values()} == {"sector"}
    assert {c["value"] for c in cells.values()} == {0.08}


def test_an_industry_missing_the_newest_period_gives_the_sector_line(monkeypatch):
    rows = [
        _row("eps_yoy", "annual", "2025", 5.0, 400, sector="Technology"),
        _row("eps_yoy", "annual", "2024", 6.0, 400, sector="Technology"),
        _row("eps_yoy", "annual", "2024", 9.0, 30, sector="Technology", industry="Solar"),
    ]
    rich = _lookup(rows, monkeypatch).get_benchmark_series("Solar", "Technology", ["eps_yoy"], "annual")
    assert flatten_benchmark_values(rich) == {"eps_yoy": {"2025": 5.0, "2024": 6.0}}


def test_a_thin_industry_with_no_sector_rows_still_draws_its_own_line(monkeypatch):
    rows = [_row("eps_yoy", "annual", "2025", 9.0, 6, sector="Technology", industry="Solar")]
    rich = _lookup(rows, monkeypatch).get_benchmark_series("Solar", "Technology", ["eps_yoy"], "annual")
    assert rich["eps_yoy"]["2025"]["level"] == "industry"


def test_the_series_hides_incomplete_periods_too(monkeypatch):
    rows = [
        _row("eps_yoy", "annual", "2026", 85.26, 20, sector="Technology", industry="Software - Infrastructure"),
        _row("eps_yoy", "annual", "2025", 18.785, 76, sector="Technology", industry="Software - Infrastructure"),
    ]
    rich = _lookup(rows, monkeypatch).get_benchmark_series(
        "Software - Infrastructure", "Technology", ["eps_yoy"], "annual")
    assert flatten_benchmark_values(rich) == {"eps_yoy": {"2025": 18.785}}


def test_a_failed_series_read_is_flagged(monkeypatch):
    lk = _lookup([], monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(lk, "_fetch_rows", _boom)
    rich = lk.get_benchmark_series("Solar", "Technology", ["eps_yoy"], "annual")
    assert sbl.lookup_failed(rich) and rich == {"eps_yoy": {}}


def test_nothing_mature_means_no_comparison_never_a_thin_cell(monkeypatch):
    rows = [
        _row("pe_ratio", TTM, "TTM", 88.0, 6, TTM_RUN, industry=_KO[0]),
        _row("pe_ratio", "annual", "2025", 61.0, 5, industry=_KO[0]),
    ]
    cur = _lookup(rows, monkeypatch).get_current_benchmarks(_KO[0], "", ["pe_ratio"])
    assert cur == {"pe_ratio": None}


def test_pick_mature_returns_none_rather_than_a_thin_cell():
    cells = {"2025": {"value": 10.0, "n": 8}, "2024": {"value": 9.0, "n": 19}}
    assert pick_mature_benchmark(cells) == (None, False)
    cells["2023"] = {"value": 7.0, "n": MATURE_SAMPLE_FLOOR}
    cell, held_back = pick_mature_benchmark(cells)
    assert cell["value"] == 7.0 and held_back is True
    assert pick_mature_benchmark({}) == (None, False)


def test_flatten_drops_cells_without_a_value():
    assert flatten_benchmark_values({"eps_yoy": {"2025": {"value": None, "n": 50},
                                                 "2024": {"value": 1.5, "n": 50}}}) == {"eps_yoy": {"2024": 1.5}}
    assert flatten_benchmark_values({"eps_yoy": {}}) == {"eps_yoy": {}}


@pytest.mark.parametrize("reader", ["get_benchmarks", "get_benchmark_series"])
def test_a_failed_read_is_not_cached_and_the_next_call_retries(monkeypatch, reader):
    # Round-3 review: nothing pinned this. A cached failure would hide every peer line for
    # the cache's hour after one Supabase blip.
    rows = [_row("eps_yoy", "annual", "2025", 5.0, 400, sector="Technology")]
    lk = _lookup(rows, monkeypatch)
    real_fetch = lk._fetch_rows
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("supabase 520")
        return real_fetch(*a, **k)

    monkeypatch.setattr(lk, "_fetch_rows", _flaky)
    first = getattr(lk, reader)("", "Technology", ["eps_yoy"], "annual")
    assert sbl.lookup_failed(first) and first == {"eps_yoy": {}}
    second = getattr(lk, reader)("", "Technology", ["eps_yoy"], "annual")
    assert not sbl.lookup_failed(second)
    assert flatten_benchmark_values(second) == {"eps_yoy": {"2025": 5.0}}
    third = getattr(lk, reader)("", "Technology", ["eps_yoy"], "annual")
    assert third is second, "a successful read IS cached"
