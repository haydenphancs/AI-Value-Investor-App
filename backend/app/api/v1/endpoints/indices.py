"""
Index Endpoints — Aggregated data for the IndexDetailView screen.

Frontend:
  GET  /indices/{symbol}/news?limit=50
  POST /indices/{symbol}/news/enrich
  GET  /indices/{symbol}?range=3M&interval=daily
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any
import logging
import re
import traceback

from app.api.error_response import (
    ErrorCode,
    error_response_from_exception,
    make_error_response,
    upstream_error_response,
)
from app.services.index_service import get_index_service
from app.schemas.index import IndexDetailResponse
from app.schemas.news import (
    MAX_ENRICH_ARTICLE_IDS,
    EnrichNewsResponse,
    TickerNewsFeedResponse,
    news_articles_from_rows,
    news_feed_from_payload,
    sanitize_article_ids,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Top-weighted constituent tickers for news queries
_INDEX_NEWS_TICKERS: Dict[str, str] = {
    "^GSPC": "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,BRK-B,JPM,V",
    "^DJI":  "AAPL,MSFT,AMZN,NVDA,JPM,V,UNH,HD,PG,JNJ",
    "^IXIC": "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,COST,NFLX",
}


# Normalization always prefixes '^', so a valid index symbol is that caret plus
# a short alphanumeric body. Anything else is malformed input, not a lookup.
_INDEX_SYMBOL_RE = re.compile(r"^\^[A-Z0-9.\-]{1,12}$")


def _normalize_index_symbol(symbol: str) -> str:
    if not symbol.startswith("^") and not symbol.startswith("%5E"):
        symbol = f"^{symbol}"
    return symbol.replace("%5E", "^").upper()


def _invalid_news_symbol(raw: str) -> JSONResponse:
    """Structured INVALID_INPUT for a malformed index symbol (invariant #3)."""
    return make_error_response(
        ErrorCode.INVALID_INPUT,
        message=f"Invalid index symbol for news: {raw[:32]!r}",
        user_message="That symbol isn't valid.",
        details={"symbol": raw[:32]},
    )


# ── News endpoints MUST come before /{symbol} to avoid route conflict ──


@router.get("/{symbol}/news", response_model=TickerNewsFeedResponse)
async def get_index_news(
    symbol: str,
    limit: int = Query(50, ge=1, le=50),
):
    """
    Get news for an index using its top constituent tickers.

    Fetches from FMP, caches in Supabase (same as stock news).
    AI enrichment is NOT automatic — use POST /{symbol}/news/enrich.
    """
    from app.services.news_cache_service import get_news_cache_service

    raw_symbol = symbol
    symbol = _normalize_index_symbol(symbol)
    if not _INDEX_SYMBOL_RE.match(symbol):
        return _invalid_news_symbol(raw_symbol)
    news_tickers = _INDEX_NEWS_TICKERS.get(symbol, "")

    try:
        service = get_news_cache_service()
        feed = await service.get_index_news(symbol, limit, news_tickers=news_tickers)
    except Exception as e:
        logger.error(
            f"Index news failed for {symbol}: {type(e).__name__}: {e}", exc_info=True
        )
        if (resp := upstream_error_response(e, ticker=symbol, step="index_news")) is not None:
            return resp
        raise HTTPException(status_code=500, detail="News service unavailable")

    return news_feed_from_payload(feed, ticker=symbol)


@router.post("/{symbol}/news/enrich", response_model=EnrichNewsResponse)
async def enrich_index_news(
    symbol: str,
    body: Dict[str, Any],
):
    """
    AI-enrich specific index news articles on demand.

    Body: { "article_ids": ["uuid1", "uuid2", ...] }
    """
    from app.services.news_cache_service import get_news_cache_service

    raw_symbol = symbol
    symbol = _normalize_index_symbol(symbol)
    if not _INDEX_SYMBOL_RE.match(symbol):
        return _invalid_news_symbol(raw_symbol)

    raw_ids = body.get("article_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message="article_ids is required (non-empty list)",
            user_message="No articles were requested.",
            details={"symbol": symbol},
        )

    ids = sanitize_article_ids(raw_ids)
    if not ids:
        # Every id was a client-side placeholder — nothing is enrichable yet.
        return EnrichNewsResponse(ticker=symbol, articles=[])
    if len(ids) > MAX_ENRICH_ARTICLE_IDS:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Too many article_ids: {len(ids)} (max {MAX_ENRICH_ARTICLE_IDS})",
            user_message="Too many articles requested at once.",
            details={"symbol": symbol, "count": len(ids)},
        )

    try:
        service = get_news_cache_service()
        enriched = await service.enrich_articles(symbol, ids)
    except Exception as e:
        logger.error(
            f"Index news enrichment failed for {symbol} ({len(ids)} ids): "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        return error_response_from_exception(e, ticker=symbol, step="index_news_enrich")

    return EnrichNewsResponse(ticker=symbol, articles=news_articles_from_rows(enriched))


# ── Main detail endpoint (catch-all /{symbol} MUST be last) ──


@router.get("/{symbol}", response_model=IndexDetailResponse)
async def get_index_detail(
    symbol: str,
    chart_range: str = Query("3M", alias="range", pattern="^(1D|1W|3M|6M|1Y|5Y|ALL)$"),
    interval: Optional[str] = Query(
        None,
        alias="interval",
        pattern="^(1min|5min|15min|30min|1hour|4hour|daily|weekly|monthly|quarterly)$",
    ),
):
    """
    Get comprehensive index detail data.

    Cache-aside: Returns Supabase-cached data if fresh (< 24h),
    otherwise fetches live from FMP + Gemini and caches.
    """
    # Normalize symbol
    if not symbol.startswith("^") and not symbol.startswith("%5E"):
        symbol = f"^{symbol}"
    symbol = symbol.replace("%5E", "^").upper()

    try:
        service = get_index_service()
        result = await service.get_index_detail(
            symbol, chart_range=chart_range, interval=interval
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        # Typed structured error (invariant #3) — surfaces FMP rate-limits as an
        # actionable message + retry instead of a generic "Server error".
        logger.error(
            f"Index detail failed for {symbol} "
            f"(range={chart_range}, interval={interval}): {type(e).__name__}: {e}",
            exc_info=True,
        )
        return error_response_from_exception(e, ticker=symbol, step="index_detail")
