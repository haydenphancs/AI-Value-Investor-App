"""Adversarial tests: Trillion-Dollar Club rules + the 13F CUSIP diff.

Written by an independent reviewer against
``app/services/trillion_club/rules.py`` and ``_whale_common.diff_13f_positions``. It
complements test_trillion_club_rules.py and test_thirteen_f_position_diff.py instead of
repeating them, and it checks results against implementations written here, not against
the module's own arithmetic:

* membership is compared with a run-length reference replay over randomized histories
  (shuffled, duplicated, junk rows, future rows);
* the SEC 13F calendar is compared with a separate 5 U.S.C. 6103 holiday calendar for
  2021-2045, and due dates for 2025-2032 are also listed by hand;
* CUSIP and ISIN check digits are compared with a separate modulus-10 / Luhn
  implementation, plus real identifiers;
* the diff is compared with a reference classifier over randomized books, and every
  invariant of the ``changes`` JSON contract is checked on each run.

Tests named ``test_BUG_*`` expose a real defect and are left failing on purpose. The
module docstring of each one says where the defect is, why it happens, and the fix.
Once a defect is fixed its test is renamed ``test_regression_*`` and kept (its docstring
opens with "REGRESSION (fixed <date>). Was: ").
Hermetic: no network and no Supabase; pure functions only.
"""
from __future__ import annotations

import calendar
import copy
import functools
import json
import logging
import math
import random
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services import _whale_common as W
from app.services.trillion_club import rules as R
from app.utils import market_hours
from app.utils.market_hours import ET, is_trading_day


@pytest.fixture(autouse=True)
def _no_learned_market_closures(monkeypatch):
    """``market_hours._OBSERVED_CLOSURES`` is a process-wide set; another test may have
    registered a 2026 weekday in it, which would move every close cutoff here."""
    monkeypatch.setattr(market_hours, "_OBSERVED_CLOSURES", set())

T = R.THRESHOLD_USD
A = 1.05 * T          # comfortably above the line
B = 0.95 * T          # comfortably below
LAST = date(2026, 9, 23)                                  # Wednesday, an ordinary session
AFTER_CLOSE = datetime(2026, 9, 23, 17, 0, tzinfo=ET)


def _sessions_ending(last: date, n: int):
    """``n`` NYSE session dates ending on ``last`` (ascending; skips weekends/holidays)."""
    out, d = [], last
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _series(caps, last: date = LAST):
    return list(zip(_sessions_ending(last, len(caps)), caps))


def _ev(closes, *, mode="auto", prior=None, now=AFTER_CLOSE, today="derive"):
    today_et = now.astimezone(ET).date() if today == "derive" else today
    return R.evaluate_membership(
        closes, mode=mode, prior=prior, today_et=today_et, now_et=now, log_ctx="adv",
    )


# ── independent reference: membership as a run-length walk ─────────────────────────────


def _ref_membership(rows):
    """Run-length formulation of the club rule over ascending, de-duplicated rows.

    An above-run of >= 10 joins on its 10th close (when outside); a below-run of >= 20
    leaves on its 20th close (when inside). Structurally different from ``_replay``,
    which keeps per-row counters.
    """
    runs = []
    for d, cap in rows:
        above = cap >= T
        if runs and runs[-1][0] == above:
            runs[-1][1].append(d)
        else:
            runs.append((above, [d]))
    member, since = False, None
    for above, ds in runs:
        if above and not member and len(ds) >= R.JOIN_CLOSES:
            member, since = True, ds[R.JOIN_CLOSES - 1]
        elif not above and member and len(ds) >= R.LEAVE_CLOSES:
            member, since = False, None
    last_above, last_ds = runs[-1]
    return member, since, (len(last_ds) if last_above else 0), (0 if last_above else len(last_ds))


def _random_caps(rng: random.Random, n: int):
    caps, above = [], rng.random() < 0.5
    while len(caps) < n:
        run = rng.choice([1, 2, 3, 5, 8, 9, 10, 11, 12, 15, 19, 20, 21, 25, 40])
        for _ in range(run):
            caps.append(rng.choice([float(T), T * 1.0001, 1.2 * T, 3.0 * T]) if above
                        else rng.choice([T - 1.0, 0.999 * T, 0.5 * T]))
        above = not above
    return caps[:n]


# ══ membership: replay vs the independent reference ═══════════════════════════════════


@pytest.mark.parametrize("seed", range(250))
def test_replay_matches_the_run_length_reference_under_messy_input(seed):
    """Shuffled, duplicated (in another date encoding), junk-laden, with future rows."""
    rng = random.Random(seed)
    n = rng.randint(R.MIN_ROWS, 320)
    clean = _series(_random_caps(rng, n))
    messy = list(clean)
    for d, c in rng.sample(clean, k=min(len(clean), rng.randint(0, 15))):
        messy.append((d.isoformat(), c))                          # identical duplicate
    for _ in range(rng.randint(0, 6)):                             # junk never counts
        d = rng.choice(clean)[0]
        messy.append((d, rng.choice([float("nan"), float("inf"), -1.0, 0, None, True, "x"])))
        messy.append((rng.choice(["2026-13-01", "", None, 20260923]), A))
    for k in range(rng.randint(0, 3)):                             # after the close: dropped
        messy.append((LAST + timedelta(days=k + 1), rng.choice([A, B])))
    rng.shuffle(messy)

    window = clean[-R.MAX_REPLAY_ROWS:]
    member, since, up, down = _ref_membership(window)
    expected = R.MembershipState(member, since, up, down, window[-1][1], window[-1][0])
    got = _ev(messy)
    assert got == expected

    # invariants of the written state
    assert (got.closes_at_or_above == 0) != (got.closes_below == 0)
    assert got.is_member or got.member_since is None
    if got.is_member:
        assert got.member_since in {d for d, _ in window}
        assert got.member_since <= got.last_cap_date
    assert math.isfinite(got.last_cap)


@pytest.mark.parametrize("seed", range(60))
def test_a_prior_state_only_ever_changes_member_since(seed):
    """The stored state may keep an older ``member_since``; it may never change who is a
    member or the counters, and it may never invent a third date."""
    rng = random.Random(10_000 + seed)
    closes = _series(_random_caps(rng, rng.randint(R.MIN_ROWS, 300)))
    base = _ev(closes)
    for prior in (
        R.MembershipState(True, date(2001, 2, 5), 999, 0, 2e12, date(2001, 2, 5)),
        R.MembershipState(True, None, 0, 0, None, None),
        R.MembershipState(False, None, 0, 40, 5e11, LAST),
    ):
        got = _ev(closes, prior=prior)
        assert (got.is_member, got.closes_at_or_above, got.closes_below, got.last_cap,
                got.last_cap_date) == (base.is_member, base.closes_at_or_above,
                                       base.closes_below, base.last_cap, base.last_cap_date)
        assert got.member_since in {base.member_since, prior.member_since}


# ══ membership: input encodings ════════════════════════════════════════════════════════


def test_date_and_cap_encodings_are_interchangeable():
    base = _series([B] * 25 + [A] * 12 + [B] * 3 + [A] * 11)
    ref = _ev(base)
    encodings = [
        [(d.isoformat(), c) for d, c in base],
        [(d.isoformat() + "T00:00:00", c) for d, c in base],
        [(d.isoformat() + " 16:00:00", c) for d, c in base],
        [(datetime(d.year, d.month, d.day), c) for d, c in base],
        [(d, int(c)) for d, c in base],
        [(d, repr(c)) for d, c in base],
        [(d, f"  {c:.1f} ") for d, c in base],
        [(d, Decimal(repr(c))) for d, c in base],
        [[d, c] for d, c in base],                                  # list rows, not tuples
    ]
    for enc in encodings:
        assert _ev(enc) == ref, enc[0]
    assert type(_ev(encodings[0]).last_cap) is float


