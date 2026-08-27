"""
Pydantic response models for the Revenue Breakdown endpoint.
Frontend: GET /stocks/{ticker}/revenue-breakdown
Maps to SwiftUI RevenueBreakdownData model.
"""

from typing import List, Optional
from pydantic import BaseModel


class RevenueSourceSchema(BaseModel):
    """Single revenue segment (e.g. 'iPhone', 'Services')."""
    name: str
    value: float  # raw dollars — iOS formats client-side


class RevenueBreakdownResponse(BaseModel):
    """
    Full payload for "How [TICKER] Makes Money" section.

    iOS still owns colours and formatting, but NOT the composition arithmetic any more —
    see the three fields at the bottom.

    ⚠️ WHY `net_income` IS SENT RATHER THAN DERIVED. iOS used to compute
    `netProfit = totalRevenue - (cost_of_sales + operating_expense + tax)`. That residual
    silently omits interest expense and every non-operating item, so it is operating profit
    after tax wearing a "Net Profit" label. Measured against live FMP data across 12 large
    caps: 9 were off by more than 10% and TWO INVERTED THE SIGN OF PROFITABILITY — the card
    reported Ford as +$6.2bn profitable in a year it lost $8.2bn, and Boeing as loss-making
    in a year it earned $2.2bn. The client cannot reconstruct this; the number has to travel.

    All three are Optional so the shape stays ADDITIVE: an already-shipped build decodes the
    response unchanged, and a cached row written before this change deserializes to None
    rather than to a confident zero.
    """
    symbol: str
    fiscal_year: str  # e.g. "2024"
    revenue_sources: List[RevenueSourceSchema]
    cost_of_sales: float
    operating_expense: float
    tax: float

    # ── The composition (see the class docstring) ──────────────────────────────
    # None means "upstream did not report it" and iOS must degrade, never substitute 0.

    #: FMP `netIncome` — the real bottom line.
    net_income: Optional[float] = None
    #: FMP `revenue`. The percentage denominator: the SUM OF SEGMENTS is not revenue
    #: (LMT FY2025: segments 74.4B vs reported 75.06B), so dividing by it ran every
    #: percentage on the card ~0.9% high, and worse wherever segment coverage is partial.
    reported_revenue: Optional[float] = None
    #: The plug that makes the waterfall reconcile:
    #:   reported_revenue - cost_of_sales - operating_expense - tax - net_income
    #: i.e. interest, non-operating items, minority interest and discontinued ops.
    #: Legitimately NEGATIVE when non-operating income exceeds those costs (MSFT: -10.6B).
    other_expense: Optional[float] = None
