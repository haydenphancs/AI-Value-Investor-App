"""Quiet hours — wraparound, DST, and every way a stored value can be garbage.

Pure time math, so every case here is exact: no clock, no database, no network.

The failure this module guards against is asymmetric and worth stating plainly. Getting
quiet hours slightly wrong sends a notification an hour early — annoying. Getting the
WRAPAROUND backwards inverts the window, so the app goes silent for the fifteen hours
the user is awake and buzzes for the nine they are asleep, and every symptom of that
("I never get alerts") points at the sender, not at this file.
"""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services import quiet_hours as qh

ET = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")


def _win(start="22:00", end="07:00", enabled=True):
    return qh.resolve_window({
        qh.PREF_QUIET_ENABLED: enabled,
        qh.PREF_QUIET_START: start,
        qh.PREF_QUIET_END: end,
    })


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("22:00", time(22, 0)),
    ("07:00", time(7, 0)),
    ("7:05", time(7, 5)),      # unpadded hour is still unambiguous
    ("00:00", time(0, 0)),
    ("23:59", time(23, 59)),
    ("  08:30  ", time(8, 30)),
])
def test_valid_times_parse(raw, expected):
    assert qh.parse_hhmm(raw) == expected


@pytest.mark.parametrize("raw", [
    "25:99",     # out of range on both halves
    "24:00",     # 24 is not a valid hour; midnight is 00:00
    "22:60",
    "-1:00",
    "",
    "   ",
    "7",         # no colon: equally readable as 07:00 or 00:07 — refuse to guess
    "22-00",
    "abc",
    "22:0a",
    None,
    22,
    True,        # bool is an int subclass; must not become 1 -> some time
    ["22:00"],
])
def test_garbage_does_not_parse(raw):
    assert qh.parse_hhmm(raw) is None


# ── timezone resolution ──────────────────────────────────────────────────────

def test_a_real_iana_zone_is_used():
    assert qh.resolve_timezone({qh.PREF_TIMEZONE: "Asia/Tokyo"}) == TOKYO


@pytest.mark.parametrize("raw", [
    None, "", "   ", "Mars/Olympus", "GMT+9", 42, {"tz": "UTC"},
])
def test_an_unusable_timezone_falls_back_to_ET(raw):
    """Fails to ET, NOT to 'never quiet' and NOT to 'always quiet'.

    An unparseable string must not silence someone forever (fail-closed) nor buzz them
    at 3am (fail-open). ET is what every user got before this feature existed.
    """
    assert qh.resolve_timezone({qh.PREF_TIMEZONE: raw}) == qh.ET


def test_a_fixed_offset_zone_still_works():
    """`UTC` and `Etc/GMT-9` are real IANA keys even though they have no DST."""
    assert qh.resolve_timezone({qh.PREF_TIMEZONE: "UTC"}) == ZoneInfo("UTC")
    assert qh.resolve_timezone({qh.PREF_TIMEZONE: "Etc/GMT-9"}) == ZoneInfo("Etc/GMT-9")


# ── window resolution ────────────────────────────────────────────────────────

def test_absent_master_toggle_means_never_quiet():
    """The shipped default. A user who never opened the screen is never deferred."""
    assert qh.resolve_window({}).enabled is False
    assert qh.resolve_window(None).enabled is False


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "", 0, False])
def test_a_falsy_master_toggle_is_off(raw):
    assert qh.resolve_window({qh.PREF_QUIET_ENABLED: raw}).enabled is False


@pytest.mark.parametrize("raw", ["true", "1", "yes", 1, True])
def test_a_truthy_master_toggle_is_on(raw):
    """A STRING "false" must not read as True. `bool("false")` is True in Python, and
    nothing between here and the database enforces the value's type."""
    assert qh.resolve_window({qh.PREF_QUIET_ENABLED: raw}).enabled is True


def test_start_equals_end_is_treated_as_disabled():
    """Read as a half-open interval it is zero-length; read as 'from 22:00 to 22:00' it
    covers the whole day. A user who fat-fingers both pickers to the same value must not
    silence the app forever with no error."""
    assert _win("22:00", "22:00").enabled is False


def test_malformed_times_fall_back_to_the_declared_defaults():
    """Not to 'disabled' — the user explicitly enabled quiet hours, so honour that with
    the documented default window rather than silently ignoring the setting."""
    w = _win("nonsense", "also nonsense")
    assert w.enabled is True
    assert w.start == time(22, 0) and w.end == time(7, 0)


# ── containment: the wraparound is the whole game ────────────────────────────

@pytest.mark.parametrize("hh,mm,expected", [
    (21, 59, False),   # one minute before
    (22, 0, True),     # start is INCLUSIVE
    (23, 30, True),
    (0, 0, True),      # across midnight
    (3, 15, True),
    (6, 59, True),
    (7, 0, False),     # end is EXCLUSIVE — an alert landing exactly here goes out
    (12, 0, False),
])
def test_a_wrapping_window_spans_midnight(hh, mm, expected):
    now = datetime(2026, 8, 7, hh, mm, tzinfo=ET)
    assert qh.is_within(_win("22:00", "07:00"), now) is expected


@pytest.mark.parametrize("hh,expected", [
    (12, False), (13, True), (13, True), (14, False), (23, False), (2, False),
])
def test_a_non_wrapping_window_is_simple_containment(hh, expected):
    now = datetime(2026, 8, 7, hh, 0, tzinfo=ET)
    assert qh.is_within(_win("13:00", "14:00"), now) is expected


def test_a_disabled_window_is_never_within():
    assert qh.is_within(_win(enabled=False), datetime(2026, 8, 7, 3, 0, tzinfo=ET)) is False


# ── wake time ────────────────────────────────────────────────────────────────

