"""Emerging Frontiers scheduler (`app/services/theme_rotation/scheduler.py`).

Two halves:

* PURE schedule math — the first US trading day of a month, the 18:30 ET rotation anchor
  in both EDT and EST, the 7-day catch-up window (whose `run_month` must come from the ET
  calendar, never UTC), the next anchor across a December→January roll, the 18:15 ET daily
  insights slot. Checked against hand-derived instants AND against an independent NYSE
  holiday computation written here (so a wrong entry in `US_MARKET_HOLIDAYS` cannot make a
  test agree with itself).
* The ASYNC ticks and loops, with in-memory fakes for the rotation service, the insights
  service and the `notification_jobs` claim/state helpers. Every fake is signature-pinned
  against the real callable so the scheduler cannot pass an argument the real one rejects.

Plus the holiday-table coverage guards (one deterministic, one against today's date that
starts failing ~a year before the calendar runs out), the job-name ↔ migration-174 seed
parity, and an AST scan that both loops are spawned only in the Railway branch of
`main.lifespan`.

Hermetic: nothing here reaches Supabase / FMP / Gemini (backend/conftest.py would raise).
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import logging
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Set

import pytest

import app.services.notification_jobs as notification_jobs
import app.services.theme_insights_service as theme_insights_service
import app.services.theme_rotation.scheduler as scheduler
import app.services.theme_rotation.service as rotation_service
import app.utils.market_hours as market_hours
from app.services.theme_rotation.models import ThemePlan
from app.services.theme_rotation.service import RunResult
from app.utils.market_hours import ET

BACKEND = Path(__file__).resolve().parents[1]
UTC = timezone.utc
SCHED_LOGGER = "app.services.theme_rotation.scheduler"


# ── helpers ───────────────────────────────────────────────────────────────────────────


def et(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0, us: int = 0) -> datetime:
    """An ET wall-clock instant, returned in UTC (what the loops pass)."""
    return datetime(y, m, d, hh, mm, ss, us, tzinfo=ET).astimezone(UTC)


def utc(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0, us: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, us, tzinfo=UTC)


def _next_month(y: int, m: int) -> tuple[int, int]:
    return (y + 1, 1) if m == 12 else (y, m + 1)


def _prev_month(y: int, m: int) -> tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


# ── an INDEPENDENT NYSE holiday calendar (rules, not a copied table) ──────────────────


def _easter(year: int) -> date:
    """Anonymous Gregorian (Meeus/Jones/Butcher) Easter Sunday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    y, m = _next_month(year, month)
    d = date(y, m, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set[date]:
    """The ten rule-based NYSE full-day closures of `year` (no ad-hoc closures)."""
    out: set[date] = set()
    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        out.add(date(year, 1, 2))
    elif new_year.weekday() < 5:
        out.add(new_year)
    # Saturday New Year's Day: NYSE Rule 7.2 — NOT observed on the Friday before.
    out.add(_nth_weekday(year, 1, 0, 3))            # MLK
    out.add(_nth_weekday(year, 2, 0, 3))            # Presidents' Day
    out.add(_easter(year) - timedelta(days=2))      # Good Friday
    out.add(_last_weekday(year, 5, 0))              # Memorial Day
    if year >= 2022:
        out.add(_observed(date(year, 6, 19)))       # Juneteenth
    out.add(_observed(date(year, 7, 4)))            # Independence Day
    out.add(_nth_weekday(year, 9, 0, 1))            # Labor Day
    out.add(_nth_weekday(year, 11, 3, 4))           # Thanksgiving
    out.add(_observed(date(year, 12, 25)))          # Christmas
    return out


def independent_is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def table_years() -> set[int]:
    return {y for (y, _m, _d) in market_hours.US_MARKET_HOLIDAYS}


def table_holidays(year: int) -> set[date]:
    return {date(y, m, d) for (y, m, d) in market_hours.US_MARKET_HOLIDAYS if y == year}


@pytest.fixture(autouse=True)
def _fresh_observed_closures(monkeypatch):
    """`_OBSERVED_CLOSURES` is a process-wide set another test may have added to; every
    expectation below assumes only the scheduled calendar."""
    monkeypatch.setattr(market_hours, "_OBSERVED_CLOSURES", set())


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def matching(self, level: int, needle: str) -> List[logging.LogRecord]:
        return [r for r in self.records if r.levelno == level and needle in r.getMessage()]


@pytest.fixture
def sched_logs(monkeypatch):
    """Attach straight to the scheduler's logger — immune to propagation / dictConfig."""
    handler = _ListHandler()
    lg = logging.getLogger(SCHED_LOGGER)
    monkeypatch.setattr(lg, "disabled", False)
    old_level = lg.level
    lg.setLevel(logging.DEBUG)
    lg.addHandler(handler)
    try:
        yield handler
    finally:
        lg.removeHandler(handler)
        lg.setLevel(old_level)


# ═════════════════════════════════════════════════════════════════════════════════════
# 1. first_trading_day
# ═════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "year, month, expected, why",
    [
        (2026, 1, date(2026, 1, 2), "Jan 1 (Thu) is New Year's Day"),
        (2027, 1, date(2027, 1, 4), "Jan 1 (Fri) holiday, then a weekend"),
        (2026, 8, date(2026, 8, 3), "month starts on a Saturday"),
        (2026, 3, date(2026, 3, 2), "month starts on a Sunday"),
        (2026, 11, date(2026, 11, 2), "month starts on a Sunday (DST ends that day)"),
        (2027, 5, date(2027, 5, 3), "month starts on a Saturday"),
        (2025, 9, date(2025, 9, 2), "Sep 1 (Mon) is Labor Day"),
        (2025, 6, date(2025, 6, 2), "month starts on a Sunday"),
        (2026, 9, date(2026, 9, 1), "an ordinary Tuesday the 1st"),
        (2026, 10, date(2026, 10, 1), "an ordinary Thursday the 1st"),
        (2026, 7, date(2026, 7, 1), "Jul 1 (Wed) — the Jul 3 holiday is later"),
        (2027, 11, date(2027, 11, 1), "an ordinary Monday the 1st"),
    ],
)
def test_first_trading_day_known_months(year, month, expected, why):
    assert scheduler.first_trading_day(year, month) == expected, why


def test_first_trading_day_matches_an_independent_nyse_calendar_for_every_covered_month():
    mismatches = []
    for year in sorted(table_years()):
        for month in range(1, 13):
            d = date(year, month, 1)
            while not independent_is_trading_day(d):
                d += timedelta(days=1)
            got = scheduler.first_trading_day(year, month)
            if got != d:
                mismatches.append(f"{year}-{month:02d}: scheduler={got} independent={d}")
    assert not mismatches, mismatches


def test_first_trading_day_is_always_a_weekday_trading_day_in_the_same_month():
    for year in sorted(table_years()):
        for month in range(1, 13):
            d = scheduler.first_trading_day(year, month)
            assert (d.year, d.month) == (year, month)
            assert d.weekday() < 5
            assert market_hours.is_trading_day(d)
            # nothing earlier in the month is a trading day
            probe = date(year, month, 1)
            while probe < d:
                assert not market_hours.is_trading_day(probe), (year, month, probe)
                probe += timedelta(days=1)


def test_first_trading_day_skips_an_unscheduled_closure_learned_at_runtime():
    # A weekday the table does not know (national day of mourning / weather) registered by
    # the close ingest must move the anchor too.
    market_hours.register_market_closure(date(2026, 10, 1))
    assert scheduler.first_trading_day(2026, 10) == date(2026, 10, 2)
    assert scheduler.rotation_anchor(2026, 10) == utc(2026, 10, 2, 22, 30)


def test_first_trading_day_long_closure_chain(monkeypatch):
    # Sat, Sun, then two holiday weekdays → the Wednesday.
    monkeypatch.setattr(
        market_hours, "US_MARKET_HOLIDAYS",
        set(market_hours.US_MARKET_HOLIDAYS) | {(2026, 8, 3), (2026, 8, 4)},
    )
    assert scheduler.first_trading_day(2026, 8) == date(2026, 8, 5)


def test_first_trading_day_boundary_day_15_is_still_found(monkeypatch):
    # Every weekday Sep 1-14 closed; Sep 15 2026 is a Tuesday → the 15th iteration finds it.
    closed = {(2026, 9, d) for d in range(1, 15)}
    monkeypatch.setattr(
        market_hours, "US_MARKET_HOLIDAYS", set(market_hours.US_MARKET_HOLIDAYS) | closed,
    )
    assert scheduler.first_trading_day(2026, 9) == date(2026, 9, 15)


