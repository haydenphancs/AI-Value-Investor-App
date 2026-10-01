"""
Health Check response schemas — matches the iOS HealthCheckSectionData struct.

Seven metrics: Debt-to-Equity, P/E Ratio, ROE, Current Ratio, Altman Z-Score,
Interest Coverage, Quick Ratio. Each metric includes the company value, sector
median comparison, gauge position (0.0–1.0), status, and dynamic insight text.
Altman Z-Score uses absolute thresholds (no sector benchmark).
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
    # the 5-minute memory tier only, never written to health_check_cache, so this always
    # describes the payload actually served. Additive: shipped iOS builds ignore it.
    degraded: List[str] = Field(default_factory=list)
