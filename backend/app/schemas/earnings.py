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
    # "yyyy-MM-dd" fiscal PERIOD END: the sort key, the "period ending" chat prints and the
    # key the report collector matches to income rows. Never the announcement day.
    fiscal_date: Optional[str] = None
    # False when no COMPARABLE consensus existed for this quarter: none was published,
    # only a GAAP actual could be set against a non-GAAP estimate, the two sides came
    # from different sources/currencies, or the consensus itself was implausible. Then
    # `estimate_value` repeats `actual_value` (the field is required, and shipped iOS
    # builds decode it as a non-optional Double) and `surprise_percent` is None. Without
    # this flag iOS read actual == estimate as "matched consensus exactly". None only on
    # payloads written before the field existed.
    has_estimate: Optional[bool] = None
    # "yyyy-MM-dd" day the company ANNOUNCED this quarter, or None (a future quarter, no
    # reported announcement on file, or a date that failed the lag / order checks in
    # earnings_service). iOS pins the dot to this day on the price line: anchored on the
    # period end instead, every report-day move drew 1-11 weeks right of its own dot.
    # Only a reported fact — never a projection (next_earnings_date carries that).
    report_date: Optional[str] = None


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
