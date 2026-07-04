"""
Emerging Frontiers theme-detail schema — the per-theme drill-down served by
``GET /api/v1/home/themes/{slug}`` and decoded by the iOS `ThemeDetailView`.

Reached by tapping a theme card on Home. Shows the theme's hero (title / subtitle
/ image) plus its live constituent companies. RAW numbers on the wire
(price / change_percent / market_cap) — the iOS DTO formats them into the display
strings the view consumes (price text, signed percent + green/red, "N.NB Cap").

The theme's constituent tickers live in the `trending_themes` Supabase row
(editable → NO app release); this endpoint resolves them to live quotes.
"""

from pydantic import BaseModel
from typing import List, Optional


class ThemeConstituentResponse(BaseModel):
    """One company in a theme's constituent list (raw numbers; iOS formats)."""

    ticker: str
    company_name: str = ""                   # "" when the quote lacks a name → iOS shows the ticker
    price: Optional[float] = None            # latest quote price
    change_percent: Optional[float] = None   # today's % change (signed; iOS colours)
    market_cap: Optional[float] = None        # raw; drives the sort + optional "N.NB Cap"


class ThemeDetailResponse(BaseModel):
    """Full theme drill-down: hero header + ranked constituents.

    ``constituents`` is ordered largest-market-cap first. An empty list (an FMP
    hiccup, or a theme with no tickers) → iOS shows an honest empty state under
    the hero, which still renders from the row's title/subtitle/image.
    """

    slug: str
    title: str                       # the "Next-Wave" name → hero title
    subtitle: str = ""               # editorial tagline → hero subtitle
    image_url: Optional[str] = None  # hero image (public Supabase Storage URL); nullable → accent fallback
    accent_hex: str                  # fallback hero gradient / accent
    constituents: List[ThemeConstituentResponse] = []
