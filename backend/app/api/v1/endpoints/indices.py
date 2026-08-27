"""
Index Endpoints — Aggregated data for the IndexDetailView screen.

Frontend:
  GET  /indices/{symbol}/news?limit=50
  POST /indices/{symbol}/news/enrich
  GET  /indices/{symbol}?range=3M&interval=daily
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.dependencies import StandardRateLimit
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
from app.schemas.index import (
    IndexCoreResponse,
    IndexDetailResponse,
    IndexQuoteResponse,
)
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
    # Throttled: an unauthenticated caller could otherwise trigger up to
    # MAX_ENRICH_ARTICLE_IDS paid Gemini enrichments per request, unbounded.
    # Per-install for guests (X-Guest-Id); mirrors updates.py's enrich route.
    _rate: None = StandardRateLimit,
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


def _normalize_index_symbol(symbol: str) -> str:
    """`GSPC` / `%5EGSPC` / `^gspc` -> `^GSPC`."""
    if not symbol.startswith("^") and not symbol.startswith("%5E"):
        symbol = f"^{symbol}"
    return symbol.replace("%5E", "^").upper()


@router.get("/{symbol}/core", response_model=IndexCoreResponse)
async def get_index_core(
    symbol: str,
    chart_range: str = Query("3M", alias="range", pattern="^(1D|1W|3M|6M|1Y|5Y|ALL)$"),
    interval: Optional[str] = Query(
        None,
        alias="interval",
        pattern="^(1min|5min|15min|30min|1hour|4hour|daily|weekly|monthly|quarterly)$",
    ),
):
    """FIRST-PAINT slice — the price header, plus the chart when it is already cached.

    Additive and zero blast radius: `/{symbol}` and `IndexDetailResponse` are untouched.
    The client fires this in PARALLEL with the full detail and paints whichever lands
    first, so the screen stops shimmering in ~0.3s instead of the 5.6s a cold `^GSPC`
    build measured. Same shape as `GET /stocks/{ticker}/overview/core`.

    NOT the same thing as `/quote`: that one is a PROJECTION of the full build and is
    therefore exactly as slow on a cold cache. This one is assembled from the two cheap
    sections and never pulls the daily history.

    Declared ABOVE the catch-all `/{symbol}`, which the comment above requires.
    """
    symbol = _normalize_index_symbol(symbol)
    try:
        service = get_index_service()
        return await service.get_index_core(
            symbol, chart_range=chart_range, interval=interval
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Index core failed for %s: %s: %s", symbol, type(e).__name__, e, exc_info=True
        )
        return error_response_from_exception(e, ticker=symbol, step="index_core")


@router.get("/{symbol}/quote", response_model=IndexQuoteResponse)
async def get_index_quote(
    symbol: str,
    chart_range: Optional[str] = Query(
        None, alias="range", pattern="^(1D|1W|3M|6M|1Y|5Y|ALL)$"
    ),
    interval: Optional[str] = Query(
        None,
        alias="interval",
        pattern="^(1min|5min|15min|30min|1hour|4hour|daily|weekly|monthly|quarterly)$",
    ),
):
    """Light refresh slice — level, market status, key stats, optional chart.

    Exists because the iOS 30-second loop and every range-pill tap were re-requesting the
    whole detail payload — including `snapshots_data`, a deep required object graph of
    AI-written stories — to move one number. Same fast-core pattern as
    `GET /commodities/{symbol}/quote` and `GET /etfs/{symbol}/quote`.

    Declared ABOVE the catch-all `/{symbol}`, which the comment above requires.

    Omit `range` to skip chart work entirely: on a daily chart a 30-second refresh cannot
    move a bar, so the loop asks for bars only when the chart is intraday.
    """
    symbol = _normalize_index_symbol(symbol)
    try:
        service = get_index_service()
        return await service.get_index_quote(
            symbol, chart_range=chart_range, interval=interval
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Index quote failed for %s: %s: %s", symbol, type(e).__name__, e, exc_info=True
        )
        return error_response_from_exception(e, ticker=symbol, step="index_quote")


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
    symbol = _normalize_index_symbol(symbol)

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
