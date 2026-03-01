"""
Index Endpoints — Aggregated data for the IndexDetailView screen.

Frontend: GET /indices/{symbol}
          GET /indices/{symbol}/chart?range=3M
"""

from fastapi import APIRouter, HTTPException, Query
import logging

from app.services.index_service import get_index_service
from app.schemas.index import IndexDetailResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{symbol}", response_model=IndexDetailResponse)
async def get_index_detail(
    symbol: str,
    chart_range: str = Query("3M", alias="range", pattern="^(1D|1W|3M|6M|1Y|5Y|ALL)$"),
):
    """
    Get comprehensive index detail data.

    Returns everything the IndexDetailView needs:
    - Real-time quote (price, change, %)
    - Chart data for the selected range
    - Key statistics (4 groups)
    - Performance periods (1M to 5Y)
    - AI-enhanced snapshots (valuation, sector, macro)
    - Static index profile
    - News articles
    """
    # Normalize symbol
    if not symbol.startswith("^") and not symbol.startswith("%5E"):
        symbol = f"^{symbol}"
    symbol = symbol.replace("%5E", "^").upper()

    try:
        service = get_index_service()
        result = await service.get_index_detail(symbol, chart_range=chart_range)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Index detail failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Index data service unavailable for {symbol}",
        )
