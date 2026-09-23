"""Coins get a real session range, so pivots and Support & Resistance can exist.

TestFlight (2026-09-22): *"check for me about Key support & resistance. we need to find a
way to make it works."* — the cards were empty on every coin, because `market_chart` (the
series all crypto surfaces run on) carries close and volume only, and classic pivots need
the previous bar's high/low/close. `/ohlc?days=30` is six 4-hour candles per day (measured:
180 candles), and each candle's high/low are true intraday extremes over its bucket, so the
daily bar aggregated from them is a real session range.

Hermetic: the adapter is pure, and the merge is exercised on rows built here.
"""

from __future__ import annotations

import inspect
import textwrap

import numpy as np
import pandas as pd
import pytest

from app.services import technical_analysis_service as tas
from app.services.coingecko_adapter import ohlc_to_daily_rows, ohlc_to_rows
from app.services.crypto_service import CryptoService
from app.services.technical_analysis_service import TechnicalAnalysisService

# 2026-09-22 12:00 ET and the five following 4-hour candles, then the next day.
_H = 4 * 60 * 60 * 1000


def _candles(start_ms: int, spec):
    return [[start_ms + i * _H, o, h, l, c] for i, (o, h, l, c) in enumerate(spec)]


def test_four_hour_candles_collapse_into_one_session_bar():
    base = 1_790_000_000_000
    raw = _candles(base, [
        (100.0, 105.0, 99.0, 104.0),
        (104.0, 112.0, 103.0, 110.0),
        (110.0, 111.0, 95.0, 97.0),
    ])
    rows = ohlc_to_daily_rows(raw)
    assert len(rows) == 1
    row = rows[0]
    assert row["open"] == 100.0, "the FIRST candle's open"
    assert row["close"] == 97.0, "the LAST candle's close"
    assert row["high"] == 112.0 and row["low"] == 95.0, "the session's true extremes"


def test_candles_are_grouped_per_et_date_and_sorted():
    base = 1_790_000_000_000
    raw = _candles(base, [(1, 2, 0.5, 1.5)] * 12)          # two days of six candles
    rows = ohlc_to_daily_rows(raw)
    assert 1 <= len(rows) <= 3
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    assert all({"date", "open", "high", "low", "close"} <= set(r) for r in rows)


@pytest.mark.parametrize("payload", [None, {}, [], "x", [[1]], [[1, 2, 3, 4]], [None]])
def test_a_malformed_payload_yields_no_rows(payload):
    assert ohlc_to_daily_rows(payload) == []


def test_a_candle_with_a_null_high_keeps_the_other_extreme():
    base = 1_790_000_000_000
    raw = [[base, 10.0, None, 9.0, 9.5], [base + _H, 9.5, 12.0, None, 11.0]]
    rows = ohlc_to_daily_rows(raw)
    assert len(rows) == 1 and rows[0]["high"] == 12.0 and rows[0]["low"] == 9.0


def test_it_agrees_with_the_per_candle_adapter_on_the_extremes():
    base = 1_790_000_000_000
    raw = _candles(base, [(1.0, 3.0, 0.5, 2.0), (2.0, 9.0, 1.0, 8.0), (8.0, 8.5, 7.0, 7.5)])
    flat, daily = ohlc_to_rows(raw), ohlc_to_daily_rows(raw)
    assert max(r["high"] for r in flat) == max(r["high"] for r in daily)
    assert min(r["low"] for r in flat) == min(r["low"] for r in daily)


# ── the fetch is capped where the candles stop being intraday ────────────────


def test_the_fetch_never_asks_for_a_range_coarser_than_a_session():
    """At `days >= 90` CoinGecko switches to 4-DAY candles; bucketing those by date would
    label a four-day range as one session."""
    src = textwrap.dedent(inspect.getsource(CryptoService._cg_recent_daily_ohlc))
    assert "min(int(days), 30)" in src
    assert "ohlc_to_daily_rows" in src
    assert '"vs_currency": "usd"' in src


