"""
Pydantic response schemas for the Growth section.
Matches the SwiftUI GrowthSectionData / GrowthDataPoint structs.
"""

from typing import List
from pydantic import BaseModel


class GrowthDataPointSchema(BaseModel):
    period: str               # "2021" or "Q1'21"
    value: float              # absolute value (eps or revenue)
    yoy_change_percent: float  # Year-over-Year growth %
    sector_average_yoy: float  # sector peers' avg YoY %


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
