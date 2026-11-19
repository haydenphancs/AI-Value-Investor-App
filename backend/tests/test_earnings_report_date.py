"""Earnings `report_date` (2026-11-14): the day each reported quarter was ANNOUNCED.

iOS pinned every Earnings dot to its fiscal PERIOD END on the price line, so the
report-day move always drew to the right of its own dot by the reporting lag — measured
on 14 live tickers: median ~25 days, worst ~80 (CSCO: period end 2026-10-24, reported
11-12, the line ran 20 days past the Q1 '27 dot two days after the report). The backend
already paired each quarter with its announcement row; now it sends that row's day.

Rules pinned here:
  * only a REPORTED row gives a day (a pending placeholder's date is a plan);
  * the day is 7..110 days after the period end and never after today;
  * `fiscal_date` stays the period end (chat prints "period ending", the report
    collector matches it to income rows);
  * future quarters carry none (next_earnings_date is the projection);
  * report days rise strictly with the period ends — an out-of-order pair loses BOTH,
    never greedily one (iOS would otherwise fold the price line back).
Hermetic: FMP is faked; "today"-relative dates are built from the real clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.earnings import EarningsQuarterSchema
from app.services.earnings_service import (
    EarningsService,
    _drop_unordered_report_dates,
    _report_date_for,
)

_TODAY = datetime.now(timezone.utc).date()
_TODAY_STR = _TODAY.strftime("%Y-%m-%d")


def _d(days_from_today: int) -> str:
    return (_TODAY + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


class _FakeFMP:
    def __init__(self, income, estimates=None, feed=None):
        self.income, self.estimates, self.feed = income, estimates or [], feed or []

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self.income

    async def get_analyst_estimates(self, ticker, period="quarter", limit=20):
        return self.estimates

    async def get_historical_prices(self, ticker, from_date=None, to_date=None):
        return []

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        return self.feed


def _svc(fake) -> EarningsService:
    svc = EarningsService.__new__(EarningsService)
    svc.fmp = fake
    svc.supabase = None
    return svc


def _inc(date_, period, fy, eps=1.0, revenue=1e9):
    return {"date": date_, "period": period, "fiscalYear": fy, "epsDiluted": eps,
            "eps": eps, "revenue": revenue, "reportedCurrency": "USD"}


def _rel(date_, eps=1.0, eps_est=0.98, rev=1e9, rev_est=0.99e9):
    return {"date": date_, "epsActual": eps, "epsEstimated": eps_est,
            "revenueActual": rev, "revenueEstimated": rev_est}


def _q(quarters, fiscal_date):
    return next((q for q in quarters if q.fiscal_date == fiscal_date), None)


# ── _report_date_for: what counts as a report day ────────────────────────────


@pytest.mark.parametrize("lag, expected", [
    (6, None),           # inside the min lag: the PREVIOUS quarter's late release
    (7, "kept"),         # Oracle-fast is ~9-10
    (18, "kept"),        # CSCO
    (110, "kept"),       # a late 10-K at the cap
    (111, None),         # past the cap
    (-3, None),          # before the period even ended
])
def test_report_day_lag_window(lag, expected):
    period = "2026-03-31"
    day = (datetime(2026, 3, 31) + timedelta(days=lag)).strftime("%Y-%m-%d")
    got = _report_date_for(_rel(day), period, "2099-01-01", ticker="T")
    assert got == (day if expected == "kept" else None)


def test_only_a_reported_row_gives_a_day():
    pending = {"date": "2026-04-28", "epsActual": None, "epsEstimated": 1.0,
               "revenueActual": None, "revenueEstimated": 1e9}
    assert _report_date_for(pending, "2026-03-31", "2099-01-01") is None
    # Revenue alone is a reported fact too.
    rev_only = {"date": "2026-04-28", "epsActual": None, "revenueActual": 1e9}
    assert _report_date_for(rev_only, "2026-03-31", "2099-01-01") == "2026-04-28"
    # A NaN actual is not a report.
    nan = {"date": "2026-04-28", "epsActual": float("nan")}
    assert _report_date_for(nan, "2026-03-31", "2099-01-01") is None


@pytest.mark.parametrize("rec, period", [
    (None, "2026-03-31"),
    ("x", "2026-03-31"),
    ([], "2026-03-31"),
    ({"date": "bad", "epsActual": 1.0}, "2026-03-31"),
    ({"date": None, "epsActual": 1.0}, "2026-03-31"),
    ({"epsActual": 1.0}, "2026-03-31"),
    (_rel("2026-04-28"), "not-a-date"),
    (_rel("2026-04-28"), ""),
])
def test_malformed_inputs_give_no_day(rec, period):
    assert _report_date_for(rec, period, "2099-01-01") is None


def test_phase_a2_passes_its_own_wider_cap():
    # A late Q4 the A2 pairing hands to the EARLIER quarter can sit past day 110.
    from app.services.earnings_service import _A2_REPORT_MAX_LAG_DAYS
    period = "2026-03-31"
    day = (datetime(2026, 3, 31) + timedelta(days=150)).strftime("%Y-%m-%d")
    assert _report_date_for(_rel(day), period, "2099-01-01") is None
    assert _report_date_for(_rel(day), period, "2099-01-01",
                            max_lag_days=_A2_REPORT_MAX_LAG_DAYS) == day
    over = (datetime(2026, 3, 31) + timedelta(days=_A2_REPORT_MAX_LAG_DAYS + 1)).strftime("%Y-%m-%d")
    assert _report_date_for(_rel(over), period, "2099-01-01",
                            max_lag_days=_A2_REPORT_MAX_LAG_DAYS) is None


def test_a_reported_row_dated_after_today_gives_no_day():
    assert _report_date_for(_rel(_d(1)), _d(-30), _TODAY_STR) is None
    assert _report_date_for(_rel(_TODAY_STR), _d(-30), _TODAY_STR) == _TODAY_STR


def test_the_day_is_normalised_to_yyyy_mm_dd():
    rec = _rel("2026-04-28 16:05:00")
    assert _report_date_for(rec, "2026-03-31", "2099-01-01") == "2026-04-28"


# ── _drop_unordered_report_dates ─────────────────────────────────────────────


def _sq(label, fiscal, report):
    return EarningsQuarterSchema(quarter=label, actual_value=1.0, estimate_value=1.0,
                                 fiscal_date=fiscal, has_estimate=True, report_date=report)


def _days(qs):
    return [q.report_date for q in qs]


def test_clean_series_is_untouched():
    qs = [_sq("Q1", "2026-03-31", "2026-04-28"), _sq("Q2", "2026-06-30", "2026-07-29"),
          _sq("Q3", "2026-09-30", "2026-10-28")]
    _drop_unordered_report_dates(qs, ticker="T", series="eps")
    assert _days(qs) == ["2026-04-28", "2026-07-29", "2026-10-28"]


def test_an_inverted_pair_loses_both_and_nothing_else():
    qs = [_sq("Q1", "2025-12-31", "2026-01-28"),
          _sq("Q2", "2026-03-31", "2026-07-08"),     # day 99: later than Q3's own
          _sq("Q3", "2026-06-30", "2026-07-07"),
          _sq("Q4", "2026-09-30", "2026-10-28")]
    _drop_unordered_report_dates(qs, ticker="T", series="eps")
    assert _days(qs) == ["2026-01-28", None, None, "2026-10-28"]


def test_a_shared_day_clears_both_quarters():
    qs = [_sq("Q1", "2026-03-31", "2026-04-28"), _sq("Q1", "2026-03-31", "2026-04-28"),
          _sq("Q2", "2026-06-30", "2026-07-29")]
    _drop_unordered_report_dates(qs, ticker="T", series="revenue")
    assert _days(qs) == [None, None, "2026-07-29"]


def test_undated_quarters_are_ignored_not_compared():
    qs = [_sq("Q1", "2026-03-31", "2026-04-28"), _sq("Q2", "2026-06-30", None),
          _sq("Q3", "2026-09-30", "2026-10-28")]
    _drop_unordered_report_dates(qs, ticker="T", series="eps")
    assert _days(qs) == ["2026-04-28", None, "2026-10-28"]


@pytest.mark.parametrize("qs", [[], [_sq("Q1", "2026-03-31", "2026-04-28")],
                                [_sq("Q1", "2026-03-31", None)]])
def test_empty_and_single_inputs(qs):
    before = _days(qs)
    _drop_unordered_report_dates(qs, ticker="T", series="eps")
    assert _days(qs) == before


def test_not_greedy_one_early_outlier_does_not_wipe_the_rest():
    # Within the 110-day window an early day can at most overtake its next neighbour; it
    # must cost those two, never every quarter after it.
    qs = [_sq("Q1", "2026-03-31", "2026-07-15"), _sq("Q2", "2026-06-30", "2026-07-10"),
          _sq("Q3", "2026-09-30", "2026-10-28"), _sq("Q4", "2026-12-31", "2027-01-27")]
    _drop_unordered_report_dates(qs, ticker="T", series="eps")
    assert _days(qs) == [None, None, "2026-10-28", "2027-01-27"]


# ── The built response ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_phase_a_quarters_carry_their_announcement_day_on_both_series():
    income = [_inc("2026-01-24", "Q2", 2026), _inc("2026-04-25", "Q3", 2026),
              _inc("2026-07-25", "Q4", 2026)]
    feed = [_rel("2026-02-11"), _rel("2026-05-13"), _rel("2026-08-12")]
    resp = await _svc(_FakeFMP(income, [], feed))._build_earnings("CSCO")
    for series in (resp.eps_quarters, resp.revenue_quarters):
        got = {q.fiscal_date: q.report_date for q in series}
        assert got == {"2026-01-24": "2026-02-11", "2026-04-25": "2026-05-13",
                       "2026-07-25": "2026-08-12"}


@pytest.mark.asyncio
async def test_fiscal_date_stays_the_period_end():
    income = [_inc("2026-04-25", "Q3", 2026)]
    resp = await _svc(_FakeFMP(income, [], [_rel("2026-05-13")]))._build_earnings("CSCO")
    q = resp.eps_quarters[0]
    assert (q.fiscal_date, q.report_date) == ("2026-04-25", "2026-05-13")


@pytest.mark.asyncio
async def test_a_matched_pending_placeholder_gives_no_day():
    # The GAAP-fallback branch: the announcement row has no actual yet.
    income = [_inc("2026-04-25", "Q3", 2026, eps=0.9)]
    feed = [{"date": "2026-05-13", "epsActual": None, "epsEstimated": 1.0,
             "revenueActual": None, "revenueEstimated": 1e9}]
    resp = await _svc(_FakeFMP(income, [], feed))._build_earnings("XYZ")
    q = _q(resp.eps_quarters, "2026-04-25")
    assert q.actual_value == pytest.approx(0.9) and q.report_date is None
    assert all(r.report_date is None for r in resp.revenue_quarters)


@pytest.mark.asyncio
async def test_no_announcement_row_gives_no_day():
    income = [_inc("2026-04-25", "Q3", 2026, eps=0.9)]
    resp = await _svc(_FakeFMP(income, [], []))._build_earnings("XYZ")
    assert resp.eps_quarters and all(q.report_date is None for q in resp.eps_quarters)


@pytest.mark.asyncio
async def test_just_reported_quarter_without_its_10q_carries_the_release_day():
    d0, d1, d2 = _d(-130), _d(-39), _d(52)
    income = [_inc(_d(-221), "Q1", 2026), _inc(d0, "Q2", 2026, eps=1.5, revenue=1.2e10)]
    estimates = [{"date": d0, "epsAvg": 1.5, "revenueAvg": 1.2e10},
                 {"date": d1, "epsAvg": 1.70, "revenueAvg": 1.3e10},
                 {"date": d2, "epsAvg": 1.80, "revenueAvg": 1.4e10}]
    feed = [_rel(_d(-190), 1.4, 1.38), _rel(_d(-100), 1.55, 1.5, 1.2e10, 1.19e10),
            _rel(_d(-9), 1.75, 1.70, 1.35e10, 1.3e10)]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    assert _q(resp.eps_quarters, d1).report_date == _d(-9)
    assert _q(resp.revenue_quarters, d1).report_date == _d(-9)
    # The future quarter is a projection, never a report day.
    assert _q(resp.eps_quarters, d2).actual_value is None
    assert _q(resp.eps_quarters, d2).report_date is None
    assert _q(resp.revenue_quarters, d2).report_date is None


@pytest.mark.asyncio
async def test_a_quarter_reported_today_carries_today():
    d0, d1 = _d(-120), _d(-18)
    income = [_inc(d0, "Q2", 2026)]
    estimates = [{"date": d0, "epsAvg": 1.0, "revenueAvg": 1e9},
                 {"date": d1, "epsAvg": 1.0, "revenueAvg": 1e9}]
    feed = [_rel(_d(-90)), _rel(_TODAY_STR)]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    assert _q(resp.eps_quarters, d1).report_date == _TODAY_STR


@pytest.mark.asyncio
async def test_late_q4_release_keeps_days_rising():
    income = [_inc("2025-09-30", "Q3", 2025, eps=0.2), _inc("2025-12-31", "Q4", 2025, eps=0.3),
              _inc("2026-03-31", "Q1", 2026, eps=0.05)]
    feed = [{"date": "2025-11-14", "epsActual": 0.30, "epsEstimated": 0.28},
            {"date": "2026-04-02", "epsActual": 0.40, "epsEstimated": 0.35},   # Q4, day 92
            {"date": "2026-05-15", "epsActual": 0.10, "epsEstimated": 0.12}]
    resp = await _svc(_FakeFMP(income, [], feed))._build_earnings("SMCO")
    assert [q.report_date for q in resp.eps_quarters] == [
        "2025-11-14", "2026-04-02", "2026-05-15"]


@pytest.mark.asyncio
async def test_an_eps_omitted_quarter_keeps_its_revenue_day():
    # The digit-shift gate drops the EPS point; the revenue quarter keeps its report day.
    income = [_inc("2026-04-25", "Q3", 2026, eps=1.7, revenue=1.3e10)]
    feed = [_rel("2026-05-13", eps=0.169, eps_est=1.70, rev=1.3e10, rev_est=1.29e10)]
    resp = await _svc(_FakeFMP(income, [], feed))._build_earnings("XYZ")
    assert _q(resp.eps_quarters, "2026-04-25") is None
    assert _q(resp.revenue_quarters, "2026-04-25").report_date == "2026-05-13"


@pytest.mark.asyncio
async def test_a_repeated_income_row_gives_no_day_to_either_copy():
    income = [_inc("2026-01-24", "Q2", 2026), _inc("2026-04-25", "Q3", 2026),
              _inc("2026-04-25", "Q3", 2026), _inc("2026-07-25", "Q4", 2026)]
    feed = [_rel("2026-02-11"), _rel("2026-05-13"), _rel("2026-08-12")]
    resp = await _svc(_FakeFMP(income, [], feed))._build_earnings("XYZ")
    assert [q.report_date for q in resp.eps_quarters] == [
        "2026-02-11", None, None, "2026-08-12"]


@pytest.mark.asyncio
async def test_report_days_rise_strictly_in_every_series():
    income = [_inc(f"{y}-{m}", f"Q{i % 4 + 1}", 2024 + i // 4)
              for i, (y, m) in enumerate([("2024", "03-31"), ("2024", "06-30"),
                                          ("2024", "09-30"), ("2024", "12-31"),
                                          ("2025", "03-31"), ("2025", "06-30")])]
    feed = [_rel(d) for d in ("2024-04-25", "2024-07-25", "2024-10-24", "2025-02-20",
                              "2025-04-24", "2025-07-24")]
    resp = await _svc(_FakeFMP(income, [], feed))._build_earnings("XYZ")
    for series in (resp.eps_quarters, resp.revenue_quarters):
        days = [q.report_date for q in series if q.report_date]
        assert len(days) == 6 and days == sorted(set(days))


@pytest.mark.asyncio
async def test_a_day_120_late_q4_keeps_its_report_day():
    """Review finding (2026-11-14): the A2 pairing deliberately gives a day-120 Q4 release
    (NT 10-K, no income row yet) to Q4; capping its report day at 110 dropped it, and the
    move then drew beside Q1's gray dot. Same fixture as round 3's
    test_day_120_late_q4_is_not_pinned_onto_q1."""
    q3, q4, q1 = _d(-221), _d(-130), _d(-39)
    income = [_inc(_d(-312), "Q2", 2025, eps=0.2), _inc(q3, "Q3", 2025, eps=0.28)]
    estimates = [{"date": q4, "epsAvg": 0.35}, {"date": q1, "epsAvg": 0.12},
                 {"date": _d(52), "epsAvg": 0.20}]
    feed = [{"date": _d(-282), "epsActual": 0.22, "epsEstimated": 0.20},
            {"date": _d(-191), "epsActual": 0.30, "epsEstimated": 0.28},
            {"date": _d(-10), "epsActual": 0.40, "epsEstimated": 0.35}]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("NTFL")
    late = _q(resp.eps_quarters, q4)
    assert late.actual_value == pytest.approx(0.40)
    assert late.report_date == _d(-10)
    assert _q(resp.eps_quarters, q1).report_date is None
    days = [q.report_date for q in resp.eps_quarters if q.report_date]
    assert days == sorted(set(days)) and len(days) == 3
