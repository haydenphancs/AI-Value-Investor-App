"""
Pydantic schemas for the Holders endpoint.

JSON keys use snake_case (matching the codebase convention).
Swift Codable DTOs map snake_case → camelCase via CodingKeys.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


# ── Shareholder Breakdown ────────────────────────────────────────

class InstitutionalHolderSchema(BaseModel):
    """Individual institutional holder (legacy top holders list)."""
    name: str
    shares_held: float = Field(0.0)
    percent_ownership: float = Field(0.0)
    change_percent: Optional[float] = None


class TopInstitutionSchema(BaseModel):
    """Top institutional owner for the Top 10 sheet."""
    rank: int
    name: str
    category: str = "Asset Management"
    value_in_billions: float = Field(0.0)
    percent_ownership: float = Field(0.0)


class TopInsiderSchema(BaseModel):
    """Top insider owner for the Top 10 sheet."""
    rank: int
    name: str
    title: str = "Officer"
    value_in_millions: float = Field(0.0)
    percent_ownership: float = Field(0.0)


class Top10OwnersSchema(BaseModel):
    """Combined top 10 institutions and insiders."""
    institutions: List[TopInstitutionSchema] = []
    insiders: List[TopInsiderSchema] = []


class ShareholderBreakdownSchema(BaseModel):
    """Ownership distribution pie chart data."""
    insiders_percent: float = Field(0.0)
    institutions_percent: float = Field(0.0)
    public_other_percent: float = Field(0.0)
    top_holders: List[InstitutionalHolderSchema] = []
    top_10_owners: Top10OwnersSchema = Top10OwnersSchema()


# ── Smart Money Flow (placeholder for now) ───────────────────────

class DailyPricePointSchema(BaseModel):
    """Daily stock price for detailed smart money price chart."""
    date: str
    price: float = 0.0


class StockPriceDataPointSchema(BaseModel):
    """Monthly stock price for smart money chart."""
    month: str
    price: float = 0.0


class SmartMoneyFlowDataPointSchema(BaseModel):
    """Buy/sell volume per period (monthly for insider/congress, quarterly for hedge funds)."""
    month: str  # "MM/YYYY" for monthly, "Q1\n'24" for quarterly
    buy_volume: float = Field(0.0)
    sell_volume: float = Field(0.0)
    has_activity: bool = True


class SmartMoneyFlowSummarySchema(BaseModel):
    """Summary of smart money activity."""
    total_net_flow: float = Field(0.0)
    total_buy: float = Field(0.0)
    total_sell: float = Field(0.0)
    is_positive: bool = True
    period_description: str = "12-Month"


class SmartMoneyDataSchema(BaseModel):
    """Complete smart money data for one tab (Insider/Hedge Funds/Congress)."""
    tab: str
    price_data: List[StockPriceDataPointSchema] = []
    daily_prices: List[DailyPricePointSchema] = []
    flow_data: List[SmartMoneyFlowDataPointSchema] = []
    summary: SmartMoneyFlowSummarySchema = SmartMoneyFlowSummarySchema()


# ── Recent Activities ────────────────────────────────────────────

class RecentActivitiesFlowSummarySchema(BaseModel):
    """Summary of institutional flow for a quarter."""
    period_description: str = ""
    quarter_description: str = ""
    in_flow_in_billions: float = Field(0.0)
    out_flow_in_billions: float = Field(0.0)


class InstitutionalActivitySchema(BaseModel):
    """A single recent institutional trading activity."""
    institution_name: str
    category: str = "Asset Management"
    date: str  # ISO date string "yyyy-MM-dd"
    change_in_millions: float = Field(0.0)
    change_percent: float = Field(0.0)
    total_held_in_billions: float = Field(0.0)


class InsiderActivitySummarySchema(BaseModel):
    """Summary of insider trading activity."""
    period_description: str = "Last 12 Months"
    informative_buys_in_millions: float = Field(0.0)
    informative_sells_in_millions: float = Field(0.0)
    num_buyers: int = 0
    num_sellers: int = 0


class InsiderActivitySchema(BaseModel):
    """A single recent insider trading activity."""
    name: str
    title: str = "Officer"
    date: str  # ISO date string "yyyy-MM-dd"
    change_in_millions: float = Field(0.0)
    transaction_type: str = "Uninformative Sell"
    price_at_transaction: float = Field(0.0)


class InsiderActivitiesDataSchema(BaseModel):
    """Insider activities with summary."""
    summary: InsiderActivitySummarySchema = InsiderActivitySummarySchema()
    activities: List[InsiderActivitySchema] = []


class RecentActivitiesSchema(BaseModel):
    """Combined recent activities data."""
    institutional_flow_summary: RecentActivitiesFlowSummarySchema = (
        RecentActivitiesFlowSummarySchema()
    )
    institutional_activities: List[InstitutionalActivitySchema] = []
    insider_activities: InsiderActivitiesDataSchema = InsiderActivitiesDataSchema()


# ── Top-level response ───────────────────────────────────────────

class HoldersResponse(BaseModel):
    """
    Full holders response — maps to the iOS HoldersData struct.

    Includes shareholder breakdown, smart money flow (placeholder),
    and recent activities (live).
    """
    symbol: str
    shareholder_breakdown: ShareholderBreakdownSchema = ShareholderBreakdownSchema()
    insider_data: SmartMoneyDataSchema = SmartMoneyDataSchema(tab="Insider")
    hedge_funds_data: SmartMoneyDataSchema = SmartMoneyDataSchema(tab="Hedge Funds")
    congress_data: SmartMoneyDataSchema = SmartMoneyDataSchema(tab="Congress")
    recent_activities: RecentActivitiesSchema = RecentActivitiesSchema()
