"""
Stock Endpoints — All data from FMP API (no local stocks table).
Frontend: GET /stocks/search, /stocks/{ticker}, /stocks/{ticker}/quote,
          /stocks/{ticker}/fundamentals, /stocks/{ticker}/news
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client
from typing import List, Dict, Any, Optional
import logging

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client, FMPClient
from app.schemas.common import normalize_fmp_response, normalize_fmp_list
from app.schemas.stock import StockSearchResult

logger = logging.getLogger(__name__)

router = APIRouter()

# Major US stock exchanges — used to filter search results.
_US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}


def _get_exchange_short_name(item: Dict[str, Any]) -> Optional[str]:
    """
    Extract the short exchange name (NYSE / NASDAQ / AMEX) from an FMP result.

    FMP APIs return exchange info in varying fields depending on the endpoint
    and API version (stable vs legacy):
      - "exchangeShortName" -> short name  (e.g. "NASDAQ")
      - "exchange"          -> may be short or full
                               (e.g. "NASDAQ" or "NASDAQ Global Select Market")

    This helper normalises both variants.
    """
    # Prefer the explicit short-name field
    short = (item.get("exchangeShortName") or "").strip()
    if short:
        return short

    # Fall back to the generic "exchange" field
    exchange = (item.get("exchange") or "").strip()
    if exchange.upper() in _US_EXCHANGES:
        return exchange

    # Check if the full name contains a known US exchange
    upper = exchange.upper()
    for ex in _US_EXCHANGES:
        if ex in upper:
            return ex

    return exchange or None


def _is_us_stock(item: Dict[str, Any]) -> bool:
    """Return True if the FMP result is a primary US-listed equity."""
    symbol = item.get("symbol", "")

    # Skip international suffixes (APC.F, AAPL.MX, etc.)
    if "." in symbol:
        return False

    short = _get_exchange_short_name(item)
    return (short or "").upper() in _US_EXCHANGES


@router.get("/search", response_model=List[StockSearchResult])
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

        results: List[StockSearchResult] = []
        for item in raw:
            if len(results) >= limit:
                break
            if not _is_us_stock(item):
                continue

            short_name = _get_exchange_short_name(item)
            results.append(StockSearchResult(
                symbol=item.get("symbol", ""),
                name=item.get("name", ""),
                currency=item.get("currency"),
                exchange_short_name=short_name,
                exchange_full_name=item.get("stockExchange") or item.get("exchange"),
            ))

        return results
    except Exception as e:
        logger.error(f"Stock search failed for q={q!r}: {e}")
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
