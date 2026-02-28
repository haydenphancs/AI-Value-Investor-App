"""Watchlist schemas matching DB watchlist_items table."""

from pydantic import BaseModel
from typing import Optional


class WatchlistItemResponse(BaseModel):
    id: str
    ticker: str
    company_name: Optional[str] = None
    logo_url: Optional[str] = None
    added_at: str


class AddToWatchlistRequest(BaseModel):
    stock_id: str  # ticker symbol - frontend sends as "stock_id"


class RemoveFromWatchlistRequest(BaseModel):
    stock_id: str  # ticker symbol
