"""
Financial Modeling Prep (FMP) API Integration — Stable Endpoints
Handles financial data retrieval for stocks.
Requirements: Section 3.3 - Financial Modeling Prep API for fundamentals

NOTE: FMP deprecated all /api/v3 ("legacy") endpoints after August 31 2025.
      This client uses the /stable/ base URL with query-param-based routing.
"""

import asyncio
import httpx
from datetime import datetime, timezone
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

            # Log rate limit info when headers are present
            remaining = response.headers.get("X-RateLimit-Remaining")
            limit = response.headers.get("X-RateLimit-Limit")
            if remaining is not None:
                remaining_int = int(remaining)
                if remaining_int <= 10:
                    logger.warning(
                        f"FMP rate limit low: {remaining}/{limit} remaining"
                    )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "unknown")
                logger.error(
                    f"FMP rate limit HIT on {endpoint}. "
                    f"Retry-After: {retry_after}s. "
                    f"Limit: {limit}"
                )

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

    # ── Revenue segmentation ─────────────────────────────────────────

    async def get_revenue_product_segmentation(
        self, ticker: str, period: str = "annual", structure: str = "flat"
    ) -> List[Dict[str, Any]]:
        """Get revenue breakdown by product segment."""
        return await self._make_request(
            "revenue-product-segmentation",
            params={
                "symbol": ticker.upper(),
                "period": period,
                "structure": structure,
            },
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

        Uses the historical/earning_calendar/{symbol} endpoint for past dates,
        then merges with earning_calendar?symbol= for upcoming dates.
        """
        symbol = ticker.upper()
        all_dates: set = set()

        # Primary: historical earnings endpoint (past dates)
        try:
            data = await self._make_request(
                f"historical/earning_calendar/{symbol}"
            )
            if isinstance(data, list) and data:
                for item in data:
                    if isinstance(item, dict) and item.get("date"):
                        all_dates.add(item["date"])
                if all_dates:
                    logger.info(f"historical/earning_calendar returned {len(all_dates)} dates for {symbol}")
        except Exception as e:
            logger.warning(f"historical/earning_calendar failed for {symbol}: {e}")

        # Also fetch upcoming earnings so future dates appear on chart
        try:
            data = await self._make_request(
                "earning_calendar", params={"symbol": symbol}
            )
            if isinstance(data, list):
                for item in data:
                    if (isinstance(item, dict)
                            and item.get("date")
                            and item.get("symbol", "").upper() == symbol):
                        all_dates.add(item["date"])
        except Exception as e:
            logger.warning(f"earning_calendar failed for {symbol}: {e}")

        dates = sorted(all_dates, reverse=True)
        logger.info(f"Total earnings dates for {symbol}: {len(dates)}")
        return dates

    # ── Sector & market data ────────────────────────────────────────

    # Sector ETF tickers for fallback sector performance computation
    _SECTOR_ETFS: Dict[str, str] = {
        "Technology": "XLK",
        "Healthcare": "XLV",
        "Financial Services": "XLF",
        "Consumer Cyclical": "XLY",
        "Communication Services": "XLC",
        "Industrials": "XLI",
        "Consumer Defensive": "XLP",
        "Energy": "XLE",
        "Real Estate": "XLRE",
        "Utilities": "XLU",
        "Basic Materials": "XLB",
    }

    async def get_sector_performance(self) -> List[Dict[str, Any]]:
        """
        Get today's sector performance percentages.

        Returns list of dicts with keys like:
          {"sector": "Technology", "changesPercentage": 2.13}

        Falls back to sector ETF quotes if the dedicated endpoint is unavailable.
        """
        # Try snapshot endpoint
        try:
            data = await self._make_request("sector-performance-snapshot")
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass

        # Try legacy endpoint
        try:
            data = await self._make_request("sectors-performance")
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass

        # Fallback: compute from sector ETF quotes
        logger.info("Sector performance endpoints unavailable, using ETF fallback")
        return await self._sector_perf_from_etfs()

    async def _sector_perf_from_etfs(self) -> List[Dict[str, Any]]:
        """Compute sector performance (daily + 1Y) from sector ETF data."""
        try:
            import asyncio as _asyncio
            from datetime import datetime, timedelta
            etf_to_sector = {v: k for k, v in self._SECTOR_ETFS.items()}

            one_year_start = (datetime.utcnow() - timedelta(days=370)).strftime("%Y-%m-%d")
            one_year_end = (datetime.utcnow() - timedelta(days=360)).strftime("%Y-%m-%d")

            async def _fetch_one(symbol: str):
                try:
                    quote = await self.get_stock_price_quote(symbol)
                    # Fetch prices around 1 year ago (10-day window for market holidays)
                    hist = await self.get_historical_prices(
                        symbol, from_date=one_year_start, to_date=one_year_end,
                    )
                    return quote, hist
                except Exception:
                    return None, None

            results = await _asyncio.gather(
                *[_fetch_one(etf) for etf in self._SECTOR_ETFS.values()]
            )
            output = []
            for quote, hist in results:
                if not quote or not isinstance(quote, dict):
                    continue
                sector = etf_to_sector.get(quote.get("symbol"))
                if not sector:
                    continue
                daily_pct = quote.get("changePercentage") or quote.get("changesPercentage") or 0.0
                # Compute 1Y return
                one_year_pct = 0.0
                current_price = quote.get("price", 0)
                # FMP stable returns list directly, legacy returns {"historical": [...]}
                hist_list = hist if isinstance(hist, list) else (hist.get("historical", []) if isinstance(hist, dict) else [])
                if hist_list and current_price:
                    # Use the earliest data point as the 1Y-ago price
                    old_price = hist_list[0].get("close") or hist_list[-1].get("close")
                    if old_price and old_price > 0:
                        one_year_pct = round(((current_price - old_price) / old_price) * 100, 2)
                output.append({
                    "sector": sector,
                    "changesPercentage": round(float(daily_pct), 2),
                    "oneYearPerformance": one_year_pct,
                })
            return output
        except Exception as e:
            logger.warning(f"Sector ETF fallback failed: {e}")
            return []

    async def get_industry_performance(self) -> List[Dict[str, Any]]:
        """
        Get industry-level performance snapshot.

        Returns list of dicts with keys like:
          {"industry": "Consumer Electronics", "sector": "Technology",
           "changesPercentage": 1.5, "exchange": "NASDAQ"}
        """
        try:
            data = await self._make_request("industry-performance-snapshot")
            if isinstance(data, list) and data:
                return data
        except Exception as e:
            logger.warning(f"Industry performance endpoint failed: {e}")
        return []

    async def get_sp500_constituents(self) -> List[Dict[str, Any]]:
        """Get S&P 500 constituent list with symbol, name, sector, subSector."""
        try:
            data = await self._make_request("sp500-constituent")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"S&P 500 constituents fetch failed: {e}")
            return []

    async def get_index_constituents(self, symbol: str) -> List[Dict[str, Any]]:
        """Get constituent list for a given index symbol."""
        endpoint_map = {
            "^GSPC": "sp500-constituent",
            "^DJI": "dowjones-constituent",
            "^IXIC": "nasdaq-constituent",
        }
        endpoint = endpoint_map.get(symbol.upper())
        if not endpoint:
            return []
        try:
            data = await self._make_request(endpoint)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"Index constituents fetch failed for {symbol}: {e}")
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
            params["symbols"] = ticker.upper()
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        try:
            return await self._make_request("news/stock", params=params)
        except Exception as e:
            logger.warning(f"Stock news request failed: {e}")
            return []

    async def get_crypto_news(
        self,
        ticker: Optional[str] = None,
        limit: int = 10,
        page: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get crypto news from FMP (stable API: news/crypto).

        Uses the dedicated crypto news endpoint which properly filters
        by crypto symbols (BTCUSD, ETHUSD, etc.).
        """
        params: Dict[str, Any] = {"limit": limit, "page": page}
        if ticker:
            params["symbols"] = ticker.upper()

        try:
            return await self._make_request("news/crypto", params=params)
        except Exception as e:
            logger.warning(f"Crypto news request failed: {e}")
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

        Stable API uses slash-separated path: etf/info (not etf-info).
        """
        try:
            data = await self._make_request(
                "etf/info", params={"symbol": ticker.upper()}
            )
            if isinstance(data, list) and data:
                return data[0]
            return data if isinstance(data, dict) else {}
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning(
                    f"ETF info endpoint unavailable for {ticker}"
                )
                return {}
            raise

    async def get_etf_holders(
        self, ticker: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get ETF top holdings with weights. Stable path: etf/holdings."""
        try:
            data = await self._make_request(
                "etf/holdings", params={"symbol": ticker.upper()}
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
        """Get ETF sector weightings. Stable path: etf/sector-weightings."""
        try:
            data = await self._make_request(
                "etf/sector-weightings", params={"symbol": ticker.upper()}
            )
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"ETF sector weightings failed for {ticker}: {e}")
            return []

    async def get_dividend_history(
        self, ticker: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get dividend payment history for a stock/ETF.

        Uses the stable ``dividends`` endpoint (the legacy ``stock-dividend``
        path was removed after August 2025).
        """
        try:
            data = await self._make_request(
                "dividends", params={"symbol": ticker.upper()}
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
        """Get quotes for multiple symbols via parallel individual requests.

        The FMP stable API does not support comma-separated symbols in a
        single /quote call, so we fetch each symbol individually in parallel.
        """
        if not symbols:
            return []

        async def _fetch_one(sym: str) -> Optional[Dict[str, Any]]:
            try:
                return await self.get_stock_price_quote(sym)
            except Exception as e:
                logger.warning("Quote fetch failed for %s: %s", sym, e)
                return None

        results = await asyncio.gather(*[_fetch_one(s) for s in symbols])
        return [r for r in results if r]

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
        self, cik: str, year: int = 0, quarter: int = 0
    ) -> List[Dict[str, Any]]:
        """Get industry/sector allocation breakdown for a 13F holder.

        The stable API requires year and quarter parameters.
        If not provided, uses the most recent quarter.
        """
        if year == 0 or quarter == 0:
            from datetime import datetime
            now = datetime.now()
            # Most recent completed quarter
            q = (now.month - 1) // 3
            if q == 0:
                year = now.year - 1
                quarter = 4
            else:
                year = now.year
                quarter = q
        try:
            return await self._make_request(
                "institutional-ownership/holder-industry-breakdown",
                params={"cik": cik, "year": year, "quarter": quarter},
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

    # ── Shares float ────────────────────────────────────────────────

    async def get_shares_float(
        self, ticker: str
    ) -> Dict[str, Any]:
        """Get shares float data (freeFloat %, float shares, outstanding)."""
        try:
            data = await self._make_request(
                "shares-float",
                params={"symbol": ticker.upper()},
            )
            if isinstance(data, list) and data:
                return data[0]
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"Shares float request failed for {ticker}: {e}")
            return {}

    # ── Institutional ownership summary ─────────────────────────────

    async def get_institutional_ownership_summary(
        self, ticker: str
    ) -> Dict[str, Any]:
        """Get total institutional ownership summary for a stock.

        Returns ownershipPercent (total), investorsHolding count,
        totalInvested, and position change data.
        """
        now = datetime.now(timezone.utc)
        month = now.month
        if month <= 3:
            year, quarter = now.year - 1, 4
        elif month <= 6:
            year, quarter = now.year, 1
        elif month <= 9:
            year, quarter = now.year, 2
        else:
            year, quarter = now.year, 3

        try:
            data = await self._make_request(
                "institutional-ownership/symbol-positions-summary",
                params={
                    "symbol": ticker.upper(),
                    "year": year,
                    "quarter": quarter,
                },
            )
            if isinstance(data, list) and data:
                return data[0]
            return data if isinstance(data, dict) else {}
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning(
                    f"Institutional ownership summary unavailable for {ticker}"
                )
                return {}
            raise
        except Exception as e:
            logger.warning(f"Institutional ownership summary failed for {ticker}: {e}")
            return {}

    async def get_institutional_ownership_history(
        self, ticker: str, quarters: int = 5
    ) -> List[Dict[str, Any]]:
        """Get institutional ownership summary for the last N quarters.

        Fetches symbol-positions-summary for each quarter in parallel and
        returns a chronologically sorted list (oldest first).
        """
        now = datetime.now(timezone.utc)
        month = now.month
        if month <= 3:
            cur_year, cur_q = now.year - 1, 4
        elif month <= 6:
            cur_year, cur_q = now.year, 1
        elif month <= 9:
            cur_year, cur_q = now.year, 2
        else:
            cur_year, cur_q = now.year, 3

        # Build list of (year, quarter) going backwards
        yq_pairs = []
        y, q = cur_year, cur_q
        for _ in range(quarters):
            yq_pairs.append((y, q))
            q -= 1
            if q == 0:
                q = 4
                y -= 1

        async def _fetch_quarter(year: int, quarter: int) -> Optional[Dict[str, Any]]:
            try:
                data = await self._make_request(
                    "institutional-ownership/symbol-positions-summary",
                    params={
                        "symbol": ticker.upper(),
                        "year": year,
                        "quarter": quarter,
                    },
                )
                if isinstance(data, list) and data:
                    return data[0]
                return data if isinstance(data, dict) else None
            except Exception as e:
                logger.warning(
                    f"Institutional ownership history failed for "
                    f"{ticker} {year}Q{quarter}: {e}"
                )
                return None

        results = await asyncio.gather(
            *[_fetch_quarter(y, q) for y, q in yq_pairs]
        )
        # Filter out failures and sort chronologically (oldest first)
        valid = [r for r in results if r and r.get("date")]
        valid.sort(key=lambda r: r["date"])
        return valid

    async def get_institutional_ownership_for_quarter(
        self, ticker: str, year: int, quarter: int
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single quarter's institutional ownership summary."""
        try:
            data = await self._make_request(
                "institutional-ownership/symbol-positions-summary",
                params={
                    "symbol": ticker.upper(),
                    "year": year,
                    "quarter": quarter,
                },
            )
            if isinstance(data, list) and data:
                return data[0]
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.warning(
                f"Institutional ownership fetch failed for "
                f"{ticker} {year}Q{quarter}: {e}"
            )
            return None

    # ── Per-ticker institutional holders (stable API) ───────────────

    async def get_institutional_holder(
        self, ticker: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get top institutional holders for a stock via 13F analytics.

        Uses the stable API endpoint:
        institutional-ownership/extract-analytics/holder?symbol={ticker}

        Returns holders sorted by market value with ownership %, change data, etc.
        """
        # Determine most recent quarter
        now = datetime.now(timezone.utc)
        # Use previous quarter (current quarter filings aren't available yet)
        month = now.month
        if month <= 3:
            year, quarter = now.year - 1, 4
        elif month <= 6:
            year, quarter = now.year, 1
        elif month <= 9:
            year, quarter = now.year, 2
        else:
            year, quarter = now.year, 3

        try:
            data = await self._make_request(
                "institutional-ownership/extract-analytics/holder",
                params={
                    "symbol": ticker.upper(),
                    "year": year,
                    "quarter": quarter,
                    "page": 0,
                    "limit": limit,
                },
            )
            if isinstance(data, list):
                return data[:limit]
            return []
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning(
                    f"Institutional holder analytics unavailable for {ticker} "
                    f"(tried {year}Q{quarter})"
                )
                return []
            raise
        except Exception as e:
            logger.warning(f"Institutional holder request failed for {ticker}: {e}")
            return []

    # ── Per-ticker insider trading (stable API) ────────────────────

    async def get_insider_trading(
        self, ticker: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get insider trading history for a stock ticker (stable API path)."""
        try:
            data = await self._make_request(
                "insider-trading/search",
                params={"symbol": ticker.upper(), "limit": limit, "page": 0},
            )
            return data if isinstance(data, list) else []
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning(
                    f"Insider trading search unavailable for {ticker}"
                )
                return []
            raise
        except Exception as e:
            logger.warning(f"Insider trading search failed for {ticker}: {e}")
            return []

    async def get_insider_trading_statistics(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """Get quarterly insider trading statistics for a stock ticker."""
        try:
            data = await self._make_request(
                "insider-trading/statistics",
                params={"symbol": ticker.upper()},
            )
            return data if isinstance(data, list) else []
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning(
                    f"Insider trading statistics unavailable for {ticker}"
                )
                return []
            raise
        except Exception as e:
            logger.warning(f"Insider trading statistics failed for {ticker}: {e}")
            return []

    # ── Per-ticker insider roster (derive from trade data) ────────

    async def get_insider_roster(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """
        Get insider roster for a stock ticker.

        Derives from insider-trading/search data since the dedicated
        insider-roster endpoint is not available on the stable API.
        """
        try:
            trades = await self.get_insider_trading(ticker, limit=100)
            # Deduplicate insiders by name
            seen = {}
            for tx in trades:
                name = tx.get("reportingName", "").strip()
                if not name or name in seen:
                    continue
                seen[name] = {
                    "owner": name,
                    "title": tx.get("typeOfOwner", "Officer"),
                    "numberOfShares": tx.get("securitiesOwned", 0),
                }
            return list(seen.values())
        except Exception as e:
            logger.warning(f"Insider roster derivation failed for {ticker}: {e}")
            return []

    # ── Congressional trading ────────────────────────────────────────

    async def _fetch_congress_pages(
        self, endpoint: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Fetch multiple pages of congress trading data in parallel.

        FMP caps results at 250 per page.  Paginating captures older
        trades that would otherwise be lost (e.g. Pelosi's large AAPL
        sales on later pages).
        """
        PAGE_SIZE = 250
        max_pages = min((limit + PAGE_SIZE - 1) // PAGE_SIZE, 30)

        results = await asyncio.gather(
            *[
                self._make_request(
                    endpoint, params={"limit": PAGE_SIZE, "page": p}
                )
                for p in range(max_pages)
            ],
            return_exceptions=True,
        )

        all_trades: List[Dict[str, Any]] = []
        for r in results:
            if isinstance(r, list):
                all_trades.extend(r)
            elif isinstance(r, Exception):
                logger.warning(f"{endpoint} page fetch error: {r}")
        return all_trades

    async def get_senate_latest(
        self, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Get latest senate trades (all symbols, filter client-side)."""
        try:
            return await self._fetch_congress_pages("senate-latest", limit)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning("Senate latest endpoint unavailable")
                return []
            raise
        except Exception as e:
            logger.warning(f"Senate latest request failed: {e}")
            return []

    async def get_house_latest(
        self, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Get latest house trades (all symbols, filter client-side)."""
        try:
            return await self._fetch_congress_pages("house-latest", limit)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning("House latest endpoint unavailable")
                return []
            raise
        except Exception as e:
            logger.warning(f"House latest request failed: {e}")
            return []

    async def get_senate_disclosure(
        self, symbol: str
    ) -> List[Dict[str, Any]]:
        """Get senate disclosure trades filtered by symbol.

        Falls back to senate-latest with client-side symbol filtering
        if the dedicated endpoint is unavailable on the stable API.
        """
        try:
            data = await self._make_request(
                "senate-disclosure",
                params={"symbol": symbol},
            )
            return data if isinstance(data, list) else []
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                # Fallback: fetch senate-latest and filter by symbol
                logger.info("senate-disclosure 404, falling back to senate-latest + filter")
                all_trades = await self._fetch_congress_pages("senate-latest", 1000)
                symbol_upper = symbol.upper()
                return [
                    t for t in all_trades
                    if (t.get("symbol") or "").upper() == symbol_upper
                ]
            raise
        except Exception as e:
            logger.warning(f"Senate disclosure request failed for {symbol}: {e}")
            return []

    async def get_house_disclosure(
        self, symbol: str
    ) -> List[Dict[str, Any]]:
        """Get house disclosure trades filtered by symbol.

        Falls back to house-latest with client-side symbol filtering
        if the dedicated endpoint is unavailable on the stable API.
        """
        try:
            data = await self._make_request(
                "house-disclosure",
                params={"symbol": symbol},
            )
            return data if isinstance(data, list) else []
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                # Fallback: fetch house-latest and filter by symbol
                logger.info("house-disclosure 404, falling back to house-latest + filter")
                all_trades = await self._fetch_congress_pages("house-latest", 1000)
                symbol_upper = symbol.upper()
                return [
                    t for t in all_trades
                    if (t.get("symbol") or "").upper() == symbol_upper
                ]
            raise
        except Exception as e:
            logger.warning(f"House disclosure request failed for {symbol}: {e}")
            return []

    async def get_senate_trades_by_name(
        self, name: str, limit: int = 7500
    ) -> List[Dict[str, Any]]:
        """Get senate trades by politician name.

        The stable API does not support by-name filtering, so we fetch
        from senate-latest in bulk and filter client-side by the
        ``office`` field (which matches "FirstName LastName").
        """
        try:
            all_trades = await self._fetch_congress_pages(
                "senate-latest", limit
            )
            name_lower = name.lower()
            return [
                t for t in all_trades
                if (t.get("office") or "").lower() == name_lower
                or f"{t.get('firstName', '')} {t.get('lastName', '')}".lower() == name_lower
            ]
        except Exception as e:
            logger.warning(f"Senate trades failed for '{name}': {e}")
            return []

    async def get_house_trades_by_name(
        self, name: str, limit: int = 7500
    ) -> List[Dict[str, Any]]:
        """Get house trades by politician name.

        The stable API does not support by-name filtering, so we fetch
        from house-latest in bulk and filter client-side by the
        ``office`` field.
        """
        try:
            all_trades = await self._fetch_congress_pages(
                "house-latest", limit
            )
            name_lower = name.lower()
            return [
                t for t in all_trades
                if (t.get("office") or "").lower() == name_lower
                or f"{t.get('firstName', '')} {t.get('lastName', '')}".lower() == name_lower
            ]
        except Exception as e:
            logger.warning(f"House trades failed for '{name}': {e}")
            return []

    async def get_company_profiles_batch(
        self, tickers: List[str]
    ) -> List[Dict[str, Any]]:
        """Get company profiles for multiple tickers.

        The stable API's profile endpoint only supports single symbols,
        so we fetch them concurrently with a bounded semaphore.
        """
        if not tickers:
            return []
        sem = asyncio.Semaphore(10)

        async def _fetch_one(symbol: str) -> Optional[Dict]:
            async with sem:
                try:
                    data = await self._make_request(
                        "profile", params={"symbol": symbol.upper()}
                    )
                    if isinstance(data, list) and data:
                        return data[0]
                except Exception:
                    pass
                return None

        results = await asyncio.gather(
            *[_fetch_one(t) for t in tickers[:50]],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, dict)]

    # ── Stock peers ────────────────────────────────────────────────

    async def get_stock_peers(self, ticker: str) -> List[str]:
        """Get peer stock symbols for a given ticker."""
        try:
            data = await self._make_request(
                "stock-peers", params={"symbol": ticker.upper()}
            )
            if isinstance(data, list) and data:
                # Stable API returns list of {symbol, companyName, ...}
                if isinstance(data[0], dict) and "symbol" in data[0]:
                    return [
                        d["symbol"]
                        for d in data
                        if d.get("symbol", "").upper() != ticker.upper()
                    ]
                # Legacy format fallback
                peers = data[0].get("peersList", [])
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