@pytest.mark.parametrize("bad_cap", [
    "", "  ", "nan", "NaN", "inf", "-inf", "-1.05e12", "0", "0.0", "-0",
    "1,050,000,000,000", "1.05T", "$1.05e12", "abc", [1e12], {"marketCap": 1e12},
])
def test_non_numeric_or_non_positive_string_caps_are_dropped(bad_cap, caplog):
    good = _series([A] * 30)
    junk = [(date(2026, 1, 2), bad_cap)]
    with caplog.at_level(logging.WARNING):
        assert _ev(good + junk) == _ev(good)
    assert "dropped 1 malformed" in caplog.text


@pytest.mark.parametrize("bad_date", [
    " 2026-09-23", "2026/09/23", "09/23/2026", "2026-9-23", "20260923", "2026-02-30",
    "2026-09", 20260923, 1758585600, 1.5, b"2026-09-23", True,
])
def test_malformed_dates_are_dropped_and_do_not_count(bad_date, caplog):
    good = _series([B] * 30 + [A] * 9)
    with caplog.at_level(logging.WARNING):
        st = _ev(good + [(bad_date, A)])
    assert st.is_member is False and st.closes_at_or_above == 9
    assert "dropped 1 malformed" in caplog.text


def test_rows_that_are_not_pairs_are_dropped():
    good = _series([A] * 30)
    junk = ["ab", "2026-09-23", (LAST,), (LAST, A, A), None, 7, {"date": "2026-09-22"}]
    assert _ev(good + junk) == _ev(good)


def test_a_generator_is_read_once_like_a_list():
    good = _series([B] * 25 + [A] * 10)
    assert _ev(iter(good)) == _ev(good)


# ══ membership: duplicates ═════════════════════════════════════════════════════════════


def test_duplicates_in_different_encodings_collapse_or_conflict_by_value(caplog):
    good = _series([A] * 30)
    d_last = good[-1][0]
    same = [(d_last.isoformat(), repr(A)), (datetime(d_last.year, d_last.month, d_last.day), A * (1 + 1e-12))]
    got, ref = _ev(good + same), _ev(good)
    assert (got.is_member, got.member_since, got.closes_at_or_above, got.closes_below,
            got.last_cap_date) == (ref.is_member, ref.member_since, ref.closes_at_or_above,
                                   ref.closes_below, ref.last_cap_date)
    assert got.last_cap == pytest.approx(ref.last_cap, rel=1e-9)
    with caplog.at_level(logging.WARNING):
        assert _ev(good + [(d_last.isoformat(), A * (1 + 1e-6))]) is None
    assert "two different caps" in caplog.text


def test_a_conflicting_duplicate_dated_after_the_close_is_ignored_not_fatal():
    """Two intraday figures for today (before the close) are dropped as not-yet-closes;
    they must not fail the whole evaluation closed."""
    now = datetime(2026, 9, 24, 11, 0, tzinfo=ET)
    good = _series([A] * 30)
    st = _ev(good + [(date(2026, 9, 24), A), (date(2026, 9, 24), B)], now=now)
    assert st == _ev(good, now=now)


def test_a_nan_duplicate_of_a_good_row_is_junk_not_a_conflict():
    good = _series([A] * 30)
    assert _ev(good + [(good[-1][0], float("nan"))]) == _ev(good)


def test_a_conflict_in_force_mode_keeps_the_prior_facts():
    prior = R.MembershipState(False, None, 3, 7, 9.9e11, date(2026, 9, 1))
    good = _series([A] * 30)
    conflict = good + [(good[-1][0], B)]
    st = _ev(conflict, mode=R.MODE_FORCE_IN, prior=prior)
    assert st == R.MembershipState(True, LAST, 3, 7, 9.9e11, date(2026, 9, 1))
    out = _ev(conflict, mode=R.MODE_FORCE_OUT, prior=prior)
    assert out == R.MembershipState(False, None, 3, 7, 9.9e11, date(2026, 9, 1))


# ══ membership: gaps and junk inside a streak (current behaviour, pinned) ═════════════


def test_pinned_a_junk_close_inside_a_streak_is_bridged_not_a_reset():
    """PINNED, and reported to the lead as a design question: a NaN / missing close in
    the middle of a streak is dropped and the streak continues across it. The unknown
    close could have been below the line. (Since 2026-09-24 two DISAGREEING rows for an
    older date that straddle $1T break the streak instead — see
    test_trillion_club_rules.py — so the two kinds of unknown close are still treated
    differently: a missing close is bridged, a contradictory one is a breaker.)"""
    dates = _sessions_ending(LAST, 40)
    caps = [B] * 29 + [A] * 5 + [float("nan")] + [A] * 5
    st = _ev(list(zip(dates, caps)))
    assert st.is_member is True and st.closes_at_or_above == 10


def test_pinned_an_interior_hole_of_many_sessions_is_bridged():
    """PINNED: 30 missing sessions between two above-runs of 5 still make 10 in a row."""
    dates = _sessions_ending(LAST, 70)
    rows = [(d, B) for d in dates[:25]] + [(d, A) for d in dates[25:30]] + \
           [(d, A) for d in dates[60:65]]
    rows += [(d, A) for d in dates[65:]]
    st = _ev(rows)
    assert st.is_member is True
    assert st.member_since == dates[64], "the 10th PRESENT close, across the hole"


# ══ membership: staleness is measured from the last completed session ══════════════════


@pytest.mark.parametrize("now, newest, fresh", [
    # Monday pre-open: the last session is Friday 9/25; 7 calendar days back is OK.
    (datetime(2026, 9, 28, 9, 0, tzinfo=ET), date(2026, 9, 18), True),
    (datetime(2026, 9, 28, 9, 0, tzinfo=ET), date(2026, 9, 17), False),
    # Tuesday after Labor Day, before the close: last session is Friday 9/4.
    (datetime(2026, 9, 8, 10, 0, tzinfo=ET), date(2026, 8, 28), True),
    (datetime(2026, 9, 8, 10, 0, tzinfo=ET), date(2026, 8, 27), False),
    # Friday after Thanksgiving before its 13:00 close: last session is Wed 11/25.
    (datetime(2026, 11, 27, 12, 0, tzinfo=ET), date(2026, 11, 18), True),
    (datetime(2026, 11, 27, 12, 0, tzinfo=ET), date(2026, 11, 17), False),
])
def test_staleness_counts_from_the_last_session_not_from_today(now, newest, fresh):
    st = _ev(_series([A] * 30, last=newest), now=now)
    assert (st is not None) is fresh


# ══ membership: the close boundary ═════════════════════════════════════════════════════


def _join_on(day: date):
    """30 below + 10 above, the 10th above close dated ``day``."""
    return _series([B] * 30 + [A] * 10, last=day)


@pytest.mark.parametrize("now, counted", [
    (datetime(2026, 9, 24, 15, 59, 59, 999999, tzinfo=ET), False),
    (datetime(2026, 9, 24, 16, 0, 0, tzinfo=ET), True),
    (datetime(2026, 9, 24, 19, 59, 59, tzinfo=timezone.utc), False),   # EDT: 15:59:59 ET
    (datetime(2026, 9, 24, 20, 0, 0, tzinfo=timezone.utc), True),      # EDT: 16:00 ET
    (datetime(2026, 9, 25, 3, 59, tzinfo=timezone.utc), True),         # 23:59 ET, UTC date+1
    (datetime(2026, 9, 24, 0, 0, tzinfo=ET), False),                   # midnight, same date
    (datetime(2026, 9, 24, 9, 30, tzinfo=ET), False),                  # the open
])
def test_todays_row_counts_only_from_1600_et_on_an_ordinary_session(now, counted):
    st = _ev(_join_on(date(2026, 9, 24)), now=now)
    assert st.is_member is counted
    assert st.last_cap_date == (date(2026, 9, 24) if counted else date(2026, 9, 23))
    assert st.closes_at_or_above == (10 if counted else 9)


@pytest.mark.parametrize("now, counted", [
    (datetime(2026, 12, 10, 20, 59, 59, tzinfo=timezone.utc), False),  # EST: 15:59:59 ET
    (datetime(2026, 12, 10, 21, 0, 0, tzinfo=timezone.utc), True),     # EST: 16:00 ET
    (datetime(2026, 12, 10, 20, 0, 0, tzinfo=timezone.utc), False),    # 15:00 ET — NOT closed
])
def test_the_close_follows_eastern_time_across_the_dst_change(now, counted):
    assert _ev(_join_on(date(2026, 12, 10)), now=now).is_member is counted


