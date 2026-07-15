"""
Index Endpoints — Aggregated data for the IndexDetailView screen.

Frontend:
  GET  /indices/{symbol}/news?limit=50
  POST /indices/{symbol}/news/enrich
  GET  /indices/{symbol}?range=3M&interval=daily
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any
import logging
import traceback

from app.api.error_response import error_response_from_exception
from app.services.index_service import get_index_service
from app.schemas.index import IndexDetailResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# Top-weighted constituent tickers for news queries
_INDEX_NEWS_TICKERS: Dict[str, str] = {
    "^GSPC": "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,BRK-B,JPM,V",
    "^DJI":  "AAPL,MSFT,AMZN,NVDA,JPM,V,UNH,HD,PG,JNJ",
    "^IXIC": "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,COST,NFLX",
}


def _normalize_index_symbol(symbol: str) -> str:
    if not symbol.startswith("^") and not symbol.startswith("%5E"):
        symbol = f"^{symbol}"
    return symbol.replace("%5E", "^").upper()


# ── News endpoints MUST come before /{symbol} to avoid route conflict ──


@router.get("/{symbol}/news")
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

    symbol = _normalize_index_symbol(symbol)
    news_tickers = _INDEX_NEWS_TICKERS.get(symbol, "")

    try:
        service = get_news_cache_service()
        return await service.get_index_news(symbol, limit, news_tickers=news_tickers)
    except Exception as e:
        logger.error(f"Index news failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="News service unavailable")


@router.post("/{symbol}/news/enrich")
async def enrich_index_news(
    symbol: str,
    body: Dict[str, Any],
):
    """
    AI-enrich specific index news articles on demand.

    Body: { "article_ids": ["uuid1", "uuid2", ...] }
    """
    from app.services.news_cache_service import get_news_cache_service

    symbol = _normalize_index_symbol(symbol)
    article_ids = body.get("article_ids", [])
    if not article_ids:
        raise HTTPException(status_code=400, detail="article_ids is required")

    try:
        service = get_news_cache_service()
        enriched = await service.enrich_articles(symbol, article_ids)
        return {"articles": enriched, "ticker": symbol}
    except Exception as e:
        logger.error(f"Index news enrichment failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Enrichment service unavailable")


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
