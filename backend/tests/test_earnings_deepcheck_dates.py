"""Earnings deep-check (2026-09-30): which quarter an announcement belongs to, which
estimates are still upcoming, the next-date fallback, and forecast labels.

Each failure here put a real number under the wrong label or a reported quarter in the
"upcoming" slots:
  * #22 a reschedule's stale pending row (or a same-date pending twin) was paired with the
    quarter instead of the real result → a fake GAAP-vs-non-GAAP miss;
  * #27 a late Q4 release (92 days, two days after Q1 closed) was read as Q1's numbers;
  * #24 a quarter already ANNOUNCED showed as a gray pending estimate until FMP carried the
    10-Q, and its actual was dropped;
  * #18 with the income call failed (or a hole in history) every past estimate rendered as
    upcoming, and iOS put the two OLDEST in the "next" slots;
  * #19/#25 the next date skipped TODAY's report, showed a stale reschedule as Confirmed,
    and the fallback returned a fiscal PERIOD END (6 weeks early) then jumped a quarter;
  * #20/#65 forecast labels jumped a fiscal year for start-year-named and 52/53-week filers.
Hermetic: FMP is faked; dates that depend on "today" are built relative to it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.earnings_service import (
    EarningsService,
    _assign_announcements,
    _forecast_anchor,
    _forecast_label,
    _match_announcement,
)

_TODAY = datetime.now(timezone.utc).date()


def _d(days_from_today: int) -> str:
    return (_TODAY + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


class _FakeFMP:
    def __init__(self, income, estimates=None, feed=None, *, income_exc=None):
        self.income, self.estimates, self.feed = income, estimates or [], feed or []
        self.income_exc = income_exc

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        if self.income_exc:
            raise self.income_exc
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


def _q(quarters, fiscal_date):
    return next((q for q in quarters if q.fiscal_date == fiscal_date), None)


# ── #22 duplicates prefer the reported row ──────────────────────────────────


def test_stale_pending_row_before_the_real_one_is_not_matched():
    stale = {"date": "2026-06-04", "epsActual": None, "epsEstimated": 1.57}
    real = {"date": "2026-06-11", "epsActual": 1.58, "epsEstimated": 1.57}
    assert _match_announcement("2026-05-03", [stale, real]) is real
    # Only a placeholder in the window → it is still returned (pending behaviour kept).
    assert _match_announcement("2026-05-03", [stale]) is stale


@pytest.mark.asyncio
async def test_same_date_pending_twin_listed_last_does_not_win():
    income = [_inc("2026-05-03", "Q2", 2026, eps=1.03)]
    estimates = [{"date": "2026-05-03", "epsAvg": 1.57, "revenueAvg": 1e9}]
    feed = [
        {"date": "2026-06-11", "epsActual": 1.58, "epsEstimated": 1.57,
         "revenueActual": 1e9, "revenueEstimated": 1e9},
        {"date": "2026-06-11", "epsActual": None, "epsEstimated": 1.57,
         "revenueActual": None, "revenueEstimated": 1e9},
    ]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("AVGO")
    q = _q(resp.eps_quarters, "2026-05-03")
    assert q.actual_value == pytest.approx(1.58)
    assert q.surprise_percent == pytest.approx(0.64)


# ── #27 late Q4 release + one-to-one + MIN_LAG ──────────────────────────────


@pytest.mark.asyncio
async def test_late_q4_release_belongs_to_q4_not_q1():
    income = [_inc("2025-09-30", "Q3", 2025, eps=0.2), _inc("2025-12-31", "Q4", 2025, eps=0.3),
              _inc("2026-03-31", "Q1", 2026, eps=0.05)]
    feed = [
        {"date": "2025-11-14", "epsActual": 0.30, "epsEstimated": 0.28},
        {"date": "2026-04-02", "epsActual": 0.40, "epsEstimated": 0.35},   # Q4, 92 days
        {"date": "2026-05-15", "epsActual": 0.10, "epsEstimated": 0.12},   # Q1
    ]
    resp = await _svc(_FakeFMP(income, [], feed))._build_earnings("SMCO")
    q4, q1 = _q(resp.eps_quarters, "2025-12-31"), _q(resp.eps_quarters, "2026-03-31")
    assert (q4.actual_value, q4.estimate_value) == (0.40, 0.35)
    assert (q1.actual_value, q1.estimate_value) == (0.10, 0.12)
    assert q1.surprise_percent == pytest.approx(-16.67)


def test_assignment_is_one_to_one():
    income = [_inc("2025-09-30", "Q3", 2025), _inc("2025-12-31", "Q4", 2025),
              _inc("2026-03-31", "Q1", 2026)]
    feed = [{"date": "2025-11-14", "epsActual": 0.3}, {"date": "2026-04-02", "epsActual": 0.4},
            {"date": "2026-05-15", "epsActual": 0.1}]
    assigned = _assign_announcements(income, feed)
    dates = [r["date"] for r in assigned.values()]
    assert len(dates) == len(set(dates)) == 3


def test_q4_without_its_own_row_never_takes_q1s_release():
    income = [_inc("2025-12-31", "Q4", 2025), _inc("2026-03-31", "Q1", 2026)]
    q1_release = {"date": "2026-04-10", "epsActual": 0.1}     # 10 days after Q1 ended
    assigned = _assign_announcements(income, [q1_release])
    assert "2025-12-31" not in assigned
    assert assigned["2026-03-31"] is q1_release


def test_late_q4_with_no_next_income_row_is_bounded_by_the_implied_quarter():
    """Newest income row is Q4: Q1's fast release (104 days after Q4) must not be taken."""
    income = [_inc("2025-12-31", "Q4", 2025)]
    assert _assign_announcements(income, [{"date": "2026-04-14", "epsActual": 0.1}]) == {}
    late = {"date": "2026-04-02", "epsActual": 0.4}
    assert _assign_announcements(income, [late]) == {"2025-12-31": late}


