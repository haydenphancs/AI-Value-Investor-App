"""
Crypto Endpoints — Aggregated data for the CryptoDetailView screen.

Frontend: GET /crypto/fear-greed
          GET /crypto/{symbol}?range=3M&interval=daily
          GET /crypto/{symbol}/news?limit=50
          POST /crypto/{symbol}/news/enrich
          GET /crypto/{symbol}/sentiment
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any
import logging

from app.api.error_response import error_response_from_exception
from app.services.crypto_service import get_crypto_service
from app.schemas.crypto import CryptoDetailResponse
from app.schemas.technical_analysis import (
    TechnicalAnalysisResponse,
    TechnicalAnalysisDetailResponse,
)
from app.services.technical_analysis_service import get_technical_analysis_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Fear & Greed Index (must be before /{symbol} to avoid route conflict) ──


@router.get("/fear-greed")
async def get_crypto_fear_greed():
    """
    Get Crypto Fear & Greed Index from Alternative.me.

    Returns current value, 7D/30D averages, and 30-day history.
    """
    from app.integrations.alternative_me import (
        get_fear_greed_index,
        compute_fear_greed_summary,
    )

    try:
        entries = await get_fear_greed_index(limit=30)
        return compute_fear_greed_summary(entries)
    except Exception as e:
        logger.error(f"Fear & Greed Index failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=502, detail="Fear & Greed Index unavailable"
        )


# ── Crypto Detail ────────────────────────────────────────────────


@router.get("/{symbol}", response_model=CryptoDetailResponse)
async def get_crypto_detail(
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
    Get comprehensive crypto detail data.

    Returns everything the CryptoDetailView needs:
    - Real-time quote (price, change, %)
    - Chart data for the selected range
    - Key statistics (3 groups)
    - Performance periods (7D to 3Y)
    - AI-enhanced snapshots (origin, tokenomics, catalysts, risks)
    - Static crypto profile
    - Related cryptocurrencies with prices
    - News articles
    """
    symbol = symbol.upper().replace("USD", "")

    try:
        service = get_crypto_service()
        result = await service.get_crypto_detail(
            symbol, chart_range=chart_range, interval=interval
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        # Typed structured error (invariant #3) — surfaces FMP/CoinGecko rate-limits
        # as an actionable message + retry instead of a generic "Server error".
        logger.error(f"Crypto detail failed for {symbol}: {type(e).__name__}: {e}", exc_info=True)
        return error_response_from_exception(e, ticker=symbol, step="crypto_detail")


# ── Crypto News (cache-aside + lazy enrichment) ──────────────────


@router.get("/{symbol}/news")
async def get_crypto_news(
    symbol: str,
    limit: int = Query(50, le=50),
):
    """
    Get news for a crypto symbol (raw + any previously enriched).

    Reuses NewsCacheService with FMP symbol format (e.g. BTCUSD).
    AI enrichment is NOT automatic — use POST /{symbol}/news/enrich.
    """
    from app.services.news_cache_service import get_news_cache_service

    symbol = symbol.upper().replace("USD", "")
    fmp_symbol = f"{symbol}USD"

    try:
        service = get_news_cache_service()
        return await service.get_ticker_news(fmp_symbol, limit, is_crypto=True)
    except Exception as e:
        logger.error(f"Crypto news failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Crypto news service unavailable")


@router.post("/{symbol}/news/enrich")
async def enrich_crypto_news(
    symbol: str,
    body: Dict[str, Any],
):
    """
    AI-enrich specific crypto news articles on demand.

    Body: { "article_ids": ["uuid1", "uuid2", ...] }
    """
    from app.services.news_cache_service import get_news_cache_service

    article_ids = body.get("article_ids", [])
    if not article_ids:
        raise HTTPException(status_code=400, detail="article_ids is required")

    symbol = symbol.upper().replace("USD", "")
    fmp_symbol = f"{symbol}USD"

    try:
        service = get_news_cache_service()
        enriched = await service.enrich_articles(fmp_symbol, article_ids)
        return {"articles": enriched, "ticker": fmp_symbol}
    except Exception as e:
        logger.error(f"Crypto news enrichment failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Enrichment service unavailable")


# ── Crypto Sentiment (reuses stock sentiment service) ────────────


@router.get("/{symbol}/sentiment")
async def get_crypto_sentiment(symbol: str):
    """
    Get sentiment analysis for a crypto symbol.

    Reuses the stock sentiment service which aggregates:
    - News sentiment (keyword classifier on FMP news)
    - Social mentions (ApeWisdom Reddit tracking)
    - Price momentum (FMP quote)

    Uses BTCUSD format for FMP (news + price). ApeWisdom social data
    may not be available (service auto-handles with adaptive weighting).
    """
    from app.services.sentiment_service import get_sentiment_service

    symbol = symbol.upper().replace("USD", "")
    fmp_symbol = f"{symbol}USD"

    try:
        service = get_sentiment_service()
        return await service.get_sentiment(fmp_symbol, social_ticker=symbol, is_crypto=True)
    except Exception as e:
        logger.error(f"Crypto sentiment failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Crypto sentiment service unavailable"
        )


# ── Crypto Technical Analysis ──────────────────────────────────────


@router.get(
    "/{symbol}/technical-analysis",
    response_model=TechnicalAnalysisResponse,
)
async def get_crypto_technical_analysis(symbol: str):
    """
    Get technical analysis gauge data for a crypto symbol.

    Computes 18 indicators (10 MAs + 8 oscillators) on daily and weekly
    timeframes, producing a 0-1 gauge value and overall signal.
    Uses W-SUN weekly resampling (crypto trades 24/7).
    """
    symbol = symbol.upper().replace("USD", "")
    fmp_symbol = f"{symbol}USD"

    try:
        service = get_technical_analysis_service()
        return await service.get_analysis(fmp_symbol)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Crypto technical analysis failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Crypto technical analysis unavailable"
        )


@router.get(
    "/{symbol}/technical-analysis/detail",
    response_model=TechnicalAnalysisDetailResponse,
)
async def get_crypto_technical_analysis_detail(symbol: str):
    """
    Get detailed technical analysis breakdown for a crypto symbol.

    Returns individual indicator values and signals, pivot points,
    volume analysis, Fibonacci retracement, and support/resistance levels.
    """
    symbol = symbol.upper().replace("USD", "")
    fmp_symbol = f"{symbol}USD"

    try:
        service = get_technical_analysis_service()
        return await service.get_analysis_detail(fmp_symbol)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Crypto TA detail failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Crypto technical analysis detail unavailable"
        )
