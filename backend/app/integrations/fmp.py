"""
Financial Modeling Prep (FMP) API Integration — Stable Endpoints
Handles financial data retrieval for stocks.
Requirements: Section 3.3 - Financial Modeling Prep API for fundamentals

NOTE: FMP deprecated all /api/v3 ("legacy") endpoints after August 31 2025.
      This client uses the /stable/ base URL with query-param-based routing.
"""

import httpx
from typing import Optional, List, Dict, Any
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class FMPClient:
    """
    Client for Financial Modeling Prep API (stable endpoints).
    Provides company fundamentals, financials, and market data.

    Uses a persistent httpx.AsyncClient with connection pooling for
    efficient connection reuse across concurrent requests.
    """

    def __init__(self):
        """Initialize FMP client with API key from settings."""
        self.base_url = settings.FMP_BASE_URL
        self.api_key = settings.FMP_API_KEY
        self.timeout = settings.HTTP_TIMEOUT_SECONDS
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the persistent AsyncClient with connection pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30,
                ),
            )
        return self._client

    async def close(self):
        """Close the persistent HTTP client. Call on app shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Make HTTP request to FMP API using the persistent client.

        Args:
            endpoint: API endpoint path (relative to base_url)
            params: Optional query parameters

        Returns:
            Parsed JSON response (list or dict)

        Raises:
            httpx.HTTPError: If request fails
        """
        url = f"{self.base_url}/{endpoint}"

        if params is None:
            params = {}
        params["apikey"] = self.api_key

        try:
            client = await self._get_client()
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as e:
            logger.error(f"FMP API request failed: {endpoint} — {e}")
            raise

    # ── Company profile & quote ─────────────────────────────────────

    async def get_company_profile(self, ticker: str) -> Dict[str, Any]:
        """Get company profile and overview."""
        data = await self._make_request(
            "profile", params={"symbol": ticker.upper()}
        )
        return data[0] if data else {}

    async def get_stock_price_quote(self, ticker: str) -> Dict[str, Any]:
        """Get real-time stock quote."""
        data = await self._make_request(
            "quote", params={"symbol": ticker.upper()}
        )
        return data[0] if data else {}

    # ── Financial statements ────────────────────────────────────────

    async def get_income_statement(
        self, ticker: str, period: str = "annual", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get income statements."""
        return await self._make_request(
            "income-statement",
            params={"symbol": ticker.upper(), "period": period, "limit": limit},
        )

    async def get_balance_sheet(
        self, ticker: str, period: str = "annual", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get balance sheets."""
        return await self._make_request(
            "balance-sheet-statement",
            params={"symbol": ticker.upper(), "period": period, "limit": limit},
        )

    async def get_cash_flow_statement(
        self, ticker: str, period: str = "annual", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get cash flow statements."""
        return await self._make_request(
            "cash-flow-statement",
            params={"symbol": ticker.upper(), "period": period, "limit": limit},
        )

    # ── Metrics & ratios ────────────────────────────────────────────

    async def get_key_metrics(
        self, ticker: str, period: str = "annual", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get key financial metrics."""
        return await self._make_request(
            "key-metrics",
            params={"symbol": ticker.upper(), "period": period, "limit": limit},
        )

    async def get_financial_ratios(
        self, ticker: str, period: str = "annual", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get financial ratios (P/E, P/B, debt-to-equity, etc.)."""
        return await self._make_request(
            "ratios",
            params={"symbol": ticker.upper(), "period": period, "limit": limit},
        )

    # ── Market data ─────────────────────────────────────────────────

    async def get_historical_prices(
        self,
        ticker: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get historical daily price data."""
        params: Dict[str, Any] = {"symbol": ticker.upper()}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        return await self._make_request("historical-price-eod/full", params=params)

    async def get_intraday_prices(
        self,
        ticker: str,
        interval: str = "5min",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get intraday price data at the specified interval.

        Args:
            ticker: Symbol (e.g. "AAPL", "BTCUSD")
            interval: One of "1min", "5min", "15min", "30min", "1hour", "4hour"
            from_date: Start date YYYY-MM-DD
            to_date: End date YYYY-MM-DD

        Returns:
            List of price dicts with datetime stamps (e.g. "2025-03-07 10:30:00")
        """
        params: Dict[str, Any] = {"symbol": ticker.upper()}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        data = await self._make_request(
            f"historical-chart/{interval}", params=params
        )
        return data if isinstance(data, list) else []

    async def get_analyst_estimates(
        self, ticker: str, period: str = "annual", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get analyst estimates."""
        return await self._make_request(
            "analyst-estimates",
            params={"symbol": ticker.upper(), "period": period, "limit": limit},
        )

    # ── Analyst grades & price targets ────────────────────────────────

    async def get_grades(
        self, ticker: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get individual analyst grade actions (upgrades/downgrades/initiations)."""
        return await self._make_request(
            "grades", params={"symbol": ticker.upper(), "limit": limit}
        )

    async def get_price_target_consensus(self, ticker: str) -> Dict[str, Any]:
        """Get analyst price target consensus (high, low, consensus, median)."""
        data = await self._make_request(
            "price-target-consensus", params={"symbol": ticker.upper()}
        )
        return data[0] if isinstance(data, list) and data else {}

    async def get_earnings_calendar(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get earnings calendar."""
        params: Dict[str, Any] = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        return await self._make_request("earnings-calendar", params=params)

    async def get_earning_calendar_full(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """Return full earning calendar records for a ticker.

        Includes date, eps, epsEstimated, revenue, revenueEstimated, time
        (amc/bmo), fiscalDateEnding, etc.
        """
        symbol = ticker.upper()
        try:
            data = await self._make_request(
                "earning_calendar", params={"symbol": symbol}
            )
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"earning_calendar_full failed for {symbol}: {e}")
            return []

    async def get_historical_earnings_dates(
        self, ticker: str
    ) -> List[str]:
        """Return list of earnings report dates (yyyy-MM-dd) for a specific ticker.

        Tries the per-symbol `earning_calendar` endpoint first (historical),
        then falls back to `earnings-calendar` with symbol filter.
        """
        symbol = ticker.upper()
        try:
            # Primary: per-symbol historical earnings endpoint
            data = await self._make_request(
                "earning_calendar", params={"symbol": symbol}
            )
            if isinstance(data, list) and data:
                dates = [
                    item["date"] for item in data
                    if isinstance(item, dict) and item.get("date")
                ]
                if dates:
                    logger.info(f"earning_calendar returned {len(dates)} dates for {symbol}")
                    return dates
        except Exception as e:
            logger.warning(f"earning_calendar failed for {symbol}: {e}")

        try:
            # Fallback: general calendar filtered by symbol
            data = await self._make_request(
                "earnings-calendar", params={"symbol": symbol}
            )
            if isinstance(data, list):
                dates = [
                    item["date"] for item in data
                    if isinstance(item, dict) and item.get("date")
                ]
                logger.info(f"earnings-calendar returned {len(dates)} dates for {symbol}")
                return dates
        except Exception as e:
            logger.warning(f"earnings-calendar with symbol failed for {symbol}: {e}")

        return []

    # ── Sector & market data ────────────────────────────────────────

    async def get_sector_performance(self) -> List[Dict[str, Any]]:
        """
        Get today's sector performance percentages.

        Returns list of dicts with keys like:
          {"sector": "Technology", "changesPercentage": "2.13"}
        """
        try:
            return await self._make_request("sectors-performance")
        except Exception as e:
            logger.warning(f"Sector performance endpoint failed: {e}")
            return []

    async def get_stock_news(
        self,
        ticker: Optional[str] = None,
        limit: int = 10,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        page: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get stock or general news from FMP (stable API: news/stock).

        Args:
            ticker: Optional stock/index symbol. If None, returns general news.
            limit: Max articles to return (FMP supports up to 1000).
            from_date: Start date in YYYY-MM-DD format.
            to_date: End date in YYYY-MM-DD format.
            page: Page number for pagination (0-based).

        Returns:
            List of news article dicts with keys: symbol, publishedDate,
            publisher, title, image, site, text, url.
        """
        params: Dict[str, Any] = {"limit": limit, "page": page}
        if ticker:
            params["tickers"] = ticker.upper()
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        try:
            return await self._make_request("news/stock", params=params)
        except Exception as e:
            logger.warning(f"Stock news request failed: {e}")
            return []

    async def get_social_sentiment(
        self, ticker: str, max_pages: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get social sentiment data (StockTwits / Twitter posts, comments, sentiment).

        FMP stable endpoint: social-sentiments/change?symbol=AAPL&page=N
        Returns hourly data. Paginates through up to ``max_pages`` pages
        (~100 records per page) to capture a full 14-day window.
        Returns partial data on error instead of empty list.
        """
        all_data: List[Dict[str, Any]] = []
        try:
            for page in range(max_pages):
                data = await self._make_request(
                    "social-sentiments/change",
                    params={"symbol": ticker.upper(), "page": page},
                )
                if not isinstance(data, list) or not data:
                    break
                all_data.extend(data)
                if len(data) < 100:
                    break  # Partial page = end of data
            return all_data
        except Exception as e:
            logger.warning(f"Social sentiment request failed for {ticker}: {e}")
            return all_data

    async def get_social_sentiment_historical(
        self, ticker: str, limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Get historical daily-aggregated social sentiment data.

        FMP stable endpoint: social-sentiments/historical
        Returns daily data with stocktwitsSentiment, twitterSentiment,
        stocktwitsPosts, twitterPosts, etc.
        Supplements the hourly social-sentiments/change endpoint.
        """
        try:
            data = await self._make_request(
                "social-sentiments/historical",
                params={"symbol": ticker.upper(), "limit": limit},
            )
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(
                f"Historical social sentiment failed for {ticker}: {e}"
            )
            return []

    async def get_news_sentiments_rss(
        self, ticker: str, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """
        Get news articles with FMP-computed sentiment scores.

        FMP stable endpoint: stock-news-sentiments-rss-feed
        Returns articles with 'sentimentScore' (float, -1 to 1)
        and 'sentiment' (Bullish/Bearish/Neutral) fields that
        the regular news/stock endpoint does NOT reliably include.
        """
        try:
            data = await self._make_request(
                "stock-news-sentiments-rss-feed",
                params={"tickers": ticker.upper(), "limit": limit},
            )
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"News sentiments RSS failed for {ticker}: {e}")
            return []

    # ── ETF-specific endpoints ───────────────────────────────────────

    async def get_etf_info(self, ticker: str) -> Dict[str, Any]:
        """
        Get ETF-specific info (expense ratio, AUM, holdings count, etc.).

        NOTE: May not be available on all FMP plans.
        """
        try:
            data = await self._make_request(
                "etf-info", params={"symbol": ticker.upper()}
            )
            if isinstance(data, list) and data:
                return data[0]
            return data if isinstance(data, dict) else {}
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning(
                    f"ETF info endpoint unavailable for {ticker} (may require higher plan)"
                )
                return {}
            raise

    async def get_etf_holders(
        self, ticker: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get ETF top holdings with weights."""
        try:
            data = await self._make_request(
                "etf-holder", params={"symbol": ticker.upper()}
            )
            if isinstance(data, list):
                return data[:limit]
            return []
        except Exception as e:
            logger.warning(f"ETF holders request failed for {ticker}: {e}")
            return []

    async def get_etf_sector_weightings(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """Get ETF sector weightings."""
        try:
            data = await self._make_request(
                "etf-sector-weightings", params={"symbol": ticker.upper()}
            )
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"ETF sector weightings failed for {ticker}: {e}")
            return []

    async def get_dividend_history(
        self, ticker: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get dividend payment history for a stock/ETF."""
        try:
            data = await self._make_request(
                "stock-dividend", params={"symbol": ticker.upper()}
            )
            if isinstance(data, dict):
                historical = data.get("historical", [])
                return historical[:limit]
            if isinstance(data, list):
                return data[:limit]
            return []
        except Exception as e:
            logger.warning(f"Dividend history failed for {ticker}: {e}")
            return []

    # ── Batch / crypto helpers ───────────────────────────────────────

    async def get_batch_quotes(
        self, symbols: List[str]
    ) -> List[Dict[str, Any]]:
        """Get quotes for multiple symbols in a single request."""
        if not symbols:
            return []
        symbol_str = ",".join(s.upper() for s in symbols)
        try:
            data = await self._make_request(
                "quote", params={"symbol": symbol_str}
            )
            if isinstance(data, list):
                return data
            return [data] if data else []
        except Exception as e:
            logger.warning(f"Batch quote request failed: {e}")
            return []

    # ── Search ──────────────────────────────────────────────────────

    async def search_stocks(
        self, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search for stocks by name or ticker.

        Uses the stable search-symbol endpoint.  Falls back to
        search-name if the first call returns no results (handles
        cases where the user types a company name instead of a ticker).
        """
        results = await self._make_request(
            "search-symbol",
            params={"query": query, "limit": limit},
        )
        if results:
            return results

        # Fallback: search by company name
        return await self._make_request(
            "search-name",
            params={"query": query, "limit": limit},
        )

    # ── SEC filings (may require higher-tier subscription) ──────────

    async def get_sec_filings(
        self,
        ticker: str,
        filing_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Get SEC filings (10-K, 10-Q, etc.).

        NOTE: This endpoint may not be available on all FMP plans.
        """
        params: Dict[str, Any] = {"symbol": ticker.upper(), "limit": limit}
        if filing_type:
            params["type"] = filing_type

        try:
            return await self._make_request("sec_filings", params=params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning(
                    "SEC filings endpoint unavailable (may require higher plan)"
                )
                return []
            raise

    # ── 13F Institutional ownership ─────────────────────────────────

    async def get_institutional_filing_dates(
        self, cik: str
    ) -> List[Dict[str, Any]]:
        """Get available 13F filing dates for a CIK."""
        try:
            return await self._make_request(
                "institutional-ownership/dates",
                params={"cik": cik},
            )
        except Exception as e:
            logger.warning(f"13F filing dates failed for CIK {cik}: {e}")
            return []

    async def get_institutional_holdings(
        self, cik: str, year: int, quarter: int
    ) -> List[Dict[str, Any]]:
        """Get raw 13F holdings for a specific CIK/quarter."""
        try:
            return await self._make_request(
                "institutional-ownership/extract",
                params={"cik": cik, "year": year, "quarter": quarter},
            )
        except Exception as e:
            logger.warning(
                f"13F holdings failed for CIK {cik} {year}Q{quarter}: {e}"
            )
            return []

    async def get_institutional_industry_breakdown(
        self, cik: str
    ) -> List[Dict[str, Any]]:
        """Get industry/sector allocation breakdown for a 13F holder."""
        try:
            return await self._make_request(
                "institutional-ownership/holder-industry-breakdown",
                params={"cik": cik},
            )
        except Exception as e:
            logger.warning(f"Industry breakdown failed for CIK {cik}: {e}")
            return []

    async def get_institutional_performance(
        self, cik: str
    ) -> Dict[str, Any]:
        """Get performance summary (ytdReturn, etc.) for a 13F holder."""
        try:
            data = await self._make_request(
                "institutional-ownership/holder-performance-summary",
                params={"cik": cik},
            )
            if isinstance(data, list) and data:
                return data[0]
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"Performance summary failed for CIK {cik}: {e}")
            return {}

    # ── Congressional trading ────────────────────────────────────────

    async def get_senate_trades_by_name(
        self, name: str
    ) -> List[Dict[str, Any]]:
        """Get senate trades by politician name."""
        try:
            return await self._make_request(
                "senate-trading-by-name",
                params={"name": name},
            )
        except Exception as e:
            logger.warning(f"Senate trades failed for '{name}': {e}")
            return []

    async def get_house_trades_by_name(
        self, name: str
    ) -> List[Dict[str, Any]]:
        """Get house trades by politician name."""
        try:
            return await self._make_request(
                "house-trading-by-name",
                params={"name": name},
            )
        except Exception as e:
            logger.warning(f"House trades failed for '{name}': {e}")
            return []

    async def get_company_profiles_batch(
        self, tickers: List[str]
    ) -> List[Dict[str, Any]]:
        """Get company profiles for multiple tickers in one call."""
        if not tickers:
            return []
        try:
            symbol_str = ",".join(t.upper() for t in tickers[:50])
            data = await self._make_request(
                "profile", params={"symbol": symbol_str}
            )
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"Batch profiles failed: {e}")
            return []

    # ── Stock peers ────────────────────────────────────────────────

    async def get_stock_peers(self, ticker: str) -> List[str]:
        """Get peer stock symbols for a given ticker."""
        try:
            data = await self._make_request(
                "stock_peers", params={"symbol": ticker.upper()}
            )
            if isinstance(data, list) and data:
                peers = data[0].get("peersList", [])
                # Filter out the ticker itself
                return [p for p in peers if p.upper() != ticker.upper()]
            return []
        except Exception as e:
            logger.warning(f"Stock peers failed for {ticker}: {e}")
            return []

    # ── Company outlook (may require higher-tier subscription) ──────

    async def get_company_outlook(self, ticker: str) -> Dict[str, Any]:
        """
        Get comprehensive company outlook (profile + metrics + ratios).

        NOTE: This endpoint may not be available on all FMP plans.
        """
        try:
            data = await self._make_request(
                "company-outlook", params={"symbol": ticker.upper()}
            )
            return data if isinstance(data, dict) else {}
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning(
                    "Company outlook endpoint unavailable (may require higher plan)"
                )
                return {}
            raise


# ── Singleton ───────────────────────────────────────────────────────

_fmp_client: Optional[FMPClient] = None


def get_fmp_client() -> FMPClient:
    """Get or create global FMP client instance."""
    global _fmp_client
    if _fmp_client is None:
        _fmp_client = FMPClient()
    return _fmp_client


async def close_fmp_client():
    """Close the global FMP client. Call from app lifespan shutdown."""
    global _fmp_client
    if _fmp_client is not None:
        await _fmp_client.close()
        _fmp_client = None
