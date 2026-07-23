"""
Earnings service — fetches EPS/Revenue estimates & actuals from FMP,
builds the response payload that matches the iOS EarningsData struct.

Data sources (priority order):
- Earnings calendar (bulk, filtered by symbol): actual EPS/Revenue + consensus estimates (adjusted/non-GAAP)
- Income statement (quarterly): fiscal period labels (Q1-Q4), fiscal dates, revenue fallback
- Analyst estimates (quarterly): future quarter EPS/Revenue estimates
- Historical prices: close price on each fiscal quarter end date
"""

import asyncio
import math
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import FMPClient, get_fmp_client
from app.utils.period_labels import quarterly_period_label
from app.schemas.earnings import (
    EarningsDailyPriceSchema,
    EarningsQuarterSchema,
    EarningsPricePointSchema,
    EarningsResponse,
    NextEarningsDateSchema,
)
from app.services._earnings_common import (
    parse_fmp_timing,
    timing_display,
    UNSPECIFIED,
)

logger = logging.getLogger(__name__)

# ── In-memory cache ────────────────────────────────────────────────
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


# ── In-flight deduplication ────────────────────────────────────────
# A miss downloads 6 years of daily closes; concurrent misses for the same
# ticker must share one fetch.
_inflight: Dict[str, asyncio.Future] = {}


# ── Helpers ────────────────────────────────────────────────────────

def _fiscal_year_for_quarter(cal_year: int, end_month: int, quarter: int) -> int:
    """Fiscal year for a quarter ending in (cal_year, end_month), i.e. the
    calendar year of that fiscal cycle's Q4 (the FY-end). For a December FYE this
    equals the calendar year; for Oracle (May FYE) fiscal Q1 (ends Aug 2025) →
    FY2026. Used to keep FORECAST labels (estimates, which lack fiscalYear)
    fiscal-consistent with the historical labels, so the Earnings Timeline reads
    monotonically across the actual→forecast boundary."""
    months_to_q4 = (4 - quarter) * 3
    return cal_year + ((end_month - 1) + months_to_q4) // 12


def _safe_float(d: dict, key: str) -> Optional[float]:
    v = d.get(key)
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def _first_not_none(*vals: Optional[float]) -> Optional[float]:
    """First value that is not None — unlike ``a or b``, this preserves 0.0.

    ``_safe_float(rec,"epsDiluted") or _safe_float(rec,"eps")`` silently discarded
    a genuine BREAK-EVEN quarter (EPS 0.00 is falsy → falls through → None → the
    quarter's bar vanished from the chart). 0.0 is a real reported value.
    """
    for v in vals:
        if v is not None:
            return v
    return None