def test_first_trading_day_raises_loudly_when_no_trading_day_in_15_days(monkeypatch):
    monkeypatch.setattr(scheduler, "is_trading_day", lambda d: False)
    with pytest.raises(RuntimeError, match=r"2026-09"):
        scheduler.first_trading_day(2026, 9)


def test_first_trading_day_day_16_is_out_of_bounds(monkeypatch):
    closed = {(2026, 9, d) for d in range(1, 16)}   # through Tue Sep 15
    monkeypatch.setattr(
        market_hours, "US_MARKET_HOLIDAYS", set(market_hours.US_MARKET_HOLIDAYS) | closed,
    )
    with pytest.raises(RuntimeError):
        scheduler.first_trading_day(2026, 9)


@pytest.mark.parametrize("bad_month", [0, 13, -1])
def test_first_trading_day_rejects_an_invalid_month(bad_month):
    with pytest.raises(ValueError):
        scheduler.first_trading_day(2026, bad_month)
    with pytest.raises(ValueError):
        scheduler.rotation_anchor(2026, bad_month)


def test_first_trading_day_rejects_wrong_types():
    with pytest.raises(TypeError):
        scheduler.first_trading_day("2026", 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        scheduler.first_trading_day(2026, 1.0)  # type: ignore[arg-type]


# ═════════════════════════════════════════════════════════════════════════════════════
# 2. rotation_anchor — 18:30 ET in both EDT and EST
# ═════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "year, month, expected_utc",
    [
        # Standard time (EST, UTC-5) → 23:30 UTC
        (2026, 1, utc(2026, 1, 2, 23, 30)),
        (2026, 2, utc(2026, 2, 2, 23, 30)),
        (2026, 3, utc(2026, 3, 2, 23, 30)),    # DST starts Mar 8 — anchor is before it
        (2026, 11, utc(2026, 11, 2, 23, 30)),  # DST ended Sun Nov 1 — anchor after it
        (2026, 12, utc(2026, 12, 1, 23, 30)),
        (2027, 1, utc(2027, 1, 4, 23, 30)),
        (2027, 3, utc(2027, 3, 1, 23, 30)),    # DST starts Mar 14 2027
        # Daylight time (EDT, UTC-4) → 22:30 UTC
        (2026, 4, utc(2026, 4, 1, 22, 30)),
        (2026, 7, utc(2026, 7, 1, 22, 30)),
        (2026, 8, utc(2026, 8, 3, 22, 30)),
        (2026, 9, utc(2026, 9, 1, 22, 30)),
        (2026, 10, utc(2026, 10, 1, 22, 30)),
        (2027, 11, utc(2027, 11, 1, 22, 30)),  # November but DST ends Nov 7 2027 → still EDT
        (2025, 9, utc(2025, 9, 2, 22, 30)),
    ],
)
def test_rotation_anchor_is_1830_et_in_dst_and_standard_time(year, month, expected_utc):
    anchor = scheduler.rotation_anchor(year, month)
    assert anchor == expected_utc
    assert anchor.utcoffset() == timedelta(0)
    assert anchor.tzinfo is not None
    local = anchor.astimezone(ET)
    assert local.time() == dtime(18, 30)
    assert local.date() == scheduler.first_trading_day(year, month)


def test_rotation_anchor_utc_hour_tracks_dst_for_every_covered_month():
    for year in sorted(table_years()):
        for month in range(1, 13):
            anchor = scheduler.rotation_anchor(year, month)
            local = anchor.astimezone(ET)
            is_dst = bool(local.dst())
            assert (anchor.hour, anchor.minute) == ((22, 30) if is_dst else (23, 30)), (
                year, month, anchor)
            assert local.time() == scheduler.ROTATION_TIME_ET


def test_schedule_times_are_after_the_regular_close():
    assert scheduler.ROTATION_TIME_ET == dtime(18, 30)
    assert scheduler.INSIGHTS_TIME_ET == dtime(18, 15)
    assert scheduler.ROTATION_TIME_ET > dtime(16, 0)
    assert scheduler.INSIGHTS_TIME_ET > dtime(16, 0)


# ═════════════════════════════════════════════════════════════════════════════════════
# 3. rotation_window
# ═════════════════════════════════════════════════════════════════════════════════════

SEP_ANCHOR = utc(2026, 9, 1, 22, 30)


def test_rotation_window_boundaries_are_half_open():
    assert scheduler.rotation_window(SEP_ANCHOR - timedelta(microseconds=1)) is None
    assert scheduler.rotation_window(SEP_ANCHOR) == (date(2026, 9, 1), SEP_ANCHOR)
    last = SEP_ANCHOR + timedelta(days=7) - timedelta(microseconds=1)
    assert scheduler.rotation_window(last) == (date(2026, 9, 1), SEP_ANCHOR)
    assert scheduler.rotation_window(SEP_ANCHOR + timedelta(days=7)) is None
    assert scheduler.CATCHUP == timedelta(days=7)


def test_rotation_window_same_et_day_before_the_anchor_is_closed():
    # 18:29 ET on the anchor day, and the morning of the 1st.
    assert scheduler.rotation_window(et(2026, 9, 1, 18, 29, 59)) is None
    assert scheduler.rotation_window(et(2026, 9, 1, 9, 30)) is None


def test_rotation_window_open_late_evening_et_when_utc_is_already_the_next_day():
    now = et(2026, 9, 3, 21, 0)                     # Sep 4 01:00 UTC
    assert now.day == 4
    assert scheduler.rotation_window(now) == (date(2026, 9, 1), SEP_ANCHOR)


def test_rotation_window_run_month_is_always_the_first_of_the_month():
    # service.run raises ValueError for a run_month whose day != 1.
    t = utc(2025, 1, 1)
    end = utc(2027, 12, 31)
    seen = 0
    while t < end:
        w = scheduler.rotation_window(t)
        if w is not None:
            seen += 1
            run_month, anchor = w
            assert run_month.day == 1
            local = t.astimezone(ET)
            assert (run_month.year, run_month.month) == (local.year, local.month)
            assert anchor == scheduler.rotation_anchor(local.year, local.month)
            assert anchor <= t < anchor + scheduler.CATCHUP
        t += timedelta(hours=5)
    assert seen > 100   # the sweep actually landed inside windows


def test_rotation_window_uses_the_et_month_on_the_last_evening_of_a_month(monkeypatch):
    # 20:00 ET on Sep 30 is Oct 1 00:00 UTC. The ET month (September) must be the one
    # consulted — a UTC derivation would ask for October's anchor.
    now = et(2026, 9, 30, 20, 0)
    assert (now.month, now.day) == (10, 1)
    real = scheduler.rotation_anchor
    asked: list = []

    def spy(year, month):
        asked.append((year, month))
        return real(year, month)

    monkeypatch.setattr(scheduler, "rotation_anchor", spy)
    assert scheduler.rotation_window(now) is None   # September's window closed Sep 8
    assert asked == [(2026, 9)]


def test_rotation_window_new_years_eve_evening_uses_december(monkeypatch):
    now = et(2026, 12, 31, 20, 0)                   # 2027-01-01 01:00 UTC
    assert now.year == 2027
    real = scheduler.rotation_anchor
    asked: list = []
    monkeypatch.setattr(scheduler, "rotation_anchor",
                        lambda y, m: asked.append((y, m)) or real(y, m))
    assert scheduler.rotation_window(now) is None
    assert asked == [(2026, 12)]


def test_rotation_window_for_a_late_anchor_month():
    jan_anchor = utc(2027, 1, 4, 23, 30)
    assert scheduler.rotation_window(et(2027, 1, 1, 19, 0)) is None     # holiday, pre-anchor
    assert scheduler.rotation_window(et(2027, 1, 4, 18, 29)) is None
    assert scheduler.rotation_window(jan_anchor) == (date(2027, 1, 1), jan_anchor)
    assert scheduler.rotation_window(utc(2027, 1, 11, 23, 29)) == (date(2027, 1, 1), jan_anchor)
    assert scheduler.rotation_window(utc(2027, 1, 11, 23, 30)) is None


@pytest.mark.parametrize("fn", ["rotation_window", "next_rotation_anchor",
                                "insights_due", "next_insights_run"])
