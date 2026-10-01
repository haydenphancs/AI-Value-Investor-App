"""Earnings round 2 (2026-09-30): a late fiscal-Q4 release at day 98-110 (NT 10-K).

R0. A December filer that releases Q4 ~100 days after year end lands past both of the
first pass's windows: the [7, 80]-day announcement window and the late window, which ends
7 days after the implied next period end (~day 98). Phase A gave Q4 nothing; the new
just-reported synthesis (Phase A2) then paired that release with the Q1 ESTIMATE, so Q1 —
which had not reported — rendered as REPORTED with Q4's numbers, the real pending Q1 date
was dropped as "stale", the fake 9-day lag dragged the projection median, and the build was
persisted for 24h and counted in a report's beat/miss record.

A release at day 98-110 is genuinely ambiguous: a late Q4, or a bank's 13-day Q1. Dates
alone cannot decide, so:
  * a row the UNMATCHED newest quarter could still own is never synthesized as the next
    quarter unless the evidence says it IS the next quarter's;
  * evidence that it is the earlier quarter's (its consensus matches that quarter's
    analyst average, or the next quarter's own release is listed >21 days later) gives it
    to the earlier quarter — which also fixes the pre-existing variant where Q1's income
    row exists and Q1 took Q4's release;
  * a bank's fast Q1 release whose consensus matches Q1 still becomes Q1 (no over-block).
Hermetic: FMP is faked; dates are built relative to today.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.earnings_service import (
    EarningsService,
    _assign_announcements,
    _late_release_owner,
)

_TODAY = datetime.now(timezone.utc).date()


def _d(days_from_today: int) -> str:
    return (_TODAY + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


def _dt(days_from_today: int) -> datetime:
    return datetime.strptime(_d(days_from_today), "%Y-%m-%d")


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


def _inc(date_, period, fy, eps=0.3, revenue=1e9):
    return {"date": date_, "period": period, "fiscalYear": fy, "epsDiluted": eps,
            "eps": eps, "revenue": revenue, "reportedCurrency": "USD"}


def _q(quarters, fiscal_date):
    return next((q for q in quarters if q.fiscal_date == fiscal_date), None)


# Q3 ended T-211 (released T-170, lag 41); Q4 ended T-120 and is the NEWEST income row;
# Q4 is released at T-20 = day 100 (NT 10-K); Q1 ended T-29 and has not reported.
_Q3, _Q4, _Q1, _Q2 = _d(-211), _d(-120), _d(-29), _d(62)
_INCOME = [_inc(_Q3, "Q3", 2025, eps=0.2), _inc(_Q4, "Q4", 2025, eps=0.3)]
_Q3_RELEASE = {"date": _d(-170), "epsActual": 0.30, "epsEstimated": 0.28}
_LATE_Q4 = {"date": _d(-20), "epsActual": 0.40, "epsEstimated": 0.35}


@pytest.mark.asyncio
async def test_late_q4_is_not_synthesized_as_the_unreported_q1():
    """The review's exact repro: the pending Q1 row (T+16) is listed. Q1 stays pending,
    its real date survives as Confirmed, and Q4 gets its own release back."""
    estimates = [{"date": _Q4, "epsAvg": 0.36}, {"date": _Q1, "epsAvg": 0.12},
                 {"date": _Q2, "epsAvg": 0.20}]
    feed = [_Q3_RELEASE, _LATE_Q4, {"date": _d(16), "epsActual": None, "epsEstimated": 0.12}]
    resp = await _svc(_FakeFMP(_INCOME, estimates, feed))._build_earnings("NTFL")

    q1 = _q(resp.eps_quarters, _Q1)
    assert q1 is not None and q1.actual_value is None, "Q1 has not reported"
    assert q1.estimate_value == pytest.approx(0.12)
    assert 0.40 not in [q.actual_value for q in resp.eps_quarters if q.fiscal_date != _Q4]
    q4 = _q(resp.eps_quarters, _Q4)
    assert (q4.actual_value, q4.estimate_value) == (0.40, 0.35)
    assert q4.surprise_percent == pytest.approx(14.29) and q4.has_estimate is True
    assert resp.next_earnings_date.date == _d(16)
    assert resp.next_earnings_date.is_confirmed is True


@pytest.mark.asyncio
async def test_ambiguous_late_release_with_no_evidence_synthesizes_nothing():
    """No later row, and both quarters share one consensus: the row could be either
    quarter's, so neither the fabricated Q1 nor a guessed Q4 surprise is shown."""
    estimates = [{"date": _Q4, "epsAvg": 0.35}, {"date": _Q1, "epsAvg": 0.35},
                 {"date": _Q2, "epsAvg": 0.35}]
    resp = await _svc(_FakeFMP(_INCOME, estimates, [_Q3_RELEASE, _LATE_Q4]))._build_earnings("NTFL")

    q1 = _q(resp.eps_quarters, _Q1)
    assert q1 is not None and q1.actual_value is None
    q4 = _q(resp.eps_quarters, _Q4)
    assert q4.actual_value == pytest.approx(0.3)        # GAAP, no announcement claimed
    assert q4.surprise_percent is None and q4.has_estimate is False
    reported = [q.fiscal_date for q in resp.eps_quarters if q.actual_value is not None]
    assert reported == [_Q3, _Q4]


