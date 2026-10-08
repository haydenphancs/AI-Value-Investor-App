"""
Health Check response schemas — matches the iOS HealthCheckSectionData struct.

Seven metrics: Debt-to-Equity, P/E Ratio, ROE, Current Ratio, Altman Z-Score,
Interest Coverage, Quick Ratio. Each metric includes the company value, the peer
median comparison (the industry's median, or the sector's when the industry group is
too thin — `peer_level` says which), gauge position (0.0–1.0), status, and dynamic
insight text. Altman Z-Score uses absolute thresholds (no peer benchmark). Banks,
insurers, capital-markets firms, asset managers and lenders get no Current Ratio, Quick
Ratio or Interest Coverage row (`financials_metric_gate`; insurance brokers keep Interest
Coverage), and no Altman Z for the Financial Services / Real Estate sectors
(`altman_z_applicable`) — the models assume an operating company's balance sheet. In the
mixed "Financial - Credit Services" industry a listed non-lender member (the payment
networks V, MA, PYPL, WU, GPN and the fee businesses TREE, PMTS; `NON_LENDER_MEMBERS`)
keeps those three rows, and EVERY row of it — Debt-to-Equity, P/E and ROE included — is
judged on absolute bands only: no peer median, `peer_level` None (the industry's median is
a lenders' yardstick). A curated per-company fact withholds rows in any industry
(`CURATED_WITHHELD_ROWS`: WU has no Current Ratio, Quick Ratio or Interest Coverage row —
its filings carry no current/non-current split, and FMP's interest expense is not WU's).
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class HealthCheckMetricSchema(BaseModel):
    type: str  # "debt_to_equity" | "pe_ratio" | "roe" | "current_ratio" | "altman_z_score" | "interest_coverage" | "quick_ratio"
    value: float
    comparison_value: Optional[float] = None
    percent_difference: Optional[float] = None
    gauge_position: float  # 0.0–1.0
    status: str  # "positive" | "neutral" | "negative"
    insight_text: str
    highlighted_value: Optional[str] = None
    highlighted_label: Optional[str] = None
    # The level of the median in `comparison_value`: "industry" | "sector". None when
    # there is no peer comparison (`comparison_value` is None — Altman Z, the
    # negative-equity D/E row, ROE "N/M", a missing or unusable median), and for a cell
    # that carried no level (logged; the text then keeps the old "sector" wording). The
    # insight text names the same level ("industry average" / "sector average").
    # Additive (2026-10-07): shipped iOS builds ignore it; older cached rows decode to None.
    peer_level: Optional[str] = None


class HealthCheckResponse(BaseModel):
    symbol: str
    overall_rating: str  # "excellent" | "good" | "mix" | "caution" | "poor"
    # passed_count / total_count count only SCORED metrics. A row the service shows but
    # cannot judge (ROE on negative equity, highlighted_value "N/M") is in `metrics` and
    # in neither count.
    passed_count: int
    total_count: int
    metrics: List[HealthCheckMetricSchema]
    # Why this build is partial: the FMP legs that failed transiently ("ratios",
    # "key_metrics", "balance_sheet", "income", "profile"), "benchmarks" when the peer
    # lookup raised, "no_metrics" when nothing survived. A degraded build is served from
    # the 5-minute memory tier only and never written to health_check_cache, with ONE
    # exception: a fund's build whose `degraded` is exactly ["no_metrics"] (a positive fund
    # flag on the profile fetched in the same build, every statement leg a raw list) is
    # written with a `security_kind: "fund"` marker; the reader strips the marker and admits
    # only that exact shape. So this always describes the payload actually served.
    # Additive: shipped iOS builds ignore it.
    degraded: List[str] = Field(default_factory=list)
