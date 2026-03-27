"""
Health Check response schemas — matches the iOS HealthCheckSectionData struct.

Five metrics: Debt-to-Equity, P/E Ratio, ROE, Current Ratio, Altman Z-Score.
Each metric includes the company value, sector median comparison,
gauge position (0.0–1.0), status, and dynamic insight text.
Altman Z-Score uses absolute thresholds (no sector benchmark).
"""

from typing import List, Optional

from pydantic import BaseModel


class HealthCheckMetricSchema(BaseModel):
    type: str  # "debt_to_equity" | "pe_ratio" | "roe" | "current_ratio" | "altman_z_score"
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
    passed_count: int
    total_count: int
    metrics: List[HealthCheckMetricSchema]
