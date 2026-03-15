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
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import FMPClient, get_fmp_client
from app.schemas.earnings import (
    EarningsDailyPriceSchema,
    EarningsQuarterSchema,
    EarningsPricePointSchema,
    EarningsResponse,
    NextEarningsDateSchema,
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


# ── Helpers ────────────────────────────────────────────────────────

def _quarter_label(period: str, fiscal_date: str) -> str:
    """Build 'Q1 '24' label from income statement fields.

    Uses the calendar year from the fiscal end date for display.
    period is 'Q1'..'Q4' directly from FMP income statement.
    """
    try:
        dt = datetime.strptime(fiscal_date[:10], "%Y-%m-%d")
        yr = dt.strftime("%y")
    except Exception:
        yr = "??"
    return f"{period} '{yr}"


def _safe_float(d: dict, key: str) -> Optional[float]:
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


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
        period = rec.get("period", "")
        date_str = rec.get("date", "")
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

    yr = str(year % 100).zfill(2)
    return f"{period} '{yr}"


def _match_earning_cal(
    income_date: str, cal_lookup: Dict[str, dict], tolerance_days: int = 10
) -> Optional[dict]:
    """Find earning_calendar record matching an income statement fiscal date.

    The earnings-calendar uses the report date as 'date' key.
    We match by fiscalDateEnding or by proximity to the income fiscal date.
    """
    key = income_date[:10]
    if key in cal_lookup:
        return cal_lookup[key]
    try:
        dt = datetime.strptime(key, "%Y-%m-%d")
    except Exception:
        return None
    for delta in range(1, tolerance_days + 1):
        for d in [(dt - timedelta(days=delta)), (dt + timedelta(days=delta))]:
            k = d.strftime("%Y-%m-%d")
            if k in cal_lookup:
                return cal_lookup[k]
    return None


# ── Service ────────────────────────────────────────────────────────

class EarningsService:
    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()

    async def get_earnings(self, ticker: str) -> EarningsResponse:
        ticker = ticker.upper()
        cache_key = f"earnings:{ticker}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        result = await self._build_earnings(ticker)
        _cache_set(cache_key, result)
        return result

    async def _fetch_earnings_calendar_for_symbol(
        self, ticker: str, accepted_dates: List[str]
    ) -> List[dict]:
        """Fetch earnings-calendar data for a specific symbol.

        Uses the bulk earnings-calendar endpoint with narrow date-range queries
        around the income statement acceptedDate. Earnings are typically reported
        1-10 days before the SEC filing date.

        Uses two narrow windows per quarter to avoid hitting the 4000-record API cap:
        - Window A: acceptedDate-10 to acceptedDate-4 (early reports)
        - Window B: acceptedDate-4 to acceptedDate+1 (late reports / filing-day)
        """
        if not accepted_dates:
            return []

        async def _fetch_window(fr: str, to: str) -> List[dict]:
            """Fetch earnings-calendar for a date range, filter by symbol."""
            try:
                data = await self.fmp._make_request(
                    "earnings-calendar", params={"from": fr, "to": to}
                )
                if isinstance(data, list):
                    return [r for r in data if r.get("symbol") == ticker]
            except Exception as e:
                logger.warning(f"earnings-calendar query failed for {ticker} ({fr} to {to}): {e}")
            return []

        # Build two windows per accepted date
        tasks = []
        for ad in accepted_dates:
            try:
                dt = datetime.strptime(ad[:10], "%Y-%m-%d")
            except Exception:
                continue
            # Window A: captures reports 5-10 days before filing
            fr_a = (dt - timedelta(days=10)).strftime("%Y-%m-%d")
            to_a = (dt - timedelta(days=4)).strftime("%Y-%m-%d")
            # Window B: captures reports 0-4 days before filing
            fr_b = (dt - timedelta(days=4)).strftime("%Y-%m-%d")
            to_b = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
            tasks.append(_fetch_window(fr_a, to_a))
            tasks.append(_fetch_window(fr_b, to_b))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect and deduplicate
        all_records: List[dict] = []
        for r in results:
            if isinstance(r, list):
                all_records.extend(r)
            elif isinstance(r, Exception):
                logger.warning(f"earnings-calendar query error: {r}")

        seen = set()
        unique: List[dict] = []
        for rec in all_records:
            d = (rec.get("date") or "")[:10]
            if d and d not in seen:
                seen.add(d)
                unique.append(rec)

        logger.info(f"earnings-calendar found {len(unique)} records for {ticker}")
        return unique

    async def _build_earnings(self, ticker: str) -> EarningsResponse:
        today = date.today()
        four_years_ago = (today - timedelta(days=4 * 365)).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")

        # Phase 1: Fetch income statements, analyst estimates, and prices in parallel
        income_raw, estimates_raw, prices_raw = await asyncio.gather(
            self.fmp.get_income_statement(ticker, period="quarter", limit=20),
            self.fmp.get_analyst_estimates(ticker, period="quarter", limit=20),
            self.fmp.get_historical_prices(ticker, from_date=four_years_ago, to_date=today_str),
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

        # Sort income statements chronologically (oldest first)
        income_sorted = sorted(
            [r for r in income_raw if isinstance(r, dict) and r.get("date") and r.get("period", "").startswith("Q")],
            key=lambda r: r["date"],
        )

        # Phase 2: Fetch earnings-calendar data for this symbol using accepted dates
        accepted_dates = []
        for rec in income_sorted:
            ad = rec.get("acceptedDate", rec.get("filingDate", rec["date"]))
            accepted_dates.append(ad[:10] if ad else rec["date"][:10])

        ec_records = await self._fetch_earnings_calendar_for_symbol(ticker, accepted_dates)

        # Build earnings-calendar lookup keyed by report date
        # We'll match these to income statements by date proximity
        ec_by_date: Dict[str, dict] = {}
        for rec in ec_records:
            d = (rec.get("date") or "")[:10]
            if d:
                ec_by_date[d] = rec

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
                    price_lookup[d] = float(c)
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
            period = rec.get("period", "Q?")
            fiscal_date = rec["date"]
            label = _quarter_label(period, fiscal_date)
            fiscal_key = fiscal_date[:10]

            # Try to match with earnings-calendar (adjusted/non-GAAP data)
            # The earnings-calendar record date is the report date, which is
            # near the acceptedDate (within ~10 days before)
            accepted = rec.get("acceptedDate", rec.get("filingDate", fiscal_date))[:10]
            ec_match = _match_earning_cal(accepted, ec_by_date, tolerance_days=10)

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
                    actual_rev = ec_rev_actual or _safe_float(rec, "revenue")
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
                actual_eps = _safe_float(rec, "epsDiluted") or _safe_float(rec, "eps")
                actual_rev = _safe_float(rec, "revenue")

                # Try to find matching analyst estimate
                matched_est = self._find_matching_estimate(fiscal_key, est_by_date)

                if actual_eps is not None:
                    if matched_est and _safe_float(matched_est, "epsAvg") is not None:
                        est_eps = _safe_float(matched_est, "epsAvg")
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

            # Price — always emit an entry to keep 1:1 alignment with quarters
            close_price = _find_close_price(fiscal_date, price_lookup)
            price_history.append(EarningsPricePointSchema(
                quarter=label,
                price=close_price if close_price is not None else 0,
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
                        daily_price_history.append(
                            EarningsDailyPriceSchema(date=d, price=float(c))
                        )
                    except (ValueError, TypeError):
                        pass
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
        """Find the next future earnings date."""
        # First, check earnings-calendar for future dates with timing info
        for ec in sorted(ec_records, key=lambda r: r.get("date", "")):
            ec_date = (ec.get("date") or "")[:10]
            if not ec_date or ec_date <= today_str:
                continue
            # Check this quarter wasn't already reported
            if _safe_float(ec, "epsActual") is not None:
                continue
            return NextEarningsDateSchema(
                date=ec_date,
                is_confirmed=True,
                timing="Time Not Specified",
            )

        # Fallback: use analyst-estimates
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
                        timing="Time Not Specified",
                    )
        return None


# ── Singleton ──────────────────────────────────────────────────────
_earnings_service: Optional[EarningsService] = None


def get_earnings_service() -> EarningsService:
    global _earnings_service
    if _earnings_service is None:
        _earnings_service = EarningsService()
    return _earnings_service
