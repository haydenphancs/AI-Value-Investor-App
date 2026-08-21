"""
Regression tests for the systemic operator-precedence date-filter bug + the
non-finite OHLCV hardening surfaced by an adversarial review of the asset-detail
(TickerDetail / ETF / crypto / index / commodity) data path.

The bug: `p.get("date") or "" >= cutoff` parses (Python precedence: `>=` binds
tighter than `or`) as `p.get("date") or ("" >= cutoff)`. `"" >= cutoff` is always
False, so the whole condition collapses to the truthiness of the date string —
EVERY dated row passes and the date window is a no-op. That silently:

  * returned the ENTIRE multi-year history for a 3M/6M/1Y chart request
    (index/crypto/commodity/stock chart extractors),
  * computed the 5-year benchmark CAGR / crypto 52-week high-low over ALL
    history while labeling it 5-year / 52-week,
  * baselined commodity "YTD" performance against the oldest (~2010) price
    instead of Jan 1 of the current year.

The finite-guard half: a NaN close survives a bare `close <= 0` / `close > 0`
check (nan comparisons are False), and raw open/high/low/volume forwarded into
the response serialize as invalid JSON `NaN`/`Infinity` tokens — Starlette
(allow_nan=False) then 500s the WHOLE detail response.

No network, no Supabase — service math methods are exercised on an
`__init__`-bypassed instance; chart_helper functions are pure.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import chart_helper
from app.services.etf_service import ETFService
from app.services.crypto_service import CryptoService
from app.services.index_service import IndexService
from app.services.commodity_service import CommodityService
from app.services.stock_overview_service import StockOverviewService

# Every asset service whose _extract_chart_data(self, historical, chart_range)
# shares the (now-fixed) date-window + finite-guard shape.
_CHART_SERVICES = [ETFService, CryptoService, IndexService, CommodityService]


def _svc(cls):
    return object.__new__(cls)  # bypass __init__ (no FMP / Supabase)


def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _row_date(row):
    # index returns ChartDataPointResponse objects; others return dicts.
    return row.date if hasattr(row, "date") else row["date"]


def _row_get(row, key):
    return getattr(row, key) if hasattr(row, key) else row.get(key)


# ─────────────────────────────────────────────────────────────────────────────
# Precedence bug: the date-window filter must actually window the history
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls", _CHART_SERVICES)
def test_extract_chart_data_windows_to_requested_range(cls):
    svc = _svc(cls)
    rows = [
        {"date": _d(0), "close": 100.0},
        {"date": _d(5), "close": 101.0},
        # ~5.5y old: outside a 3M window even for the index (90 + 320d warmup).
        {"date": _d(2000), "close": 50.0},
    ]
    out = svc._extract_chart_data(list(rows), "3M")
    dates = {_row_date(r) for r in out}
    assert _d(0) in dates and _d(5) in dates          # recent rows kept
    assert _d(2000) not in dates                       # ancient row dropped
    # Regression: pre-fix the filter was a no-op and returned all 3 rows.
    assert len(out) == 2


@pytest.mark.parametrize("cls", _CHART_SERVICES)
def test_extract_chart_data_drops_and_sanitizes_nonfinite(cls):
    svc = _svc(cls)
    rows = [
        {"date": _d(1), "close": float("nan")},   # non-finite close → drop
        {"date": _d(2), "close": float("inf")},   # non-finite close → drop
        {"date": _d(3), "close": 0},              # non-positive close → drop
        {"date": _d(4), "open": float("inf"), "high": float("nan"),
         "low": 9.0, "close": 25.0, "volume": float("nan")},  # keep, sanitize OHLCV
    ]
    out = svc._extract_chart_data(list(rows), "1Y")
    assert len(out) == 1
    r = out[0]
    assert _row_get(r, "close") == 25.0
    # No non-finite value survives — it would be an invalid JSON token → 500.
    for k in ("open", "high", "low", "volume"):
        v = _row_get(r, k)
        assert v is None or math.isfinite(v)
    assert _row_get(r, "open") is None   # inf open sanitized away, not fabricated
    assert _row_get(r, "high") is None   # nan high sanitized away
    assert _row_get(r, "low") == 9.0     # finite low preserved


@pytest.mark.parametrize("cls", _CHART_SERVICES)
def test_extract_chart_data_empty_input(cls):
    assert _svc(cls)._extract_chart_data([], "3M") == []


# ─────────────────────────────────────────────────────────────────────────────
# Commodity YTD: baseline must be the first row on/after Jan 1, not the oldest
# ─────────────────────────────────────────────────────────────────────────────

def test_commodity_ytd_baseline_is_year_start_not_oldest():
    svc = _svc(CommodityService)
    year = datetime.now(tz=timezone.utc).year
    historical = [
        {"date": f"{year - 2}-06-01", "close": 100.0},  # oldest — must NOT baseline
        {"date": f"{year}-01-02", "close": 200.0},       # first row of THIS year
        {"date": f"{year}-06-01", "close": 220.0},
        {"date": f"{year}-09-01", "close": 240.0},       # latest → current_close
    ]
    periods = svc._build_performance(historical)
    ytd = next(p for p in periods if p.label == "YTD")
    # (240 - 200)/200*100 = 20.0 — NOT the (240-100)/100 = 140.0 oldest-row bug.
    assert ytd.change_percent == pytest.approx(20.0)


def test_commodity_ytd_absent_when_no_current_year_rows():
    svc = _svc(CommodityService)
    year = datetime.now(tz=timezone.utc).year
    # Only history from prior years — no row on/after Jan 1 of the current year.
    historical = [
        {"date": f"{year - 3}-06-01", "close": 100.0},
        {"date": f"{year - 2}-06-01", "close": 120.0},
    ]
    periods = svc._build_performance(historical)
    # No YTD baseline → the YTD row is skipped, never fabricated from the oldest.
    assert all(p.label != "YTD" for p in periods)


# ─────────────────────────────────────────────────────────────────────────────
# Stock 5-year benchmark CAGR must use the trailing-5y window, not all history
# ─────────────────────────────────────────────────────────────────────────────

def test_stock_benchmark_5y_uses_trailing_window_not_full_history():
    svc = _svc(StockOverviewService)
    today = date.today()
    stock_hist = []
    # 5 ancient rows (~8.7y ago) that the trailing-5y window MUST exclude. If the
    # filter is a no-op (the bug) hist_5y[0] is one of these (close 999) and the
    # "5Y" CAGR is computed 999→200 over ~8.7y (~ -17%/yr).
    for i in range(5):
        stock_hist.append(
            {"date": (today - timedelta(days=3200 + i)).isoformat(), "close": 999.0}
        )
    # 300 rows within the last ~4.9y (>=252 so the 5Y branch is taken), all 150.
    for i in range(300):
        stock_hist.append(
            {"date": (today - timedelta(days=1794 - i * 6)).isoformat(), "close": 150.0}
        )
    # Newest row today at 200 — the window end price.
    stock_hist.append({"date": today.isoformat(), "close": 200.0})
    stock_hist.sort(key=lambda p: p["date"])  # production passes oldest-first

    result = svc._build_benchmark_summary(stock_hist, spy_hist=[], ipo_price_data=None)
    assert result is not None
    # 5Y CAGR ≈ (200/150)^(1/~4.9) - 1 ≈ +6%/yr — a positive single-digit number,
    # NOT the negative all-history figure. Bounds are wide to stay non-brittle.
    assert 3.0 <= result.avg_annual_return <= 9.0


# ─────────────────────────────────────────────────────────────────────────────
# chart_helper: the dropped `[:10]` slice must not silently discard full
# datetime-format rows (strptime on the un-sliced string used to raise → skip)
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Ownership snapshot: a non-numeric FMP field must not 500 the whole /overview
# ─────────────────────────────────────────────────────────────────────────────

def test_ownership_snapshot_tolerates_nonnumeric_fmp_fields():
    svc = _svc(StockOverviewService)
    # FMP hands "" / "N/A" back for absent numeric fields; a raw float() would
    # ValueError and 500 the whole /overview (price + chart + 4 valid snapshots).
    km = {"institutionalOwnership": "N/A", "insidersPercentage": ""}
    snap = svc._build_ownership_snapshot(km)  # must not raise
    inst = next(m for m in snap.metrics if m.name == "Institutional Ownership")
    ins = next(m for m in snap.metrics if m.name == "Insider Ownership")
    assert inst.value == "—"   # "N/A" → honest dash, not a fabricated 0.0%
    assert ins.value == "—"    # "" → honest dash


def test_ownership_snapshot_formats_genuine_decimals():
    svc = _svc(StockOverviewService)
    snap = svc._build_ownership_snapshot(
        {"institutionalOwnership": 0.61, "insidersPercentage": 0.02}
    )
    inst = next(m for m in snap.metrics if m.name == "Institutional Ownership")
    ins = next(m for m in snap.metrics if m.name == "Insider Ownership")
    assert inst.value == "61.0%"   # decimal (0.61) → 61.0% (happy path preserved)
    assert ins.value == "2.0%"


def test_aggregate_prices_handles_datetime_format_dates():
    daily = [
        {"date": "2026-01-05 00:00:00", "open": 10, "high": 10, "low": 10, "close": 10.0},
        {"date": "2026-01-06 00:00:00", "open": 12, "high": 12, "low": 12, "close": 12.0},
    ]
    out = chart_helper._aggregate_prices(daily, "weekly")
    # Both rows share an ISO week → one aggregated bar; the (X or "")[:10] fix
    # lets strptime parse them instead of dropping the whole week to [].
    assert len(out) == 1
    assert out[0]["date"] == "2026-01-06"   # 10-char sliced, no time component
    assert out[0]["close"] == 12.0


# ─────────────────────────────────────────────────────────────────────────────
# MA(200) warm-up: the backend must send leading bars the client can trim
#
# `TickerChartView.warmupCount` finds the first bar at or after the visible start and
# draws from there, feeding everything EARLIER to the indicator pass. So the number of
# bars the backend sends before the visible window IS the indicator warm-up — send none
# and MA(200) is blank across the whole chart, silently, with no error anywhere.
#
# Two holes existed:
#   (a) `commodity_service._window_by_range` trimmed locally-aggregated bars with
#       `compute_date_range` alone. The path it replaced (`fetch_chart_data`) extended
#       `from_date` by `_warmup_calendar_days(interval)` — 1,484 days for weekly — so the
#       decomposition dropped ~210 leading weekly bars from every 5Y commodity chart.
#   (b) etf / crypto / commodity each carried their own copy of the daily range map with
#       NO warm-up term at all, and stock_overview_service's was sized for MACD/RSI (50
#       bars), not MA(200) (200). Only index_service was ever right.
# ─────────────────────────────────────────────────────────────────────────────

def _daily_series(years: int) -> list:
    """`years` of daily bars ending today, oldest-first."""
    n = years * 365
    return [
        {
            "date": (date.today() - timedelta(days=n - 1 - i)).isoformat(),
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0 + i * 0.01, "volume": 1000,
        }
        for i in range(n)
    ]


def test_window_by_range_keeps_the_weekly_warmup_ahead_of_the_visible_start():
    """A 5Y weekly chart must carry ~210 bars BEFORE the 5-year mark.

    This is the regression the commodity decomposition shipped: windowing to exactly
    `today - 5y` leaves warmupCount == 0 and MA(200) never draws.
    """
    weekly = chart_helper._aggregate_prices(_daily_series(12), "weekly")
    out = chart_helper.window_by_range(weekly, "5Y", "weekly")

    visible_start = (date.today() - timedelta(days=365 * 5)).isoformat()
    warmup = [b for b in out if b["date"] < visible_start]

    assert warmup, "no warm-up bars: MA(200) cannot draw at the left edge of a 5Y chart"
    # ~210 weeks of warm-up (1484 calendar days). Bounded on both sides so this fails
    # if the warm-up is dropped AND if it silently becomes the whole series.
    assert 180 <= len(warmup) <= 230, f"expected ~210 warm-up bars, got {len(warmup)}"
    assert len(out) < len(weekly), "windowing did nothing at all"


def test_window_by_range_warmup_is_interval_sensitive():
    """Anti-vacuity: the warm-up must come from the INTERVAL, not be a constant pad.

    Daily warm-up is 320 days, weekly is 1,484 — if this returns the same slice for both
    then `resolved_interval` is being ignored and the test above would pass on a hardcode.
    """
    weekly = chart_helper._aggregate_prices(_daily_series(12), "weekly")
    as_weekly = chart_helper.window_by_range(weekly, "5Y", "weekly")
    as_daily = chart_helper.window_by_range(weekly, "5Y", "daily")
    assert len(as_weekly) > len(as_daily), (
        "weekly warm-up (1484d) must reach further back than daily (320d)"
    )


def test_window_by_range_degrades_rather_than_raising():
    bars = [{"date": "2020-01-01", "close": 1.0}, {"close": 2.0}, {"date": None}]
    # ALL has from_date "1970-01-01"; nothing to trim, and the malformed rows must not
    # raise a TypeError mid-request.
    assert chart_helper.window_by_range(bars, "ALL", "monthly")
    assert chart_helper.window_by_range([], "5Y", "weekly") == []
    # An unknown range falls back to the 3M delta rather than blowing up.
    chart_helper.window_by_range(bars, "NOPE", "daily")


@pytest.mark.parametrize("cls", _CHART_SERVICES)
def test_daily_chart_carries_the_ma200_warmup(cls):
    """3M must return bars OLDER than 90 days — that is the warm-up — but not unbounded.

    Fails against the pre-fix etf / crypto / commodity, which returned nothing before the
    90-day mark.
    """
    svc = _svc(cls)
    out = svc._extract_chart_data(_daily_series(3), "3M")
    dates = sorted(_row_date(r) for r in out)
    assert dates, "3M produced no bars at all"

    visible_start = (date.today() - timedelta(days=90)).isoformat()
    warmup = [d for d in dates if d < visible_start]
    assert warmup, f"{cls.__name__}: no warm-up bars before the visible 3M window"
    # 320 calendar days of warm-up, and nothing beyond 3M + 320.
    assert 250 <= len(warmup) <= 340, f"{cls.__name__}: {len(warmup)} warm-up bars"
    assert dates[0] >= (date.today() - timedelta(days=90 + 330)).isoformat(), (
        f"{cls.__name__}: window is not bounded — it returned the whole history"
    )


def test_every_daily_extractor_windows_identically():
    """One definition, proven by behaviour rather than by grepping for the call.

    These were four separate copies of a literal range map and only one of them carried
    the warm-up. A source scan for `daily_range_days(` would pass on a comment; this
    cannot.
    """
    rows = _daily_series(3)
    windows = {
        cls.__name__: sorted(_row_date(r) for r in _svc(cls)._extract_chart_data(list(rows), "6M"))
        for cls in _CHART_SERVICES
    }
    first = next(iter(windows.values()))
    for name, dates in windows.items():
        assert dates == first, f"{name} windows 6M differently from the others"


def test_stock_chart_warmup_covers_ma200_not_just_macd():
    """stock_overview_service slices by BAR COUNT, not by date — same window, other unit.

    Its warm-up was 50 bars, sized for MACD (~34) and RSI (~14). MA(200) needs 200, so the
    overlay drew nothing across a 3M chart.
    """
    from app.services.chart_helper import _WARMUP_DATA_POINTS
    from app.services.stock_overview_service import _extract_chart_data as stock_extract

    assert _WARMUP_DATA_POINTS >= 200, "the shared warm-up no longer covers MA(200)"
    out = stock_extract(_daily_series(3), "3M")
    # 63 visible trading days + 210 warm-up bars.
    assert len(out) == 63 + _WARMUP_DATA_POINTS, (
        f"expected {63 + _WARMUP_DATA_POINTS} bars (visible + warm-up), got {len(out)}"
    )
