"""
ETF Endpoints — Aggregated data for the ETFDetailView screen.

Frontend: GET /api/v1/etfs/{symbol}?range=3M&interval=daily
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

from app.services.etf_service import get_etf_service
from app.schemas.etf import ETFDetailResponse, ETFDividendHistoryResponse, ETFHoldingsRiskResponse, ETFProfileResponse

logger = logging.getLogger(__name__)

router = APIRouter()


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
        logger.error(f"ETF detail failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"ETF data service unavailable for {symbol}",
        )


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
