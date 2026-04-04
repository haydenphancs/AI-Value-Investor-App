"""
Index Endpoints — Aggregated data for the IndexDetailView screen.

Frontend: GET /indices/{symbol}?range=3M&interval=daily
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging
import traceback

from app.services.index_service import get_index_service
from app.schemas.index import IndexDetailResponse

logger = logging.getLogger(__name__)

router = APIRouter()


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
        tb = traceback.format_exc()
        logger.error(
            f"Index detail failed for {symbol} "
            f"(range={chart_range}, interval={interval}): {e}\n{tb}"
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "index_data_unavailable",
                "symbol": symbol,
                "reason": str(e),
                "hint": "Check FMP API key, network, or Supabase connectivity.",
            },
        )
