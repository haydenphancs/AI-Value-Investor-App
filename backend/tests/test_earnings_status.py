"""Per-ticker earnings STATUS for the Insights prompt and gate — pure derivation.

TestFlight, ORCL, Thu 2026-09-10: the card said "Oracle is set to report Q1
earnings" hours after the 16:10 ET release; the prompt had no earnings status at
all. The status is now derived from FMP's calendar rows, which carry no time of day
— a finite `epsActual`/`revenueActual` is the only "has reported" signal. These
tests pin the precedence and every row shape that could make it lie.
"""

from datetime import date, datetime, timezone

import pytest

from app.services.earnings_window_service import (
    EARNINGS_DUE_TODAY,
    EARNINGS_REPORTED,
    EARNINGS_UPCOMING,
    EarningsStatus,
    earnings_gate_inputs,
    earnings_is_hot,
    earnings_status,
    row_reported,
    statuses_by_symbol,
)

TODAY = date(2026, 9, 10)   # Thursday


def _row(d, eps=None, rev=None, symbol="ORCL"):
    return {
        "symbol": symbol, "date": d, "epsActual": eps, "revenueActual": rev,
        "epsEstimated": 1.74, "revenueEstimated": 19134970000,
    }


# ── row_reported ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("eps,rev,expected", [
    (1.92, None, True),
    (None, 19345000000, True),
    (0.0, None, True),                 # a breakeven quarter IS a report
    (-0.12, None, True),               # so is a loss
    ("1.92", None, True),              # numeric strings coerce
    (None, None, False),
    ("", None, False),
    (float("nan"), None, False),
    (float("inf"), None, False),
    ("NaN", None, False),
    (True, None, False),               # a bool is not a figure
])
def test_row_reported(eps, rev, expected):
    assert row_reported({"epsActual": eps, "revenueActual": rev}) is expected


@pytest.mark.parametrize("bad", [None, "ORCL", 42, []])
def test_row_reported_rejects_non_dicts(bad):
    assert row_reported(bad) is False


# ── earnings_status precedence ────────────────────────────────────────────────

def test_reported_today():
    st = earnings_status([_row("2026-09-10", eps=1.92)], TODAY)
    assert st == EarningsStatus(EARNINGS_REPORTED, TODAY)


def test_due_today_without_actuals():
    st = earnings_status([_row("2026-09-10")], TODAY)
    assert st == EarningsStatus(EARNINGS_DUE_TODAY, TODAY)


def test_reported_four_days_ago_is_in_the_window_five_is_not():
    assert earnings_status([_row("2026-09-06", eps=1.0)], TODAY).status == EARNINGS_REPORTED
    assert earnings_status([_row("2026-09-05", eps=1.0)], TODAY) is None


def test_upcoming_is_the_nearest_pending_date_within_three_days():
    st = earnings_status([_row("2026-09-13"), _row("2026-09-11")], TODAY)
    assert st == EarningsStatus(EARNINGS_UPCOMING, date(2026, 9, 11))
    assert earnings_status([_row("2026-09-14")], TODAY) is None


def test_a_past_date_without_actuals_says_nothing():
    # FMP lag, or a stale estimate after a reschedule — never "the report is late".
    assert earnings_status([_row("2026-09-09")], TODAY) is None


def test_a_reschedule_duplicate_prefers_the_future_date_over_the_stale_one():
    # FMP can carry two rows across a reschedule (earnings_sender.py). The stale
    # past estimate must not win.
    st = earnings_status([_row("2026-09-08"), _row("2026-09-12")], TODAY)
    assert st == EarningsStatus(EARNINGS_UPCOMING, date(2026, 9, 12))


def test_same_date_duplicates_one_reported_is_reported():
    st = earnings_status([_row("2026-09-10"), _row("2026-09-10", eps=1.92)], TODAY)
    assert st.status == EARNINGS_REPORTED


def test_reported_beats_a_pending_row_today():
    st = earnings_status([_row("2026-09-08", eps=1.0), _row("2026-09-10")], TODAY)
    assert st == EarningsStatus(EARNINGS_REPORTED, date(2026, 9, 8))


def test_several_reported_rows_choose_the_latest():
    st = earnings_status([_row("2026-09-07", eps=1.0), _row("2026-09-09", eps=1.0)], TODAY)
    assert st.date == date(2026, 9, 9)


def test_future_dated_actuals_make_the_symbol_unknown():
    # Falling through to "upcoming" would call a completed report still ahead —
    # the ORCL error itself.
    assert earnings_status([_row("2026-09-11", eps=1.92)], TODAY) is None
    assert earnings_status(
        [_row("2026-09-11", eps=1.92), _row("2026-09-12")], TODAY,
    ) is None


