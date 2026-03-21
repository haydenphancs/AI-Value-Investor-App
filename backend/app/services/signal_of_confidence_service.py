"""
Signal of Confidence service — fetches cash flow, income, key metrics, and
dividend history from FMP, computes per-quarter shareholder yield data
(dividends, buybacks, shares outstanding), and returns a response matching
the iOS SignalOfConfidenceSectionData struct.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``signal_of_confidence_cache`` table (24-hour TTL + earnings-aware)

Matches the iOS SignalOfConfidenceSectionData struct.
"""

import asyncio
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.schemas.signal_of_confidence import (
    DividendInfoSchema,
    SignalOfConfidenceDataPointSchema,
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


# ── In-flight deduplication ───────────────────────────────────────
_inflight: Dict[str, asyncio.Future] = {}

# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    """Safely extract a float value from a dict."""
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _extract_year(record: Dict[str, Any]) -> str:
    """Extract calendar year, preferring FMP's calendarYear field."""
    cal_year = record.get("calendarYear")
    if cal_year:
        return str(cal_year)
    date_str = record.get("date", "")
    if len(date_str) >= 4:
        return date_str[:4]
    return ""


def _quarterly_period_label(record: Dict[str, Any]) -> str:
    """Build period label like \"Q2 '24\" (with space before apostrophe)."""
    period = record.get("period", "")  # "Q1", "Q2", etc.
    year = _extract_year(record)
    if len(year) >= 4:
        return f"{period} '{year[-2:]}"
    return f"{period} '{year}"


def _find_next_earnings_date(ec_records: List[Dict[str, Any]]) -> Optional[str]:
    """Return the first future earnings date as yyyy-MM-dd, or None."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ec in sorted(ec_records, key=lambda r: r.get("date", "")):
        ec_date = (ec.get("date") or "")[:10]
        if not ec_date or ec_date <= today_str:
            continue
        if ec.get("eps") is not None:
            continue
        return ec_date
    return None


# ── Service ───────────────────────────────────────────────────────

class SignalOfConfidenceService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_signal_of_confidence(self, ticker: str) -> SignalOfConfidenceResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"signal_of_confidence:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Signal of confidence in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Signal of confidence Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Signal of confidence in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Signal of confidence cache MISS for {ticker} — fetching from FMP")
            result, next_earnings = await self._build_signal_of_confidence(ticker)

            # Persist to Supabase in background
            asyncio.get_running_loop().run_in_executor(
                None,
                self._upsert_supabase_cache_safe,
                ticker,
                result,
                next_earnings,
            )

            _cache_set(cache_key, result)
            future.set_result(result)
            return result
        except Exception as e:
            future.set_exception(e)
            raise
        finally:
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ──────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[SignalOfConfidenceResponse]:
        """Return cached response if fresh (< 24h and before next earnings)."""
        try:
            row = (
                self.supabase.table("signal_of_confidence_cache")
                .select("response_json, cached_at, next_earnings_date")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Supabase cache STALE (age={age}) for {ticker}")
                return None

            next_earnings = entry.get("next_earnings_date")
            if next_earnings:
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today_str >= next_earnings:
                    logger.info(f"Supabase cache STALE (past earnings {next_earnings}) for {ticker}")
                    return None

            json_data = entry["response_json"]
            return SignalOfConfidenceResponse(**json_data)

        except Exception as e:
            logger.warning(f"Supabase cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: SignalOfConfidenceResponse,
        next_earnings: Optional[str],
    ) -> None:
        """Upsert to Supabase cache — safe wrapper that logs and swallows errors."""
        try:
            self.supabase.table("signal_of_confidence_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Supabase signal_of_confidence upsert failed for {ticker}: {e}")

    # ── Builder ───────────────────────────────────────────────────

    async def _build_signal_of_confidence(
        self, ticker: str
    ) -> Tuple[SignalOfConfidenceResponse, Optional[str]]:
        """Fetch FMP data, compute per-quarter shareholder yield, build response."""

        # Phase 1: parallel FMP fetch (5 calls)
        (
            quarterly_cashflow,
            quarterly_income,
            quote_data,
            dividend_history,
            ec_raw,
        ) = await asyncio.gather(
            self.fmp.get_cash_flow_statement(ticker, period="quarter", limit=20),
            self.fmp.get_income_statement(ticker, period="quarter", limit=20),
            self.fmp.get_stock_price_quote(ticker),
            self.fmp.get_dividend_history(ticker, limit=40),
            self.fmp.get_earning_calendar_full(ticker),
            return_exceptions=True,
        )

        # Handle failures gracefully
        if isinstance(quarterly_cashflow, Exception):
            logger.error(f"Quarterly cash flow fetch failed for {ticker}: {quarterly_cashflow}")
            quarterly_cashflow = []
        if isinstance(quarterly_income, Exception):
            logger.error(f"Quarterly income fetch failed for {ticker}: {quarterly_income}")
            quarterly_income = []
        if isinstance(quote_data, Exception):
            logger.warning(f"Quote fetch failed for {ticker}: {quote_data}")
            quote_data = {}
        if isinstance(dividend_history, Exception):
            logger.warning(f"Dividend history fetch failed for {ticker}: {dividend_history}")
            dividend_history = []
        if isinstance(ec_raw, Exception):
            logger.warning(f"Earnings calendar fetch failed for {ticker}: {ec_raw}")
            ec_raw = []

        # Normalize quote_data — FMP returns list for quote endpoint
        if isinstance(quote_data, list):
            quote_data = quote_data[0] if quote_data else {}

        # Ensure all are lists
        quarterly_cashflow = quarterly_cashflow if isinstance(quarterly_cashflow, list) else []
        quarterly_income = quarterly_income if isinstance(quarterly_income, list) else []
        dividend_history = dividend_history if isinstance(dividend_history, list) else []
        ec_raw = ec_raw if isinstance(ec_raw, list) else []

        # Phase 2: build per-quarter data points
        current_market_cap = _safe_float(quote_data, "marketCap")

        data_points = self._build_data_points(
            quarterly_cashflow,
            quarterly_income,
            current_market_cap,
        )

        # Phase 3: build trailing-12-month summary
        summary = self._build_summary(data_points, current_market_cap)

        # Phase 4: build dividend info (optional)
        dividend_info = self._build_dividend_info(
            dividend_history,
            summary.dividend_yield,
            summary.buyback_yield,
            summary.share_count_change,
            data_points=data_points,
        )

        # Phase 5: extract next earnings date for cache invalidation
        next_earnings = _find_next_earnings_date(ec_raw)

        response = SignalOfConfidenceResponse(
            symbol=ticker,
            data_points=data_points,
            summary=summary,
            dividend_info=dividend_info,
        )

        return response, next_earnings

    # ── Per-quarter data points ───────────────────────────────────

    def _build_data_points(
        self,
        cashflow_records: List[Dict[str, Any]],
        income_records: List[Dict[str, Any]],
        current_market_cap: Optional[float],
    ) -> List[SignalOfConfidenceDataPointSchema]:
        """Build per-quarter data points from FMP data."""

        # Build lookup dict by date
        cf_by_date: Dict[str, Dict[str, Any]] = {}
        for rec in cashflow_records:
            date = rec.get("date", "")
            if date:
                cf_by_date[date] = rec

        # Sort income records ascending by date, take last 8
        sorted_income = sorted(income_records, key=lambda r: r.get("date", ""))
        # Take the most recent 8 quarters
        recent_income = sorted_income[-8:] if len(sorted_income) > 8 else sorted_income

        results = []
        for rec in recent_income:
            date = rec.get("date", "")
            if not date:
                continue

            label = _quarterly_period_label(rec)
            if not label or not label.startswith("Q"):
                continue

            # Shares outstanding from income statement (weighted average)
            shares_raw = _safe_float(rec, "weightedAverageShsOut")
            shares_outstanding = round(shares_raw / 1_000_000, 2) if shares_raw else 0.0

            # Cash flow data for this quarter
            cf_rec = cf_by_date.get(date, {})

            # Dividend amount: abs(commonDividendsPaid) in millions
            # FMP stable API uses commonDividendsPaid; fall back to dividendsPaid
            dividends_paid_raw = _safe_float(cf_rec, "commonDividendsPaid")
            if dividends_paid_raw is None:
                dividends_paid_raw = _safe_float(cf_rec, "dividendsPaid")
            if dividends_paid_raw is None:
                dividends_paid_raw = _safe_float(cf_rec, "netDividendsPaid")
            if dividends_paid_raw is not None:
                dividend_amount = round(abs(dividends_paid_raw) / 1_000_000, 2)
            else:
                dividend_amount = 0.0

            # Buyback amount: commonStockRepurchased is negative when buying back
            repurchased_raw = _safe_float(cf_rec, "commonStockRepurchased")
            if repurchased_raw is not None and repurchased_raw < 0:
                # Negative = actual buyback
                buyback_amount = round(abs(repurchased_raw) / 1_000_000, 2)
            else:
                # Positive or zero = stock issuance or none
                buyback_amount = 0.0

            # Yields: compute annualised yields from quarterly cash flow / market cap
            # FMP stable key_metrics may not include dividendYield / buybackYield,
            # so we compute from raw cash flow data for reliability.
            if current_market_cap and current_market_cap > 0 and dividends_paid_raw:
                dividend_yield = round(abs(dividends_paid_raw) / current_market_cap * 100 * 4, 2)
            else:
                dividend_yield = 0.0

            if current_market_cap and current_market_cap > 0 and repurchased_raw and repurchased_raw < 0:
                buyback_yield = round(abs(repurchased_raw) / current_market_cap * 100 * 4, 2)
            else:
                buyback_yield = 0.0

            results.append(SignalOfConfidenceDataPointSchema(
                period=label,
                dividend_yield=dividend_yield,
                buyback_yield=buyback_yield,
                dividend_amount=dividend_amount,
                buyback_amount=buyback_amount,
                shares_outstanding=shares_outstanding,
            ))

        return results

    # ── Trailing-12-month summary ─────────────────────────────────

    def _build_summary(
        self,
        data_points: List[SignalOfConfidenceDataPointSchema],
        current_market_cap: Optional[float],
    ) -> SignalOfConfidenceSummarySchema:
        """Build T12M summary from the most recent 4 quarters."""

        if not data_points:
            return SignalOfConfidenceSummarySchema(
                total_yield=0.0,
                dividend_yield=0.0,
                buyback_yield=0.0,
                share_count_change=0.0,
            )

        # Last 4 quarters (or fewer if not enough data)
        last_4 = data_points[-4:] if len(data_points) >= 4 else data_points

        # T12M dividend yield: sum of dollar amounts / market cap * 100
        # (amounts are already in millions, market cap is in raw dollars)
        total_div_amount = sum(dp.dividend_amount for dp in last_4)
        total_bb_amount = sum(dp.buyback_amount for dp in last_4)

        if current_market_cap and current_market_cap > 0:
            # Convert millions back to raw for division
            t12m_div_yield = round(total_div_amount * 1_000_000 / current_market_cap * 100, 2)
            t12m_bb_yield = round(total_bb_amount * 1_000_000 / current_market_cap * 100, 2)
        else:
            # Fallback: average the per-quarter yields
            t12m_div_yield = round(sum(dp.dividend_yield for dp in last_4) / len(last_4), 2)
            t12m_bb_yield = round(sum(dp.buyback_yield for dp in last_4) / len(last_4), 2)

        total_yield = round(t12m_div_yield + t12m_bb_yield, 2)

        # Share count change: oldest → newest across all data points
        if len(data_points) >= 2:
            oldest_shares = data_points[0].shares_outstanding
            newest_shares = data_points[-1].shares_outstanding
            if oldest_shares and oldest_shares > 0:
                share_count_change = round(
                    (newest_shares - oldest_shares) / oldest_shares * 100, 2
                )
            else:
                share_count_change = 0.0
        else:
            share_count_change = 0.0

        return SignalOfConfidenceSummarySchema(
            total_yield=total_yield,
            dividend_yield=t12m_div_yield,
            buyback_yield=t12m_bb_yield,
            share_count_change=share_count_change,
        )

    # ── Dividend info ─────────────────────────────────────────────

    def _build_dividend_info(
        self,
        dividend_history: List[Dict[str, Any]],
        t12m_dividend_yield: float,
        t12m_buyback_yield: float,
        share_count_change: float = 0.0,
        data_points: Optional[List] = None,
    ) -> Optional[DividendInfoSchema]:
        """Build DividendInfo from dividend history."""

        if not dividend_history:
            return None

        # Sort descending by date to find most recent
        sorted_divs = sorted(
            dividend_history,
            key=lambda d: d.get("date", ""),
            reverse=True,
        )

        # Most recent dividend entry
        latest = sorted_divs[0] if sorted_divs else {}
        ex_date = (latest.get("date") or "")[:10] or None
        payment_date = (latest.get("paymentDate") or latest.get("payment_date") or "")[:10] or None

        # 5-year average total yield (Div + Buyback) from quarterly data points.
        # Each data point has dividend_yield and buyback_yield (annualized %).
        # We average across all available quarters to get a comparable figure
        # to Current Yield which also includes Div + Buyback.
        five_year_avg_yield = 0.0
        if data_points and len(data_points) >= 4:
            # Average the per-quarter total yields (div + buyback)
            total_yields = [
                dp.dividend_yield + dp.buyback_yield for dp in data_points
            ]
            five_year_avg_yield = round(
                sum(total_yields) / len(total_yields), 2
            )
        else:
            # Fallback: use dividend history only
            yearly_yields: dict[str, float] = defaultdict(float)
            for d in sorted_divs:
                y = _safe_float(d, "yield")
                date_str = (d.get("date") or "")[:4]
                if y is not None and y > 0 and date_str:
                    yearly_yields[date_str] += y
            sorted_years = sorted(yearly_yields.keys(), reverse=True)
            annual_values = [yearly_yields[yr] for yr in sorted_years if yearly_yields[yr] > 0]
            annual_values = annual_values[:5]
            five_year_avg_yield = round(
                sum(annual_values) / len(annual_values), 2
            ) if annual_values else 0.0

        # Dividend yield status: compare current T12M yield to 5-year average
        if five_year_avg_yield > 0:
            ratio = t12m_dividend_yield / five_year_avg_yield
            if ratio < 0.7:
                status = "Low"
            elif ratio < 1.0:
                status = "Fair"
            elif ratio < 1.5:
                status = "High"
            else:
                status = "Very High"
        else:
            # No historical average — classify based on absolute yield
            if t12m_dividend_yield < 1.0:
                status = "Low"
            elif t12m_dividend_yield < 2.0:
                status = "Fair"
            elif t12m_dividend_yield < 4.0:
                status = "High"
            else:
                status = "Very High"

        # Buyback status: check for dilution first, then classify by yield
        if share_count_change > 2.0:
            buyback_status = "Diluting"
        elif share_count_change > 0:
            buyback_status = "Diluting (Mild)"
        elif t12m_buyback_yield < 1.0:
            buyback_status = "Low"
        elif t12m_buyback_yield < 2.0:
            buyback_status = "Moderate"
        elif t12m_buyback_yield < 4.0:
            buyback_status = "High"
        else:
            buyback_status = "Very High"

        return DividendInfoSchema(
            ex_dividend_date=ex_date,
            payment_date=payment_date,
            five_year_avg_yield=five_year_avg_yield,
            status=status,
            buyback_status=buyback_status,
        )


# ── Singleton ─────────────────────────────────────────────────────

_signal_of_confidence_service: Optional[SignalOfConfidenceService] = None


def get_signal_of_confidence_service() -> SignalOfConfidenceService:
    global _signal_of_confidence_service
    if _signal_of_confidence_service is None:
        _signal_of_confidence_service = SignalOfConfidenceService()
    return _signal_of_confidence_service