def test_schedule_functions_are_timezone_representation_independent(fn):
    f = getattr(scheduler, fn)
    tz_tokyo = timezone(timedelta(hours=9))
    tz_odd = timezone(timedelta(hours=-3, minutes=-30))
    t = utc(2026, 8, 1)
    end = utc(2026, 10, 15)
    while t < end:
        base = f(t)
        for tz in (ET, tz_tokyo, tz_odd):
            assert f(t.astimezone(tz)) == base, (fn, t, tz)
        t += timedelta(hours=7, minutes=13)


@pytest.mark.parametrize("fn", ["rotation_window", "next_rotation_anchor", "next_insights_run"])
def test_naive_datetime_is_never_silently_misread(fn):
    # The loops always pass aware UTC. A naive value must either be refused loudly or be
    # read as UTC (the market_hours convention) — never as the machine's local zone.
    f = getattr(scheduler, fn)
    naive = datetime(2026, 9, 2, 12, 0)
    try:
        got = f(naive)
    except TypeError:
        return
    assert got == f(naive.replace(tzinfo=UTC))


# ═════════════════════════════════════════════════════════════════════════════════════
# 4. next_rotation_anchor
# ═════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "now, expected",
    [
        (et(2026, 8, 1, 12, 0), utc(2026, 8, 3, 22, 30)),        # before this month's anchor
        (SEP_ANCHOR - timedelta(microseconds=1), SEP_ANCHOR),
        (SEP_ANCHOR, utc(2026, 10, 1, 22, 30)),                  # AT the anchor → next month
        (et(2026, 9, 15, 12, 0), utc(2026, 10, 1, 22, 30)),
        (et(2026, 9, 30, 20, 0), utc(2026, 10, 1, 22, 30)),      # last evening, ET month
        (et(2026, 10, 30, 12, 0), utc(2026, 11, 2, 23, 30)),     # into EST
        (et(2026, 12, 1, 10, 0), utc(2026, 12, 1, 23, 30)),      # December, pre-anchor
        (et(2026, 12, 15, 12, 0), utc(2027, 1, 4, 23, 30)),      # December → January roll
        (et(2026, 12, 31, 20, 0), utc(2027, 1, 4, 23, 30)),      # NYE evening (UTC = Jan 1)
        (et(2027, 1, 2, 12, 0), utc(2027, 1, 4, 23, 30)),        # Jan, pre-anchor
        (et(2025, 12, 20, 12, 0), utc(2026, 1, 2, 23, 30)),
    ],
)
def test_next_rotation_anchor_known_instants(now, expected):
    assert scheduler.next_rotation_anchor(now) == expected


def test_next_rotation_anchor_is_strictly_future_minimal_and_opens_a_window():
    t = utc(2025, 1, 1, 3)
    end = utc(2027, 11, 30)
    while t < end:
        nxt = scheduler.next_rotation_anchor(t)
        assert nxt > t, t
        local = nxt.astimezone(ET)
        assert scheduler.rotation_window(nxt) == (date(local.year, local.month, 1), nxt)
        # minimal: the previous month's anchor is not in (t, nxt)
        py, pm = _prev_month(local.year, local.month)
        assert scheduler.rotation_anchor(py, pm) <= t, (t, nxt)
        # never more than ~46 days away (a month plus the longest first-day delay)
        assert nxt - t < timedelta(days=46)
        t += timedelta(hours=11, minutes=17)


# ═════════════════════════════════════════════════════════════════════════════════════
# 5. insights_due / next_insights_run
# ═════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "now, due, why",
    [
        (et(2026, 9, 23, 18, 15), True, "trading day, exactly 18:15 ET"),
        (et(2026, 9, 23, 18, 14, 59, 999999), False, "one microsecond early"),
        (et(2026, 9, 23, 23, 59, 59), True, "late evening same ET day"),
        (et(2026, 9, 23, 0, 0), False, "midnight"),
        (et(2026, 9, 23, 12, 0), False, "midday"),
        (et(2026, 9, 26, 19, 0), False, "Saturday"),
        (et(2026, 9, 27, 19, 0), False, "Sunday"),
        (et(2026, 9, 7, 19, 0), False, "Labor Day"),
        (et(2026, 4, 3, 19, 0), False, "Good Friday"),
        (et(2026, 12, 25, 19, 0), False, "Christmas"),
        (et(2026, 11, 27, 18, 30), True, "half-day is still a trading day"),
        (et(2026, 12, 24, 18, 30), True, "Christmas Eve half-day"),
        # ET date vs UTC date
        (utc(2026, 9, 26, 0, 30), True, "Sat 00:30 UTC = Fri 20:30 ET"),
        (utc(2026, 9, 28, 0, 30), False, "Mon 00:30 UTC = Sun 20:30 ET"),
        # EDT vs EST — a hard-coded UTC slot would get one of these wrong
        (utc(2026, 9, 23, 22, 15), True, "22:15 UTC = 18:15 EDT"),
        (utc(2026, 12, 2, 22, 15), False, "22:15 UTC = 17:15 EST"),
        (utc(2026, 12, 2, 23, 14), False, "23:14 UTC = 18:14 EST"),
        (utc(2026, 12, 2, 23, 15), True, "23:15 UTC = 18:15 EST"),
    ],
)
def test_insights_due(now, due, why):
    assert scheduler.insights_due(now) is due, why


def test_insights_due_matches_an_independent_calendar():
    t = utc(2026, 1, 1)
    end = utc(2027, 12, 31)
    while t < end:
        local = t.astimezone(ET)
        expected = independent_is_trading_day(local.date()) and local.time() >= dtime(18, 15)
        assert scheduler.insights_due(t) is expected, t
        t += timedelta(hours=3, minutes=7)


@pytest.mark.parametrize(
    "now, expected",
    [
        (et(2026, 9, 23, 12, 0), utc(2026, 9, 23, 22, 15)),                 # later today
        (et(2026, 9, 23, 18, 14, 59, 999999), utc(2026, 9, 23, 22, 15)),
        (et(2026, 9, 23, 18, 15), utc(2026, 9, 24, 22, 15)),                # AT slot → tomorrow
        (et(2026, 9, 23, 20, 0), utc(2026, 9, 24, 22, 15)),
        (et(2026, 9, 25, 19, 0), utc(2026, 9, 28, 22, 15)),                 # Fri → Mon
        (et(2026, 9, 26, 12, 0), utc(2026, 9, 28, 22, 15)),                 # Sat → Mon
        (et(2026, 9, 4, 19, 0), utc(2026, 9, 8, 22, 15)),                   # over Labor Day
        (et(2026, 4, 2, 19, 0), utc(2026, 4, 6, 22, 15)),                   # over Good Friday
        (et(2026, 12, 24, 19, 0), utc(2026, 12, 28, 23, 15)),               # over Christmas
        (et(2026, 12, 31, 19, 0), utc(2027, 1, 4, 23, 15)),                 # over NYD 2027
        (et(2026, 3, 6, 19, 0), utc(2026, 3, 9, 22, 15)),                   # EST Fri → EDT Mon
        (et(2026, 10, 30, 19, 0), utc(2026, 11, 2, 23, 15)),                # EDT Fri → EST Mon
        (utc(2026, 9, 26, 0, 30), utc(2026, 9, 28, 22, 15)),                # Fri 20:30 ET
    ],
)
def test_next_insights_run_known_instants(now, expected):
    assert scheduler.next_insights_run(now) == expected


def test_next_insights_run_is_strictly_after_now_minimal_and_due():
    t = utc(2026, 1, 1)
    end = utc(2027, 12, 20)
    while t < end:
        nxt = scheduler.next_insights_run(t)
        assert nxt > t
        local = nxt.astimezone(ET)
        assert local.time() == dtime(18, 15)
        assert independent_is_trading_day(local.date())
        assert scheduler.insights_due(nxt)
        assert not scheduler.insights_due(nxt - timedelta(microseconds=1))
        # minimal: no trading-day 18:15 slot strictly between t and nxt
        d = t.astimezone(ET).date()
        while d < local.date():
            slot = datetime.combine(d, dtime(18, 15), tzinfo=ET)
            if independent_is_trading_day(d):
                assert slot <= t, (t, nxt, d)
            d += timedelta(days=1)
        t += timedelta(hours=4, minutes=41)


def test_next_insights_run_raises_loudly_when_no_trading_day_within_15_days(monkeypatch):
    monkeypatch.setattr(scheduler, "is_trading_day", lambda d: False)
    with pytest.raises(RuntimeError, match="no trading day"):
        scheduler.next_insights_run(utc(2026, 9, 23))


