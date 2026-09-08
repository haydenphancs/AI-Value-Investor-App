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
    # Optional because 0.0 is not a share count any listed company can have — FMP
    # genuinely returns `weightedAverageShsOut: 0` on some rows, and treating that as a
    # measurement produced a fabricated -100% share-count change. None = "not reported".
    shares_outstanding: Optional[float] = Field(
        None, description="Weighted-average shares outstanding (millions); None if unreported"
    )


class SignalOfConfidenceSummarySchema(BaseModel):
    """Trailing-12-month summary metrics."""

    total_yield: float = Field(0.0, description="T12M total shareholder yield %")
    dividend_yield: float = Field(0.0, description="T12M dividend yield %")
    buyback_yield: float = Field(0.0, description="T12M buyback yield %")
    share_count_change: float = Field(0.0, description="Share count change % (negative = shrinking)")
    # Lives here, NOT only on DividendInfoSchema. That schema is Optional and is None
    # for every non-dividend payer, so the buyback verdict — which depends solely on
    # the two fields above — was computed and discarded for AMZN, BRK-B and NFLX. The
    # summary is always present, so this always reaches the client.
    buyback_status: str = Field(
        "Low",
        description="Buyback status: Diluting / Diluting (Mild) / Low / Moderate / High / Very High",
    )


class AnnualDividendSchema(BaseModel):
    """Dividends actually paid per share in one completed fiscal year.

    ⚠️ `per_share` of 0.0 is a REAL MEASUREMENT here, not a missing value — it means the
    company paid nothing that year. Intel's series runs 1.4598 → 0.7370 → 0.3736 → 0.0000
    as it wound its dividend down and then suspended it, and that last zero is the single
    most informative point in the series. Years BEFORE a company started paying are
    trimmed instead (see `_build_annual_dividends`), so a leading zero never appears and
    the two cases can never be confused.
    """

    year: str        # fiscal year, e.g. "2025"
    per_share: float


class DividendInfoSchema(BaseModel):
    """Latest dividend details and status ratings."""

    ex_dividend_date: Optional[str] = Field(None, description="Ex-dividend date ISO string, e.g. 2025-11-10")
    payment_date: Optional[str] = Field(None, description="Payment date ISO string")
    five_year_avg_yield: float = Field(0.0, description="5-year average dividend yield %")
    status: str = Field("Fair", description="Dividend yield status: Low / Fair / High / Very High")
    buyback_status: str = Field("Low", description="Buyback status: Diluting / Diluting (Mild) / Low / Moderate / High / Very High")

    # ── Annual dividend amounts ──────────────────────────────────────────────────────
    #
    # From `ratios` (period=annual), which is entitled — unlike `/dividends`, which went
    # outside the FMP Order Form on 2026-09-03 and took the per-payment feed with it.
    # Verified exact against declared totals: KO 2024 = 1.9399 vs a declared $1.94,
    # KO 2025 = 2.0402 vs $2.04.
    #
    # ⚠️ ANNUAL ONLY. `ratios period=quarter` also carries `dividendPerShare` and is a
    # TRAP: it is `commonDividendsPaid / weightedAverageShsOut`, i.e. cash that happened
    # to settle in the period, so KO reads $0.0207 in Q1 2025 and $1.0199 in Q4 2025 on a
    # dividend that never changed. Over a full year the timing cancels, which is the only
    # reason the annual figure is exact.
    #
    # All additive and defaulted, so a payload cached before this shipped still validates.
    annual_dividends: List[AnnualDividendSchema] = Field(
        default_factory=list, description="Completed fiscal years, oldest first"
    )
    dividend_per_share: Optional[float] = Field(
        None, description="Latest completed fiscal year's dividend per share"
    )
    dividend_per_share_year: Optional[str] = Field(None, description="e.g. 2025")
    # None when undefined rather than 0.0: a company that started paying inside the window
    # has no growth RATE (META and GOOGL both went 0 -> a real number in 2024, and "+∞%"
    # is not a fact). A company that cut to zero DOES have one, and it is -100%.
    dividend_growth_pct: Optional[float] = Field(
        None, description="Total growth across the window, None when undefined"
    )
    dividend_growth_years: Optional[int] = Field(
        None, description="Years the growth figure spans, so the label can say so"
    )


class SignalOfConfidenceResponse(BaseModel):
    """Top-level response — matches iOS SignalOfConfidenceSectionData."""

    symbol: str
    data_points: List[SignalOfConfidenceDataPointSchema]
    summary: SignalOfConfidenceSummarySchema
    dividend_info: Optional[DividendInfoSchema] = None
