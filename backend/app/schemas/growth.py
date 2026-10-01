"""
Pydantic response schemas for the Growth section.
Matches the SwiftUI GrowthSectionData / GrowthDataPoint structs.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class GrowthDataPointSchema(BaseModel):
    period: str                              # "2021" or "Q1'21"
    value: float                             # absolute value (eps or revenue)
    yoy_change_percent: Optional[float] = None  # Year-over-Year growth % (None when prev is 0 or missing)
    sector_average_yoy: Optional[float] = None   # sector peers' median YoY %
    sector_average_qoq: Optional[float] = None   # sector peers' median QoQ % (quarterly only)


class GrowthResponse(BaseModel):
    symbol: str
    eps_annual: List[GrowthDataPointSchema]
    eps_quarterly: List[GrowthDataPointSchema]
    revenue_annual: List[GrowthDataPointSchema]
    revenue_quarterly: List[GrowthDataPointSchema]
    net_income_annual: List[GrowthDataPointSchema] = []
    net_income_quarterly: List[GrowthDataPointSchema] = []
    operating_profit_annual: List[GrowthDataPointSchema] = []
    operating_profit_quarterly: List[GrowthDataPointSchema] = []
    free_cash_flow_annual: List[GrowthDataPointSchema] = []
    free_cash_flow_quarterly: List[GrowthDataPointSchema] = []
    # The FMP legs that failed in THIS build ("quarterly_income", "profile", ...). Empty
    # for a complete build. Reflects the value actually served — a Tier-1 hit or an
    # in-flight join reports the degradation of the build it hands out — so a client
    # with its own long-lived cache (iOS StockRepository) can refuse to keep a partial
    # build. Additive: shipped iOS builds ignore the key.
    degraded: List[str] = Field(default_factory=list)
    # Which peer group each series' dashed line comes from: "industry" | "sector", keyed
    # by series name (eps_annual, revenue_quarterly, ..., fcf_quarterly). Only series
    # whose line is actually drawn carry a key; absent means "no peer line" (iOS then
    # keeps its neutral "Sector" wording). The benchmark lookup falls back industry →
    # sector PER CELL, so one ticker-wide level would mislabel some selections.
    peer_group_levels: Dict[str, str] = Field(default_factory=dict)