@pytest.mark.asyncio
async def test_consensus_match_alone_gives_the_late_release_to_q4():
    estimates = [{"date": _Q4, "epsAvg": 0.35}, {"date": _Q1, "epsAvg": 0.12}]
    resp = await _svc(_FakeFMP(_INCOME, estimates, [_Q3_RELEASE, _LATE_Q4]))._build_earnings("NTFL")
    q4 = _q(resp.eps_quarters, _Q4)
    assert (q4.actual_value, q4.estimate_value) == (0.40, 0.35)
    assert _q(resp.eps_quarters, _Q1).actual_value is None


@pytest.mark.asyncio
async def test_bank_fast_q1_release_is_still_synthesized_as_q1():
    """Over-block twin: Q4's own row is missing from the feed and Q1 is released 13 days
    after it closed (104 days after Q4). Its consensus matches Q1, and the next listed row
    is Q2's (not inside Q1's window) — so the just-reported Q1 still shows (#24)."""
    estimates = [{"date": _Q4, "epsAvg": 0.95, "revenueAvg": 20e9},
                 {"date": _Q1, "epsAvg": 1.05, "revenueAvg": 22e9},
                 {"date": _Q2, "epsAvg": 1.15, "revenueAvg": 23e9}]
    feed = [
        {"date": _d(-198), "epsActual": 0.9, "epsEstimated": 0.88},           # Q3, lag 13
        {"date": _d(-16), "epsActual": 1.10, "epsEstimated": 1.05,
         "revenueActual": 22.4e9, "revenueEstimated": 22e9},                    # Q1, 104 d
        {"date": _d(75), "epsActual": None, "epsEstimated": 1.15},             # Q2 pending
    ]
    income = [_inc(_Q3, "Q3", 2025, eps=0.8), _inc(_Q4, "Q4", 2025, eps=0.9)]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("BANK")

    q1 = _q(resp.eps_quarters, _Q1)
    assert q1.actual_value == pytest.approx(1.10)
    assert q1.surprise_percent == pytest.approx(4.76)
    q4 = _q(resp.eps_quarters, _Q4)
    assert q4.actual_value == pytest.approx(0.9) and q4.has_estimate is False


@pytest.mark.asyncio
async def test_q1_income_row_present_q4_keeps_its_day_100_release():
    """Variant 2 (pre-existing, #27 at 100 days): Q1's own release follows 35 days later,
    so the day-100 row is Q4's and Q1 gets its real -16.67%."""
    income = [_inc("2025-09-30", "Q3", 2025, eps=0.2), _inc("2025-12-31", "Q4", 2025, eps=0.3),
              _inc("2026-03-31", "Q1", 2026, eps=0.05)]
    feed = [
        {"date": "2025-11-14", "epsActual": 0.30, "epsEstimated": 0.28},
        {"date": "2026-04-10", "epsActual": 0.40, "epsEstimated": 0.35},   # Q4, day 100
        {"date": "2026-05-15", "epsActual": 0.10, "epsEstimated": 0.12},   # Q1
    ]
    resp = await _svc(_FakeFMP(income, [], feed))._build_earnings("SMCO")
    q4, q1 = _q(resp.eps_quarters, "2025-12-31"), _q(resp.eps_quarters, "2026-03-31")
    assert (q4.actual_value, q4.estimate_value) == (0.40, 0.35)
    assert (q1.actual_value, q1.estimate_value) == (0.10, 0.12)
    assert q1.surprise_percent == pytest.approx(-16.67)


