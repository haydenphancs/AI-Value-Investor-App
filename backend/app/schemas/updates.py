"""
Pydantic schemas for the iOS Updates screen (`UpdatesView`).

Contract notes that iOS depends on — change these and the app fails to decode:

* Dates are ISO-8601 **strings** without fractional seconds. Swift's
  ``JSONDecoder.dateDecodingStrategy = .iso8601`` rejects fractional seconds, and
  ``APIClient`` deliberately does NOT use ``.convertFromSnakeCase`` (every DTO
  declares explicit ``CodingKeys``), so field names here must stay snake_case.
* ``sentiment`` on the **card** is Capitalized (``Bullish``/``Bearish``/
  ``Neutral``) to match the iOS ``MarketSentiment`` enum, while ``sentiment`` on
  an **article** is lowercase (``bullish``/``bearish``/``neutral``) to match the
  existing ``ticker_news_cache`` contract that five other screens already decode.
  The two domains are genuinely different types on the iOS side; do not unify
  them without changing both.
* ``sentiment`` on an article is **Optional**. A not-yet-enriched article has no
  sentiment, and defaulting it to "neutral" would render a confident-looking
  badge that no model produced.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


# ── Filter tabs ───────────────────────────────────────────────────────

class UpdatesTabResponse(BaseModel):
    """One pill in the Updates tab bar."""

    # 'AAPL', or the reserved '__MARKET__' scope key.
    scope: str
    # What the pill renders ("Market", "AAPL").
    title: str
    company_name: Optional[str] = None
    # Session change, PERCENT (e.g. -2.14). None when the quote is unavailable —
    # iOS hides the change label rather than rendering a fabricated 0.0%.
    change_percent: Optional[float] = None
    logo_url: Optional[str] = None
    is_market_tab: bool = False


class UpdatesTabsResponse(BaseModel):
    tabs: List[UpdatesTabResponse] = Field(default_factory=list)


# ── AI Insights card ──────────────────────────────────────────────────

class AIInsightCardResponse(BaseModel):
    """The AI roll-up card at the top of the Updates screen."""

    scope: str
    headline: str
    bullets: List[str] = Field(default_factory=list)
    # 'Bullish' | 'Bearish' | 'Neutral'
    sentiment: str = "Neutral"
    # Provenance shown in the badge, e.g. "24h · AI Summary".
    badge: str = "24h · AI Summary"
    article_count: int = 0
    generated_at: Optional[str] = None
    # True once past the soft-expiry window — iOS labels the card as catching up
    # rather than silently presenting old commentary as current.
    is_stale: bool = False
    # True when this is a placeholder and a real AI card is being produced;
    # iOS re-polls a couple of times, then stops.
    refreshing: bool = False
    # False for the deterministic headline-list fallback. iOS uses this to
    # decide whether to show the AI badge at all — never claim AI authorship
    # for text no model wrote.
    ai_generated: bool = True
    # Which materiality branch produced this card. Analytics/debugging; iOS ignores it.
    trigger_reason: Optional[str] = None


# ── Articles ──────────────────────────────────────────────────────────

class UpdatesArticleResponse(BaseModel):
    """One row in the Live News timeline.

    Mirrors ``news_cache_service._format_single_row`` so the same cached rows
    serve both this screen and the ticker-detail news tab.
    """

    id: str
    headline: str
    summary: Optional[str] = None
    summary_bullets: List[str] = Field(default_factory=list)
    # lowercase bullish|bearish|neutral, or None when not yet AI-enriched
    sentiment: Optional[str] = None
    sentiment_confidence: int = 0
    source_name: Optional[str] = None
    source_logo_url: Optional[str] = None
    published_at: Optional[str] = None
    thumbnail_url: Optional[str] = None
    article_url: Optional[str] = None
    related_tickers: List[str] = Field(default_factory=list)
    ai_processed: bool = False


class UpdatesFeedResponse(BaseModel):
    """One tab's worth of content: timeline + insight card, in a single call."""

    scope: str
    articles: List[UpdatesArticleResponse] = Field(default_factory=list)
    insight: Optional[AIInsightCardResponse] = None
    cached: bool = False
    cache_age_seconds: Optional[int] = None
    # Where this page started, echoed back so a client that fired two scroll
    # requests can tell which response it is looking at.
    offset: int = 0
    # Whether another page exists. Derived from "this page came back full",
    # NOT from a COUNT: an exact count costs a second scan of the same rows on
    # every page, and the only decision it feeds is "try one more page". A full
    # last page yields one extra request that returns empty — cheap and
    # self-correcting. Defaults False so an older client that ignores the field
    # simply never paginates.
    has_more: bool = False


# ── Enrichment ────────────────────────────────────────────────────────

class EnrichUpdatesNewsRequest(BaseModel):
    scope: str
    article_ids: List[str] = Field(default_factory=list)


class EnrichUpdatesNewsResponse(BaseModel):
    scope: str
    articles: List[UpdatesArticleResponse] = Field(default_factory=list)
