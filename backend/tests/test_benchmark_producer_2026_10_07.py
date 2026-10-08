"""Benchmark PRODUCER fixes of 2026-10-07 (peer-benchmark audit, plan workstream B).

`industry_benchmark_service` (+ its helpers in `sector_benchmark_service`) writes every
sector / industry median in `sector_benchmarks`. The audit proved on prod data that:

  B1  annual rows were written for the year still being reported: the 2026-10-04 run wrote
      "2026" cells from 6-27% of each group. Owner decision: an incomplete period shows NO
      peer value, so year Y is written only from Dec 31 Y + 75 days (the calendar-quarter
      rule), on the same constant the readers apply (`BENCHMARK_REPORTING_LAG_DAYS`).
  B2  the producer keyed annual rows `date[:4]` while every reader joins on
      `period_labels.annual_benchmark_key` (year of `date - 7 days`): a 52/53-week year ending
      Jan 1-7 sat one year AHEAD of its peers, and two of a company's rows sharing a key both
      voted in the median.
  B3  a quarter that ended before 2000 ("Q4'93") was keyed — and read back as 2093.
  B4  a sector that failed while writing its aggregate could look FRESH to the resume logic
      (the marker was written in 100-row batches, before the calendar-quarter rows), and a
      partial run settled the quarterly claim, so the failed sector was not retried.
  B5  every FMP failure (a 429 storm included) silently became "no data for this company";
      a 429 was never retried and nothing was counted.

Hermetic: inline FMP-shaped rows, an in-memory `sector_benchmarks` table, stubbed sleeps.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest

import app.services.industry_benchmark_service as ibs
import app.services.notification_jobs as nj
import app.services.sector_benchmark_service as sbs
from app import main as m
from app.integrations.fmp import FMPRateLimitException, FMPUnavailableException
from app.services.sector_benchmark_lookup import (
    CALENDAR_QUARTER_PERIOD_TYPE,
    servable_benchmark_rows,
)
from app.utils import period_labels as pl

CQ = CALENDAR_QUARTER_PERIOD_TYPE
_FIVE = [0.10, 0.20, 0.30, 0.40, 0.50]          # clears MIN_SAMPLE_SIZE (5)


def _svc() -> ibs.IndustryBenchmarkService:
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    svc._calendar_quarter_blocked = False
    return svc


def _written(values, now, sector="Technology", industry="Software - Application"):
    rows = _svc()._rows_from_values(sector, industry, values, now)
    return {(r["metric_name"], r["period_type"], r["period_label"]) for r in rows}


def _metric(name: str) -> dict:
    return next(mc for mc in sbs.METRIC_CONFIGS if mc["name"] == name)


def _sector_svc(fmp=None) -> sbs.SectorBenchmarkService:
    svc = sbs.SectorBenchmarkService.__new__(sbs.SectorBenchmarkService)
    svc.fmp = fmp
    svc.supabase = None
    svc._fmp_semaphore = asyncio.Semaphore(10)
    return svc


# ═══ B1 — the annual completeness gate ═════════════════════════════════════════════


def test_the_gate_constants_are_the_readers_constants():
    assert pl.BENCHMARK_REPORTING_LAG_DAYS == 75
    assert ibs.CALENDAR_QUARTER_MIN_AGE_DAYS == pl.BENCHMARK_REPORTING_LAG_DAYS
    for label in ("Q1'26", " Q4'25 ", "Q2'06", "Q5'26", "Q3 '26", "2026", "", None, 3):
        assert ibs._calendar_quarter_end(label) == pl.benchmark_period_end(CQ, label)


def test_on_2026_10_07_the_current_year_is_held_and_complete_years_land(caplog):
    values = {
        ("eps_yoy", "annual", "2026"): list(_FIVE) * 5,   # n=25: a "mature" partial cohort
        ("eps_yoy", "annual", "2025"): list(_FIVE),
        ("eps_yoy", "annual", "2011"): list(_FIVE),
        ("roe", "annual", "2026"): list(_FIVE),
    }
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        written = _written(values, "2026-10-07T04:00:00+00:00", industry="Software - Infrastructure")
    # Fails on the pre-fix producer, which wrote "2026" from 21 of 77 companies.
    assert written == {("eps_yoy", "annual", "2025"), ("eps_yoy", "annual", "2011")}
    held = [r.getMessage() for r in caplog.records if "not writing annual year" in r.getMessage()]
    assert len(held) == 1                         # one line per peer group, not per metric
    assert "Technology / Software - Infrastructure" in held[0]
    assert "2026" in held[0] and "2025" not in held[0] and "2026-10-07" in held[0]


@pytest.mark.parametrize("run_at, expected", [
    ("2027-03-15T23:59:59+00:00", False),   # Dec 31 2026 + 74 days
    ("2027-03-16T00:00:00+00:00", True),    # + 75 days: the April 2027 run writes it
    ("2026-12-31T12:00:00+00:00", False),   # the year's own last day
    ("2026-06-30T00:00:00+00:00", False),   # mid-year (clock skew / an early run)
    ("2027-03-15T20:00:00-05:00", True),    # = 2027-03-16 01:00 UTC → judged on the UTC date
])
def test_annual_boundary_is_75_days_after_december_31(run_at, expected):
    written = _written({("roe", "annual", "2026"): list(_FIVE)}, run_at)
    assert (("roe", "annual", "2026") in written) is expected


@pytest.mark.parametrize("label", ["", "FY26", "20261", "26", "abcd", None, 2025])
def test_an_annual_row_with_no_year_label_is_dropped_and_logged(label, caplog):
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        written = _written({("roe", "annual", label): list(_FIVE)}, "2026-10-07T00:00:00+00:00")
    assert written == set()
    assert any("no four-digit year label" in r.getMessage() for r in caplog.records)


def test_a_thin_annual_cell_is_dropped_by_min_sample_size_first(caplog):
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        written = _written({("roe", "annual", "2026"): [0.1, 0.2]}, "2026-10-07T00:00:00+00:00")
    assert written == set()
    assert not [r for r in caplog.records if "annual year" in r.getMessage()]


def test_ttm_and_calendar_quarter_rows_are_not_touched_by_the_annual_gate():
    values = {
        ("roe", CQ, "Q2'26"): list(_FIVE),
        ("roe", "annual", "2025"): list(_FIVE),
    }
    assert _written(values, "2026-10-07T00:00:00+00:00") == set(values)


@pytest.mark.parametrize("run_day", [
    date(2026, 10, 4), date(2026, 10, 7), date(2027, 1, 3), date(2027, 3, 16), date(2027, 4, 4),
])
def test_every_row_the_producer_writes_is_servable_by_the_readers(run_day):
    """Producer and reader apply ONE rule: nothing written by a run is hidden by the reader's
    completeness filter when read back (computed_at = the run), and everything the producer
    holds back is a period the reader would hide too."""
    labels = ["2024", "2025", "2026", "2027"]
    quarters = ["Q4'25", "Q1'26", "Q2'26", "Q3'26", "Q4'26", "Q1'27"]
    values = {("roe", "annual", y): list(_FIVE) for y in labels}
    values.update({("roe", CQ, q): list(_FIVE) for q in quarters})
    now = datetime(run_day.year, run_day.month, run_day.day, 4, tzinfo=timezone.utc).isoformat()
    rows = _svc()._rows_from_values("Technology", "", values, now)
    for period_type in ("annual", CQ):
        typed = [r for r in rows if r["period_type"] == period_type]
        kept, dropped = servable_benchmark_rows(typed, period_type, now=datetime.now(timezone.utc))
        assert len(kept) == len(typed) and not dropped
        held = {
            lab for (_m, pt, lab) in values if pt == period_type
        } - {r["period_label"] for r in typed}
        for lab in held:
            assert pl.benchmark_period_complete(period_type, lab, run_day) is False


# ═══ B2 — the annual key is the readers' join key, one record per company per key ══════


@pytest.mark.parametrize("record", [
    {"date": "2026-01-03"},                                   # Cadence-shaped FY2025
    {"date": "2026-01-07"},                                   # last spill day
    {"date": "2026-01-08"},                                   # first non-spill day
    {"date": "2025-12-31", "fiscalYear": "2025"},
    {"date": "2026-02-01", "fiscalYear": "2025"},             # Home Depot FY2025
    {"date": "2025-06-30", "calendarYear": "2025"},
    {"calendarYear": "2024"},                                 # no date: calendarYear fallback
    {"calendarYear": 2023, "date": None},
    {"date": "garbage"},
    {"date": None},
    {},
])
def test_the_storage_key_is_the_readers_join_key(record):
    assert sbs._annual_period_label(record) == pl.annual_benchmark_key(record)


def test_a_jan_1_to_7_year_end_pools_with_its_december_peers():
    # FY2023 ends 2023-12-30, FY2024 2025-01-04 (53 weeks), FY2025 2026-01-03.
    cadence = [
        {"date": "2026-01-03", "grossProfitMargin": 0.88},
        {"date": "2025-01-04", "grossProfitMargin": 0.86},
        {"date": "2023-12-30", "grossProfitMargin": 0.89},
    ]
    december = [
        {"date": "2025-12-31", "grossProfitMargin": 0.50},
        {"date": "2024-12-31", "grossProfitMargin": 0.40},
        {"date": "2023-12-31", "grossProfitMargin": 0.30},
    ]
    data = [{"ratios_annual": cadence}, {"ratios_annual": december}]
    pooled = _sector_svc()._collect_metric_values(data, _metric("gross_margin"), "annual")
    # The old `date[:4]` key gave {"2026": [0.88], "2025": [0.86, 0.50], "2024": [0.40],
    # "2023": [0.89, 0.30]} — FY2025 a year ahead, FY2024 pooled with the peers' 2025.
    assert pooled == {"2025": [0.88, 0.50], "2024": [0.86, 0.40], "2023": [0.89, 0.30]}


@pytest.mark.parametrize("order", ["newest-first", "oldest-first"])
def test_two_rows_with_one_key_vote_once_and_a_full_year_beats_a_transition_stub(order):
    rows = [
        {"date": "2024-06-30", "grossProfitMargin": 0.20},   # old June year-end (a full year)
        {"date": "2024-12-31", "grossProfitMargin": 0.60},   # Jul-Dec 2024 transition stub, same key
        {"date": "2024-12-31", "grossProfitMargin": 0.60},   # FMP repeated the row
        {"date": "2023-06-30", "grossProfitMargin": 0.10},
    ]
    if order == "newest-first":
        rows = sorted(rows, key=lambda r: r["date"], reverse=True)
    data = [{"ratios_annual": rows}]
    pooled = _sector_svc()._collect_metric_values(data, _metric("gross_margin"), "annual")
    # Round 2 (review F5): newest-end-wins kept the 6-month stub (its end is 184 days after
    # the June year's) — the FULL June year now keeps 2024; each key still votes once.
    # Newest-wins between two full years / a repeated row: test_benchmark_producer_round2_*.
    assert pooled == {"2024": [0.20], "2023": [0.10]}
    indexed = sbs._index_by_period(rows, "annual")
    assert indexed["2024"]["date"] == "2024-06-30"


def test_computed_ratios_join_statements_on_the_same_annual_key():
    income = [{"date": "2026-01-03", "revenue": 100.0}, {"date": "2025-01-04", "revenue": 80.0}]
    cash = [{"date": "2026-01-03", "freeCashFlow": 25.0}, {"date": "2025-01-04", "freeCashFlow": 8.0}]
    pooled = sbs._compute_ratio_values(
        [{"income_annual": income, "cashflow_annual": cash}], "fcf_margin", "annual",
    )
    assert pooled == {"2025": [0.25], "2024": [0.10]}


def test_annual_yoy_is_labelled_by_the_join_key_across_a_53_week_year():
    rows = [
        {"date": "2023-12-30", "revenue": 100.0},
        {"date": "2025-01-04", "revenue": 120.0},    # 371 days later: FY2024
        {"date": "2026-01-03", "revenue": 150.0},    # FY2025
    ]
    yoy = sbs._compute_yoy_for_records(rows, "revenue", is_quarterly=False)
    # The old `date[:4]` walk: 2023 → 2025 (gap 2, skipped), 2025 → 2026 filed as "2026".
    assert yoy == {"2024": 20.0, "2025": 25.0}


def test_annual_yoy_skips_a_fiscal_year_end_change_rather_than_file_15_months_as_one_year():
    rows = [
        {"date": "2022-09-30", "revenue": 100.0},
        {"date": "2023-09-30", "revenue": 110.0},
        {"date": "2024-12-31", "revenue": 200.0},    # Sep → Dec switch: 458 days later
        {"date": "2025-12-31", "revenue": 220.0},
    ]
    yoy = sbs._compute_yoy_for_records(rows, "revenue", is_quarterly=False)
    assert yoy == {"2023": 10.0, "2025": 10.0}       # 2024 (458 days) is not a YoY


def test_annual_yoy_with_duplicates_missing_years_and_bad_bases():
    rows = [
        {"date": "2021-12-31", "revenue": 0.0},          # zero base → 2022 skipped
        {"date": "2022-12-31", "revenue": 50.0},
        {"date": "2022-12-31", "revenue": 50.0},         # repeated row
        # 2023 missing → 2024 has no base
        {"date": "2024-12-31", "revenue": float("nan")},
        {"date": "2025-12-31", "revenue": 70.0},         # NaN base → skipped
        None, "x", {"revenue": 1.0},                     # malformed / undated
    ]
    assert sbs._compute_yoy_for_records(rows, "revenue", is_quarterly=False) == {}


def test_a_dated_row_beats_an_undated_or_garbage_dated_row_with_the_same_key():
    rows = [
        {"date": "2024-12-31", "grossProfitMargin": 0.60},
        {"date": "garbage", "calendarYear": "2024", "grossProfitMargin": 0.10},
        {"calendarYear": "2024", "grossProfitMargin": 0.20},
    ]
    for order in (rows, list(reversed(rows))):
        assert sbs._by_annual_key(order)["2024"]["grossProfitMargin"] == 0.60


def test_annual_yoy_without_dates_falls_back_to_calendar_year_and_needs_no_gap():
    rows = [{"calendarYear": "2024", "revenue": 100.0}, {"calendarYear": "2025", "revenue": 90.0}]
    assert sbs._compute_yoy_for_records(rows, "revenue", is_quarterly=False) == {"2025": -10.0}


# ═══ B3 — no calendar quarter before 2000 is ever keyed ═══════════════════════════════


_OLD_AND_NEW = [
    {"date": "1993-12-31", "revenue": 10.0, "grossProfitMargin": 0.11},
    {"date": "1999-03-31", "revenue": 11.0, "grossProfitMargin": 0.12},
    {"date": "1999-12-31", "revenue": 12.0, "grossProfitMargin": 0.13},
    {"date": "2000-01-02", "revenue": 13.0, "grossProfitMargin": 0.14},   # spill → Q4 1999
    {"date": "2000-03-31", "revenue": 14.0, "grossProfitMargin": 0.15},
    {"date": "2000-06-30", "revenue": 28.0, "grossProfitMargin": 0.16},
]


def test_pre_2000_quarters_are_skipped_quietly_with_a_debug_count(caplog):
    with caplog.at_level(logging.DEBUG, logger=sbs.logger.name):
        keyed = sbs._by_calendar_quarter(_OLD_AND_NEW)
    assert set(keyed) == {(2000, 1), (2000, 2)}
    debug = [r for r in caplog.records if "ended before 2000" in r.getMessage()]
    assert len(debug) == 1 and debug[0].levelno == logging.DEBUG
    assert "skipped 4 quarterly row(s)" in debug[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_no_two_digit_label_from_the_1990s_reaches_a_stored_key():
    data = [{"ratios_quarterly": list(_OLD_AND_NEW), "income_quarterly": list(_OLD_AND_NEW)}]
    svc = _sector_svc()
    pooled = svc._collect_metric_values(data, _metric("gross_margin"), "quarterly")
    assert set(pooled) == {"Q1'00", "Q2'00"}
    qoq = svc._collect_metric_values(data, _metric("revenue_qoq"), "quarterly")
    assert qoq == {"Q2'00": [100.0]}                 # Q1'00 has no (keyed) Q4'99 to compare
    yoy = svc._collect_metric_values(data, _metric("revenue_yoy"), "quarterly")
    assert yoy == {}                                 # Q1'00 vs Q1'99: the base is not keyed
    assert sbs._quarterly_period_label({"date": "1993-12-31"}) == ""
    assert sbs._quarterly_period_label({"date": "2000-01-02"}) == ""
    assert sbs._quarterly_period_label({"date": "2000-03-31"}) == "Q1'00"
    for label in pooled:
        assert pl.benchmark_period_end(CQ, label).year == 2000


@pytest.mark.asyncio
async def test_a_1993_cohort_never_becomes_a_2093_row(monkeypatch):
    svc = _svc()
    svc._sb = _sector_svc()
    rows = [{"date": "1993-12-31", "grossProfitMargin": 0.5, "revenue": 1.0}]

    async def fake_fetch(_tickers, _al, _ql, **_k):
        return [{"ratios_quarterly": list(rows), "income_quarterly": list(rows)}] * 6

    monkeypatch.setattr(svc, "_fetch_batched", fake_fetch)
    values = await svc._industry_value_lists([("T", 1.0)], 1, 1)
    assert not any(pt == CQ for (_m, pt, _l) in values)
    assert svc._rows_from_values("S", "I", values, "2026-10-07T00:00:00+00:00") == []


# ═══ B4 — the freshness marker is the last, atomic write; a partial run is unsettled ══


class _Table:
    """`sector_benchmarks` in memory: upserts merge on the conflict key; the freshness
    probe answers the newest matching row. `fail(batch)` may return an exception to raise."""

    def __init__(self, fail: Optional[Callable[[List[Dict[str, Any]]], Optional[Exception]]] = None):
        self.rows: Dict[tuple, Dict[str, Any]] = {}
        self.calls: List[List[Dict[str, Any]]] = []
        self.fail = fail

    def table(self, _name: str):
        db = self

        class _Q:
            def __init__(self) -> None:
                self.batch: Optional[List[Dict[str, Any]]] = None
                self.eqs: Dict[str, Any] = {}

            def upsert(self, batch, on_conflict=None):
                self.batch = list(batch)
                return self

            def eq(self, col, val):
                self.eqs[col] = val
                return self

            def __getattr__(self, _attr):            # select / order / limit
                return lambda *a, **k: self

            def execute(self):
                if self.batch is not None:
                    db.calls.append(self.batch)
                    exc = db.fail(self.batch) if db.fail else None
                    if exc is not None:
                        raise exc
                    for r in self.batch:
                        key = (r["sector"], r["industry"], r["metric_name"],
                               r["period_type"], r["period_label"])
                        db.rows[key] = dict(r)
                    return SimpleNamespace(data=self.batch)
                hits = [r for r in db.rows.values()
                        if all(r.get(c) == v for c, v in self.eqs.items())]
                hits.sort(key=lambda r: r["computed_at"], reverse=True)
                return SimpleNamespace(data=hits[:1])

        return _Q()


def _many_values(n_metrics: int = 30, years=("2021", "2022", "2023", "2024", "2025"),
                 quarters=("Q1'25", "Q2'25")) -> Dict[tuple, List[float]]:
    v: Dict[tuple, List[float]] = {}
    for i in range(n_metrics):
        for y in years:
            v[(f"m{i}", "annual", y)] = list(_FIVE)
        for q in quarters:
            v[(f"m{i}", CQ, q)] = list(_FIVE)
    return v


def _wired(monkeypatch, db: _Table, universe: List[Dict[str, Any]], values=None):
    """A service over `db` whose per-industry statement fetch is stubbed to `values`."""
    svc = _svc()
    svc.supabase = db
    svc._sb = _sector_svc()
    computed: List[str] = []

    async def fake_values(ticker_caps, _al, _ql, **_k):
        computed.append(ticker_caps[0][0])
        return dict(values if values is not None else _many_values())

    monkeypatch.setattr(svc, "_industry_value_lists", fake_values)
    monkeypatch.setattr(ibs, "_fetch_benchmark_universe", lambda: [dict(e) for e in universe])
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)
    monkeypatch.setattr(ibs, "_industry_benchmark_service", svc)
    return svc, computed


_TWO_SECTORS = [
    {"industry": "Software", "sector": "Technology", "market_caps": {"MSFT": 3e12}},
    {"industry": "Banks", "sector": "Financial Services", "market_caps": {"JPM": 6e11}},
]


def _aggregate(db: _Table, sector: str, period_type: str) -> List[Dict[str, Any]]:
    return [r for r in db.rows.values()
            if r["sector"] == sector and r["industry"] == "" and r["period_type"] == period_type]


@pytest.mark.asyncio
async def test_the_marker_is_one_statement_after_the_aggregates_calendar_quarter_rows(monkeypatch):
    db = _Table()
    svc, _ = _wired(monkeypatch, db, _TWO_SECTORS[:1])
    await svc._compute_sector("Technology", [("Software", [("MSFT", 1.0)])], 1, 1)

    aggregate_calls = [c for c in db.calls if c[0]["industry"] == ""]
    annual_calls = [c for c in aggregate_calls if c[0]["period_type"] == "annual"]
    # 30 metrics × 5 years = 150 annual rows > UPSERT_BATCH_SIZE, still ONE statement…
    assert len(annual_calls) == 1 and len(annual_calls[0]) == 150 > sbs.UPSERT_BATCH_SIZE
    assert all(r["period_type"] == "annual" for r in annual_calls[0])
    # …and the LAST write of the sector, after its calendar-quarter rows.
    assert db.calls[-1] is annual_calls[0]
    assert any(c[0]["period_type"] == CQ for c in aggregate_calls[:-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["aggregate-quarter", "marker", "marker-second-hundred", "industry"])
async def test_a_sector_failing_mid_write_never_looks_fresh(monkeypatch, where):
    def fail(batch):
        r = batch[-1]
        if where == "aggregate-quarter" and r["industry"] == "" and r["period_type"] == CQ:
            return RuntimeError("Server disconnected")
        if where == "marker" and r["industry"] == "" and r["period_type"] == "annual":
            return RuntimeError("Server disconnected")
        # Under the old 100-row batches the first hundred marker rows were already stored
        # (and fresh) when the second hundred failed.
        if where == "marker-second-hundred" and any(
            x["industry"] == "" and x["period_type"] == "annual" and x["metric_name"] == "m29"
            for x in batch
        ):
            return RuntimeError("Server disconnected")
        if where == "industry" and r["industry"] == "Software" and r["period_type"] == CQ:
            return RuntimeError("Server disconnected")
        return None

    db = _Table(fail)
    svc, _ = _wired(monkeypatch, db, _TWO_SECTORS[:1])
    with pytest.raises(RuntimeError):
        await svc._compute_sector("Technology", [("Software", [("MSFT", 1.0)])], 1, 1)

    assert _aggregate(db, "Technology", "annual") == []
    assert svc._sector_is_fresh("Technology", 24) is False


@pytest.mark.asyncio
async def test_a_refused_calendar_quarter_type_still_writes_the_marker(monkeypatch):
    """Migration 184 missing: the aggregate's calendar-quarter rows are refused (23514 on
    the period_type CHECK) and skipped; the marker still lands — as before 2026-10-07."""
    from postgrest.exceptions import APIError

    check = APIError({"code": "23514", "message": 'violates check constraint '
                      '"sector_benchmarks_period_type_check"', "details": None, "hint": None})
    db = _Table(lambda b: check if b[0]["period_type"] == CQ else None)
    svc, _ = _wired(monkeypatch, db, _TWO_SECTORS[:1])
    await svc._compute_sector("Technology", [("Software", [("MSFT", 1.0)])], 1, 1)
    assert svc._calendar_quarter_blocked is True
    assert len(_aggregate(db, "Technology", "annual")) == 150
    assert svc._sector_is_fresh("Technology", 24) is True


@pytest.mark.asyncio
async def test_a_partial_run_raises_and_the_same_day_retry_recomputes_only_the_failed_sector(monkeypatch, caplog):
    outage = {"on": True}

    def fail(batch):
        if outage["on"] and any(r["sector"] == "Financial Services" and r["industry"] == ""
                                and r["period_type"] == CQ for r in batch):
            return RuntimeError("Server disconnected")
        return None

    db = _Table(fail)
    svc, computed = _wired(monkeypatch, db, _TWO_SECTORS)

    with caplog.at_level(logging.ERROR, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await svc.recompute_all(skip_if_fresh_hours=24)
    exc = info.value
    assert exc.reason == "sectors failed" and exc.failed_sectors == ["Financial Services"]
    assert exc.summary["sectors_done"] == 1 and exc.summary["sectors_failed"] == 1
    assert exc.summary["rows_upserted"] > 0
    assert "INCOMPLETE (1 sector(s) failed: Financial Services)" in str(exc)
    assert any("recompute INCOMPLETE" in r.getMessage() for r in caplog.records)
    assert svc._sector_is_fresh("Technology", 24) is True
    assert svc._sector_is_fresh("Financial Services", 24) is False

    # The retry (same day, upstream back): Technology is skipped as fresh, Banks recomputed.
    outage["on"] = False
    computed.clear()
    summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert computed == ["JPM"]
    assert summary["sectors_skipped_fresh"] == 1 and summary["sectors_done"] == 1
    assert summary["sectors_failed"] == 0
    assert svc._sector_is_fresh("Financial Services", 24) is True


@pytest.mark.asyncio
async def test_a_dry_run_with_a_failing_sector_still_returns(monkeypatch):
    svc, _ = _wired(monkeypatch, _Table(), _TWO_SECTORS)

    async def boom(sector, *_a, **_k):
        if sector == "Technology":
            raise RuntimeError("bug")
        return 0

    monkeypatch.setattr(svc, "_compute_sector", boom)
    summary = await svc.recompute_all(dry_run=True)
    assert summary["sectors_failed"] == 1 and summary["dry_run"] is True


def _real_claim_ledger(monkeypatch) -> Dict[str, Any]:
    ledger: Dict[str, Any] = {}

    def _claim(job, *, timezone_name="UTC", now=None, stale_seconds=None):
        ledger["claimed"] = (job, stale_seconds)
        return True

    def _finish(job, *, success, items=0, error=None, timezone_name="UTC", now=None):
        ledger["finished"] = {"job": job, "success": success, "error": error}

    monkeypatch.setattr(nj, "claim_scheduled", _claim)
    monkeypatch.setattr(nj, "finish_scheduled", _finish)
    return ledger


@pytest.mark.asyncio
async def test_a_partial_run_leaves_the_quarterly_claim_unsettled(monkeypatch):
    db = _Table(lambda b: RuntimeError("Server disconnected")
                if b[0]["sector"] == "Financial Services" else None)
    _wired(monkeypatch, db, _TWO_SECTORS)
    ledger = _real_claim_ledger(monkeypatch)

    async def body():   # main.py's `_benchmarks` closure
        return await ibs.get_industry_benchmark_service().recompute_all(skip_if_fresh_hours=24)

    settled = await m._run_claimed_phase(m.JOB_INDUSTRY_BENCHMARK_QUARTERLY, "phase", body)
    assert settled is False
    assert ledger["finished"]["success"] is False
    assert ledger["finished"]["error"].startswith("IndustryBenchmarkRecomputeIncomplete:")
    assert "Financial Services" in ledger["finished"]["error"]


@pytest.mark.asyncio
async def test_a_healthy_run_still_settles_the_quarterly_claim(monkeypatch):
    """Mutation twin of the above."""
    _wired(monkeypatch, _Table(), _TWO_SECTORS)
    ledger = _real_claim_ledger(monkeypatch)

    async def body():
        return await ibs.get_industry_benchmark_service().recompute_all(skip_if_fresh_hours=24)

    assert await m._run_claimed_phase(m.JOB_INDUSTRY_BENCHMARK_QUARTERLY, "phase", body) is True
    assert ledger["finished"]["success"] is True and ledger["finished"]["error"] is None


@pytest.mark.asyncio
async def test_a_partial_ttm_run_raises_incomplete_too(monkeypatch):
    db = _Table(lambda b: RuntimeError("Server disconnected")
                if b[0]["sector"] == "Financial Services" else None)
    svc, _ = _wired(monkeypatch, db, _TWO_SECTORS)

    async def ttm_values(_tc, _sem, **_k):
        return {"pe_ratio": [10.0, 11.0, 12.0, 13.0, 14.0]}

    monkeypatch.setattr(svc, "_industry_ttm_values", ttm_values)
    with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
        await svc.recompute_all_ttm(skip_if_fresh_hours=24)
    assert info.value.failed_sectors == ["Financial Services"]
    assert "TTM" in str(info.value)
    assert svc._ttm_sector_is_fresh("Technology", 24) is True
    assert svc._ttm_sector_is_fresh("Financial Services", 24) is False


def test_the_freshness_probe_logs_why_it_said_no(caplog):
    class _Broken:
        def table(self, _n):
            raise RuntimeError("h2 connection reset")

    svc = _svc()
    svc.supabase = _Broken()
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        assert svc._sector_is_fresh("Technology", 24) is False
    assert any("freshness probe for Technology (annual) failed (RuntimeError: h2"
               in r.getMessage() for r in caplog.records)

    db = _Table()
    db.rows[("Technology", "", "roe", "annual", "2025")] = {
        "sector": "Technology", "industry": "", "metric_name": "roe", "period_type": "annual",
        "period_label": "2025", "computed_at": "not-a-date",
    }
    svc.supabase = db
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        assert svc._sector_is_fresh("Technology", 24) is False
    assert any("unreadable computed_at 'not-a-date'" in r.getMessage() for r in caplog.records)


# ═══ B5 — 429 back-off and fetch-failure counting ═══════════════════════════════════════


@pytest.fixture
def sleeps(monkeypatch):
    """Record every back-off sleep instead of waiting it out."""
    recorded: List[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *a, **k):
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(sbs.asyncio, "sleep", fake_sleep)
    # The 429 breaker is process-wide state: start every test closed, and restore it after.
    monkeypatch.setattr(sbs, "_exhausted_in_a_row", 0)
    return recorded


def _flaky(failures: List[Exception], result: Any = "ok"):
    calls = {"n": 0}

    async def fn(*_a, **_k):
        calls["n"] += 1
        if failures:
            raise failures.pop(0)
        return result

    return fn, calls


@pytest.mark.asyncio
async def test_a_429_is_retried_then_succeeds(sleeps, monkeypatch):
    monkeypatch.setattr(sbs.random, "uniform", lambda a, b: 0.0)
    fn, calls = _flaky([FMPRateLimitException("429"), FMPRateLimitException("429")])
    assert await sbs.call_with_rate_limit_retry(fn, "AAPL") == "ok"
    assert calls["n"] == 3
    assert sleeps == [2.0, 4.0]                    # exponential, no Retry-After


@pytest.mark.asyncio
@pytest.mark.parametrize("header, expected", [
    ("7", 7.0), ("0", 0.0), ("12.5", 12.5), ("999", 30.0), ("-5", 0.0),
    ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0),       # an HTTP date in the past
])
async def test_retry_after_is_honoured_and_clamped(sleeps, header, expected):
    fn, _ = _flaky([FMPRateLimitException("429", retry_after=header)])
    await sbs.call_with_rate_limit_retry(fn)
    assert sleeps == [expected]


@pytest.mark.parametrize("header", [None, "", "soon", "nan", "inf", "-inf"])
def test_an_unreadable_retry_after_falls_back_to_the_exponential_delay(header):
    assert sbs._retry_after_seconds(header) is None


def test_an_http_date_retry_after_in_the_future_counts_down():
    when = datetime.now(timezone.utc) + timedelta(seconds=20)
    secs = sbs._retry_after_seconds(when.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    assert 17.0 <= secs <= 20.0


@pytest.mark.asyncio
async def test_a_429_storm_is_bounded_then_reraised(sleeps):
    """Round 3 (P3-2): the burst retries, then at most RATE_LIMIT_LOCKOUT_WINDOWS shared
    windows (~60 s each, covering FMP's per-minute quota) — then the 429 is re-raised. The
    shared-window behaviour itself: test_benchmark_producer_round4_rate_limit_window.py."""
    fn, calls = _flaky([FMPRateLimitException("429") for _ in range(10)])
    with pytest.raises(FMPRateLimitException):
        await sbs.call_with_rate_limit_retry(fn)
    assert calls["n"] == 1 + sbs.RATE_LIMIT_MAX_RETRIES + sbs.RATE_LIMIT_LOCKOUT_WINDOWS == 7
    burst, windows = sleeps[:3], sleeps[3:]
    assert len(sleeps) == 6 and all(0 < s <= sbs.RATE_LIMIT_MAX_DELAY_SECONDS for s in burst)
    assert all(55.0 < s <= sbs.RATE_LIMIT_WINDOW_SECONDS for s in windows)


@pytest.mark.asyncio
async def test_a_quota_lockout_trips_the_breaker_so_the_run_fails_fast(sleeps, monkeypatch, caplog):
    """A sustained 429 (the quota spent, not a burst) must not stretch a ~47k-call run into
    hours of back-off past its claim's stale window: after RATE_LIMIT_BREAKER_THRESHOLD
    calls in a row ran out of retries AND RATE_LIMIT_LOCKOUT_WINDOWS shared windows passed
    with no success (round 3, P3-2), a 429 fails at once — until a call succeeds."""
    monkeypatch.setattr(sbs, "RATE_LIMIT_BREAKER_THRESHOLD", 3)
    with caplog.at_level(logging.INFO, logger=sbs.logger.name):
        # Calls 1 and 2 each wait out their own 3 windows (one call past its retries is not
        # an account-wide lockout); call 3 is the third in a row: the breaker opens.
        for expected in (7, 7, 4):
            fn, calls = _flaky([FMPRateLimitException("429") for _ in range(9)])
            with pytest.raises(FMPRateLimitException):
                await sbs.call_with_rate_limit_retry(fn)
            assert calls["n"] == expected
        assert len(sleeps) == 6 + 6 + 3
        opened = [r for r in caplog.records if "quota lockout" in r.getMessage()]
        assert len(opened) == 1 and opened[0].levelno == logging.WARNING

        # Open: the next 429 is raised on the first attempt, with no sleep.
        fn, calls = _flaky([FMPRateLimitException("429") for _ in range(9)])
        with pytest.raises(FMPRateLimitException):
            await sbs.call_with_rate_limit_retry(fn)
        assert calls["n"] == 1 and len(sleeps) == 15

        # A 5xx neither closes nor counts; a success closes it.
        fn, _ = _flaky([FMPUnavailableException("503")])
        with pytest.raises(FMPUnavailableException):
            await sbs.call_with_rate_limit_retry(fn)
        fn, _ = _flaky([])
        assert await sbs.call_with_rate_limit_retry(fn) == "ok"
        assert sbs._exhausted_in_a_row == 0
        assert any("answers again" in r.getMessage() for r in caplog.records)

        fn, calls = _flaky([FMPRateLimitException("429")])
        assert await sbs.call_with_rate_limit_retry(fn) == "ok"   # retried again
        assert calls["n"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [FMPUnavailableException("503"), ValueError("bad"), RuntimeError("x")])
async def test_other_failures_are_not_retried(sleeps, exc):
    fn, calls = _flaky([exc])
    with pytest.raises(type(exc)):
        await sbs.call_with_rate_limit_retry(fn)
    assert calls["n"] == 1 and sleeps == []


class _StormFMP:
    """Answers like FMP. `storm[ticker]` = how many 429s each of that ticker's calls gets
    before it answers (a huge number = the storm outlives the retries). `down` tickers
    raise a 5xx; `empty` tickers answer [] everywhere; `junk` tickers answer a dict."""

    def __init__(self, storm=None, down=(), empty=(), junk=()):
        self.storm = dict(storm or {})
        self.down, self.empty, self.junk = set(down), set(empty), set(junk)
        self.seen: Dict[tuple, int] = {}

    def __getattr__(self, name):
        if not name.startswith("get_"):
            raise AttributeError(name)

        async def call(ticker, *a, **k):
            key = (name, ticker, k.get("period"))
            self.seen[key] = self.seen.get(key, 0) + 1
            if self.seen[key] <= self.storm.get(ticker, 0):
                raise FMPRateLimitException(f"429 {name}", retry_after=None)
            if ticker in self.down:
                raise FMPUnavailableException(f"503 {name}")
            if ticker in self.junk:
                return {"Error Message": "Limit Reach"}
            if ticker in self.empty:
                return []
            n = int(ticker[1:]) if ticker[1:].isdigit() else 0
            if name == "get_financial_ratios" and k.get("period") == "annual":
                return [{"date": "2024-12-31", "grossProfitMargin": 0.40 + n / 100}]
            if name == "get_ratios_ttm":
                return [{"grossProfitMarginTTM": 0.40 + n / 100}]
            if name == "get_key_metrics_ttm":
                return [{"returnOnEquityTTM": 0.10 + n / 100}]
            return []

        return call


@pytest.mark.asyncio
async def test_fetch_company_data_lists_what_failed_and_keeps_what_answered(sleeps):
    svc = _sector_svc(_StormFMP(storm={"A1": 99}))
    data = await svc._fetch_company_data("A1", 3, 4)
    assert data[sbs.FETCH_ERRORS_KEY] == ["rate_limited"] * 10
    assert all(data[k] == [] for k in data
               if k not in (sbs.FETCH_ERRORS_KEY, sbs.FETCH_FAILED_CALLS_KEY))

    svc = _sector_svc(_StormFMP(storm={"A2": 2}))      # two 429s, then FMP answers
    data = await svc._fetch_company_data("A2", 3, 4)
    assert data[sbs.FETCH_ERRORS_KEY] == []
    assert data["ratios_annual"] == [{"date": "2024-12-31", "grossProfitMargin": pytest.approx(0.42)}]

    svc = _sector_svc(_StormFMP(junk={"A3"}))
    # Review round 5 (P4-2): a 200 whose body is not a list is a refusal, not transient.
    assert (await svc._fetch_company_data("A3", 3, 4))[sbs.FETCH_ERRORS_KEY] == ["refused"] * 10


def test_one_outcome_per_ticker_most_telling_kind_first():
    c = ibs._new_counts()
    ibs._record_fetch_outcome(c, ["error", "unavailable", "rate_limited"], True)
    ibs._record_fetch_outcome(c, ["error", "unavailable"], True)
    ibs._record_fetch_outcome(c, ["error"], False)
    ibs._record_fetch_outcome(c, ["something-new"], True)       # unknown kind → "error"
    ibs._record_fetch_outcome(c, [], False)                     # answered, nothing in it
    ibs._record_fetch_outcome(c, [], True)                      # ok
    ibs._record_fetch_outcome(None, ["error"], False)           # no tally: a no-op
    assert c == {"tickers": 6, "rate_limited": 1, "unavailable": 1, "error": 2, "refused": 0,
                 "empty": 1, "lost_rate_limited": 1, "lost_unavailable": 1}


def _bench_universe(groups: Dict[str, List[str]], sector="Technology") -> List[Dict[str, Any]]:
    return [
        {"industry": ind, "sector": sector,
         "market_caps": {t: 1.0e10 - i for i, t in enumerate(tickers)}}
        for ind, tickers in groups.items()
    ]


def _fmp_wired(monkeypatch, fmp, universe, db=None):
    db = db or _Table()
    svc = _svc()
    svc.supabase = db
    svc._sb = _sector_svc(fmp)
    svc._sb.supabase = db
    svc._fmp = fmp
    monkeypatch.setattr(ibs, "_fetch_benchmark_universe", lambda: [dict(e) for e in universe])
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)
    monkeypatch.setattr(ibs, "BATCH_DELAY_SECONDS", 0)
    return svc, db


@pytest.mark.asyncio
async def test_a_429_storm_that_relents_loses_no_company(monkeypatch, sleeps, caplog):
    tickers = [f"S{i}" for i in range(6)]
    fmp = _StormFMP(storm={t: 2 for t in tickers})
    svc, db = _fmp_wired(monkeypatch, fmp, _bench_universe({"Semis": tickers}))
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["tickers_fetched"] == 6 and summary["fetch_failures"] == 0
    assert summary["fetch_failures_by_kind"] == {"rate_limited": 0, "unavailable": 0, "error": 0,
                                                 "refused": 0}
    assert len(sleeps) == 6 * 10 * 2                 # every call backed off twice
    row = db.rows[("Technology", "Semis", "gross_margin", "annual", "2024")]
    assert row["sample_size"] == 6                   # pre-fix: the 429s dropped all six
    assert not [r for r in caplog.records if "above the warning line" in r.getMessage()]


@pytest.mark.asyncio
async def test_failures_are_counted_per_industry_and_the_worst_are_named(monkeypatch, sleeps, caplog):
    healthy = [f"H{i}" for i in range(20)]
    stormed = [f"R{i}" for i in range(8)]
    partly_down = [f"D{i}" for i in range(6)]
    fmp = _StormFMP(
        storm={t: 99 for t in stormed[:4]},          # 4 of 8 outlive the retries (50%)
        down=partly_down[:1],                        # 1 of 6 is down (17%, under 25%)
        empty=["H0"],                                # one healthy ticker answers nothing
    )
    universe = _bench_universe({"Healthy": healthy, "Stormed": stormed, "PartlyDown": partly_down})
    svc, _ = _fmp_wired(monkeypatch, fmp, universe)

    # Round 3 (P3-1): Stormed lost 50% of an 8-ticker industry to failures, so Technology
    # does not settle — the counts ride on the exception's summary.
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await svc.recompute_all(skip_if_fresh_hours=24)
    summary = info.value.summary
    assert info.value.lossy_sectors == ["Technology"]

    assert summary["tickers_fetched"] == 34
    assert summary["fetch_failures"] == 5
    assert summary["fetch_failures_by_kind"] == {"rate_limited": 4, "unavailable": 1, "error": 0,
                                                 "refused": 0}
    assert summary["fetch_empty"] == 1
    assert summary["industries_with_failures"] == 2
    warns = [r.getMessage() for r in caplog.records if "above the warning line" in r.getMessage()]
    assert len(warns) == 1
    assert "[fiscal run]" in warns[0] and "5 of 34 tickers failed" in warns[0]
    assert "Technology / Stormed 4/8 (50%: 4 rate-limited" in warns[0]
    assert "PartlyDown" not in warns[0] and "Healthy" not in warns[0]


@pytest.mark.asyncio
async def test_a_few_failures_under_both_lines_log_no_warning(monkeypatch, sleeps, caplog):
    tickers = [f"T{i}" for i in range(50)]
    fmp = _StormFMP(down=["T0"])                     # 1 of 50 = 2%, not MORE than 2%
    svc, _ = _fmp_wired(monkeypatch, fmp, _bench_universe({"Big": tickers}))
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["fetch_failures"] == 1 and summary["industries_with_failures"] == 1
    assert not [r for r in caplog.records if "above the warning line" in r.getMessage()]


@pytest.mark.asyncio
async def test_an_empty_industry_is_counted_and_flagged_but_not_as_a_failure(monkeypatch, sleeps, caplog):
    funds = [f"F{i}" for i in range(5)]                # e.g. funds in the universe
    tiny = ["X0", "X1"]                                 # < MIN_SAMPLE_SIZE: never flagged
    good = [f"G{i}" for i in range(10)]
    fmp = _StormFMP(empty=funds + tiny)
    universe = _bench_universe({"Funds": funds, "Tiny": tiny, "Good": good})
    svc, _ = _fmp_wired(monkeypatch, fmp, universe)
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["fetch_failures"] == 0 and summary["fetch_empty"] == 7
    assert summary["industries_with_failures"] == 0
    (warn,) = [r.getMessage() for r in caplog.records if "above the warning line" in r.getMessage()]
    assert ("Technology / Funds 5/5 (100%: 0 rate-limited, 0 unavailable, 0 refused, 0 other, "
            "5 empty)") in warn
    assert "Tiny" not in warn


@pytest.mark.asyncio
async def test_an_outage_names_its_cause_before_the_nothing_written_refusal(monkeypatch, sleeps, caplog):
    tickers = [f"O{i}" for i in range(6)]
    svc, _ = _fmp_wired(monkeypatch, _StormFMP(down=tickers), _bench_universe({"Oil": tickers}))
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeSkipped):
            await svc.recompute_all(skip_if_fresh_hours=24)
    messages = [r.getMessage() for r in caplog.records]
    tally = next(i for i, msg in enumerate(messages) if "above the warning line" in msg)
    refusal = next(i for i, msg in enumerate(messages) if "recompute SKIPPED" in msg)
    assert tally < refusal
    assert "6 of 6 tickers failed (0 rate-limited after retries, 6 unavailable" in messages[tally]


@pytest.mark.asyncio
async def test_the_ttm_fan_out_retries_429s_and_counts_failures(monkeypatch, sleeps, caplog):
    ok = [f"K{i}" for i in range(6)]
    stormed = [f"Q{i}" for i in range(5)]
    fmp = _StormFMP(storm={**{t: 1 for t in ok}, **{t: 99 for t in stormed}})
    universe = _bench_universe({"Okay": ok, "Stormed": stormed})
    svc, db = _fmp_wired(monkeypatch, fmp, universe)
    # Round 3 (P3-1): 5 of Technology's 11 tickers failed — the sector does not settle.
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await svc.recompute_all_ttm(skip_if_fresh_hours=24)
    summary = info.value.summary
    assert info.value.lossy_sectors == ["Technology"]
    assert summary["tickers_fetched"] == 11
    assert summary["fetch_failures_by_kind"]["rate_limited"] == 5
    assert db.rows[("Technology", "Okay", "gross_margin", "ttm", "TTM")]["sample_size"] == 6
    assert any("[ttm run]" in r.getMessage() and "Technology / Stormed 5/5" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_ttm_classifies_each_ticker(sleeps):
    svc = _svc()
    sem = asyncio.Semaphore(2)
    counts = ibs._new_counts()
    for fmp, ticker in (
        (_StormFMP(), "A1"),                     # ok
        (_StormFMP(down={"A2"}), "A2"),          # unavailable
        (_StormFMP(empty={"A3"}), "A3"),         # empty
        (_StormFMP(junk={"A4"}), "A4"),          # a dict body → refused (round 5, P4-2)
        (_StormFMP(storm={"A5": 99}), "A5"),     # 429 outlived the retries
    ):
        svc._fmp = fmp
        await svc._fetch_ttm(ticker, sem, counts=counts)
    assert counts == {"tickers": 5, "rate_limited": 1, "unavailable": 1, "error": 0, "refused": 1,
                      "empty": 1, "lost_rate_limited": 1, "lost_unavailable": 1}


@pytest.mark.asyncio
async def test_the_industries_only_paths_report_their_counts(monkeypatch, sleeps):
    tickers = [f"V{i}" for i in range(6)]
    svc, _ = _fmp_wired(monkeypatch, _StormFMP(down=tickers[:1]),
                        _bench_universe({"Validate": tickers}))
    fiscal = await svc.recompute_all(industries=["Validate"], dry_run=True)
    ttm = await svc.recompute_all_ttm(industries=["Validate"], dry_run=True)
    for summary in (fiscal, ttm):
        assert summary["tickers_fetched"] == 6 and summary["fetch_failures"] == 1