@pytest.mark.parametrize("half_day", [
    date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
    date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26),
])
def test_a_half_day_closes_at_1300_et(half_day):
    series = _join_on(half_day)
    at = lambda h, m: datetime(half_day.year, half_day.month, half_day.day, h, m, tzinfo=ET)
    assert _ev(series, now=at(12, 59)).is_member is False
    assert _ev(series, now=at(13, 0)).is_member is True
    assert _ev(series, now=at(15, 0)).last_cap_date == half_day


@pytest.mark.parametrize("holiday", [
    date(2026, 4, 3),      # Good Friday (NYSE only; a federal business day)
    date(2026, 7, 3),      # Independence Day observed
    date(2026, 9, 7),      # Labor Day
    date(2026, 11, 26),    # Thanksgiving
    date(2027, 3, 26),     # Good Friday 2027
    date(2027, 12, 24),    # Christmas observed 2027
])
def test_a_row_stamped_on_an_nyse_holiday_is_never_a_close(holiday):
    """FMP stamps an intraday figure with today's date even when the tape is shut."""
    prev = _sessions_ending(holiday - timedelta(days=1), 1)[0]
    series = _series([B] * 30 + [A] * 9, last=prev) + [(holiday, A)]
    st = _ev(series, now=datetime(holiday.year, holiday.month, holiday.day, 17, 0, tzinfo=ET))
    assert st.is_member is False and st.last_cap_date == prev and st.closes_at_or_above == 9


def test_regression_the_close_cutoff_knows_2028_nyse_holidays():
    """REGRESSION (fixed 2026-09-24). Was: the NYSE holiday and half-day tables
    (app/utils/market_hours.py, used by rules.evaluate_membership) stopped at 2027, so
    ``last_completed_close`` treated Good Friday 2028 (2028-04-14) as a session that
    closed at 16:00, and a row FMP stamped with that date completed a 10-close join on a
    day the exchange never opened. Fixed by adding NYSE's published 2028 calendar, plus a
    guard in test_trillion_club_rules.py that the tables cover 12 months ahead."""
    hol_2028 = {date(2028, 1, 17), date(2028, 2, 21), date(2028, 4, 14)}
    days, d = [], date(2028, 4, 13)
    while len(days) < 39:
        if d.weekday() < 5 and d not in hol_2028:
            days.append(d)
        d -= timedelta(days=1)
    rows = list(zip(days[::-1], [B] * 30 + [A] * 9)) + [(date(2028, 4, 14), A)]
    st = _ev(rows, now=datetime(2028, 4, 14, 17, 0, tzinfo=ET))
    assert st.is_member is False and st.last_cap_date == date(2028, 4, 13)


@pytest.mark.parametrize("now", [
    datetime(2026, 9, 26, 0, 1, tzinfo=ET), datetime(2026, 9, 26, 17, 0, tzinfo=ET),
    datetime(2026, 9, 27, 23, 59, tzinfo=ET), datetime(2026, 9, 28, 9, 29, tzinfo=ET),
])
def test_across_a_weekend_the_cutoff_is_fridays_close(now):
    series = _join_on(date(2026, 9, 25))
    extra = [(date(2026, 9, 26), B), (date(2026, 9, 27), B), (date(2026, 9, 28), B)]
    st = _ev(series + extra, now=now)
    assert st.is_member is True and st.last_cap_date == date(2026, 9, 25)


def test_today_et_can_only_pull_the_cutoff_earlier():
    series = _join_on(date(2026, 9, 24))
    now = datetime(2026, 9, 24, 17, 0, tzinfo=ET)
    later = _ev(series, now=now, today=date(2026, 9, 30))
    assert later.is_member is True and later.last_cap_date == date(2026, 9, 24)
    earlier = _ev(series, now=now, today=date(2026, 9, 23))
    assert earlier.is_member is False and earlier.last_cap_date == date(2026, 9, 23)
    assert _ev(series, now=now, today="2026-09-23") == earlier
    assert _ev(series, now=now, today=None) == later
    assert _ev(series, now=now, today="garbage") == later


def test_pinned_a_naive_now_is_read_as_utc_not_eastern():
    """PINNED trap (reported): the parameter is named ``now_et`` but a NAIVE value is read
    as UTC by ``last_completed_close``. Naive 17:00 is 13:00 ET, before the close. The job
    passes an aware datetime, so production is unaffected."""
    series = _join_on(date(2026, 9, 24))
    naive = datetime(2026, 9, 24, 17, 0)
    st = R.evaluate_membership(series, mode="auto", prior=None,
                               today_et=date(2026, 9, 24), now_et=naive)
    assert st.is_member is False and st.last_cap_date == date(2026, 9, 23)


# ══ membership: member_since and the stored state ══════════════════════════════════════


def test_a_long_member_keeps_its_stored_date_as_the_window_slides():
    """Five consecutive daily runs over an all-above history: the stored date survives."""
    history = _series([A] * 265)
    state = R.MembershipState(True, date(2021, 6, 1), 260, 0, A, history[-6][0])
    for k in range(5, -1, -1):
        rows = history[:len(history) - k]
        day = rows[-1][0]
        state = _ev(rows, prior=state, now=datetime(day.year, day.month, day.day, 17, 0, tzinfo=ET))
        assert state.is_member is True and state.member_since == date(2021, 6, 1), day


def test_censored_window_with_a_newer_stored_date_takes_the_older_window_date():
    """The window proves membership at least since its 10th row, so a stored date
    AFTER that is wrong and must not be kept."""
    series = _series([A] * 260)
    prior = R.MembershipState(True, date(2026, 6, 1), 200, 0, A, LAST)
    st = _ev(series, prior=prior)
    assert st.member_since == series[R.JOIN_CLOSES - 1][0] < date(2026, 6, 1)


def test_censored_window_without_a_stored_member_is_capped_by_the_window():
    series = _series([A] * 260)
    for prior in (None, R.NOT_A_MEMBER, R.MembershipState(True, None, 0, 0, None, None)):
        assert _ev(series, prior=prior).member_since == series[R.JOIN_CLOSES - 1][0]


def test_a_real_exit_inside_the_window_discards_the_stored_date():
    series = _series([A] * 5 + [B] * 20 + [A] * 30)
    prior = R.MembershipState(True, date(2019, 1, 2), 30, 0, A, LAST)
    st = _ev(series, prior=prior)
    assert st.is_member is True and st.member_since == series[25 + R.JOIN_CLOSES - 1][0]


def test_regression_member_since_holds_when_the_window_slides_past_an_old_dip():
    """REGRESSION (fixed 2026-09-24). Was: rules._replay / evaluate_membership kept the
    stored date only when the replay's join was "censored" (began on the window's first
    row). Fixed: the stored, older date is kept whenever the window holds no run of
    LEAVE_CLOSES closes below the line.

    A long-standing member (stored since 2024-01-02) had a 15-close dip below $1T about a
    year ago. Nothing changes in reality from one day to the next, yet:

    * day 1: the window opens with 10 closes above, then the dip. The replay joins on
      row 10 (streak_start == 0 -> censored), stays a member through the dip, and the
      stored 2024-01-02 is kept. Correct.
    * day 2: the window has slid by one close, so it opens with only 9 above. The replay
      starts from "not a member", so it cannot join before the dip. It joins 10 closes
      after the dip instead, with streak_start != 0 -> NOT censored, and member_since is
      overwritten with a date in late 2025. Every later day keeps the wrong date,
      because once censored the stored (now wrong) date wins.

    The window cannot see a >= 20-close exit anywhere, so the membership never lapsed
    and the stored date should survive. Fix: when ``prior.is_member``, replay starting
    from "member since prior.member_since" (a >= 20-close below run inside the window
    still ends it), or treat a membership as censored when no LEAVE_CLOSES run below
    precedes the join inside the window.
    """
    history = _series([A] * 10 + [B] * 15 + [A] * 236)      # 261 closes, newest = LAST
    day1_rows, day1 = history[:-1], history[-2][0]
    prior = R.MembershipState(True, date(2024, 1, 2), 234, 0, A, day1 - timedelta(days=1))
    s1 = _ev(day1_rows, prior=prior, now=datetime(day1.year, day1.month, day1.day, 17, 0, tzinfo=ET))
    assert s1.is_member is True and s1.member_since == date(2024, 1, 2)
    s2 = _ev(history, prior=s1)
    assert s2.is_member is True
    assert s2.member_since == date(2024, 1, 2), (
        f"member_since moved from 2024-01-02 to {s2.member_since} with no exit in the data"
    )


