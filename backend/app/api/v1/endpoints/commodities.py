"""
Commodity Endpoints — Aggregated data for the CommodityDetailView screen.

Frontend: GET /api/v1/commodities/{symbol}?range=3M&interval=daily
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

from app.services.commodity_service import get_commodity_service
from app.schemas.commodity import CommodityDetailResponse

logger = logging.getLogger(__name__)

router = APIRouter()


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
