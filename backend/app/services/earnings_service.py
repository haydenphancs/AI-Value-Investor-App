"""
Earnings service — fetches EPS/Revenue estimates & actuals from FMP,
builds the response payload that matches the iOS EarningsData struct.

Data sources:
- Income statement (quarterly): actual EPS (epsDiluted) and Revenue, with fiscal period labels
- Analyst estimates (quarterly): estimated EPS (epsAvg) and Revenue (revenueAvg)
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


def _match_estimate_to_income(
    est_date: str, income_records: List[dict], tolerance_days: int = 15
) -> Optional[dict]:
    """Find the income statement record whose fiscal end date is closest to the estimate date."""
    try:
        est_dt = datetime.strptime(est_date[:10], "%Y-%m-%d")
    except Exception:
        return None

    best = None
    best_diff = tolerance_days + 1
    for rec in income_records:
        try:
            inc_dt = datetime.strptime(rec["date"][:10], "%Y-%m-%d")
            diff = abs((est_dt - inc_dt).days)
            if diff < best_diff:
                best_diff = diff
                best = rec
        except Exception:
            continue
    return best


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

    async def _build_earnings(self, ticker: str) -> EarningsResponse:
        today = date.today()
        four_years_ago = (today - timedelta(days=4 * 365)).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")

        # Fetch all sources in parallel
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

        # Sort income statements chronologically (oldest first)
        income_sorted = sorted(
            [r for r in income_raw if isinstance(r, dict) and r.get("date") and r.get("period", "").startswith("Q")],
            key=lambda r: r["date"],
        )

        # Sort estimates chronologically (oldest first)
        estimates_sorted = sorted(
            [e for e in estimates_raw if isinstance(e, dict) and e.get("date")],
            key=lambda e: e["date"],
        )

        # ── Build merged quarterly data ──
        # Strategy: iterate through estimates, match each to an income statement by date proximity.
        # For quarters with actuals → include actual+estimate+surprise
        # For future quarters → estimate only

        eps_quarters: List[EarningsQuarterSchema] = []
        revenue_quarters: List[EarningsQuarterSchema] = []
        price_history: List[EarningsPricePointSchema] = []
        used_income_dates: set = set()

        for est in estimates_sorted:
            est_date = est.get("date", "")
            est_eps = _safe_float(est, "epsAvg")
            est_rev = _safe_float(est, "revenueAvg")

            if est_eps is None and est_rev is None:
                continue

            # Try to match with income statement
            matched_income = _match_estimate_to_income(est_date, income_sorted)

            if matched_income and matched_income["date"] not in used_income_dates:
                # We have actuals for this quarter
                used_income_dates.add(matched_income["date"])
                period = matched_income.get("period", "Q?")
                fiscal_date = matched_income["date"]
                label = _quarter_label(period, fiscal_date)

                actual_eps = _safe_float(matched_income, "epsDiluted") or _safe_float(matched_income, "eps")
                actual_rev = _safe_float(matched_income, "revenue")

                # EPS
                if est_eps is not None and actual_eps is not None:
                    eps_quarters.append(EarningsQuarterSchema(
                        quarter=label,
                        actual_value=actual_eps,
                        estimate_value=est_eps,
                        surprise_percent=_compute_surprise(actual_eps, est_eps),
                    ))
                elif actual_eps is not None:
                    eps_quarters.append(EarningsQuarterSchema(
                        quarter=label,
                        actual_value=actual_eps,
                        estimate_value=actual_eps,
                        surprise_percent=0.0,
                    ))

                # Revenue
                if est_rev is not None and actual_rev is not None:
                    revenue_quarters.append(EarningsQuarterSchema(
                        quarter=label,
                        actual_value=actual_rev,
                        estimate_value=est_rev,
                        surprise_percent=_compute_surprise(actual_rev, est_rev),
                    ))
                elif actual_rev is not None:
                    revenue_quarters.append(EarningsQuarterSchema(
                        quarter=label,
                        actual_value=actual_rev,
                        estimate_value=actual_rev,
                        surprise_percent=0.0,
                    ))

                # Price — always emit an entry to keep 1:1 alignment with quarters
                close_price = _find_close_price(fiscal_date, price_lookup)
                price_history.append(EarningsPricePointSchema(
                    quarter=label,
                    price=close_price if close_price is not None else 0,
                ))
            else:
                # Future quarter — no actuals
                # Derive label from estimate date using calendar quarter
                try:
                    dt = datetime.strptime(est_date[:10], "%Y-%m-%d")
                    q = (dt.month - 1) // 3 + 1
                    yr = dt.strftime("%y")
                    label = f"Q{q} '{yr}"
                except Exception:
                    label = est_date[:10]

                if est_eps is not None:
                    eps_quarters.append(EarningsQuarterSchema(
                        quarter=label,
                        actual_value=None,
                        estimate_value=est_eps,
                        surprise_percent=None,
                    ))
                if est_rev is not None:
                    revenue_quarters.append(EarningsQuarterSchema(
                        quarter=label,
                        actual_value=None,
                        estimate_value=est_rev,
                        surprise_percent=None,
                    ))

        # Add any income quarters that didn't match estimates (older historical quarters)
        for rec in income_sorted:
            if rec["date"] in used_income_dates:
                continue
            period = rec.get("period", "Q?")
            fiscal_date = rec["date"]
            label = _quarter_label(period, fiscal_date)
            actual_eps = _safe_float(rec, "epsDiluted") or _safe_float(rec, "eps")
            actual_rev = _safe_float(rec, "revenue")

            if actual_eps is not None:
                eps_quarters.append(EarningsQuarterSchema(
                    quarter=label,
                    actual_value=actual_eps,
                    estimate_value=actual_eps,
                    surprise_percent=0.0,
                ))
            if actual_rev is not None:
                revenue_quarters.append(EarningsQuarterSchema(
                    quarter=label,
                    actual_value=actual_rev,
                    estimate_value=actual_rev,
                    surprise_percent=0.0,
                ))
            close_price = _find_close_price(fiscal_date, price_lookup)
            price_history.append(EarningsPricePointSchema(
                quarter=label,
                price=close_price if close_price is not None else 0,
            ))

        # Sort everything by the fiscal date order
        # Re-sort by extracting year+quarter from label
        eps_quarters.sort(key=lambda q: _label_sort_key(q.quarter))
        revenue_quarters.sort(key=lambda q: _label_sort_key(q.quarter))
        price_history.sort(key=lambda p: _label_sort_key(p.quarter))

        # ── Daily Price History (continuous line data) ──
        daily_price_history: List[EarningsDailyPriceSchema] = []
        if used_income_dates and price_list:
            sorted_dates = sorted(d[:10] for d in used_income_dates if d)
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
        next_earnings = self._find_next_earnings_date(estimates_sorted, used_income_dates, today_str)

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

    def _find_next_earnings_date(
        self,
        estimates_sorted: List[dict],
        used_income_dates: set,
        today_str: str,
    ) -> Optional[NextEarningsDateSchema]:
        """Find the next future earnings date from unmatched estimates."""
        for est in estimates_sorted:
            est_date = est.get("date", "")
            if est_date > today_str:
                # Check this estimate wasn't matched to an income record
                matched = any(
                    abs((datetime.strptime(est_date[:10], "%Y-%m-%d") -
                         datetime.strptime(d[:10], "%Y-%m-%d")).days) <= 15
                    for d in used_income_dates
                    if d
                )
                if not matched:
                    return NextEarningsDateSchema(
                        date=est_date[:10],
                        is_confirmed=False,
                        timing="Time Not Specified",
                    )
        return None


def _label_sort_key(label: str) -> str:
    """Convert 'Q1 '24' → '2024-01' for sorting."""
    try:
        parts = label.split(" '")
        q_num = parts[0][1:]
        yr = int(parts[1])
        year = yr + 2000 if yr < 50 else yr + 1900
        return f"{year}-{int(q_num):02d}"
    except Exception:
        return label


# ── Singleton ──────────────────────────────────────────────────────
_earnings_service: Optional[EarningsService] = None


def get_earnings_service() -> EarningsService:
    global _earnings_service
    if _earnings_service is None:
        _earnings_service = EarningsService()
    return _earnings_service