# ═════════════════════════════════════════════════════════════════════════════════════
# 6. _sleep_until and cross-module invariants
# ═════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "delta_seconds, expected",
    [
        (-86400.0, 60.0),          # target in the past → floor
        (0.0, 60.0),
        (59.9, 60.0),
        (60.0, 60.0),
        (3600.0, 3600.0),
        (6 * 3600.0, 6 * 3600.0),
        (6 * 3600.0 + 1, 6 * 3600.0),
        (7 * 86400.0, 6 * 3600.0),   # a week away → re-read the flags every 6 h
    ],
)
def test_sleep_until_is_clamped(delta_seconds, expected):
    now = utc(2026, 9, 23, 12)
    assert scheduler._sleep_until(now + timedelta(seconds=delta_seconds), now) == expected


def test_idle_sleep_can_never_skip_a_catchup_window():
    assert scheduler.MAX_IDLE_SLEEP_SECONDS < scheduler.CATCHUP.total_seconds()
    assert scheduler.RETRY_SECONDS < scheduler.CATCHUP.total_seconds()
    # every allowed attempt fits in the window with room to spare
    assert (scheduler.RETRY_SECONDS * rotation_service.MAX_ATTEMPTS_PER_MONTH
            < scheduler.CATCHUP.total_seconds() / 2)


def test_month_level_stale_is_shorter_than_the_day_claim_stale():
    # A process killed mid-run leaves the day claim AND the month row in_progress. The day
    # claim frees after _ROTATION_STALE_SECONDS; if the month row were still "fresh" then,
    # service.run would answer "skipped" and the scheduler would mark the day a success —
    # burning a day of the 7-day window.
    assert (rotation_service.STALE_IN_PROGRESS.total_seconds()
            <= scheduler._ROTATION_STALE_SECONDS)


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def test_job_names_match_the_kill_switch_rows_seeded_by_migration_174():
    sql = _strip_sql_comments(
        (BACKEND / "database" / "migrations" / "174_theme_rotation_and_insights.sql").read_text()
    )
    m = re.search(
        r"INSERT\s+INTO\s+public\.notification_job_state\s*\([^)]*\)\s*VALUES(.*?);",
        sql, flags=re.S | re.I,
    )
    assert m, "migration 174 no longer seeds notification_job_state"
    seeded = set(re.findall(r"\(\s*'([a-z0-9_]+)'", m.group(1)))
    assert scheduler.JOB_THEME_ROTATION_MONTHLY in seeded, seeded
    assert scheduler.JOB_THEME_INSIGHTS_DAILY in seeded, seeded
    assert scheduler.JOB_THEME_ROTATION_MONTHLY != scheduler.JOB_THEME_INSIGHTS_DAILY


# ═════════════════════════════════════════════════════════════════════════════════════
# 7. Holiday-table coverage guards
# ═════════════════════════════════════════════════════════════════════════════════════


def test_holiday_table_is_complete_and_correct_for_every_year_it_lists():
    years = sorted(table_years())
    assert years, "US_MARKET_HOLIDAYS is empty"
    assert years == list(range(years[0], years[-1] + 1)), f"gap in covered years: {years}"
    for (y, m, d) in market_hours.US_MARKET_HOLIDAYS:
        assert date(y, m, d).weekday() < 5, f"{y}-{m:02d}-{d:02d} is a weekend 'holiday'"
    problems = []
    for year in years:
        have, want = table_holidays(year), nyse_holidays(year)
        if have != want:
            problems.append(
                f"{year}: missing={sorted(want - have)} unexpected={sorted(have - want)}")
    assert not problems, problems


def test_holiday_table_early_closes_are_trading_days_inside_covered_years():
    years = table_years()
    for (y, m, d) in market_hours.US_MARKET_EARLY_CLOSES:
        assert y in years, f"early close {y}-{m:02d}-{d:02d} outside the holiday table's years"
        assert market_hours.is_trading_day(date(y, m, d)), (y, m, d)
    for year in years:
        day_after_thanksgiving = _nth_weekday(year, 11, 3, 4) + timedelta(days=1)
        assert (year, day_after_thanksgiving.month, day_after_thanksgiving.day) in \
            market_hours.US_MARKET_EARLY_CLOSES, year


def test_holiday_table_covers_the_next_13_monthly_rotation_anchors__extend_US_MARKET_HOLIDAYS_when_this_fails():
    """Uses the REAL clock on purpose: this is an early-warning timer, not a unit test.

    It starts failing about a year before the calendar in app/utils/market_hours.py runs
    out, which is the point — once it runs out, `is_trading_day` calls every holiday of the
    uncovered year a trading day, and the monthly rotation (and the daily insights) would
    run on e.g. a closed New Year's Day.
    """
    now = datetime.now(UTC)
    first = scheduler.next_rotation_anchor(now).astimezone(ET)
    months = []
    y, m = first.year, first.month
    for _ in range(13):
        months.append((y, m))
        y, m = _next_month(y, m)
    needed = sorted({yy for (yy, _mm) in months})
    covered = table_years()
    missing = [yy for yy in needed if yy not in covered]
    incomplete = [yy for yy in needed if yy in covered and table_holidays(yy) != nyse_holidays(yy)]
    assert not missing and not incomplete, (
        f"US_MARKET_HOLIDAYS (app/utils/market_hours.py) does not fully cover the next 13 "
        f"monthly theme-rotation anchors ({months[0][0]}-{months[0][1]:02d} .. "
        f"{months[-1][0]}-{months[-1][1]:02d}). Missing years: {missing}; incomplete years: "
        f"{incomplete}. Add the NYSE holidays (and early closes) for those years — until "
        f"then every holiday in them counts as a trading day for the rotation and the "
        f"daily theme insights. Expected for {missing or incomplete}: "
        f"{ {yy: sorted(str(d) for d in nyse_holidays(yy)) for yy in (missing or incomplete)} }"
    )


# ═════════════════════════════════════════════════════════════════════════════════════
# 8. _rotation_tick with fakes
# ═════════════════════════════════════════════════════════════════════════════════════


def _plan(slug: str, after: List[str]) -> ThemePlan:
    return ThemePlan(slug=slug, before=list(after), after=list(after), decisions=[],
                     added=[], returned=[], removed=[], deferred=[], shortfall=False,
                     change_cap=0)


class _FakeRotationService:
    """Mirrors ThemeRotationService's four scheduler-facing coroutines."""

    def __init__(self) -> None:
        self.done: Any = False               # value, or a list consumed one per call
        self.exhausted: bool = False
        self.attempted: bool = True          # False = enabled after the window closed
        self.result: Optional[RunResult] = None
        self.run_exc: Optional[BaseException] = None
        self.calls: List[tuple] = []

    async def month_done(self, run_month, mode):
        self.calls.append(("month_done", run_month, mode))
        v = self.done.pop(0) if isinstance(self.done, list) else self.done
        if isinstance(v, BaseException):
            raise v
        return v

    async def attempts_exhausted(self, run_month, mode):
        self.calls.append(("attempts_exhausted", run_month, mode))
        return self.exhausted

    async def month_attempted(self, run_month, mode):
        self.calls.append(("month_attempted", run_month, mode))
        return self.attempted

    async def run(self, run_month, mode, *, slugs=None, record=True, as_of=None):
        self.calls.append(("run", run_month, mode, slugs, record, as_of))
        if self.run_exc is not None:
            raise self.run_exc
        if self.result is not None:
            return self.result
        return RunResult("run-1", run_month, mode, "published",
                         plans={"a": _plan("a", ["AAA", "BBB", "CCC", "DDD", "EEE"]),
                                "b": _plan("b", ["FFF", "GGG", "HHH"])})

    def names(self) -> List[str]:
        return [c[0] for c in self.calls]


class _FakeClaim:
    """Drop-in for notification_jobs.claimed_scheduled_job (same signature)."""

    def __init__(self, granted: bool = True) -> None:
        self.granted = granted
        self.calls: List[dict] = []
        self.runs: List[notification_jobs.ScheduledJobResult] = []
        self.errors: List[BaseException] = []

    def __call__(self, job, *, timezone_name="UTC", stale_seconds=None):
        self.calls.append({"job": job, "timezone_name": timezone_name,
                           "stale_seconds": stale_seconds})
        return self._cm()

    @contextlib.asynccontextmanager
    async def _cm(self):
        if not self.granted:
            yield None
            return
        run = notification_jobs.ScheduledJobResult()
        self.runs.append(run)
        try:
            yield run
        except BaseException as e:
            run.success = False
            self.errors.append(e)
            raise


