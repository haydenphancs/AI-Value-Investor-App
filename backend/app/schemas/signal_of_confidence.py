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
    # TRAILING-TWELVE-MONTH yields (owner decision, 2026-09-30): the four quarters ending
    # here, over the market cap at THIS quarter's period end. They used to be the single
    # quarter x4, which turned cash-settlement timing into fake swings — KO's unchanged
    # dividend read 0.12% / 2.92% / 2.92% / 5.85% on a 2.95% yield, and one ASR quarter
    # flattened every other bar. A quarter whose four-quarter window is not consecutive
    # falls back to x4 (logged `[soc-ttm-fallback]`).
    dividend_yield: float = Field(
        0.0, description="Trailing-12-month dividend yield at this quarter's end, % (1.3 = 1.3%)"
    )
    buyback_yield: float = Field(
        0.0, description="Trailing-12-month buyback yield at this quarter's end, %"
    )
    dividend_amount: float = Field(0.0, description="Dividends paid in the quarter (USD millions)")
    buyback_amount: float = Field(0.0, description="Share buybacks in the quarter (USD millions)")
    # Optional because 0.0 is not a share count any listed company can have — FMP
    # genuinely returns `weightedAverageShsOut: 0` on some rows, and treating that as a
    # measurement produced a fabricated -100% share-count change. None = "not reported",
    # also for an interior quarter whose count the service refuses as a vendor artifact
    # (FMP's annual-average copy in a fiscal-Q4 row). The NEWEST point is never None for
    # that reason — build 1.0 (10) prints a null newest count as a bold "0.00M" — so a
    # refused newest count carries the count the quarter's own EPS puts it at instead
    # (`signal_of_confidence_service._ShareGlitch`); a newest count FMP sent as 0 / null
    # carries that figure too where one is sound beside the quarter before, else None.
    # Such an estimate is for the chart only: the summary's `share_count_change` and
    # `buyback_status` measure reported counts and skip it (round-5 review 2026-10-08).
    shares_outstanding: Optional[float] = Field(
        None, description="Weighted-average shares outstanding (millions); None if unreported"
    )
    # P19 (2026-10-01): False when the vendor has NO cash-flow row for this quarter (an
    # interior or leading-edge history hole; the newest edge is trimmed instead). The point
    # stays because its share count is real, but the four cash fields above are then 0.0
    # PLACEHOLDERS — they cannot be null, shipped iOS decodes them as non-Optional `Double`
    # in a DTO the report reuses — never a measured zero. Defaulted True so a payload built
    # before the key existed (a cached report, an older backend) keeps its old meaning.
    cash_flow_reported: bool = Field(
        True,
        description=(
            "False when the vendor has no cash-flow row for this quarter; the four cash "
            "fields are then 0.0 placeholders, non-Optional on shipped iOS, not measurements"
        ),
    )
    # 2026-10-05 (TestFlight 1.0 (11), CRWV): the market cap this point's yields were
    # divided by — the cap at THIS period end, else the current cap where the history does
    # not reach (a pre-IPO quarter). iOS scales the Capital ($) view against it, so a
    # $2.6M amount on a ~$50B company draws as the sliver it is instead of a full-height
    # bar on a "$0-$3M" axis. Additive and Optional: shipped builds ignore it, and None
    # (no usable cap — the yields then read 0.00) means "no scale floor", never a zero cap.
    market_cap: Optional[float] = Field(
        None,
        description=(
            "Market cap (USD millions) this point's yields were computed against; None "
            "when no cap was available"
        ),
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
    # False when fewer than two quarters REPORT a share count. `share_count_change` is then
    # 0.0 because the field is a non-Optional Double on shipped iOS builds — not because the
    # count was measured flat. Without this flag the card printed "+0.0%" and Cay AI was told
    # "Share count unchanged" as a fact. Defaulted True so a payload cached before it existed
    # (none survive the payload-version bump, but a fixture might) keeps its old meaning.
    share_count_change_known: bool = Field(
        True, description="False when share_count_change could not be measured (< 2 reported counts)"
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
    # The window `five_year_avg_yield` ACTUALLY spans. The field name is legacy (kept for the
    # shipped DTO): the average is over the quarterly points we hold — at most eight — so the
    # card's "5Y Avg Yield" label claimed five years for two. "8Q" = mean of eight quarterly
    # trailing-12-month yields; "NY" only on the annual fallback; None when there is no average.
    avg_yield_window: Optional[str] = Field(
        None, description="Window of five_year_avg_yield, e.g. '8Q' or '5Y'; None when undefined"
    )


class SignalOfConfidenceResponse(BaseModel):
    """Top-level response — matches iOS SignalOfConfidenceSectionData."""

    symbol: str
    data_points: List[SignalOfConfidenceDataPointSchema]
    summary: SignalOfConfidenceSummarySchema
    dividend_info: Optional[DividendInfoSchema] = None
    # Why THIS build is partial (e.g. "income", "profile", "market_cap", "cash_flow_row").
    # Non-empty means it was never written to the 24h Supabase tier and lives in memory for
    # a minute only; it reflects the value actually served (a Supabase hit is always []).
    # iOS skips its own cache for a degraded payload.
    degraded: List[str] = Field(default_factory=list)
