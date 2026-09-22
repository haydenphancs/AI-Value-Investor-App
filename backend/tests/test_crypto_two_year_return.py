"""The crypto Performance card's "2 Years" row (TestFlight, build 1.0 (9), BNB: *"We have 2
year for bitcoin right? Add 2 year also"*).

Why a separate helper exists: `_compute_return(prices, 730)` is POSITIONAL — it needs
`len(prices) > 730` and reads `prices[-731]`. CoinGecko's `days=730` daily series, bucketed
by ET date, is 731 rows only while the trailing live point sits on its own ET date; between
20:00 and 24:00 ET it collapses into the midnight print (730 rows), and any single missing day
does the same. A positional "2 Years" would be on the card for part of the day and off it for
the rest. `_compute_return_by_date` anchors on the calendar instead, and refuses to relabel a
younger coin's since-inception return as "2 Years".

Everything here is pure: rows are built in the test, the builder is exercised on an
`__init__`-bypassed instance, and the call-site pins are AST scans of `get_crypto_detail`.
"""

from __future__ import annotations

import ast
import inspect
import json
import math
import textwrap
from datetime import date, timedelta

import pytest

from app.schemas.crypto import PerformancePeriodResponse
from app.services import crypto_service as cs
from app.services.crypto_service import (
    CryptoService,
    _compute_return,
    _compute_return_by_date,
    _row_date,
)

END = date(2026, 9, 21)


def _daily(n: int, *, end: date = END, start_close: float = 100.0, end_close: float = 150.0,
           skip: set[date] | None = None) -> list[dict]:
    """`n` consecutive daily rows ending on `end`, oldest first, closes ramping linearly from
    `start_close` to `end_close`; `skip` removes whole days (a gap, not a null)."""
    rows = []
    for i in range(n):
        d = end - timedelta(days=n - 1 - i)
        if skip and d in skip:
            continue
        frac = i / (n - 1) if n > 1 else 1.0
        rows.append({"date": d.isoformat(), "close": start_close + (end_close - start_close) * frac})
    return rows


def _pct(start: float, end: float) -> float:
    return (end - start) / start * 100


# ── _row_date ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"date": "2026-09-21"}, date(2026, 9, 21)),
        ({"date": "2026-09-21 16:00:00"}, date(2026, 9, 21)),     # FMP shape
        ({"date": "2026-09-21T20:00:00+00:00"}, date(2026, 9, 21)),
        ({"date": None}, None),
        ({"date": ""}, None),
        ({"date": "garbage"}, None),
        ({"date": "2026/09/21"}, None),
        ({"date": "2026-13-45"}, None),
        ({"date": 20260921}, None),
        ({}, None),
        (None, None),
        ("2026-09-21", None),
    ],
)
def test_row_date_reads_the_first_ten_characters_or_nothing(row, expected):
    assert _row_date(row) == expected


# ── _compute_return_by_date: the row-count boundary that broke positional maths ──


def test_exactly_731_rows_two_years_is_first_to_last():
    rows = _daily(731)
    assert rows[0]["date"] == (END - timedelta(days=730)).isoformat()
    got = _compute_return_by_date(rows, 730)
    assert got == pytest.approx(_pct(100.0, 150.0))
    # The positional helper agrees here — this is the one row count where it works at all.
    assert _compute_return(rows, 730) == pytest.approx(got)


def test_730_rows_the_collapsed_live_point_still_yields_the_row():
    """20:00–24:00 ET the live point shares the last midnight print's ET date → 730 rows."""
    rows = _daily(730)
    assert _compute_return(rows, 730) is None, "positional maths drops the tile at 730 rows"
    got = _compute_return_by_date(rows, 730)
    assert got is not None
    # The first row is one day AFTER the target — inside the 7-day tolerance.
    assert got == pytest.approx(_pct(rows[0]["close"], rows[-1]["close"]))


def test_one_missing_day_in_the_middle_does_not_drop_the_row():
    gap = {END - timedelta(days=400)}
    rows = _daily(731, skip=gap)
    assert len(rows) == 730
    assert _compute_return(rows, 730) is None
    assert _compute_return_by_date(rows, 730) == pytest.approx(_pct(100.0, 150.0))


def test_a_gap_on_the_target_date_moves_forward_never_back():
    target = END - timedelta(days=730)
    rows = _daily(740, skip={target})          # series starts BEFORE the target, target missing
    got = _compute_return_by_date(rows, 730)
    expected_start = next(r for r in rows if r["date"] == (target + timedelta(days=1)).isoformat())
    assert got == pytest.approx(_pct(expected_start["close"], rows[-1]["close"]))


def test_rows_before_the_target_are_ignored_not_used():
    """A 3-year series must still measure the LAST two years."""
    rows = _daily(365 * 3 + 1)
    target = END - timedelta(days=730)
    start = next(r for r in rows if r["date"] == target.isoformat())
    assert _compute_return_by_date(rows, 730) == pytest.approx(_pct(start["close"], rows[-1]["close"]))


