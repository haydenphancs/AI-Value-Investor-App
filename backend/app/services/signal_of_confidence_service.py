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
    AnnualDividendSchema,
    DividendInfoSchema,
    SignalOfConfidenceDataPointSchema,
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)
from app.services.corporate_actions_service import (
    corporate_actions_source,
    window_for_range,
)
from app.services.price_service import price_source

logger = logging.getLogger(__name__)

#: Bumped whenever `SignalOfConfidenceResponse` gains a field OR the way a stored value
#: is computed changes. `_check_supabase_cache` refuses any row that does not carry the
#: current value, so a formula fix reaches users on the next read instead of 24h later.
#:
#: 1 → pre-versioning rows (no key at all; they never match, which is the intent).
#: 2 → adds `dividend_info.annual_dividends` / `dividend_per_share*`, and corrects the
#:     `status` denominator to compare T12M against the 5-year average on the SAME
#:     point-in-time basis (JNJ was reported "Low").
#: 3 → the comparison baseline EXCLUDES its own numerator (it was self-referential, so a
#:     40% dividend cut still read "Fair"), `_ABOUT_AVERAGE` sends a payer yielding its own
#:     history to Fair rather than green, and `dividend_growth_pct` drops a partial first
#:     paying year (GOOGL read +38.3% for a 0.20 -> 0.21 quarterly raise).
_PAYLOAD_VERSION = 3

#: Half-open ratio band treated as "about its own average", and therefore Fair rather
#: than the green "High". See `_build_dividend_info` for why it is narrow.
_ABOUT_AVERAGE = (0.97, 1.03)

#: Quarters that make up the "trailing yield" the dividend verdict is about.
_TRAILING_POINTS = 4

#: Older quarters required before that trailing yield can be compared to a baseline at
#: all. Fewer than this and the ratio has too little independent history to mean anything
#: — `_build_dividend_info` falls back to the absolute yield ladder instead.
_MIN_BASELINE_POINTS = 4


#: Completed fiscal years of dividend history to request. Six spans a full cut-and-
#: recover cycle (Intel went 1.4598 -> 0.0000 across four) without bloating the card.
_ANNUAL_DIVIDEND_YEARS = 6

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


# Hard cap on the in-memory tier. Without it this dict grew with the number of DISTINCT
# keys ever requested and was never pruned: `_cache_get` only deletes an entry when that
# SAME key is read again after expiry, so a ticker fetched once and never revisited stayed
# resident for the life of the process. Across ~17 services on a long-lived Railway
# container that is a slow leak whose only resolution is an OOM restart — which drops every
# in-flight report with it. Bounded LRU-ish: evict from the head (least recently WRITTEN).
_CACHE_MAX_ENTRIES = 1024


