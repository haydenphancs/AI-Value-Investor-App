"""
Pydantic response models for the Revenue Breakdown endpoint.
Frontend: GET /stocks/{ticker}/revenue-breakdown
Maps to SwiftUI RevenueBreakdownData model.
"""

from typing import List
from pydantic import BaseModel


class RevenueSourceSchema(BaseModel):
    """Single revenue segment (e.g. 'iPhone', 'Services')."""
    name: str
    value: float  # raw dollars — iOS formats client-side


class RevenueBreakdownResponse(BaseModel):
    """
    Full payload for "How [TICKER] Makes Money" section.
    iOS computes totalRevenue, netProfit, percentages, and colors client-side.
    """
    symbol: str
    fiscal_year: str  # e.g. "2024"
    revenue_sources: List[RevenueSourceSchema]
    cost_of_sales: float
    operating_expense: float
    tax: float
