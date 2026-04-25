"""
Portfolio Insights Service — server-side diversification scoring.

Mirrors the Swift ``DiversificationCalculator`` so iOS and the backend agree on
the same number. The algorithm uses three additive buckets totalling 100 points:

  Bucket 1 — Single Asset Concentration  (40 pts)
  Bucket 2 — Sector Weighting             (40 pts)
  Bucket 3 — Asset Class & Geography      (20 pts)

For holdings with ``shares`` set, ``market_value`` is recomputed from the
current FMP price before scoring so the result stays accurate as the market
moves. Holdings with ``shares = NULL`` keep their static stored value.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.schemas.tracking import (
    DiversificationSubScoresResponse,
    PortfolioHoldingResponse,
    PortfolioInsightsResponse,
    SectorAllocationResponse,
)

logger = logging.getLogger(__name__)


# ── Tunable thresholds ──────────────────────────────────────────────
# Keep these in sync with frontend/ios/.../DiversificationCalculator.swift —
# any change here requires the iOS constants to follow.

MIN_HOLDINGS = 2

# Bucket 1 — concentration
SINGLE_HEALTHY_LIMIT = 0.10        # 10%
SINGLE_SEVERE_LIMIT = 0.30         # 30%
SINGLE_PENALTY_PER_PCT = 1.0
SINGLE_SEVERE_MULTIPLIER = 1.5

# Bucket 2 — sector
SECTOR_HEALTHY_LIMIT = 0.25        # 25%
SECTOR_SEVERE_LIMIT = 0.50         # 50%
SECTOR_PENALTY_PER_PCT = 0.8
SECTOR_SEVERE_MULTIPLIER = 1.5

# Bucket 3 — diversity
POINTS_PER_ASSET_CLASS = 4.0
CLASS_POINTS_CAP = 12.0
INTERNATIONAL_BONUS = 5.0
INTERNATIONAL_SIGNIFICANT_BONUS = 3.0
INTERNATIONAL_SIGNIFICANT_THRESHOLD = 0.20

BUCKET1_MAX = 40.0
BUCKET2_MAX = 40.0
BUCKET3_MAX = 20.0


# ── Asset-type → asset-class mapping ────────────────────────────────
# Mirrors AssetType.assetClass in PortfolioHoldingModels.swift. The bucket-3
# scoring rewards holding a mix of these classes.

_ASSET_CLASS = {
    "Stock": "equity",
    "International Stock": "equity",
    "ETF": "etf",
    "Bond": "fixedIncome",
    "Crypto": "alternative",
    "Cash": "cash",
}


def _asset_class(asset_type: Optional[str]) -> str:
    return _ASSET_CLASS.get(asset_type or "Stock", "equity")


# ── Public API ──────────────────────────────────────────────────────


class PortfolioInsightsService:
    """Compute the Portfolio Insights payload for a user."""

    async def get_holdings(
        self, user_id: str
    ) -> List[PortfolioHoldingResponse]:
        """Fetch holdings and refresh ``market_value`` from FMP for share-based
        rows. Static rows pass through unchanged."""
        sb = get_supabase()
        result = (
            sb.table("portfolio_holdings")
            .select("*")
            .eq("user_id", user_id)
            .order("market_value", desc=True)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return []

        # Refresh prices for share-based rows in one parallel batch.
        share_tickers = [r["ticker"] for r in rows if r.get("shares")]
        price_map = await self._fetch_prices(share_tickers)

        out: List[PortfolioHoldingResponse] = []
        for row in rows:
            stored_value = float(row.get("market_value") or 0)
            shares = row.get("shares")
            if shares and row["ticker"] in price_map:
                live_value = float(shares) * price_map[row["ticker"]]
                market_value = live_value
            else:
                market_value = stored_value

            out.append(
                PortfolioHoldingResponse(
                    id=str(row["id"]),
                    ticker=row["ticker"],
                    company_name=row.get("company_name") or row["ticker"],
                    market_value=market_value,
                    shares=float(shares) if shares is not None else None,
                    sector=row.get("sector"),
                    asset_type=row.get("asset_type") or "Stock",
                    country=row.get("country") or "US",
                )
            )
        return out

    async def compute_insights(
        self, user_id: str
    ) -> Optional[PortfolioInsightsResponse]:
        """Return the full insights payload, or ``None`` if the user has fewer
        than ``MIN_HOLDINGS`` positions or the total value is non-positive."""
        holdings = await self.get_holdings(user_id)
        if len(holdings) < MIN_HOLDINGS:
            return None

        total_value = sum(h.market_value for h in holdings)
        if total_value <= 0:
            return None

        weights = [h.market_value / total_value for h in holdings]

        bucket1 = _concentration_score(weights)
        bucket2 = _sector_score(holdings, weights)
        bucket3 = _diversity_score(holdings, weights)

        total = round(bucket1 + bucket2 + bucket3)
        score = max(0, min(100, int(total)))

        allocations = _build_sector_allocations(holdings, weights)
        message = _generate_message(score, len(allocations), holdings, weights)

        return PortfolioInsightsResponse(
            score=score,
            message=message,
            sector_count=len(allocations),
            allocations=allocations,
            sub_scores=DiversificationSubScoresResponse(
                concentration_score=int(round(bucket1)),
                sector_score=int(round(bucket2)),
                diversity_score=int(round(bucket3)),
            ),
            holdings_count=len(holdings),
            total_value=total_value,
        )

    # ── Internals ──────────────────────────────────────────────────

    async def _fetch_prices(self, tickers: List[str]) -> Dict[str, float]:
        """Fetch current FMP prices for the given tickers in parallel.
        Returns a ticker→price map. Tickers whose quote fails are omitted —
        the caller falls back to the stored ``market_value`` for those rows."""
        if not tickers:
            return {}

        fmp = get_fmp_client()

        async def _one(t: str) -> Tuple[str, Optional[float]]:
            try:
                quote = await fmp.get_stock_price_quote(t)
                price = quote.get("price") if quote else None
                return t, float(price) if price else None
            except Exception as e:
                logger.warning(
                    "[portfolio_insights] Quote fetch failed for %s: %s", t, e
                )
                return t, None

        results = await asyncio.gather(*[_one(t) for t in tickers])
        return {t: p for t, p in results if p is not None and p > 0}


# ── Bucket scoring ──────────────────────────────────────────────────


def _concentration_score(weights: List[float]) -> float:
    """Bucket 1: deduct 40 points-worth of penalty for over-concentrated
    individual positions. Penalty accelerates above ``SINGLE_SEVERE_LIMIT``."""
    score = BUCKET1_MAX
    for w in weights:
        excess = w - SINGLE_HEALTHY_LIMIT
        if excess <= 0:
            continue

        if w > SINGLE_SEVERE_LIMIT:
            normal = (SINGLE_SEVERE_LIMIT - SINGLE_HEALTHY_LIMIT) * 100.0
            severe = (w - SINGLE_SEVERE_LIMIT) * 100.0
            penalty = (
                normal * SINGLE_PENALTY_PER_PCT
                + severe * SINGLE_PENALTY_PER_PCT * SINGLE_SEVERE_MULTIPLIER
            )
        else:
            penalty = excess * 100.0 * SINGLE_PENALTY_PER_PCT

        score -= penalty
    return max(0.0, score)


def _sector_score(
    holdings: List[PortfolioHoldingResponse], weights: List[float]
) -> float:
    """Bucket 2: same shape as Bucket 1 but on summed sector weights."""
    sector_weights: Dict[str, float] = {}
    for holding, w in zip(holdings, weights):
        key = holding.sector or "Other"
        sector_weights[key] = sector_weights.get(key, 0.0) + w

    score = BUCKET2_MAX
    for w in sector_weights.values():
        excess = w - SECTOR_HEALTHY_LIMIT
        if excess <= 0:
            continue

        if w > SECTOR_SEVERE_LIMIT:
            normal = (SECTOR_SEVERE_LIMIT - SECTOR_HEALTHY_LIMIT) * 100.0
            severe = (w - SECTOR_SEVERE_LIMIT) * 100.0
            penalty = (
                normal * SECTOR_PENALTY_PER_PCT
                + severe * SECTOR_PENALTY_PER_PCT * SECTOR_SEVERE_MULTIPLIER
            )
        else:
            penalty = excess * 100.0 * SECTOR_PENALTY_PER_PCT

        score -= penalty
    return max(0.0, score)


def _diversity_score(
    holdings: List[PortfolioHoldingResponse], weights: List[float]
) -> float:
    """Bucket 3: additive points for asset-class mix + international exposure."""
    classes = {_asset_class(h.asset_type) for h in holdings}
    score = min(len(classes) * POINTS_PER_ASSET_CLASS, CLASS_POINTS_CAP)

    international_weight = sum(
        w for h, w in zip(holdings, weights) if (h.country or "US") != "US"
    )
    if international_weight > 0:
        score += INTERNATIONAL_BONUS
        if international_weight >= INTERNATIONAL_SIGNIFICANT_THRESHOLD:
            score += INTERNATIONAL_SIGNIFICANT_BONUS

    return min(score, BUCKET3_MAX)


# ── Sector allocations + message ────────────────────────────────────


def _build_sector_allocations(
    holdings: List[PortfolioHoldingResponse], weights: List[float]
) -> List[SectorAllocationResponse]:
    sector_weights: Dict[str, float] = {}
    for holding, w in zip(holdings, weights):
        key = holding.sector or "Other"
        sector_weights[key] = sector_weights.get(key, 0.0) + w

    return [
        SectorAllocationResponse(name=name, percentage=weight * 100.0)
        for name, weight in sorted(
            sector_weights.items(), key=lambda kv: kv[1], reverse=True
        )
    ]


def _generate_message(
    score: int,
    sector_count: int,
    holdings: List[PortfolioHoldingResponse],
    weights: List[float],
) -> str:
    if score >= 80:
        return f"Excellent diversification across {sector_count} sectors"
    if score >= 60:
        return f"Well-diversified across {sector_count} sectors"
    if score >= 40:
        # Surface the worst offender if any single position is over 25%.
        if holdings and weights:
            top_idx = max(range(len(weights)), key=lambda i: weights[i])
            if weights[top_idx] > 0.25:
                pct = int(weights[top_idx] * 100)
                return (
                    f"Consider reducing {holdings[top_idx].ticker} "
                    f"concentration ({pct}%)"
                )
        return "Moderate diversification — consider spreading across more sectors"
    return "High concentration risk — diversify across more sectors and asset classes"
