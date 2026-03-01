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
    """

    def __init__(self):
        """Initialize FMP client with API key from settings."""
        self.base_url = settings.FMP_BASE_URL
        self.api_key = settings.FMP_API_KEY
        self.timeout = settings.HTTP_TIMEOUT_SECONDS

    async def _make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Make HTTP request to FMP API.

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
            async with httpx.AsyncClient(timeout=self.timeout) as client:
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

    async def get_analyst_estimates(
        self, ticker: str, period: str = "annual", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get analyst estimates."""
        return await self._make_request(
            "analyst-estimates",
            params={"symbol": ticker.upper(), "period": period, "limit": limit},
        )

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
