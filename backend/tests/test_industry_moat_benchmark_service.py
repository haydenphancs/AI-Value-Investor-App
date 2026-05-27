"""Unit tests for industry_moat_benchmark_service helpers.

These cover the pure math (winsorize + percentile + sample-size
threshold). No Supabase, no FMP — the heavy compute path is exercised
end-to-end via the bootstrap script and manual verification.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `import app...` work without a conftest.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.industry_moat_benchmark_service import (
    MIN_SAMPLE_SIZE,
    _percentile,
    _winsorize_p10_p90,
)


# ── _winsorize_p10_p90 ──────────────────────────────────────────────


def test_winsorize_below_10_samples_returns_input_unchanged():
    # Percentile-based winsorization on tiny samples is more noise than
    # signal; the helper should pass through unchanged.
    vals = [1.0, 2.0, 9.5, 10.0]
    assert _winsorize_p10_p90(vals) == vals


def test_winsorize_caps_extreme_values_at_p10_p90():
    # 20 values: 0..19. p10 ≈ 2.0, p90 ≈ 18.0.
    vals = [float(i) for i in range(20)]
    out = _winsorize_p10_p90(vals)
    # Lowest values clamped up to p10 (~2.0), highest clamped down to p90 (~18.0).
    assert min(out) == 2.0
    assert max(out) == 18.0
    # Order preserved (caller may correlate with per-ticker metadata).
    assert len(out) == len(vals)
    # Middle values untouched.
    assert out[10] == 10.0


def test_winsorize_preserves_count_and_dtype():
    vals = [1.5, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 9.5]
    out = _winsorize_p10_p90(vals)
    assert len(out) == 10
    for v in out:
        assert isinstance(v, float)


# ── _percentile ─────────────────────────────────────────────────────


def test_percentile_empty_returns_none():
    assert _percentile([], 0.5) is None


def test_percentile_single_value():
    assert _percentile([4.2], 0.5) == 4.2
    assert _percentile([4.2], 0.25) == 4.2


def test_percentile_linear_interpolation():
    # Sorted [0,1,2,3,4,5,6,7,8,9]. p50 should be 4.5 (midpoint).
    assert _percentile([float(i) for i in range(10)], 0.50) == pytest.approx(4.5)
    # p25 ≈ 2.25, p75 ≈ 6.75.
    assert _percentile([float(i) for i in range(10)], 0.25) == pytest.approx(2.25)
    assert _percentile([float(i) for i in range(10)], 0.75) == pytest.approx(6.75)


def test_percentile_edges():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(vals, 0.0) == 1.0
    assert _percentile(vals, 1.0) == 5.0


# ── MIN_SAMPLE_SIZE ─────────────────────────────────────────────────


def test_min_sample_size_matches_sector_benchmark_constant():
    # The threshold mirrors sector_benchmark_service.MIN_SAMPLE_SIZE so
    # the two related tables behave consistently for sparse industries.
    from app.services.sector_benchmark_service import (
        MIN_SAMPLE_SIZE as SECTOR_MIN,
    )
    assert MIN_SAMPLE_SIZE == SECTOR_MIN == 5
