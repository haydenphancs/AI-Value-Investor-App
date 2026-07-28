"""
Outlier / adversarial tests for the SHARED chart pipeline (chart_helper.py) and
the stock overview chart extractor.

These pin the hardening added after an adversarial review of the TickerDetail
data flow. The chart dicts feed `chart_data: List[Dict[str, Any]]` (stock/etf)
and `close: float` (commodity/index) with NO Pydantic guard, so a bad row here
becomes either a backend 500 or an iOS decode crash of the WHOLE response.

Regressions guarded:
  * a row with date=None (explicit null) crashed `_filter_regular_hours`
    (`len(None)`) and violated required `date: str` schemas.
  * a non-finite close/open/high/low (NaN/Inf) serialized as an invalid JSON
    `NaN`/`Infinity` token and crashed the iOS JSONDecoder.
  * a non-numeric close raised inside the loop and 500'd the endpoint.
"""

import math

from app.services import chart_helper
from app.services.stock_overview_service import _extract_chart_data


# ── _finite_or_none ──────────────────────────────────────────────

def test_finite_or_none_rejects_non_finite_and_garbage():
    f = chart_helper._finite_or_none
    assert f(None) is None
    assert f(float("nan")) is None
    assert f(float("inf")) is None
    assert f(float("-inf")) is None
    assert f("abc") is None
    assert f("1.5") == 1.5          # numeric strings still coerce
    assert f(2) == 2.0
    assert f(0) == 0.0              # zero is finite (caller decides positivity)


# ── _normalize_prices ────────────────────────────────────────────

def test_normalize_prices_drops_none_and_blank_dates():
    rows = [
        {"date": None, "close": 100.0},          # explicit null date
        {"close": 101.0},                        # missing date key
        {"date": "", "close": 102.0},            # blank date
        {"date": "2026-01-05", "close": 103.0},  # good
    ]
    out = chart_helper._normalize_prices(rows)
    assert len(out) == 1
    assert out[0]["date"] == "2026-01-05"
    # Regression: output must NEVER carry a None date (crashed _filter_regular_hours).
    assert all(p["date"] for p in out)


def test_normalize_prices_drops_non_finite_and_nonpositive_close():
    rows = [
        {"date": "2026-01-01", "close": float("nan")},
        {"date": "2026-01-02", "close": float("inf")},
        {"date": "2026-01-03", "close": 0},        # non-positive
        {"date": "2026-01-04", "close": -5},       # negative
        {"date": "2026-01-05", "close": "N/A"},    # non-numeric
        {"date": "2026-01-06", "close": 50.0},     # good
    ]
    out = chart_helper._normalize_prices(rows)
    assert len(out) == 1
    assert out[0]["close"] == 50.0


def test_normalize_prices_sanitizes_ohlcv_to_finite_or_none():
    rows = [{
        "date": "2026-01-05",
        "open": float("nan"),
        "high": float("inf"),
        "low": "bad",
        "close": 10.0,
        "volume": float("nan"),
    }]
    out = chart_helper._normalize_prices(rows)
    assert len(out) == 1
    p = out[0]
    # No non-finite value survives into the payload (would be an invalid JSON token).
    for k in ("open", "high", "low", "volume"):
        assert p[k] is None or math.isfinite(p[k])
    assert p["close"] == 10.0


def test_normalize_falls_back_to_adjclose():
    rows = [{"date": "2026-01-05", "close": 0, "adjClose": 42.0}]
    out = chart_helper._normalize_prices(rows)
    assert len(out) == 1
    assert out[0]["close"] == 42.0


# ── the exact original crash: null-date intraday row -> filter ────

def test_null_date_row_does_not_crash_filter_regular_hours():
    """The reproduction of the confirmed crash: an intraday FMP row lacking a
    date used to reach _filter_regular_hours and blow up on len(None)."""
    raw = [
        {"close": 100.0, "open": 99.0},                       # no date
        {"date": "2026-01-05 10:30:00", "close": 101.0},      # inside RTH
        {"date": "2026-01-05 20:00:00", "close": 102.0},      # outside RTH
    ]
    prices = chart_helper._normalize_prices(raw)          # drops the date-less row
    filtered = chart_helper._filter_regular_hours(prices)  # must not raise
    assert all(p["date"] for p in filtered)
    # 10:30 kept, 20:00 dropped (outside 09:30–16:00 ET)
    assert [p["date"] for p in filtered] == ["2026-01-05 10:30:00"]


# ── _aggregate_prices ────────────────────────────────────────────

def test_aggregate_prices_empty_returns_empty():
    assert chart_helper._aggregate_prices([], "weekly") == []


def test_aggregate_prices_ignores_non_finite_close():
    daily = [
        {"date": "2026-01-05", "close": float("nan"), "high": 1, "low": 1, "open": 1},
        {"date": "2026-01-06", "close": 10.0, "high": 11, "low": 9, "open": 10},
        {"date": "2026-01-07", "close": 12.0, "high": 13, "low": 11, "open": 12},
    ]
    out = chart_helper._aggregate_prices(daily, "weekly")
    assert len(out) == 1
    bar = out[0]
    assert math.isfinite(bar["close"])
    assert bar["close"] == 12.0  # last finite close in the week


