"""
Schema-parity test for Portfolio Insights.

Pins the `PortfolioInsightsResponse` shape against the iOS Swift Codable
decoder (frontend/ios/ios/Models/TrackingModels.swift). A renamed/dropped key
crashes the diversification card on decode, so this fails before the app does.

Scoring math + edge cases live in test_portfolio_insights_scoring.py.
No network / Supabase — `score_holdings` is pure and holdings are built inline.
"""

from __future__ import annotations

from app.schemas.tracking import PortfolioHoldingResponse
from app.services.portfolio_insights_service import score_holdings


def _h(ticker, value, sector="Technology", country="US", market_cap=300e9):
    return PortfolioHoldingResponse(
        id=ticker, ticker=ticker, company_name=ticker, market_value=value,
        shares=None, sector=sector, asset_type="Stock", country=country,
        market_cap=market_cap,
    )


# Mirror the CodingKeys in TrackingModels.swift — keep in lockstep.
EXPECTED_TOP_LEVEL = {
    "score",
    "zone",
    "effective_holdings",
    "message",
    "sector_count",
    "sub_scores",
    "sector_allocations",
    "marketcap_allocations",
    "holdings_count",
    "total_value",
}
EXPECTED_SUBSCORE_KEYS = {"key", "label", "points", "max_points", "zone"}
EXPECTED_ALLOCATION_KEYS = {"name", "percentage"}


def test_portfolio_insights_schema_parity():
    res = score_holdings([
        _h("ORCL", 12_000, sector="Technology", market_cap=400e9),
        _h("JNJ", 3_000, sector="Healthcare", country="US", market_cap=380e9),
        _h("NESN", 2_000, sector="Consumer Defensive", country="US", market_cap=2e9),
    ])
    assert res is not None
    payload = res.model_dump()

    assert set(payload.keys()) == EXPECTED_TOP_LEVEL

    assert payload["sub_scores"], "expected at least one sub-score"
    for sub in payload["sub_scores"]:
        assert set(sub.keys()) == EXPECTED_SUBSCORE_KEYS

    for field in ("sector_allocations", "marketcap_allocations"):
        assert payload[field], f"expected non-empty {field}"
        for alloc in payload[field]:
            assert set(alloc.keys()) == EXPECTED_ALLOCATION_KEYS

    # Types the Swift decoder is strict about.
    assert isinstance(payload["score"], int)
    assert isinstance(payload["zone"], str)
    assert isinstance(payload["effective_holdings"], float)
    assert isinstance(payload["total_value"], float)
    # Bars add up to the overall score.
    assert sum(s["points"] for s in payload["sub_scores"]) == payload["score"]
