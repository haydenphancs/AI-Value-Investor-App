"""
Pydantic schemas for the Signal of Confidence endpoint.

Matches the iOS SignalOfConfidenceSectionData struct hierarchy:
  SignalOfConfidenceResponse
    ├── data_points: [SignalOfConfidenceDataPointSchema]  (per-quarter)
    ├── summary: SignalOfConfidenceSummarySchema           (trailing 12 months)
    └── dividend_info: DividendInfoSchema?                 (optional)
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class SignalOfConfidenceDataPointSchema(BaseModel):
    """One quarter of shareholder-return data."""

    period: str = Field(..., description="Quarter label, e.g. \"Q2 '24\"")
    dividend_yield: float = Field(0.0, description="Annualised dividend yield as percentage (1.3 = 1.3%)")
    buyback_yield: float = Field(0.0, description="Annualised buyback yield as percentage")
    dividend_amount: float = Field(0.0, description="Dividends paid in the quarter (USD millions)")
    buyback_amount: float = Field(0.0, description="Share buybacks in the quarter (USD millions)")
    shares_outstanding: float = Field(0.0, description="Weighted-average shares outstanding (millions)")


class SignalOfConfidenceSummarySchema(BaseModel):
    """Trailing-12-month summary metrics."""

    total_yield: float = Field(0.0, description="T12M total shareholder yield %")
    dividend_yield: float = Field(0.0, description="T12M dividend yield %")
    buyback_yield: float = Field(0.0, description="T12M buyback yield %")
    share_count_change: float = Field(0.0, description="Share count change % (negative = shrinking)")


class DividendInfoSchema(BaseModel):
    """Latest dividend details and status ratings."""

    ex_dividend_date: Optional[str] = Field(None, description="Ex-dividend date ISO string, e.g. 2025-11-10")
    payment_date: Optional[str] = Field(None, description="Payment date ISO string")
    five_year_avg_yield: float = Field(0.0, description="5-year average dividend yield %")
    status: str = Field("Fair", description="Dividend yield status: Low / Fair / High / Very High")
    buyback_status: str = Field("Low", description="Buyback status: Diluting / Diluting (Mild) / Low / Moderate / High / Very High")


class SignalOfConfidenceResponse(BaseModel):
    """Top-level response — matches iOS SignalOfConfidenceSectionData."""

    symbol: str
    data_points: List[SignalOfConfidenceDataPointSchema]
    summary: SignalOfConfidenceSummarySchema
    dividend_info: Optional[DividendInfoSchema] = None
