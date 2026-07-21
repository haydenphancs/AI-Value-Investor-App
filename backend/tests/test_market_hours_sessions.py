"""
Session-phase boundaries.

`session_phase()` splits what `is_market_active()` collapses into one boolean.
The distinction is load-bearing for spend: the Updates insight sweeper wakes at
04:00 ET, so without a pre-market/regular split a busy scope burned its whole
daily LLM allowance before the opening bell and sat frozen through the session
(observed live — `__MARKET__` last generated 05:49 ET at 6/6). Every boundary
below is therefore a money boundary, not a cosmetic one.

All cases pin an explicit ET instant; nothing here reads the wall clock.
"""

from datetime import datetime, timezone

import pytest

from app.utils.market_hours import (
    ET,
    SESSION_AFTERHOURS,
    SESSION_CLOSED,
    SESSION_PREMARKET,
    SESSION_REGULAR,
    is_market_active,
    session_phase,
)


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# 2026-07-21 is a Tuesday and not a holiday.
@pytest.mark.parametrize("hh,mm,expected", [
    (0, 0, SESSION_CLOSED),       # midnight
    (3, 59, SESSION_CLOSED),      # one minute before the sweeper wakes
    (4, 0, SESSION_PREMARKET),    # sweeper wakes
    (9, 29, SESSION_PREMARKET),   # last pre-market minute
    (9, 30, SESSION_REGULAR),     # opening bell
    (15, 59, SESSION_REGULAR),    # last regular minute
    (16, 0, SESSION_AFTERHOURS),  # closing bell — earnings window opens
    (19, 59, SESSION_AFTERHOURS), # last minute the sweeper runs
    (20, 0, SESSION_CLOSED),      # sweeper sleeps
    (23, 59, SESSION_CLOSED),
])
def test_weekday_session_boundaries(hh, mm, expected):
    assert session_phase(_et(2026, 7, 21, hh, mm)) == expected


@pytest.mark.parametrize("day", [25, 26])  # Saturday, Sunday
def test_weekends_are_closed_at_every_hour(day):
    for hh in range(24):
        assert session_phase(_et(2026, 7, day, hh, 0)) == SESSION_CLOSED


def test_market_holidays_are_closed_even_at_midday():
    # 2026-11-26 is Thanksgiving, in US_MARKET_HOLIDAYS.
    assert session_phase(_et(2026, 11, 26, 12, 0)) == SESSION_CLOSED


@pytest.mark.parametrize("day", [21, 25, 26])  # Tuesday, Saturday, Sunday
def test_phase_agrees_with_is_market_active_at_every_minute(day):
    """The two helpers must never disagree, or the sweeper would run in a phase
    the budget gate believes is closed (or vice versa).

    This compares the two FUNCTIONS against each other across a full 1440-minute
    grid. An earlier version of this test re-derived the 240/1200 boundaries
    inline and compared them to `session_phase` — which is a transcription of
    the implementation under test, not an independent expectation, and would
    have passed even if `is_market_active` had been deleted.
    """
    for minute in range(24 * 60):
        at = _et(2026, 7, day, minute // 60, minute % 60)
        assert is_market_active(at) is (session_phase(at) != SESSION_CLOSED)


def test_is_market_active_honours_weekends_and_holidays_at_midday():
    assert is_market_active(_et(2026, 7, 21, 12, 0)) is True     # Tuesday
    assert is_market_active(_et(2026, 7, 25, 12, 0)) is False    # Saturday
    assert is_market_active(_et(2026, 11, 26, 12, 0)) is False   # Thanksgiving


def test_is_market_active_boundaries():
    assert is_market_active(_et(2026, 7, 21, 3, 59)) is False
    assert is_market_active(_et(2026, 7, 21, 4, 0)) is True
    assert is_market_active(_et(2026, 7, 21, 19, 59)) is True
    assert is_market_active(_et(2026, 7, 21, 20, 0)) is False


def test_a_utc_instant_is_converted_not_misread():
    """The sweeper passes `datetime.now(timezone.utc)`. Reading its .hour
    directly would put the whole ladder 4-5 hours out and mislabel the entire
    trading day."""
    # 14:00 UTC on 2026-07-21 == 10:00 EDT == regular session.
    utc_noon_ish = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
    assert session_phase(utc_noon_ish) == SESSION_REGULAR
    # 02:00 UTC == 22:00 ET the PREVIOUS day — closed, and a naive read of the
    # UTC hour (2) would also say closed for the wrong reason, so pin a case
    # where the two disagree: 12:00 UTC == 08:00 ET == pre-market.
    assert session_phase(datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)) == SESSION_PREMARKET


def test_a_naive_datetime_is_read_as_utc_not_as_local_time():
    """`astimezone()` on a naive datetime assumes LOCAL time — UTC on Railway,
    America/Denver on the dev machine — so the same input would resolve to
    different sessions in different environments. Every caller means UTC.

    12:00 naive must therefore mean 12:00 UTC == 08:00 EDT == pre-market. Read
    as Denver local it would be 18:00 UTC == 14:00 EDT == regular, which is the
    silent environment-dependent bug this pins shut.
    """
    assert session_phase(datetime(2026, 7, 21, 12, 0)) == SESSION_PREMARKET
    assert session_phase(datetime(2026, 7, 21, 12, 0)) == session_phase(
        datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    )


def test_dst_transition_days_still_resolve_a_phase():
    # 2026-03-08 (spring forward) and 2026-11-01 (fall back) are Sundays, so
    # they are closed; the day after each must behave normally.
    assert session_phase(_et(2026, 3, 9, 10, 0)) == SESSION_REGULAR
    assert session_phase(_et(2026, 11, 2, 10, 0)) == SESSION_REGULAR


def test_is_market_active_is_still_a_plain_bool():
    assert isinstance(is_market_active(), bool)