def test_aggregate_prices_quarterly_sorts_across_years():
    daily = [
        {"date": "2025-11-15", "close": 1.0, "open": 1, "high": 1, "low": 1},  # 2025-Q4
        {"date": "2026-02-15", "close": 2.0, "open": 2, "high": 2, "low": 2},  # 2026-Q1
        {"date": "2025-02-15", "close": 3.0, "open": 3, "high": 3, "low": 3},  # 2025-Q1
    ]
    out = chart_helper._aggregate_prices(daily, "quarterly")
    dates = [b["date"] for b in out]
    # Chronological: 2025-Q1 (Feb'25) < 2025-Q4 (Nov'25) < 2026-Q1 (Feb'26)
    assert dates == ["2025-02-15", "2025-11-15", "2026-02-15"]


# ── stock_overview_service._extract_chart_data ───────────────────

def test_stock_extract_chart_data_drops_bad_rows():
    prices = [
        {"date": None, "close": 100.0},               # null date
        {"date": "2026-01-02", "close": float("nan")},  # non-finite
        {"date": "2026-01-03", "close": 0},           # non-positive
        {"date": "2026-01-04", "open": float("inf"), "close": 25.0},  # good, bad open
    ]
    out = _extract_chart_data(prices, "3M")
    assert len(out) == 1
    p = out[0]
    assert p["date"] == "2026-01-04"
    assert p["close"] == 25.0
    assert p["open"] is None  # inf open sanitized away, not fabricated


def test_stock_extract_chart_data_empty():
    assert _extract_chart_data([], "1Y") == []


# ── _filter_regular_hours: what it does and does NOT do ──────────
# Documented deliberately, because two separate reviewers mis-stated it. It is a
# pure TIME-OF-DAY test with no calendar awareness at all — which is exactly why
# the fix for the 24/7 tiles belongs at the CALL SITE (pass extended_hours=True
# for crypto/commodities), not inside this filter.


def test_filter_regular_hours_is_time_of_day_only_not_calendar_aware():
    rows = [
        {"date": "2026-06-27 10:00:00", "close": 1.0},  # SATURDAY, inside 09:30-16:00
        {"date": "2026-06-29 02:00:00", "close": 2.0},  # weekday, overnight
        {"date": "2026-06-29 09:29:00", "close": 3.0},  # one minute before the bell
        {"date": "2026-06-29 09:30:00", "close": 4.0},  # the bell
        {"date": "2026-06-29 15:59:00", "close": 5.0},  # last regular minute
        {"date": "2026-06-29 16:00:00", "close": 6.0},  # the close — excluded
        {"date": "2026-06-29 20:00:00", "close": 7.0},  # after hours
    ]
    kept = [r["close"] for r in chart_helper._filter_regular_hours(rows)]
    # The Saturday bar SURVIVES (no weekday check) while the weekday overnight and
    # after-hours bars are dropped. A 24/7 asset therefore loses most of its day.
    assert kept == [1.0, 4.0, 5.0]


def test_filter_regular_hours_keeps_daily_and_unparseable_rows():
    rows = [
        {"date": "2026-06-29", "close": 1.0},            # daily bar (len <= 10)
        {"date": "29/06/2026 10:00", "close": 2.0},      # unparseable timestamp
    ]
    kept = [r["close"] for r in chart_helper._filter_regular_hours(rows)]
    assert kept == [1.0, 2.0]  # kept, not silently dropped


# ── sparkline_precision ──────────────────────────────────────────
# Shared by BOTH mini-chart builders (tracking_service holdings cards and
# home_dashboard_service pulse tiles). A flat round(c, 2) collapsed any sub-dollar
# series into a couple of levels, so the card drew a dead-flat line beside a live
# non-zero % change.


def test_sparkline_precision_scales_to_magnitude():
    p = chart_helper.sparkline_precision
    assert p([144.27, 145.01]) == 2          # normal equity — no noise digits
    assert p([12.5, 12.9]) == 2
    assert p([4.10, 4.22]) == 3              # low single digits
    assert p([0.2015, 0.2049]) == 5          # penny stock keeps its shape
    assert p([0.00001234, 0.00001301]) == 8  # sub-cent coin, capped at 8dp


def test_sparkline_precision_ignores_zeros_and_empty():
    p = chart_helper.sparkline_precision
    assert p([]) == 8                        # nothing to measure → most precise
    assert p([0.0, 0.0]) == 8                # all-zero: `min` default path
    assert p([0.0, 250.0]) == 2              # a zero must not force max precision


def test_sub_dollar_series_survives_rounding_at_its_own_precision():
    closes = [0.2015, 0.2021, 0.2033, 0.2028, 0.2044]
    digits = chart_helper.sparkline_precision(closes)
    rounded = [round(c, digits) for c in closes]
    assert min(rounded) != max(rounded)
    assert len(set(rounded)) == len(set(closes))   # no collapsing
    # The old behaviour, pinned so the regression is unmistakable.
    assert len({round(c, 2) for c in closes}) == 1