def test_regression_a_huge_integer_cap_is_dropped_not_a_crash():
    """REGRESSION (fixed 2026-09-24). Was: rules._as_positive_finite let
    ``float(10**400)``'s OverflowError escape (it caught only TypeError and ValueError);
    it now catches OverflowError too. JSON decodes a 400-digit
    integer literal to an int, so one malformed FMP row makes ``evaluate_membership``
    RAISE. The job logs that as "a bug in the rule" and fails the membership stage. The
    documented contract is that a malformed row is dropped. Fix: catch OverflowError in
    ``_as_positive_finite`` (and in ``_whale_common._diff_finite``, see below)."""
    good = _series([A] * 30)
    assert _ev(good + [(date(2026, 1, 2), 10 ** 400)]) == _ev(good)


# ══ membership: owner overrides vs the stored state ═════════════════════════════════════


OLD = date(2024, 3, 1)
MEMBER_SERIES = _series([B] * 25 + [A] * 15)              # joins inside the window
NON_MEMBER_SERIES = _series([A] * 25 + [B] * 25)


@pytest.mark.parametrize("mode, prior, closes, expect_member, expect_since", [
    # force_in: an existing stored date always wins
    (R.MODE_FORCE_IN, R.MembershipState(True, OLD, 0, 0, None, None), MEMBER_SERIES, True, OLD),
    (R.MODE_FORCE_IN, R.MembershipState(True, OLD, 0, 0, None, None), NON_MEMBER_SERIES, True, OLD),
    # force_in over a member with no stored date: the data's date, else today
    (R.MODE_FORCE_IN, R.MembershipState(True, None, 0, 0, None, None), MEMBER_SERIES, True,
     MEMBER_SERIES[25 + R.JOIN_CLOSES - 1][0]),
    (R.MODE_FORCE_IN, R.MembershipState(True, None, 0, 0, None, None), NON_MEMBER_SERIES, True, LAST),
    # force_in over a non-member
    (R.MODE_FORCE_IN, None, MEMBER_SERIES, True, MEMBER_SERIES[25 + R.JOIN_CLOSES - 1][0]),
    (R.MODE_FORCE_IN, None, NON_MEMBER_SERIES, True, LAST),
    (R.MODE_FORCE_IN, None, [], True, LAST),
    # force_out never carries a date
    (R.MODE_FORCE_OUT, R.MembershipState(True, OLD, 0, 0, None, None), MEMBER_SERIES, False, None),
    (R.MODE_FORCE_OUT, None, [], False, None),
])
def test_force_modes_decide_membership_and_member_since(mode, prior, closes, expect_member, expect_since):
    st = _ev(closes, mode=mode, prior=prior)
    assert st is not None
    assert st.is_member is expect_member and st.member_since == expect_since
    if closes:
        assert st.last_cap == closes[-1][1] and st.last_cap_date == closes[-1][0]


def test_force_modes_never_fail_closed_on_thin_or_missing_data():
    prior = R.MembershipState(False, None, 2, 9, 8e11, date(2026, 9, 1))
    thin = _series([B] * 5)
    st = _ev(thin, mode=R.MODE_FORCE_IN, prior=prior)
    assert st.is_member is True and (st.closes_at_or_above, st.closes_below) == (0, 5)
    assert _ev(thin, mode="auto", prior=prior) is None, "control: auto fails closed on 5 rows"
    for junk in ([], None, [(None, None)], _series([A] * 30, last=LAST - timedelta(days=30))):
        for mode in (R.MODE_FORCE_IN, R.MODE_FORCE_OUT):
            st = _ev(junk, mode=mode, prior=prior)
            assert (st.closes_at_or_above, st.closes_below, st.last_cap, st.last_cap_date) == \
                (2, 9, 8e11, date(2026, 9, 1))


@pytest.mark.parametrize("mode", ["AUTO", " auto", "force-in", "forced", "", None, 1, "auto\n"])
def test_an_unknown_mode_fails_closed_even_with_perfect_data(mode):
    assert _ev(_series([A] * 40), mode=mode) is None


# ══ the federal calendar vs an independent 5 U.S.C. 6103 computation ═══════════════════


def _nth_weekday_indep(y, m, weekday, n):
    days = [date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)
            if date(y, m, d).weekday() == weekday]
    return days[n - 1] if n > 0 else days[n]


@functools.lru_cache(maxsize=None)
def _federal_holidays_indep(y):
    out = set()
    for yy in (y - 1, y, y + 1):
        actual = [date(yy, 1, 1), _nth_weekday_indep(yy, 1, 0, 3), _nth_weekday_indep(yy, 2, 0, 3),
                  _nth_weekday_indep(yy, 5, 0, -1), date(yy, 7, 4), _nth_weekday_indep(yy, 9, 0, 1),
                  _nth_weekday_indep(yy, 10, 0, 2), date(yy, 11, 11), _nth_weekday_indep(yy, 11, 3, 4),
                  date(yy, 12, 25)]
        if yy >= 2021:
            actual.append(date(yy, 6, 19))
        for h in actual:
            if h.weekday() == 5:
                h = h - timedelta(days=1)
            elif h.weekday() == 6:
                h = h + timedelta(days=1)
            out.add(h)
    return {d for d in out if d.year == y}


def _is_bd_indep(d):
    return d.weekday() < 5 and d not in _federal_holidays_indep(d.year)


def _roll_indep(d):
    while not _is_bd_indep(d):
        d += timedelta(days=1)
    return d


def _add_bd_indep(d, n):
    while n:
        d += timedelta(days=1)
        if _is_bd_indep(d):
            n -= 1
    return d


def _quarter_end_indep(y, q):
    first_of_next = date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1)
    return first_of_next - timedelta(days=1)


def _due_indep(y, q):
    return _roll_indep(_quarter_end_indep(y, q) + timedelta(days=45))


# OPM's published lists (observed dates), checked by hand.
_OPM = {
    2025: {date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 5, 26),
           date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1), date(2025, 10, 13),
           date(2025, 11, 11), date(2025, 11, 27), date(2025, 12, 25)},
    2026: {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 5, 25),
           date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 10, 12),
           date(2026, 11, 11), date(2026, 11, 26), date(2026, 12, 25)},
    2027: {date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 5, 31),
           date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 10, 11),
           date(2027, 11, 11), date(2027, 11, 25), date(2027, 12, 24), date(2027, 12, 31)},
    2028: {date(2028, 1, 17), date(2028, 2, 21), date(2028, 5, 29), date(2028, 6, 19),
           date(2028, 7, 4), date(2028, 9, 4), date(2028, 10, 9), date(2028, 11, 10),
           date(2028, 11, 23), date(2028, 12, 25)},
}


@pytest.mark.parametrize("year", range(2021, 2046))
def test_federal_holidays_match_an_independent_5_usc_6103_calendar(year):
    assert R.federal_holidays(year) == _federal_holidays_indep(year)
    if year in _OPM:
        assert R.federal_holidays(year) == _OPM[year]


def test_business_day_helpers_match_the_independent_calendar_2025_2032():
    d = date(2025, 1, 1)
    while d <= date(2032, 12, 31):
        assert R.is_federal_business_day(d) is _is_bd_indep(d), d
        assert R.next_federal_business_day(d) == _roll_indep(d), d
        if d.toordinal() % 3 == 0:
            for n in range(0, 7):
                expected = d if n == 0 else _add_bd_indep(d, n)
                assert R.add_federal_business_days(d, n) == expected, (d, n)
        d += timedelta(days=1)


