"""
Unit tests for the deterministic math in TickerReportDataCollector.

These pin the edge-case behavior that the cross-view audit found
weakest: Earnings Yield with negative/zero PE, CAGR direction
regardless of FMP order, unit-aware forecast scaling, valuation vital
fallback when DCF is missing.

No network, no Supabase. Each test exercises a single helper in
isolation.
"""

from __future__ import annotations

import pytest

from app.services.agents.ticker_report_data_collector import (
    _build_revenue_forecast_partial,
    _build_valuation_vital,
    _safe_cagr,
    compute_earnings_yield,
)
from app.schemas.stock_overview import (
    SnapshotItemResponse,
    SnapshotMetricResponse,
)


# ── Earnings Yield ────────────────────────────────────────────────────


def test_earnings_yield_negative_pe():
    """Negative PE (negative earnings) → None, never a negative percent."""
    c = {"pe_ratio": -12.5}
    assert compute_earnings_yield(c) is None


def test_earnings_yield_zero_pe():
    """Zero PE → None (would otherwise divide by zero)."""
    c = {"pe_ratio": 0}
    assert compute_earnings_yield(c) is None


def test_earnings_yield_missing_pe():
    """Missing PE → None."""
    c = {}
    assert compute_earnings_yield(c) is None
    assert compute_earnings_yield({"pe_ratio": None}) is None


def test_earnings_yield_positive_pe():
    """PE 25 → 4.0% earnings yield."""
    c = {"pe_ratio": 25.0}
    assert compute_earnings_yield(c) == 4.0


def test_earnings_yield_high_pe():
    """High PE (low yield) — 100 PE → 1.0%."""
    c = {"pe_ratio": 100.0}
    assert compute_earnings_yield(c) == 1.0


# ── CAGR direction safety ─────────────────────────────────────────────


def test_cagr_negative_endpoints_returns_none():
    """Negative start or end → None (CAGR undefined for sign flips)."""
    assert _safe_cagr(-10.0, 100.0, 3) is None
    assert _safe_cagr(100.0, -10.0, 3) is None


def test_cagr_zero_endpoint_returns_none():
    assert _safe_cagr(0.0, 100.0, 3) is None
    assert _safe_cagr(100.0, 0.0, 3) is None


def test_cagr_correct_direction():
    """100 → 200 over 3 periods (2 years of growth) → ~41.4% CAGR."""
    result = _safe_cagr(100.0, 200.0, 3)
    assert result is not None
    assert 41.0 < result < 42.0


# ── Forecast scaling ──────────────────────────────────────────────────


def test_forecast_picks_billions_for_large_revenue():
    """A $100B forecast should plot in Billions, not collapse to 0.0."""
    estimates = [
        {"date": "2024-12-31", "estimatedRevenueAvg": 100_000_000_000, "estimatedEpsAvg": 5.0},
        {"date": "2025-12-31", "estimatedRevenueAvg": 110_000_000_000, "estimatedEpsAvg": 5.5},
        {"date": "2026-12-31", "estimatedRevenueAvg": 121_000_000_000, "estimatedEpsAvg": 6.0},
    ]
    result = _build_revenue_forecast_partial(estimates, 10.0, 9.5)
    revs = [p["revenue"] for p in result["projections"]]
    assert revs == [100.0, 110.0, 121.0]


def test_forecast_picks_millions_for_small_revenue():
    """A $500M forecast should plot in Millions (not 0.5 in billions)."""
    estimates = [
        {"date": "2024-12-31", "estimatedRevenueAvg": 500_000_000, "estimatedEpsAvg": 1.0},
        {"date": "2025-12-31", "estimatedRevenueAvg": 600_000_000, "estimatedEpsAvg": 1.2},
        {"date": "2026-12-31", "estimatedRevenueAvg": 720_000_000, "estimatedEpsAvg": 1.5},
    ]
    result = _build_revenue_forecast_partial(estimates, 20.0, 22.0)
    revs = [p["revenue"] for p in result["projections"]]
    assert revs == [500.0, 600.0, 720.0]


def test_forecast_sorted_oldest_to_newest():
    """Even when FMP returns newest-first, the chart reads left→right by year."""
    estimates = [
        {"date": "2026-12-31", "estimatedRevenueAvg": 121_000_000_000, "estimatedEpsAvg": 6.0},
        {"date": "2024-12-31", "estimatedRevenueAvg": 100_000_000_000, "estimatedEpsAvg": 5.0},
        {"date": "2025-12-31", "estimatedRevenueAvg": 110_000_000_000, "estimatedEpsAvg": 5.5},
    ]
    result = _build_revenue_forecast_partial(estimates, 10.0, 9.5)
    periods = [p["period"] for p in result["projections"]]
    assert periods == ["2024", "2025", "2026"]


# ── Valuation vital with snapshot fallback ────────────────────────────


def _snap(rating: int) -> SnapshotItemResponse:
    """Build a minimal valuation snapshot at the given rating."""
    return SnapshotItemResponse(
        category="Price",
        rating=rating,
        metrics=[SnapshotMetricResponse(name="P/E", value="22.0")],
        full_report_available=True,
    )


def test_valuation_vital_no_dcf_uses_snapshot():
    """When DCF is missing, snapshot rating drives status."""
    result = _build_valuation_vital(
        current_price=100.0,
        fair_value=None,
        upside=None,
        valuation_snapshot=_snap(rating=5),
    )
    assert result["status"] == "underpriced"
    assert result["upside_potential"] == 10.0


def test_valuation_vital_no_dcf_no_snapshot_neutral():
    """No DCF + no snapshot → honest fair_value default."""
    result = _build_valuation_vital(
        current_price=100.0, fair_value=None, upside=None, valuation_snapshot=None,
    )
    assert result["status"] == "fair_value"
    assert result["upside_potential"] == 0.0


def test_valuation_vital_dcf_overrides_when_snapshot_agrees():
    """DCF + agreeing snapshot → status uses DCF, full upside preserved."""
    result = _build_valuation_vital(
        current_price=100.0,
        fair_value=140.0,
        upside=40.0,                      # +40% → deep_undervalued
        valuation_snapshot=_snap(rating=5),
    )
    assert result["status"] == "deep_undervalued"
    assert result["upside_potential"] == 40.0


def test_valuation_vital_snapshot_disagreement_softens_dcf():
    """DCF says deep_undervalued but multi-metric snapshot says overpriced
    → status downgrades one level milder rather than trusting stale DCF."""
    result = _build_valuation_vital(
        current_price=100.0,
        fair_value=140.0,
        upside=40.0,                      # DCF: deep_undervalued (level 4)
        valuation_snapshot=_snap(rating=2),  # Snapshot: overpriced (level 1) → diff 3
    )
    # diff 3 ≥ 2 → downgrade one level toward snapshot → underpriced (level 3)
    assert result["status"] == "underpriced"
    # Upside numeric stays as-is so the user sees the DCF-implied number.
    assert result["upside_potential"] == 40.0