class _FakeState:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls: List[str] = []

    def __call__(self, job):
        self.calls.append(job)
        return self.value


def _param_shape(fn) -> list:
    return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values()
            if p.name != "self"]


def test_fakes_match_the_real_signatures():
    assert _param_shape(_FakeClaim.__call__) == _param_shape(notification_jobs.claimed_scheduled_job)
    assert _param_shape(_FakeState.__call__) == _param_shape(notification_jobs.scheduled_job_state)
    real = rotation_service.ThemeRotationService
    for name in ("month_done", "attempts_exhausted", "month_attempted", "run"):
        assert [(n, k) for n, k, _d in _param_shape(getattr(_FakeRotationService, name))] == \
            [(n, k) for n, k, _d in _param_shape(getattr(real, name))], name
    assert [(n, k) for n, k, _d in _param_shape(_FakeInsights.run_daily)] == \
        [(n, k) for n, k, _d in _param_shape(theme_insights_service.ThemeInsightsService.run_daily)]


@pytest.fixture
def rot(monkeypatch):
    svc = _FakeRotationService()
    claim = _FakeClaim()
    state = _FakeState({"job": scheduler.JOB_THEME_ROTATION_MONTHLY, "run_day": None,
                        "claim_at": None, "enabled": True})
    factory_calls: List[int] = []

    def _get():
        factory_calls.append(1)
        return svc

    monkeypatch.setattr(rotation_service, "get_theme_rotation_service", _get)
    monkeypatch.setattr(notification_jobs, "claimed_scheduled_job", claim)
    monkeypatch.setattr(notification_jobs, "scheduled_job_state", state)
    monkeypatch.setattr(scheduler.settings, "THEME_ROTATION_ENABLED", True)
    monkeypatch.setattr(scheduler.settings, "THEME_ROTATION_DRY_RUN", False)
    return SimpleNamespace(svc=svc, claim=claim, state=state, factory_calls=factory_calls)


IN_WINDOW = utc(2026, 9, 2, 12, 0)
BEFORE_ANCHOR = et(2026, 9, 1, 12, 0)
AFTER_WINDOW = utc(2026, 9, 20, 12, 0)
SEPT = date(2026, 9, 1)


@pytest.mark.asyncio
async def test_rotation_tick_disabled_returns_none_and_touches_nothing(rot, monkeypatch):
    monkeypatch.setattr(scheduler.settings, "THEME_ROTATION_ENABLED", False)
    for now in (BEFORE_ANCHOR, IN_WINDOW, AFTER_WINDOW):
        missed: set = set()
        assert await scheduler._rotation_tick(now, missed) is None
        assert missed == set()
    assert rot.factory_calls == []
    assert rot.svc.calls == []
    assert rot.claim.calls == [] and rot.state.calls == []


@pytest.mark.asyncio
async def test_rotation_tick_before_the_anchor_returns_none_without_reading_state(rot):
    for now in (BEFORE_ANCHOR, SEP_ANCHOR - timedelta(microseconds=1), et(2026, 9, 1, 0, 0)):
        assert await scheduler._rotation_tick(now, set()) is None
    assert rot.svc.calls == []
    assert rot.claim.calls == [] and rot.state.calls == []


@pytest.mark.asyncio
async def test_rotation_tick_claim_granted_runs_live_and_marks_success(rot):
    wait = await scheduler._rotation_tick(IN_WINDOW, set())
    assert wait == scheduler.RETRY_SECONDS
    assert rot.svc.names() == ["month_done", "attempts_exhausted", "run"]
    assert rot.svc.calls[0] == ("month_done", SEPT, "live")
    assert rot.svc.calls[1] == ("attempts_exhausted", SEPT, "live")
    assert rot.svc.calls[2][:3] == ("run", SEPT, "live")
    # the scheduler must not pass preview-only knobs
    assert rot.svc.calls[2][3:] == (None, True, None)
    assert rot.state.calls == [scheduler.JOB_THEME_ROTATION_MONTHLY]
    assert rot.claim.calls == [{
        "job": scheduler.JOB_THEME_ROTATION_MONTHLY,
        "timezone_name": "America/New_York",
        "stale_seconds": scheduler._ROTATION_STALE_SECONDS,
    }]
    [run] = rot.claim.runs
    assert run.success is True
    assert run.items == 8          # 5 + 3 tickers after rotation
    assert rot.claim.errors == []


@pytest.mark.asyncio
async def test_rotation_tick_exactly_at_the_anchor_runs(rot):
    assert await scheduler._rotation_tick(SEP_ANCHOR, set()) == scheduler.RETRY_SECONDS
    assert rot.svc.names()[-1] == "run"


@pytest.mark.asyncio
async def test_rotation_tick_last_microsecond_of_the_window_still_runs(rot, sched_logs):
    now = SEP_ANCHOR + scheduler.CATCHUP - timedelta(microseconds=1)
    assert await scheduler._rotation_tick(now, set()) == scheduler.RETRY_SECONDS
    assert rot.svc.names()[-1] == "run"
    assert sched_logs.matching(logging.ERROR, "MISSED") == []


@pytest.mark.asyncio
async def test_rotation_tick_dry_run_mode_everywhere(rot, monkeypatch):
    monkeypatch.setattr(scheduler.settings, "THEME_ROTATION_DRY_RUN", True)
    assert await scheduler._rotation_tick(IN_WINDOW, set()) == scheduler.RETRY_SECONDS
    assert [c[2] for c in rot.svc.calls] == ["dry_run", "dry_run", "dry_run"]
    assert rot.claim.runs[0].success is True


@pytest.mark.asyncio
async def test_rotation_tick_unreadable_run_state_fails_closed(rot, sched_logs):
    rot.svc.done = None
    assert await scheduler._rotation_tick(IN_WINDOW, set()) == scheduler.RETRY_SECONDS
    assert rot.svc.names() == ["month_done"]
    assert rot.claim.calls == [] and rot.state.calls == []
    assert sched_logs.matching(logging.WARNING, "unreadable")


@pytest.mark.asyncio
async def test_rotation_tick_month_already_done_returns_none(rot):
    rot.svc.done = True
    assert await scheduler._rotation_tick(IN_WINDOW, set()) is None
    assert rot.svc.names() == ["month_done"]
    assert rot.claim.calls == []


@pytest.mark.asyncio
async def test_rotation_tick_attempts_exhausted_returns_none(rot):
    rot.svc.exhausted = True
    assert await scheduler._rotation_tick(IN_WINDOW, set()) is None
    assert rot.svc.names() == ["month_done", "attempts_exhausted"]
    assert rot.claim.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, None, 0, ""])
async def test_rotation_tick_kill_switch_off_skips_without_claiming(rot, sched_logs, enabled):
    rot.state.value = {"job": scheduler.JOB_THEME_ROTATION_MONTHLY, "enabled": enabled}
    assert await scheduler._rotation_tick(IN_WINDOW, set()) is None
    assert rot.claim.calls == []
    assert "run" not in rot.svc.names()
    assert sched_logs.matching(logging.INFO, "kill switch")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, {"job": "theme_rotation_monthly"}, {"enabled": True}])
async def test_rotation_tick_unreadable_or_default_state_defers_to_the_sql_claim(rot, state):
    # scheduled_job_state() is None when the read failed; the claim RPC itself checks
    # `enabled`, so the scheduler still asks it rather than guessing.
    rot.state.value = state
    assert await scheduler._rotation_tick(IN_WINDOW, set()) == scheduler.RETRY_SECONDS
    assert len(rot.claim.calls) == 1
    assert rot.svc.names()[-1] == "run"


@pytest.mark.asyncio
async def test_rotation_tick_claim_refused_retries_without_running(rot):
    rot.claim.granted = False
    assert await scheduler._rotation_tick(IN_WINDOW, set()) == scheduler.RETRY_SECONDS
    assert len(rot.claim.calls) == 1
    assert "run" not in rot.svc.names()


@pytest.mark.asyncio
@pytest.mark.parametrize("status, success", [
    ("published", True), ("computed", True), ("skipped", True), ("failed", False),
    ("", False), ("PUBLISHED", False),
])
async def test_rotation_tick_success_follows_the_run_status(rot, status, success):
    rot.svc.result = RunResult("r", SEPT, "live", status,
                               plans={"a": _plan("a", ["X", "Y"])} if status != "skipped" else {})
    assert await scheduler._rotation_tick(IN_WINDOW, set()) == scheduler.RETRY_SECONDS
    run = rot.claim.runs[0]
    assert run.success is success
    assert run.items == (0 if status == "skipped" else 2)