# Worked out by hand: Q1 May 15, Q2 Aug 14, Q3 Nov 14, Q4 Feb 14 of the next year, rolled
# past weekends and federal holidays (only Washington's Birthday ever intervenes).
_DUE_BY_HAND = {
    (2025, 1): date(2025, 5, 15), (2025, 2): date(2025, 8, 14), (2025, 3): date(2025, 11, 14),
    (2025, 4): date(2026, 2, 17),   # Sat 14th, Mon 16th = Washington's Birthday
    (2026, 1): date(2026, 5, 15), (2026, 2): date(2026, 8, 14), (2026, 3): date(2026, 11, 16),
    (2026, 4): date(2027, 2, 16),   # Sun 14th, Mon 15th = Washington's Birthday
    (2027, 1): date(2027, 5, 17), (2027, 2): date(2027, 8, 16), (2027, 3): date(2027, 11, 15),
    (2027, 4): date(2028, 2, 14),   # Monday; the holiday is the 21st
    (2028, 1): date(2028, 5, 15), (2028, 2): date(2028, 8, 14), (2028, 3): date(2028, 11, 14),
    (2028, 4): date(2029, 2, 14),
    (2029, 1): date(2029, 5, 15), (2029, 2): date(2029, 8, 14), (2029, 3): date(2029, 11, 14),
    (2029, 4): date(2030, 2, 14),
    (2030, 1): date(2030, 5, 15), (2030, 2): date(2030, 8, 14), (2030, 3): date(2030, 11, 14),
    (2030, 4): date(2031, 2, 14),
    (2031, 1): date(2031, 5, 15), (2031, 2): date(2031, 8, 14), (2031, 3): date(2031, 11, 14),
    (2031, 4): date(2032, 2, 17),   # Sat 14th, Mon 16th = Washington's Birthday
    (2032, 1): date(2032, 5, 17), (2032, 2): date(2032, 8, 16), (2032, 3): date(2032, 11, 15),
    (2032, 4): date(2033, 2, 14),
}


@pytest.mark.parametrize("yq", sorted(_DUE_BY_HAND))
def test_sec_13f_due_dates_2025_2032(yq):
    due = R.sec_13f_due_date(*yq)
    assert due == _DUE_BY_HAND[yq] == _due_indep(*yq)
    assert R.is_federal_business_day(due)


@pytest.mark.parametrize("yq", sorted(_DUE_BY_HAND))
def test_notice_boundaries_2025_2032(yq):
    """With the quarter BEFORE ``yq`` on file, "latest not in" turns on the day after
    due + 5 federal business days. "No newer filing" turns on after the NEXT quarter's
    due date + 5 business days."""
    on_file = R.period_label(*R.previous_quarter(*yq))
    due = _due_indep(*yq)
    grace = _add_bd_indep(due, 5)
    assert R.next_due_date(on_file) == due
    assert R.latest_not_in_due(on_file, due) is False
    assert R.latest_not_in_due(on_file, grace) is False
    assert R.latest_not_in_due(on_file, grace + timedelta(days=1)) is True
    assert all(R.latest_not_in_due(on_file, grace + timedelta(days=k)) for k in range(1, 120, 7))
    grace2 = _add_bd_indep(_due_indep(*R.next_quarter(*yq)), 5)
    assert R.no_newer_filing(on_file, grace2) is False
    assert R.no_newer_filing(on_file, grace2 + timedelta(days=1)) is True
    assert R.no_newer_filing(on_file, grace + timedelta(days=1)) is False, \
        "one missed due date is 'latest not in', never 'no newer filing'"


def test_quarter_arithmetic_round_trips_across_centuries():
    for y in range(1901, 2101):
        for q in (1, 2, 3, 4):
            assert R.next_quarter(*R.previous_quarter(y, q)) == (y, q)
            assert R.parse_period(R.period_label(y, q)) == (y, q)
            qe = R.quarter_end(y, q)
            assert qe == _quarter_end_indep(y, q)
            assert R.quarter_of(qe) == (y, q)
            assert R.quarter_of(qe + timedelta(days=1)) == R.next_quarter(y, q)


@pytest.mark.parametrize("bad", ["2026-Q2\n2026-Q3", "2026-Q2 ", "\t2026-Q2", "2026-Q2x",
                                 "02026-Q2", "2026-Q 2", "2026–Q2", b"2026-Q2", ["2026-Q2"]])
def test_period_parser_is_strict_but_tolerates_surrounding_whitespace(bad):
    if isinstance(bad, str) and bad.strip() == "2026-Q2":
        assert R.parse_period(bad) == (2026, 2)
    else:
        with pytest.raises(ValueError):
            R.parse_period(bad)


# ══ identifiers: CUSIP / ISIN check digits vs an independent implementation ════════════


_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _cusip_cd_indep(body8: str) -> int:
    """Modulus 10 "double-add-double": concatenate the (doubled) values, sum the digits."""
    s = "".join(str(int(ch, 36) * (2 if pos % 2 == 0 else 1))
                for pos, ch in enumerate(body8, start=1))
    return (10 - sum(int(x) for x in s) % 10) % 10


def _isin_cd_indep(payload11: str) -> int:
    """Try each check digit and keep the one that makes the full string Luhn-valid."""
    digits = "".join(str(int(c, 36)) for c in payload11)
    for cd in range(10):
        total = 0
        for i, ch in enumerate(reversed(digits + str(cd))):
            v = int(ch)
            if i % 2 == 1:
                v = v * 2 - 9 if v * 2 > 9 else v * 2
            total += v
        if total % 10 == 0:
            return cd
    raise AssertionError("unreachable")


@pytest.mark.parametrize("seed", range(8))
def test_cusip_check_digit_matches_an_independent_implementation(seed):
    rng = random.Random(seed)
    for _ in range(500):
        body = "".join(rng.choice(_ALNUM) for _ in range(8))
        assert R.cusip_check_digit(body) == _cusip_cd_indep(body), body


@pytest.mark.parametrize("seed", range(8))
def test_us_isin_derivation_is_exactly_right_and_refuses_every_wrong_check_digit(seed):
    rng = random.Random(100 + seed)
    for _ in range(250):
        body = rng.choice("0123456789") + "".join(rng.choice(_ALNUM) for _ in range(7))
        cd = _cusip_cd_indep(body)
        cusip = body + str(cd)
        isin = "US" + cusip + str(_isin_cd_indep("US" + cusip))
        assert R.cusip_to_us_isin(cusip) == isin
        assert R.cusip_to_us_isin(f"  {cusip.lower()}\n") == isin
        for wrong in set(range(10)) - {cd}:
            assert R.cusip_to_us_isin(body + str(wrong)) is None, (body, wrong)


@pytest.mark.parametrize("seed", range(4))
def test_a_cins_number_never_becomes_a_us_isin_even_with_a_valid_check_digit(seed):
    rng = random.Random(200 + seed)
    for _ in range(250):
        body = rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + "".join(rng.choice(_ALNUM) for _ in range(7))
        assert R.cusip_to_us_isin(body + str(_cusip_cd_indep(body))) is None


@pytest.mark.parametrize("seed", range(4))
def test_isin_check_digit_matches_an_independent_luhn(seed):
    rng = random.Random(300 + seed)
    for _ in range(500):
        payload = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(2)) + \
                  "".join(rng.choice(_ALNUM) for _ in range(9))
        assert R.isin_check_digit(payload) == _isin_cd_indep(payload), payload


@pytest.mark.parametrize("cusip, isin", [
    ("594918104", "US5949181045"),   # Microsoft
    ("67066G104", "US67066G1040"),   # NVIDIA
    ("02079K305", "US02079K3059"),   # Alphabet A
    ("02079K107", "US02079K1079"),   # Alphabet C
    ("023135106", "US0231351067"),   # Amazon
    ("30303M102", "US30303M1027"),   # Meta
    ("88160R101", "US88160R1014"),   # Tesla
    ("084670702", "US0846707026"),   # Berkshire B
    ("11135F101", "US11135F1012"),   # Broadcom
    ("874039100", "US8740391003"),   # TSMC ADR (a US CUSIP)
    ("92826C839", "US92826C8394"),   # Visa
    ("68389X105", "US68389X1054"),   # Oracle
])
def test_real_us_identifiers(cusip, isin):
    assert R.cusip_to_us_isin(cusip) == isin