def test_oracle_nine_day_release_still_matches():
    row = {"date": "2025-09-09", "epsActual": 1.47, "epsEstimated": 1.48}
    assert _match_announcement("2025-08-31", [row]) is row


def test_release_within_min_lag_is_not_this_quarters():
    row = {"date": "2026-04-02", "epsActual": 0.4}
    assert _match_announcement("2026-03-31", [row]) is None


# ── #24 just-reported quarter before its income row ─────────────────────────


def _s7(feed_override=None, extra_feed=()):
    d0 = _d(-130)                       # newest filed quarter
    d1 = _d(-39)                        # the quarter just announced (no income row yet)
    d2 = _d(52)                         # the next quarter
    income = [_inc(_d(-221), "Q1", 2026), _inc(d0, "Q2", 2026, eps=1.5, revenue=1.2e10)]
    estimates = [
        {"date": d0, "epsAvg": 1.5, "revenueAvg": 1.2e10},
        {"date": d1, "epsAvg": 1.70, "revenueAvg": 1.3e10},
        {"date": d2, "epsAvg": 1.80, "revenueAvg": 1.4e10},
    ]
    feed = [
        {"date": _d(-190), "epsActual": 1.4, "epsEstimated": 1.38,
         "revenueActual": 1e9, "revenueEstimated": 1e9},
        {"date": _d(-100), "epsActual": 1.55, "epsEstimated": 1.5,
         "revenueActual": 1.2e10, "revenueEstimated": 1.19e10},
        feed_override or {"date": _d(-9), "epsActual": 1.75, "epsEstimated": 1.70,
                          "revenueActual": 1.35e10, "revenueEstimated": 1.3e10},
        *extra_feed,
    ]
    return income, estimates, feed, d0, d1, d2


@pytest.mark.asyncio
async def test_announced_quarter_shows_its_actual_before_the_10q_lands():
    income, estimates, feed, d0, d1, d2 = _s7()
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")

    e1 = _q(resp.eps_quarters, d1)
    assert e1 is not None and e1.actual_value == pytest.approx(1.75)
    assert e1.surprise_percent == pytest.approx(2.94)
    assert e1.has_estimate is True
    assert e1.quarter == "Q3 '26"                      # one step after Q2 '26
    r1 = _q(resp.revenue_quarters, d1)
    assert r1.actual_value == pytest.approx(1.35e10) and r1.surprise_percent == pytest.approx(3.85)
    # No pending duplicate of the announced quarter, and the next one is still upcoming.
    assert [q.fiscal_date for q in resp.eps_quarters].count(d1) == 1
    nxt = _q(resp.eps_quarters, d2)
    assert nxt.actual_value is None and nxt.quarter == "Q4 '26"


@pytest.mark.asyncio
async def test_synthesized_revenue_outlier_is_omitted():
    """No filed revenue exists yet to corroborate the feed, so the AVGO dropped digit must
    not get a new unguarded door. Round 2 (R9): keeping the value without a surprise still
    CHARTED it (and fed the report) — the revenue row is omitted until the 10-Q lands; the
    EPS half stays, and no pending duplicate of the quarter appears."""
    income, estimates, feed, d0, d1, d2 = _s7(feed_override={
        "date": _d(-9), "epsActual": 1.75, "epsEstimated": 1.70,
        "revenueActual": 1.35e9, "revenueEstimated": 1.3e10,
    })
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    assert _q(resp.revenue_quarters, d1) is None
    assert _q(resp.eps_quarters, d1).actual_value == pytest.approx(1.75)