@pytest.mark.asyncio
async def test_rotation_tick_empty_after_lists_count_zero_items(rot):
    rot.svc.result = RunResult("r", SEPT, "live", "published",
                               plans={"a": _plan("a", []), "b": _plan("b", [])})
    await scheduler._rotation_tick(IN_WINDOW, set())
    assert rot.claim.runs[0].items == 0
    assert rot.claim.runs[0].success is True


@pytest.mark.asyncio
async def test_rotation_tick_a_raising_run_propagates_and_is_not_a_success(rot):
    rot.svc.run_exc = RuntimeError("fmp down")
    with pytest.raises(RuntimeError, match="fmp down"):
        await scheduler._rotation_tick(IN_WINDOW, set())
    [run] = rot.claim.runs
    assert run.success is False
    assert len(rot.claim.errors) == 1 and isinstance(rot.claim.errors[0], RuntimeError)


@pytest.mark.asyncio
async def test_rotation_tick_cancellation_propagates_through_the_claim(rot):
    rot.svc.run_exc = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await scheduler._rotation_tick(IN_WINDOW, set())
    assert rot.claim.runs[0].success is False


@pytest.mark.asyncio
async def test_rotation_tick_month_done_raising_propagates(rot):
    rot.svc.done = RuntimeError("db exploded")
    with pytest.raises(RuntimeError, match="db exploded"):
        await scheduler._rotation_tick(IN_WINDOW, set())
    assert rot.claim.calls == []


# ── the MISSED alarm ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missed_is_logged_at_error_exactly_once_per_month(rot, sched_logs):
    missed: set = set()
    for now in (AFTER_WINDOW, AFTER_WINDOW + timedelta(hours=6), et(2026, 9, 30, 23, 0)):
        assert await scheduler._rotation_tick(now, missed) is None
    hits = sched_logs.matching(logging.ERROR, "theme rotation MISSED")
    assert len(hits) == 1
    assert "2026-09-01" in hits[0].getMessage() and "live" in hits[0].getMessage()
    assert missed == {SEPT}
    # after the first log the state is not even re-read
    assert rot.svc.names() == ["month_done", "month_attempted"]
    assert rot.claim.calls == [] and rot.state.calls == []


@pytest.mark.asyncio
async def test_missed_logs_again_for_a_different_month(rot, sched_logs):
    missed: set = set()
    await scheduler._rotation_tick(AFTER_WINDOW, missed)
    await scheduler._rotation_tick(utc(2026, 10, 20, 12), missed)
    await scheduler._rotation_tick(utc(2026, 10, 25, 12), missed)
    hits = sched_logs.matching(logging.ERROR, "theme rotation MISSED")
    assert [h.getMessage().split()[3] for h in hits] == ["2026-09-01", "2026-10-01"]
    assert missed == {SEPT, date(2026, 10, 1)}


@pytest.mark.asyncio
async def test_missed_is_silent_when_the_month_completed(rot, sched_logs):
    rot.svc.done = True
    missed: set = set()
    assert await scheduler._rotation_tick(AFTER_WINDOW, missed) is None
    assert sched_logs.matching(logging.ERROR, "MISSED") == []
    assert missed == set()


@pytest.mark.asyncio
async def test_missed_waits_for_a_readable_state_then_logs_once(rot, sched_logs):
    rot.svc.done = [None, None, False, False]
    missed: set = set()
    for i in range(4):
        assert await scheduler._rotation_tick(AFTER_WINDOW + timedelta(hours=i), missed) is None
    assert len(sched_logs.matching(logging.ERROR, "theme rotation MISSED")) == 1
    # unreadable, unreadable, then readable + attempted; the 4th tick short-circuits on the set
    assert rot.svc.names() == ["month_done"] * 3 + ["month_attempted"]
    assert missed == {SEPT}


@pytest.mark.asyncio
async def test_missed_fires_at_exactly_anchor_plus_seven_days(rot, sched_logs):
    assert await scheduler._rotation_tick(SEP_ANCHOR + scheduler.CATCHUP, set()) is None
    assert len(sched_logs.matching(logging.ERROR, "theme rotation MISSED")) == 1
    assert "run" not in rot.svc.names()


@pytest.mark.asyncio
async def test_missed_uses_the_et_month_on_the_last_evening_of_the_month(rot, sched_logs):
    # 20:00 ET on Sep 30 is already Oct 1 in UTC. The tick must judge SEPTEMBER (whose
    # window closed) — never October (whose anchor is still ahead).
    now = et(2026, 9, 30, 20, 0)
    assert now.month == 10
    missed: set = set()
    assert await scheduler._rotation_tick(now, missed) is None
    assert rot.svc.calls == [("month_done", SEPT, "live"), ("month_attempted", SEPT, "live")]
    assert missed == {SEPT}
    [hit] = sched_logs.matching(logging.ERROR, "theme rotation MISSED")
    assert "2026-09-01" in hit.getMessage()


@pytest.mark.asyncio
async def test_missed_uses_december_on_new_years_eve_evening(rot, sched_logs):
    now = et(2026, 12, 31, 21, 0)                    # 2027-01-01 02:00 UTC
    missed: set = set()
    assert await scheduler._rotation_tick(now, missed) is None
    assert rot.svc.calls == [("month_done", date(2026, 12, 1), "live"),
                             ("month_attempted", date(2026, 12, 1), "live")]
    assert missed == {date(2026, 12, 1)}


@pytest.mark.asyncio
async def test_missed_in_dry_run_mode_names_the_mode(rot, sched_logs, monkeypatch):
    monkeypatch.setattr(scheduler.settings, "THEME_ROTATION_DRY_RUN", True)
    await scheduler._rotation_tick(AFTER_WINDOW, set())
    assert rot.svc.calls == [("month_done", SEPT, "dry_run"), ("month_attempted", SEPT, "dry_run")]
    [hit] = sched_logs.matching(logging.ERROR, "theme rotation MISSED")
    assert "dry_run" in hit.getMessage()


@pytest.mark.asyncio
async def test_rotation_tick_attempts_a_run_exactly_inside_rotation_window(rot):
    """Property: across a month and a half, the tick tries `service.run` iff
    `rotation_window(now)` is open, and with that window's run_month."""
    missed: set = set()
    t = utc(2026, 8, 25)
    end = utc(2026, 10, 12)
    checked_open = checked_closed = 0
    points = []
    while t < end:
        points.append(t)
        t += timedelta(hours=2, minutes=53)
    points += [SEP_ANCHOR, SEP_ANCHOR - timedelta(microseconds=1),
               SEP_ANCHOR + scheduler.CATCHUP, SEP_ANCHOR + scheduler.CATCHUP - timedelta(microseconds=1),
               utc(2026, 10, 1, 22, 30), utc(2026, 10, 1, 22, 29, 59)]
    for now in points:
        rot.svc.calls.clear()
        w = scheduler.rotation_window(now)
        await scheduler._rotation_tick(now, missed)
        runs = [c for c in rot.svc.calls if c[0] == "run"]
        if w is None:
            assert runs == [], now
            checked_closed += 1
        else:
            assert len(runs) == 1 and runs[0][1] == w[0], now
            checked_open += 1
    assert checked_open > 20 and checked_closed > 100


# ═════════════════════════════════════════════════════════════════════════════════════
# 9. _insights_tick with fakes
# ═════════════════════════════════════════════════════════════════════════════════════


class _FakeInsights:
    def __init__(self) -> None:
        self.summary: Any = {"themes_ok": 7, "themes_failed": ["x"]}
        self.exc: Optional[BaseException] = None
        self.calls: List[tuple] = []
        self.slug_calls: List[Any] = []

    async def run_daily(self, now=None, *, force=False, slugs=None):
        self.calls.append((now, force))
        self.slug_calls.append(slugs)
        if self.exc is not None:
            raise self.exc
        return self.summary


@pytest.fixture
def ins(monkeypatch):
    svc = _FakeInsights()
    claim = _FakeClaim()
    factory_calls: List[int] = []

    def _get():
        factory_calls.append(1)
        return svc

    monkeypatch.setattr(theme_insights_service, "get_theme_insights_service", _get)
    monkeypatch.setattr(notification_jobs, "claimed_scheduled_job", claim)
    monkeypatch.setattr(scheduler.settings, "THEME_INSIGHTS_ENABLED", True)
    monkeypatch.setattr(scheduler, "_insights_retry", {})
    return SimpleNamespace(svc=svc, claim=claim, factory_calls=factory_calls)