@pytest.mark.parametrize("isin", ["NL0009805522", "CH0044328745", "KR7005930003",
                                  "TW0002330008", "SA14TG012N13", "CA98390R1029",
                                  "GB0002374006", "JP3633400001", "DE0007164600"])
def test_real_non_us_isin_check_digits(isin):
    assert R.isin_check_digit(isin[:11]) == int(isin[11])


@pytest.mark.parametrize("value, expected", [
    ("037833100", "037833100"), (" 037833100\n", "037833100"), ("67066g104", "67066G104"),
    ("0378 33100", None), ("03783310*", None), ("03783310@", None), ("03783310#", None),
    ("０３７８３３１００", None), (b"037833100", None), (37833100, None), (None, None),
    ("", None), ("037833100037833100", None),
])
def test_normalize_cusip(value, expected):
    assert R.normalize_cusip(value) == expected


# ══ accession numbers ═══════════════════════════════════════════════════════════════════


ACC = "0001045810-26-000065"
EDGAR = "https://www.sec.gov/Archives/edgar/data/1045810"


@pytest.mark.parametrize("link, expected", [
    (f"{EDGAR}/000104581026000065/{ACC}-index.htm", ACC),
    (f"{EDGAR}/000104581026000065/{ACC}-index.html", ACC),
    (f"{EDGAR}/{ACC}.txt", ACC),
    (f"{EDGAR}/000104581026000065/", ACC),
    (f"{EDGAR}/000104581026000065", ACC),
    (f"{EDGAR}/000104581026000065/primary_doc.xml", ACC),
    ("/000104581026000065/", ACC),
    (f"{EDGAR}/0{ACC}-index.htm", None),                    # 11 leading digits
    (f"{EDGAR}/{ACC}0-index.htm", None),                    # 7 trailing digits
    (f"{EDGAR}/0001045810260000651/", None),                # 19-digit folder
    (f"{EDGAR}/00010458102600006/", None),                  # 17-digit folder
    (f"{EDGAR}/000104581026000065.txt", None),              # folder form must end the segment
    ("000104581026000065", None),                           # no leading slash
    (" ", None),
])
def test_accession_from_link_variants(link, expected):
    assert R.accession_from_link(link) == expected


def test_the_dashed_accession_wins_over_a_disagreeing_folder():
    link = f"{EDGAR}/000104581026000065/0001193125-26-352454-index.htm"
    assert R.accession_from_link(link) == "0001193125-26-352454"


# ══ diff_13f_positions ════════════════════════════════════════════════════════════════════


Q1_END = date(2026, 3, 31)
ROW_KEYS = {"cusip", "symbol", "name", "change", "newly_listed", "shares", "prev_shares",
            "share_change", "value", "weight"}
COUNT_KEYS = {"newly_reported", "increased", "decreased", "no_longer_reported", "unchanged",
              "corporate_action"}
ORDER = ["newly_reported", "increased", "decreased", "no_longer_reported", "corporate_action"]


def _h(cusip, shares, symbol=None, *, value=None, weight=None, ipo=None, name="X"):
    return {"cusip": cusip, "symbol": symbol, "name": name, "shares": shares,
            "value": value if value is not None else (shares * 10.0 if isinstance(shares, float) else None),
            "weight": weight, "ipo_date": ipo}


def _q(curr, prev, *, split_ratios=None, unclassified=None, comparison="quarter",
       cutoff=Q1_END, prev_period="2026-Q1"):
    return W.diff_13f_positions(
        curr, prev, split_ratios=split_ratios or {}, unclassified=unclassified or set(),
        comparison=comparison, prev_ipo_cutoff=cutoff, prev_period=prev_period,
    )


def _one(ch):
    assert len(ch["rows"]) == 1, ch
    return ch["rows"][0]


# ── contract invariants, against a reference classifier ───────────────────────────────


_CUSIP_POOL = ["037833100", "594918104", "67066G104", "02079K305", "02079K107",
               "023135106", "H1467J104", "N97284108", "88160R101"]
_SYM_POOL = ["AAPL", "MSFT", "ABC", "ABC", None, None, "--", " abc ", "XYZ", ""]
_SHARE_POOL = [100.0, 100.0, 150.0, 50.0, 100.5, 101.0, 99.0, 1000.0, 10.0, 0.5]


def _ref_positions(rows):
    out = {}
    for r in rows or ():
        if not isinstance(r, dict):
            continue
        cu = r.get("cusip").strip().upper() if isinstance(r.get("cusip"), str) else ""
        sh = r.get("shares")
        if not re.fullmatch(r"[0-9A-Z]{9}", cu) or isinstance(sh, bool) or sh is None:
            continue
        try:
            sh = float(sh)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(sh) or sh <= 0:
            continue
        sym = r.get("symbol").strip().upper() if isinstance(r.get("symbol"), str) else ""
        sym = sym if sym and sym != "--" else None
        if cu in out:
            out[cu] = (out[cu][0] + sh, out[cu][1] or sym)
        else:
            out[cu] = (sh, sym)
    return out


def _ref_diff(curr, prev):
    """No splits, nothing unclassified: {cusip: (change, shares, prev_shares, share_change)}."""
    c, p = _ref_positions(curr), _ref_positions(prev)

    def both(cs, ps):
        delta = cs - ps
        if abs(delta) < 1.0:
            return ("unchanged", cs, ps, 0.0)
        return ("increased" if delta > 0 else "decreased", cs, ps, delta)

    out = {}
    for cu in c.keys() | p.keys():
        if cu in c and cu in p:
            out[cu] = both(c[cu][0], p[cu][0])
        elif cu in c:
            out[cu] = ("newly_reported", c[cu][0], None, None)
        else:
            out[cu] = ("no_longer_reported", None, p[cu][0], None)
    new_by, gone_by = {}, {}
    for cu, o in out.items():
        sym = (c.get(cu) or p.get(cu))[1]
        if sym and o[0] == "newly_reported":
            new_by.setdefault(sym, []).append(cu)
        elif sym and o[0] == "no_longer_reported":
            gone_by.setdefault(sym, []).append(cu)
    for sym, news in new_by.items():
        gones = gone_by.get(sym, [])
        if len(news) == 1 and len(gones) == 1:
            del out[gones[0]]
            out[news[0]] = both(c[news[0]][0], p[gones[0]][0])
    return out


def _random_book(rng):
    rows = []
    for _ in range(rng.randint(1, 8)):
        cu = rng.choice(_CUSIP_POOL)
        rows.append(_h(cu.lower() if rng.random() < 0.1 else cu, rng.choice(_SHARE_POOL),
                       rng.choice(_SYM_POOL), value=rng.choice([None, 1.0, rng.uniform(0, 1e9)]),
                       weight=rng.choice([None, 0.1, 0.3]),
                       ipo=rng.choice([None, "2026-06-12", "2026-03-31", "2026-04-01", "junk"])))
    if rng.random() < 0.3:
        rows.append(_h(rng.choice(_CUSIP_POOL), rng.choice([float("nan"), 0.0, -5.0, None])))
    return rows


