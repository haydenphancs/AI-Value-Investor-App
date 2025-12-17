"""
Stock Pydantic Schemas
Request and response models for stock-related operations.
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from decimal import Decimal

from app.schemas.common import BaseResponse, TimestampMixin


# Stock Models
# ============

class StockBase(BaseModel):
    """Base stock model."""
    ticker: str = Field(..., min_length=1, max_length=10, description="Stock ticker symbol")
    company_name: str


class StockSearch(StockBase):
    """Stock search result."""
    id: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    market_cap: Optional[Decimal] = None
    logo_url: Optional[str] = None
    exchange: Optional[str] = None

    # Extra helpful fields
    description: Optional[str] = Field(None, max_length=500, description="Brief company description")
    website: Optional[str] = None


class StockDetail(BaseResponse, TimestampMixin):
    """Detailed stock information."""
    id: str
    ticker: str
    company_name: str
    exchange: Optional[str]
    sector: Optional[str]
    industry: Optional[str]

    # Metadata
    market_cap: Optional[Decimal]
    description: Optional[str]
    website: Optional[str]
    logo_url: Optional[str]

    # Data freshness
    last_data_update: Optional[datetime]
    is_active: bool = True

    # Extra computed fields
    market_cap_formatted: Optional[str] = Field(None, description="Formatted market cap (e.g., $1.2T)")
    sector_emoji: Optional[str] = Field(None, description="Emoji representing sector")


class StockPrice(BaseModel):
    """Stock price data."""
    stock_id: str
    price_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    adjusted_close: Optional[Decimal]
    volume: Optional[int]
    daily_return: Optional[Decimal] = Field(None, description="Daily return percentage")

    # Extra computed fields
    price_change: Optional[Decimal] = Field(None, description="Close - Open")
    price_change_percent: Optional[Decimal] = Field(None, description="Percentage change")


# Fundamentals Models
# ===================

class FinancialMetrics(BaseModel):
    """Key financial metrics."""
    # Income Statement
    revenue: Optional[Decimal]
    gross_profit: Optional[Decimal]
    operating_income: Optional[Decimal]
    net_income: Optional[Decimal]
    eps: Optional[Decimal]
    ebitda: Optional[Decimal]

    # Balance Sheet
    total_assets: Optional[Decimal]
    total_liabilities: Optional[Decimal]
    shareholders_equity: Optional[Decimal]
    total_debt: Optional[Decimal]
    cash_and_equivalents: Optional[Decimal]

    # Cash Flow
    operating_cash_flow: Optional[Decimal]
    free_cash_flow: Optional[Decimal]
    capex: Optional[Decimal]

    # Ratios
    pe_ratio: Optional[Decimal]
    pb_ratio: Optional[Decimal]
    ps_ratio: Optional[Decimal]
    debt_to_equity: Optional[Decimal]
    current_ratio: Optional[Decimal]
    roe: Optional[Decimal]
    roa: Optional[Decimal]
    gross_margin: Optional[Decimal]
    operating_margin: Optional[Decimal]
    profit_margin: Optional[Decimal]


class CompanyFundamentals(BaseResponse, TimestampMixin):
    """Company fundamental data."""
    id: str
    stock_id: str
    fiscal_year: int
    fiscal_quarter: Optional[int]
    period_end_date: date

    # Financial data
    metrics: FinancialMetrics

    # Source
    filing_date: Optional[date]
    document_url: Optional[str]

    # Extra computed fields
    is_annual: bool = Field(description="True if annual (Q4) data")
    period_label: str = Field(description="e.g., 'FY2024' or 'Q3 2024'")


class EarningsData(BaseModel):
    """Earnings report data."""
    id: str
    stock_id: str
    earnings_date: datetime
    fiscal_quarter: Optional[int]
    fiscal_year: Optional[int]

    # Actual vs Estimate
    eps_actual: Optional[Decimal]
    eps_estimate: Optional[Decimal]
    eps_surprise: Optional[Decimal]
    eps_surprise_percent: Optional[Decimal]

    revenue_actual: Optional[Decimal]
    revenue_estimate: Optional[Decimal]
    revenue_surprise: Optional[Decimal]
    revenue_surprise_percent: Optional[Decimal]

    has_occurred: bool = False

    # Extra fields
    beat_estimates: Optional[bool] = Field(None, description="True if beat both EPS and revenue")
    earnings_call_url: Optional[str] = None


# Watchlist Models
# ================

class WatchlistAdd(BaseModel):
    """Add stock to watchlist."""
    stock_id: str
    alert_on_news: bool = True
    alert_threshold_percentage: Optional[Decimal] = Field(
        None,
        ge=-100,
        le=100,
        description="Alert when price moves by this percentage"
    )
    custom_notes: Optional[str] = Field(None, max_length=1000)

    # Position tracking (optional)
    position_size: Optional[Decimal] = Field(None, gt=0, description="Number of shares")
    average_cost: Optional[Decimal] = Field(None, gt=0, description="Average cost per share")


class WatchlistUpdate(BaseModel):
    """Update watchlist item."""
    alert_on_news: Optional[bool] = None
    alert_threshold_percentage: Optional[Decimal] = None
    custom_notes: Optional[str] = None
    position_size: Optional[Decimal] = None
    average_cost: Optional[Decimal] = None


class WatchlistItem(BaseResponse, TimestampMixin):
    """Watchlist item with stock details."""
    id: str
    user_id: str
    stock_id: str

    # Stock details (embedded)
    stock: StockSearch

    # Preferences
    alert_on_news: bool
    alert_threshold_percentage: Optional[Decimal]
    custom_notes: Optional[str]

    # Position tracking
    position_size: Optional[Decimal]
    average_cost: Optional[Decimal]

    # Timestamps
    added_at: datetime
    last_viewed_at: Optional[datetime]

    # Extra computed fields
    current_value: Optional[Decimal] = Field(None, description="position_size * current_price")
    gain_loss: Optional[Decimal] = Field(None, description="Current value - cost basis")
    gain_loss_percent: Optional[Decimal] = Field(None, description="Percentage gain/loss")
    has_breaking_news: Optional[bool] = Field(False, description="Flag for breaking news")


# Analyst Data
# ============

class AnalystForecast(BaseModel):
    """Analyst forecast/estimate."""
    id: str
    stock_id: str
    forecast_type: str  # "eps", "revenue", "price_target"
    forecast_period: str  # "Q1 2025", "FY2025"

    mean_estimate: Optional[Decimal]
    median_estimate: Optional[Decimal]
    high_estimate: Optional[Decimal]
    low_estimate: Optional[Decimal]
    number_of_analysts: Optional[int]
    standard_deviation: Optional[Decimal]

    as_of_date: date

    # Extra fields
    consensus_rating: Optional[str] = Field(None, description="Buy/Hold/Sell")
    analyst_confidence: Optional[str] = Field(None, description="High/Medium/Low")


# Company Insights
# ================

class CompanyInsight(BaseModel):
    """Cached AI-generated company insight."""
    id: str
    stock_id: str
    insight_type: str  # "moat", "risks", "opportunities", "valuation"
    question: str
    answer: str
    sources: Optional[List[Dict[str, Any]]] = None
    charts_data: Optional[Dict[str, Any]] = None

    # Cache metadata
    cache_hit_count: int = 0
    generated_at: datetime
    expires_at: Optional[datetime]

    # Extra fields
    is_fresh: bool = Field(description="True if not yet expired")
    confidence_score: Optional[float] = Field(None, ge=0.0, le=1.0)
