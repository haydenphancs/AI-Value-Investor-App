"""A NaN must never reach a published sector median — it winsorizes to the CEILING.

`max(-500.0, min(500.0, nan))` evaluates to **500.0**. Every comparison against NaN is
False, so `min` returns its other operand and `max` does too. A missing/garbage field
therefore became the single most extreme positive growth reading in the sample and dragged
the sector median up — permanently, because historical benchmark rows are never recomputed
(`existing_periods` skips them).

Two things let it in, and both are the documented traps:
  • `except (ValueError, TypeError)` does not catch a NaN — `float("NaN")` succeeds.
  • `prev_val != 0` is True for NaN, so the YoY guard admits `(nan - nan) / abs(nan)`.

`profit_power_service._safe_float` had the `isfinite` guard all along; this twin did not.
"""
from __future__ import annotations

import math
import statistics

import pytest

from app.services.sector_benchmark_service import (
    MIN_SAMPLE_SIZE,
    WINSORIZE_CEIL,
    WINSORIZE_FLOOR,
    _compute_yoy_for_records,
    _safe_float,
    _winsorize,
)

NAN = float("nan")
INF = float("inf")


# ── the arithmetic that made this invisible ──────────────────────────────────

def test_the_clamp_really_does_turn_a_nan_into_the_ceiling():
    """Pinning the behaviour the fix exists for, so the test above cannot be misread as
    theoretical. If Python ever changed this, the guard would still be right — but the
    rationale would need rewriting."""
    assert max(WINSORIZE_FLOOR, min(WINSORIZE_CEIL, NAN)) == WINSORIZE_CEIL


def test_a_single_nan_poisons_an_unfiltered_median():
    assert math.isnan(statistics.median([1.0, 2.0, NAN, 3.0, 4.0]))


# ── the guards ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [NAN, INF, -INF, "NaN", "nan", "inf", "-Infinity"])
def test_safe_float_rejects_every_non_finite_spelling(raw):
    """String spellings matter: FMP returns JSON, and `float("NaN")` parses happily."""
    assert _safe_float({"x": raw}, "x") is None


@pytest.mark.parametrize("raw,expected", [
    (0, 0.0), (-12.5, -12.5), ("3.25", 3.25), (0.0, 0.0),
])
def test_safe_float_still_accepts_real_numbers(raw, expected):
    """Including 0 and negatives — "unknown" must not swallow "measured zero"."""
    assert _safe_float({"x": raw}, "x") == expected


def test_winsorize_drops_non_finite_rather_than_clamping_it():
    out = _winsorize([10.0, NAN, -20.0, INF, 30.0])
    assert out == [10.0, -20.0, 30.0]
    assert WINSORIZE_CEIL not in out, "a NaN was clamped to the ceiling"


def test_winsorize_still_clamps_real_extremes():
    """The fix must not disarm winsorization itself."""
    assert _winsorize([-9999.0, 9999.0, 5.0]) == [WINSORIZE_FLOOR, WINSORIZE_CEIL, 5.0]


def test_winsorize_honours_a_custom_band_while_dropping_non_finite():
    assert _winsorize([NAN, 500.0, -3.0], floor=0.0, ceil=200.0) == [200.0, 0.0]


# ── the upstream path that produced the NaN ──────────────────────────────────

def _rec(year, value):
    return {"calendarYear": str(year), "date": f"{year}-12-31", "revenue": value}


def test_a_nan_field_produces_no_yoy_point_at_all():
    """`prev_val != 0` is True for NaN, so without the `_safe_float` guard this yields
    `(nan - nan) / abs(nan) * 100` = nan, admitted as a growth rate."""
    points = _compute_yoy_for_records(
        [_rec(2023, NAN), _rec(2024, NAN)], "revenue", is_quarterly=False,
    )
    assert points == {}


def test_a_nan_current_year_against_a_real_prior_is_also_dropped():
    points = _compute_yoy_for_records(
        [_rec(2023, 100.0), _rec(2024, NAN)], "revenue", is_quarterly=False,
    )
    assert all(math.isfinite(v) for v in points.values())
    assert points == {}


def test_a_real_yoy_still_computes():
    points = _compute_yoy_for_records(
        [_rec(2023, 100.0), _rec(2024, 120.0)], "revenue", is_quarterly=False,
    )
    assert points == {"2024": 20.0}


# ── the sample-size gate must survive the filter ─────────────────────────────

def test_the_min_sample_size_is_measured_on_what_actually_survives():
    """The gate ran on the RAW list while `sample_size` stored `len(cleaned)`. With the
    winsorizer now dropping values, that combination would publish a median whose own
    stored sample size is below the minimum — a row that reads authoritative and is not."""
    raw = [1.0] * (MIN_SAMPLE_SIZE - 1) + [NAN, NAN, NAN]
    assert len(raw) >= MIN_SAMPLE_SIZE          # would have passed a raw-length gate
    assert len(_winsorize(raw)) < MIN_SAMPLE_SIZE