def _check_contract(ch):
    assert set(ch) == {"comparison", "prev_period", "counts", "rows"}
    assert set(ch["counts"]) == COUNT_KEYS
    rows, counts = ch["rows"], ch["counts"]
    assert sum(counts.values()) == len(rows) + counts["unchanged"]
    for k in ORDER:
        assert counts[k] == sum(1 for r in rows if r["change"] == k), k
    assert all(r["change"] != "unchanged" for r in rows)
    assert len({r["cusip"] for r in rows}) == len(rows)
    kinds = [ORDER.index(r["change"]) for r in rows]
    assert kinds == sorted(kinds), "rows are grouped by kind in the contract order"
    new = [(-(r["value"] or 0.0), r["cusip"]) for r in rows if r["change"] == "newly_reported"]
    assert new == sorted(new), "newly reported rows: largest value first, then CUSIP"
    for r in rows:
        assert set(r) == ROW_KEYS
        if r["change"] == "newly_reported":
            assert r["prev_shares"] is None and r["share_change"] is None and r["shares"] > 0
        if r["change"] == "no_longer_reported":
            assert r["shares"] is None and r["value"] is None and r["weight"] is None
            assert r["prev_shares"] > 0 and r["share_change"] is None
        if r["change"] in ("increased", "decreased"):
            assert r["share_change"] == pytest.approx(r["shares"] - r["prev_shares"])
            assert (r["share_change"] >= 1.0) if r["change"] == "increased" else (r["share_change"] <= -1.0)
        if r["change"] == "corporate_action":
            assert r["share_change"] is None
        assert r["newly_listed"] is False or r["change"] == "newly_reported"
    json.dumps(ch, allow_nan=False)


@pytest.mark.parametrize("seed", range(400))
def test_diff_matches_a_reference_classifier_and_keeps_the_json_contract(seed):
    rng = random.Random(seed)
    curr, prev = _random_book(rng), _random_book(rng)
    expected = _ref_diff(curr, prev)
    if not _ref_positions(curr) or not _ref_positions(prev):
        with pytest.raises(ValueError):
            _q(curr, prev)
        return
    snap = copy.deepcopy((curr, prev))
    ch = _q(curr, prev)
    assert (curr, prev) == snap, "inputs mutated"
    _check_contract(ch)
    got = {r["cusip"]: (r["change"], r["shares"], r["prev_shares"], r["share_change"]) for r in ch["rows"]}
    want_rows = {cu: v for cu, v in expected.items() if v[0] != "unchanged"}
    assert got == want_rows
    assert ch["counts"]["unchanged"] == sum(1 for v in expected.values() if v[0] == "unchanged")


@pytest.mark.parametrize("seed", range(200))
def test_diff_contract_holds_with_random_splits_and_unclassified(seed):
    rng = random.Random(5_000 + seed)
    curr, prev = _random_book(rng), _random_book(rng)
    if not _ref_positions(curr) or not _ref_positions(prev):
        return
    ratios = {s: rng.choice([2.0, 10.0, 0.1, 1.5, 1.0, 0.5]) for s in ("ABC", "AAPL", "XYZ")
              if rng.random() < 0.4}
    flagged = {s for s in ("ABC", "MSFT", "XYZ") if rng.random() < 0.4}
    ch = _q(curr, prev, split_ratios=ratios, unclassified=flagged)
    _check_contract(ch)
    total = len(set(_ref_positions(curr)) | set(_ref_positions(prev)))
    joins = total - len(_ref_diff(curr, prev))
    assert sum(ch["counts"].values()) == total - joins, "every position counted exactly once"


# ── comparisons that list nothing ───────────────────────────────────────────────────────


@pytest.mark.parametrize("curr, prev", [([], None), (None, None), (["junk"], [None]),
                                        ([_h("037833100", 5.0)], [_h("037833100", 1.0)])])
def test_first_filing_and_gap_never_validate_and_never_list(curr, prev):
    first = _q(curr, prev, comparison="first_filing", prev_period="2026-Q1")
    assert first == {"comparison": "first_filing", "prev_period": None,
                     "counts": {k: 0 for k in COUNT_KEYS}, "rows": []}
    gap = _q(curr, prev, comparison="gap", prev_period="2025-Q3")
    assert gap["prev_period"] == "2025-Q3" and gap["rows"] == [] and set(gap["counts"].values()) == {0}
    assert _q(curr, prev, comparison="gap", prev_period=None)["prev_period"] is None


@pytest.mark.parametrize("comparison", ["Quarter", "QUARTER", " quarter", "", None, "first-filing"])
def test_comparison_is_matched_exactly(comparison):
    with pytest.raises(ValueError):
        _q([_h("037833100", 1.0)], [_h("037833100", 1.0)], comparison=comparison)


@pytest.mark.parametrize("curr, prev", [
    ([], [_h("037833100", 1.0)]), (None, [_h("037833100", 1.0)]),
    ([_h("037833100", 1.0)], []), ([_h("037833100", 1.0)], None),
    ([_h("037833100", float("nan"))], [_h("037833100", 1.0)]),
    ([_h("037833100", 1.0)], ["junk", {"cusip": "bad", "shares": 5.0}, _h("037833100", 0.0)]),
])
def test_a_quarter_diff_needs_usable_positions_on_both_sides(curr, prev):
    """An empty side would book the whole other filing as new, or as gone."""
    with pytest.raises(ValueError, match="both sides"):
        _q(curr, prev)


def test_quarter_without_prev_period_keeps_none():
    assert _q([_h("037833100", 1.0)], [_h("037833100", 1.0)], prev_period=None)["prev_period"] is None


# ── CUSIP re-keys ───────────────────────────────────────────────────────────────────────


def test_rekey_joins_across_case_and_whitespace_and_keeps_the_new_cusip():
    prev = [_h("111111118", 100.0, " abc "), _h("458140100", 5.0, "INTC")]
    curr = [_h("222222226", 125.0, "ABC", ipo="2026-06-12"), _h("458140100", 5.0, "INTC")]
    row = _one(_q(curr, prev))
    assert row["cusip"] == "222222226" and row["symbol"] == "ABC"
    assert row["change"] == "increased" and row["share_change"] == 25.0
    assert row["prev_shares"] == 100.0 and row["newly_listed"] is False


def test_the_same_cusip_in_different_case_is_one_position():
    ch = _q([_h("67066g104", 100.0, "NVDA")], [_h("67066G104", 100.0, "nvda")])
    assert ch["rows"] == [] and ch["counts"]["unchanged"] == 1


def test_a_dashes_symbol_never_joins():
    ch = _q([_h("222222226", 100.0, "--")], [_h("111111118", 100.0, "--")])
    assert ch["counts"]["newly_reported"] == 1 and ch["counts"]["no_longer_reported"] == 1


def test_one_new_two_gone_is_ambiguous_and_not_joined(caplog):
    prev = [_h("111111118", 60.0, "ABC"), _h("333333334", 40.0, "ABC")]
    with caplog.at_level(logging.WARNING):
        ch = _q([_h("222222226", 100.0, "ABC")], prev)
    assert ch["counts"]["newly_reported"] == 1 and ch["counts"]["no_longer_reported"] == 2
    assert "ambiguous" in caplog.text


def test_a_rekey_with_a_reverse_split_is_restated():
    """A reverse split often issues a NEW CUSIP: the join and the split must compose."""
    prev = [_h("111111118", 1_000_000.0, "ABC")]
    curr = [_h("222222226", 100_000.0, "ABC")]
    held = _q(curr, prev, split_ratios={"ABC": 0.1})
    assert held["rows"] == [] and held["counts"]["unchanged"] == 1
    flagged = _one(_q(curr, prev, unclassified={"ABC"}))
    assert flagged["change"] == "corporate_action" and flagged["cusip"] == "222222226"


def test_a_rekey_joined_position_that_only_moved_by_a_share_fraction_is_unchanged():
    ch = _q([_h("222222226", 100.4, "ABC")], [_h("111111118", 100.0, "ABC")])
    assert ch["rows"] == [] and ch["counts"] == {**{k: 0 for k in COUNT_KEYS}, "unchanged": 1}


# ── splits: the SPLIT_SUPPRESS bands ────────────────────────────────────────────────────


