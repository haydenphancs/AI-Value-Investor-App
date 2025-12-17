"""
Financial Modeling Prep (FMP) API Integration
Handles financial data retrieval for stocks.
Requirements: Section 3.3 - Financial Modeling Prep API for fundamentals
"""

import httpx
from typing import Optional, List, Dict, Any
import logging
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)


class FMPClient:
    """
    Client for Financial Modeling Prep API.
    Provides company fundamentals, financials, and market data.
    """

    def __init__(self):
        """Initialize FMP client with API key from settings."""
        self.base_url = settings.FMP_BASE_URL
        self.api_key = settings.FMP_API_KEY
        self.timeout = settings.HTTP_TIMEOUT_SECONDS

    async def _make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make HTTP request to FMP API.

        Args:
            endpoint: API endpoint path
            params: Optional query parameters

        Returns:
            dict: API response data

        Raises:
            httpx.HTTPError: If request fails
        """
        url = f"{self.base_url}/{endpoint}"

        # Add API key to params
        if params is None:
            params = {}
        params["apikey"] = self.api_key

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPError as e:
            logger.error(f"FMP API request failed: {e}")
            raise

    async def get_company_profile(self, ticker: str) -> Dict[str, Any]:
        """
        Get company profile and overview.

        Args:
            ticker: Stock ticker symbol

        Returns:
            dict: Company profile data
        """
        data = await self._make_request(f"profile/{ticker.upper()}")
        return data[0] if data else {}

    async def get_income_statement(
        self,
        ticker: str,
        period: str = "annual",
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get income statements.

        Args:
            ticker: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of periods to retrieve

        Returns:
            list: Income statement data
        """
        return await self._make_request(
            f"income-statement/{ticker.upper()}",
            params={"period": period, "limit": limit}
        )

    async def get_balance_sheet(
        self,
        ticker: str,
        period: str = "annual",
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get balance sheets.

        Args:
            ticker: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of periods to retrieve

        Returns:
            list: Balance sheet data
        """
        return await self._make_request(
            f"balance-sheet-statement/{ticker.upper()}",
            params={"period": period, "limit": limit}
        )

    async def get_cash_flow_statement(
        self,
        ticker: str,
        period: str = "annual",
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get cash flow statements.

        Args:
            ticker: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of periods to retrieve

        Returns:
            list: Cash flow data
        """
        return await self._make_request(
            f"cash-flow-statement/{ticker.upper()}",
            params={"period": period, "limit": limit}
        )

    async def get_key_metrics(
        self,
        ticker: str,
        period: str = "annual",
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get key financial metrics and ratios.

        Args:
            ticker: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of periods to retrieve

        Returns:
            list: Key metrics data
        """
        return await self._make_request(
            f"key-metrics/{ticker.upper()}",
            params={"period": period, "limit": limit}
        )

    async def get_financial_ratios(
        self,
        ticker: str,
        period: str = "annual",
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get financial ratios (P/E, P/B, debt-to-equity, etc.).

        Args:
            ticker: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of periods to retrieve

        Returns:
            list: Financial ratios
        """
        return await self._make_request(
            f"ratios/{ticker.upper()}",
            params={"period": period, "limit": limit}
        )

    async def get_earnings_calendar(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get earnings calendar.

        Args:
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            list: Earnings calendar data
        """
        params = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        return await self._make_request("earning_calendar", params=params)

    async def get_stock_price_quote(self, ticker: str) -> Dict[str, Any]:
        """
        Get real-time stock quote.

        Args:
            ticker: Stock ticker symbol

        Returns:
            dict: Stock quote data
        """
        data = await self._make_request(f"quote/{ticker.upper()}")
        return data[0] if data else {}

    async def get_historical_prices(
        self,
        ticker: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get historical daily price data.

        Args:
            ticker: Stock ticker symbol
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            dict: Historical price data
        """
        params = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        return await self._make_request(
            f"historical-price-full/{ticker.upper()}",
            params=params
        )

    async def get_analyst_estimates(
        self,
        ticker: str,
        period: str = "annual",
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get analyst estimates.

        Args:
            ticker: Stock ticker symbol
            period: 'annual' or 'quarter'
            limit: Number of periods to retrieve

        Returns:
            list: Analyst estimates
        """
        return await self._make_request(
            f"analyst-estimates/{ticker.upper()}",
            params={"period": period, "limit": limit}
        )

    async def get_sec_filings(
        self,
        ticker: str,
        filing_type: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get SEC filings (10-K, 10-Q, etc.).
        Section 4.3.3 - REQ-6: Retrieve 10-K reports

        Args:
            ticker: Stock ticker symbol
            filing_type: Optional filing type filter (10-K, 10-Q, 8-K, etc.)
            limit: Number of filings to retrieve

        Returns:
            list: SEC filings
        """
        params = {"limit": limit}
        if filing_type:
            params["type"] = filing_type

        return await self._make_request(
            f"sec_filings/{ticker.upper()}",
            params=params
        )

    async def search_stocks(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for stocks by name or ticker.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            list: Matching stocks
        """
        return await self._make_request(
            "search",
            params={"query": query, "limit": limit}
        )

    async def get_company_outlook(self, ticker: str) -> Dict[str, Any]:
        """
        Get comprehensive company outlook (profile + metrics + ratios).

        Args:
            ticker: Stock ticker symbol

        Returns:
            dict: Company outlook data
        """
        data = await self._make_request(f"company-outlook", params={"symbol": ticker.upper()})
        return data if isinstance(data, dict) else {}


# Global client instance
_fmp_client: Optional[FMPClient] = None


def get_fmp_client() -> FMPClient:
    """
    Get or create global FMP client instance.

    Returns:
        FMPClient: FMP client instance
    """
    global _fmp_client
    if _fmp_client is None:
        _fmp_client = FMPClient()
    return _fmp_client
