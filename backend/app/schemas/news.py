"""News schemas matching DB news_articles table."""

from enum import Enum
from pydantic import BaseModel, validator
from typing import Any, Dict, List, Optional


class SentimentValue(str, Enum):
    """Strict sentiment values — the only three the frontend accepts."""
    POSITIVE = "Positive"
    NEGATIVE = "Negative"
    NEUTRAL = "Neutral"


class NewsArticleResponse(BaseModel):
    id: str
    headline: str
    summary: Optional[str] = None
    source_name: Optional[str] = None
    source_logo_url: Optional[str] = None
    source_is_verified: bool = False
    sentiment: Optional[str] = None
    published_at: Optional[str] = None
    thumbnail_url: Optional[str] = None
    related_tickers: Optional[List[str]] = None
    category: Optional[str] = None
    is_breaking: bool = False
    article_url: Optional[str] = None
    insight_summary: Optional[str] = None
    insight_key_points: Optional[List[str]] = None
    key_takeaways: Optional[List[str]] = None
    read_time_minutes: Optional[int] = None
    created_at: Optional[str] = None


class NewsFeedResponse(BaseModel):
    articles: List[NewsArticleResponse]
    page: int
    per_page: int
    total: Optional[int] = None
    has_more: bool = False


# ── Ticker-specific AI-enriched news ──────────────────────────────────

class TickerNewsArticleResponse(BaseModel):
    id: str
    headline: str
    summary: Optional[str] = None
    summary_bullets: List[str] = []
    sentiment: Optional[SentimentValue] = None
    sentiment_confidence: int = 0
    source_name: Optional[str] = None
    source_logo_url: Optional[str] = None
    published_at: Optional[str] = None
    thumbnail_url: Optional[str] = None
    article_url: Optional[str] = None
    related_tickers: List[str] = []
    ai_processed: bool = False

    @validator("sentiment", pre=True, always=True)
    def coerce_sentiment(cls, v):
        """Coerce raw sentiment strings to SentimentValue or None."""
        if v is None:
            return None
        if isinstance(v, SentimentValue):
            return v
        s = str(v).strip().lower()
        if s in ("positive", "bullish"):
            return SentimentValue.POSITIVE
        if s in ("negative", "bearish"):
            return SentimentValue.NEGATIVE
        if s in ("neutral", "none", "mixed", ""):
            return SentimentValue.NEUTRAL
        return SentimentValue.NEUTRAL


class TickerNewsFeedResponse(BaseModel):
    articles: List[TickerNewsArticleResponse]
    ticker: str
    cached: bool = False
    cache_age_seconds: Optional[int] = None


class EnrichNewsResponse(BaseModel):
    """Body of every ``POST /{symbol}/news/enrich`` route (stocks, crypto,
    indices, commodities). iOS decodes all four with one Codable."""
    ticker: str
    articles: List[TickerNewsArticleResponse] = []


# ── Cache row → wire model ────────────────────────────────────────────
#
# All four asset endpoints serve rows from the same `ticker_news_cache`
# table, so the guarded mapping lives beside the model it protects rather
# than being copy-pasted four times.

# Enrichment costs one Gemini call per batch; a caller asking for more than
# this is a bug or an abuse, not a screenful of articles.
MAX_ENRICH_ARTICLE_IDS = 50

# Client-side placeholder ids. `_build_and_cache_rows` hands out `temp_N` when
# the upsert yields no DB id, and the un-cached fallback path hands out `raw_N`;
# neither is a row key, so sending them to Postgres just errors an `IN (...)`
# against a uuid column.
_PLACEHOLDER_ID_PREFIXES = ("temp_", "raw_", "sample_")


def _opt_str(value: Any) -> Optional[str]:
    if value is None or isinstance(value, str):
        return value
    return str(value)


def news_article_from_row(row: Dict[str, Any]) -> TickerNewsArticleResponse:
    """Map one cache/service row to the wire model, coercing every field.

    Nothing here is cosmetic: `summary_bullets`, `related_tickers`,
    `sentiment_confidence` and `ai_processed` are all NULLable columns, and FMP
    can return an article with no title. Handing such a row straight to
    `TickerNewsArticleResponse` raises ResponseValidationError — a 500 for the
    whole feed because of one degraded article. A well-formed row round-trips
    unchanged.
    """
    try:
        confidence = int(row.get("sentiment_confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0

    bullets = row.get("summary_bullets")
    related = row.get("related_tickers")

    return TickerNewsArticleResponse(
        id=_opt_str(row.get("id")) or "",
        headline=_opt_str(row.get("headline")) or "",
        summary=_opt_str(row.get("summary")),
        summary_bullets=(
            [b for b in bullets if isinstance(b, str)] if isinstance(bullets, list) else []
        ),
        # Forwarded as stored — the field validator maps bullish/bearish/neutral
        # (and None) onto the wire enum. Deliberately NOT blanked for
        # un-enriched rows: the cache normalizes those to 'neutral' and iOS has
        # rendered that Neutral badge since the feature shipped.
        sentiment=row.get("sentiment"),
        sentiment_confidence=max(0, min(100, confidence)),
        source_name=_opt_str(row.get("source_name")),
        source_logo_url=_opt_str(row.get("source_logo_url")),
        published_at=_opt_str(row.get("published_at")),
        thumbnail_url=_opt_str(row.get("thumbnail_url")),
        article_url=_opt_str(row.get("article_url")),
        related_tickers=(
            [t for t in related if isinstance(t, str) and t] if isinstance(related, list) else []
        ),
        ai_processed=bool(row.get("ai_processed")),
    )


def news_articles_from_rows(rows: Any) -> List[TickerNewsArticleResponse]:
    """Map a list of cache rows, skipping anything that isn't a dict."""
    if not isinstance(rows, list):
        return []
    return [news_article_from_row(r) for r in rows if isinstance(r, dict)]


def news_feed_from_payload(payload: Any, *, ticker: str) -> TickerNewsFeedResponse:
    """Map the `NewsCacheService` envelope {articles, ticker, cached,
    cache_age_seconds} to the response model.

    `ticker` is the endpoint's own cache key and is used only when the service
    envelope is malformed, so the client always gets back the key it asked for.
    """
    payload = payload if isinstance(payload, dict) else {}

    age = payload.get("cache_age_seconds")
    try:
        age = int(age) if age is not None else None
    except (TypeError, ValueError):
        age = None

    return TickerNewsFeedResponse(
        articles=news_articles_from_rows(payload.get("articles")),
        ticker=_opt_str(payload.get("ticker")) or ticker,
        cached=bool(payload.get("cached")),
        cache_age_seconds=age,
    )


def sanitize_article_ids(value: Any) -> List[str]:
    """De-dup an incoming `article_ids` list and drop placeholder ids.

    Returns [] for a non-list, which the caller treats as "nothing to enrich".
    """
    if not isinstance(value, list):
        return []
    # Filter BEFORE de-duping: these routes take an untyped JSON body, so a
    # client can put a dict in the list and `dict.fromkeys` raises TypeError
    # (unhashable) on it — a 500 from input we should just be dropping.
    ids = [
        i for i in value
        if isinstance(i, str) and i and not i.startswith(_PLACEHOLDER_ID_PREFIXES)
    ]
    return list(dict.fromkeys(ids))
