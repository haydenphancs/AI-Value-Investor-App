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
    # How central the theme is to this company, from the monthly rotation's scoring:
    # "pure_play" (the theme is its main business) | "diversified" (a material part of a
    # broader business) | None (not reviewed yet / unknown).
    role: Optional[str] = None
    # Joined (or came back) in the most recent monthly review.
    is_new: Optional[bool] = None


class ThemeChangeResponse(BaseModel):
    """One change from the most recent monthly review — "What changed this month".

    `reason` is a FIXED template about relevance or eligibility (never a price move), from
    `services/theme_rotation/reasons.py`. The app shows "Not a recommendation" beside it.
    """

    ticker: str
    company_name: str = ""
    action: str                                # "added" | "returned" | "removed"
    reason: str = ""


class ThemePeriodReturnResponse(BaseModel):
    """Theme vs benchmark over one period; either side may be null (coverage too thin)."""

    period: str                                # "1M" | "YTD" | "1Y"
    theme: Optional[float] = None              # fraction, 0.042 = +4.2%
    benchmark: Optional[float] = None


class ThemePerformanceResponse(BaseModel):
    """Equal-weight performance of the theme's CURRENT stocks vs an S&P 500 ETF.

    Current-stock basis (not a tradable index with its own history): today's list, looked
    back. The app labels it so, and never as a track record of past picks.
    """

    as_of: Optional[str] = None                # ISO date of the last close used
    benchmark_label: str = "S&P 500 ETF"
    periods: List[ThemePeriodReturnResponse] = []
    # Normalised 1-year series (first point = 100) for the chart; may be empty.
    theme_series: List[float] = []
    benchmark_series: List[float] = []


class ThemeInsightResponse(BaseModel):
    """The dated "Why it's moving" summary (generated once per theme after the close)."""

    as_of: Optional[str] = None                # ISO date the text was written for
    headline: str = ""
    summary: str = ""
    tickers: List[str] = []                    # the constituents it cites


class ThemeNewsItemResponse(BaseModel):
    title: str
    source: str = ""
    url: Optional[str] = None
    published_at: Optional[str] = None
    ticker: Optional[str] = None


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
    # ── Monthly rotation + daily insights (migration 174); all optional, all additive ──
    updated_on: Optional[str] = None                     # last monthly review → "Updated Oct 1"
    changes: List[ThemeChangeResponse] = []              # that review's added/returned/removed
    performance: Optional[ThemePerformanceResponse] = None
    insight: Optional[ThemeInsightResponse] = None
    news: List[ThemeNewsItemResponse] = []
