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
import math
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.utils.period_labels import quarterly_period_label
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
        f = float(val)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


# Quarter display labels come from app.utils.period_labels.quarterly_period_label
# (shared app-wide): the fiscal-year apostrophe form "Q4 '26", monotonic for
# off-calendar-fiscal companies. The Institutions / 13F chart is the only section
# that intentionally counts calendar quarters instead.


def _as_list(payload: Any) -> List[Dict[str, Any]]:
    """Normalize an FMP payload to a list of record dicts (see the sibling
    services): ``_make_request`` is typed ``-> Any`` and a bare error dict
    iterates as string keys → AttributeError → 502."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if payload:
        logger.warning(
            "signal_of_confidence: expected a list from FMP, got %s — degrading to empty",
            type(payload).__name__,
        )
    return []


def _build_market_cap_lookup(records: List[Dict[str, Any]]) -> Dict[str, float]:
    """``{yyyy-MM-dd: marketCap}`` from FMP's historical-market-capitalization."""
    lookup: Dict[str, float] = {}
    for rec in records:
        d = (rec.get("date") or "")[:10]
        mc = _safe_float(rec, "marketCap")
        if d and mc is not None and mc > 0:
            lookup[d] = mc
    return lookup


def _market_cap_on(date_str: str, lookup: Dict[str, float]) -> Optional[float]:
    """Market cap on ``date_str``, scanning back then forward up to 5 days.

    A fiscal period-end often falls on a weekend/holiday, so an exact match is
    not guaranteed. Mirrors ``earnings_service._find_close_price``.
    """
    if not date_str:
        return None
    if date_str in lookup:
        return lookup[date_str]
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return None
    for delta in range(1, 6):
        key = (dt - timedelta(days=delta)).strftime("%Y-%m-%d")
        if key in lookup:
            return lookup[key]
    for delta in range(1, 6):
        key = (dt + timedelta(days=delta)).strftime("%Y-%m-%d")
        if key in lookup:
            return lookup[key]
    return None