@pytest.mark.parametrize("curr_shares, change, share_change", [
    (1150.0, "increased", 150.0),        # obs 11.5: inside +15% -> restated to 1,000
    (851.0, "decreased", -149.0),        # obs 8.51: inside -15%
    (1152.0, "corporate_action", None),  # obs 11.52: outside the band, jumped -> SUPPRESS
    (849.0, "corporate_action", None),   # obs 8.49: outside the band, jumped
    (550.0, "corporate_action", None),   # obs 5.5 == midpoint -> jumped (>=)
    (549.0, "increased", 449.0),         # obs 5.49 < midpoint -> the raw diff stands
    (100.0, None, None),                 # already split-adjusted feed, nothing moved
])
def test_forward_split_bands(curr_shares, change, share_change):
    ch = _q([_h("67066G104", curr_shares, "NVDA")], [_h("67066G104", 100.0, "NVDA")],
            split_ratios={"NVDA": 10.0})
    if change is None:
        assert ch["rows"] == [] and ch["counts"]["unchanged"] == 1
        return
    row = _one(ch)
    assert row["change"] == change
    if share_change is None:
        assert row["share_change"] is None
    else:
        assert row["share_change"] == pytest.approx(share_change)


@pytest.mark.parametrize("curr_shares, change", [
    (86.0, "decreased"),          # obs 0.086: inside the band -> restated 100, -14
    (114.0, "increased"),         # obs 0.114
    (84.0, "corporate_action"),   # obs 0.084: outside, jumped (<= midpoint 0.55)
    (550.0, "corporate_action"),  # obs 0.55 == midpoint -> jumped
    (551.0, "decreased"),         # obs 0.551 -> the raw diff, -449
    (1000.0, None),               # the feed already restated it
])
def test_reverse_split_bands(curr_shares, change):
    ch = _q([_h("482480100", curr_shares, "KLAC")], [_h("482480100", 1000.0, "KLAC")],
            split_ratios={"KLAC": 0.1})
    if change is None:
        assert ch["rows"] == []
    else:
        assert _one(ch)["change"] == change


def test_a_split_is_found_through_the_previous_symbol_after_a_ticker_change():
    ch = _q([_h("30303M102", 2_000.0, "META")], [_h("30303M102", 100.0, "FB")],
            split_ratios={"FB": 20.0})
    assert ch["rows"] == [] and ch["counts"]["unchanged"] == 1


def test_a_suppressed_split_row_reports_the_raw_previous_count():
    row = _one(_q([_h("67066G104", 700.0, "NVDA")], [_h("67066G104", 100.0, "NVDA")],
                  split_ratios={"NVDA": 10.0}))
    assert row["change"] == "corporate_action" and row["prev_shares"] == 100.0


# ── the magnitude backstop ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("curr_shares, prev_shares, change", [
    (200.0, 100.0, "corporate_action"),   # +100 >= 0.5 x 200
    (199.0, 100.0, "increased"),          # +99  <  99.5
    (100.0, 150.0, "corporate_action"),   # -50  >= 50
    (101.0, 151.0, "decreased"),          # -50  <  50.5
    (1.0, 1_000.0, "corporate_action"),
])
def test_implausible_flow_boundary_when_flagged(curr_shares, prev_shares, change):
    flagged = _one(_q([_h("111111118", curr_shares, "ABC")], [_h("111111118", prev_shares, "ABC")],
                      unclassified={"ABC"}))
    assert flagged["change"] == change
    plain = _one(_q([_h("111111118", curr_shares, "ABC")], [_h("111111118", prev_shares, "ABC")]))
    assert plain["change"] in ("increased", "decreased"), "unflagged: never suppressed"


def test_the_flag_is_found_through_the_previous_symbol():
    row = _one(_q([_h("111111118", 300.0, "NEW")], [_h("111111118", 100.0, "OLD")],
                  unclassified={"OLD"}))
    assert row["change"] == "corporate_action" and row["symbol"] == "NEW"


def test_a_backstop_after_a_restated_split_reads_the_restated_flow():
    """10:1 split plus a 10% buy: inside the clean band, so the count is restated and the
    backstop judges only the real 100-share flow (not 1,000 shares of "flow")."""
    row = _one(_q([_h("111111118", 1_100.0, "ABC")], [_h("111111118", 100.0, "ABC")],
                  split_ratios={"ABC": 10.0}, unclassified={"ABC"}))
    assert row["change"] == "increased" and row["share_change"] == pytest.approx(100.0)


# ── the one-share rule, weights, values ────────────────────────────────────────────────


@pytest.mark.parametrize("curr, change", [
    (100.999, None), (101.0, "increased"), (99.0, "decreased"), (99.0001, None),
])
def test_a_move_under_one_share_is_unchanged(curr, change):
    ch = _q([_h("111111118", curr, "ABC")], [_h("111111118", 100.0, "ABC")])
    if change is None:
        assert ch["rows"] == [] and ch["counts"]["unchanged"] == 1
    else:
        assert _one(ch)["change"] == change


def test_weights_and_values_pass_through_and_duplicates_sum():
    curr = [_h("111111118", 60.0, "ABC", value=600.0, weight=0.2),
            _h("111111118", 40.0, "ABC", value=400.0, weight=0.3),
            _h("222222226", 10.0, "NEW", value=100.0, weight=0.5)]
    prev = [_h("111111118", 50.0, "ABC", value=450.0, weight=1.0),
            _h("333333334", 5.0, "OLD", value=50.0, weight=0.1)]
    ch = _q(curr, prev)
    by = {r["cusip"]: r for r in ch["rows"]}
    assert by["111111118"]["shares"] == 100.0 and by["111111118"]["weight"] == pytest.approx(0.5)
    assert by["111111118"]["value"] == 1000.0
    assert by["222222226"]["weight"] == 0.5 and by["222222226"]["value"] == 100.0
    assert by["333333334"]["weight"] is None and by["333333334"]["value"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "x", None, True])
def test_non_finite_value_or_weight_is_none_never_nan(bad):
    curr = {"cusip": "111111118", "symbol": "ABC", "name": "X", "shares": 150.0,
            "value": bad, "weight": bad, "ipo_date": None}
    row = _one(_q([curr], [_h("111111118", 100.0, "ABC")]))
    assert row["value"] is None and row["weight"] is None
    json.dumps(row, allow_nan=False)


# ── newly listed ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ipo, expected", [
    ("2026-03-31", False), ("2026-04-01", True), ("2026-04-01T00:00:00", True),
    (datetime(2026, 4, 1, 0, 0), True), (datetime(2026, 3, 31, 23, 59), False),
    (date(2026, 3, 31), False), ("2026-3-31", False), ("", False), (20260401, False),
    ("0000-00-00", False),
])
def test_newly_listed_boundary(ipo, expected):
    ch = _q([_h("84615Q103", 5.0, "SPCX", ipo=ipo), _h("458140100", 1.0, "INTC")],
            [_h("458140100", 1.0, "INTC")])
    (row,) = [r for r in ch["rows"] if r["symbol"] == "SPCX"]
    assert row["newly_listed"] is expected


def test_newly_listed_is_decided_by_the_current_row_only():
    """A previous row's IPO date must not mark a newly reported position."""
    ch = _q([_h("84615Q103", 5.0, "SPCX"), _h("458140100", 1.0, "INTC")],
            [_h("458140100", 1.0, "INTC", ipo="2026-06-12")])
    (row,) = [r for r in ch["rows"] if r["symbol"] == "SPCX"]
    assert row["newly_listed"] is False


# ── the overflow defect (fixed) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("where", ["shares", "value", "split_ratio"])
def test_regression_a_huge_integer_is_skipped_by_the_diff_not_a_crash(where):
    """REGRESSION (fixed 2026-09-24). Was: _whale_common._diff_finite let
    ``float(10**400)``'s OverflowError escape (it caught TypeError and ValueError only),
    so a 400-digit JSON integer in shares, value or a split ratio raised out of
    ``diff_13f_positions`` instead of being skipped as garbage. It now catches
    OverflowError, as do the builder's ``_positive_finite`` and ``_num_key``."""
    huge = 10 ** 400
    curr = [_h("458140100", 5.0, "INTC")]
    prev = [_h("458140100", 5.0, "INTC")]
    kwargs = {}
    if where == "shares":
        curr.append(_h("037833100", 1.0, "AAPL") | {"shares": huge})
    elif where == "value":
        curr[0] = curr[0] | {"value": huge}
    else:
        kwargs["split_ratios"] = {"INTC": huge}
    ch = _q(curr, prev, **kwargs)
    assert ch["counts"]["unchanged"] == 1
    json.dumps(ch, allow_nan=False)
