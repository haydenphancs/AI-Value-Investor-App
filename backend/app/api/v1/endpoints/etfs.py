"""
ETF Endpoints — Aggregated data for the ETFDetailView screen.

Frontend: GET /api/v1/etfs/{symbol}?range=3M&interval=daily
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
from app.services.etf_service import get_etf_service
from app.schemas.etf import ETFDetailResponse, ETFDividendHistoryResponse, ETFHoldingsRiskResponse, ETFProfileResponse
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

# ETF tickers are ordinary NMS symbols (SPY, QQQ, ARKK). Same shape as stocks.
_ETF_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,15}$")


def _invalid_news_symbol(raw: str) -> JSONResponse:
    """Structured INVALID_INPUT for a malformed news symbol (invariant #3)."""
    return make_error_response(
        ErrorCode.INVALID_INPUT,
        message=f"Invalid ETF symbol for news: {raw[:32]!r}",
        user_message="That symbol isn't valid.",
        details={"symbol": raw[:32]},
    )


@router.get("/{symbol}", response_model=ETFDetailResponse)
async def get_etf_detail(
    symbol: str,
    chart_range: str = Query(
        "3M", alias="range", pattern="^(1D|1W|3M|6M|1Y|5Y|ALL)$"
    ),
    interval: Optional[str] = Query(
        None,
        alias="interval",
        pattern="^(1min|5min|15min|30min|1hour|4hour|daily|weekly|monthly|quarterly)$",
    ),
):
    """
    Get comprehensive ETF detail data.
    """
    symbol = symbol.upper()

    try:
        service = get_etf_service()
        result = await service.get_etf_detail(
            symbol, chart_range=chart_range, interval=interval
        )
        return result

    except HTTPException:
        raise
    except Exception as e:
        # Structured typed error (invariant #3) so an FMP rate-limit surfaces as
        # FMP_RATE_LIMITED with an actionable user_message + retry, not a generic
        # "Server error". iOS APIClient decodes the 5xx structured body into
        # businessError(code, message).
        logger.error(f"ETF detail failed for {symbol}: {type(e).__name__}: {e}", exc_info=True)
        return error_response_from_exception(e, ticker=symbol, step="etf_detail")


@router.get("/{symbol}/dividends", response_model=ETFDividendHistoryResponse)
async def get_etf_dividends(symbol: str):
    """
    Get full dividend payment history for an ETF.
    Returns up to 100 historical dividend payments.
    """
    symbol = symbol.upper()

    try:
        service = get_etf_service()
        result = await service.get_dividend_history(symbol)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ETF dividend history failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Dividend history unavailable for {symbol}",
        )


@router.get("/{symbol}/holdings-risk", response_model=ETFHoldingsRiskResponse)
async def get_etf_holdings_risk(symbol: str):
    """
    Get holdings & risk breakdown for an ETF.
    Returns asset allocation, top sectors, top holdings, and concentration analysis.
    """
    symbol = symbol.upper()

    try:
        service = get_etf_service()
        result = await service.get_holdings_risk(symbol)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ETF holdings risk failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Holdings & risk data unavailable for {symbol}",
        )


@router.get("/{symbol}/profile", response_model=ETFProfileResponse)
async def get_etf_profile(symbol: str):
    """
    Get ETF profile data: description, issuer, asset class, inception, domicile, index, website.
    """
    symbol = symbol.upper()

    try:
        service = get_etf_service()
        result = await service.get_profile(symbol)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ETF profile failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"ETF profile unavailable for {symbol}",
        )


# ── ETF News (shared cache-aside + on-demand enrichment) ──────────
# ETFs use the SAME shared `ticker_news_cache` as stocks/indices/commodities
# (via news_cache_service), so an ETF's News tab and its Updates tab show the
# same articles + AI summaries, cross-user. This replaced the old path where
# ETF news was a direct FMP fetch baked into the detail payload — uncached and
# never AI-enriched. `news/stock` serves ETF tickers (SPY, QQQ) fine.


@router.get("/{symbol}/news", response_model=TickerNewsFeedResponse)
async def get_etf_news(
    symbol: str,
    limit: int = Query(50, ge=1, le=50),
):
    """
    Get news for an ETF (raw + any previously enriched), from the shared cache.

    AI enrichment is NOT automatic — use POST /{symbol}/news/enrich.
    """
    from app.services.news_cache_service import get_news_cache_service

    symbol = symbol.strip().upper()
    if not _ETF_SYMBOL_RE.match(symbol):
        return _invalid_news_symbol(symbol)

    try:
        service = get_news_cache_service()
        feed = await service.get_ticker_news(symbol, limit)
    except Exception as e:
        logger.error(
            f"ETF news failed for {symbol}: {type(e).__name__}: {e}", exc_info=True
        )
        if (resp := upstream_error_response(e, ticker=symbol, step="etf_news")) is not None:
            return resp
        raise HTTPException(status_code=500, detail="News service unavailable")

    return news_feed_from_payload(feed, ticker=symbol)


@router.post("/{symbol}/news/enrich", response_model=EnrichNewsResponse)
async def enrich_etf_news(
    symbol: str,
    body: Dict[str, Any],
):
    """
    AI-enrich specific ETF news articles on demand.

    Body: { "article_ids": ["uuid1", "uuid2", ...] }
    """
    from app.services.news_cache_service import get_news_cache_service

    symbol = symbol.strip().upper()
    if not _ETF_SYMBOL_RE.match(symbol):
        return _invalid_news_symbol(symbol)

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
            f"ETF news enrichment failed for {symbol} ({len(ids)} ids): "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        return error_response_from_exception(e, ticker=symbol, step="etf_news_enrich")

    return EnrichNewsResponse(ticker=symbol, articles=news_articles_from_rows(enriched))
