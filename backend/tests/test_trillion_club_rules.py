"""Pure rules of the Trillion-Dollar Club: membership, the SEC 13F calendar, identifiers.

Membership is pinned on the shapes that actually occur near the line (AMD's first three
closes over $1T, LLY at 9 vs 10, MU/LLY's back-and-forth), plus every malformed input the
FMP series can carry. Due dates are pinned on the federal calendar, which is NOT the NYSE
one. Hermetic: no I/O at all.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.services.trillion_club import rules as R
from app.utils.market_hours import ET, is_trading_day

T = R.THRESHOLD_USD
ABOVE, BELOW = 1.05 * T, 0.95 * T


def _weekdays_ending(last: date, n: int):
    """``n`` weekday dates ending on ``last`` (ascending)."""
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


LAST = date(2026, 9, 23)                          # a Wednesday
AFTER_CLOSE = datetime(2026, 9, 23, 17, 0, tzinfo=ET)


def _series(*caps, last=LAST):
    return list(zip(_weekdays_ending(last, len(caps)), caps))


def _eval(closes, mode="auto", prior=None, now=AFTER_CLOSE):
    return R.evaluate_membership(
        closes, mode=mode, prior=prior, today_et=now.astimezone(ET).date(), now_et=now,
        log_ctx="test",
    )


# ── membership: the join side ────────────────────────────────────────────────────────


def test_amd_with_three_qualifying_closes_is_not_yet_a_member():
    # AMD 2026-09-21/22/23: $1,004B, $1,017B, $1,002B after a year below the line.
    caps = [0.9 * T] * 257 + [1.00391e12, 1.01737e12, 1.00243e12]
    st = _eval(_series(*caps))
    assert st is not None
    assert st.is_member is False and st.member_since is None
    assert st.closes_at_or_above == 3 and st.closes_below == 0
    assert st.last_cap == pytest.approx(1.00243e12) and st.last_cap_date == LAST


def test_nine_closes_do_not_join_and_the_tenth_does():
    nine = _eval(_series(*([BELOW] * 30 + [ABOVE] * 9)))
    ten = _eval(_series(*([BELOW] * 30 + [ABOVE] * 10)))
    assert nine.is_member is False and nine.closes_at_or_above == 9
    assert ten.is_member is True and ten.member_since == LAST, (
        "member_since is the close that completed the 10-close streak"
    )


def test_exactly_one_trillion_counts_as_at_or_above():
    st = _eval(_series(*([BELOW] * 20 + [float(T)] * 10)))
    assert st.is_member is True
    just_under = _eval(_series(*([BELOW] * 20 + [T - 1.0] * 10)))
    assert just_under.is_member is False


def test_one_close_below_resets_the_join_streak():
    st = _eval(_series(*([BELOW] * 20 + [ABOVE] * 9 + [BELOW] + [ABOVE] * 9)))
    assert st.is_member is False and st.closes_at_or_above == 9


# ── membership: the leave side ───────────────────────────────────────────────────────


def test_nineteen_closes_below_stay_and_the_twentieth_leaves():
    stay = _eval(_series(*([ABOVE] * 30 + [BELOW] * 19)))
    leave = _eval(_series(*([ABOVE] * 30 + [BELOW] * 20)))
    assert stay.is_member is True and stay.closes_below == 19
    assert leave.is_member is False and leave.member_since is None and leave.closes_below == 20


def test_alternating_nineteen_below_one_above_keeps_a_member_in():
    """⚠️ KNOWN property of the owner's rule (20 STRAIGHT closes below): one close above
    resets the leave count, so this member spends 95% of its closes under the line and
    stays in. Pinned so a change to the rule is a deliberate decision; `force_out` is the
    remedy. The critic's alternative (average-based bands) was not adopted."""
    caps = [ABOVE] * 10 + ([BELOW] * 19 + [ABOVE]) * 12
    st = _eval(_series(*caps))
    assert st.is_member is True
    assert sum(1 for c in caps[10:] if c < T) / len(caps[10:]) == pytest.approx(0.95)


