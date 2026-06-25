"""Schedule-collision tests for the weekly TTM benchmark job.

Confirmed bug: the weekly TTM job fired at 04:00 UTC Sunday — the SAME instant as the
quarterly fiscal industry recompute (dossier base 02:00 + 120 min) on the first Sunday
of Jan/Apr/Jul/Oct — so the two FMP-heavy jobs raced 4x/year. Fixed by offsetting the
TTM run to 06:00 UTC via the pure helper `_next_weekly_ttm_run`, now unit-testable.
"""

from datetime import datetime, timedelta, timezone

from app.main import _next_weekly_ttm_run, _next_quarterly_dossier_run


def test_ttm_next_run_is_sunday_0600_utc():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)   # Wednesday
    nxt = _next_weekly_ttm_run(now)
    assert nxt.weekday() == 6                                  # Sunday
    assert (nxt.hour, nxt.minute, nxt.second) == (6, 0, 0)
    assert nxt > now


def test_ttm_sunday_before_0600_runs_same_day():
    now = datetime(2026, 6, 28, 3, 0, tzinfo=timezone.utc)    # Sunday 03:00
    assert now.weekday() == 6
    nxt = _next_weekly_ttm_run(now)
    assert nxt.date() == now.date()
    assert nxt.hour == 6


def test_ttm_sunday_after_0600_rolls_to_next_week():
    now = datetime(2026, 6, 28, 7, 0, tzinfo=timezone.utc)    # Sunday 07:00
    assert now.weekday() == 6
    nxt = _next_weekly_ttm_run(now)
    assert nxt.weekday() == 6
    assert nxt.date() > now.date()
    assert (nxt - now) >= timedelta(days=6)


def test_ttm_handles_month_and_year_boundary():
    now = datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)  # Thursday, year-end
    nxt = _next_weekly_ttm_run(now)
    assert nxt.weekday() == 6 and nxt.hour == 6
    assert nxt > now


def test_ttm_never_collides_with_quarterly_fiscal_recompute():
    # On each quarter-start Sunday the fiscal recompute runs at dossier(02:00) + 120min
    # = 04:00 UTC, with the moat-job tail up to ~05:00. The TTM run must clear that.
    for year, month in [(2026, 1), (2026, 4), (2026, 7), (2026, 10)]:
        base = datetime(year, month, 1, 0, 0, tzinfo=timezone.utc)
        dossier = _next_quarterly_dossier_run(base)           # first Sunday 02:00 UTC
        fiscal_recompute = dossier + timedelta(minutes=120)   # 04:00 UTC
        ttm = _next_weekly_ttm_run(dossier.replace(hour=0, minute=0))
        assert ttm.date() == fiscal_recompute.date()          # same Sunday
        assert (ttm - fiscal_recompute) >= timedelta(hours=2)  # clears moat tail too
