"""`learn_weekly.schedule` — the Wiser weekly engine's ET maths (plan §1.3-§1.4, §1.10).

Pure: no network, no Supabase, no clock. The instants below were computed by hand from the
America/New_York rules (EDT = UTC-4 until 2026-11-01 02:00, EST = UTC-5 until 2027-03-14 02:00) and
are pinned in UTC, so a regression to wall-clock arithmetic (`aware + timedelta`) or to a
naive/UTC calendar date shows up as a one-hour or one-day miss, not as a pass.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.services.learn_weekly import contracts
from app.services.learn_weekly import schedule as sch
from app.services.marketing import selection
from app.utils.market_hours import ET

UTC = timezone.utc
H = timedelta(hours=1)
US = timedelta(microseconds=1)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def et(*args) -> datetime:
    return datetime(*args, tzinfo=ET)


@pytest.fixture(autouse=True)
def _fresh_caches_and_defaults(monkeypatch):
    """Every test starts from config's declared defaults and empty log-once caches, so a test that
    patches a setting or asserts on a log line never depends on test order."""
    for name in sch._SETTING_BOUNDS:
        monkeypatch.setattr(sch.settings, name, Settings.model_fields[name].default)
    monkeypatch.setattr(sch.settings, "LEARN_SLOT_WEEKDAYS", Settings.model_fields["LEARN_SLOT_WEEKDAYS"].default)
    for cached in (sch._parse_slot_weekdays_text, sch._log_bad_setting, sch._warn_if_out_of_order,
                   sch._log_cutoff_fallback):
        cached.cache_clear()
    yield
    for cached in (sch._parse_slot_weekdays_text, sch._log_bad_setting, sch._warn_if_out_of_order,
                   sch._log_cutoff_fallback):
        cached.cache_clear()


def _fridays(start: date, end: date):
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    while d <= end:
        yield d
        d += timedelta(days=7)


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ── LEARN_SLOT_WEEKDAYS ───────────────────────────────────────────────────────────────────────


def test_weekday_tokens_are_marketings_vocabulary():
    # config documents LEARN_SLOT_WEEKDAYS as "normalised like MARKETING_POST_WEEKDAYS".
    assert sch.WEEKDAY_TOKENS == selection.WEEKDAY_TOKENS


def test_config_default_parses_to_monday_only():
    assert Settings.model_fields["LEARN_SLOT_WEEKDAYS"].default == "mon"
    assert sch.parse_slot_weekdays("mon") == sch.DEFAULT_SLOT_WEEKDAYS == (0,)


@pytest.mark.parametrize("raw, expected, logged", [
    ("mon", (0,), False),
    ("mon,sat", (0, 5), False),
    ("sat,mon", (0, 5), False),                         # sorted, whatever the order typed
    (" MON , Sat ,mon,MON", (0, 5), False),             # stripped, case-folded, duplicates collapsed
    ("mon,,sat,", (0, 5), False),                       # empty tokens skipped silently
    ("mon,tue,wed,thu,fri,sat,sun", (0, 1, 2, 3, 4, 5, 6), False),
    ("mon,funday", (0,), True),                         # unknown dropped, ERROR
    ("monday,sat", (5,), True),                         # three-letter tokens only
    ("funday", (0,), True),                             # nothing left → default, ERROR
    ("", (0,), True),
    (" , ,", (0,), True),
    ("sunday", (0,), True),
    ("1,5", (0,), True),                                # numbers are not tokens
])
def test_parse_slot_weekdays_matrix(caplog, raw, expected, logged):
    with caplog.at_level(logging.ERROR, logger=sch.logger.name):
        assert sch.parse_slot_weekdays(raw) == expected
    assert any(r.levelno == logging.ERROR for r in caplog.records) is logged


@pytest.mark.parametrize("raw", [None, 5, 0, True, ["mon"], b"mon", 1.0])
def test_parse_slot_weekdays_non_string_is_the_default_with_an_error(caplog, raw):
    with caplog.at_level(logging.ERROR, logger=sch.logger.name):
        assert sch.parse_slot_weekdays(raw) == (0,)
    assert any("not a string" in r.getMessage() for r in caplog.records)


def test_parse_slot_weekdays_logs_once_per_distinct_value(caplog):
    with caplog.at_level(logging.ERROR, logger=sch.logger.name):
        for _ in range(5):
            sch.parse_slot_weekdays("mon,funday")
    assert sum("funday" in r.getMessage() for r in caplog.records) == 1


def test_parse_slot_weekdays_bounds_what_it_logs(caplog):
    junk = ",".join(f"x{i}" + "y" * 200 for i in range(40))
    with caplog.at_level(logging.ERROR, logger=sch.logger.name):
        assert sch.parse_slot_weekdays(junk) == (0,)
    message = next(r.getMessage() for r in caplog.records if "unknown day" in r.getMessage())
    assert len(message) < 400


def test_slot_weekdays_reads_the_setting_at_call_time(monkeypatch):
    assert sch.slot_weekdays() == (0,)
    monkeypatch.setattr(sch.settings, "LEARN_SLOT_WEEKDAYS", "mon,sat")
    assert sch.slot_weekdays() == (0, 5)
    monkeypatch.setattr(sch.settings, "LEARN_SLOT_WEEKDAYS", None)
    assert sch.slot_weekdays() == (0,)


# ── Settings readers ──────────────────────────────────────────────────────────────────────────


def test_setting_bounds_mirror_configs_field_bounds():
    """A bound here that drifted from config's `Field(ge, le)` would either refuse a value the
    deploy accepted or accept one it refuses."""
    for name, (lo, hi) in sch._SETTING_BOUNDS.items():
        meta = Settings.model_fields[name].metadata
        ge = next(m.ge for m in meta if hasattr(m, "ge"))
        le = next(m.le for m in meta if hasattr(m, "le"))
        assert (lo, hi) == (ge, le), name


def test_search_window_covers_the_largest_lead_and_hold():
    lead_hi = sch._SETTING_BOUNDS["LEARN_SLOT_BUILD_LEAD_HOURS"][1]
    hold_hi = sch._SETTING_BOUNDS["LEARN_REVIEW_HOLD_HOURS"][1]
    assert sch._SLOT_SEARCH_DAYS_AHEAD >= math.ceil(lead_hi / 24) + 1
    assert sch._SLOT_SEARCH_DAYS_BEHIND >= math.ceil(hold_hi / 24) + 1


@pytest.mark.parametrize("bad", [True, False, 24, -1, "18", 18.0, None, 10**9])
def test_a_bad_hour_setting_falls_back_to_configs_default_with_an_error(monkeypatch, caplog, bad):
    monkeypatch.setattr(sch.settings, "LEARN_TOPIC_HOUR_ET", bad)
    with caplog.at_level(logging.ERROR, logger=sch.logger.name):
        times = sch.deep_dive_times_for(date(2026, 10, 23))
    assert times.topic_at == et(2026, 10, 22, Settings.model_fields["LEARN_TOPIC_HOUR_ET"].default)
    assert any("LEARN_TOPIC_HOUR_ET" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("name, bad", [
    ("LEARN_REVIEW_HOLD_HOURS", 0), ("LEARN_REVIEW_HOLD_HOURS", 73), ("LEARN_REVIEW_HOLD_HOURS", True),
    ("LEARN_SLOT_BUILD_LEAD_HOURS", 5), ("LEARN_SLOT_BUILD_LEAD_HOURS", 97),
    ("LEARN_SLOT_BUILD_LEAD_HOURS", float("nan")),
])
def test_a_bad_duration_setting_falls_back_to_configs_default(monkeypatch, name, bad):
    good = sch.slot_times_for(date(2026, 10, 26))
    monkeypatch.setattr(sch.settings, name, bad)
    assert sch.slot_times_for(date(2026, 10, 26)) == good


def test_a_bad_setting_logs_once_per_value(monkeypatch, caplog):
    monkeypatch.setattr(sch.settings, "LEARN_DEEP_DIVE_PUBLISH_HOUR_ET", 99)
    with caplog.at_level(logging.ERROR, logger=sch.logger.name):
        for _ in range(4):
            sch.deep_dive_times_for(date(2026, 10, 23))
    assert sum("LEARN_DEEP_DIVE_PUBLISH_HOUR_ET" in r.getMessage() for r in caplog.records) == 1


def test_hours_are_read_at_call_time(monkeypatch):
    before = sch.deep_dive_times_for(date(2026, 10, 23))
    monkeypatch.setattr(sch.settings, "LEARN_DEEP_DIVE_PUBLISH_HOUR_ET", 22)
    monkeypatch.setattr(sch.settings, "LEARN_TOPIC_HOUR_ET", 17)
    monkeypatch.setattr(sch.settings, "LEARN_REVIEW_HOLD_HOURS", 24)
    after = sch.deep_dive_times_for(date(2026, 10, 23))
    assert before.publish_at == et(2026, 10, 23, 23)
    assert after.publish_at == et(2026, 10, 23, 22)
    assert after.topic_at == et(2026, 10, 22, 17)
    assert after.hold_until == et(2026, 10, 24, 22)


def test_an_out_of_order_friday_is_computed_as_set_but_warned_once(monkeypatch, caplog):
    monkeypatch.setattr(sch.settings, "LEARN_DEEP_DIVE_BUILD_HOUR_ET", 6)      # before the 07:00 auto-pick
    with caplog.at_level(logging.WARNING, logger=sch.logger.name):
        for _ in range(3):
            times = sch.deep_dive_times_for(date(2026, 10, 23))
    assert times.build_at == et(2026, 10, 23, 6)                              # never clamped
    assert sum("Friday order" in r.getMessage() for r in caplog.records) == 1


def test_the_default_friday_is_in_order_and_silent(caplog):
    with caplog.at_level(logging.WARNING, logger=sch.logger.name):
        t = sch.deep_dive_times_for(date(2026, 10, 23))
    assert t.topic_at < t.auto_pick_at < t.build_at < t.deadman_at < t.publish_at < t.hold_until
    assert not caplog.records


# ── Deep Dive times ───────────────────────────────────────────────────────────────────────────


def test_this_weeks_deep_dive_matches_the_plans_timeline():
    t = sch.deep_dive_times_for(date(2026, 10, 23))
    assert t.friday == date(2026, 10, 23)
    assert (t.iso_year, t.iso_week) == (2026, 43)
    assert t.period_key == "2026-W43:deep_dive" == contracts.deep_dive_period_key(2026, 43)
    assert contracts.is_period_key(t.period_key)
    assert t.topic_at == utc(2026, 10, 22, 22)        # Thu 18:00 EDT
    assert t.auto_pick_at == utc(2026, 10, 23, 11)    # Fri 07:00 EDT
    assert t.build_at == utc(2026, 10, 23, 12)        # Fri 08:00 EDT
    assert t.deadman_at == utc(2026, 10, 23, 19)      # Fri 15:00 EDT
    assert t.publish_at == utc(2026, 10, 24, 3)       # Fri 23:00 EDT
    assert t.hold_until == utc(2026, 10, 26, 3)       # Sun 23:00 EDT
    assert t.sort_order == -202643
    for instant in (t.topic_at, t.auto_pick_at, t.build_at, t.deadman_at, t.publish_at, t.hold_until):
        assert instant.utcoffset() == timedelta(0)                 # instants are returned in UTC


def test_every_day_of_an_iso_week_maps_to_that_weeks_friday():
    monday = date(2026, 10, 19)
    for offset in range(7):
        assert sch.deep_dive_times_for(monday + timedelta(days=offset)).friday == date(2026, 10, 23)
    assert sch.deep_dive_times_for(monday - timedelta(days=1)).friday == date(2026, 10, 16)
    assert sch.deep_dive_times_for(monday + timedelta(days=7)).friday == date(2026, 10, 30)


@pytest.mark.parametrize("bad, exc", [
    (datetime(2026, 10, 23, 12, tzinfo=UTC), TypeError),      # a datetime is refused, aware or not
    (datetime(2026, 10, 23, 12), TypeError),
    ("2026-10-23", TypeError),
    (None, TypeError),
    (20261023, TypeError),
])
def test_deep_dive_times_for_refuses_non_dates(bad, exc):
    with pytest.raises(exc):
        sch.deep_dive_times_for(bad)


def test_dst_fall_back_2026_11_01():
    # Friday before the change: publish 23:00 EDT; the 48 h hold is 48 ELAPSED hours, so it ends at
    # Sun 22:00 EST — wall-clock arithmetic would say 23:00 EST, an hour late (49 h).
    before = sch.deep_dive_times_for(date(2026, 10, 30))
    assert before.period_key == "2026-W44:deep_dive"
    assert before.publish_at == utc(2026, 10, 31, 3)
    assert before.hold_until == utc(2026, 11, 2, 3)
    assert before.hold_until.astimezone(ET).replace(tzinfo=None) == datetime(2026, 11, 1, 22)
    # Friday after: back to 23:00 local, now EST (UTC-5).
    after = sch.deep_dive_times_for(date(2026, 11, 6))
    assert after.topic_at == utc(2026, 11, 5, 23)
    assert after.publish_at == utc(2026, 11, 7, 4)
    assert after.hold_until == utc(2026, 11, 9, 4)


def test_dst_spring_forward_2027_03_14():
    before = sch.deep_dive_times_for(date(2027, 3, 12))
    assert before.publish_at == utc(2027, 3, 13, 4)                # Fri 23:00 EST
    assert before.hold_until == utc(2027, 3, 15, 4)                # 48 h later = Mon 00:00 EDT
    assert before.hold_until.astimezone(ET).replace(tzinfo=None) == datetime(2027, 3, 15, 0)
    after = sch.deep_dive_times_for(date(2027, 3, 19))
    assert after.publish_at == utc(2027, 3, 20, 3)                 # Fri 23:00 EDT
    assert after.topic_at == utc(2027, 3, 18, 22)


def test_three_years_of_fridays_keep_wall_hours_and_exact_hold():
    for friday in _fridays(date(2026, 1, 1), date(2028, 12, 31)):
        t = sch.deep_dive_times_for(friday)
        assert t.friday == friday
        assert t.publish_at.astimezone(ET).hour == 23 and t.publish_at.astimezone(ET).date() == friday
        assert t.topic_at.astimezone(ET).hour == 18
        assert t.topic_at.astimezone(ET).date() == friday - timedelta(days=1)
        for instant, hour in ((t.auto_pick_at, 7), (t.build_at, 8), (t.deadman_at, 15)):
            assert instant.astimezone(ET).hour == hour and instant.astimezone(ET).date() == friday
        # Elapsed, compared in UTC (aware subtraction across zones is absolute).
        assert t.hold_until.astimezone(UTC) - t.publish_at.astimezone(UTC) == timedelta(hours=48)


def test_iso_week_53_and_the_year_boundary():
    # Pinned from date.isocalendar(): 2026 starts on a Thursday, so it has an ISO week 53, and the
    # Friday of that week is New Year's Day 2027.
    assert tuple(date(2026, 12, 31).isocalendar()) == (2026, 53, 4)
    assert tuple(date(2027, 1, 1).isocalendar()) == (2026, 53, 5)
    assert tuple(date(2027, 1, 4).isocalendar()) == (2027, 1, 1)
    w53 = sch.deep_dive_times_for(date(2026, 12, 31))
    assert w53.friday == date(2027, 1, 1)
    assert (w53.iso_year, w53.iso_week) == (2026, 53)
    assert w53.period_key == "2026-W53:deep_dive"                   # the Friday's ISO year, not 2027
    assert w53.topic_at == utc(2026, 12, 31, 23)                    # Thu 18:00 EST
    assert w53.publish_at == utc(2027, 1, 2, 4)                     # Fri 2027-01-01 23:00 EST
    assert sch.deep_dive_times_for(date(2027, 1, 3)).period_key == "2026-W53:deep_dive"   # Sunday
    w01 = sch.deep_dive_times_for(date(2027, 1, 4))
    assert w01.friday == date(2027, 1, 8) and w01.period_key == "2027-W01:deep_dive"
    assert w53.sort_order == -202653 > w01.sort_order == -202701


def test_holiday_fridays_are_computed_like_any_friday():
    # Publish-or-skip on 2026-12-25 / 2027-01-01 is an OPEN owner decision (plan): the maths must
    # not decide it, so both get an ordinary schedule.
    xmas = sch.deep_dive_times_for(date(2026, 12, 25))
    assert xmas.period_key == "2026-W52:deep_dive"
    assert xmas.publish_at == utc(2026, 12, 26, 4)
    assert sch.deep_dive_times_for(date(2027, 1, 1)).publish_at == utc(2027, 1, 2, 4)


# ── current_deep_dive ─────────────────────────────────────────────────────────────────────────


def test_current_deep_dive_at_each_boundary_instant():
    t = sch.deep_dive_times_for(date(2026, 10, 23))
    assert sch.current_deep_dive(t.topic_at) == t                  # exactly topic_at: inside
    assert sch.current_deep_dive(t.topic_at - US) is None          # Thu 17:59:59.999999: nothing
    assert sch.current_deep_dive(t.hold_until) == t                # exactly hold_until: inside
    assert sch.current_deep_dive(t.hold_until + US) is None
    assert sch.current_deep_dive(t.publish_at) == t
    # Saturday and Sunday belong to the same ISO week; still this week's piece.
    assert sch.current_deep_dive(et(2026, 10, 25, 12)) == t


def test_current_deep_dive_accepts_any_aware_zone():
    t = sch.deep_dive_times_for(date(2026, 10, 23))
    # Sat 03:30 UTC is still Friday 23:30 in ET.
    assert sch.current_deep_dive(utc(2026, 10, 24, 3, 30)) == t
    plus9 = timezone(timedelta(hours=9))
    assert sch.current_deep_dive(datetime(2026, 10, 24, 12, 30, tzinfo=plus9)) == t


def test_current_deep_dive_across_fall_back_uses_elapsed_hold():
    t = sch.deep_dive_times_for(date(2026, 10, 30))
    assert sch.current_deep_dive(et(2026, 11, 1, 22)) == t
    # 22:30 EST is 48.5 h after publish: outside. Wall-clock arithmetic would still say inside.
    assert sch.current_deep_dive(et(2026, 11, 1, 22, 30)) is None


def test_current_deep_dive_with_the_longest_hold_reaches_into_the_next_iso_week(monkeypatch):
    monkeypatch.setattr(sch.settings, "LEARN_REVIEW_HOLD_HOURS", 72)
    t = sch.deep_dive_times_for(date(2026, 10, 23))
    assert t.hold_until == et(2026, 10, 26, 23)                    # Monday of ISO week 44
    assert sch.current_deep_dive(et(2026, 10, 26, 22)) == t
    assert sch.current_deep_dive(et(2026, 10, 26, 23) + US) is None


@pytest.mark.parametrize("topic_hour, hold", [(18, 48), (0, 72), (23, 1)])
def test_current_deep_dive_matches_a_brute_force_scan(monkeypatch, topic_hour, hold):
    """Every 30 minutes over half a year spanning both DST changes and ISO week 53: the answer is
    exactly the one Friday (if any) whose window contains the instant."""
    monkeypatch.setattr(sch.settings, "LEARN_TOPIC_HOUR_ET", topic_hour)
    monkeypatch.setattr(sch.settings, "LEARN_REVIEW_HOLD_HOURS", hold)
    windows = [sch.deep_dive_times_for(f) for f in _fridays(date(2026, 9, 20), date(2027, 4, 30))]
    now = utc(2026, 10, 1)
    end = utc(2027, 4, 1)
    while now < end:
        hits = [w for w in windows if w.topic_at <= now <= w.hold_until]
        assert len(hits) <= 1
        assert sch.current_deep_dive(now) == (hits[0] if hits else None), now
        now += timedelta(minutes=30)


@pytest.mark.parametrize("bad, exc", [(datetime(2026, 10, 23, 12), ValueError), (date(2026, 10, 23), TypeError),
                                      ("2026-10-23T12:00:00Z", TypeError), (None, TypeError)])
def test_current_deep_dive_refuses_naive_and_non_datetimes(bad, exc):
    with pytest.raises(exc):
        sch.current_deep_dive(bad)


# ── Slots ─────────────────────────────────────────────────────────────────────────────────────


def test_monday_slot_matches_the_plan():
    # Plan §1.4: build Sat 12:00, publish Mon 05:00, hold until Wed 05:00 (41 h lead, 48 h hold).
    s = sch.slot_times_for(date(2026, 10, 26))
    assert s.period_key == "2026-10-26:slot" == contracts.slot_period_key(date(2026, 10, 26))
    assert contracts.is_period_key(s.period_key)
    assert s.build_at == et(2026, 10, 24, 12)
    assert s.publish_at == et(2026, 10, 26, 5)
    assert s.hold_until == et(2026, 10, 28, 5)
    assert s.sort_order == 100000 + 298


def test_slot_lead_and_hold_are_elapsed_hours_across_fall_back():
    s = sch.slot_times_for(date(2026, 11, 2))
    assert s.publish_at == utc(2026, 11, 2, 10)                    # Mon 05:00 EST
    assert s.build_at == utc(2026, 10, 31, 17)                     # 41 h before = Sat 13:00 EDT
    assert s.hold_until == utc(2026, 11, 4, 10)
    assert s.publish_at.astimezone(UTC) - s.build_at.astimezone(UTC) == timedelta(hours=41)


def test_slot_wall_hours_inside_a_dst_gap_or_overlap(monkeypatch):
    monkeypatch.setattr(sch.settings, "LEARN_SLOT_PUBLISH_HOUR_ET", 2)
    # 2027-03-14 02:00 does not exist: fold=0 moves it past the gap, to 03:00 EDT (07:00 UTC).
    assert sch.slot_times_for(date(2027, 3, 14)).publish_at == utc(2027, 3, 14, 7)
    monkeypatch.setattr(sch.settings, "LEARN_SLOT_PUBLISH_HOUR_ET", 1)
    # 2026-11-01 01:00 happens twice: the FIRST occurrence (EDT, 05:00 UTC).
    assert sch.slot_times_for(date(2026, 11, 1)).publish_at == utc(2026, 11, 1, 5)


def test_instants_are_utc_so_the_fall_back_hour_compares_by_instant(monkeypatch):
    """Why the module returns UTC: two datetimes sharing the ET tzinfo compare by WALL time and
    ignore `fold`. Here the slot's build starts at 01:00 EST (the SECOND 01:00 of 2026-11-01, 06:00
    UTC); 01:30 EDT (the first pass, 05:30 UTC) is half an hour BEFORE it. An ET-valued `build_at`
    would call that instant inside the window (01:30 ≥ 01:00 on the wall)."""
    monkeypatch.setattr(sch.settings, "LEARN_SLOT_PUBLISH_HOUR_ET", 5)
    monkeypatch.setattr(sch.settings, "LEARN_SLOT_BUILD_LEAD_HOURS", 28)
    s = sch.slot_times_for(date(2026, 11, 2))
    assert s.build_at == utc(2026, 11, 1, 6) and s.build_at.utcoffset() == timedelta(0)
    first_0130 = datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=ET)    # EDT, 05:30 UTC
    second_0130 = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=ET)   # EST, 06:30 UTC
    assert not s.window_contains(first_0130)
    assert s.window_contains(second_0130)
    assert sch.due_slots(first_0130, (0,)) == []
    assert sch.due_slots(second_0130, (0,)) == [s]
    # The trap itself, pinned so the reason above stays true of the Python in use.
    assert s.build_at.astimezone(ET) <= first_0130                  # wall-time comparison: wrong


def test_due_slots_monday_boundaries():
    s = sch.slot_times_for(date(2026, 10, 26))
    assert sch.due_slots(s.build_at - US, (0,)) == []
    assert sch.due_slots(s.build_at, (0,)) == [s]
    assert sch.due_slots(et(2026, 10, 26, 5), (0,)) == [s]
    assert sch.due_slots(s.hold_until, (0,)) == [s]
    assert sch.due_slots(s.hold_until + US, (0,)) == []


def test_due_slots_monday_and_saturday_overlap():
    sat = sch.slot_times_for(date(2026, 10, 17))
    mon = sch.slot_times_for(date(2026, 10, 19))
    assert sat.build_at == et(2026, 10, 15, 12) and sat.hold_until == et(2026, 10, 19, 5)
    assert sch.due_slots(et(2026, 10, 16, 9), (0, 5)) == [sat]
    # Sat 12:00: Saturday's piece is in its hold and Monday's build starts — both, oldest first.
    assert sch.due_slots(et(2026, 10, 17, 12), (0, 5)) == [sat, mon]
    # Mon 05:00: Saturday's hold ends (inclusive) as Monday publishes.
    assert sch.due_slots(et(2026, 10, 19, 5), (0, 5)) == [sat, mon]
    assert sch.due_slots(et(2026, 10, 19, 5) + US, (0, 5)) == [mon]
    assert sch.due_slots(et(2026, 10, 17, 12), (0,)) == [mon]


def test_due_slots_reads_the_setting_when_weekdays_is_omitted(monkeypatch):
    now = et(2026, 10, 16, 9)                                      # only a Saturday slot is due
    assert sch.due_slots(now) == []
    monkeypatch.setattr(sch.settings, "LEARN_SLOT_WEEKDAYS", "sat")
    assert [s.publish_date for s in sch.due_slots(now)] == [date(2026, 10, 17)]


def test_due_slots_drops_junk_weekdays_with_an_error(caplog):
    now = et(2026, 10, 24, 12)
    with caplog.at_level(logging.ERROR, logger=sch.logger.name):
        got = sch.due_slots(now, [0, True, 7, -1, "mon", None, 0.0, 0])
    assert [s.publish_date for s in got] == [date(2026, 10, 26)]
    assert any("ignored weekday" in r.getMessage() for r in caplog.records)


def test_due_slots_empty_weekdays_means_nothing_is_due():
    assert sch.due_slots(et(2026, 10, 24, 12), []) == []
    assert sch.due_slots(et(2026, 10, 24, 12), ()) == []


def test_due_slots_parses_a_setting_string_instead_of_iterating_characters():
    assert [s.publish_date for s in sch.due_slots(et(2026, 10, 24, 12), "mon")] == [date(2026, 10, 26)]


@pytest.mark.parametrize("lead, hold", [(41, 48), (96, 72), (6, 1)])
def test_due_slots_matches_a_brute_force_scan(monkeypatch, lead, hold):
    """Hourly over two months with every weekday on, at the default and both extreme settings: the
    ±8/−4-day search finds exactly the slots a ±30-day scan finds."""
    monkeypatch.setattr(sch.settings, "LEARN_SLOT_BUILD_LEAD_HOURS", lead)
    monkeypatch.setattr(sch.settings, "LEARN_REVIEW_HOLD_HOURS", hold)
    all_days = tuple(range(7))
    slots = [sch.slot_times_for(d) for d in _days(date(2026, 10, 1), date(2026, 12, 31))]
    now = utc(2026, 10, 15)
    end = utc(2026, 12, 10)
    while now < end:
        expected = [s for s in slots if s.build_at <= now <= s.hold_until]
        assert sch.due_slots(now, all_days) == expected, now
        now += H


def test_due_slots_refuses_a_naive_now():
    with pytest.raises(ValueError):
        sch.due_slots(datetime(2026, 10, 24, 12), (0,))


@pytest.mark.parametrize("bad", [datetime(2026, 10, 26, 5, tzinfo=ET), "2026-10-26", None])
def test_slot_times_for_refuses_non_dates(bad):
    with pytest.raises(TypeError):
        sch.slot_times_for(bad)


# ── Period keys back to times ─────────────────────────────────────────────────────────────────


def test_period_keys_round_trip():
    for friday in _fridays(date(2026, 1, 1), date(2028, 1, 31)):
        t = sch.deep_dive_times_for(friday)
        assert sch.times_for_period_key(t.period_key) == t
    s = sch.slot_times_for(date(2026, 10, 26))
    assert sch.times_for_period_key("2026-10-26:slot") == s


@pytest.mark.parametrize("bad", [
    "2027-W53:deep_dive",      # 2027 has 52 ISO weeks
    "2026-W00:deep_dive", "2026-W54:deep_dive", "0000-W01:deep_dive",
    "2026-02-30:slot", "2026-13-01:slot",
    "2026-W43:slot", "2026-10-26:deep_dive", "2026-W43", "", "garbage",
    None, 202643, b"2026-W43:deep_dive",
])
def test_times_for_period_key_refuses_junk(bad):
    with pytest.raises(ValueError):
        sch.times_for_period_key(bad)


# ── catch_up_cutoff ───────────────────────────────────────────────────────────────────────────


def _needed_seconds(words: float, factor: float) -> float:
    render = words / contracts.NARRATION_WPM * 60 * factor
    return 900 + 2 * (1200 + 600 + render) + 7200


HOLD = utc(2026, 10, 26, 3)        # 2026-W43's hold end (Sun 23:00 EDT)


@pytest.mark.parametrize("engine", list(contracts.ENGINES))
def test_catch_up_cutoff_formula_per_engine(engine):
    factor = contracts.RENDER_FACTOR[engine]
    for words in (1, 900, 1400, 1900):
        cut = sch.catch_up_cutoff(HOLD, engine, words)
        assert cut.tzinfo is not None
        assert (HOLD - cut).total_seconds() == pytest.approx(_needed_seconds(words, factor), abs=1e-3)


def test_catch_up_cutoff_magnitudes_for_a_full_deep_dive():
    # The plan's "about 9 h for Chatterbox": 1,900 words → 12.3 audio-min × 14.3 ≈ 2.9 h per round.
    chatterbox = (HOLD - sch.catch_up_cutoff(HOLD, contracts.ENGINE_CHATTERBOX, 1900)) / H
    kokoro = (HOLD - sch.catch_up_cutoff(HOLD, contracts.ENGINE_KOKORO, 1900)) / H
    assert 9.0 < chatterbox < 9.2
    assert 4.4 < kokoro < 4.6
    assert kokoro < chatterbox


def test_catch_up_cutoff_is_elapsed_time_across_dst():
    hold = sch.deep_dive_times_for(date(2026, 10, 30)).hold_until   # Sun 22:00 EST after fall-back
    cut = sch.catch_up_cutoff(hold, contracts.ENGINE_CHATTERBOX, 1900)
    assert (hold.astimezone(UTC) - cut.astimezone(UTC)).total_seconds() == pytest.approx(
        _needed_seconds(1900, contracts.RENDER_FACTOR[contracts.ENGINE_CHATTERBOX]), abs=1e-3)


@pytest.mark.parametrize("words", [0, -5, -1e9, float("nan"), float("inf"), float("-inf"), None, True,
                                   False, "1500", [1500]])
def test_catch_up_cutoff_junk_words_use_the_deep_dive_maximum(caplog, words):
    expected = sch.catch_up_cutoff(HOLD, contracts.ENGINE_KOKORO, contracts.DEEP_DIVE_WORDS[1])
    with caplog.at_level(logging.WARNING, logger=sch.logger.name):
        assert sch.catch_up_cutoff(HOLD, contracts.ENGINE_KOKORO, words) == expected
    assert any("words=" in r.getMessage() for r in caplog.records)


def test_catch_up_cutoff_huge_words_clamp_instead_of_overflowing(caplog):
    with caplog.at_level(logging.WARNING, logger=sch.logger.name):
        cut = sch.catch_up_cutoff(HOLD, contracts.ENGINE_CHATTERBOX, 1e300)
    assert cut == sch.catch_up_cutoff(HOLD, contracts.ENGINE_CHATTERBOX, sch._MAX_CUTOFF_WORDS)
    assert HOLD - cut > timedelta(days=7)                           # no catch-up for such a piece
    assert any("clamp" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("engine", ["whisper", "Chatterbox", "", None, 3, "kokoro "])
def test_catch_up_cutoff_unknown_engine_uses_the_largest_factor(caplog, engine):
    worst = max(contracts.RENDER_FACTOR.values())
    with caplog.at_level(logging.WARNING, logger=sch.logger.name):
        cut = sch.catch_up_cutoff(HOLD, engine, 1900)
    assert (HOLD - cut).total_seconds() == pytest.approx(_needed_seconds(1900, worst), abs=1e-3)
    # Never LATER than any known engine's cut-off: the fallback is the fail-safe direction.
    assert all(cut <= sch.catch_up_cutoff(HOLD, e, 1900) for e in contracts.ENGINES)
    assert any("engine=" in r.getMessage() for r in caplog.records)


def test_catch_up_cutoff_logs_each_junk_value_once(caplog):
    with caplog.at_level(logging.WARNING, logger=sch.logger.name):
        for _ in range(5):
            sch.catch_up_cutoff(HOLD, "whisper", float("nan"))
    assert sum("engine=" in r.getMessage() for r in caplog.records) == 1
    assert sum("words=" in r.getMessage() for r in caplog.records) == 1


def test_catch_up_cutoff_keyword_budgets():
    base = sch.catch_up_cutoff(HOLD, contracts.ENGINE_KOKORO, 1000)
    longer_review = sch.catch_up_cutoff(HOLD, contracts.ENGINE_KOKORO, 1000, min_review_seconds=10800)
    assert base - longer_review == timedelta(hours=1)
    zero = sch.catch_up_cutoff(HOLD, contracts.ENGINE_KOKORO, 1000, build_seconds=0, cron_seconds=0,
                               lease_seconds=0, min_review_seconds=0)
    render = 1000 / contracts.NARRATION_WPM * 60 * contracts.RENDER_FACTOR[contracts.ENGINE_KOKORO]
    assert (HOLD - zero).total_seconds() == pytest.approx(2 * render, abs=1e-3)


@pytest.mark.parametrize("kwargs", [{"build_seconds": -1}, {"cron_seconds": float("nan")},
                                    {"lease_seconds": float("inf")}, {"min_review_seconds": True},
                                    {"build_seconds": "900"}, {"cron_seconds": None}])
def test_catch_up_cutoff_refuses_bad_budgets(kwargs):
    with pytest.raises(ValueError):
        sch.catch_up_cutoff(HOLD, contracts.ENGINE_KOKORO, 1000, **kwargs)


@pytest.mark.parametrize("bad, exc", [(datetime(2026, 10, 26, 3), ValueError), (date(2026, 10, 26), TypeError),
                                      (None, TypeError)])
def test_catch_up_cutoff_refuses_naive_hold(bad, exc):
    with pytest.raises(exc):
        sch.catch_up_cutoff(bad, contracts.ENGINE_KOKORO, 1000)


# ── sort_order ────────────────────────────────────────────────────────────────────────────────


def test_deep_dive_sort_order_is_monotonic_across_three_year_boundaries():
    fridays = list(_fridays(date(2026, 1, 1), date(2029, 12, 31)))
    orders = [sch.deep_dive_times_for(f).sort_order for f in fridays]
    keys = [sch.deep_dive_times_for(f).period_key for f in fridays]
    assert all(o < 0 for o in orders)
    assert all(a > b for a, b in zip(orders, orders[1:]))          # strictly newer → lower
    assert len(set(keys)) == len(keys) == len(fridays)
    assert sch.deep_dive_sort_order(2026, 53) == -202653
    assert sch.deep_dive_sort_order(2027, 1) == -202701
    assert sch.deep_dive_sort_order(2026, 53) > sch.deep_dive_sort_order(2027, 1)
    # 2032 also starts on a Thursday (leap year): its W53 → 2033-W01 stays monotonic too.
    assert sch.deep_dive_sort_order(2032, 53) > sch.deep_dive_sort_order(2033, 1)


@pytest.mark.parametrize("year, week, exc", [
    (2027, 53, ValueError), (2026, 0, ValueError), (2026, 54, ValueError), (2026, -1, ValueError),
    (0, 1, ValueError), (10000, 1, ValueError),
    (True, 5, TypeError), (2026, True, TypeError), (2026, 5.0, TypeError), ("2026", 5, TypeError),
    (None, 5, TypeError),
])
def test_deep_dive_sort_order_refuses_junk(year, week, exc):
    with pytest.raises(exc):
        sch.deep_dive_sort_order(year, week)


def test_slot_sort_order_values_and_monotonicity():
    assert sch.slot_sort_order(date(2026, 1, 1)) == 100000
    assert sch.slot_sort_order(date(2026, 12, 31)) == 100364
    assert sch.slot_sort_order(date(2027, 1, 1)) == 100365
    days = list(_days(date(2026, 1, 1), date(2029, 1, 1)))
    orders = [sch.slot_sort_order(d) for d in days]
    assert all(b == a + 1 for a, b in zip(orders, orders[1:]))
    # Disjoint from the Deep Dives (negative) and far above the bundle's 0..N-1.
    assert min(orders) == sch.SLOT_SORT_BASE > 0


@pytest.mark.parametrize("bad, exc", [
    (date(2025, 12, 31), ValueError), (date(1970, 1, 1), ValueError),
    (datetime(2026, 10, 26, 5, tzinfo=ET), TypeError), ("2026-10-26", TypeError), (None, TypeError),
])
def test_slot_sort_order_refuses_junk(bad, exc):
    with pytest.raises(exc):
        sch.slot_sort_order(bad)


# ── Purity ────────────────────────────────────────────────────────────────────────────────────


def test_schedule_imports_nothing_with_io():
    """Pure module: no FMP, Supabase, Gemini, httpx or clock — its callers pass `now`."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sch))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    banned = ("fmp", "supabase", "gemini", "httpx", "app.database", "integrations")
    assert not [m for m in imported if any(b in m for b in banned)], imported
    calls = {getattr(n.func, "attr", None) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not calls & {"now", "utcnow", "today", "time_ns", "monotonic"}, calls & {"now", "utcnow", "today"}
