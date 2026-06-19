"""
Schema parity + scoring math tests for Portfolio Insights.

Two jobs:
  1. Pin the `PortfolioInsightsResponse` shape against the iOS Swift Codable
     decoder. A renamed/dropped field crashes the diversification card on
     decode, so this fails before the app does.
  2. Exercise the pure `score_holdings` math — including the regression that
     started this work: the score must RESPOND to weight changes (the old
     rubric returned the same number for 20 vs 100 shares), and real sectors
     must not collapse into "Other".

No network / Supabase — `score_holdings` is pure and holdings are built inline.
"""

from __future__ import annotations

import pytest

from app.schemas.tracking import PortfolioHoldingResponse
from app.services.portfolio_insights_service import (
    effective_holdings,
    hhi,
    normalized_hhi_score,
    score_holdings,
)


def _h(
    ticker: str,
    value: float,
    sector: str = "Technology",
    country: str = "US",
    market_cap: float = 300_000_000_000.0,
) -> PortfolioHoldingResponse:
    return PortfolioHoldingResponse(
        id=ticker,
        ticker=ticker,
        company_name=ticker,
        market_value=value,
        shares=None,
        sector=sector,
        asset_type="Stock",
        country=country,
        market_cap=market_cap,
    )


# ── Math ────────────────────────────────────────────────────────────


def test_normalized_hhi_score_monotonic():
    # Equal weights → perfectly diversified on this axis.
    assert normalized_hhi_score([0.5, 0.5], 2) == pytest.approx(100.0)
    assert normalized_hhi_score([0.25] * 4, 4) == pytest.approx(100.0)
    # Skew lowers the score, but stays in-range.
    skew = normalized_hhi_score([0.9, 0.1], 2)
    assert 0.0 < skew < 100.0
    # More skew → lower score (monotonic).
    assert normalized_hhi_score([0.95, 0.05], 2) < skew
    # Single bucket → zero (can't diversify one thing).
    assert normalized_hhi_score([1.0], 1) == 0.0


def test_effective_holdings_is_inverse_hhi():
    assert effective_holdings([0.5, 0.5]) == pytest.approx(2.0)
    assert effective_holdings([0.25] * 4) == pytest.approx(4.0)
    w = [0.6, 0.3, 0.1]
    assert effective_holdings(w) == pytest.approx(1.0 / hhi(w))


def test_min_holdings_returns_none():
    assert score_holdings([]) is None
    assert score_holdings([_h("ORCL", 1000)]) is None


# ── The original bug: score must respond to weights ─────────────────


def test_score_responds_to_share_weight_change():
    """ORCL + CRM, both Technology. The score must change when the weight split
    changes (20 vs 100 shares). This is the regression that motivated the work."""
    skewed = score_holdings([_h("ORCL", 12_000), _h("CRM", 3_000)])      # ~80/20
    balanced = score_holdings([_h("ORCL", 12_000), _h("CRM", 12_000)])   # 50/50
    assert skewed is not None and balanced is not None
    assert balanced.score > skewed.score
    # And the position sub-score in particular must move.
    pos_skew = next(s for s in skewed.sub_scores if s.key == "position")
    pos_bal = next(s for s in balanced.sub_scores if s.key == "position")
    assert pos_bal.score > pos_skew.score


def test_real_sectors_do_not_collapse_to_other():
    res = score_holdings([
        _h("ORCL", 10_000, sector="Technology"),
        _h("JNJ", 10_000, sector="Healthcare"),
    ])
    assert res is not None
    names = {a.name for a in res.sector_allocations}
    assert names == {"Technology", "Healthcare"}
    assert "Other" not in names
    assert res.sector_count == 2


def test_single_sector_triggers_nudge():
    res = score_holdings([_h("ORCL", 10_000), _h("CRM", 10_000)])  # both Tech
    assert res is not None
    titles = {n.title for n in res.nudges}
    assert "Add another sector" in titles


# ── Schema parity with the iOS Codable decoder ──────────────────────

# These mirror the CodingKeys in frontend/ios/ios/Models/TrackingModels.swift.
# Keep in lockstep — a missing key crashes the diversification card on decode.
EXPECTED_TOP_LEVEL = {
    "score",
    "grade",
    "zone",
    "effective_holdings",
    "message",
    "sector_count",
    "sub_scores",
    "sector_allocations",
    "marketcap_allocations",
    "region_allocations",
    "nudges",
    "holdings_count",
    "total_value",
}
EXPECTED_SUBSCORE_KEYS = {"key", "label", "score", "zone"}
EXPECTED_ALLOCATION_KEYS = {"name", "percentage"}
EXPECTED_NUDGE_KEYS = {"severity", "title", "detail"}


def test_portfolio_insights_schema_parity():
    res = score_holdings([
        _h("ORCL", 12_000, sector="Technology", market_cap=400e9),
        _h("JNJ", 3_000, sector="Healthcare", country="US", market_cap=380e9),
        _h("NESN", 2_000, sector="Consumer Defensive", country="CH", market_cap=300e9),
    ])
    assert res is not None
    payload = res.model_dump()

    assert set(payload.keys()) == EXPECTED_TOP_LEVEL

    assert payload["sub_scores"], "expected at least one sub-score"
    for sub in payload["sub_scores"]:
        assert set(sub.keys()) == EXPECTED_SUBSCORE_KEYS

    for field in ("sector_allocations", "marketcap_allocations", "region_allocations"):
        assert payload[field], f"expected non-empty {field}"
        for alloc in payload[field]:
            assert set(alloc.keys()) == EXPECTED_ALLOCATION_KEYS

    for nudge in payload["nudges"]:
        assert set(nudge.keys()) == EXPECTED_NUDGE_KEYS

    # Spot-check types the Swift decoder is strict about.
    assert isinstance(payload["score"], int)
    assert isinstance(payload["grade"], str)
    assert isinstance(payload["effective_holdings"], float)
    assert isinstance(payload["total_value"], float)