def _cache_set(key: str, value: Any) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


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
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Signal of confidence cache MISS for {ticker} — fetching from FMP")
            result, next_earnings = await self._build_signal_of_confidence(ticker)

            # NEVER persist a degraded build. A single FMP 429 on the quarterly income
            # call is turned into `[]` by `return_exceptions=True`, `_build_data_points`
            # returns `[]`, and `_build_summary` takes its empty branch — producing a
            # structurally valid response reading total_yield 0.0 / dividend 0.0 /
            # buyback 0.0 / share change 0.0. Written to the 24-hour tier that is a
            # FABRICATED "returns nothing to shareholders" verdict for a day, for every
            # user, and it is frozen into the 20-credit report. The 5-minute in-memory
            # tier still absorbs the retry storm. Mirrors profit_power_service's gate.
            soc_degraded = not getattr(result, "data_points", None)
            if soc_degraded:
                logger.warning(
                    "Signal of confidence NOT persisted for %s (degraded: no data "
                    "points survived the build) — will rebuild after the in-memory TTL",
                    ticker,
                )
            else:
                # Persist to Supabase in background
                asyncio.get_running_loop().run_in_executor(
                    None,
                    self._upsert_supabase_cache_safe,
                    ticker,
                    result,
                    next_earnings,
                )

            _cache_set(cache_key, result)
            if not future.done():
                future.set_result(result)
            return result
        except asyncio.CancelledError:
            # CancelledError is a BaseException, NOT an Exception, so it skips the handler
            # below and used to leave this future unresolved forever — every joiner attached
            # via `await _inflight[...]` then hung for the life of the process. Reachable
            # whenever the LEADER is a cancellable caller: a report run hitting
            # RESEARCH_PIPELINE_TIMEOUT_SECONDS, or any pre-warm task cancelled at shutdown.
            # Hand waiters a normal exception so they fail fast through their own error path.
            if not future.done():
                future.set_exception(RuntimeError("in-flight fetch was cancelled"))
            raise
        except Exception as e:
            if not future.done():
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

            # SCHEMA DRIFT GUARD. `buyback_status` moved onto the summary so a
            # non-dividend payer could carry a buyback verdict at all. A row cached
            # BEFORE that change has no such key, and the Pydantic field's `"Low"`
            # default then silently fills it in — reproducing the exact defect (a
            # confident "Low" for the market's largest repurchasers) for a further 24h,
            # with nothing to distinguish it from a real measurement.
            #
            # A default is the wrong tool for a value that must be COMPUTED, so detect
            # the drift and recompute instead of serving a plausible-looking guess.
            # Self-limiting: it stops mattering once every cached row is rewritten.
            # A VERSION, not a key probe. The original guard tested
            # `"buyback_status" not in summary`, which detects exactly one historical
            # change and nothing since: rows written before `annual_dividends` existed
            # pass it and are served for 24h with the field defaulted to `[]` ("no
            # dividend history" for a dividend king), and — worse — rows written before
            # the `status` denominator was corrected pass it while carrying a value that
            # is simply WRONG (JNJ cached as "Low"). A key probe cannot see a changed
            # formula at all. Bump `_PAYLOAD_VERSION` whenever a field is added OR a
            # stored value's computation changes, and every stale row recomputes on its
            # next read. Self-limiting, same as before.
            version = (json_data or {}).get("payload_version")
            if version != _PAYLOAD_VERSION:
                logger.info(
                    "Supabase cache STALE for %s (payload_version=%r, want %d) — recomputing",
                    ticker, version, _PAYLOAD_VERSION,
                )
                return None
            # Not a response field; strip it so the model never sees it. (Pydantic v2
            # ignores extras by default, but relying on that would break the moment
            # someone sets `extra="forbid"`.)
            json_data = {k: v for k, v in json_data.items() if k != "payload_version"}

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
                    "response_json": {**result.model_dump(),
                                      "payload_version": _PAYLOAD_VERSION},
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
            annual_ratios,
            ec_raw,
            hist_mcap_raw,
            ex_dividend_dates,
        ) = await asyncio.gather(
            self.fmp.get_cash_flow_statement(ticker, period="quarter", limit=20),
            self.fmp.get_income_statement(ticker, period="quarter", limit=20),
            price_source(self).get_quote(ticker),
            # Was `get_dividend_history`, which is outside the FMP licence and answered
            # 402 on every request — a guaranteed failure occupying a slot in this gather.
            # `ratios` (annual) is entitled and carries the dividend AMOUNTS instead.
            self.fmp.get_financial_ratios(
                ticker, period="annual", limit=_ANNUAL_DIVIDEND_YEARS
            ),
            self.fmp.get_earning_calendar_full(ticker),
            self.fmp.get_historical_market_cap(
                ticker, from_date=mcap_from, to_date=mcap_to, limit=2000
            ),
            # Ex-dividend DATES, derived from entitled price series. `/dividends` is 402,
            # so this row read a permanent "N/A" on every dividend payer in the market.
            # Dates come back exact (AAPL 6/6, KO / MSFT / JNJ 6/6 each); AMOUNTS do not
            # and are taken from `annual_ratios` above instead.
            corporate_actions_source(self).get_ex_dividend_dates(
                ticker, *window_for_range(None, mcap_to)
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
        if isinstance(annual_ratios, Exception):
            logger.warning(
                "Annual ratios fetch failed for %s (%s: %s) — the dividend amounts and "
                "growth are omitted; the yield and status still resolve from cash flow",
                ticker, type(annual_ratios).__name__, annual_ratios,
            )
            annual_ratios = []
        if isinstance(ec_raw, Exception):
            logger.warning(f"Earnings calendar fetch failed for {ticker}: {ec_raw}")
            ec_raw = []
        if isinstance(ex_dividend_dates, Exception):
            logger.warning(
                "Ex-dividend date derivation failed for %s (%s: %s) — the date row is "
                "hidden rather than guessed",
                ticker, type(ex_dividend_dates).__name__, ex_dividend_dates,
            )
            ex_dividend_dates = []
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
        annual_ratios = _as_list(annual_ratios)
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
            [],  # per-payment history is unlicensed; amounts come from `annual_ratios`
            summary.dividend_yield,
            summary.buyback_yield,
            summary.share_count_change,
            data_points=data_points,
            annual_ratios=annual_ratios,
            ex_dividend_dates=ex_dividend_dates,
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
            # 0.0 is a SENTINEL here, not a share count — no listed company has zero
            # weighted-average shares. FMP does return `weightedAverageShsOut: 0` on real
            # rows (verified live: CD's newest quarter, 2026-06-30, while every older row
            # is populated), and collapsing that to 0.0 made it indistinguishable from a
            # measured value: the newest point read "0 shares outstanding" and the
            # share-count change came out as a flat -100%, rendered as a spectacular
            # buyback. Keep it as None so the summary can SKIP the point instead.
            shares_raw = _safe_float(rec, "weightedAverageShsOut")
            shares_outstanding = (
                round(shares_raw / 1_000_000, 2)
                if (shares_raw is not None and shares_raw > 0)
                else None
            )

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
                # No data points at all: 0 yield / 0 change classifies as "Low", which
                # is the honest reading of "we measured nothing".
                buyback_status=self._classify_buyback(0.0, 0.0),
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
            # Pick the oldest and newest points that actually REPORT a share count.
            # Anchoring on `data_points[0]`/`[-1]` regardless meant one unreported
            # quarter at either end produced a ±100% change out of nothing.
            measured = [
                dp for dp in data_points
                if dp.shares_outstanding is not None and dp.shares_outstanding > 0
            ]
            oldest_shares = measured[0].shares_outstanding if measured else None
            newest_shares = measured[-1].shares_outstanding if len(measured) > 1 else None
            if oldest_shares and newest_shares and oldest_shares > 0:
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
            buyback_status=self._classify_buyback(t12m_bb_yield, share_count_change),
        )

    # ── Buyback status ────────────────────────────────────────────

    @staticmethod
    def _classify_buyback(
        t12m_buyback_yield: float, share_count_change: float
    ) -> str:
        """Buyback verdict from share-count change + buyback yield.

        Lifted out of `_build_dividend_info` because it never depended on dividends
        in the first place. That function returns None the moment `dividend_history`
        is empty — which is every non-payer, including AMZN, BRK-B and NFLX, three of
        the largest repurchasers on the market — so their buyback verdict was computed
        and then thrown away. The report's fallback then asserted a flat "Low".
        """
        if share_count_change > 2.0:
            return "Diluting"
        if share_count_change > 0:
            return "Diluting (Mild)"
        if t12m_buyback_yield < 1.0:
            return "Low"
        if t12m_buyback_yield < 2.0:
            return "Moderate"
        if t12m_buyback_yield < 4.0:
            return "High"
        return "Very High"

    # ── Dividend info ─────────────────────────────────────────────

    @staticmethod
    def _annual_dividend_map(rows: Any) -> Dict[str, float]:
        """``{fiscal_year: dividendPerShare}`` from `ratios` (period=annual)."""
        by_year: Dict[str, float] = {}
        if not isinstance(rows, list):
            return by_year
        for row in rows:
            if not isinstance(row, dict):
                continue
            year = str(row.get("date") or "")[:4]
            if len(year) != 4 or not year.isdigit():
                continue
            value = _safe_float(row, "dividendPerShare")
            if value is None or value < 0:
                continue
            by_year[year] = value
        return by_year

    @staticmethod
    def _initiation_observed(rows: Any, series: List[AnnualDividendSchema]) -> bool:
        """True when the series' first paying year is one we watched the company START.

        The discriminator for a PARTIAL first year. `dividendPerShare` is a full-calendar
        -year total, so a company that initiates in Q2 or Q4 books a fraction of its
        run-rate in that year — and `_build_annual_dividends` trims the leading zeros, so
        that stub becomes `series[0]` and any growth measured from it is inflated.

        Requires an OBSERVED zero in the immediately preceding year, not a guess from the
        shape of the numbers. A mature payer whose window merely begins mid-stream has no
        such zero and is left alone; inferring "partial" from a large year-two rise would
        discard genuine raises.
        """
        if not series:
            return False
        by_year = SignalOfConfidenceService._annual_dividend_map(rows)
        try:
            prior = str(int(series[0].year) - 1)
        except (TypeError, ValueError):
            return False
        return by_year.get(prior) == 0.0

    @staticmethod
    def _build_annual_dividends(rows: Any) -> List[AnnualDividendSchema]:
        """Dividends per share by completed fiscal year, oldest first.

        Source is `ratios` (period=annual) — entitled, and verified exact against declared
        totals (KO 2024 = 1.9399 against a declared $1.94; 2025 = 2.0402 against $2.04).

        **Leading zeros are trimmed, interior and trailing zeros are kept.** The two look
        identical in the raw feed and mean opposite things: META and GOOGL read
        `0, 0, 0, 0, 2.0016, 2.1119` because they did not pay before 2024, while Intel
        reads `1.4598, 0.7370, 0.3736, 0.0000` because it wound its dividend down and
        suspended it. Rendering META's four $0.00 years would be noise; dropping Intel's
        would delete the most important fact in the series. A company that has never paid
        trims to nothing at all, which is how a non-payer ends up with no series rather
        than a flat line at zero.
        """
        by_year = SignalOfConfidenceService._annual_dividend_map(rows)
        series = [
            AnnualDividendSchema(year=y, per_share=round(by_year[y], 4))
            for y in sorted(by_year)
        ]
        first_paid = next((i for i, p in enumerate(series) if p.per_share > 0), None)
        return [] if first_paid is None else series[first_paid:]

    @staticmethod
    def _dividend_growth(
        series: List[AnnualDividendSchema],
        first_year_partial: bool = False,
    ) -> Tuple[Optional[float], Optional[int]]:
        """Total growth across the series, or ``(None, None)`` when it is undefined.

        Undefined is not zero. The series always starts at the first paying year (see
        above), so a company that began paying inside the window has exactly ONE point and
        no rate — "+infinity%" is not a fact about GOOGL. A company that cut to nothing
        does have one, and it is -100%, which is the number a reader most needs to see.

        ⚠️ `first_year_partial` drops that first year, and it matters more than it looks.
        `dividendPerShare` is a full-CALENDAR-year total, so an initiation part-way
        through the year books a fraction of the run-rate. Measured live: GOOGL 2024 =
        0.60 (three $0.20 payments) against 2025 = 0.83 (0.20 + three 0.21) rendered
        "Dividend Growth +38.3% over 1y" in the gain colour, for a per-quarter dividend
        that went 0.20 -> 0.21. Worse for a Q4 initiation that is never raised again:
        0.25 then 1.00 five years running reads "+300.0% over 5y" for a FLAT dividend.
        Same class as the `0 -> N` case already handled by trimming; this one was missed
        because a stub year is non-zero. Dropping it can leave fewer than two full years,
        and then undefined is the honest answer — exactly as it already is above.
        """
        if first_year_partial:
            series = series[1:]
        if len(series) < 2:
            return None, None
        first, last = series[0].per_share, series[-1].per_share
        if first <= 0:
            return None, None
        years = int(series[-1].year) - int(series[0].year)
        if years <= 0:
            return None, None
        return round((last / first - 1.0) * 100.0, 1), years

    def _build_dividend_info(
        self,
        dividend_history: List[Dict[str, Any]],
        t12m_dividend_yield: float,
        t12m_buyback_yield: float,
        share_count_change: float = 0.0,
        data_points: Optional[List] = None,
        annual_ratios: Optional[List[Dict[str, Any]]] = None,
        ex_dividend_dates: Optional[List[str]] = None,
    ) -> Optional[DividendInfoSchema]:
        """Build DividendInfo for a company that actually pays a dividend.

        ⚠️ THE GATE IS NOT `dividend_history`. It used to be, and that turned the whole
        card off for EVERY ticker on 2026-09-03: FMP's `/dividends` went outside the signed
        Order Form and answers 402, so `dividend_history` is now permanently `[]` — while
        every number this card renders is still perfectly available. `five_year_avg_yield`
        and `status` come from `data_points[].dividend_yield`, which
        `_build_data_points` computes from cash-flow `dividendsPaid` over historical market
        cap, and has never touched `/dividends` at all.

        So the gate is "does this company pay a dividend", answered by the yield. The one
        genuine loss is the per-payment metadata: the ex-dividend and payment dates below
        degrade to None, and the iOS card already renders "N/A" for a nil date rather than
        inventing one.
        """
        annual = self._build_annual_dividends(annual_ratios)
        if annual:
            pays_dividend = True
        elif annual_ratios:
            # We HAVE the authoritative per-share record and it says the company has never
            # paid. Trust it over the cash-flow yield, which is not the same question: it
            # is `dividendsPaid / market cap`, and that line picks up preferred and
            # one-off distributions. Measured — TSLA, which has never paid a common
            # dividend, shows a 0.01% trailing yield from a single quarter and used to
            # render a whole dividend card of em dashes on the strength of it.
            pays_dividend = False
        else:
            # No series at all (the `ratios` fetch failed). Fall back to the yield rather
            # than hiding a real payer's card because one upstream call went down.
            pays_dividend = (
                t12m_dividend_yield > 0
                or any(getattr(dp, "dividend_yield", 0) > 0 for dp in (data_points or []))
            )
        if not dividend_history and not pays_dividend:
            return None
        growth_pct, growth_years = self._dividend_growth(
            annual, self._initiation_observed(annual_ratios, annual)
        )

        # Sort descending by date to find most recent
        sorted_divs = sorted(
            dividend_history,
            key=lambda d: d.get("date") or "",
            reverse=True,
        )

        # Most recent dividend entry
        latest = sorted_divs[0] if sorted_divs else {}
        ex_date = (latest.get("date") or "")[:10] or None
        # Fall back to the DERIVED dates (newest first). Only the date is recoverable this
        # way — the step size implies an amount to ~1%, which is not good enough to print
        # as money, so `payment_date` below stays absent and the amounts come from
        # `annual_ratios`.
        if not ex_date and ex_dividend_dates:
            ex_date = str(ex_dividend_dates[0])[:10] or None
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

        # Dividend yield status: compare the trailing yield to its own history.
        #
        # ⚠️ BOTH SIDES MUST USE THE SAME DENOMINATOR, and they did not. `t12m_dividend_yield`
        # (the summary) divides the last four quarters' dividends by the **current** market
        # cap, while `five_year_avg_yield` averages per-quarter yields each divided by that
        # quarter's **point-in-time** cap. So a stock that merely re-rated upward scored low
        # with no change whatsoever in its payout.
        #
        # Measured across 8 mega-caps, 4 changed verdict once the bases matched:
        #   JNJ  Low -> Fair   (a dividend king reported as "Low")
        #   MSFT Fair -> High     KO Fair -> High     WMT High -> Fair
        #
        # Same class as the bug the `five_year_avg_yield` comment above already fixed once
        # (a div+buyback denominator under a dividend-only numerator) — one layer deeper.
        # The verdict also reaches the 20-credit report via `capital_allocation.dividend_status`.
        #
        # `summary.dividend_yield` itself is untouched: it is a genuine current-cap yield and
        # is rendered as such elsewhere. Only the COMPARISON is put on a consistent footing.
        #
        # ⚠️ AND THE BASELINE MUST EXCLUDE THE NUMERATOR. Putting the trailing window on a
        # point-in-time basis fixed the units but made the ratio SELF-REFERENTIAL, because
        # `five_year_avg_yield` averages ALL points — the same four among them. Measured on
        # the intermediate version:
        #   4 points (all we hold for a recent initiator) -> ratio is IDENTICALLY 1.0,
        #     so a 0.05% token yield published as green "High";
        #   8 flat points -> ratio exactly 1.0, the first value of the "High" bucket, and a
        #     0.3% wiggle flipped the verdict Fair <-> High;
        #   8 points across a 40% dividend CUT -> still "Fair", because ratio = 2B/(A+B)
        #     compresses everything toward 1.0 and puts the ladder's ends out of reach.
        # So the baseline is the OLDER points only. `five_year_avg_yield` keeps its meaning
        # (the published trailing average over everything we hold) — only the comparison
        # denominator changes.
        #
        # Below `_MIN_BASELINE_POINTS` older quarters there is no independent history to
        # compare against, so the ratio is refused outright and the absolute ladder runs.
        # A fabricated verdict from a degenerate ratio is worse than an absolute one.
        points = list(data_points or [])
        recent_points = points[-_TRAILING_POINTS:]
        baseline_points = points[:-_TRAILING_POINTS]
        comparable_t12m = (
            round(sum(dp.dividend_yield for dp in recent_points) / len(recent_points), 2)
            if recent_points
            else t12m_dividend_yield
        )
        baseline_yield = (
            round(sum(dp.dividend_yield for dp in baseline_points) / len(baseline_points), 2)
            if len(baseline_points) >= _MIN_BASELINE_POINTS
            else 0.0
        )

        if baseline_yield > 0:
            ratio = comparable_t12m / baseline_yield
            if _ABOUT_AVERAGE[0] <= ratio < _ABOUT_AVERAGE[1]:
                # "Yielding what it always has" is FAIR, not green.
                #
                # The bare `>= 1.0 -> High` cut split hairs it cannot actually measure:
                # T at 0.993 and VZ at 1.011 are 1.8% apart in trailing yield and were
                # rendered in different colours, one of them as a positive signal. The
                # basis fix above makes that boundary far more crowded than it used to
                # be — matched denominators put a stable payer very close to 1.0 by
                # construction, where the old mismatched ones scattered.
                #
                # Deliberately a NARROW band rather than a re-centred ladder. Measured
                # over 20 real payers, re-centring to 0.85/1.15 moves 10 of them and
                # drops JNJ (0.740) and CSCO (0.727) into "Low" — reintroducing exactly
                # the dividend-king-reads-Low defect this whole section was fixing. The
                # band moves one (VZ), which is the only genuine anomaly in the sample.
                status = "Fair"
            elif ratio < 0.7:
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

        # Same verdict the summary carries — kept on DividendInfo too so the existing
        # iOS DividendInfoCard row is unchanged for dividend payers.
        buyback_status = self._classify_buyback(t12m_buyback_yield, share_count_change)

        return DividendInfoSchema(
            ex_dividend_date=ex_date,
            payment_date=payment_date,
            five_year_avg_yield=five_year_avg_yield,
            status=status,
            buyback_status=buyback_status,
            annual_dividends=annual,
            dividend_per_share=annual[-1].per_share if annual else None,
            dividend_per_share_year=annual[-1].year if annual else None,
            dividend_growth_pct=growth_pct,
            dividend_growth_years=growth_years,
        )


# ── Singleton ─────────────────────────────────────────────────────

_signal_of_confidence_service: Optional[SignalOfConfidenceService] = None


def get_signal_of_confidence_service() -> SignalOfConfidenceService:
    global _signal_of_confidence_service
    if _signal_of_confidence_service is None:
        _signal_of_confidence_service = SignalOfConfidenceService()
    return _signal_of_confidence_service