def test_alternating_series_never_joins_from_outside():
    st = _eval(_series(*(([BELOW] * 19 + [ABOVE]) * 13)))
    assert st.is_member is False and st.closes_at_or_above == 1


def test_leave_then_rejoin_takes_the_new_join_date():
    caps = [ABOVE] * 10 + [BELOW] * 20 + [ABOVE] * 12
    series = _series(*caps)
    st = _eval(series)
    assert st.is_member is True
    assert st.member_since == series[-3][0], "the 10th close of the NEW streak"


# ── membership: fail closed ──────────────────────────────────────────────────────────


def test_fewer_than_twenty_rows_fails_closed():
    assert _eval(_series(*([ABOVE] * 19))) is None
    assert _eval([]) is None
    assert _eval(None) is None
    assert _eval(_series(*([ABOVE] * 20))).is_member is True


def test_malformed_rows_are_dropped_and_can_push_below_the_minimum(caplog):
    good = _series(*([ABOVE] * 18))
    junk = [
        (LAST - timedelta(days=40), float("nan")),
        (LAST - timedelta(days=41), float("inf")),
        (LAST - timedelta(days=42), -5.0),
        (LAST - timedelta(days=43), 0),
        (LAST - timedelta(days=44), None),
        ("not-a-date", ABOVE),
        (None, ABOVE),
        (LAST - timedelta(days=45), True),          # bool is not a number here
        "garbage",
        (1, 2, 3),
    ]
    with caplog.at_level(logging.WARNING):
        assert _eval(good + junk) is None
    assert "dropped 10 malformed" in caplog.text
    # With enough good rows the junk is simply ignored.
    st = _eval(_series(*([ABOVE] * 25)) + junk)
    assert st is not None and st.is_member is True and st.closes_at_or_above == 25


def test_unsorted_fmp_shaped_input_matches_sorted():
    """FMP returns newest first, with ISO strings; order and type must not matter."""
    series = _series(*([BELOW] * 25 + [ABOVE] * 12))
    fmp_shaped = [(d.isoformat(), c) for d, c in reversed(series)]
    assert _eval(fmp_shaped) == _eval(series)


def test_identical_duplicates_collapse_but_conflicting_ones_fail_closed(caplog):
    series = _series(*([ABOVE] * 25))
    assert _eval(series + series[-5:]) == _eval(series)
    with caplog.at_level(logging.WARNING):
        assert _eval(series + [(series[-1][0], BELOW)]) is None
    assert "two different caps" in caplog.text


def test_a_stale_series_fails_closed_in_auto():
    old_last = LAST - timedelta(days=R.MAX_CLOSE_STALENESS_DAYS + 3)
    assert _eval(_series(*([ABOVE] * 40), last=old_last)) is None
    recent_enough = LAST - timedelta(days=R.MAX_CLOSE_STALENESS_DAYS)
    assert _eval(_series(*([ABOVE] * 40), last=recent_enough)) is not None


def test_unknown_mode_fails_closed_loudly(caplog):
    with caplog.at_level(logging.ERROR):
        assert _eval(_series(*([ABOVE] * 30)), mode="forced") is None
    assert "unknown membership_mode" in caplog.text


# ── membership: dated closes only ────────────────────────────────────────────────────


def test_todays_intraday_row_is_dropped_before_the_close():
    """FMP stamps an intraday figure with today's date. Before 16:00 ET the 10th
    "close" is not a close yet; after 16:00 it is."""
    today = date(2026, 9, 24)                      # a Thursday
    series = _series(*([BELOW] * 30 + [ABOVE] * 10), last=today)
    before = _eval(series, now=datetime(2026, 9, 24, 10, 25, tzinfo=ET))
    after = _eval(series, now=datetime(2026, 9, 24, 16, 5, tzinfo=ET))
    assert before.is_member is False and before.closes_at_or_above == 9
    assert before.last_cap_date == date(2026, 9, 23)
    assert after.is_member is True and after.last_cap_date == today