def test_existing_pins_still_hold_without_evidence():
    """A lone day-104 row is Q1's fast release, never Q4's (no estimates, no later row)."""
    income = [_inc("2025-12-31", "Q4", 2025)]
    assert _assign_announcements(income, [{"date": "2026-04-14", "epsActual": 0.1}]) == {}
    # With Q1's income row present and only one release listed, Q1 keeps it.
    income = [_inc("2025-12-31", "Q4", 2025), _inc("2026-03-31", "Q1", 2026)]
    q1_release = {"date": "2026-04-10", "epsActual": 0.1}
    assert _assign_announcements(income, [q1_release]) == {"2026-03-31": q1_release}


# ── The owner decision itself ───────────────────────────────────────────────


def _owner(rec, ec_rows, est_this=None, est_next=None, next_reported=False):
    pe = _dt(-120)
    return _late_release_owner(
        rec, pe, pe + timedelta(days=91), ec_rows, est_this, est_next, next_reported,
    )


def test_owner_conflicting_evidence_is_ambiguous():
    """Consensus says Q1, but Q1's own release is also listed later: refuse to guess."""
    rec = {"date": _d(-20), "epsActual": 0.4, "epsEstimated": 0.12}
    later = {"date": _d(16), "epsActual": None}
    assert _owner(rec, [rec, later], {"epsAvg": 0.35}, {"epsAvg": 0.12}) is None
    assert _owner(rec, [rec], {"epsAvg": 0.35}, {"epsAvg": 0.12}) == "next"


def test_owner_reschedule_duplicate_is_not_evidence():
    """A second row only days after the ambiguous one is a duplicate, not Q1's release."""
    rec = {"date": _d(-20), "epsActual": 0.4}
    dup = {"date": _d(-12), "epsActual": None}
    assert _owner(rec, [rec, dup]) is None


def test_owner_needs_a_reported_later_row_when_the_next_quarter_has_reported():
    rec = {"date": _d(-20), "epsActual": 0.4}
    pending = {"date": _d(16), "epsActual": None}
    assert _owner(rec, [rec, pending], next_reported=True) is None
    assert _owner(rec, [rec, {**pending, "epsActual": 0.1}], next_reported=True) == "this"


@pytest.mark.parametrize(
    "row_est, this_avg, next_avg, expected",
    [
        (0.35, 0.35, 0.12, "this"),
        (0.12, 0.35, 0.12, "next"),
        (0.35, 0.35, 0.35, None),     # identical consensus: no signal
        (0.24, 0.35, 0.12, None),     # halfway: no signal
        (0.50, 0.35, 0.12, None),     # nearer Q4 but 43% off it: no signal
        (None, 0.35, 0.12, None),
        ("NaN", 0.35, 0.12, None),
    ],
)
def test_owner_consensus_vote(row_est, this_avg, next_avg, expected):
    rec = {"date": _d(-20), "epsActual": 0.4, "epsEstimated": row_est}
    assert _owner(rec, [rec], {"epsAvg": this_avg}, {"epsAvg": next_avg}) == expected


def test_owner_eps_and_revenue_disagreeing_is_no_signal():
    rec = {"date": _d(-20), "epsActual": 0.4, "epsEstimated": 0.35,
           "revenueEstimated": 22e9}
    this = {"epsAvg": 0.35, "revenueAvg": 20e9}
    nxt = {"epsAvg": 0.12, "revenueAvg": 22e9}
    assert _owner(rec, [rec], this, nxt) is None
