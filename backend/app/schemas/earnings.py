"""
Pydantic response models for the Earnings endpoint.

JSON field names use snake_case to match the iOS Codable CodingKeys.
"""

from typing import List, Optional

from pydantic import BaseModel


class EarningsQuarterSchema(BaseModel):
    quarter: str  # e.g. "Q1 '24"
    actual_value: Optional[float] = None  # None for future quarters
    estimate_value: float
    surprise_percent: Optional[float] = None  # None for future quarters


class EarningsPricePointSchema(BaseModel):
    quarter: str  # e.g. "Q1 '24"
    price: float


class NextEarningsDateSchema(BaseModel):
    date: str  # "yyyy-MM-dd"
    is_confirmed: bool
    timing: str  # "Before Market Open", "After Market Close", etc.


class EarningsResponse(BaseModel):
    symbol: str
    eps_quarters: List[EarningsQuarterSchema]
    revenue_quarters: List[EarningsQuarterSchema]
    price_history: List[EarningsPricePointSchema]
    next_earnings_date: Optional[NextEarningsDateSchema] = None
