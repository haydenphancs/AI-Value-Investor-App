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
    # What the CLIENT says this is ("crypto" / "stock" / "etf" / ...). Optional so the
    # currently-shipped build, which does not send it, keeps working.
    #
    # It exists to disambiguate a bare coin ticker: search deliberately returns BOTH
    # "BTC — Bitcoin" and "BTC — Grayscale Bitcoin Mini Trust ETF", and without this the
    # two were stored as the same string and nothing downstream could tell them apart.
    # See `asset_class.canonical_stored_symbol`.
    asset_type: Optional[str] = None


class RemoveFromWatchlistRequest(BaseModel):
    stock_id: str  # ticker symbol
    # Same disambiguation as the add: "crypto" means the PAIR row (BTCUSD), anything else
    # or absent means raw-then-canonical. Optional so the shipped build keeps working.
    asset_type: Optional[str] = None
