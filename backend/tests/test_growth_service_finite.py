"""Non-finite (NaN/Inf) guard for growth_service point values.

growth_service feeds the Growth tab AND the report's frozen ``growth_chart``.
``GrowthDataPointSchema.value`` is a non-optional float, and the frozen report is
persisted to a Postgres JSONB column (which rejects bare ``NaN``/``Infinity``).
A non-finite upstream FMP value must therefore be coerced to None and dropped at
the point level, never carried into a charted/persisted value. Pure functions —
no network.
"""

import math

from app.services.growth_service import _compute_growth_points, _safe_float


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


def test_compute_growth_points_skips_non_finite_value_quarterly():
    records = [
        {"calendarYear": "2023", "period": "Q1", "date": "2023-03-31", "revenue": 50.0},
        {"calendarYear": "2024", "period": "Q1", "date": "2024-03-31", "revenue": float("inf")},
    ]
    pts = _compute_growth_points(records, "revenue", is_quarterly=True)
    assert all(math.isfinite(p["value"]) for p in pts)
    # The Inf Q1'24 is dropped; Q1'23 has no prior → no point. Result empty, no crash.
    assert pts == []
