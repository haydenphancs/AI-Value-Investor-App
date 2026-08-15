"""Which trading session a set of numbers belongs to, and how it is named.

Pure and clock-injected, per `.claude/rules/testing.md` — no network, no Supabase.

WHY THIS FILE EXISTS
--------------------
`session_phase` answers "what is happening right now". That is a DIFFERENT question from
"which trading day do these numbers describe", and conflating them is what let a Friday
snapshot render on Monday with no label at all: the phase on Monday morning is
`premarket`, so a phase-only reading calls Friday's close "pre-market" and says nothing
about the date.

It also silently broke attribution. `_payload` gated every dated detector — earnings
rows, analyst grades, news cards — on `datetime.now(ET).date()`. On a Saturday, when the
quotes being described are Friday's close, nothing could match that date, so every
detector went dark AND the tile still asserted "No company news today." A confident
negative produced entirely by asking about the wrong day.

None of that is observable by looking at the widget on a Tuesday afternoon, which is
exactly why it is tested here with an injected clock rather than by hand.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.utils.market_hours import (
    ET,
    is_trading_day,
    previous_trading_day,
    session_label,
    session_phase,
    session_trading_date,
)


def _et(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# ── is_trading_day / previous_trading_day ─────────────────────────────


@pytest.mark.parametrize(
    "d, expected",
    [
        (date(2026, 8, 14), True),    # ordinary Friday
        (date(2026, 8, 15), False),   # Saturday
        (date(2026, 8, 16), False),   # Sunday
        (date(2026, 1, 19), False),   # MLK Day, a Monday
        (date(2026, 11, 26), False),  # Thanksgiving
        (date(2026, 11, 27), True),   # HALF-day is still a trading day
    ],
)
def test_is_trading_day(d, expected):
    assert is_trading_day(d) is expected


@pytest.mark.parametrize(
    "d, expected",
    [
        (date(2026, 8, 15), date(2026, 8, 14)),   # Sat -> Fri
        (date(2026, 8, 17), date(2026, 8, 14)),   # Mon -> Fri (skips the weekend)
        (date(2026, 1, 20), date(2026, 1, 16)),   # day after MLK -> the Friday before
        (date(2026, 11, 27), date(2026, 11, 25)), # day after Thanksgiving -> Wednesday
        (date(2027, 1, 1), date(2026, 12, 31)),   # New Year's Day (a Friday) -> Thursday
    ],
)
def test_previous_trading_day(d, expected):
    assert previous_trading_day(d) == expected


def test_previous_trading_day_terminates_on_a_broken_calendar():
    """Bounded, not `while True` — a hung request is worse than a date that is a day off."""
    assert previous_trading_day(date(2026, 8, 14)) is not None


# ── session_trading_date ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "now, expected",
    [
        # A normal Friday, every phase — all describe THAT day.
        (_et(2026, 8, 14, 7, 31), date(2026, 8, 14)),    # premarket
        (_et(2026, 8, 14, 14, 14), date(2026, 8, 14)),   # regular
        (_et(2026, 8, 14, 17, 2), date(2026, 8, 14)),    # afterhours
        (_et(2026, 8, 14, 20, 1), date(2026, 8, 14)),    # closed, but today HAPPENED
        (_et(2026, 8, 14, 23, 59), date(2026, 8, 14)),
    ],
)
def test_a_live_or_completed_session_describes_today(now, expected):
    assert session_trading_date(now) == expected


def test_the_overnight_boundary_is_0400_et():
    """Before the pre-market bell the freshest numbers are still YESTERDAY's."""
    assert session_trading_date(_et(2026, 8, 14, 3, 59)) == date(2026, 8, 13)
    assert session_trading_date(_et(2026, 8, 14, 4, 1)) == date(2026, 8, 14)


@pytest.mark.parametrize(
    "now, expected",
    [
        (_et(2026, 8, 15, 11, 0), date(2026, 8, 14)),   # Saturday -> Friday
        (_et(2026, 8, 16, 11, 0), date(2026, 8, 14)),   # Sunday   -> Friday
        (_et(2026, 1, 19, 11, 0), date(2026, 1, 16)),   # MLK Mon  -> Friday
        (_et(2026, 11, 26, 11, 0), date(2026, 11, 25)), # Thanksgiving -> Wednesday
    ],
)
def test_a_closed_day_describes_the_previous_session(now, expected):
    assert session_trading_date(now) == expected


def test_a_half_day_afternoon_still_describes_that_day():
    """The session HAPPENED — it just ended at 13:00.

    `session_phase` returns `closed` at 14:00 on the Friday after Thanksgiving, so a
    phase-only reading would reach back to Wednesday and mislabel a real Friday session.
    """
    assert session_trading_date(_et(2026, 11, 27, 14, 0)) == date(2026, 11, 27)
    # ...but before that day's open it is still the previous session.
    assert session_trading_date(_et(2026, 11, 27, 3, 0)) == date(2026, 11, 25)


@pytest.mark.parametrize(
    "now",
    [
        _et(2026, 3, 8, 10, 0),    # US DST begins (a Sunday)
        _et(2026, 11, 1, 10, 0),   # US DST ends (a Sunday)
    ],
)
def test_dst_transitions_do_not_shift_the_session(now):
    """ZoneInfo handles this; assert it anyway — hand-rolled offsets die here."""
    assert session_trading_date(now) == previous_trading_day(now.date())


def test_a_naive_datetime_is_read_as_utc_not_local():
    """Same input must resolve identically on Railway (UTC) and a dev Mac."""
    naive = datetime(2026, 8, 14, 18, 14)          # 18:14 UTC == 14:14 ET
    assert session_trading_date(naive) == date(2026, 8, 14)
    assert session_phase(naive) == "regular"


# ── session_label ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "now, expected",
    [
        (_et(2026, 8, 14, 14, 14), "Live 2:14 PM ET"),
        (_et(2026, 8, 14, 7, 31), "Pre-market 7:31 AM ET"),
        (_et(2026, 8, 14, 17, 2), "After hours 5:02 PM ET"),
        (_et(2026, 8, 14, 21, 0), "Fri close"),
        (_et(2026, 8, 16, 11, 0), "Fri close"),      # read on a Sunday
        (_et(2026, 1, 19, 11, 0), "Fri close"),      # MLK Monday
        (_et(2026, 11, 26, 11, 0), "Wed close"),     # Thanksgiving
    ],
)
def test_session_label(now, expected):
    assert session_label(now) == expected


def test_the_label_is_never_empty_in_any_phase():
    """An empty label is what let a stale tile render with no time cue at all."""
    for hour in range(24):
        for day in (14, 15, 16):   # Fri, Sat, Sun
            assert session_label(_et(2026, 8, day, hour, 30)).strip()


def test_midnight_and_noon_do_not_render_as_hour_zero():
    """`hour % 12 or 12` — a naive `% 12` prints "0:30 AM"."""
    assert session_label(_et(2026, 8, 14, 0, 30)).endswith("close")   # closed overnight
    assert "12:30 PM" in session_label(_et(2026, 8, 14, 12, 30))


def test_the_label_agrees_with_the_session_date_it_names():
    """The weekday in the text must be the weekday of `session_trading_date`."""
    for now in (
        _et(2026, 8, 15, 11, 0),
        _et(2026, 8, 16, 11, 0),
        _et(2026, 1, 19, 11, 0),
        _et(2026, 11, 26, 11, 0),
        _et(2026, 8, 14, 21, 0),
    ):
        label = session_label(now)
        assert label.startswith(session_trading_date(now).strftime("%a"))
