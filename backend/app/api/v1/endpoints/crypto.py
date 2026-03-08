"""
Crypto Endpoints — Aggregated data for the CryptoDetailView screen.

Frontend: GET /crypto/{symbol}?range=3M&interval=daily
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

from app.services.crypto_service import get_crypto_service
from app.schemas.crypto import CryptoDetailResponse

logger = logging.getLogger(__name__)

router = APIRouter()


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
        logger.error(f"Crypto detail failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Crypto data service unavailable for {symbol}",
        )
