"""A calendar-quarter benchmark row is not WRITTEN until its quarter is mostly reported.

Pre-ship finding P5 (2026-09-30). Calendar-quarter rows are keyed by the calendar quarter a
period ENDS in, and the only writer is the quarterly chain (first Sunday of Jan/Apr/Jul/Oct,
a few days after a quarter closes). On 2026-10-04 the Q3'26 cells therefore held only the
off-calendar companies that had already filed a quarter ending Jul-Sep (Jan-FY retailers,
Nvidia/Cisco/Salesforce, the Aug-quarter Oracle/Nike/FedEx/Micron); at sector level that
cohort reaches n>=20, so the readers' maturity hold-back passed it, and Growth / Profit Power
/ the report overlay / index P/E showed it as the Q3'26 peer value until January.

Fix (producer side): `IndustryBenchmarkService._rows_from_values` drops a calendar-quarter
row whose quarter ended less than CALENDAR_QUARTER_MIN_AGE_DAYS (75) before the run and logs
what it held back. Annual / TTM rows are untouched. Hermetic: inline values, no FMP/Supabase.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import pytest

from app.services import industry_benchmark_service as ibs
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE

CQ = CALENDAR_QUARTER_PERIOD_TYPE
_FIVE = [0.10, 0.20, 0.30, 0.40, 0.50]          # clears MIN_SAMPLE_SIZE (5)


def _svc() -> ibs.IndustryBenchmarkService:
    # `_rows_from_values` needs no FMP client or database.
    return ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)


def _written(values, now, sector="Technology", industry="Software - Application"):
    rows = _svc()._rows_from_values(sector, industry, values, now)
    return {(r["metric_name"], r["period_type"], r["period_label"]) for r in rows}


# ── The scheduled run that triggered the finding ──────────────────────────────────

def test_the_2026_10_04_run_writes_q2_but_not_the_just_closed_q3():
    values = {
        ("gross_margin", CQ, "Q3'26"): list(_FIVE) * 5,   # n=25: would pass the n>=20 floor
        ("gross_margin", CQ, "Q2'26"): list(_FIVE),
        ("gross_margin", CQ, "Q1'26"): list(_FIVE),
        ("gross_margin", "annual", "2026"): list(_FIVE),
        ("gross_margin", "annual", "2025"): list(_FIVE),
    }
    written = _written(values, "2026-10-04T04:00:00.123456+00:00")
    # Fails on the pre-fix code: Q3'26 (4 days old, early filers only) was written.
    assert ("gross_margin", CQ, "Q3'26") not in written
    assert ("gross_margin", CQ, "Q2'26") in written
    assert ("gross_margin", CQ, "Q1'26") in written
    # The gate is calendar-quarter only: annual rows (even the current year) still land.
    assert {("gross_margin", "annual", "2026"), ("gross_margin", "annual", "2025")} <= written


def test_the_january_run_writes_the_now_complete_q3_and_holds_q4():
    values = {
        ("revenue_yoy", CQ, "Q4'26"): list(_FIVE),
        ("revenue_yoy", CQ, "Q3'26"): list(_FIVE),
    }
    written = _written(values, "2027-01-03T04:00:00+00:00")
    assert written == {("revenue_yoy", CQ, "Q3'26")}


# ── Boundaries ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("run_at, expected", [
    ("2026-09-12T23:59:59+00:00", False),   # Q2'26 ended Jun 30: 74 days → held
    ("2026-09-13T00:00:00+00:00", True),    # 75 days → written
    ("2026-06-30T12:00:00+00:00", False),   # the quarter's own last day
    ("2026-03-31T00:00:00+00:00", False),   # BEFORE the quarter ended (clock skew / bad label)
])
def test_age_threshold_is_75_days_after_the_quarter_end(run_at, expected):
    assert ibs.CALENDAR_QUARTER_MIN_AGE_DAYS == 75
    written = _written({("roe", CQ, "Q2'26"): list(_FIVE)}, run_at)
    assert (("roe", CQ, "Q2'26") in written) is expected


def test_age_is_measured_on_the_utc_date_of_the_run():
    # 2026-09-12 20:30 at -05:00 is 2026-09-13 01:30 UTC → 75 days → written.
    written = _written({("roe", CQ, "Q2'26"): list(_FIVE)}, "2026-09-12T20:30:00-05:00")
    assert ("roe", CQ, "Q2'26") in written


def test_q4_year_wrap():
    # Q4'25 ended 2025-12-31; 2026-03-16 is day 75, 2026-03-15 day 74.
    v = {("roe", CQ, "Q4'25"): list(_FIVE)}
    assert _written(v, "2026-03-15T00:00:00+00:00") == set()
    assert _written(v, "2026-03-16T00:00:00+00:00") == {("roe", CQ, "Q4'25")}


def test_a_thin_cell_is_still_dropped_by_min_sample_size_first():
    # n=4 in an old quarter: dropped by MIN_SAMPLE_SIZE, and NOT reported as "too recent".
    assert _written({("roe", CQ, "Q1'20"): [0.1, 0.2, 0.3, 0.4]}, "2026-10-04T00:00:00+00:00") == set()


# ── Malformed inputs never invent a row or crash ─────────────────────────────────

@pytest.mark.parametrize("label", ["", "Q5'26", "Q0'26", "2026", "Q3 '26", "Q3'2026", "garbage"])
def test_a_calendar_quarter_row_with_no_quarter_label_is_dropped_and_logged(label, caplog):
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        written = _written({("roe", CQ, label): list(_FIVE)}, "2026-10-04T00:00:00+00:00")
    assert written == set()
    assert any("no calendar-quarter label" in r.getMessage() for r in caplog.records)


def test_an_unparseable_run_timestamp_falls_back_to_today_not_to_no_gate(monkeypatch, caplog):
    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 4, 4, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(ibs, "datetime", _Fixed)
    values = {("roe", CQ, "Q3'26"): list(_FIVE), ("roe", CQ, "Q2'26"): list(_FIVE)}
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        written = _written(values, "not-a-timestamp")
    assert written == {("roe", CQ, "Q2'26")}
    assert any("unparseable run timestamp" in r.getMessage() for r in caplog.records)


def test_calendar_quarter_end_parser():
    assert ibs._calendar_quarter_end("Q1'26") == date(2026, 3, 31)
    assert ibs._calendar_quarter_end("Q2'06") == date(2006, 6, 30)
    assert ibs._calendar_quarter_end(" Q3'26 ") == date(2026, 9, 30)
    assert ibs._calendar_quarter_end("Q4'25") == date(2025, 12, 31)
    for bad in (None, 3, "", "Q5'26", "TTM", "2025"):
        assert ibs._calendar_quarter_end(bad) is None


# ── What was held back is logged, with the peer group ────────────────────────────

def test_held_back_quarters_are_logged_with_sector_industry_and_run_date(caplog):
    values = {
        ("roe", CQ, "Q3'26"): list(_FIVE),
        ("gross_margin", CQ, "Q3'26"): list(_FIVE),
        ("roe", CQ, "Q2'26"): list(_FIVE),
    }
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        _svc()._rows_from_values("Technology", "", values, "2026-10-04T04:00:00+00:00")
    msgs = [r.getMessage() for r in caplog.records if "not writing calendar quarter" in r.getMessage()]
    assert len(msgs) == 1                      # one line per peer group, not per metric
    assert "Technology (sector aggregate)" in msgs[0]
    assert "Q3'26" in msgs[0] and "Q2'26" not in msgs[0]
    assert "2026-10-04" in msgs[0]


def test_nothing_is_logged_when_nothing_is_held_back(caplog):
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        _svc()._rows_from_values(
            "Technology", "Software", {("roe", CQ, "Q1'26"): list(_FIVE)},
            "2026-10-04T04:00:00+00:00",
        )
    assert not [r for r in caplog.records if "calendar quarter" in r.getMessage()]


# ── End to end through a sector compute (industry rows AND the sector aggregate) ──

@pytest.mark.asyncio
async def test_compute_sector_on_the_scheduled_run_never_upserts_the_just_closed_quarter(monkeypatch):
    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 4, 4, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(ibs, "datetime", _Fixed)
    svc = _svc()
    svc._calendar_quarter_blocked = False
    upserted = []
    monkeypatch.setattr(svc, "_upsert", lambda rows: upserted.extend(rows) or len(rows))

    async def fake_values(_ticker_caps, _al, _ql):
        return {
            ("gross_margin", CQ, "Q3'26"): list(_FIVE) * 5,
            ("gross_margin", CQ, "Q2'26"): list(_FIVE),
            ("gross_margin", "annual", "2026"): list(_FIVE),
        }

    monkeypatch.setattr(svc, "_industry_value_lists", fake_values)
    n = await svc._compute_sector(
        "Technology", [("Semiconductors", [("NVDA", 1.0)]), ("Software", [("CRM", 1.0)])],
        1, 1, dry_run=False,
    )
    labels = {(r["industry"], r["period_type"], r["period_label"]) for r in upserted}
    assert n == len(upserted) == 6            # 3 groups × (Q2'26 + annual 2026)
    assert not any(pt == CQ and lab == "Q3'26" for (_i, pt, lab) in labels)
    for industry in ("Semiconductors", "Software", ""):
        assert (industry, CQ, "Q2'26") in labels
        assert (industry, "annual", "2026") in labels
