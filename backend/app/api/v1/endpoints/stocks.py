"""
Stock Endpoints — All data from FMP API (no local stocks table).
Frontend: GET /stocks/search, /stocks/{ticker}, /stocks/{ticker}/quote,
          /stocks/{ticker}/fundamentals, /stocks/{ticker}/news
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client
from typing import List, Dict, Any
import logging

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client, FMPClient
from app.schemas.common import normalize_fmp_response, normalize_fmp_list

logger = logging.getLogger(__name__)

router = APIRouter()

# Major US stock exchanges — used to filter search results.
# FMP stable API returns these in the "exchange" field.
_US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}


def _filter_us_stocks(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep only primary US-listed equities.

    Filters out:
    - International listings (APC.F, AAPL.MX, AAPL.DE, etc.)
    - Stocks on non-US exchanges (XETRA, BMV, LSE, etc.)
    - OTC / crypto / non-equity instruments
    """
    filtered = []
    for item in results:
        symbol = item.get("symbol", "")
        # FMP stable API uses "exchange", NOT "exchangeShortName"
        exchange = (item.get("exchange") or "").upper().strip()

        # Skip symbols with dots — international exchange suffixes
        if "." in symbol:
            continue

        # Only keep stocks from major US exchanges
        if exchange not in _US_EXCHANGES:
            continue

        filtered.append(item)
    return filtered


@router.get("/search")
async def search_stocks(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, le=50),
):
    """Search stocks by ticker or company name via FMP."""
    fmp = get_fmp_client()
    try:
        # Over-fetch to compensate for international/fund results we'll discard
        raw = await fmp.search_stocks(q, limit=max(limit * 3, 30))
        if not raw:
            return []
        us_only = _filter_us_stocks(raw)
        return normalize_fmp_list(us_only[:limit])
    except Exception as e:
        logger.error(f"Stock search failed: {e}")
        raise HTTPException(status_code=502, detail="Stock search service unavailable")


@router.get("/{ticker}")
async def get_stock_details(ticker: str):
    """Get detailed company profile from FMP."""
    fmp = get_fmp_client()
    try:
        profile = await fmp.get_company_profile(ticker)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Stock {ticker} not found")
        return normalize_fmp_response(profile)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stock detail failed: {e}")
        raise HTTPException(status_code=502, detail="Stock data service unavailable")


@router.get("/{ticker}/quote")
async def get_stock_quote(ticker: str):
    """Get real-time stock quote from FMP."""
    fmp = get_fmp_client()
    try:
        quote = await fmp.get_stock_price_quote(ticker)
        if not quote:
            raise HTTPException(status_code=404, detail=f"Quote for {ticker} not found")
        return normalize_fmp_response(quote)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stock quote failed: {e}")
        raise HTTPException(status_code=502, detail="Quote service unavailable")


@router.get("/{ticker}/fundamentals")
async def get_stock_fundamentals(ticker: str):
    """Get key financial metrics and ratios from FMP."""
    fmp = get_fmp_client()
    try:
        metrics = await fmp.get_key_metrics(ticker, period="annual", limit=5)
        ratios = await fmp.get_financial_ratios(ticker, period="annual", limit=5)
        return {
            "key_metrics": normalize_fmp_list(metrics) if metrics else [],
            "financial_ratios": normalize_fmp_list(ratios) if ratios else [],
        }
    except Exception as e:
        logger.error(f"Fundamentals failed: {e}")
        raise HTTPException(status_code=502, detail="Fundamentals service unavailable")


@router.get("/{ticker}/news")
async def get_stock_news(
    ticker: str,
    limit: int = Query(10, le=50),
    supabase: Client = Depends(get_supabase),
):
    """Get news articles related to a specific ticker from DB."""
    try:
        result = supabase.table("news_articles").select("*").contains(
            "related_tickers", [ticker.upper()]
        ).order("published_at", desc=True).limit(limit).execute()

        return result.data
    except Exception as e:
        logger.error(f"Stock news failed: {e}")
        raise HTTPException(status_code=500, detail="News service unavailable")
