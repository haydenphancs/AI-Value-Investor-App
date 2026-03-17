"""
Pydantic response schemas for the Growth section.
Matches the SwiftUI GrowthSectionData / GrowthDataPoint structs.
"""

from typing import List, Optional
from pydantic import BaseModel


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