# ── a coin younger than the window is omitted, never mislabelled ─────────────


def test_a_young_coin_gets_no_two_year_row():
    rows = _daily(400)                          # 400 days of history
    assert _compute_return_by_date(rows, 730) is None


@pytest.mark.parametrize("late_by,shown", [(0, True), (7, True), (8, False), (30, False)])
def test_the_reach_tolerance_is_seven_days(late_by, shown):
    n = 731 - late_by                           # first row is `late_by` days after the target
    rows = _daily(n)
    assert (_compute_return_by_date(rows, 730) is not None) is shown


def test_the_tolerance_is_a_parameter():
    rows = _daily(731 - 10)
    assert _compute_return_by_date(rows, 730) is None
    assert _compute_return_by_date(rows, 730, max_gap_days=10) is not None


# ── degraded inputs → None, never a wrong number, never an exception ──────────


def test_fewer_than_two_rows():
    assert _compute_return_by_date([], 730) is None
    assert _compute_return_by_date([{"date": END.isoformat(), "close": 1.0}], 730) is None


def test_two_rows_spanning_the_window_is_enough():
    rows = [
        {"date": (END - timedelta(days=730)).isoformat(), "close": 50.0},
        {"date": END.isoformat(), "close": 100.0},
    ]
    assert _compute_return_by_date(rows, 730) == pytest.approx(100.0)


def test_non_positive_window_is_refused():
    rows = _daily(731)
    assert _compute_return_by_date(rows, 0) is None
    assert _compute_return_by_date(rows, -5) is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 0, 0.0, None, "x"])
def test_a_bad_start_close_degrades_to_none(bad):
    rows = _daily(731)
    rows[0]["close"] = bad
    assert _compute_return_by_date(rows, 730) is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, 0, None])
def test_a_bad_end_close_degrades_to_none(bad):
    rows = _daily(731)
    rows[-1]["close"] = bad
    assert _compute_return_by_date(rows, 730) is None


def test_adj_close_is_the_fallback_field():
    rows = _daily(731)
    rows[0] = {"date": rows[0]["date"], "adjClose": 100.0}
    rows[-1] = {"date": rows[-1]["date"], "close": None, "adjClose": 300.0}
    assert _compute_return_by_date(rows, 730) == pytest.approx(200.0)


def test_an_unreadable_end_date_is_none():
    rows = _daily(731)
    rows[-1]["date"] = None
    assert _compute_return_by_date(rows, 730) is None


def test_unreadable_dates_inside_the_series_are_skipped():
    rows = _daily(731)
    rows[0]["date"] = "garbage"                 # the would-be start; the next row is 1 day late
    rows[5]["date"] = None
    got = _compute_return_by_date(rows, 730)
    assert got == pytest.approx(_pct(rows[1]["close"], rows[-1]["close"]))


def test_non_dict_rows_do_not_raise():
    rows = _daily(731)
    rows[3] = None
    rows[4] = "2026-01-01"
    assert _compute_return_by_date(rows, 730) == pytest.approx(_pct(100.0, 150.0))


def test_the_result_is_finite():
    rows = _daily(731, start_close=1e-9, end_close=1e9)
    got = _compute_return_by_date(rows, 730)
    assert got is not None and math.isfinite(got)


def test_a_row_dated_after_the_end_row_is_ignored():
    """Defensive: the scan takes the last row as `end`; a row dated AFTER it is never a start,
    even when it is the only candidate inside the reach tolerance. A short window on purpose —
    with the 730-day one, the tolerance check alone would refuse the case and prove nothing."""
    rows = [
        {"date": (END - timedelta(days=800)).isoformat(), "close": 100.0},   # before the target
        {"date": (END + timedelta(days=2)).isoformat(), "close": 999.0},     # 7 days past target
        {"date": END.isoformat(), "close": 150.0},
    ]
    assert _compute_return_by_date(rows, 5) is None
    # The same shape with the stray row inside the window instead is a normal start.
    rows[1]["date"] = (END - timedelta(days=4)).isoformat()
    assert _compute_return_by_date(rows, 5) == pytest.approx(_pct(999.0, 150.0))


def test_descending_input_degrades_rather_than_lies():
    """Every caller sorts oldest-first. Reversed, the LAST row is the oldest, so no row can
    be on/after target and before end — the scan finds nothing and the row is omitted."""
    rows = list(reversed(_daily(731)))
    assert _compute_return_by_date(rows, 730) is None