def test_known_reported_is_sticky_against_a_response_that_drops_the_actuals():
    st = earnings_status(
        [_row("2026-09-10")], TODAY, known_reported={TODAY},
    )
    assert st.status == EARNINGS_REPORTED


def test_nan_actuals_are_pending():
    st = earnings_status([_row("2026-09-10", eps=float("nan"))], TODAY)
    assert st.status == EARNINGS_DUE_TODAY


@pytest.mark.parametrize("bad", [
    None, "x", 3, {"symbol": "ORCL"}, {"symbol": "ORCL", "date": "garbage"},
    {"symbol": "ORCL", "date": "2026/09/10"},
])
def test_malformed_rows_are_skipped(bad):
    assert earnings_status([bad], TODAY) is None
    assert earnings_status([bad, _row("2026-09-10", eps=1.0)], TODAY).status == EARNINGS_REPORTED


def test_a_timestamp_date_is_read_by_its_day():
    st = earnings_status([_row("2026-09-10 16:05:00", eps=1.0)], TODAY)
    assert st.date == TODAY


def test_weekend_today_still_derives():
    saturday = date(2026, 9, 12)
    st = earnings_status([_row("2026-09-10", eps=1.0)], saturday)
    assert st == EarningsStatus(EARNINGS_REPORTED, TODAY)


# ── statuses_by_symbol ────────────────────────────────────────────────────────

def test_statuses_are_grouped_per_symbol_without_leaking():
    rows = [
        _row("2026-09-10", eps=1.9, symbol=" orcl "),
        _row("2026-09-11", symbol="ADBE"),
        _row("2026-09-09", symbol="LATE"),       # past + pending → nothing
        {"symbol": "", "date": "2026-09-10"},
    ]
    out = statuses_by_symbol(rows, TODAY)
    assert out == {
        "ORCL": EarningsStatus(EARNINGS_REPORTED, TODAY),
        "ADBE": EarningsStatus(EARNINGS_UPCOMING, date(2026, 9, 11)),
    }


def test_statuses_attach_the_seen_at_only_for_the_reported_date():
    seen = datetime(2026, 9, 10, 20, 30, tzinfo=timezone.utc)
    out = statuses_by_symbol(
        [_row("2026-09-10", eps=1.9)], TODAY,
        reported_keys={("ORCL", TODAY)},
        reported_seen_at={("ORCL", TODAY): seen, ("ORCL", date(2026, 6, 1)): seen},
    )
    assert out["ORCL"].reported_seen_at == seen


def test_reported_keys_are_per_symbol():
    out = statuses_by_symbol(
        [_row("2026-09-10", symbol="ORCL"), _row("2026-09-10", symbol="ADBE")],
        TODAY, reported_keys={("ORCL", TODAY)},
    )
    assert out["ORCL"].status == EARNINGS_REPORTED
    assert out["ADBE"].status == EARNINGS_DUE_TODAY


# ── gate inputs + admission heat ──────────────────────────────────────────────

def test_gate_inputs_table():
    seen = datetime(2026, 9, 10, 20, 30, tzinfo=timezone.utc)
    assert earnings_gate_inputs(None, TODAY) == (False, False, None)
    assert earnings_gate_inputs(EarningsStatus(EARNINGS_DUE_TODAY, TODAY), TODAY) == (
        True, True, None,
    )
    assert earnings_gate_inputs(
        EarningsStatus(EARNINGS_REPORTED, TODAY, seen), TODAY,
    ) == (True, False, seen)
    # Reported YESTERDAY: not a report day, but the trigger still carries.
    assert earnings_gate_inputs(
        EarningsStatus(EARNINGS_REPORTED, date(2026, 9, 9), seen), TODAY,
    ) == (False, False, seen)
    assert earnings_gate_inputs(
        EarningsStatus(EARNINGS_UPCOMING, date(2026, 9, 11)), TODAY,
    ) == (False, False, None)


def test_hot_scopes():
    monday = date(2026, 9, 14)
    assert earnings_is_hot(EarningsStatus(EARNINGS_DUE_TODAY, TODAY), TODAY)
    assert earnings_is_hot(EarningsStatus(EARNINGS_REPORTED, TODAY), TODAY)
    assert earnings_is_hot(EarningsStatus(EARNINGS_REPORTED, date(2026, 9, 9)), TODAY)
    # Monday: the previous trading day is Friday.
    assert earnings_is_hot(EarningsStatus(EARNINGS_REPORTED, date(2026, 9, 11)), monday)
    assert not earnings_is_hot(EarningsStatus(EARNINGS_REPORTED, date(2026, 9, 8)), TODAY)
    assert not earnings_is_hot(EarningsStatus(EARNINGS_UPCOMING, date(2026, 9, 11)), TODAY)
    assert not earnings_is_hot(None, TODAY)
