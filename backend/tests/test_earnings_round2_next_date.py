"""Earnings round 2 (2026-09-30): the stale-reschedule window in ``next_pending_earnings``.

R6/R8. The first fix pass skipped any pending row dated within 45 days after a reported
row, treating it as a reschedule's leftover. But 45 days is exactly the gap between a
non-accelerated filer's 10-K deadline (Mar 31) and its 10-Q deadline (May 15), and an
NT 10-K filer's two releases sit ~30 days apart. For those filers the REAL next report was
dropped: the card showed the quarter after it (~3 months out) as "Confirmed" (or nothing),
and that far date became the cache-invalidation key of all five Financials services, so
their rows were not rebuilt on the real release day.

Now: the window is 21 days (a reschedule moves a date by days to two weeks), and a lone
pending row (no later pending row to prefer) is never dropped — the earnings card shows it
UNCONFIRMED when it sits inside the window, because it may be a reschedule's leftover.
Hermetic: pure functions, no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import _earnings_common as ec
from app.services._earnings_common import (
    next_pending_earnings,
    next_pending_earnings_date,
    within_reschedule_window,
)
from app.services.earnings_service import EarningsService


# ── The window is 21 days, not 45 ───────────────────────────────────────────


def test_the_window_is_three_weeks():
    assert ec.STALE_RESCHEDULE_DAYS == 21


@pytest.mark.parametrize("today", ["2026-04-20", "2026-05-15"])
def test_non_accelerated_filer_45_day_gap_returns_the_real_next_report(today):
    """10-K deadline (Mar 31) → 10-Q deadline (May 15): exactly 45 days apart. On report
    day itself (05-15) the pending row must still be today's report (the #58 fix)."""
    rows = [{"date": "2026-03-31", "epsActual": 0.1},
            {"date": "2026-05-15"},
            {"date": "2026-08-14"}]
    assert next_pending_earnings_date(rows, today) == "2026-05-15"


def test_nt_10k_filer_30_day_gap_returns_the_real_next_report():
    rows = [{"date": "2026-04-14", "epsActual": 0.1},
            {"date": "2026-05-14"},
            {"date": "2026-08-14"}]
    assert next_pending_earnings_date(rows, "2026-04-20") == "2026-05-14"


def test_late_q4_filer_44_day_gap_with_and_without_the_following_quarter():
    rows = [{"date": "2027-03-31", "epsActual": 0.2, "epsEstimated": 0.18},
            {"date": "2027-05-14", "epsActual": None},
            {"date": "2027-08-13", "epsActual": None}]
    assert next_pending_earnings_date(rows, "2027-04-10") == "2027-05-14"
    # Only Q1 listed: it used to come back None.
    assert next_pending_earnings_date(rows[:2], "2027-04-10") == "2027-05-14"


def test_boundary_21_days_is_stale_22_is_not():
    later = {"date": "2026-12-28"}
    stale = [{"date": "2026-09-01", "epsActual": 1.0}, {"date": "2026-09-22"}, later]
    assert next_pending_earnings_date(stale, "2026-09-05") == "2026-12-28"
    fresh = [{"date": "2026-09-01", "epsActual": 1.0}, {"date": "2026-09-23"}, later]
    assert next_pending_earnings_date(fresh, "2026-09-05") == "2026-09-23"


def test_seven_day_reschedule_with_a_later_row_is_still_skipped():
    rows = [{"date": "2026-09-27", "epsActual": 1.0},
            {"date": "2026-10-04", "epsActual": None},
            {"date": "2026-12-28", "epsActual": None}]
    assert next_pending_earnings_date(rows, "2026-09-30") == "2026-12-28"


# ── A lone pending row is never dropped ─────────────────────────────────────


def test_lone_pending_row_inside_the_window_is_kept_not_none():
    rows = [{"date": "2026-09-27", "epsActual": 1.0}, {"date": "2026-10-04"}]
    rec = next_pending_earnings(rows, "2026-09-30")
    assert rec is not None and rec["date"] == "2026-10-04"
    assert within_reschedule_window(rec, rows) is True


def test_two_stale_rows_skip_the_first_and_keep_the_last():
    rows = [{"date": "2026-09-27", "epsActual": 1.0},
            {"date": "2026-10-02"}, {"date": "2026-10-04"}]
    assert next_pending_earnings_date(rows, "2026-09-30") == "2026-10-04"


def test_a_row_outside_the_window_is_not_suspect():
    rows = [{"date": "2026-03-31", "epsActual": 0.1}, {"date": "2026-05-15"}]
    assert within_reschedule_window(rows[1], rows) is False
    # Defensive: junk input never raises.
    assert within_reschedule_window({"date": None}, rows) is False
    assert within_reschedule_window({"date": "2026-05-15"}, "junk") is False


# ── Through the earnings card ────────────────────────────────────────────────

_TODAY = datetime.now(timezone.utc).date()


def _d(days: int) -> str:
    return (_TODAY + timedelta(days=days)).strftime("%Y-%m-%d")


def _next(ec_rows):
    svc = EarningsService.__new__(EarningsService)
    return svc._find_next_earnings_date([], list(ec_rows), set(), _d(0))


def test_card_shows_the_real_next_report_as_confirmed_after_a_late_q4():
    nd = _next([{"date": _d(-10), "epsActual": 0.2},
                {"date": _d(34), "epsActual": None, "time": "amc"},
                {"date": _d(125), "epsActual": None}])
    assert nd.date == _d(34) and nd.is_confirmed is True


def test_card_shows_a_lone_suspect_row_unconfirmed():
    """Reported 3 days ago; the original date (4 days ahead) is the only pending row. It is
    probably a reschedule's leftover — shown, but never as 'Confirmed'."""
    nd = _next([{"date": _d(-3), "epsActual": 1.2}, {"date": _d(4), "epsActual": None}])
    assert nd.date == _d(4)
    assert nd.is_confirmed is False


def _financials_cache_keys():
    """What each of the four services stamps as its row's report-day invalidation key.
    Health Check, Profit Power and Signal of Confidence stamp through
    `_earnings_common.next_earnings_stamp` (P20, 2026-10-01); Revenue Breakdown still
    through its own wrapper."""
    import functools

    from app.services._earnings_common import next_earnings_stamp
    from app.services.revenue_breakdown_service import _find_next_earnings_date_simple

    return [
        pytest.param(functools.partial(next_earnings_stamp, ticker="T", service=s), id=s)
        for s in ("health_check", "signal_of_confidence", "profit_power")
    ] + [pytest.param(_find_next_earnings_date_simple, id="revenue_breakdown")]


@pytest.mark.parametrize("fn", _financials_cache_keys())
def test_the_four_financials_services_key_on_the_real_next_report(fn):
    """Their Supabase rows are invalidated on this date: it must be the real release."""
    rows = [{"date": _d(-10), "epsActual": 0.2}, {"date": _d(34)}, {"date": _d(125)}]
    assert fn(rows) == _d(34)
