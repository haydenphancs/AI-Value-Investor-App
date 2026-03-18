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
    sector_average_net_margin: Optional[float] = None  # sector median net margin %


class ProfitPowerResponse(BaseModel):
    symbol: str
    annual: List[ProfitPowerDataPointSchema]
    quarterly: List[ProfitPowerDataPointSchema]