def test_a_weekend_row_is_not_a_close():
    series = _series(*([ABOVE] * 30), last=date(2026, 9, 25))   # ends Friday
    sat_row = [(date(2026, 9, 26), BELOW)]
    st = _eval(series + sat_row, now=datetime(2026, 9, 26, 12, 0, tzinfo=ET))
    assert st.is_member is True and st.last_cap_date == date(2026, 9, 25)


def test_a_utc_now_is_read_in_eastern_time():
    """21:00 UTC is 17:00 ET — after the close — so today's row counts."""
    today = date(2026, 9, 24)
    series = _series(*([BELOW] * 30 + [ABOVE] * 10), last=today)
    from datetime import timezone
    st = R.evaluate_membership(
        series, mode="auto", prior=None, today_et=today,
        now_et=datetime(2026, 9, 24, 21, 0, tzinfo=timezone.utc),
    )
    assert st.is_member is True


# ── membership: the replay window and member_since ────────────────────────────────────


def test_long_history_is_trimmed_to_the_replay_window():
    caps = [ABOVE] * 300
    series = _series(*caps)
    st = _eval(series)
    assert st.is_member is True and st.closes_at_or_above == R.MAX_REPLAY_ROWS
    window = series[-R.MAX_REPLAY_ROWS:]
    assert st.member_since == window[R.JOIN_CLOSES - 1][0]


def test_a_censored_membership_keeps_the_older_stored_date():
    """The window cannot see when a long-standing member joined; keep the stored date
    instead of sliding member_since forward every day."""
    prior = R.MembershipState(True, date(2020, 1, 2), 500, 0, 5e12, LAST - timedelta(days=1))
    st = _eval(_series(*([ABOVE] * 300)), prior=prior)
    assert st.member_since == date(2020, 1, 2)


def test_an_uncensored_join_uses_the_window_date_even_if_prior_is_older():
    prior = R.MembershipState(True, date(2020, 1, 2), 0, 0, None, None)
    series = _series(*([BELOW] * 40 + [ABOVE] * 15))
    st = _eval(series, prior=prior)
    assert st.member_since == series[40 + R.JOIN_CLOSES - 1][0]


def test_membership_state_is_immutable():
    with pytest.raises(Exception):
        R.NOT_A_MEMBER.is_member = True


# ── membership: owner overrides ─────────────────────────────────────────────────────


def test_force_in_decides_regardless_of_the_data():
    st = _eval(_series(*([BELOW] * 5)), mode="force_in")
    assert st.is_member is True and st.member_since == LAST
    assert st.last_cap == BELOW and st.closes_below == 5


def test_force_in_keeps_a_stored_member_since():
    prior = R.MembershipState(True, date(2025, 1, 6), 3, 0, 1e12, date(2025, 1, 6))
    assert _eval(_series(*([BELOW] * 30)), mode="force_in", prior=prior).member_since == date(2025, 1, 6)


def test_force_in_with_no_rows_keeps_the_stored_facts():
    """Aramco / Samsung: a hand-entered cap, no FMP series at all."""
    prior = R.MembershipState(False, None, 4, 2, 1.6e12, date(2026, 9, 1))
    st = _eval([], mode="force_in", prior=prior)
    assert st == R.MembershipState(True, LAST, 4, 2, 1.6e12, date(2026, 9, 1))


def test_force_out_overrides_an_always_above_series():
    st = _eval(_series(*([ABOVE] * 40)), mode="force_out",
               prior=R.MembershipState(True, date(2025, 1, 1), 0, 0, None, None))
    assert st.is_member is False and st.member_since is None
    assert st.closes_at_or_above == 40 and st.last_cap == ABOVE


def test_force_mode_with_stale_data_keeps_the_stored_cap():
    prior = R.MembershipState(True, date(2025, 1, 1), 7, 0, 1.2e12, date(2026, 9, 1))
    stale = _series(*([ABOVE] * 30), last=LAST - timedelta(days=30))
    st = _eval(stale, mode="force_in", prior=prior)
    assert st.last_cap == 1.2e12 and st.last_cap_date == date(2026, 9, 1)


# ── quarters ────────────────────────────────────────────────────────────────────────


