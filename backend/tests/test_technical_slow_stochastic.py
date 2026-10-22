"""The Technical Meter's stochastic is the SLOW Stoch(14,3,3) (plan B5).

The chart's pane is labelled "Stoch(14,3,3)" and draws slow %K = SMA3(raw 14-bar %K); the
meter used to compute the FAST %K (`ta`'s `stoch()`) under the name "Stoch(14,3)", so the
two disagreed on the very last bar. Pinned here with a hand-computed fixture:

  lows  L[i] = 100 − i   → the 14-bar low at i is L[i]
  highs H[i] = 110 + i   → the 14-bar high at i is H[i]; range = 10 + 2i
  raw %K(13) = (96 − 87) / 36 · 100 = 25
  raw %K(14) = (105 − 86) / 38 · 100 = 50
  raw %K(15) = (115 − 85) / 40 · 100 = 75
  slow %K(15) = (25 + 50 + 75) / 3 = 50    (the fast %K would read 75)

Slow %K first exists at index 15, so 15 bars give a null row (listed, not counted) and 16
give a value. A close-only source (crypto) still omits the row under its new name.
"""

from __future__ import annotations

import inspect
import io
import re
import tokenize

import numpy as np
import pandas as pd

from app.schemas.technical_analysis import IndicatorSignal
from app.services import technical_analysis_service as TA
from app.services.technical_analysis_service import STOCH_INDICATOR_NAME, TechnicalAnalysisService

_CLOSES = [105.0] * 13 + [96.0, 105.0, 115.0]


def _frame(n: int, *, high_low: bool = True) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    closes = np.asarray(_CLOSES[:n], dtype=float)
    i = np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "open": closes if high_low else np.full(n, np.nan),
            "high": 110.0 + i if high_low else np.full(n, np.nan),
            "low": 100.0 - i if high_low else np.full(n, np.nan),
            "close": closes,
            "volume": np.full(n, 1_000.0),
        },
        index=idx,
    )


def _stoch_row(n: int, *, high_low: bool = True):
    svc = object.__new__(TechnicalAnalysisService)
    result, _, mas, oscs = svc._compute_timeframe_signal(_frame(n, high_low=high_low))
    rows = {o.name: o for o in oscs}
    return result, mas, oscs, rows.get(STOCH_INDICATOR_NAME)


def test_the_name_is_the_charts_and_is_one_constant():
    assert STOCH_INDICATOR_NAME == "Stoch(14,3,3)"


def test_sixteen_bars_give_the_hand_computed_slow_k():
    _result, _mas, _oscs, row = _stoch_row(16)
    assert row is not None, "the stochastic row disappeared"
    assert row.value == 50.0, f"slow %K should be 50.00 (fast %K reads 75.00), got {row.value}"
    assert row.signal == IndicatorSignal.NEUTRAL


def test_fifteen_bars_list_the_row_but_do_not_count_it():
    result, mas, oscs, row = _stoch_row(15)
    assert row is not None, "an uncomputable row is LISTED (iOS renders '—'), not removed"
    assert row.value is None
    computed = [i for i in (*mas, *oscs) if i.value is not None]
    assert result.total_indicators == len(computed), "a null row was counted"


def test_the_twenty_eighty_thresholds_are_kept():
    assert TechnicalAnalysisService._classify_stoch(19.99) == IndicatorSignal.BUY
    assert TechnicalAnalysisService._classify_stoch(20.0) == IndicatorSignal.NEUTRAL
    assert TechnicalAnalysisService._classify_stoch(80.0) == IndicatorSignal.NEUTRAL
    assert TechnicalAnalysisService._classify_stoch(80.01) == IndicatorSignal.SELL


def test_a_close_only_source_still_omits_the_stochastic_after_the_rename():
    """`_needs_high_low` drops the range oscillators BY NAME: a label renamed in one place
    only would let a close-only coin ship a stochastic built from fabricated ranges."""
    closes = 100.0 + 4.0 * np.sin(np.arange(300) * 0.5) + np.linspace(0, 50, 300)
    idx = pd.date_range("2025-01-01", periods=300, freq="D")
    df = pd.DataFrame({"open": np.nan, "high": np.nan, "low": np.nan, "close": closes,
                       "volume": 1_000.0}, index=idx)
    svc = object.__new__(TechnicalAnalysisService)
    _result, _, _mas, oscs = svc._compute_timeframe_signal(df)
    names = {o.name for o in oscs}
    assert STOCH_INDICATOR_NAME not in names
    assert not any(n.startswith("Stoch(") for n in names), names
    assert "StochRSI(14)" in names, "the close-only oscillators must survive (anti-vacuity)"


def test_no_stochastic_label_is_spelled_out_beside_the_constant():
    """One spelling of the label in the service: the constant's own definition."""
    src = inspect.getsource(TA)
    code = "".join(
        tok.string if tok.type != tokenize.COMMENT else ""
        for tok in tokenize.generate_tokens(io.StringIO(src).readline)
        if tok.type in (tokenize.STRING, tokenize.COMMENT)
    )
    literals = re.findall(r'"Stoch\(14,3(?:,3)?\)"', code)
    assert literals == ['"Stoch(14,3,3)"'], literals
    assert inspect.getsource(TA).count("STOCH_INDICATOR_NAME") >= 3, (
        "the constant must be used for the row label AND the close-only drop set"
    )