def test_the_merge_is_best_effort_and_only_fills_the_missing_columns():
    fetch = textwrap.dedent(inspect.getsource(TechnicalAnalysisService._fetch_daily_ohlcv_uncached))
    assert "_cg_recent_daily_ohlc" in fetch
    assert "except Exception" in fetch, "a failed range must leave the close-only frame"
    merge = fetch[fetch.index("_cg_recent_daily_ohlc"):]
    assert 'for col in ("open", "high", "low")' in merge, (
        "close and volume must stay the series every other crypto surface reads")


# ── the range tail: what the indicators are actually fed ────────────────────


def _mixed_frame(n_close_only: int, n_range: int) -> pd.DataFrame:
    total = n_close_only + n_range
    idx = pd.date_range("2024-01-01", periods=total, freq="D")
    closes = np.linspace(100, 300, total) + 3 * np.sin(np.arange(total) * 0.7)
    df = pd.DataFrame({"close": closes, "volume": 1e6}, index=idx)
    df["open"] = np.nan
    df["high"] = np.nan
    df["low"] = np.nan
    if n_range:
        df.iloc[-n_range:, df.columns.get_loc("high")] = closes[-n_range:] + 2
        df.iloc[-n_range:, df.columns.get_loc("low")] = closes[-n_range:] - 2
        df.iloc[-n_range:, df.columns.get_loc("open")] = closes[-n_range:]
    return df[["open", "high", "low", "close", "volume"]]


def test_the_range_tail_is_the_contiguous_run_that_has_a_range():
    df = _mixed_frame(700, 30)
    tail = TechnicalAnalysisService._range_tail(df)
    assert len(tail) == 30 and tail.index[-1] == df.index[-1]
    assert TechnicalAnalysisService._range_tail(_mixed_frame(700, 0)).empty
    full = _mixed_frame(0, 40)
    assert len(TechnicalAnalysisService._range_tail(full)) == 40, "an equity frame is unchanged"


def test_adx_and_atr_compute_from_the_merged_tail():
    """Wilder smoothing carries a leading NaN all the way to the last value, so feeding
    the whole frame left ADX and ATR null on every coin even with 30 real sessions."""
    svc = object.__new__(TechnicalAnalysisService)
    _, _, _, oscs = svc._compute_timeframe_signal(_mixed_frame(700, 30))
    by_name = {o.name: o for o in oscs}
    for name in ("Stoch(14,3)", "Williams %R", "CCI(14)", "ADX(14)", "ATR(14)"):
        assert by_name[name].value is not None, f"{name} should compute from a 30-bar range"


def test_a_frame_with_no_range_anywhere_still_drops_the_five():
    svc = object.__new__(TechnicalAnalysisService)
    _, _, mas, oscs = svc._compute_timeframe_signal(_mixed_frame(300, 0))
    assert len(mas) == 10 and len(oscs) == 3, "close-only sources must not ship five nulls"


def test_the_fifty_two_week_band_is_not_drawn_from_a_thirty_day_range():
    """⚠️ The merged range covers ~30 sessions. `window["high"].max()` over that is a
    30-day high; printing it as "52-Week" would be a fabricated number under a confident
    label. The closing extremes DO span the year, so they are used instead."""
    svc = object.__new__(TechnicalAnalysisService)
    fib = svc._compute_fibonacci(_mixed_frame(700, 30))
    assert fib.timeframe == "52-Week Levels · closing prices"
    closes = _mixed_frame(700, 30)["close"]
    window_closes = closes[closes.index >= closes.index[-1] - pd.Timedelta(days=365)]
    assert fib.levels[0].value == pytest.approx(round(float(window_closes.max()), 2))


def test_pivots_and_support_resistance_exist_once_the_range_is_merged():
    svc = object.__new__(TechnicalAnalysisService)
    df = _mixed_frame(700, 30)
    pivots = svc._compute_pivot_points(df)
    assert len(pivots.levels) == 7 and all(l.value > 0 for l in pivots.levels)
    sr = svc._compute_support_resistance(df)
    assert len(sr.resistance_levels) == 3 and len(sr.support_levels) == 3
    # And they stay empty when the source has no range at all.
    assert svc._compute_pivot_points(_mixed_frame(300, 0)).levels == []
