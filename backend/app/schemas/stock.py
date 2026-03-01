"""Stock schemas - thin wrappers around FMP API responses, normalized to snake_case."""

from pydantic import BaseModel
from typing import Optional


class StockSearchResult(BaseModel):
    symbol: str
    name: str
    currency: Optional[str] = None
    exchange_full_name: Optional[str] = None
    exchange: Optional[str] = None


class StockProfile(BaseModel):
    symbol: str
    company_name: str
    price: Optional[float] = None
    market_cap: Optional[float] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    website: Optional[str] = None
    image: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None
    country: Optional[str] = None
    ipo_date: Optional[str] = None
    ceo: Optional[str] = None
    full_time_employees: Optional[int] = None
    beta: Optional[float] = None
    vol_avg: Optional[float] = None
    last_div: Optional[float] = None
    changes: Optional[float] = None
    dcf: Optional[float] = None


class StockQuote(BaseModel):
    symbol: str
    price: Optional[float] = None
    changes_percentage: Optional[float] = None
    change: Optional[float] = None
    day_low: Optional[float] = None
    day_high: Optional[float] = None
    year_high: Optional[float] = None
    year_low: Optional[float] = None
    market_cap: Optional[float] = None
    volume: Optional[int] = None
    avg_volume: Optional[int] = None
    open: Optional[float] = None
    previous_close: Optional[float] = None
    eps: Optional[float] = None
    pe: Optional[float] = None
    name: Optional[str] = None
    exchange: Optional[str] = None
    timestamp: Optional[int] = None