def test_period_helpers_round_trip():
    assert R.period_label(2026, 2) == "2026-Q2"
    assert R.parse_period("2026-Q2") == (2026, 2)
    assert R.parse_period(" 2026-Q4 ") == (2026, 4)
    assert R.quarter_end(2026, 1) == date(2026, 3, 31)
    assert R.quarter_end(2026, 4) == date(2026, 12, 31)
    assert R.previous_quarter(2026, 1) == (2025, 4)
    assert R.previous_quarter(2026, 3) == (2026, 2)
    assert R.next_quarter(2026, 4) == (2027, 1)
    assert R.quarter_of(date(2026, 9, 30)) == (2026, 3)
    assert R.quarter_of(date(2026, 10, 1)) == (2026, 4)


@pytest.mark.parametrize("bad", ["2026-Q5", "2026-Q0", "2026Q2", "26-Q2", "", None, 2026, "2026-q2"])
def test_parse_period_rejects_garbage(bad):
    with pytest.raises(ValueError):
        R.parse_period(bad)


@pytest.mark.parametrize("y, q", [(2026, 0), (2026, 5), (2026, "2"), (True, 1), (2026.0, 1)])
def test_quarter_helpers_reject_bad_input(y, q):
    with pytest.raises(ValueError):
        R.quarter_end(y, q)


# ── SEC 13F due dates on the FEDERAL calendar ─────────────────────────────────────────


@pytest.mark.parametrize("y, q, due", [
    (2026, 2, date(2026, 8, 14)),    # Friday — no roll
    (2026, 3, date(2026, 11, 16)),   # Nov 14 is a Saturday
    (2026, 4, date(2027, 2, 16)),    # Feb 14 Sunday, Feb 15 Washington's Birthday
    (2027, 1, date(2027, 5, 17)),    # May 15 is a Saturday
    (2025, 4, date(2026, 2, 17)),    # Feb 14 Saturday, Feb 16 Washington's Birthday
    (2024, 4, date(2025, 2, 14)),
])
def test_sec_13f_due_dates(y, q, due):
    assert R.sec_13f_due_date(y, q) == due


def test_every_due_date_is_a_federal_business_day_within_the_roll_window():
    for y in range(2020, 2036):
        for q in (1, 2, 3, 4):
            due = R.sec_13f_due_date(y, q)
            gap = (due - R.quarter_end(y, q)).days
            assert R.is_federal_business_day(due), (y, q, due)
            assert 45 <= gap <= 49, (y, q, due, gap)


def test_the_federal_calendar_is_not_the_nyse_calendar():
    """Columbus/Veterans Day close the SEC, not the exchange; Good Friday the reverse."""
    h = R.federal_holidays(2026)
    assert date(2026, 10, 12) in h and date(2026, 11, 11) in h
    assert is_trading_day(date(2026, 10, 12)) and is_trading_day(date(2026, 11, 11))
    good_friday = date(2026, 4, 3)
    assert R.is_federal_business_day(good_friday) and not is_trading_day(good_friday)


def test_observed_holidays_follow_5_usc_6103():
    assert date(2021, 12, 31) in R.federal_holidays(2021)      # Jan 1 2022 is a Saturday
    assert date(2022, 1, 1) not in R.federal_holidays(2022)
    h27 = R.federal_holidays(2027)
    assert {date(2027, 6, 18), date(2027, 7, 5), date(2027, 12, 24), date(2027, 12, 31)} <= h27
    assert date(2020, 6, 19) not in R.federal_holidays(2020), "Juneteenth is federal from 2021"
    assert date(2021, 6, 18) in R.federal_holidays(2021)       # Jun 19 2021 is a Saturday
    assert len(R.federal_holidays(2026)) == 11


def test_business_day_arithmetic():
    assert R.add_federal_business_days(date(2026, 11, 16), 5) == date(2026, 11, 23)
    assert R.add_federal_business_days(date(2026, 11, 20), 5) == date(2026, 11, 30)  # Thanksgiving
    assert R.add_federal_business_days(date(2026, 11, 16), 0) == date(2026, 11, 16)
    assert R.next_federal_business_day(date(2026, 11, 14)) == date(2026, 11, 16)
    with pytest.raises(ValueError):
        R.add_federal_business_days(date(2026, 1, 1), -1)