def _as_list(payload: Any) -> List[dict]:
    """Normalize an FMP payload to a list of record dicts.

    ``FMPClient._make_request`` is typed ``-> Any`` ("list or dict"); a bare
    error dict returned with a 200 iterates as string KEYS → ``AttributeError``
    → a bare 502 for the Earnings section. Degrade loudly instead.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if payload:
        logger.warning(
            "earnings: expected a list from FMP, got %s — degrading to empty series",
            type(payload).__name__,
        )
    return []


def _compute_surprise(actual: float, estimate: float) -> Optional[float]:
    if estimate == 0:
        return None
    return round(((actual - estimate) / abs(estimate)) * 100, 2)


def _find_close_price(
    date_str: str, price_lookup: Dict[str, float]
) -> Optional[float]:
    """Find close price for a date. If exact date missing, scan ±5 days."""
    if date_str in price_lookup:
        return price_lookup[date_str]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return None
    for delta in range(1, 6):
        key = (dt - timedelta(days=delta)).strftime("%Y-%m-%d")
        if key in price_lookup:
            return price_lookup[key]
    for delta in range(1, 6):
        key = (dt + timedelta(days=delta)).strftime("%Y-%m-%d")
        if key in price_lookup:
            return price_lookup[key]
    return None


def _date_to_sort_key(date_str: str) -> str:
    """Ensure dates sort chronologically."""
    return date_str[:10] if date_str else "0000-00-00"


def _build_fiscal_quarter_map(income_sorted: List[dict]) -> Dict[int, str]:
    """Analyze historical income records to build month → fiscal period mapping.

    Returns e.g. {1: "Q4", 4: "Q1", 7: "Q2", 10: "Q3"} for Salesforce.
    """
    freq: Dict[Tuple[int, str], int] = {}
    for rec in income_sorted:
        # Null-safe: `.get(k, "")` returns None for a present-but-null key, and
        # None.startswith(...) raises AttributeError -> 502 for the section.
        period = rec.get("period") or ""
        date_str = rec.get("date") or ""
        if not period.startswith("Q") or not date_str:
            continue
        try:
            month = datetime.strptime(date_str[:10], "%Y-%m-%d").month
            freq[(month, period)] = freq.get((month, period), 0) + 1
        except Exception:
            continue

    # For each month that appears, pick the most frequent period label
    month_to_period: Dict[int, str] = {}
    for (month, period), count in sorted(freq.items(), key=lambda x: -x[1]):
        if month not in month_to_period:
            month_to_period[month] = period

    return month_to_period


def _infer_fiscal_label(est_date: str, fiscal_month_map: Dict[int, str]) -> str:
    """Map an estimate date to the correct fiscal quarter label using the inferred pattern."""
    try:
        dt = datetime.strptime(est_date[:10], "%Y-%m-%d")
    except Exception:
        return est_date[:10]

    if not fiscal_month_map:
        q = (dt.month - 1) // 3 + 1
        yr = dt.strftime("%y")
        return f"Q{q} '{yr}"

    # Find closest fiscal month
    best_month = None
    best_diff = 999
    for month in fiscal_month_map:
        # Circular distance between months (1-12)
        diff = min(abs(dt.month - month), 12 - abs(dt.month - month))
        if diff < best_diff:
            best_diff = diff
            best_month = month

    if best_month is None:
        q = (dt.month - 1) // 3 + 1
        yr = dt.strftime("%y")
        return f"Q{q} '{yr}"

    period = fiscal_month_map[best_month]

    # Determine the year: the fiscal quarter end is in best_month.
    # If est_date month is close to best_month, use the same year context.
    # Handle year boundary: e.g., est_date is Dec and fiscal end is Jan → next year
    if dt.month > best_month and (dt.month - best_month) > 6:
        # est is in Dec, fiscal end is in Jan → fiscal date is next year
        year = dt.year + 1
    elif dt.month < best_month and (best_month - dt.month) > 6:
        # est is in Jan, fiscal end is in Dec → fiscal date is previous year
        year = dt.year - 1
    else:
        year = dt.year

    # Pair the inferred fiscal quarter with the FISCAL year (not the calendar
    # year of the estimate date) so off-calendar-FY companies stay monotonic and
    # match the historical labels built from FMP's fiscalYear field.
    try:
        q = int(period[1:])
        fy = _fiscal_year_for_quarter(year, best_month, q)
    except (ValueError, IndexError):
        fy = year
    yr = str(fy % 100).zfill(2)
    return f"{period} '{yr}"


def _match_announcement(
    period_end: str, ec_sorted: List[dict], max_days: int = 80
) -> Optional[dict]:
    """Pair an income quarter (by fiscal PERIOD-END) with its earnings announcement:
    the FIRST earnings-calendar record strictly AFTER the period end, within
    ``max_days``.

    An earnings announcement lands ~20-50 days after the quarter-end, and the NEXT
    quarter's announcement is ~130 days out — so "first record after the period end,
    within ~80 days" is unambiguous. Crucially it's robust to WHEN the 10-Q/10-K is
    filed. Matching the filing/accepted date instead broke both ways:
      * a tight window MISSED a fiscal-Q4 whose 10-K lags the release (Oracle FY26 Q4:
        announced 2026-06-10, 10-K accepted 2026-06-22) → the quarter fell to the GAAP
        income-statement EPS compared against a non-GAAP estimate = a bogus "miss";
      * a loose window CROSS-MATCHED a very late 10-K into the NEXT quarter's
        announcement (Disney files its 10-K ~Jan for a Sep FY-end → Q4 grabbed Q1's
        numbers, and both showed the same figure).
    ``ec_sorted`` must be ascending by ``date``.
    """
    try:
        pe = datetime.strptime(period_end[:10], "%Y-%m-%d")
    except Exception:
        return None
    for rec in ec_sorted:
        d = (rec.get("date") or "")[:10]
        try:
            dd = datetime.strptime(d, "%Y-%m-%d")
        except Exception:
            continue
        if dd <= pe:
            continue
        # First announcement after the period end: it's this quarter's iff it lands
        # inside the window (else this quarter simply has no announcement on file).
        return rec if (dd - pe).days <= max_days else None
    return None


# ── Service ────────────────────────────────────────────────────────

class EarningsService:
    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()
        self.supabase = get_supabase()

    async def get_earnings(self, ticker: str) -> EarningsResponse:
        """Two-tier cache-aside with in-flight dedup.

        Tier 1: in-memory (5 min) · Tier 2: Supabase ``earnings_cache`` (24h,
        invalidated early by the next earnings date). This is the heaviest
        payload on the Financials tab — a miss downloads SIX YEARS of daily
        closes — so deduping concurrent misses matters more here than anywhere.
        """
        ticker = ticker.upper()
        cache_key = f"earnings:{ticker}"

        # ── Tier 1: in-memory ──
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        # ── Tier 2: Supabase ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Earnings Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight dedup ──
        if cache_key in _inflight:
            logger.info(f"Earnings in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Earnings cache MISS for {ticker} — fetching from FMP")
            result = await self._build_earnings(ticker)

            # This service already knows the next earnings date — reuse it as
            # the cache-invalidation key instead of an extra lookup.
            next_earnings = (
                result.next_earnings_date.date if result.next_earnings_date else None
            )
            loop.run_in_executor(
                None, self._upsert_supabase_cache_safe, ticker, result, next_earnings
            )

            _cache_set(cache_key, result)
            future.set_result(result)
            return result
        except Exception as e:
            future.set_exception(e)
            raise
        finally:
            # CancelledError is a BaseException, not caught above — cancel the
            # future on a mid-fetch cancellation so joiners don't hang forever.
            if not future.done():
                future.cancel()
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ───────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[EarningsResponse]:
        """Return the cached response if fresh (<24h and before next earnings).
        Synchronous — call via asyncio.to_thread()."""
        try:
            row = (
                self.supabase.table("earnings_cache")
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
            if cached_at.tzinfo is None:  # defensive: column is timestamptz
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Earnings Supabase cache STALE (age={age}) for {ticker}")
                return None

            next_earnings = entry.get("next_earnings_date")
            if next_earnings:
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today_str >= next_earnings:
                    logger.info(
                        f"Earnings Supabase cache STALE (past earnings "
                        f"{next_earnings}) for {ticker}"
                    )
                    return None

            return EarningsResponse(**entry["response_json"])
        except Exception as e:
            logger.warning(f"Earnings Supabase cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: EarningsResponse,
        next_earnings: Optional[str],
    ) -> None:
        """Write-through to the Supabase tier. Best-effort: logged, never fatal."""
        try:
            self.supabase.table("earnings_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Earnings Supabase upsert failed for {ticker}: {e}")

    async def _build_earnings(self, ticker: str) -> EarningsResponse:
        # UTC, matching the cache freshness check in _check_supabase_cache (which
        # compares next_earnings_date against a UTC "today"). Identical on
        # Railway, but keeps the write path and read path on one clock.
        today = datetime.now(timezone.utc).date()
        # 6 years (was 4) so the Earnings Timeline's oldest actual year (annual
        # income reaches back ~5 yrs) gets price coverage. The daily series is
        # still clipped to the oldest income date below, and the TickerDetail
        # earnings chart clips to its displayed quarters, so the extra history
        # is unused there.
        six_years_ago = (today - timedelta(days=6 * 365)).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")

        # Phase 1: Fetch income statements, analyst estimates, and prices in parallel
        income_raw, estimates_raw, prices_raw = await asyncio.gather(
            self.fmp.get_income_statement(ticker, period="quarter", limit=20),
            self.fmp.get_analyst_estimates(ticker, period="quarter", limit=20),
            self.fmp.get_historical_prices(ticker, from_date=six_years_ago, to_date=today_str),
            return_exceptions=True,
        )

        # Handle failures gracefully
        if isinstance(income_raw, Exception):
            logger.error(f"income_statement failed for {ticker}: {income_raw}")
            income_raw = []
        if isinstance(estimates_raw, Exception):
            logger.error(f"analyst_estimates failed for {ticker}: {estimates_raw}")
            estimates_raw = []
        if isinstance(prices_raw, Exception):
            logger.error(f"historical_prices failed for {ticker}: {prices_raw}")
            prices_raw = []

        # Normalize non-list FMP payloads (error dict returned with a 200).
        income_raw = _as_list(income_raw)
        estimates_raw = _as_list(estimates_raw)

        # Sort income statements chronologically (oldest first).
        # `(r.get("period") or "")` — a present-but-null period would otherwise
        # raise AttributeError on .startswith.
        income_sorted = sorted(
            [
                r for r in income_raw
                if r.get("date") and (r.get("period") or "").startswith("Q")
            ],
            key=lambda r: r["date"],
        )

        # Phase 2: Earnings announcements (past + upcoming) for this symbol — ONE call.
        #
        # The per-symbol /stable/earnings?symbol=X endpoint returns ALL of a ticker's
        # announcements, historical AND upcoming, with paired epsActual/epsEstimated and
        # revenueActual/revenueEstimated. It's the authoritative source: no date-window
        # blind spot (a fiscal-Q4 whose 10-K is filed >10 days after the release is still
        # included — Oracle FY26 Q4: announced 06-10, 10-K accepted 06-22) and no
        # 4000-record cap risk, so every reported quarter gets its apples-to-apples
        # non-GAAP actual vs estimate, and the upcoming date feeds next_earnings_date.
        #
        # This REPLACES the old per-quarter global `earnings-calendar` fan-out
        # (_fetch_earnings_calendar_for_symbol = ~2 windows/quarter + 5 forward windows),
        # which downloaded the ENTIRE market's calendar (up to 4000 companies per call)
        # and discarded all but this ticker — that single path was ~99% of the app's FMP
        # bandwidth (4.3 GB/mo, ~10k calls). One per-symbol call (~KB) carries the same
        # data; next_earnings_date still falls back to analyst-estimates if a future
        # announcement isn't listed yet.
        full_ec = await self.fmp.get_earning_calendar_full(ticker)
        ec_records = list(full_ec) if isinstance(full_ec, list) else []

        # Build earnings-calendar lookup keyed by report date
        # We'll match these to income statements by date proximity
        ec_by_date: Dict[str, dict] = {}
        for rec in ec_records:
            d = (rec.get("date") or "")[:10]
            if d:
                ec_by_date[d] = rec
        # Announcements ascending by date → pair each income quarter to the FIRST
        # announcement after its period-end (see _match_announcement).
        ec_sorted = sorted(ec_by_date.values(), key=lambda r: (r.get("date") or "")[:10])

        # Build price lookup: date → close
        # FMP returns either a list or a dict with "historical" key
        if isinstance(prices_raw, dict):
            price_list = prices_raw.get("historical", [])
        elif isinstance(prices_raw, list):
            price_list = prices_raw
        else:
            price_list = []

        price_lookup: Dict[str, float] = {}
        for p in price_list:
            d = p.get("date")
            c = p.get("close")
            if d and c is not None:
                try:
                    fc = float(c)
                    if math.isfinite(fc):  # a NaN/Inf close -> REQUIRED price float -> 500
                        price_lookup[d] = fc
                except (ValueError, TypeError):
                    pass

        logger.info(f"Price lookup has {len(price_lookup)} entries for {ticker}")

        # Sort estimates chronologically (oldest first)
        estimates_sorted = sorted(
            [e for e in estimates_raw if isinstance(e, dict) and e.get("date")],
            key=lambda e: e["date"],
        )

        # Build estimate lookup by date for matching with income statements
        est_by_date: Dict[str, dict] = {}
        for est in estimates_sorted:
            d = est.get("date", "")[:10]
            if d:
                est_by_date[d] = est

        # ── Build merged quarterly data ──
        # Phase A: Historical quarters from income-statement, enriched by earnings-calendar
        # Phase B: Future quarters from analyst-estimates

        eps_quarters: List[EarningsQuarterSchema] = []
        revenue_quarters: List[EarningsQuarterSchema] = []
        price_history: List[EarningsPricePointSchema] = []
        used_fiscal_dates: set = set()

        fiscal_month_map = _build_fiscal_quarter_map(income_sorted)

        # ── Phase A: Historical quarters ──
        for rec in income_sorted:
            fiscal_date = rec["date"]
            # Fiscal-year display label ("Q4 '26") — uses FMP's fiscalYear so
            # off-calendar-FY companies (Oracle FY ends May) read monotonically
            # instead of scrambling fiscal Q1/Q2 to the prior calendar year.
            label = quarterly_period_label(rec, use_fiscal_year=True)
            fiscal_key = fiscal_date[:10]

            # Pair this quarter with its earnings-calendar announcement (adjusted /
            # non-GAAP actual + estimate) by fiscal PERIOD-END — the first
            # announcement after it. Robust to when the 10-Q/10-K is filed; see
            # _match_announcement for the Oracle (lagging Q4 10-K) and Disney (very
            # late 10-K) failure modes that filing-date matching got wrong.
            ec_match = _match_announcement(fiscal_key, ec_sorted)

            if ec_match:
                # earnings-calendar has properly paired adjusted actual/estimate data
                ec_eps_actual = _safe_float(ec_match, "epsActual")
                ec_eps_estimate = _safe_float(ec_match, "epsEstimated")
                ec_rev_actual = _safe_float(ec_match, "revenueActual")
                ec_rev_estimate = _safe_float(ec_match, "revenueEstimated")

                # EPS: use earnings-calendar values (adjusted, non-GAAP)
                if ec_eps_actual is not None and ec_eps_estimate is not None:
                    eps_quarters.append(EarningsQuarterSchema(
                        quarter=label,
                        actual_value=ec_eps_actual,
                        estimate_value=ec_eps_estimate,
                        surprise_percent=_compute_surprise(ec_eps_actual, ec_eps_estimate),
                        fiscal_date=fiscal_key,
                    ))
                elif ec_eps_actual is not None:
                    # Has actual but no estimate — show actual without surprise
                    eps_quarters.append(EarningsQuarterSchema(
                        quarter=label,
                        actual_value=ec_eps_actual,
                        estimate_value=ec_eps_actual,
                        surprise_percent=None,
                        fiscal_date=fiscal_key,
                    ))
                else:
                    # Matched announcement lacks a usable EPS actual (a not-yet-reported
                    # placeholder, or an FMP gap). DON'T silently drop the quarter's EPS
                    # — fall back to the income-statement GAAP epsDiluted (+ analyst
                    # estimate if present), the SAME degrade path as the no-match branch
                    # (revenue already falls back to the income statement below). Without
                    # this, a matched-but-null-actual record consumed the quarter and its
                    # EPS bar vanished.
                    gaap_eps = _first_not_none(_safe_float(rec, "epsDiluted"), _safe_float(rec, "eps"))
                    if gaap_eps is not None:
                        matched_est = self._find_matching_estimate(fiscal_key, est_by_date)
                        est_eps = _safe_float(matched_est, "epsAvg") if matched_est else None
                        if est_eps is not None:
                            logger.warning(
                                "earnings EPS DEGRADED to GAAP epsDiluted vs non-GAAP epsAvg "
                                "for %s %s — matched announcement had null epsActual "
                                "(actual=%s est=%s)",
                                ticker, fiscal_key, gaap_eps, est_eps,
                            )
                        eps_quarters.append(EarningsQuarterSchema(
                            quarter=label,
                            actual_value=gaap_eps,
                            estimate_value=est_eps if est_eps is not None else gaap_eps,
                            surprise_percent=_compute_surprise(gaap_eps, est_eps) if est_eps is not None else None,
                            fiscal_date=fiscal_key,
                        ))

                # Revenue: prefer earnings-calendar, fall back to income-statement
                if ec_rev_actual is not None and ec_rev_estimate is not None:
                    revenue_quarters.append(EarningsQuarterSchema(
                        quarter=label,
                        actual_value=ec_rev_actual,
                        estimate_value=ec_rev_estimate,
                        surprise_percent=_compute_surprise(ec_rev_actual, ec_rev_estimate),
                        fiscal_date=fiscal_key,
                    ))
                else:
                    # Fall back to income-statement revenue
                    actual_rev = _first_not_none(ec_rev_actual, _safe_float(rec, "revenue"))
                    est_rev = ec_rev_estimate
                    if actual_rev is not None:
                        revenue_quarters.append(EarningsQuarterSchema(
                            quarter=label,
                            actual_value=actual_rev,
                            estimate_value=est_rev if est_rev is not None else actual_rev,
                            surprise_percent=_compute_surprise(actual_rev, est_rev) if est_rev is not None else None,
                            fiscal_date=fiscal_key,
                        ))
            else:
                # No earnings-calendar match — fall back to income-statement + analyst-estimates
                actual_eps = _first_not_none(_safe_float(rec, "epsDiluted"), _safe_float(rec, "eps"))
                actual_rev = _safe_float(rec, "revenue")

                # Try to find matching analyst estimate
                matched_est = self._find_matching_estimate(fiscal_key, est_by_date)

                if actual_eps is not None:
                    if matched_est and _safe_float(matched_est, "epsAvg") is not None:
                        est_eps = _safe_float(matched_est, "epsAvg")
                        # DEGRADED path: no announcement in the per-symbol feed for
                        # this quarter, so we compare the income statement's GAAP
                        # epsDiluted against the NON-GAAP analyst epsAvg — the exact
                        # apples-to-oranges comparison the announcement matching
                        # avoids (it can look like a big beat/miss when GAAP and
                        # non-GAAP diverge). Rare (feed gap / stub period), but log it
                        # loudly so it's greppable in prod rather than a silent wrong
                        # surprise. See _match_announcement.
                        logger.warning(
                            "earnings surprise DEGRADED to GAAP epsDiluted vs non-GAAP "
                            "epsAvg for %s %s — no announcement in per-symbol feed "
                            "(actual=%s est=%s)",
                            ticker, fiscal_key, actual_eps, est_eps,
                        )
                        eps_quarters.append(EarningsQuarterSchema(
                            quarter=label,
                            actual_value=actual_eps,
                            estimate_value=est_eps,
                            surprise_percent=_compute_surprise(actual_eps, est_eps),
                            fiscal_date=fiscal_key,
                        ))
                    else:
                        # No estimate available — show actual as pending (no surprise)
                        eps_quarters.append(EarningsQuarterSchema(
                            quarter=label,
                            actual_value=actual_eps,
                            estimate_value=actual_eps,
                            surprise_percent=None,
                            fiscal_date=fiscal_key,
                        ))

                if actual_rev is not None:
                    if matched_est and _safe_float(matched_est, "revenueAvg") is not None:
                        est_rev = _safe_float(matched_est, "revenueAvg")
                        revenue_quarters.append(EarningsQuarterSchema(
                            quarter=label,
                            actual_value=actual_rev,
                            estimate_value=est_rev,
                            surprise_percent=_compute_surprise(actual_rev, est_rev),
                            fiscal_date=fiscal_key,
                        ))
                    else:
                        revenue_quarters.append(EarningsQuarterSchema(
                            quarter=label,
                            actual_value=actual_rev,
                            estimate_value=actual_rev,
                            surprise_percent=None,
                            fiscal_date=fiscal_key,
                        ))

            used_fiscal_dates.add(fiscal_key)

            # Price — OMIT the quarter when no close is within +-5 days rather
            # than emitting a fabricated 0. A 0 is a real price on the wire: it
            # entered the chart's Y domain and dragged the whole price line to
            # the floor. iOS matches price points to quarters by LABEL (not by
            # position), so a missing entry is handled; a fake 0 was not.
            close_price = _find_close_price(fiscal_date, price_lookup)
            if close_price is None:
                logger.warning(
                    "earnings %s: no close price within +-5d of %s — omitting price point",
                    ticker, fiscal_key,
                )
            else:
                price_history.append(EarningsPricePointSchema(
                    quarter=label,
                    price=close_price,
                    fiscal_date=fiscal_key,
                ))

        # ── Phase B: Future quarters from analyst-estimates ──
        for est in estimates_sorted:
            est_date = est.get("date", "")
            est_eps = _safe_float(est, "epsAvg")
            est_rev = _safe_float(est, "revenueAvg")

            if est_eps is None and est_rev is None:
                continue

            # Skip if this estimate matches an already-processed income quarter
            est_key = est_date[:10]
            already_covered = False
            for fd in used_fiscal_dates:
                try:
                    diff = abs((datetime.strptime(est_key, "%Y-%m-%d") -
                                datetime.strptime(fd, "%Y-%m-%d")).days)
                    if diff <= 15:
                        already_covered = True
                        break
                except Exception:
                    continue

            if already_covered:
                continue

            # Future quarter — no actuals
            label = _infer_fiscal_label(est_date, fiscal_month_map)

            if est_eps is not None:
                eps_quarters.append(EarningsQuarterSchema(
                    quarter=label,
                    actual_value=None,
                    estimate_value=est_eps,
                    surprise_percent=None,
                    fiscal_date=est_key,
                ))
            if est_rev is not None:
                revenue_quarters.append(EarningsQuarterSchema(
                    quarter=label,
                    actual_value=None,
                    estimate_value=est_rev,
                    surprise_percent=None,
                    fiscal_date=est_key,
                ))

        # Sort everything by actual fiscal date (correct for all fiscal year types)
        eps_quarters.sort(key=lambda q: q.fiscal_date or "9999-99-99")
        revenue_quarters.sort(key=lambda q: q.fiscal_date or "9999-99-99")
        price_history.sort(key=lambda p: p.fiscal_date or "9999-99-99")

        # ── Daily Price History (continuous line data) ──
        daily_price_history: List[EarningsDailyPriceSchema] = []
        # Use ALL income dates to determine the price range
        all_income_dates = {r["date"][:10] for r in income_sorted if r.get("date")}
        if all_income_dates and price_list:
            sorted_dates = sorted(all_income_dates)
            range_start = sorted_dates[0]
            range_end = today_str
            for p in price_list:
                d = (p.get("date") or "")[:10]
                c = p.get("close")
                if d and c is not None and range_start <= d <= range_end:
                    try:
                        fc = float(c)
                    except (ValueError, TypeError):
                        continue
                    # A NaN/Inf close reaches the REQUIRED `price: float` and
                    # Starlette serializes with allow_nan=False -> ValueError ->
                    # 500 for the whole Earnings section. The price_lookup loop
                    # above already guards this; this loop did not.
                    if not math.isfinite(fc):
                        logger.warning(
                            "earnings %s: non-finite close %r on %s — skipping daily point",
                            ticker, c, d,
                        )
                        continue
                    daily_price_history.append(
                        EarningsDailyPriceSchema(date=d, price=fc)
                    )
            daily_price_history.sort(key=lambda x: x.date)

        # ── Next Earnings Date ──
        next_earnings = self._find_next_earnings_date(
            estimates_sorted, ec_records, used_fiscal_dates, today_str
        )

        logger.info(
            f"Earnings for {ticker}: {len(eps_quarters)} EPS quarters, "
            f"{len(revenue_quarters)} rev quarters, {len(price_history)} price points, "
            f"next={'yes' if next_earnings else 'no'}"
        )

        return EarningsResponse(
            symbol=ticker,
            eps_quarters=eps_quarters,
            revenue_quarters=revenue_quarters,
            price_history=price_history,
            daily_price_history=daily_price_history,
            next_earnings_date=next_earnings,
        )

    def _find_matching_estimate(
        self, fiscal_date: str, est_by_date: Dict[str, dict], tolerance_days: int = 15
    ) -> Optional[dict]:
        """Find analyst-estimate record matching a fiscal date."""
        if fiscal_date in est_by_date:
            return est_by_date[fiscal_date]
        try:
            dt = datetime.strptime(fiscal_date, "%Y-%m-%d")
        except Exception:
            return None
        for delta in range(1, tolerance_days + 1):
            for d in [(dt - timedelta(days=delta)), (dt + timedelta(days=delta))]:
                k = d.strftime("%Y-%m-%d")
                if k in est_by_date:
                    return est_by_date[k]
        return None

    def _find_next_earnings_date(
        self,
        estimates_sorted: List[dict],
        ec_records: List[dict],
        used_fiscal_dates: set,
        today_str: str,
    ) -> Optional[NextEarningsDateSchema]:
        """Find the next future earnings date.

        Prefers FMP's earnings-calendar (confirmed date + timing) over
        analyst-estimates (fiscal period-end). Uses the shared timing
        parser so the returned ``timing`` matches what the alert card
        shows for the same event.
        """
        # First, check earnings-calendar for future dates with timing info
        for ec in sorted(ec_records, key=lambda r: (r.get("date") or "")):
            ec_date = (ec.get("date") or "")[:10]
            if not ec_date or ec_date <= today_str:
                continue
            # Check this quarter wasn't already reported
            if _safe_float(ec, "epsActual") is not None:
                continue
            timing_token = parse_fmp_timing(ec.get("time"))
            return NextEarningsDateSchema(
                date=ec_date,
                is_confirmed=True,
                timing=timing_display(timing_token),
            )

        # Fallback: use analyst-estimates (fiscal-period-end, no timing)
        for est in estimates_sorted:
            est_date = est.get("date", "")
            if est_date > today_str:
                # Check this estimate wasn't matched to an income record
                matched = any(
                    abs((datetime.strptime(est_date[:10], "%Y-%m-%d") -
                         datetime.strptime(d[:10], "%Y-%m-%d")).days) <= 15
                    for d in used_fiscal_dates
                    if d
                )
                if not matched:
                    return NextEarningsDateSchema(
                        date=est_date[:10],
                        is_confirmed=False,
                        timing=timing_display(UNSPECIFIED),
                    )
        return None


# ── Singleton ──────────────────────────────────────────────────────
_earnings_service: Optional[EarningsService] = None


def get_earnings_service() -> EarningsService:
    global _earnings_service
    if _earnings_service is None:
        _earnings_service = EarningsService()
    return _earnings_service
