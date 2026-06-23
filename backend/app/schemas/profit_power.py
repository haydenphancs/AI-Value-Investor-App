"""
Pydantic response schemas for the Profit Power section.
Matches the SwiftUI ProfitPowerSectionData / ProfitPowerDataPoint structs.
"""

from typing import List, Optional
from pydantic import BaseModel


class ProfitPowerDataPointSchema(BaseModel):
    period: str                                        # "2021" or "Q1'24"
    gross_margin: Optional[float] = None               # company's gross margin %
    operating_margin: Optional[float] = None           # company's operating margin %
    fcf_margin: Optional[float] = None                 # company's FCF margin %
    net_margin: Optional[float] = None                 # company's net margin %
    # Sector median per margin, %. net is the original (the live detail chart's
    # dashed line); gross/operating/fcf were added so the report's per-metric
    # Profitability drill-down can draw a sector line for every margin. All
    # Optional/defaulted → the live detail-view DTO ignores the new ones.
    sector_average_net_margin: Optional[float] = None
    sector_average_gross_margin: Optional[float] = None
    sector_average_operating_margin: Optional[float] = None
    sector_average_fcf_margin: Optional[float] = None


class ProfitPowerResponse(BaseModel):
    symbol: str
    annual: List[ProfitPowerDataPointSchema]
    quarterly: List[ProfitPowerDataPointSchema]