# ── card notices ──────────────────────────────────────────────────────────────────────


def test_latest_not_in_waits_for_due_date_plus_five_business_days():
    # Q2 on file -> Q3 due 2026-11-16 -> grace ends 2026-11-23.
    assert R.next_due_date("2026-Q2") == date(2026, 11, 16)
    assert R.latest_not_in_due("2026-Q2", date(2026, 11, 16)) is False
    assert R.latest_not_in_due("2026-Q2", date(2026, 11, 23)) is False
    assert R.latest_not_in_due("2026-Q2", date(2026, 11, 24)) is True


def test_no_newer_filing_needs_two_missed_due_dates():
    # Q2 on file -> Q3 due Nov 16, Q4 due 2027-02-16 -> grace ends 2027-02-23.
    assert R.no_newer_filing("2026-Q2", date(2026, 12, 31)) is False
    assert R.no_newer_filing("2026-Q2", date(2027, 2, 23)) is False
    assert R.no_newer_filing("2026-Q2", date(2027, 2, 24)) is True


def test_notice_helpers_reject_a_malformed_period():
    with pytest.raises(ValueError):
        R.latest_not_in_due("Q2 2026", date(2026, 12, 1))
    with pytest.raises(ValueError):
        R.no_newer_filing(None, date(2026, 12, 1))


# ── CUSIP -> US ISIN ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cusip, isin", [
    ("29765A101", "US29765A1016"),     # Ethos (null symbol on Alphabet's 13F) -> LIFE
    ("84615Q103", "US84615Q1031"),     # SpaceX
    ("037833100", "US0378331005"),     # Apple — the textbook check-digit vector
    (" 29765a101 ", "US29765A1016"),   # whitespace / lower case normalised
])
def test_cusip_to_us_isin(cusip, isin):
    assert R.cusip_to_us_isin(cusip) == isin


@pytest.mark.parametrize("bad", [
    "N97284108",      # CINS (Nebius) — its real ISIN is NL0009805522
    "H1467J104",      # CINS (Chubb)
    "29765A102",      # wrong CUSIP check digit
    "29765A10",       # too short
    "29765A1011",     # too long
    "29765A10X",      # non-digit check character
    "", None, 29765101, "29765-101",
])
def test_cusip_to_us_isin_refuses_cins_and_malformed(bad):
    assert R.cusip_to_us_isin(bad) is None


def test_derived_isins_match_fmp_profiles():
    """Cross-check on real data: every US issuer in the fixtures derives FMP's own ISIN,
    and the one Canadian issuer (Xanadu) shows why search-cusip is the fallback."""
    profiles = json.loads((Path(__file__).parent / "fixtures/trillion_club/profiles.json").read_text())
    checked = 0
    for sym, p in profiles.items():
        if sym == "_meta" or not p or not p.get("cusip") or not p.get("isin"):
            continue
        derived = R.cusip_to_us_isin(p["cusip"])
        if p["isin"].startswith("US"):
            assert derived == p["isin"], sym
            checked += 1
        elif p["cusip"][0].isdigit():
            assert derived is not None and derived != p["isin"], sym     # XNDU: CA98390R1029
        else:
            assert derived is None, sym                                   # NBIS / YNDX: CINS
    assert checked >= 40


# ── accession numbers ────────────────────────────────────────────────────────────────


def test_accession_from_link_and_final_link():
    idx = ("https://www.sec.gov/Archives/edgar/data/1045810/000104581026000065/"
           "0001045810-26-000065-index.htm")
    xml = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000065/information_table.xml"
    assert R.accession_from_link(idx) == "0001045810-26-000065"
    assert R.accession_from_link(xml) == "0001045810-26-000065"
    assert R.accession_from_link("https://www.sec.gov/Archives/edgar/data/2488/000119312526352454") \
        == "0001193125-26-352454"


