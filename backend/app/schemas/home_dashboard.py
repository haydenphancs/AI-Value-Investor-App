"""
Caydex Home dashboard schemas — aggregated response for the redesigned
`HomeDashboardView` (NOT the legacy `HomeView`, which uses `schemas/home.py`).

Single-response design: one `GET /home/dashboard` call returns everything the
dashboard renders, built top-to-bottom. Today that is the market-status header
plus the "Market Pulse" strip; scanners / signals / themes will be added to
this same response later.

Field names are snake_case. The Swift frontend decodes via explicit
`CodingKeys` (the iOS `APIClient` deliberately does NOT use
`.convertFromSnakeCase`), so the wire contract is the literal snake_case below.

Values are RAW numbers — the iOS repository formats them into the display
strings the views consume (price text, signed percent, green/red), mirroring
how the mock repository built those strings.
"""

from pydantic import BaseModel
from typing import List


class MarketPulseItemResponse(BaseModel):
    """One tile in the Market Pulse strip (an index, crypto, or commodity)."""

    symbol: str                 # e.g. "^GSPC", "BTCUSD", "GCUSD"
    name: str                   # display name, e.g. "S&P 500"
    type: str                   # "index" | "crypto" | "commodity" | "stock" | "etf"
    price: float                # latest quote price (raw)
    change_percent: float       # today's % change (raw; iOS derives sign/colour)
    spark: List[float]          # daily closes, OLDEST-first (ascending in time);
                                # may be empty if history was unavailable


class HomeDashboardResponse(BaseModel):
    """Top-level aggregated payload for the Caydex Home dashboard.

    Grows top-to-bottom: only the market-status header and Market Pulse strip
    are populated today. The iOS repository fills the not-yet-served sections
    (scanners / signals / themes) with empty arrays, which render nothing.
    """

    market_status_text: str     # "Markets Open" | "Markets Closed" | "Pre-Market" | "After Hours"
    market_is_open: bool
    pulse: List[MarketPulseItemResponse]
