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
from app.log_redaction import redact_secrets
from app.utils.period_labels import latest_filed_13f_quarter

logger = logging.getLogger(__name__)


def _normalize_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    # FMP /stable/profile renamed `mktCap` → `marketCap`. Many callers
    # still read `mktCap`; alias it in-place so a single field rename
    # upstream can't silently zero out market-cap-driven logic (peer
    # filtering, competitor floors, sector aggregates).
    if not isinstance(profile, dict):
        return profile
    if profile.get("mktCap") in (None, 0) and profile.get("marketCap") is not None:
        profile["mktCap"] = profile["marketCap"]
    return profile


class FMPException(Exception):
    """Base class for typed FMP integration errors."""


class FMPAuthException(FMPException):
    """Raised on 401 Unauthorized — invalid/expired FMP_API_KEY."""


class FMPRateLimitException(FMPException):
    """Raised on 429 — quota exhausted or burst limit hit."""

    def __init__(self, message: str, retry_after: Optional[str] = None):
        super().__init__(message)
        self.retry_after = retry_after


class FMPUnavailableException(FMPException):
    """Raised when FMP is transiently unavailable — a 5xx (502/503/504) or a
    network drop that persisted through retries. Distinct from a 429 (quota):
    this is upstream flakiness the caller should degrade around, not a bug in
    our code. Logged at WARNING (handled/degraded), so it does NOT page on-call.
    """


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

    # ── Retry config for transient upstream failures ────────────────
    # FMP's gateway intermittently returns 5xx (502/503/504) or drops the
    # connection. These are transient and usually clear on a short retry, so a
    # single blip must NOT fail the request or page on-call. We retry with
    # exponential backoff, then degrade to a typed FMPUnavailableException
    # logged at WARNING (handled/degraded) — keeping it OUT of high-priority
    # Sentry. Real config errors (401) and quota (429) are NOT retried.
    _RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
    _MAX_RETRIES = 2            # 3 attempts total
    _RETRY_BASE_DELAY = 0.5     # seconds; exponential backoff (0.5s, 1.0s)

    async def _make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Make HTTP request to FMP API using the persistent client.

        Transient upstream failures (5xx in _RETRYABLE_STATUS or network drops)
        are retried with exponential backoff; when retries are exhausted, raises
        the typed FMPUnavailableException (logged at WARNING, not ERROR) so
        callers degrade gracefully and transient FMP blips don't page on-call.

        Args:
            endpoint: API endpoint path (relative to base_url)
            params: Optional query parameters

        Returns:
            Parsed JSON response (list or dict)

        Raises:
            FMPAuthException:        401 (bad/expired FMP_API_KEY)
            FMPRateLimitException:   429 (quota / burst limit)
            FMPUnavailableException: transient 5xx / network error after retries
            httpx.HTTPStatusError:   other non-retryable HTTP status (e.g. 400)
        """
        url = f"{self.base_url}/{endpoint}"

        if params is None:
            params = {}
        params["apikey"] = self.api_key

        for attempt in range(self._MAX_RETRIES + 1):
            try:
                client = await self._get_client()
                response = await client.get(url, params=params)

                # Log rate limit info when headers are present
                remaining = response.headers.get("X-RateLimit-Remaining")
                limit = response.headers.get("X-RateLimit-Limit")
                if remaining is not None:
                    try:
                        if int(remaining) <= 10:
                            logger.warning(
                                f"FMP rate limit low: {remaining}/{limit} remaining"
                            )
                    except (TypeError, ValueError):
                        pass

                if response.status_code == 401:
                    logger.error(
                        f"FMP auth failed on {endpoint}: 401 Unauthorized "
                        f"— check FMP_API_KEY in backend/.env"
                    )
                    raise FMPAuthException(
                        f"FMP returned 401 Unauthorized for {endpoint} "
                        f"(check FMP_API_KEY)"
                    )

                if response.status_code == 429:
                    # A 429 is handled/expected backpressure (esp. during the benchmark
                    # recompute's TTM burst): it's raised as a typed exception and the
                    # caller degrades/backs off via return_exceptions. Log at WARNING —
                    # NOT ERROR — so it doesn't page on-call as a bug, matching the
                    # FMPUnavailableException convention and the "warning = rate limits"
                    # logging rule. FMP /stable often omits Retry-After / X-RateLimit-*,
                    # so render those cleanly instead of "unknowns" / "None".
                    retry_after = response.headers.get("Retry-After")
                    logger.warning(
                        "FMP rate limit HIT on %s (Retry-After: %s, X-RateLimit-Limit: %s)",
                        endpoint,
                        retry_after or "not provided",
                        limit if limit is not None else "not provided",
                    )
                    raise FMPRateLimitException(
                        f"FMP rate limit hit on {endpoint}",
                        retry_after=retry_after,
                    )

                # Transient upstream 5xx — retry with backoff, then degrade.
                if response.status_code in self._RETRYABLE_STATUS:
                    if attempt < self._MAX_RETRIES:
                        delay = self._RETRY_BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "FMP %s returned %s — transient, retry %d/%d in %.1fs",
                            endpoint, response.status_code,
                            attempt + 1, self._MAX_RETRIES, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.warning(
                        "FMP %s unavailable: HTTP %s after %d attempts "
                        "(transient upstream)",
                        endpoint, response.status_code, self._MAX_RETRIES + 1,
                    )
                    raise FMPUnavailableException(
                        f"FMP returned {response.status_code} for {endpoint} "
                        f"after {self._MAX_RETRIES + 1} attempts"
                    )

                response.raise_for_status()
                return response.json()

            except httpx.TransportError as e:
                # Network-level transient error (connect/read timeout, reset,
                # protocol error). Retry with backoff, then degrade.
                if attempt < self._MAX_RETRIES:
                    delay = self._RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "FMP %s network error (%s) — retry %d/%d in %.1fs",
                        endpoint, type(e).__name__,
                        attempt + 1, self._MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning(
                    "FMP %s unavailable after %d attempts: %s",
                    endpoint, self._MAX_RETRIES + 1, redact_secrets(e),
                )
                raise FMPUnavailableException(
                    f"FMP request to {endpoint} failed after "
                    f"{self._MAX_RETRIES + 1} attempts: {type(e).__name__}"
                ) from e

            except httpx.HTTPStatusError as e:
                # Non-retryable HTTP status (4xx other than 401/429 handled
                # above, or a 5xx not in the retryable set). 403/404 = expected
                # "no data / endpoint unavailable", routinely handled by callers
                # (e.g. get_senate_disclosure catches the 404 and falls back), so
                # log WARNING to keep them OUT of Sentry; anything else is ERROR.
                # redact_secrets strips the apikey= that httpx echoes in the URL.
                status = getattr(getattr(e, "response", None), "status_code", None)
                log = logger.warning if status in (403, 404) else logger.error
                log(f"FMP API request failed: {endpoint} — {redact_secrets(e)}")
                raise

            except httpx.HTTPError as e:
                # Any other httpx error (decoding, redirect loop) — non-retryable.
                logger.error(
                    f"FMP API request failed: {endpoint} — {redact_secrets(e)}"
                )
                raise

    # ── Company profile & quote ─────────────────────────────────────

    async def get_company_profile(self, ticker: str) -> Dict[str, Any]:
        """Get company profile and overview."""
        data = await self._make_request(
            "profile", params={"symbol": ticker.upper()}
        )
        return _normalize_profile(data[0]) if data else {}

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

    async def get_ratios_ttm(self, ticker: str) -> List[Dict[str, Any]]:
        """Trailing-twelve-month financial ratios.

        Returns the same field shape as `get_financial_ratios` but the
        denominators (earnings, sales, book value, FCF) sum the last 4
        reported quarters instead of the latest fiscal year-end. Use this
        for valuation card display where the user expects values that
        match Webull / Yahoo / TradingView, which all use TTM.
        """
        return await self._make_request(
            "ratios-ttm",
            params={"symbol": ticker.upper()},
        )

    async def get_key_metrics_ttm(self, ticker: str) -> List[Dict[str, Any]]:
        """TTM key metrics (per-share book value, free cash flow yield,
        ROE, ROIC, EV/EBITDA, etc.). TTM counterpart of `get_key_metrics`."""
        return await self._make_request(
            "key-metrics-ttm",
            params={"symbol": ticker.upper()},
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

    async def get_historical_market_cap(
        self,
        ticker: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Daily historical market capitalization.

        Returns ``[{"symbol", "date", "marketCap"}, ...]``. Used to compute
        POINT-IN-TIME shareholder yields (a quarter's dividends/buybacks against
        the market cap at THAT quarter's end) instead of scaling every historical
        quarter by today's cap.
        """
        params: Dict[str, Any] = {"symbol": ticker.upper(), "limit": limit}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        data = await self._make_request(
            "historical-market-capitalization", params=params
        )
        return data if isinstance(data, list) else []

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

        Includes date, eps, epsEstimated, revenue, revenueEstimated.

        FMP renamed the per-symbol earnings endpoint from
        `/api/v3/earning_calendar?symbol=X` to `/stable/earnings?symbol=X`
        (returns BOTH historical and upcoming records in one call). The
        new endpoint ships `epsActual`/`revenueActual` instead of the
        legacy `eps`/`revenue`; we normalize here so the 4 downstream
        helpers (`_find_next_earnings_date(_simple)`) keep working
        unchanged.
        """
        symbol = ticker.upper()
        try:
            data = await self._make_request(
                "earnings", params={"symbol": symbol}
            )
            if not isinstance(data, list):
                return []
            normalized: List[Dict[str, Any]] = []
            for row in data:
                if not isinstance(row, dict):
                    continue
                # Map new field names to legacy names. Keep originals too
                # so any caller that's already on the new schema works.
                if "eps" not in row and "epsActual" in row:
                    row["eps"] = row.get("epsActual")
                if "revenue" not in row and "revenueActual" in row:
                    row["revenue"] = row.get("revenueActual")
                normalized.append(row)
            return normalized
        except Exception as e:
            logger.warning(f"earnings (per-symbol) failed for {symbol}: {e}")
            return []

    async def get_earning_call_transcript(
        self, ticker: str, year: Optional[int] = None, quarter: Optional[int] = None,
    ) -> str:
        """Return the most recent earnings-call transcript text for a ticker.

        FMP exposes the transcript at `/earning_call_transcript`. The
        `year` and `quarter` params are required by the v3 endpoint, so
        when they're not supplied we list available transcripts via
        `earning-call-transcript-list` (newer endpoint) and pick the
        latest. Returns "" on any failure — the caller treats that as
        "transcript unavailable" and falls back to AI-without-transcript.

        Used by:
          - Stage A AI for TAM extraction (PR 3)
          - Stage B guidance/quote extraction (PR 6)
        """
        symbol = ticker.upper()
        # Resolve latest year/quarter when not provided.
        #
        # FMP renamed both endpoints in 2026 (underscores → dashes) and
        # changed the list endpoint's identifying field from `year` to
        # `fiscalYear`. The legacy `earning-call-transcript-list` and
        # `earning_call_transcript` paths now return HTTP 404. We accept
        # both `year` and `fiscalYear` from the listing rows just in case
        # the response shape shifts again.
        if year is None or quarter is None:
            try:
                listing = await self._make_request(
                    "earning-call-transcript-dates", params={"symbol": symbol}
                )
                if isinstance(listing, list) and listing:
                    # Newest first by (fiscalYear desc, quarter desc).
                    def _yr(row: Dict[str, Any]) -> int:
                        return int(row.get("fiscalYear") or row.get("year") or 0)
                    listing.sort(
                        key=lambda x: (_yr(x), int(x.get("quarter") or 0)),
                        reverse=True,
                    )
                    head = listing[0]
                    year = _yr(head)
                    quarter = int(head.get("quarter") or 0)
            except Exception as e:
                logger.warning(
                    f"earning-call-transcript-dates failed for {symbol}: {e}"
                )
                return ""
            if not year or not quarter:
                return ""
        try:
            data = await self._make_request(
                "earning-call-transcript",
                params={"symbol": symbol, "year": year, "quarter": quarter},
            )
            if isinstance(data, list) and data:
                # FMP returns [{symbol, quarter, year, date, content}, ...]
                content = data[0].get("content") or ""
                return content if isinstance(content, str) else ""
            if isinstance(data, dict):
                content = data.get("content") or ""
                return content if isinstance(content, str) else ""
        except Exception as e:
            logger.warning(
                f"earning-call-transcript failed for {symbol} "
                f"({year}Q{quarter}): {e}"
            )
        return ""

    async def get_historical_earnings_dates(
        self, ticker: str
    ) -> List[str]:
        """Return list of earnings report dates (yyyy-MM-dd) for a specific ticker.

        Uses `/stable/earnings?symbol=X`, which returns BOTH past and
        upcoming earnings rows in a single response. Previously this
        method made two FMP calls (`historical/earning_calendar/{X}`
        + `earning_calendar?symbol=X`) — both of those paths were
        renamed in FMP's 2026 migration to `/stable` and now 404.
        """
        symbol = ticker.upper()
        try:
            data = await self._make_request(
                "earnings", params={"symbol": symbol}
            )
        except Exception as e:
            logger.warning(f"earnings (per-symbol) failed for {symbol}: {e}")
            return []

        all_dates: set = set()
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                d = item.get("date")
                if not d:
                    continue
                # Endpoint already filters by symbol, but double-check
                # in case FMP returns a noisy mixed set during plan
                # downgrades.
                if str(item.get("symbol", "")).upper() not in ("", symbol):
                    continue
                all_dates.add(d)

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

    async def _latest_perf_snapshot(
        self, endpoint: str, max_lookback: int = 6
    ) -> List[Dict[str, Any]]:
        """Fetch a ``*-performance-snapshot`` for the latest available trading day.

        FMP changed these endpoints in two ways that broke the old param-less
        calls:
          1. ``date`` is now REQUIRED — a call without it 400s with
             ``"Invalid or missing query parameter - date"``.
          2. The value field was renamed ``changesPercentage`` → ``averageChange``.
        And a non-trading ``date`` (weekend/holiday) returns ``[]`` rather than
        the prior session. So walk back from today (UTC) to the most recent date
        that returns rows, then alias ``averageChange`` → ``changesPercentage``
        (the key downstream consumers read). Returns ``[]`` if nothing is found
        within ``max_lookback`` days — callers keep their existing fallbacks.
        """
        from datetime import timedelta

        today = datetime.now(timezone.utc).date()
        for i in range(max_lookback):
            d = (today - timedelta(days=i)).isoformat()
            data = await self._make_request(endpoint, params={"date": d})
            if isinstance(data, list) and data:
                for row in data:
                    if (
                        isinstance(row, dict)
                        and "averageChange" in row
                        and "changesPercentage" not in row
                    ):
                        row["changesPercentage"] = row["averageChange"]
                return data
        return []

    async def get_sector_performance(self) -> List[Dict[str, Any]]:
        """
        Get the latest trading day's sector performance percentages.

        Returns list of dicts with keys like:
          {"sector": "Technology", "changesPercentage": 2.13, ...}

        Falls back to sector ETF quotes if the snapshot is unavailable.
        """
        # Primary: FMP's dated sector snapshot (see _latest_perf_snapshot for the
        # date-required + field-rename migration). Passing no `date` previously
        # 400'd on every call, silently forcing the ETF fallback below.
        try:
            data = await self._latest_perf_snapshot("sector-performance-snapshot")
            if data:
                return data
        except Exception as e:
            logger.warning(f"Sector performance snapshot failed: {e}")

        # Fallback: compute from sector ETF quotes (approximate; also carries a
        # 1Y figure the snapshot lacks). The old `sectors-performance` endpoint
        # was removed by FMP (now 404) and is intentionally no longer tried.
        logger.info("Sector snapshot unavailable, using ETF fallback")
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
        Get the latest trading day's industry-level performance snapshot.

        Returns list of dicts with keys like:
          {"industry": "Consumer Electronics", "changesPercentage": 1.5,
           "averageChange": 1.5, "exchange": "NASDAQ", "date": "2026-07-03"}

        NOTE: FMP's snapshot no longer includes a ``sector`` field (the param-less
        call also 400'd on every request — see _latest_perf_snapshot). Callers
        that rank an industry *within its sector* therefore need a separate
        industry→sector map; ranking off this response alone will not match.
        """
        try:
            return await self._latest_perf_snapshot("industry-performance-snapshot")
        except Exception as e:
            logger.warning(f"Industry performance snapshot failed: {e}")
            return []

    async def get_sp500_constituents(self) -> List[Dict[str, Any]]:
        """Get S&P 500 constituent list with symbol, name, sector, subSector."""
        try:
            data = await self._make_request("sp500-constituent")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"S&P 500 constituents fetch failed: {e}")
            return []

    # ── Market movers (market-wide, single cheap calls) ──────────────
    # All three return a flat list ranked by FMP; fields:
    #   symbol, name, price, change, changesPercentage, exchange
    # (most-actives is ranked by volume but does NOT expose the volume value).
    # Non-critical → return [] on failure so the caller degrades gracefully.

    async def get_biggest_gainers(self) -> List[Dict[str, Any]]:
        """Market's biggest % gainers today (ranked desc by % change)."""
        try:
            data = await self._make_request("biggest-gainers")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"Biggest gainers fetch failed: {e}")
            return []

    async def get_biggest_losers(self) -> List[Dict[str, Any]]:
        """Market's biggest % losers today (ranked desc by magnitude of decline)."""
        try:
            data = await self._make_request("biggest-losers")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"Biggest losers fetch failed: {e}")
            return []

    async def get_most_actives(self) -> List[Dict[str, Any]]:
        """Most actively traded stocks today (ranked by raw volume)."""
        try:
            data = await self._make_request("most-actives")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"Most actives fetch failed: {e}")
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
        Get news for one or more SYMBOLS from FMP (stable API: news/stock).

        WARNING: passing ``ticker=None`` does NOT return general market news.
        An earlier version of this docstring claimed it did; verified against the
        live API, omitting ``symbols`` makes FMP fall back to a single default
        symbol (AAPL), so the caller silently gets an all-Apple feed. For genuine
        market-wide news use :meth:`get_general_news`.

        Args:
            ticker: Stock/index symbol, or a comma-separated list of them.
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
        except (FMPRateLimitException, FMPAuthException):
            # Do NOT degrade these to []. Swallowing them made a quota
            # exhaustion indistinguishable from "this ticker has no news":
            # the news endpoints returned an empty feed and FMP_RATE_LIMITED
            # could never reach the user (invariant #3). The service layer
            # catches these and maps them to a structured error response.
            raise
        except Exception as e:
            logger.warning(
                "Stock news request failed (symbols=%s): %s: %s",
                params.get("symbols", "<market>"), type(e).__name__, e,
            )
            return []

    async def get_general_news(
        self, limit: int = 50, page: int = 0
    ) -> List[Dict[str, Any]]:
        """Broad market / macro news, not tied to any one symbol.

        FMP stable endpoint: ``news/general-latest``. Rows carry ``symbol: null``
        and cover the market-wide narrative (Fed, macro data, sector rotation,
        strategist commentary) rather than single-name coverage.

        This is the correct source for a "Market" feed — ``news/stock`` without
        a ``symbols`` param returns AAPL, not general news.

        Returns the same row shape as :meth:`get_stock_news`
        (symbol / publishedDate / publisher / site / title / image / url / text).
        """
        try:
            data = await self._make_request(
                "news/general-latest", params={"limit": limit, "page": page}
            )
            return data if isinstance(data, list) else []
        except (FMPRateLimitException, FMPAuthException):
            raise  # quota must not masquerade as "no news"
        except Exception as e:
            logger.warning(
                "General news request failed: %s: %s", type(e).__name__, e
            )
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
        except (FMPRateLimitException, FMPAuthException):
            raise  # see get_stock_news — quota must not masquerade as "no news"
        except Exception as e:
            logger.warning(
                "Crypto news request failed (symbols=%s): %s: %s",
                params.get("symbols", "<all>"), type(e).__name__, e,
            )
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

    # Max symbols per `batch-quote` request. FMP accepts long lists but a
    # very long query string risks a 414 / upstream truncation, so we chunk.
    _BATCH_QUOTE_CHUNK = 300
    # Bound on the legacy per-symbol fan-out. Unbounded gather over a 500-name
    # watchlist opened 500 concurrent sockets against a 20-connection pool
    # (`_get_client()` limits), starving every other FMP caller.
    _QUOTE_FANOUT_LIMIT = 10

    async def get_batch_quotes_bulk(
        self, symbols: List[str]
    ) -> List[Dict[str, Any]]:
        """Get quotes for many symbols using the stable ``batch-quote`` endpoint.

        Unlike ``/quote`` (one symbol per call), ``/stable/batch-quote`` DOES
        accept a comma-separated ``symbols`` list and returns one row per
        symbol in a single request — verified live: 4 symbols requested,
        4 returned. Index symbols (e.g. ``^GSPC``) ride along for free.

        Each row carries: symbol, name, price, change, changePercentage, open,
        previousClose, dayHigh, dayLow, yearHigh, yearLow, volume, marketCap,
        priceAvg50, priceAvg200, exchange, timestamp.

        Prefer this over :meth:`get_batch_quotes` for any list longer than a
        couple of symbols — it turns N HTTP calls into ``ceil(N/300)``.

        Returns whatever chunks succeeded; a failed chunk is logged and
        skipped rather than failing the whole batch (callers treat a missing
        symbol as "no quote", which every one of them already handles).
        """
        if not symbols:
            return []

        # De-dup while preserving order — a repeated symbol wastes query budget
        # and yields a duplicate row the caller would have to collapse anyway.
        uniq = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
        if not uniq:
            return []

        chunks = [
            uniq[i : i + self._BATCH_QUOTE_CHUNK]
            for i in range(0, len(uniq), self._BATCH_QUOTE_CHUNK)
        ]

        async def _fetch_chunk(chunk: List[str]) -> List[Dict[str, Any]]:
            try:
                data = await self._make_request(
                    "batch-quote", params={"symbols": ",".join(chunk)}
                )
                return data if isinstance(data, list) else []
            except Exception as e:
                logger.warning(
                    "Batch quote chunk failed (%d symbols, first=%s): %s: %s",
                    len(chunk), chunk[0], type(e).__name__, e,
                )
                return []

        results = await asyncio.gather(
            *[_fetch_chunk(c) for c in chunks], return_exceptions=True
        )

        out: List[Dict[str, Any]] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("Batch quote chunk raised: %s: %s", type(r).__name__, r)
                continue
            out.extend(r)

        if len(out) < len(uniq):
            logger.info(
                "Batch quote returned %d/%d symbols (missing symbols are "
                "delisted, unsupported, or came back empty)",
                len(out), len(uniq),
            )
        return out

    async def get_batch_quotes(
        self, symbols: List[str]
    ) -> List[Dict[str, Any]]:
        """Get quotes for multiple symbols via parallel individual ``/quote`` calls.

        NOTE: ``/stable/batch-quote`` *does* support comma-separated symbols in a
        single request — an earlier version of this docstring claimed otherwise.
        For anything more than a handful of symbols use
        :meth:`get_batch_quotes_bulk` instead; this method is kept for callers
        that depend on its exact per-symbol ``/quote`` field set.

        Concurrency is bounded at ``_QUOTE_FANOUT_LIMIT`` so a large symbol list
        cannot exhaust the shared 20-connection httpx pool.
        """
        if not symbols:
            return []

        sem = asyncio.Semaphore(self._QUOTE_FANOUT_LIMIT)

        async def _fetch_one(sym: str) -> Optional[Dict[str, Any]]:
            async with sem:
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

    async def get_stock_splits(self, ticker: str) -> List[Dict[str, Any]]:
        """Stock split history for a symbol (stable ``/splits``).

        Used to normalize 13F share-count changes across splits: FMP reports
        raw (unadjusted) historical 13F counts, so a split quarter's raw change
        is dominated by the split (e.g. NVDA Q2'24 = +14.2B from the 10:1).
        Rows carry ``date`` + ``numerator`` / ``denominator``. Returns [] on
        failure (caller treats as "no splits").
        """
        try:
            data = await self._make_request(
                "splits", params={"symbol": ticker.upper()}
            )
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"Splits fetch failed for {ticker}: {e}")
            return []

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
    ) -> List[Dict[str, Any]]:
        """Get full performance history for a 13F holder.

        Returns the complete list of quarterly performance records
        (newest first) so callers can compute historical averages.
        """
        try:
            data = await self._make_request(
                "institutional-ownership/holder-performance-summary",
                params={"cik": cik},
            )
            if isinstance(data, list):
                return data
            return [data] if isinstance(data, dict) else []
        except Exception as e:
            logger.warning(f"Performance summary failed for CIK {cik}: {e}")
            return []

    async def get_stock_price_change(
        self, symbol: str
    ) -> Dict[str, Any]:
        """Get total price change percentages (1D, 5D, 1M, ..., max) for a ticker."""
        try:
            data = await self._make_request(
                "stock-price-change", params={"symbol": symbol}
            )
            if isinstance(data, list) and data:
                return data[0]
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"Stock price change failed for {symbol}: {e}")
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
        # Newest quarter whose 13F filing deadline (quarter-end + 45 days) has
        # passed. Taking the merely-most-recently-ENDED quarter returned a
        # partially-filed roster, so the per-holder rows disagreed with the
        # positions-summary aggregate the service pairs them with.
        year, quarter = latest_filed_13f_quarter()

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
        self,
        ticker: str,
        limit: int = 100,
        since_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get insider trading history for a stock ticker (stable API path).

        FMP returns rows newest-first. A single page (``limit`` rows, default
        100) can stop short of a caller's 365-day window for actively-traded
        names, silently truncating "last 12 months" totals. When ``since_date``
        (ISO ``YYYY-MM-DD``) is supplied, page back until the oldest row on a
        page predates it, so the window is fully covered. Without it, the
        original single-page behavior is preserved (used by callers that just
        want the most-recent N, e.g. ``get_insider_roster``).

        The 365-day decision stays in the service layer — this method only
        pages until rows predate the supplied date bound.
        """
        if since_date is None:
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

        # Paginated path: cover the full window back to `since_date`.
        PAGE_SIZE = 100
        MAX_PAGES = 15  # safety cap (~1,500 rows) for hyper-active tickers
        all_trades: List[Dict[str, Any]] = []
        for page in range(MAX_PAGES):
            try:
                rows = await self._make_request(
                    "insider-trading/search",
                    params={
                        "symbol": ticker.upper(),
                        "limit": PAGE_SIZE,
                        "page": page,
                    },
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (403, 404):
                    logger.warning(
                        f"Insider trading search unavailable for {ticker}"
                    )
                else:
                    logger.warning(
                        f"Insider trading page {page} failed for {ticker}: {e}"
                    )
                break  # return whatever pages already succeeded
            except Exception as e:
                logger.warning(
                    f"Insider trading page {page} failed for {ticker}: {e}"
                )
                break

            if not isinstance(rows, list) or not rows:
                break
            all_trades.extend(rows)

            # ISO dates sort lexicographically; the last row is the oldest on
            # the page. Once it predates the cutoff, the window is covered.
            oldest = (
                rows[-1].get("transactionDate")
                or rows[-1].get("filingDate")
                or ""
            )[:10]
            if oldest and oldest < since_date:
                break
            if len(rows) < PAGE_SIZE:
                break  # short page → no more data upstream

        return all_trades

    async def get_beneficial_ownership(
        self, ticker: str
    ) -> List[Dict[str, Any]]:
        """SC 13D/G filings — true beneficial ownership for 5%+ holders.

        Bridges the Form 4 gap for founders and major holders who don't
        trade often: their Form 4 `securitiesOwned` is just one account's
        post-trade balance, while 13G `soleVotingPower` is the full
        beneficial stake (including trusts and other vehicles). For ORCL
        this is the only path that exposes Larry Ellison's 1.157B / 43%
        position.
        """
        try:
            data = await self._make_request(
                "acquisition-of-beneficial-ownership",
                params={"symbol": ticker.upper()},
            )
            return data if isinstance(data, list) else []
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                logger.warning(
                    f"Beneficial-ownership unavailable for {ticker}"
                )
                return []
            raise
        except Exception as e:
            logger.warning(
                f"Beneficial-ownership fetch failed for {ticker}: {e}"
            )
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
                        return _normalize_profile(data[0])
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
