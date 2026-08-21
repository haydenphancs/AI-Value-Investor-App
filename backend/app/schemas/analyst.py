"""
Analyst Analysis schemas — response models for GET /stocks/{ticker}/analyst-analysis.

All field names use snake_case. The Swift frontend decodes via Codable DTO
structs with CodingKeys mapping to these snake_case names.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class AnalystConsensus(str, Enum):
    STRONG_BUY = "STRONG BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG SELL"


class AnalystActionType(str, Enum):
    UPGRADE = "UPGRADE"
    DOWNGRADE = "DOWNGRADE"
    MAINTAIN = "MAINTAIN"
    INITIATED = "INITIATED"
    REITERATED = "REITERATED"


class AnalystRatingDistribution(BaseModel):
    label: str   # "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"
    count: int


class AnalystPriceTarget(BaseModel):
    low_price: float
    average_price: float
    high_price: float
    current_price: float


class AnalystMomentumMonth(BaseModel):
    month: str            # "Jul", "Aug", etc.
    positive_count: int
    negative_count: int


class AnalystActionsSummary(BaseModel):
    upgrades: int
    maintains: int
    downgrades: int


class AnalystAction(BaseModel):
    firm_name: str
    action_type: AnalystActionType
    date: str                                  # ISO date "2026-01-12"
    previous_rating: Optional[str] = None      # raw grade string from FMP
    new_rating: str                            # raw grade string from FMP
    previous_price_target: Optional[float] = None
    new_price_target: Optional[float] = None


class AnalystAnalysisResponse(BaseModel):
    """Top-level response for GET /stocks/{ticker}/analyst-analysis."""

    symbol: str
    total_analysts: int
    updated_date: str                          # ISO date string
    consensus: AnalystConsensus
    # False when NO analyst covers this ticker: FMP returns `[]` for both /grades and
    # /price-target-consensus (verified live on AACT, a real NYSE listing). The numeric
    # fields below then default to 0.0 / HOLD, which is indistinguishable from a real
    # consensus of Hold at a $0.00 target — so the card presented a fabricated verdict
    # for a company no analyst has an opinion on. Additive + defaulted, so an older
    # client is unaffected; a current one renders an honest empty state instead.
    has_coverage: bool = True
    target_price: float
    target_upside: float                       # percentage
    distributions: List[AnalystRatingDistribution]
    price_target: AnalystPriceTarget
    momentum_data: List[AnalystMomentumMonth]
    net_positive: int
    net_negative: int
    actions_summary: AnalystActionsSummary
    actions: List[AnalystAction]