DUE = et(2026, 9, 23, 18, 30)


@pytest.mark.asyncio
async def test_insights_tick_disabled_returns_none_even_when_due(ins, monkeypatch):
    monkeypatch.setattr(scheduler.settings, "THEME_INSIGHTS_ENABLED", False)
    assert await scheduler._insights_tick(DUE) is None
    assert ins.claim.calls == [] and ins.svc.calls == [] and ins.factory_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("now", [
    et(2026, 9, 23, 18, 14, 59), et(2026, 9, 26, 19, 0), et(2026, 9, 7, 19, 0),
    et(2026, 12, 25, 20, 0), utc(2026, 9, 28, 0, 30),
])
async def test_insights_tick_not_due_returns_none_without_claiming(ins, now):
    assert await scheduler._insights_tick(now) is None
    assert ins.claim.calls == [] and ins.svc.calls == []


@pytest.mark.asyncio
async def test_insights_tick_claim_refused_retries(ins):
    ins.claim.granted = False
    assert await scheduler._insights_tick(DUE) == scheduler.INSIGHTS_RETRY_SECONDS
    assert ins.svc.calls == []
    assert len(ins.claim.calls) == 1


@pytest.mark.asyncio
async def test_insights_tick_granted_runs_daily_and_marks_success(ins):
    assert await scheduler._insights_tick(DUE) is None
    assert ins.claim.calls == [{
        "job": scheduler.JOB_THEME_INSIGHTS_DAILY,
        "timezone_name": "America/New_York",
        "stale_seconds": scheduler._INSIGHTS_STALE_SECONDS,
    }]
    assert len(ins.svc.calls) == 1
    passed_now, force = ins.svc.calls[0]
    assert passed_now is DUE
    assert force is False          # the scheduler must never force a disabled run
    [run] = ins.claim.runs
    assert run.success is True and run.items == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("summary, items", [
    (None, 0), ({}, 0), ({"themes_ok": None}, 0), ({"themes_ok": 0}, 0),
    ({"themes_ok": 3.0}, 3), ({"themes_ok": True}, 1),
    ({"as_of": "2026-09-23", "skipped": "disabled"}, 0),
])
async def test_insights_tick_summary_shapes(ins, summary, items):
    ins.svc.summary = summary
    assert await scheduler._insights_tick(DUE) is None
    run = ins.claim.runs[0]
    assert run.items == items
    assert run.success is True


@pytest.mark.asyncio
async def test_insights_tick_a_raising_run_propagates_and_is_not_a_success(ins):
    ins.svc.exc = theme_insights_service.ThemeInsightsError("every theme failed")
    with pytest.raises(theme_insights_service.ThemeInsightsError):
        await scheduler._insights_tick(DUE)
    assert ins.claim.runs[0].success is False
    assert len(ins.claim.errors) == 1


# ═════════════════════════════════════════════════════════════════════════════════════
# 10. The loops — error containment, cancellation, sleep choice
# ═════════════════════════════════════════════════════════════════════════════════════


class _StopLoop(BaseException):
    pass


def _asyncio_proxy(sleeps: list, stop_after: int) -> SimpleNamespace:
    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= stop_after:
            raise _StopLoop()
    return SimpleNamespace(sleep=fake_sleep, CancelledError=asyncio.CancelledError,
                           to_thread=asyncio.to_thread)


def _frozen_datetime(fixed: datetime):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)
    return _Frozen


@pytest.mark.asyncio
async def test_rotation_loop_contains_errors_and_sleeps_correctly(monkeypatch, sched_logs):
    fixed = utc(2026, 9, 1, 20, 0)        # 2.5 h before September's anchor
    sleeps: list = []
    monkeypatch.setattr(scheduler, "asyncio", _asyncio_proxy(sleeps, stop_after=4))
    monkeypatch.setattr(scheduler, "datetime", _frozen_datetime(fixed))
    script: list = [RuntimeError("boom"), 42.0, None]
    seen: list = []

    async def fake_tick(now, missed_logged):
        seen.append((now, missed_logged))
        step = script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    monkeypatch.setattr(scheduler, "_rotation_tick", fake_tick)
    with pytest.raises(_StopLoop):
        await scheduler.run_theme_rotation_loop()
    assert sleeps == [180, scheduler.RETRY_SECONDS, 42.0, 9000.0]
    assert len(seen) == 3
    assert all(now == fixed and now.tzinfo is not None for now, _ in seen)
    # ONE set for the life of the loop — what makes the MISSED alarm once-per-month
    assert len({id(s) for _, s in seen}) == 1
    [err] = sched_logs.matching(logging.ERROR, "theme rotation loop: tick failed")
    assert "RuntimeError" in err.getMessage() and err.exc_info is not None


@pytest.mark.asyncio
async def test_rotation_loop_idle_sleep_is_capped_between_windows(monkeypatch):
    fixed = utc(2026, 9, 12, 12, 0)       # window closed, next anchor Oct 1
    sleeps: list = []
    monkeypatch.setattr(scheduler, "asyncio", _asyncio_proxy(sleeps, stop_after=2))
    monkeypatch.setattr(scheduler, "datetime", _frozen_datetime(fixed))

    async def fake_tick(now, missed_logged):
        return None

    monkeypatch.setattr(scheduler, "_rotation_tick", fake_tick)
    with pytest.raises(_StopLoop):
        await scheduler.run_theme_rotation_loop()
    assert sleeps == [180, float(scheduler.MAX_IDLE_SLEEP_SECONDS)]


@pytest.mark.asyncio
async def test_rotation_loop_lets_cancellation_through(monkeypatch, sched_logs):
    sleeps: list = []
    monkeypatch.setattr(scheduler, "asyncio", _asyncio_proxy(sleeps, stop_after=99))

    async def fake_tick(now, missed_logged):
        raise asyncio.CancelledError()

    monkeypatch.setattr(scheduler, "_rotation_tick", fake_tick)
    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_theme_rotation_loop()
    assert sleeps == [180]
    assert sched_logs.matching(logging.ERROR, "tick failed") == []


@pytest.mark.asyncio
async def test_insights_loop_contains_errors_and_sleeps_until_the_next_slot(monkeypatch, sched_logs):
    fixed = et(2026, 9, 23, 17, 15)       # an hour before the 18:15 ET slot
    sleeps: list = []
    monkeypatch.setattr(scheduler, "asyncio", _asyncio_proxy(sleeps, stop_after=4))
    monkeypatch.setattr(scheduler, "datetime", _frozen_datetime(fixed))
    script: list = [ValueError("bad row"), 7.0, None]

    async def fake_tick(now):
        assert now == fixed
        step = script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    monkeypatch.setattr(scheduler, "_insights_tick", fake_tick)
    with pytest.raises(_StopLoop):
        await scheduler.run_theme_insights_loop()
    assert sleeps == [240, scheduler.INSIGHTS_RETRY_SECONDS, 7.0, 3600.0]
    [err] = sched_logs.matching(logging.ERROR, "theme insights loop: tick failed")
    assert "ValueError" in err.getMessage() and err.exc_info is not None


@pytest.mark.asyncio
async def test_insights_loop_lets_cancellation_through(monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(scheduler, "asyncio", _asyncio_proxy(sleeps, stop_after=99))

    async def fake_tick(now):
        raise asyncio.CancelledError()

    monkeypatch.setattr(scheduler, "_insights_tick", fake_tick)
    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_theme_insights_loop()
    assert sleeps == [240]


# ═════════════════════════════════════════════════════════════════════════════════════
# 11. main.lifespan spawns both loops ONLY on Railway (AST scan — comments never count)
# ═════════════════════════════════════════════════════════════════════════════════════

_LOOPS = ("run_theme_rotation_loop", "run_theme_insights_loop")


def _spawned_loops(nodes) -> List[str]:
    """Names X for every `_spawn(X(), ...)` call under `nodes` (calls, not references)."""
    out: List[str] = []
    for top in nodes:
        for n in ast.walk(top):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_spawn" and n.args
                    and isinstance(n.args[0], ast.Call)
                    and isinstance(n.args[0].func, ast.Name)):
                out.append(n.args[0].func.id)
    return out


def _is_development_check(node) -> bool:
    return (isinstance(node, ast.Compare) and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Eq)
            and isinstance(node.left, ast.Attribute) and node.left.attr == "ENVIRONMENT"
            and isinstance(node.left.value, ast.Name) and node.left.value.id == "settings"
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value == "development")


