"""The Technical Meter's WEEKLY frame, the StochRSI scale, and "listed, not counted".

Developer question (2026-09-21, crypto Analysis tab): *"crypto have 13 indicators only? because
it has 2Y only?"* The 13 is the close-only source (no high/low → five oscillators dropped), not
the history cap. But the history window DID hollow out the weekly signal for every asset:
600 days resample to ~86 weekly bars, so weekly SMA/EMA(100) and (200) were emitted as
`value=None`, classified Neutral and COUNTED — ETH's "8 of 13" weekly was 8 Buy / 0 Sell /
5 Neutral of which four were impossible, and the weekly extremes were capped. And StochRSI
from `ta` is on a 0–1 scale while the classifier read 0–100, so every StochRSI on every asset
was "Buy" (a live 0.99 — overbought — shown as Buy).

Hermetic: frames are built here; the fetch windows and the dedup wrapper are source pins.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap

import numpy as np
import pandas as pd
import pytest

from app.services import technical_analysis_service as tas
from app.services.technical_analysis_service import (
    TechnicalAnalysisService,
    _HISTORY_DAYS,
    _count_summary,
)
from app.schemas.technical_analysis import IndicatorSignal, TechnicalSignal


def _frame(closes, *, high_low: bool, start="2022-01-03") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="D")
    closes = np.asarray(closes, dtype=float)
    df = pd.DataFrame({"close": closes, "volume": 1_000_000.0}, index=idx)
    if high_low:
        df["open"] = closes
        df["high"] = closes + 1.0
        df["low"] = closes - 1.0
    else:
        for col in ("open", "high", "low"):
            df[col] = float("nan")
    return df[["open", "high", "low", "close", "volume"]]


def _wavy_ramp(n, lo=100.0, hi=400.0):
    return np.linspace(lo, hi, n) + 4.0 * np.sin(np.arange(n) * 0.5)


def _svc():
    return object.__new__(TechnicalAnalysisService)


# ── StochRSI is on 0–100, like RSI and the classifier ─────────────────────────


def test_stochrsi_is_on_a_0_to_100_scale():
    _, _, _, oscs = _svc()._compute_timeframe_signal(_frame(_wavy_ramp(300), high_low=False))
    row = next(o for o in oscs if o.name == "StochRSI(14)")
    assert row.value is not None and 0.0 <= row.value <= 100.0
    assert row.value > 1.0, "a 0–1 reading would mean the ×100 scale was lost"


def test_a_saturated_stochrsi_is_a_sell_and_a_washed_out_one_a_buy():
    """Flat, then a sharp run-up: RSI climbs to its 14-bar high → StochRSI ≈ 100 → SELL.
    Under the 0–1 bug this was 0.99 → 'Buy'. The mirror image reads BUY."""
    svc = _svc()
    up = np.concatenate([np.linspace(100, 110, 200) + 2.0 * np.sin(np.arange(200) * 0.5),
                         np.linspace(110, 220, 40)])
    _, _, _, oscs = svc._compute_timeframe_signal(_frame(up, high_low=False))
    row = next(o for o in oscs if o.name == "StochRSI(14)")
    assert row.value is not None and row.value > 80 and row.signal == IndicatorSignal.SELL
    down = np.concatenate([np.linspace(220, 210, 200) + 2.0 * np.sin(np.arange(200) * 0.5),
                           np.linspace(210, 100, 40)])
    _, _, _, oscs = svc._compute_timeframe_signal(_frame(down, high_low=False))
    row = next(o for o in oscs if o.name == "StochRSI(14)")
    assert row.value is not None and row.value < 20 and row.signal == IndicatorSignal.BUY


def test_stochrsi_is_no_longer_a_permanent_buy():
    """Over a long random walk StochRSI must land in all three bands."""
    rng = np.random.default_rng(7)
    svc = _svc()
    seen = set()
    for seed in range(12):
        walk = 200.0 + np.cumsum(rng.normal(0, 3.0, 260))
        walk = np.maximum(walk, 20.0)
        _, _, _, oscs = svc._compute_timeframe_signal(_frame(walk, high_low=False))
        row = next(o for o in oscs if o.name == "StochRSI(14)")
        if row.value is not None:
            seen.add(row.signal)
    assert seen != {IndicatorSignal.BUY}, "StochRSI read Buy on every random walk"
    assert IndicatorSignal.SELL in seen or IndicatorSignal.NEUTRAL in seen


# ── the weekly frame ──────────────────────────────────────────────────────────


def test_a_730_day_crypto_frame_gives_about_104_weekly_bars():
    weekly = TechnicalAnalysisService._daily_to_weekly(_frame(_wavy_ramp(731), high_low=False), is_crypto=True)
    assert 104 <= len(weekly) <= 106
    assert weekly.index.dayofweek[0] == 6, "crypto weeks end on Sunday (W-SUN)"


def test_a_1500_day_stock_frame_gives_at_least_200_weekly_bars():
    days = pd.date_range("2022-01-03", periods=_HISTORY_DAYS, freq="D")
    trading = days[days.dayofweek < 5]                      # ~1,071 rows
    closes = _wavy_ramp(len(trading))
    df = pd.DataFrame({"open": closes, "high": closes + 1, "low": closes - 1,
                       "close": closes, "volume": 1e6}, index=trading)
    weekly = TechnicalAnalysisService._daily_to_weekly(df, is_crypto=False)
    assert len(weekly) >= 200, f"{len(weekly)} weekly bars — weekly SMA/EMA(200) needs 200"
    assert weekly.index.dayofweek[0] == 4, "equity weeks end on Friday (W-FRI)"


def test_the_600_day_window_was_the_defect():
    """Anti-vacuity for the constant: 600 days is ~86 bars, which cannot carry (100)."""
    days = pd.date_range("2022-01-03", periods=600, freq="D")
    trading = days[days.dayofweek < 5]
    closes = _wavy_ramp(len(trading))
    df = pd.DataFrame({"open": closes, "high": closes + 1, "low": closes - 1,
                       "close": closes, "volume": 1e6}, index=trading)
    assert len(TechnicalAnalysisService._daily_to_weekly(df)) < 100
    assert _HISTORY_DAYS >= 1400


def test_crypto_weekly_computes_the_100s_and_lists_the_200s_as_null():
    weekly = TechnicalAnalysisService._daily_to_weekly(_frame(_wavy_ramp(731), high_low=False), is_crypto=True)
    result, gauge, mas, oscs = _svc()._compute_timeframe_signal(weekly)
    by_name = {m.name: m for m in mas}
    for name in ("SMA(100)", "EMA(100)"):
        assert by_name[name].value is not None, f"{name} must be computable on 104 weekly bars"
    for name in ("SMA(200)", "EMA(200)"):
        assert by_name[name].value is None, f"{name} cannot exist on 104 weekly bars"
        assert by_name[name].signal == IndicatorSignal.NEUTRAL
    # Listed (the sheet names them), not counted (the card is honest).
    assert len(mas) == 10 and len(oscs) == 3
    assert result.total_indicators == 11
    assert result.total_indicators == sum(1 for i in (*mas, *oscs) if i.value is not None)


def test_stock_weekly_computes_all_ten_moving_averages():
    days = pd.date_range("2022-01-03", periods=_HISTORY_DAYS, freq="D")
    trading = days[days.dayofweek < 5]
    closes = _wavy_ramp(len(trading))
    df = pd.DataFrame({"open": closes, "high": closes + 1, "low": closes - 1,
                       "close": closes, "volume": 1e6}, index=trading)
    weekly = TechnicalAnalysisService._daily_to_weekly(df)
    result, _, mas, oscs = _svc()._compute_timeframe_signal(weekly)
    assert all(m.value is not None for m in mas), [m.name for m in mas if m.value is None]
    assert result.total_indicators == len(mas) + len([o for o in oscs if o.value is not None])
    assert result.total_indicators >= 17


# ── listed, not counted ───────────────────────────────────────────────────────


def test_null_rows_are_excluded_from_total_matching_gauge_and_summaries():
    """A frame long enough for everything but the 200s: 200 close-only rows."""
    result, gauge, mas, oscs = _svc()._compute_timeframe_signal(_frame(_wavy_ramp(199), high_low=False))
    nulls = [i for i in (*mas, *oscs) if i.value is None]
    assert {i.name for i in nulls} == {"SMA(200)", "EMA(200)"}
    computed = [i for i in (*mas, *oscs) if i.value is not None]
    assert result.total_indicators == len(computed) == 11
    assert result.matching_indicators <= result.total_indicators
    summary = _count_summary(mas)
    assert summary.buy_count + summary.neutral_count + summary.sell_count == 8, (
        "the MA summary must count the eight computed rows, not the two null ones")


def test_the_extreme_is_reachable_when_every_computed_row_agrees():
    """Four impossible rows used to pin the weekly gauge inside [0.154, 0.846]."""
    result, gauge, mas, oscs = _svc()._compute_timeframe_signal(_frame(np.linspace(100, 400, 199), high_low=False))
    # Linear ramp: every computed MA is Buy, MACD Buy, RSI 100 → Sell, StochRSI undefined.
    assert result.signal == TechnicalSignal.STRONG_BUY
    assert gauge > 0.861, f"gauge {gauge:.3f} still capped"


def test_a_frame_with_nothing_computable_is_zero_of_zero_and_hold():
    result, gauge, mas, oscs = _svc()._compute_timeframe_signal(_frame([10.0] * 5, high_low=True))
    assert (result.total_indicators, result.matching_indicators) == (0, 0)
    assert gauge == 0.5 and result.signal == TechnicalSignal.HOLD
    assert len(mas) + len(oscs) == 18 and all(i.value is None for i in (*mas, *oscs))


def test_matching_never_exceeds_total_across_lengths():
    svc = _svc()
    for n in (5, 12, 20, 40, 60, 120, 199, 300):
        for hl in (True, False):
            result, gauge, _, _ = svc._compute_timeframe_signal(_frame(_wavy_ramp(n), high_low=hl))
            assert 0 <= result.matching_indicators <= result.total_indicators
            assert 0.0 <= gauge <= 1.0


def test_a_weekly_result_has_no_non_finite_floats():
    weekly = TechnicalAnalysisService._daily_to_weekly(_frame(_wavy_ramp(731), high_low=False), is_crypto=True)
    _, gauge, mas, oscs = _svc()._compute_timeframe_signal(weekly)
    payload = {"gauge": gauge, "rows": [i.model_dump() for i in (*mas, *oscs)]}
    json.dumps(payload, allow_nan=False)


# ── source pins ───────────────────────────────────────────────────────────────


def _src(fn):
    return textwrap.dedent(inspect.getsource(fn))


def test_the_crypto_window_is_the_plan_cap_not_a_literal():
    src = _src(TechnicalAnalysisService._fetch_daily_ohlcv_uncached)
    assert "_history_days_cap()" in src, "the crypto window must follow CRYPTO_HISTORY_YEARS"
    assert "min(600" not in src and "days=600" not in src, "the 600-day literal is back"
    assert "timedelta(days=_HISTORY_DAYS)" in src, "the equity window must use _HISTORY_DAYS"


def test_the_detail_build_is_deduped():
    src = _src(TechnicalAnalysisService.get_analysis_detail)
    assert "_deduped(" in src and "ta_detail:build:" in src, (
        "N sheets opened at once must build one response, as get_analysis already does")


def test_the_counting_reads_only_computed_rows():
    tree = ast.parse(_src(TechnicalAnalysisService._compute_timeframe_signal))
    comps = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.ListComp)]
    assert any("value is not None" in c and "signal" in c for c in comps), (
        "the gauge must count rows whose value is not None — null rows are listed, not counted")


def test_the_stochrsi_is_scaled_by_100():
    src = _src(TechnicalAnalysisService._compute_timeframe_signal)
    start = src.index("StochRSIIndicator(")
    window = src[start:start + 600]
    assert "* 100" in window, "the ×100 on StochRSI is gone — ta returns 0–1"


# ── the "52-Week" Fibonacci window is 365 calendar days, not 252 rows ────────


def test_fibonacci_window_is_a_year_of_calendar_days_on_a_seven_day_series():
    """252 rows is 52 trading weeks on an equity but ~36 weeks on crypto. A closing spike
    300 days back must be the 0 % level; one 400 days back must not."""
    closes = np.full(731, 100.0)
    closes[-300] = 500.0      # inside 365 days
    closes[-400] = 900.0      # outside
    fib = _svc()._compute_fibonacci(_frame(closes, high_low=False))
    assert fib.timeframe == "52-Week Levels · closing prices"
    assert fib.levels[0].value == 500.0 and fib.levels[-1].value == 100.0


def test_fibonacci_on_an_equity_frame_still_uses_intraday_range():
    closes = _wavy_ramp(400)
    df = _frame(closes, high_low=True)
    fib = _svc()._compute_fibonacci(df)
    assert fib.timeframe == "52-Week Levels"
    window = df[df.index >= df.index[-1] - pd.Timedelta(days=365)]
    assert fib.levels[0].value == round(float(window["high"].max()), 2)


# ── a verdict needs a quorum (review finding, 2026-09-22) ────────────────────


@pytest.mark.parametrize("n", [10, 11, 12, 13])
def test_a_two_indicator_sample_holds_instead_of_reading_strong(n):
    """With 10–13 bars exactly SMA(10) and EMA(10) compute — every other indicator is
    gated at 14/15/28/35 — so a unanimous two-row sample scored 0.5 + 2/(2·2) = 1.0 and
    published "Strong Buy · 2 of 2", which the overall gauge then averaged into the
    headline. A smaller sample must widen the uncertainty, not sharpen the verdict."""
    svc = _svc()
    for closes in (np.linspace(100, 140, n), np.linspace(140, 100, n)):
        result, gauge, mas, oscs = svc._compute_timeframe_signal(_frame(closes, high_low=True))
        computed = [i for i in (*mas, *oscs) if i.value is not None]
        assert len(computed) < 5, "this test only means something below the quorum"
        assert gauge == 0.5 and result.signal == TechnicalSignal.HOLD, (
            f"{n} bars, {len(computed)} computed → {result.signal} at {gauge}")
        assert result.total_indicators == len(computed), "the rows shipped are still reported"


def test_the_quorum_floor_does_not_touch_a_full_frame():
    """Anti-vacuity: a long frame still reaches the extremes."""
    result, gauge, _, _ = _svc()._compute_timeframe_signal(
        _frame(np.linspace(100, 400, 199), high_low=False)
    )
    assert result.total_indicators >= tas._MIN_GAUGE_INDICATORS
    assert result.signal == TechnicalSignal.STRONG_BUY and gauge > 0.861


def test_the_quorum_constant_is_pinned():
    assert tas._MIN_GAUGE_INDICATORS == 5


# ── OBV does not move with the fetch window ─────────────────────────────────


def test_obv_is_accumulated_over_a_fixed_window_not_the_whole_fetch():
    """OBV is cumulative from its first bar, so widening `_HISTORY_DAYS` rescaled it and
    flipped the sign iOS paints bullish/bearish. Every other figure on the Volume card is
    a rolling tail; this one has to be pinned too."""
    rng = np.random.default_rng(3)
    closes = 100.0 + np.cumsum(rng.normal(0, 1.5, 1400))
    long_df = _frame(closes, high_low=True)
    long_df["volume"] = rng.uniform(1e6, 5e6, len(closes))
    short_df = long_df.tail(600).copy()          # the pre-change window
    a = _svc()._compute_volume_analysis(long_df)
    b = _svc()._compute_volume_analysis(short_df)
    assert a.obv == pytest.approx(b.obv), "OBV still depends on how far back the fetch went"
    assert tas._OBV_WINDOW_BARS == 252


def test_obv_still_reads_the_recent_tail():
    """Anti-vacuity: a rising tape accumulates positive OBV, a falling one negative."""
    rng = np.random.default_rng(5)
    for closes, positive in ((np.linspace(100, 200, 300), True), (np.linspace(200, 100, 300), False)):
        df = _frame(closes, high_low=True)
        df["volume"] = rng.uniform(1e6, 2e6, len(closes))
        obv = _svc()._compute_volume_analysis(df).obv
        assert (obv > 0) is positive, obv