@pytest.mark.asyncio
async def test_eps_only_announcement_emits_eps_only():
    income, estimates, feed, d0, d1, d2 = _s7(feed_override={
        "date": _d(-9), "epsActual": 1.75, "epsEstimated": 1.70,
        "revenueActual": None, "revenueEstimated": 1.3e10,
    })
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    assert _q(resp.eps_quarters, d1).actual_value == pytest.approx(1.75)
    assert _q(resp.revenue_quarters, d1) is None


@pytest.mark.asyncio
async def test_stale_duplicate_rows_never_become_a_new_quarter():
    """A second reported row for an already-paired quarter, and an old unconsumed row
    dated before the newest filed period, are both ignored."""
    income, estimates, feed, d0, d1, d2 = _s7(extra_feed=(
        {"date": _d(-99), "epsActual": 1.55, "epsEstimated": 1.5},   # dup of d0's release
        {"date": _d(-400), "epsActual": 9.9, "epsEstimated": 9.0},   # ancient orphan
    ))
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    reported = [q for q in resp.eps_quarters if q.actual_value is not None]
    assert [q.fiscal_date for q in reported] == [_d(-221), d0, d1]
    assert 9.9 not in [q.actual_value for q in reported]


# ── #18 Phase B never resurrects a reported period as "upcoming" ────────────


@pytest.mark.asyncio
async def test_income_failure_does_not_turn_past_estimates_into_upcoming():
    estimates = [{"date": _d(-600 + 91 * i), "epsAvg": 1.0 + i / 10, "revenueAvg": 1e9}
                 for i in range(9)]
    feed = [{"date": _d(-40), "epsActual": 1.5, "epsEstimated": 1.4},
            {"date": _d(50), "epsActual": None, "epsEstimated": 1.6, "time": "amc"}]
    resp = await _svc(_FakeFMP([], estimates, feed, income_exc=RuntimeError("429")))._build_earnings("AVGO")
    assert "income" in resp.degraded
    pending = [q for q in resp.eps_quarters if q.actual_value is None]
    assert pending, "the genuinely upcoming quarters must still show"
    assert all(q.fiscal_date > _d(-40) for q in pending), [q.fiscal_date for q in pending]


@pytest.mark.asyncio
async def test_gap_in_history_is_not_emitted_as_pending():
    income = [_inc("2025-03-31", "Q1", 2025), _inc("2025-09-30", "Q3", 2025)]  # Q2 missing
    estimates = [{"date": "2025-06-30", "epsAvg": 1.0, "revenueAvg": 1e9}]
    resp = await _svc(_FakeFMP(income, estimates, []))._build_earnings("SEMI")
    assert _q(resp.eps_quarters, "2025-06-30") is None


@pytest.mark.asyncio
async def test_ended_but_unreported_quarter_stays_upcoming():
    """Cut on the last REPORTED period, never on today: Q3 ended 2 weeks ago, reports
    later this month — it is genuinely upcoming."""
    d_last = _d(-105)
    income = [_inc(d_last, "Q2", 2026)]
    estimates = [{"date": _d(-14), "epsAvg": 1.2, "revenueAvg": 1e9}]
    feed = [{"date": _d(-75), "epsActual": 1.1, "epsEstimated": 1.0}]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("CAL")
    q = _q(resp.eps_quarters, _d(-14))
    assert q is not None and q.actual_value is None


# ── #19 / #25 / #58 next earnings date ───────────────────────────────────────


def _next(ec, estimates=(), used=(), reported_through=None, lag=None):
    svc = EarningsService.__new__(EarningsService)
    return svc._find_next_earnings_date(
        list(estimates), list(ec), set(used), _d(0),
        reported_through=reported_through, announce_lag_days=lag,
    )


def test_todays_pending_report_is_the_next_date():
    nd = _next([{"date": _d(0), "epsActual": None, "time": "amc"},
                {"date": _d(70), "epsActual": None}])
    assert nd.date == _d(0) and nd.is_confirmed is True
    assert nd.timing == "After Market Close"


def test_reported_today_moves_to_the_next_row():
    nd = _next([{"date": _d(0), "epsActual": 1.2}, {"date": _d(70), "epsActual": None}])
    assert nd.date == _d(70)


def test_stale_reschedule_row_is_not_confirmed():
    # A later pending row exists: the stale leftover is skipped for it.
    nd = _next([{"date": _d(-3), "epsActual": 1.2}, {"date": _d(4), "epsActual": None},
                {"date": _d(88), "epsActual": None}])
    assert nd.date == _d(88) and nd.is_confirmed is True
    # Lone (round 2, R6/R8): never dropped to None, but never shown as "Confirmed".
    nd = _next([{"date": _d(-3), "epsActual": 1.2}, {"date": _d(4), "epsActual": None}])
    assert nd.date == _d(4) and nd.is_confirmed is False