def _theme_loop_spawn_problems(src: str) -> List[str]:
    tree = ast.parse(src)
    lifespans = [n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"]
    if len(lifespans) != 1:
        return [f"expected one `async def lifespan`, found {len(lifespans)}"]
    body = lifespans[0].body
    problems: List[str] = []
    if not any(isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "is_local_dev" for t in n.targets)
               and _is_development_check(n.value) for n in body):
        problems.append('`is_local_dev = settings.ENVIRONMENT == "development"` not found')
    branches = [n for n in body if isinstance(n, ast.If)
                and isinstance(n.test, ast.Name) and n.test.id == "is_local_dev"]
    if len(branches) != 1:
        return problems + [f"expected one top-level `if is_local_dev:`, found {len(branches)}"]
    branch = branches[0]
    railway = _spawned_loops(branch.orelse)
    local = _spawned_loops(branch.body)
    everywhere = _spawned_loops(body)
    for loop in _LOOPS:
        if railway.count(loop) != 1:
            problems.append(f"{loop}() spawned {railway.count(loop)}x in the Railway branch")
        if loop in local:
            problems.append(f"{loop}() spawned in the local-dev branch")
        if everywhere.count(loop) != railway.count(loop):
            problems.append(f"{loop}() spawned outside the Railway branch")
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == "app.services.theme_rotation.scheduler":
            imported |= {a.asname or a.name for a in n.names if a.name in _LOOPS}
    for loop in _LOOPS:
        if loop not in imported:
            problems.append(f"{loop} is not imported from app.services.theme_rotation.scheduler")
    return problems


_GOOD_LIFESPAN = '''
async def lifespan(app):
    is_local_dev = settings.ENVIRONMENT == "development"
    if is_local_dev:
        logger.info("skip")
    else:
        from app.services.theme_rotation.scheduler import (
            run_theme_insights_loop,
            run_theme_rotation_loop,
        )
        _spawn(run_theme_rotation_loop(), "theme_rotation")
        _spawn(run_theme_insights_loop(), "theme_insights")
    yield
'''


@pytest.mark.parametrize("mutant", [
    # moved into the local-dev branch
    _GOOD_LIFESPAN.replace('        logger.info("skip")',
                           '        _spawn(run_theme_rotation_loop(), "x")\n'
                           '        logger.info("skip")'),
    # present only as a comment
    _GOOD_LIFESPAN.replace('        _spawn(run_theme_rotation_loop(), "theme_rotation")',
                           '        # _spawn(run_theme_rotation_loop(), "theme_rotation")'),
    # a reference, not a coroutine
    _GOOD_LIFESPAN.replace('_spawn(run_theme_insights_loop(), "theme_insights")',
                           '_spawn(run_theme_insights_loop, "theme_insights")'),
    # spawned a second time outside the branch (would run locally with notification jobs)
    _GOOD_LIFESPAN.replace("    yield",
                           "    if run_notification_jobs:\n"
                           "        _spawn(run_theme_insights_loop(), 'again')\n    yield"),
    # the branch no longer keys on the development check
    _GOOD_LIFESPAN.replace('settings.ENVIRONMENT == "development"', "False"),
])
def test_spawn_scan_is_not_vacuous(mutant):
    assert _theme_loop_spawn_problems(_GOOD_LIFESPAN) == []
    assert _theme_loop_spawn_problems(mutant), "the scan accepted a broken lifespan"


def test_main_spawns_both_theme_loops_only_in_the_railway_branch():
    src = (BACKEND / "app" / "main.py").read_text()
    assert _theme_loop_spawn_problems(src) == []
    # and the same scan rejects the real file with one spawn commented out
    commented = src.replace('_spawn(run_theme_rotation_loop(), "theme_rotation")',
                            '# _spawn(run_theme_rotation_loop(), "theme_rotation")')
    assert commented != src, "main.py no longer spells the spawn this way — update the mutant"
    assert _theme_loop_spawn_problems(commented)


# ═════════════════════════════════════════════════════════════════════════════════════
# 12. Findings
# ═════════════════════════════════════════════════════════════════════════════════════


def test_missed_alarm_breadcrumb_leads_somewhere_real():
    """The MISSED ERROR is this feature's loudest log line, so its breadcrumb must lead
    somewhere. It used to say `GET /api/v1/admin/theme-rotation-status` — a route that was
    never built (404). It now names the `theme_rotation_runs` table; and any API route the
    rotation code names in a message must actually be registered."""
    from app.api.v1.api import api_router
    registered = {"/api/v1" + r.path for r in api_router.routes if hasattr(r, "path")}
    pkg = BACKEND / "app" / "services" / "theme_rotation"
    for path in sorted(pkg.glob("*.py")):
        for m in re.finditer(r"(/api/v1/[\w\-/{}]+)", path.read_text()):
            route = m.group(1).rstrip(".")
            assert route in registered, f"{path.name} names {route}, which is not a route"
    src = (pkg / "scheduler.py").read_text()
    missed = src[src.index('"theme rotation MISSED'):]
    missed = missed[:missed.index(")\n")]
    assert "theme_rotation_runs" in missed


@pytest.mark.asyncio
async def test_a_month_never_attempted_is_not_an_error(rot, sched_logs):
    """Enabling the job after a month's 7-day window closed (a first deploy mid-month) is
    not a failure: no run row exists, so the tick logs INFO, never the MISSED ERROR."""
    rot.svc.attempted = False
    now = scheduler.rotation_anchor(2026, 10) + scheduler.CATCHUP + timedelta(hours=1)
    missed: Set[date] = set()
    assert await scheduler._rotation_tick(now, missed) is None
    assert date(2026, 10, 1) in missed
    assert not [r for r in sched_logs.records if r.levelno >= logging.ERROR]
    assert any("enabled after its window" in r.getMessage() for r in sched_logs.records)
    assert "run" not in rot.svc.names()



# ── Same-evening retry of themes that failed on a partial run (2026-09-23 review) ────

_EVENING = datetime(2026, 9, 23, 22, 30, tzinfo=timezone.utc)      # 18:30 ET, a Wednesday


@pytest.mark.asyncio
async def test_a_partial_run_retries_only_the_themes_that_can_recover(ins):
    ins.svc.summary = {"themes_ok": 6, "themes_failed": [
        {"slug": "silicon-rush", "error": "ThemeInsightsError: performance unavailable "
                                          "(as_of_coverage_low:9/16; ...)"},
        {"slug": "empty-theme", "error": "ThemeInsightsError: performance unavailable "
                                         "(no_constituents; ...)"}]}
    assert await scheduler._insights_tick(_EVENING) == scheduler.INSIGHTS_RETRY_SECONDS
    assert ins.claim.runs[0].success is True               # the day itself is done
    ins.svc.summary = {"themes_ok": 1, "themes_failed": []}
    later = _EVENING + timedelta(minutes=30)
    assert await scheduler._insights_tick(later) is None
    assert ins.svc.slug_calls == [None, ["silicon-rush"]]
    assert len(ins.claim.calls) == 1                       # the retry takes no day claim


@pytest.mark.asyncio
async def test_retries_are_bounded(ins):
    failing = {"themes_ok": 7, "themes_failed": [{"slug": "a", "error": "APIError: 503"}]}
    ins.svc.summary = failing
    now = _EVENING
    waits = []
    for _ in range(6):
        waits.append(await scheduler._insights_tick(now))
        now += timedelta(minutes=30)
        if waits[-1] is None:
            break
    assert ins.svc.slug_calls == [None] + [["a"]] * scheduler.INSIGHTS_SAME_DAY_RETRIES
    assert waits[-1] is None


@pytest.mark.asyncio
async def test_a_permanent_failure_is_not_retried(ins):
    ins.svc.summary = {"themes_ok": 7, "themes_failed": [
        {"slug": "empty-theme", "error": "performance unavailable (no_constituents; {})"}]}
    assert await scheduler._insights_tick(_EVENING) is None
    assert scheduler._insights_retry == {}


@pytest.mark.asyncio
async def test_a_retry_left_from_yesterday_is_dropped(ins):
    scheduler._insights_retry[date(2026, 9, 22)] = (0, ["stale"])
    ins.svc.summary = {"themes_ok": 8, "themes_failed": []}
    assert await scheduler._insights_tick(_EVENING) is None
    assert ins.svc.slug_calls == [None] and scheduler._insights_retry == {}