def _find_next_earnings_date(ec_records: List[Dict[str, Any]]) -> Optional[str]:
    """Return the first future earnings date as yyyy-MM-dd, or None."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ec in sorted(ec_records, key=lambda r: r.get("date") or ""):
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

        # Phase 1: parallel FMP fetch (6 calls). historical-market-cap covers
        # ~6y so every displayed quarter can be valued at ITS OWN period end.
        today = datetime.now(timezone.utc).date()
        mcap_from = (today - timedelta(days=6 * 365)).strftime("%Y-%m-%d")
        mcap_to = today.strftime("%Y-%m-%d")

        (
            quarterly_cashflow,
            quarterly_income,
            quote_data,
            dividend_history,
            ec_raw,
            hist_mcap_raw,
        ) = await asyncio.gather(
            self.fmp.get_cash_flow_statement(ticker, period="quarter", limit=20),
            self.fmp.get_income_statement(ticker, period="quarter", limit=20),
            self.fmp.get_stock_price_quote(ticker),
            self.fmp.get_dividend_history(ticker, limit=40),
            self.fmp.get_earning_calendar_full(ticker),
            self.fmp.get_historical_market_cap(
                ticker, from_date=mcap_from, to_date=mcap_to, limit=2000
            ),
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
        if isinstance(hist_mcap_raw, Exception):
            logger.warning(
                f"Historical market cap fetch failed for {ticker}: {hist_mcap_raw} "
                f"— per-quarter yields fall back to the current market cap"
            )
            hist_mcap_raw = []

        # Normalize quote_data — FMP returns list for quote endpoint
        if isinstance(quote_data, list):
            quote_data = quote_data[0] if quote_data else {}
        if not isinstance(quote_data, dict):
            quote_data = {}

        # Ensure all are lists
        quarterly_cashflow = _as_list(quarterly_cashflow)
        quarterly_income = _as_list(quarterly_income)
        dividend_history = _as_list(dividend_history)
        ec_raw = _as_list(ec_raw)
        hist_mcap_raw = _as_list(hist_mcap_raw)

        # Phase 2: build per-quarter data points
        current_market_cap = _safe_float(quote_data, "marketCap")
        mcap_by_date = _build_market_cap_lookup(hist_mcap_raw)

        data_points = self._build_data_points(
            quarterly_cashflow,
            quarterly_income,
            current_market_cap,
            mcap_by_date,
            ticker,
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
        mcap_by_date: Optional[Dict[str, float]] = None,
        ticker: str = "",
    ) -> List[SignalOfConfidenceDataPointSchema]:
        """Build per-quarter data points from FMP data.

        Each quarter's yields are computed against the market cap at THAT
        quarter's period end (point-in-time), not today's — scaling a two-year-old
        quarter by the current cap understated the yields of any stock that has
        since re-rated. Falls back to the current cap (with a warning) only when
        the historical series has no value near the period end.
        """
        mcap_by_date = mcap_by_date or {}

        # Build lookup dict by date
        cf_by_date: Dict[str, Dict[str, Any]] = {}
        for rec in cashflow_records:
            date = rec.get("date") or ""
            if date:
                cf_by_date[date] = rec

        # Sort income records ascending by date, take last 8
        sorted_income = sorted(income_records, key=lambda r: r.get("date") or "")
        # Take the most recent 8 quarters
        recent_income = sorted_income[-8:] if len(sorted_income) > 8 else sorted_income

        results = []
        fell_back_to_current = 0
        for rec in recent_income:
            date = rec.get("date") or ""
            if not date:
                continue

            # Fiscal-year labels so off-calendar-FY companies (e.g. Oracle) read
            # monotonically: fiscal Q1 (Aug 2025) -> "Q1 '26", not "Q1 '25".
            label = quarterly_period_label(rec, use_fiscal_year=True)
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

            # Market cap AT THIS QUARTER'S PERIOD END (point-in-time). Using
            # today's cap for a two-year-old quarter mis-states that quarter's
            # yield by the whole re-rating since. Fall back to the current cap
            # only when the historical series doesn't reach this period.
            period_mcap = _market_cap_on(date, mcap_by_date)
            if period_mcap is None:
                period_mcap = current_market_cap
                fell_back_to_current += 1

            # Yields: annualised (x4) from the quarter's cash flow / that
            # quarter's market cap. FMP stable key_metrics may not include
            # dividendYield / buybackYield, so we compute from raw cash flow.
            if period_mcap and period_mcap > 0 and dividends_paid_raw:
                dividend_yield = round(abs(dividends_paid_raw) / period_mcap * 100 * 4, 2)
            else:
                dividend_yield = 0.0

            if period_mcap and period_mcap > 0 and repurchased_raw and repurchased_raw < 0:
                buyback_yield = round(abs(repurchased_raw) / period_mcap * 100 * 4, 2)
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

        if fell_back_to_current:
            logger.warning(
                "signal_of_confidence %s: %d/%d quarters had no historical market "
                "cap within +-5d of the period end — those yields use the CURRENT "
                "cap and are not point-in-time",
                ticker or "?", fell_back_to_current, len(results),
            )

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
            key=lambda d: d.get("date") or "",
            reverse=True,
        )

        # Most recent dividend entry
        latest = sorted_divs[0] if sorted_divs else {}
        ex_date = (latest.get("date") or "")[:10] or None
        payment_date = (latest.get("paymentDate") or latest.get("payment_date") or "")[:10] or None

        # Historical average DIVIDEND yield from the quarterly data points.
        #
        # This value is compared against the T12M DIVIDEND yield below, so it
        # must be dividend-only. It previously summed `dividend_yield +
        # buyback_yield` and was then divided into a dividend-only numerator —
        # any large repurchaser got a systematically depressed ratio and was
        # mislabelled "Low". (Verified: 0.5% dividends + 3.5% buybacks every
        # quarter -> avg 4.0 -> ratio 0.125 -> "Low", for a company yielding
        # exactly its own average.)
        #
        # NOTE the window is the available data points (<= 8 quarters, see
        # _build_data_points), NOT five years — the schema field is named
        # `five_year_avg_yield` for backward compatibility with the shipped iOS
        # DTO, but it is a trailing average over whatever history we hold.
        five_year_avg_yield = 0.0
        if data_points and len(data_points) >= 4:
            dividend_yields = [dp.dividend_yield for dp in data_points]
            five_year_avg_yield = round(
                sum(dividend_yields) / len(dividend_yields), 2
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
