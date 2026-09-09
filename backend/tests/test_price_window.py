"""Math guards for `app/services/price_window` — the trailing-window % change.

Phase 4 routes six macro risk factors, the index/commodity proxies and the sector 1Y
figure through this one function, so its edge cases are not academic. Each test below
pins a mistake one of the three ad-hoc precedents actually made.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.price_window import (
    latest_value,
    normalize_history,
    series_from_observations,
    series_from_rows,
    window_change_pct,
)

_D0 = date(2026, 9, 4)


def _rows(values, *, start=None, step=1, key="close"):
    """Daily rows, OLDEST-first, ending at _D0."""
    n = len(values)
    base = start or (_D0 - timedelta(days=(n - 1) * step))
    return [
        {"date": (base + timedelta(days=i * step)).isoformat(), key: v}
        for i, v in enumerate(values)
    ]


# ── normalize_history: the three duplicated copies it replaces ───────────────

@pytest.mark.parametrize("raw,expected", [
    ([{"date": "2026-09-04", "close": 1.0}], 1),
    ({"historical": [{"date": "2026-09-04", "close": 1.0}]}, 1),
    ({"historical": None}, 0),
    ({}, 0), ([], 0), (None, 0), ("nonsense", 0), (42, 0),
    ([{"date": "x"}, "junk", None, 7], 1),   # non-dict rows dropped
])
def test_normalize_history_survives_every_shape(raw, expected):
    assert len(normalize_history(raw)) == expected


# ── Order independence — the bug in fmp.py's inline 1Y calc ──────────────────

def test_the_same_data_in_either_order_yields_the_same_answer():
    """FMP's RAW response is newest-first; `_fetch_all_daily` sorts oldest-first. The
    same endpoint therefore reaches different callers in opposite orders."""
    rows = _rows([100.0, 101.0, 102.0, 110.0])
    assert series_from_rows(rows) == series_from_rows(list(reversed(rows)))
    assert window_change_pct(series_from_rows(list(reversed(rows))), 3) == pytest.approx(10.0)


def test_it_reads_the_newest_value_not_the_first_row():
    """`hist_list[0].get("close")` as the "1Y-ago price" is the live bug in fmp.py:979."""
    newest_first = [
        {"date": "2026-09-04", "close": 110.0},
        {"date": "2026-09-01", "close": 100.0},
    ]
    s = series_from_rows(newest_first)
    assert latest_value(s) == 110.0
    assert window_change_pct(s, 3) == pytest.approx(10.0)


# ── Refuse rather than shrink ────────────────────────────────────────────────

def test_a_short_series_returns_none_rather_than_a_mislabelled_window():
    s = series_from_rows(_rows([100.0, 110.0]))     # 2 days of history
    assert window_change_pct(s, 365) is None, "a 1-day change was reported as 1Y"
    assert window_change_pct(s, 1) == pytest.approx(10.0)


def test_a_gap_wider_than_the_tolerance_refuses():
    """A delisted proxy or a stalled series must not silently measure a longer window."""
    s = series_from_rows([
        {"date": "2025-01-02", "close": 100.0},
        {"date": "2026-09-04", "close": 200.0},
    ])
    assert window_change_pct(s, 90) is None
    # ...but a normal long weekend is inside the tolerance.
    ok = series_from_rows([
        {"date": "2026-06-04", "close": 100.0},
        {"date": "2026-09-04", "close": 110.0},
    ])
    assert window_change_pct(ok, 90) == pytest.approx(10.0)


@pytest.mark.parametrize("days", [0, -1, -365])
def test_a_nonpositive_window_is_refused(days):
    assert window_change_pct(series_from_rows(_rows([1.0, 2.0, 3.0])), days) is None


@pytest.mark.parametrize("series", [[], [(date(2026, 9, 4), 100.0)]])
def test_empty_and_single_point_series_return_none(series):
    assert window_change_pct(series, 30) is None


# ── Outliers: the values that survive `or 0` ─────────────────────────────────

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, "x", 0, -5.0])
def test_unusable_values_are_dropped_not_propagated(bad):
    """NaN is TRUTHY: it sails through `or 0` and serializes as an invalid JSON token
    that fails the iOS decode of the whole screen."""
    s = series_from_rows([
        {"date": "2026-06-04", "close": 100.0},
        {"date": "2026-09-03", "close": bad},
        {"date": "2026-09-04", "close": 110.0},
    ])
    assert all(isinstance(v, float) and v > 0 for _, v in s)
    assert window_change_pct(s, 90) == pytest.approx(10.0)


def test_a_series_that_is_entirely_junk_returns_none():
    s = series_from_rows([{"date": "2026-09-04", "close": float("nan")},
                          {"date": "2026-09-03", "close": 0}])
    assert s == [] and window_change_pct(s, 30) is None and latest_value(s) is None


@pytest.mark.parametrize("bad_date", [None, "", "not-a-date", "2026-13-45", 20260904, "2026-09"])
def test_unparseable_dates_are_dropped(bad_date):
    s = series_from_rows([
        {"date": "2026-06-04", "close": 100.0},
        {"date": bad_date, "close": 999.0},
        {"date": "2026-09-04", "close": 110.0},
    ])
    assert len(s) == 2 and window_change_pct(s, 90) == pytest.approx(10.0)


def test_duplicate_dates_collapse_instead_of_double_counting():
    s = series_from_rows([
        {"date": "2026-09-04", "close": 100.0},
        {"date": "2026-09-04", "close": 110.0},
        {"date": "2026-06-04", "close": 100.0},
    ])
    assert len(s) == 2 and latest_value(s) == 110.0


def test_a_datetime_stamped_row_is_accepted():
    """FMP intraday rows carry `YYYY-MM-DD HH:MM:SS`."""
    s = series_from_rows([
        {"date": "2026-06-04 16:00:00", "close": 100.0},
        {"date": "2026-09-04 16:00:00", "close": 110.0},
    ])
    assert len(s) == 2 and window_change_pct(s, 90) == pytest.approx(10.0)


def test_adjclose_is_the_fallback_price_key():
    s = series_from_rows([
        {"date": "2026-06-04", "adjClose": 100.0},
        {"date": "2026-09-04", "adjClose": 110.0},
    ])
    assert window_change_pct(s, 90) == pytest.approx(10.0)


# ── FRED observations ────────────────────────────────────────────────────────

class _Obs:
    def __init__(self, d, v): self.date, self.value = d, v


def test_fred_observations_work_from_objects_or_dicts():
    """`get_observations` returns FREDObservation newest-first."""
    objs = [_Obs("2026-09-04", 110.0), _Obs("2026-06-04", 100.0)]
    dicts = [{"date": "2026-09-04", "value": 110.0}, {"date": "2026-06-04", "value": 100.0}]
    assert series_from_observations(objs) == series_from_observations(dicts)
    assert window_change_pct(series_from_observations(objs), 90) == pytest.approx(10.0)


def test_a_holiday_hole_does_not_shift_the_window():
    """FRED prints "." on holidays and `get_observations` drops those rows, so the
    Nth observation back is NOT N calendar days back. `get_snapshot`'s obs[6]/obs[12]
    indexing is exactly this trap — bisect on the date instead."""
    obs = [{"date": (_D0 - timedelta(days=i)).isoformat(), "value": 100.0 + i}
           for i in range(0, 200) if i % 7 not in (5, 6)]   # weekends missing
    s = series_from_observations(obs)

    # The anchor is the last observation AT OR BEFORE 90 calendar days back. Day 90 is
    # itself a hole here (90 % 7 == 6), so the correct anchor is day 91 — derived, not
    # hard-coded, because hard-coding it is how you write a test that agrees with a
    # broken implementation.
    anchor_i = next(i for i in range(90, 200) if i % 7 not in (5, 6))
    assert anchor_i == 91
    expected = (100.0 / (100.0 + anchor_i) - 1) * 100
    assert window_change_pct(s, 90) == pytest.approx(expected, abs=1e-3)

    # And an index-based reading (what `get_snapshot`'s obs[N] does) would land somewhere
    # else entirely — that difference is the trap this function exists to avoid.
    by_index = s[-1][1] / s[-91][1] - 1
    assert abs(by_index * 100 - expected) > 1.0


def test_none_is_distinguishable_from_a_genuinely_flat_market():
    flat = series_from_rows([{"date": "2026-06-04", "close": 100.0},
                             {"date": "2026-09-04", "close": 100.0}])
    assert window_change_pct(flat, 90) == 0.0
    assert window_change_pct([], 90) is None