@pytest.mark.parametrize("bad", [None, "", 123, "https://www.sec.gov/", "0001045810-26-00006",
                                 "x/1234567890123456789/y"])
def test_accession_from_link_refuses_garbage(bad):
    assert R.accession_from_link(bad) is None


def test_membership_output_is_finite():
    st = _eval(_series(*([ABOVE] * 30)))
    assert math.isfinite(st.last_cap)


def test_a_datetime_today_is_read_as_its_date_and_a_bad_prior_is_refused():
    series = _series(*([ABOVE] * 30))
    as_dt = R.evaluate_membership(series, mode="auto", prior=None,
                                  today_et=AFTER_CLOSE, now_et=AFTER_CLOSE)
    assert as_dt == _eval(series)
    forced = R.evaluate_membership([], mode="force_in", prior=None, today_et=AFTER_CLOSE, now_et=AFTER_CLOSE)
    assert forced.member_since == LAST and type(forced.member_since) is date
    with pytest.raises(TypeError, match="MembershipState"):
        R.evaluate_membership(series, mode="auto", prior={"is_member": True},
                              today_et=LAST, now_et=AFTER_CLOSE)


# ── regressions (hardening pass, 2026-09-24) ─────────────────────────────────────────


def _with_dup(series, i, *caps):
    """``series`` plus extra rows for the date at index ``i`` (a same-date duplicate)."""
    return list(series) + [(series[i][0].isoformat(), c) for c in caps]


def test_an_old_conflicting_close_does_not_freeze_membership():
    """REGRESSION (resilience-4). Was: one same-date conflict ANYWHERE in the ~13-month
    window returned None, so the stored state was kept and never re-evaluated for as long
    as the date stayed in the fetch window — here a company 60 sessions below $1T stayed a
    member. Now only the newest date's conflict fails closed."""
    series = _series(*([ABOVE] * 200 + [BELOW] * 60))
    prior = R.MembershipState(True, date(2024, 1, 2), 200, 0, ABOVE, LAST - timedelta(days=1))
    clean = _eval(series, prior=prior)
    assert clean.is_member is False and clean.closes_below == 60
    for caps in ((ABOVE * 1.001,), (BELOW,), (BELOW, ABOVE * 1.2)):   # same side / straddling
        assert _eval(_with_dup(series, 60, *caps), prior=prior) == clean, caps


def test_a_same_side_conflict_changes_nothing_and_is_logged(caplog):
    series = _series(*([BELOW] * 30 + [ABOVE] * 5 + [ABOVE] + [ABOVE] * 5))
    with caplog.at_level(logging.WARNING):
        st = _eval(_with_dup(series, 35, ABOVE * 1.3))
    assert st == _eval(series) and st.is_member is True and st.closes_at_or_above == 11
    assert "two different caps" in caplog.text and "same side" in caplog.text


def test_a_straddling_conflict_breaks_a_joining_streak_never_completes_it(caplog):
    """5 above + a close known only to straddle $1T + 5 above: the unknown close could
    have been below, so it must not be bridged into a 10-close join."""
    series = _series(*([BELOW] * 30 + [ABOVE] * 5 + [ABOVE] + [ABOVE] * 5))
    with caplog.at_level(logging.WARNING):
        st = _eval(_with_dup(series, 35, BELOW))
    assert st.is_member is False and st.closes_at_or_above == 5 and st.closes_below == 0
    assert "straddle" in caplog.text
    # the straddling row is order-independent (FMP is newest first)
    assert _eval(list(reversed(_with_dup(series, 35, BELOW)))) == st


def test_a_straddling_conflict_breaks_a_leaving_streak_never_completes_it():
    series = _series(*([ABOVE] * 30 + [BELOW] * 10 + [BELOW] + [BELOW] * 10))
    assert _eval(series).is_member is False, "control: 21 straight closes below leave"
    st = _eval(_with_dup(series, 40, ABOVE))
    assert st.is_member is True and st.closes_below == 10


