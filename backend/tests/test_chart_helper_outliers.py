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
