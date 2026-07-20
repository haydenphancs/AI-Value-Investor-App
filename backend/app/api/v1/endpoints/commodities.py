"""
Commodity Endpoints — Aggregated data for the CommodityDetailView screen.

Frontend:
  GET  /commodities/{symbol}/news?limit=50
  POST /commodities/{symbol}/news/enrich
  GET  /commodities/{symbol}?range=3M&interval=daily
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any
import logging
import re

from app.api.error_response import (
    ErrorCode,
    error_response_from_exception,
    make_error_response,
    upstream_error_response,
)
from app.services.commodity_service import get_commodity_service
from app.schemas.commodity import CommodityDetailResponse
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


# ETF/stock proxies for commodity news queries (FMP has no commodity news)
_COMMODITY_NEWS_TICKERS: Dict[str, str] = {
    "GC": "GLD,IAU,GOLD,NEM,AEM",
    "SI": "SLV,PAAS,WPM,AG",
    "CL": "USO,XLE,CVX,XOM,OXY",
    "NG": "UNG,LNG,AR,EQT",
    "HG": "COPX,FCX,SCCO",
    "PL": "PPLT,SBSW",
    "PA": "PALL,SBSW",
    "ZW": "WEAT,ADM,BG",
    "ZC": "CORN,ADM,BG",
    "ZS": "SOYB,ADM,BG",
    "KC": "JO,SBUX",
    "SB": "CANE,SGG",
    "CC": "NIB,HSY",
    "CT": "BAL",
}


# Futures roots are 1-4 alphanumerics (GC, CL, ZW). Note that normalization can
# empty the string ("USD" → ""), which this also rejects before it becomes a
# "COMMODITY_" cache key matching every commodity at once.
_COMMODITY_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,6}$")


def _normalize_commodity_symbol(symbol: str) -> str:
    """Strip USD suffix and uppercase: GCUSD → GC, gc → GC."""
    return symbol.upper().replace("USD", "")


def _invalid_news_symbol(raw: str) -> JSONResponse:
    """Structured INVALID_INPUT for a malformed commodity symbol (invariant #3)."""
    return make_error_response(
        ErrorCode.INVALID_INPUT,
        message=f"Invalid commodity symbol for news: {raw[:32]!r}",
        user_message="That symbol isn't valid.",
        details={"symbol": raw[:32]},
    )


# ── News endpoints MUST come before /{symbol} to avoid route conflict ──


@router.get("/{symbol}/news", response_model=TickerNewsFeedResponse)
async def get_commodity_news(
    symbol: str,
    limit: int = Query(50, ge=1, le=50),
):
    """
    Get news for a commodity using ETF/stock proxy tickers.

    FMP has no commodity-specific news, so we fetch news for related
    ETFs and mining/energy stocks (e.g., Gold → GLD, IAU, NEM).
    """
    from app.services.news_cache_service import get_news_cache_service

    base = _normalize_commodity_symbol(symbol)
    if not _COMMODITY_SYMBOL_RE.match(base):
        return _invalid_news_symbol(symbol)
    news_tickers = _COMMODITY_NEWS_TICKERS.get(base, "")
    cache_key = f"COMMODITY_{base}"

    try:
        service = get_news_cache_service()
        feed = await service.get_index_news(cache_key, limit, news_tickers=news_tickers)
    except Exception as e:
        logger.error(
            f"Commodity news failed for {base}: {type(e).__name__}: {e}", exc_info=True
        )
        if (resp := upstream_error_response(e, ticker=base, step="commodity_news")) is not None:
            return resp
        raise HTTPException(status_code=500, detail="News service unavailable")

    return news_feed_from_payload(feed, ticker=cache_key)


@router.post("/{symbol}/news/enrich", response_model=EnrichNewsResponse)
async def enrich_commodity_news(
    symbol: str,
    body: Dict[str, Any],
):
    """
    AI-enrich specific commodity news articles on demand.

    Body: { "article_ids": ["uuid1", "uuid2", ...] }
    """
    from app.services.news_cache_service import get_news_cache_service

    base = _normalize_commodity_symbol(symbol)
    if not _COMMODITY_SYMBOL_RE.match(base):
        return _invalid_news_symbol(symbol)
    cache_key = f"COMMODITY_{base}"

    raw_ids = body.get("article_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message="article_ids is required (non-empty list)",
            user_message="No articles were requested.",
            details={"symbol": cache_key},
        )

    ids = sanitize_article_ids(raw_ids)
    if not ids:
        # Every id was a client-side placeholder — nothing is enrichable yet.
        return EnrichNewsResponse(ticker=cache_key, articles=[])
    if len(ids) > MAX_ENRICH_ARTICLE_IDS:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Too many article_ids: {len(ids)} (max {MAX_ENRICH_ARTICLE_IDS})",
            user_message="Too many articles requested at once.",
            details={"symbol": cache_key, "count": len(ids)},
        )

    try:
        service = get_news_cache_service()
        enriched = await service.enrich_articles(cache_key, ids)
    except Exception as e:
        logger.error(
            f"Commodity news enrichment failed for {base} ({len(ids)} ids): "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        return error_response_from_exception(
            e, ticker=base, step="commodity_news_enrich"
        )

    return EnrichNewsResponse(
        ticker=cache_key, articles=news_articles_from_rows(enriched)
    )


# ── Main detail endpoint (catch-all /{symbol} MUST be last) ──


@router.get("/{symbol}", response_model=CommodityDetailResponse)
async def get_commodity_detail(
    symbol: str,
    chart_range: str = Query(
        "3M",
        alias="range",
        pattern="^(1D|1W|3M|6M|1Y|5Y|ALL)$",
    ),
    interval: Optional[str] = Query(
        None,
        alias="interval",
        pattern="^(1min|5min|15min|30min|1hour|4hour|daily|weekly|monthly|quarterly)$",
    ),
):
    """
    Get comprehensive commodity detail data.

    Returns price, chart data, key statistics, performance, and news.
    Supports symbols like GC (Gold), SI (Silver), CL (Crude Oil), NG (Natural Gas).
    """
    symbol = symbol.upper().replace("USD", "")

    try:
        service = get_commodity_service()
        result = await service.get_commodity_detail(
            symbol, chart_range=chart_range, interval=interval
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        # Typed structured error (invariant #3) — surfaces FMP rate-limits as an
        # actionable message + retry instead of a generic "Server error".
        logger.error(f"Commodity detail failed for {symbol}: {type(e).__name__}: {e}", exc_info=True)
        return error_response_from_exception(e, ticker=symbol, step="commodity_detail")