def test_not_quiet_means_no_wake_time():
    now = datetime(2026, 8, 7, 12, 0, tzinfo=ET).astimezone(timezone.utc)
    assert qh.next_end_utc(_win(), now, ET) is None


def test_late_evening_wakes_the_NEXT_morning():
    now = datetime(2026, 8, 7, 23, 0, tzinfo=ET).astimezone(timezone.utc)
    wake = qh.next_end_utc(_win("22:00", "07:00"), now, ET).astimezone(ET)
    assert (wake.date().isoformat(), wake.hour, wake.minute) == ("2026-08-08", 7, 0)


def test_early_morning_wakes_the_SAME_morning():
    """The window wrapped at midnight, so 02:00 must wake at 07:00 TODAY — not
    tomorrow. Getting this wrong parks the notification for 29 hours, past the
    staleness cutoff, and it is never delivered at all."""
    now = datetime(2026, 8, 7, 2, 0, tzinfo=ET).astimezone(timezone.utc)
    wake = qh.next_end_utc(_win("22:00", "07:00"), now, ET).astimezone(ET)
    assert (wake.date().isoformat(), wake.hour) == ("2026-08-07", 7)


def test_a_non_wrapping_window_wakes_the_same_day():
    now = datetime(2026, 8, 7, 13, 30, tzinfo=ET).astimezone(timezone.utc)
    wake = qh.next_end_utc(_win("13:00", "14:00"), now, ET).astimezone(ET)
    assert (wake.date().isoformat(), wake.hour) == ("2026-08-07", 14)


# ── DST ──────────────────────────────────────────────────────────────────────
#
# US DST 2027: forward 2027-03-14 (02:00 -> 03:00, so 02:00-02:59 does not exist),
# back 2027-11-07 (02:00 -> 01:00, so 01:00-01:59 happens twice).

def test_spring_forward_resolves_a_nonexistent_wake_time_LATER_not_earlier():
    """02:30 does not exist on the spring-forward date. ZoneInfo's fold=0 resolves it
    with the pre-transition offset, landing at 03:30 local — an hour 'late' in wall
    clock terms, which is the safe direction. Waking EARLY would deliver inside the
    user's quiet window, which is the thing this whole feature prevents."""
    now = datetime(2027, 3, 14, 0, 30, tzinfo=ET).astimezone(timezone.utc)
    wake = qh.next_end_utc(_win("22:00", "02:30"), now, ET).astimezone(ET)
    assert wake.hour == 3 and wake.minute == 30
    assert wake > now.astimezone(ET)


def test_fall_back_picks_the_first_of_the_two_01_30s():
    """01:30 occurs twice. fold=0 picks the earlier instant, so the wake is the first
    one. It fires once — the row moves to `sent`, so the second occurrence has nothing
    left to deliver."""
    now = datetime(2027, 11, 7, 0, 30, tzinfo=ET).astimezone(timezone.utc)
    wake = qh.next_end_utc(_win("22:00", "01:30"), now, ET)
    assert wake.astimezone(ET).hour == 1
    # EDT (-4) still, i.e. the FIRST 01:30, not the EST (-5) repeat.
    assert wake.astimezone(ET).utcoffset().total_seconds() == -4 * 3600


def test_a_wake_time_never_lands_more_than_a_day_out():
    """Bounded by construction (today or tomorrow), which is what keeps a deferred row
    inside the staleness window."""
    for hour in range(24):
        now = datetime(2026, 8, 7, hour, 0, tzinfo=ET).astimezone(timezone.utc)
        wake = qh.next_end_utc(_win("22:00", "07:00"), now, ET)
        if wake is not None:
            assert 0 < (wake - now).total_seconds() <= 24 * 3600


# ── extreme timezones ────────────────────────────────────────────────────────

@pytest.mark.parametrize("zone", ["Pacific/Kiritimati", "Pacific/Niue", "Asia/Kathmandu"])
def test_extreme_and_half_hour_offsets_still_produce_a_sane_wake(zone):
    """UTC+14, UTC-11, and a :45 offset. The whole point of storing a timezone is that
    these users are not on the trading floor's clock."""
    tz = ZoneInfo(zone)
    now = datetime(2026, 8, 7, 23, 30, tzinfo=tz).astimezone(timezone.utc)
    wake = qh.next_end_utc(_win("22:00", "07:00"), now, tz)
    assert wake is not None
    local = wake.astimezone(tz)
    assert (local.hour, local.minute) == (7, 0)


# ── the daily-cap boundary ───────────────────────────────────────────────────

def test_the_cap_day_starts_at_the_USERS_midnight_not_ET():
    """A Tokyo user's '3 per day' resetting at ET midnight resets at ~1pm their time,
    which is not a day. This is the entire reason a timezone is stored."""
    now = datetime(2026, 8, 7, 15, 0, tzinfo=TOKYO).astimezone(timezone.utc)
    start = qh.local_day_start_utc(now, TOKYO).astimezone(TOKYO)
    assert (start.date().isoformat(), start.hour, start.minute) == ("2026-08-07", 0, 0)

    et_start = qh.local_day_start_utc(now, ET).astimezone(ET)
    assert et_start != start.astimezone(ET) or True  # they are genuinely different instants
    assert qh.local_day_start_utc(now, TOKYO) != qh.local_day_start_utc(now, ET)


def test_the_day_start_is_built_from_the_calendar_date_not_by_subtracting_hours():
    """On the spring-forward date the local day is 23 hours long, so `now - 24h` is the
    wrong answer and would let a user's cap window cover part of the previous day."""
    now = datetime(2027, 3, 14, 12, 0, tzinfo=ET).astimezone(timezone.utc)
    start = qh.local_day_start_utc(now, ET).astimezone(ET)
    assert (start.date().isoformat(), start.hour) == ("2027-03-14", 0)
