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


class AnalystEstimateRange(BaseModel):
    """Street low/average/high for one forecast line item.

    Every field is Optional because an unknown number must be `None`, never `0.0` — a
    zero revenue estimate is indistinguishable from a measurement, which is the exact
    class of bug `has_coverage` and `section_available` exist to prevent.
    """

    low: Optional[float] = None
    avg: Optional[float] = None
    high: Optional[float] = None


class AnalystEstimatePeriod(BaseModel):
    """One fiscal period of Street estimates, from FMP's entitled `analyst-estimates`.

    This is a DIFFERENT dataset from the rest of this response. `grades` and
    `price-target-consensus` fund the consensus/target/momentum fields and are outside the
    signed licence; `analyst-estimates` is inside it and carries forward fundamentals
    instead. It cannot produce a rating, a price target, or an upgrade — so nothing here
    is a substitute for those, and the two halves are gated by separate flags.
    """

    fiscal_period: str                                  # "FY2027"
    date: str                                           # ISO — a fiscal period END
    is_forward: bool
    revenue: Optional[AnalystEstimateRange] = None
    ebitda: Optional[AnalystEstimateRange] = None
    ebit: Optional[AnalystEstimateRange] = None
    net_income: Optional[AnalystEstimateRange] = None
    eps: Optional[AnalystEstimateRange] = None
    num_analysts_revenue: Optional[int] = None
    num_analysts_eps: Optional[int] = None


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
    # False when the DATA SOURCE is outside the FMP licence, which is a different statement
    # from `has_coverage=False`. `has_coverage` means "we asked and no analyst covers this
    # ticker" — an honest, useful fact. This means "we cannot ask at all", and rendering the
    # no-coverage card for it tells the user that nobody covers Apple, which is false.
    # Additive and defaulted, so an older client is unaffected; a current one hides the
    # section entirely. Flips back to True on its own if the packages are ever repurchased.
    section_available: bool = True
    target_price: float
    target_upside: float                       # percentage
    distributions: List[AnalystRatingDistribution]
    price_target: AnalystPriceTarget
    momentum_data: List[AnalystMomentumMonth]
    net_positive: int
    net_negative: int
    actions_summary: AnalystActionsSummary
    actions: List[AnalystAction]

    # ── Street estimates — a SEPARATE dataset behind a SEPARATE flag ──────────────────
    #
    # ⚠️ These deliberately do NOT flip `section_available`. iOS renders `EmptyView()`
    # precisely because `sectionAvailable == false`; flipping it while every legacy field
    # above is still a zero default would make every ALREADY-SHIPPED build render a
    # confident HOLD at a $0.00 price target — re-shipping the fabricated verdict that flag
    # was added to stop, to exactly the users who cannot update. The new card is driven by
    # `estimates_available` instead, which old clients ignore because they never decode it.
    #
    # `analyst_is_usable()` in `_analyst_common` is likewise about the RATINGS half and
    # must stay that way: nothing here licenses quoting a consensus or a price target.
    #
    # All additive and defaulted, so a payload cached before this shipped still validates.
    estimates_available: bool = False          # the licence permits asking
    estimates_have_coverage: bool = False      # ...and at least one period survived
    estimates: List[AnalystEstimatePeriod] = []
    estimates_period: str = "annual"
    # ISO date of the nearest FORWARD fiscal period end, or None when every period we hold
    # is already in the past. Deliberately NOT called `updated_date`: an estimate row's
    # date is a fiscal period END, never a publication date, and reusing the legacy field's
    # wording would put "Updated On 2027-09-27" under a card about the future.
    estimates_next_period: Optional[str] = None
