"""
ETF Endpoints — Aggregated data for the ETFDetailView screen.

Frontend: GET /api/v1/etfs/{symbol}?range=3M
"""

from fastapi import APIRouter, HTTPException, Query
import logging

from app.services.etf_service import get_etf_service
from app.schemas.etf import ETFDetailResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{symbol}", response_model=ETFDetailResponse)
async def get_etf_detail(
    symbol: str,
    chart_range: str = Query(
        "3M", alias="range", pattern="^(1D|1W|3M|6M|1Y|5Y|ALL)$"
    ),
):
    """
    Get comprehensive ETF detail data.

    Returns everything the ETFDetailView needs:
    - Real-time quote (price, change, %)
    - Chart data for the selected range
    - Key statistics (flat + grouped)
    - Performance periods (1M to 10Y)
    - AI-generated snapshots (identity rating, strategy)
    - Net yield analysis (expense ratio vs dividend yield)
    - Holdings & risk metrics (top holdings, sectors, concentration)
    - ETF profile (description, company, inception, etc.)
    - Related ETFs with live quotes
    - News articles
    """
    symbol = symbol.upper()

    try:
        service = get_etf_service()
        result = await service.get_etf_detail(symbol, chart_range=chart_range)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ETF detail failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"ETF data service unavailable for {symbol}",
        )