def test_fallback_projects_the_release_not_the_period_end():
    """No pending row: an ended-but-unreported quarter (period ended 14 days ago) projects
    to period end + this ticker's lag — never the bare period end, never a quarter ahead."""
    estimates = [{"date": _d(-14)}, {"date": _d(77)}]
    nd = _next([], estimates, used=[_d(-105)], reported_through=_d(-105), lag=38)
    assert nd.date == _d(24) and nd.is_confirmed is False
    assert nd.timing == "Time Not Specified"
    # A future period: period end + lag, not the period end.
    nd = _next([], [{"date": _d(30)}], used=[_d(-61)], reported_through=_d(-61), lag=38)
    assert nd.date == _d(68)


def test_fallback_never_returns_today_or_the_past():
    estimates = [{"date": _d(-60)}, {"date": _d(-40)}]
    assert _next([], estimates, reported_through=_d(-200), lag=40) is None
    # Exactly today → not returned either.
    assert _next([], [{"date": _d(-40)}], reported_through=_d(-200), lag=40) is None


def test_fallback_without_lag_history_is_none():
    assert _next([], [{"date": _d(30)}], reported_through=_d(-60), lag=None) is None


def test_fallback_survives_malformed_estimate_dates():
    estimates = [{"date": "2026/12/31"}, {"date": None}, {}, {"date": _d(30)}]
    nd = _next([], estimates, reported_through=_d(-60), lag=30)
    assert nd is not None and nd.date == _d(60)


@pytest.mark.asyncio
async def test_build_uses_the_tickers_own_lag_when_the_feed_lists_no_upcoming_row():
    d_q1, d_q2 = _d(-200), _d(-109)
    income = [_inc(d_q1, "Q1", 2026), _inc(d_q2, "Q2", 2026)]
    feed = [{"date": _d(-160), "epsActual": 1.0, "epsEstimated": 1.0},     # lag 40
            {"date": _d(-73), "epsActual": 1.1, "epsEstimated": 1.0}]      # lag 36
    estimates = [{"date": _d(-18), "epsAvg": 1.2, "revenueAvg": 1e9}]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("THIN")
    assert resp.next_earnings_date.date == _d(-18 + 38)
    assert resp.next_earnings_date.is_confirmed is False


# ── #20 / #65 forecast labels step from the last historical label ────────────


def _labels(income, est_dates):
    anchor = _forecast_anchor(income)
    return [_forecast_label(d, anchor, {}) for d in est_dates]


def test_start_year_named_fiscal_year_hd_shape():
    income = [_inc("2025-11-02", "Q3", 2025), _inc("2026-02-01", "Q4", 2025),
              _inc("2026-05-03", "Q1", 2026), _inc("2026-08-02", "Q2", 2026)]
    assert _labels(income, ["2026-11-01", "2027-01-31", "2027-05-02"]) == [
        "Q3 '26", "Q4 '26", "Q1 '27"]


def test_end_year_named_fiscal_year_wmt_shape():
    income = [_inc("2026-04-30", "Q1", 2027), _inc("2026-07-31", "Q2", 2027)]
    assert _labels(income, ["2026-10-31", "2027-01-31", "2027-04-30"]) == [
        "Q3 '27", "Q4 '27", "Q1 '28"]


def test_december_52_53_week_spill_ko_shape():
    income = [_inc("2026-04-03", "Q1", 2026), _inc("2026-07-03", "Q2", 2026)]
    labels = _labels(income, ["2026-10-02", "2027-01-01", "2027-04-02", "2028-03-31"])
    assert labels == ["Q3 '26", "Q4 '26", "Q1 '27", "Q1 '28"]
    assert len(set(labels)) == len(labels)


def test_no_fiscal_year_falls_back_to_month_inference():
    income = [{"date": "2026-03-31", "period": "Q1"}]
    assert _forecast_anchor(income) is None
    assert _labels(income, ["2026-06-30"]) == ["Q2 '26"]


@pytest.mark.asyncio
async def test_build_forecast_labels_continue_history_without_gaps_or_repeats():
    income = [_inc("2025-11-02", "Q3", 2025), _inc("2026-02-01", "Q4", 2025),
              _inc("2026-05-03", "Q1", 2026), _inc("2026-08-02", "Q2", 2026)]
    estimates = [{"date": d, "epsAvg": 2.0, "revenueAvg": 1e9}
                 for d in ("2026-11-01", "2027-01-31", "2027-05-02")]
    feed = [{"date": "2026-08-19", "epsActual": 4.6, "epsEstimated": 4.5}]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("HD")
    labels = [q.quarter for q in resp.eps_quarters]
    assert labels[-3:] == ["Q3 '26", "Q4 '26", "Q1 '27"]
    assert len(labels) == len(set(labels))