def test_a_conflict_on_the_newest_close_still_fails_closed_either_way():
    series = _series(*([ABOVE] * 30))
    assert _eval(_with_dup(series, -1, ABOVE * 1.01)) is None      # same side
    assert _eval(_with_dup(series, -1, BELOW)) is None             # straddling


def test_a_straddling_close_does_not_count_toward_the_minimum_rows():
    series = _series(*([ABOVE] * R.MIN_ROWS))
    assert _eval(series) is not None
    assert _eval(_with_dup(series, 3, BELOW)) is None, "19 known closes < MIN_ROWS"


def test_a_member_keeps_its_stored_date_across_a_short_dip_but_not_across_an_exit():
    """REGRESSION (member_since window slide). The window opens in a 19-close dip: the
    replay (which starts from 'not a member') joins after it, but nothing in the window
    is an exit, so the stored, older date stands. A 20-close dip IS an exit."""
    prior = R.MembershipState(True, date(2020, 1, 2), 240, 0, ABOVE, LAST - timedelta(days=1))
    dip = _series(*([ABOVE] * 9 + [BELOW] * 19 + [ABOVE] * 232))
    kept = _eval(dip, prior=prior)
    assert kept.is_member is True and kept.member_since == date(2020, 1, 2)
    assert _eval(dip).member_since == dip[28 + R.JOIN_CLOSES - 1][0], "no prior: the window's date"
    exited = _series(*([ABOVE] * 9 + [BELOW] * 20 + [ABOVE] * 231))
    assert _eval(exited, prior=prior).member_since == exited[29 + R.JOIN_CLOSES - 1][0]
    # A straddling close inside the dip splits it: still no exit, the stored date stands.
    split = _with_dup(_series(*([ABOVE] * 9 + [BELOW] * 25 + [ABOVE] * 226)), 21, ABOVE)
    assert _eval(split, prior=prior).member_since == date(2020, 1, 2)


def _easter(year: int) -> date:
    """Anonymous Gregorian computus — independent of app.utils.market_hours."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(year, month, (h + l - 7 * m + 114) % 31 + 1)


def test_nyse_tables_cover_at_least_12_months_ahead__extend_market_hours_when_this_fails():
    """Uses the REAL clock on purpose: an early-warning timer, not a unit test.

    The membership rule drops rows dated after ``last_completed_close``, which reads the
    hand-kept NYSE tables in app/utils/market_hours.py. Past their last year every NYSE
    holiday reads as a session that closed at 16:00, so a row FMP stamps on a shut day
    could complete a join. This fails a year before that happens: add the next year's
    NYSE holidays AND early closes from nyse.com/markets/hours-calendars (the rule-based
    cross-check in test_theme_rotation_scheduler.py then verifies them).
    """
    from app.utils import market_hours as MH
    today = date.today()
    needed = range(today.year, (today + timedelta(days=365)).year + 1)
    problems = []
    for y in needed:
        holidays = {date(*t) for t in MH.US_MARKET_HOLIDAYS if t[0] == y}
        early = {date(*t) for t in MH.US_MARKET_EARLY_CLOSES if t[0] == y}
        good_friday = _easter(y) - timedelta(days=2)
        day_after_thanksgiving = date(y, 11, 1) + timedelta(days=(3 - date(y, 11, 1).weekday()) % 7 + 22)
        if len(holidays) < 9:
            problems.append(f"{y}: {len(holidays)} holidays listed (NYSE closes 9-10 days a year)")
        if good_friday not in holidays:
            problems.append(f"{y}: Good Friday {good_friday} missing (NYSE-only, not federal)")
        if day_after_thanksgiving not in early:
            problems.append(f"{y}: the {day_after_thanksgiving} half-day is missing")
        # and the membership cutoff really sees it: Good Friday evening is not a new close
        at = datetime(good_friday.year, good_friday.month, good_friday.day, 17, 0, tzinfo=ET)
        if MH.last_completed_close(at).astimezone(ET).date() == good_friday:
            problems.append(f"{y}: last_completed_close treats Good Friday as a session")
    assert not problems, (
        "app/utils/market_hours.py does not cover the next 12 months: " + "; ".join(problems)
    )
