"""
Commodity Endpoints — Aggregated data for the CommodityDetailView screen.

Frontend:
  GET  /commodities/{symbol}/news?limit=50
  POST /commodities/{symbol}/news/enrich
  GET  /commodities/{symbol}?range=3M&interval=daily
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any
import logging

from app.services.commodity_service import get_commodity_service
from app.schemas.commodity import CommodityDetailResponse

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


def _normalize_commodity_symbol(symbol: str) -> str:
    """Strip USD suffix and uppercase: GCUSD → GC, gc → GC."""
    return symbol.upper().replace("USD", "")


# ── News endpoints MUST come before /{symbol} to avoid route conflict ──


@router.get("/{symbol}/news")
async def get_commodity_news(
    symbol: str,
    limit: int = Query(50, le=50),
):
    """
    Get news for a commodity using ETF/stock proxy tickers.

    FMP has no commodity-specific news, so we fetch news for related
    ETFs and mining/energy stocks (e.g., Gold → GLD, IAU, NEM).
    """
    from app.services.news_cache_service import get_news_cache_service

    base = _normalize_commodity_symbol(symbol)
    news_tickers = _COMMODITY_NEWS_TICKERS.get(base, "")
    cache_key = f"COMMODITY_{base}"

    try:
        service = get_news_cache_service()
        return await service.get_index_news(cache_key, limit, news_tickers=news_tickers)
    except Exception as e:
        logger.error(f"Commodity news failed for {base}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="News service unavailable")


@router.post("/{symbol}/news/enrich")
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
    cache_key = f"COMMODITY_{base}"
    article_ids = body.get("article_ids", [])
    if not article_ids:
        raise HTTPException(status_code=400, detail="article_ids is required")

    try:
        service = get_news_cache_service()
        enriched = await service.enrich_articles(cache_key, article_ids)
        return {"articles": enriched, "ticker": cache_key}
    except Exception as e:
        logger.error(f"Commodity news enrichment failed for {base}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Enrichment service unavailable")


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
        logger.error(f"Commodity detail failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Commodity data service unavailable for {symbol}",
        )
