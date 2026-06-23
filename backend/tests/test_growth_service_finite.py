"""Non-finite (NaN/Inf) guard for growth_service point values.

growth_service feeds the Growth tab AND the report's frozen ``growth_chart``.
``GrowthDataPointSchema.value`` is a non-optional float, and the frozen report is
persisted to a Postgres JSONB column (which rejects bare ``NaN``/``Infinity``).
A non-finite upstream FMP value must therefore be coerced to None and dropped at
the point level, never carried into a charted/persisted value. Pure functions —
no network.
"""

import math

from app.services.growth_service import _compute_growth_points, _compute_yoy, _safe_float


def test_compute_yoy_sign_corrected_for_negative_bases():
    """YoY uses abs(previous) so the SIGN is always meaningful (improvement = +,
    deterioration = −) at any sign, and the value is shown verbatim (not nulled)."""
    # Positive base → standard %.
    assert _compute_yoy(120.0, 100.0) == 20.0
    # Deepening loss (worse): correct NEGATIVE (naive ÷ would falsely give +).
    assert _compute_yoy(-10_000.0, -362.0) < 0
    # Shrinking loss (better): POSITIVE.
    assert _compute_yoy(-3_000.0, -10_000.0) == 70.0
    # Sign-flip into loss: large but CORRECT negative, shown verbatim (not None).
    v = _compute_yoy(-3_000.0, 71.0)
    assert v is not None and v < -1000
    # Recovery into profit: positive.
    assert _compute_yoy(1_000.0, -3_000.0) > 0
    # Undefined only when an endpoint is missing or the base is exactly zero.
    assert _compute_yoy(100.0, 0.0) is None
    assert _compute_yoy(100.0, None) is None


def test_safe_float_coerces_non_finite_to_none():
    assert _safe_float({"v": float("nan")}, "v") is None
    assert _safe_float({"v": float("inf")}, "v") is None
    assert _safe_float({"v": float("-inf")}, "v") is None
    # FMP string literals.
    assert _safe_float({"v": "NaN"}, "v") is None
    assert _safe_float({"v": "Infinity"}, "v") is None
    assert _safe_float({"v": "-Infinity"}, "v") is None
    # Finite values (incl. negatives and zero) pass through unchanged.
    assert _safe_float({"v": 42.5}, "v") == 42.5
    assert _safe_float({"v": -3.0}, "v") == -3.0
    assert _safe_float({"v": 0}, "v") == 0.0
    # Missing / non-numeric → None (unchanged behaviour).
    assert _safe_float({"v": None}, "v") is None
    assert _safe_float({}, "v") is None
    assert _safe_float({"v": "abc"}, "v") is None


def test_compute_growth_points_skips_non_finite_value_annual():
    """A NaN revenue year is dropped (no point), so a non-finite value can never
    reach the frozen growth_chart and break the JSONB write."""
    records = [
        {"calendarYear": "2022", "date": "2022-12-31", "revenue": 90.0},
        {"calendarYear": "2023", "date": "2023-12-31", "revenue": 100.0},
        {"calendarYear": "2024", "date": "2024-12-31", "revenue": float("nan")},
    ]
    pts = _compute_growth_points(records, "revenue", is_quarterly=False)
    periods = [p["period"] for p in pts]
    # 2023 has prior 2022 → real point; 2024 is NaN → skipped (not a NaN point).
    assert periods == ["2023"]
    assert all(math.isfinite(p["value"]) for p in pts)
    assert pts[0]["value"] == 100.0


def test_compute_growth_points_annual_year_gap_keeps_bar_null_yoy():
    """A multi-year gap must NOT drop the gap year's bar (the old `continue`
    silently lost a real, finite value). The gap year charts with its value and a
    null YoY (a discontinuity is not zero growth), so the yellow line just breaks."""
    records = [
        {"calendarYear": "2020", "date": "2020-12-31", "revenue": 100.0},
        {"calendarYear": "2021", "date": "2021-12-31", "revenue": 120.0},
        {"calendarYear": "2024", "date": "2024-12-31", "revenue": 200.0},  # 2022/2023 missing
        {"calendarYear": "2025", "date": "2025-12-31", "revenue": 220.0},
    ]
    pts = _compute_growth_points(records, "revenue", is_quarterly=False)
    by = {p["period"]: p for p in pts}
    # 2020 = YoY baseline (not charted); 2021/2024/2025 all get bars.
    assert set(by) == {"2021", "2024", "2025"}
    assert by["2021"]["yoy_change_percent"] == 20.0   # consecutive → YoY
    assert by["2024"]["value"] == 200.0               # gap year: bar PRESENT (was dropped)
    assert by["2024"]["yoy_change_percent"] is None   # gap → YoY null (line breaks)
    assert by["2025"]["yoy_change_percent"] == 10.0    # consecutive again


def test_compute_growth_points_skips_non_finite_value_quarterly():
    records = [
        {"calendarYear": "2023", "period": "Q1", "date": "2023-03-31", "revenue": 50.0},
        {"calendarYear": "2024", "period": "Q1", "date": "2024-03-31", "revenue": float("inf")},
    ]
    pts = _compute_growth_points(records, "revenue", is_quarterly=True)
    assert all(math.isfinite(p["value"]) for p in pts)
    # The Inf Q1'24 is dropped; Q1'23 has no prior → no point. Result empty, no crash.
    assert pts == []
