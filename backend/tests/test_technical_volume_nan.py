"""Outlier guard for Technical Analysis volume metrics.

The DataFrame is dropna'd on `close` only, so the latest bar can carry a NaN
volume (pd.to_numeric coerced a missing/non-numeric value). `NaN or 0` is NaN
(truthy), which would land in current_volume and serialize to an invalid-JSON
`NaN` token — crashing the iOS decode of the whole Technical Analysis screen.
_compute_volume_analysis must degrade a NaN volume to 0.0 like every other
indicator in the file (which already route through _safe_float).
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from app.services.technical_analysis_service import TechnicalAnalysisService


def _ohlcv(n: int) -> pd.DataFrame:
    close = np.linspace(100.0, 120.0, n)
    return pd.DataFrame({
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": np.full(n, 1_000_000.0),
    })


def test_volume_analysis_nan_latest_volume_degrades_to_finite():
    df = _ohlcv(40)
    df.loc[df.index[-1], "volume"] = np.nan  # FMP omitted the latest bar's volume

    result = TechnicalAnalysisService._compute_volume_analysis(df)

    assert math.isfinite(result.current_volume)       # not NaN → JSON-safe
    assert result.current_volume == 0.0               # NaN degraded to 0.0
    assert math.isfinite(result.current_volume_change)

    # The full model must serialize as strict JSON (no NaN/Inf token that iOS rejects).
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_volume_analysis_happy_path_unchanged():
    df = _ohlcv(40)
    result = TechnicalAnalysisService._compute_volume_analysis(df)
    assert result.current_volume == 1_000_000.0
    assert math.isfinite(result.money_flow_index)
    assert math.isfinite(result.obv)
