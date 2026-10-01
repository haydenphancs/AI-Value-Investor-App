"""
Pydantic response models for the Earnings endpoint.

JSON field names use snake_case to match the iOS Codable CodingKeys.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class EarningsQuarterSchema(BaseModel):
    quarter: str  # e.g. "Q1 '24"
    actual_value: Optional[float] = None  # None for future quarters
    estimate_value: float
    surprise_percent: Optional[float] = None  # None for future quarters
    fiscal_date: Optional[str] = None  # "yyyy-MM-dd" for sorting & iOS positioning
    # False when no COMPARABLE consensus existed for this quarter: none was published,
    # only a GAAP actual could be set against a non-GAAP estimate, the two sides came
    # from different sources/currencies, or the consensus itself was implausible. Then
    # `estimate_value` repeats `actual_value` (the field is required, and shipped iOS
    # builds decode it as a non-optional Double) and `surprise_percent` is None. Without
    # this flag iOS read actual == estimate as "matched consensus exactly". None only on
    # payloads written before the field existed.
    has_estimate: Optional[bool] = None


class EarningsPricePointSchema(BaseModel):
    quarter: str  # e.g. "Q1 '24"
    price: float
    fiscal_date: Optional[str] = None


class EarningsDailyPriceSchema(BaseModel):
    date: str    # "yyyy-MM-dd"
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
    daily_price_history: List[EarningsDailyPriceSchema] = []
    next_earnings_date: Optional[NextEarningsDateSchema] = None
    # Why this build is PARTIAL ("income", "estimates", "prices", "earnings_feed": an
    # upstream leg failed). Empty for a complete build. A degraded build is never written
    # to the Supabase tier (60s in-memory only), so this reflects the payload actually
    # served; the report collector and the iOS cache read it to refuse freezing it.
    degraded: List[str] = Field(default_factory=list)