def test_a_trading_day_series_reaches_two_years():
    """SPY (the BTC screen's benchmark): ~5 rows a week over 730 calendar days ≈ 504 rows.
    Positional 252*2 = 504 is a coin flip against the holiday count; the calendar anchor is not."""
    weekdays = [d for d in (END - timedelta(days=i) for i in range(731)) if d.weekday() < 5]
    weekdays.sort()
    rows = [{"date": d.isoformat(), "close": 100.0 + i} for i, d in enumerate(weekdays)]
    assert 495 <= len(rows) <= 525
    assert _compute_return_by_date(rows, 730) == pytest.approx(_pct(rows[0]["close"], rows[-1]["close"]))


# ── the builder: placement, omission, benchmark ──────────────────────────────


def _periods(svc, **overrides):
    kwargs = dict(
        one_month=2.0, ytd=3.0, one_year=4.0,
        three_year=None, five_year=None, ten_year=None, all_time=None,
        bench_1m=None, bench_ytd=None, bench_1y=None,
        bench_3y=None, bench_5y=None, bench_10y=None, bench_all_time=None,
        benchmark_label="BTC",
    )
    kwargs.update(overrides)
    return svc._build_performance_periods(**kwargs)


def test_two_years_sits_after_one_year_and_before_the_long_horizons():
    svc = object.__new__(CryptoService)
    labels = [p.label for p in _periods(svc, two_year=41.02, three_year=90.0)]
    assert labels == ["1 Month", "YTD", "1 Year", "2 Years", "3 Years"]


def test_two_years_none_is_an_absent_row_not_a_null_or_zero():
    svc = object.__new__(CryptoService)
    labels = [p.label for p in _periods(svc, two_year=None)]
    assert "2 Years" not in labels
    assert labels == ["1 Month", "YTD", "1 Year"]


def test_two_years_carries_its_benchmark_and_the_delta():
    svc = object.__new__(CryptoService)
    row = next(p for p in _periods(svc, two_year=41.024, bench_2y=88.456) if p.label == "2 Years")
    assert row.change_percent == 41.02
    assert row.sp_return_percent == 88.46
    assert row.vs_market_percent == pytest.approx(41.02 - 88.46)
    assert row.benchmark_label == "BTC"


def test_two_years_without_a_benchmark_leaves_both_benchmark_fields_null():
    svc = object.__new__(CryptoService)
    row = next(p for p in _periods(svc, two_year=41.02, bench_2y=None) if p.label == "2 Years")
    assert row.sp_return_percent is None and row.vs_market_percent is None


def test_the_new_keywords_default_so_older_callers_are_untouched():
    svc = object.__new__(CryptoService)
    labels = [p.label for p in _periods(svc)]
    assert labels == ["1 Month", "YTD", "1 Year"]


def test_a_two_year_row_serialises_under_allow_nan_false():
    r = PerformancePeriodResponse(label="2 Years", change_percent=41.02,
                                  sp_return_percent=None, benchmark_label="S&P 500")
    json.dumps(r.model_dump(), allow_nan=False)


# ── call-site pins (AST over get_crypto_detail) ──────────────────────────────


def _detail_assignments():
    src = textwrap.dedent(inspect.getsource(cs.CryptoService.get_crypto_detail))
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, []).append(ast.unparse(node.value))
    return out


def test_the_coin_row_is_date_anchored_and_cap_gated():
    values = _detail_assignments().get("two_year_return")
    assert values, "two_year_return is no longer computed in get_crypto_detail"
    for v in values:
        assert "_compute_return_by_date(historical, 365 * 2, max_gap_days=3)" in v, (
            "the daily coin leg must keep the 3-day reach tolerance: a coin listed 723-729 days "
            "ago must not show since-inception as 2 Years")
        assert "_history_days_cap()" in v, "the 2Y row must follow the plan cap like every horizon"
        assert "_compute_return(historical" not in v, "positional maths is the 731-row trap"


def test_both_benchmark_legs_are_date_anchored():
    values = _detail_assignments().get("bench_2y")
    assert values, "bench_2y is no longer assigned in get_crypto_detail"
    sources = {("spy_hist" if "spy_hist" in v else "btc_hist" if "btc_hist" in v else "?") for v in values
               if "_compute_return_by_date" in v}
    assert sources == {"spy_hist", "btc_hist"}, f"expected a BTC leg and an S&P leg, got {sources}"
    for v in values:
        assert "_compute_return(" not in v.replace("_compute_return_by_date(", "")
        if "btc_hist" in v:
            assert "max_gap_days=3" in v, "the daily BTC leg keeps the 3-day tolerance"
        if "spy_hist" in v:
            assert "max_gap_days" not in v, "the trading-day S&P leg keeps the 7-day default"


def test_the_builder_receives_both_two_year_values():
    src = textwrap.dedent(inspect.getsource(cs.CryptoService.get_crypto_detail))
    call = next(
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("_build_performance_periods")
    )
    keywords = {k.arg: ast.unparse(k.value) for k in call.keywords}
    assert keywords.get("two_year") == "two_year_return"
    assert keywords.get("bench_2y") == "bench_2y"
