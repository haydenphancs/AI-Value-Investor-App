"""
Stock Endpoints — All data from FMP API (no local stocks table).
Frontend: GET /stocks/search, /stocks/{ticker}, /stocks/{ticker}/quote,
          /stocks/{ticker}/overview, /stocks/{ticker}/fundamentals,
          /stocks/{ticker}/chart, /stocks/{ticker}/financials-full,
          /stocks/{ticker}/news
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import asyncio
import logging

from app.integrations.fmp import get_fmp_client, FMPClient
from app.schemas.common import normalize_fmp_response, normalize_fmp_list
from app.schemas.stock import StockSearchResult
from app.schemas.stock_overview import StockOverviewResponse
from app.schemas.analyst import AnalystAnalysisResponse
from app.schemas.sentiment import SentimentAnalysisResponse
from app.schemas.technical_analysis import (
    TechnicalAnalysisResponse,
    TechnicalAnalysisDetailResponse,
)
from app.services.stock_overview_service import get_stock_overview_service
from app.services.analyst_service import get_analyst_service
from app.services.earnings_service import get_earnings_service
from app.schemas.earnings import EarningsResponse
from app.services.sentiment_service import get_sentiment_service
from app.services.technical_analysis_service import get_technical_analysis_service

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


# Keywords in the company name that indicate non-company securities
_NON_COMPANY_KEYWORDS = {
    "etf", "fund", "index", "trust", "proshares", "ishares", "vanguard",
    "spdr", "direxion", "wisdomtree", "vaneck", "invesco", "schwab",
    "fidelity", "grayscale", "bitcoin", "ethereum", "crypto", "commodity",
    "bond", "treasury", "municipal", "reit", "futures", "leveraged",
    "inverse", "ultra", "3x", "2x", "-x", "short", "bear", "bull",
}


def _is_company(item: Dict[str, Any]) -> bool:
    """Return True if the result looks like an operating company (not ETF/fund/index/crypto)."""
    name = (item.get("name") or "").lower()
    symbol = (item.get("symbol") or "").upper()

    # Check name for non-company keywords
    for kw in _NON_COMPANY_KEYWORDS:
        if kw in name:
            return False

    # Common ETF symbol patterns: 3-5 letter symbols that are known ETF-like
    # Skip symbols that end with common ETF suffixes
    if symbol.endswith(("X", "Q")) and len(symbol) <= 4:
        # Many mutual funds end in X, but so do real companies (e.g., SLX, NVAX)
        # Only filter if name also looks fund-like
        pass

    return True


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
            if not _is_company(item):
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


@router.get("/{ticker}/overview", response_model=StockOverviewResponse)
async def get_stock_overview(
    ticker: str,
    chart_range: str = Query("3M", alias="range", pattern="^(1D|1W|3M|6M|1Y|5Y|ALL)$"),
    interval: Optional[str] = Query(
        None,
        alias="interval",
        pattern="^(1min|5min|15min|30min|1hour|4hour|daily|weekly|monthly|quarterly)$",
    ),
    extended_hours: bool = Query(False, alias="extended_hours"),
):
    """
    Get comprehensive stock overview data for the Overview tab.

    Returns everything the TickerDetailView Overview tab needs in a single call:
    key stats, performance, snapshots, sector info, company profile,
    related tickers, and benchmark summary.
    Set extended_hours=true to include pre-market and after-hours data (intraday only).
    """
    ticker = ticker.upper()
    try:
        service = get_stock_overview_service()
        return await service.get_overview(ticker, chart_range=chart_range, interval=interval, extended_hours=extended_hours)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stock overview failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Stock overview service unavailable for {ticker}",
        )


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
        metrics, ratios = await asyncio.gather(
            fmp.get_key_metrics(ticker, period="annual", limit=5),
            fmp.get_financial_ratios(ticker, period="annual", limit=5),
        )
        return {
            "key_metrics": normalize_fmp_list(metrics) if metrics else [],
            "financial_ratios": normalize_fmp_list(ratios) if ratios else [],
        }
    except Exception as e:
        logger.error(f"Fundamentals failed: {e}")
        raise HTTPException(status_code=502, detail="Fundamentals service unavailable")


# ── Date-range helpers for the chart endpoint ──────────────────────

_RANGE_DELTAS = {
    "1D": timedelta(days=5),   # Fetch ~5 calendar days to guarantee 2 trading days
    "1W": timedelta(weeks=1),
    "3M": timedelta(days=90),
    "6M": timedelta(days=180),
    "1Y": timedelta(days=365),
    "5Y": timedelta(days=365 * 5),
}


def _chart_date_range(range_code: str):
    """Return (from_date, to_date) ISO strings for the given range code."""
    today = datetime.utcnow().date()
    to_date = today.isoformat()

    if range_code == "ALL":
        # FMP caps results when from_date is omitted; use explicit old date
        return "1970-01-01", to_date

    delta = _RANGE_DELTAS.get(range_code)
    if delta is None:
        return None, None

    from_date = (today - delta).isoformat()
    return from_date, to_date


# ── Chart endpoint ─────────────────────────────────────────────────

@router.get("/{ticker}/chart")
async def get_stock_chart(
    ticker: str,
    range: str = Query("3M", regex="^(1D|1W|3M|6M|1Y|5Y|ALL)$"),
    interval: Optional[str] = Query(
        None,
        alias="interval",
        pattern="^(1min|5min|15min|30min|1hour|4hour|daily|weekly|monthly|quarterly)$",
    ),
    extended_hours: bool = Query(False, alias="extended_hours"),
):
    """
    Get historical price data for charting.

    Supported ranges: 1D, 1W, 3M, 6M, 1Y, 5Y, ALL.
    Optional interval: 1min, 5min, 15min, 30min, 1hour, 4hour, daily, weekly, monthly, quarterly.
    Defaults: 1D→5min, 1W→1hour, others→daily.
    Set extended_hours=true to include pre-market and after-hours data (intraday only).
    """
    from app.services.chart_helper import fetch_chart_data

    fmp = get_fmp_client()
    try:
        prices = await fetch_chart_data(fmp, ticker.upper(), range, interval, extended_hours=extended_hours)
        return {"symbol": ticker.upper(), "prices": prices}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chart data failed for {ticker}: {e}")
        raise HTTPException(status_code=502, detail="Chart data service unavailable")


# ── Full financials endpoint ───────────────────────────────────────

@router.get("/{ticker}/financials-full")
async def get_stock_financials_full(ticker: str):
    """
    Get comprehensive financial data for a ticker.

    Returns income statements, balance sheets, cash flow statements (each with
    annual and quarterly periods), plus key metrics, financial ratios, and
    analyst estimates.  All FMP calls are made in parallel for performance.
    """
    fmp = get_fmp_client()

    try:
        (
            income_annual,
            income_quarterly,
            balance_annual,
            balance_quarterly,
            cashflow_annual,
            cashflow_quarterly,
            key_metrics,
            fin_ratios,
            analyst_est,
        ) = await asyncio.gather(
            fmp.get_income_statement(ticker, period="annual", limit=5),
            fmp.get_income_statement(ticker, period="quarter", limit=8),
            fmp.get_balance_sheet(ticker, period="annual", limit=5),
            fmp.get_balance_sheet(ticker, period="quarter", limit=8),
            fmp.get_cash_flow_statement(ticker, period="annual", limit=5),
            fmp.get_cash_flow_statement(ticker, period="quarter", limit=8),
            fmp.get_key_metrics(ticker, period="annual", limit=5),
            fmp.get_financial_ratios(ticker, period="annual", limit=5),
            fmp.get_analyst_estimates(ticker, period="annual", limit=3),
        )

        return {
            "symbol": ticker.upper(),
            "income_statement": {
                "annual": normalize_fmp_list(income_annual) if income_annual else [],
                "quarterly": normalize_fmp_list(income_quarterly) if income_quarterly else [],
            },
            "balance_sheet": {
                "annual": normalize_fmp_list(balance_annual) if balance_annual else [],
                "quarterly": normalize_fmp_list(balance_quarterly) if balance_quarterly else [],
            },
            "cash_flow": {
                "annual": normalize_fmp_list(cashflow_annual) if cashflow_annual else [],
                "quarterly": normalize_fmp_list(cashflow_quarterly) if cashflow_quarterly else [],
            },
            "key_metrics": normalize_fmp_list(key_metrics) if key_metrics else [],
            "financial_ratios": normalize_fmp_list(fin_ratios) if fin_ratios else [],
            "analyst_estimates": normalize_fmp_list(analyst_est) if analyst_est else [],
        }

    except Exception as e:
        logger.error(f"Financials-full failed for {ticker}: {e}")
        raise HTTPException(
            status_code=502, detail="Financial data service unavailable"
        )


@router.get("/{ticker}/news")
async def get_stock_news(
    ticker: str,
    limit: int = Query(50, le=50),
):
    """
    Get news for a specific ticker (raw + any previously enriched).

    Fetches up to 50 articles from FMP, caches all in Supabase.
    AI enrichment is NOT automatic — use POST /{ticker}/news/enrich
    to enrich specific articles on demand.
    """
    from app.services.news_cache_service import get_news_cache_service

    try:
        service = get_news_cache_service()
        return await service.get_ticker_news(ticker.upper(), limit)
    except Exception as e:
        logger.error(f"Stock news failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="News service unavailable")


@router.post("/{ticker}/news/enrich")
async def enrich_stock_news(
    ticker: str,
    body: Dict[str, Any],
):
    """
    AI-enrich specific news articles on demand.

    Body: { "article_ids": ["uuid1", "uuid2", ...] }

    Only processes articles that haven't been enriched yet.
    Returns the enriched article data.
    """
    from app.services.news_cache_service import get_news_cache_service

    article_ids = body.get("article_ids", [])
    if not article_ids:
        raise HTTPException(status_code=400, detail="article_ids is required")

    try:
        service = get_news_cache_service()
        enriched = await service.enrich_articles(ticker.upper(), article_ids)
        return {"articles": enriched, "ticker": ticker.upper()}
    except Exception as e:
        logger.error(f"News enrichment failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Enrichment service unavailable")


# ── Analyst analysis endpoint ─────────────────────────────────────

@router.get("/{ticker}/analyst-analysis", response_model=AnalystAnalysisResponse)
async def get_analyst_analysis(ticker: str):
    """
    Get comprehensive analyst analysis data for a ticker.

    Returns analyst consensus rating, price targets, rating distribution,
    momentum trends, and individual analyst actions (upgrades/downgrades).
    """
    ticker = ticker.upper()
    try:
        service = get_analyst_service()
        return await service.get_analysis(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analyst analysis failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Analyst analysis service unavailable for {ticker}",
        )


# ── Earnings endpoint ────────────────────────────────────────────

@router.get("/{ticker}/earnings", response_model=EarningsResponse)
async def get_earnings(ticker: str):
    """
    Get quarterly earnings data (EPS & Revenue actuals vs estimates),
    price overlay, and next earnings date.
    """
    ticker = ticker.upper()
    try:
        service = get_earnings_service()
        return await service.get_earnings(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Earnings failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Earnings service unavailable for {ticker}",
        )


# ── Sentiment analysis endpoint ──────────────────────────────────

@router.get("/{ticker}/sentiment", response_model=SentimentAnalysisResponse)
async def get_sentiment_analysis(ticker: str):
    """
    Get sentiment analysis / market mood data for a ticker.

    Aggregates AI-analyzed news sentiment and social media sentiment
    to produce a 0-100 mood score with 24H and 7D breakdowns.
    """
    ticker = ticker.upper()
    try:
        service = get_sentiment_service()
        return await service.get_sentiment(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sentiment analysis failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Sentiment analysis service unavailable for {ticker}",
        )


# ── Technical analysis endpoints ──────────────────────────────

@router.get("/{ticker}/technical-analysis", response_model=TechnicalAnalysisResponse)
async def get_technical_analysis(ticker: str):
    """
    Get technical analysis gauge data for a ticker.

    Computes 18 technical indicators (10 moving averages + 8 oscillators)
    on both daily and weekly timeframes, producing a 0-1 gauge value
    and signal (Strong Sell to Strong Buy).
    """
    ticker = ticker.upper()
    try:
        service = get_technical_analysis_service()
        return await service.get_analysis(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Technical analysis failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Technical analysis service unavailable for {ticker}",
        )


@router.get("/{ticker}/chart-events")
async def get_chart_events(ticker: str):
    """
    Get earnings and ex-dividend dates for chart markers.

    Returns lists of dates (yyyy-MM-dd) so the frontend can render
    "E" (earnings) and "D" (dividend) markers on the price chart.
    """
    ticker = ticker.upper()
    fmp = get_fmp_client()
    try:
        earnings_dates, dividend_data = await asyncio.gather(
            fmp.get_historical_earnings_dates(ticker),
            fmp.get_dividend_history(ticker, limit=50),
        )
        # Defensive extraction — dividend dates can be under different field names
        dividend_dates = []
        for item in dividend_data:
            if not isinstance(item, dict):
                continue
            d = item.get("date") or item.get("recordDate") or ""
            if d:
                dividend_dates.append(d)

        logger.info(
            f"Chart events for {ticker}: "
            f"{len(earnings_dates)} earnings, {len(dividend_dates)} dividends"
        )
        return {
            "earnings_dates": earnings_dates,
            "dividend_dates": dividend_dates,
        }
    except Exception as e:
        logger.error(f"Chart events failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Chart events service unavailable for {ticker}",
        )


@router.get(
    "/{ticker}/technical-analysis/detail",
    response_model=TechnicalAnalysisDetailResponse,
)
async def get_technical_analysis_detail(ticker: str):
    """
    Get detailed technical analysis breakdown for a ticker.

    Returns individual indicator values and signals, pivot points,
    volume analysis, Fibonacci retracement, and support/resistance levels.
    """
    ticker = ticker.upper()
    try:
        service = get_technical_analysis_service()
        return await service.get_analysis_detail(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Technical analysis detail failed for {ticker}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=502,
            detail=f"Technical analysis detail service unavailable for {ticker}",
        )
