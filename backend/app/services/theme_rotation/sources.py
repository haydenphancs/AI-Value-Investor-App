"""Licensed data for the monthly theme rotation — loaded strictly, never guessed.

Every FMP read here either returns real data or says clearly that it could not:

* A SYSTEMIC gap raises `ThemeSourceError` and fails the whole run — the service then
  publishes nothing and last month's lists stay. That covers: a short screener universe,
  more than a fifth of profiles / segment reads / price histories failing, or more than half
  of a theme's seed ETFs failing. A rotation computed on a partial view of the market would
  quietly change which stocks belong in a theme.
* A PER-TICKER gap is recorded as UNKNOWN (`None`), never as zero: no segment data, a
  symbol whose price history is not licensed (`history_blocked`), a missing profile. The
  scoring treats unknown neutrally for members and as unproven for newcomers.

`get_etf_holders` / `get_stock_peers` swallow failures to `[]`, so they are not used here
(`FMPClient.get_etf_holdings_strict` exists for this). `quote`/`batch-quote` are not
licensed — prices and caps come from `company-screener` and `profile`.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.integrations.fmp import FMPClient, FMPNotEntitledException
from app.integrations.fmp_entitlements import is_blocked_symbol

logger = logging.getLogger(__name__)

SCREENER_EXCHANGES = ("NASDAQ", "NYSE", "AMEX")
SCREENER_MIN_MARKET_CAP = 300_000_000
SCREENER_PAGE_LIMIT = 10_000              # FMP's hard per-page ceiling
SCREENER_MAX_PAGES = 5
MIN_UNIVERSE_ROWS = 2_000                 # ~3,500+ US names clear $300M; far fewer = broken
MAX_FAILURE_FRACTION = 0.20
MAX_ETF_FAILURE_FRACTION = 0.5
HISTORY_LOOKBACK_DAYS = 200               # covers 126 sessions (6 months) with holidays
SESSIONS_3M, SESSIONS_6M = 63, 126
CONCURRENCY = 8
_SEGMENT_META_KEYS = frozenset({"symbol", "fiscalYear", "calendarYear", "period",
                                "reportedCurrency", "date", "acceptedDate", "filingDate"})


class ThemeSourceError(Exception):
    """A source failed badly enough that the month's rotation must not be published."""

    def __init__(self, source: str, detail: str):
        super().__init__(f"{source}: {detail}")
        self.source = source
        self.detail = detail


@dataclass(frozen=True)
class PriceStats:
    ret_3m: Optional[float]
    ret_6m: Optional[float]
    adtv_6m: Optional[float]
    session_coverage: Optional[float]
    sessions_listed: Optional[int]           # set when the listing is younger than ~6 months


@dataclass
class CallCounter:
    calls: int = 0

    def add(self, n: int = 1) -> None:
        self.calls += n


# ── Universe (screener) ───────────────────────────────────────────────────────────────

async def load_universe(fmp: FMPClient, counter: CallCounter) -> Dict[str, dict]:
    """Every actively-trading US common stock above $300M, keyed by symbol.

    Paged until a short page — FMP silently caps a page at 10,000 rows, so a single call
    can LOOK complete while missing the tail (same sweep shape as
    `price_service._fetch_universe_pages`). An empty FIRST page is impossible in practice
    and is a failure, not "no stocks".
    """
    rows: Dict[str, dict] = {}
    exchanges = ",".join(SCREENER_EXCHANGES)
    for page in range(SCREENER_MAX_PAGES):
        counter.add()
        try:
            batch = await fmp.get_company_screener(
                market_cap_more_than=SCREENER_MIN_MARKET_CAP, exchange=exchanges,
                actively_trading=True, is_fund=False, is_etf=False,
                limit=SCREENER_PAGE_LIMIT, page=page,
            )
        except Exception as e:
            raise ThemeSourceError("company-screener",
                                   f"page {page}: {type(e).__name__}: {e}") from e
        if not batch:
            if page == 0:
                raise ThemeSourceError("company-screener", "the first page returned no rows")
            break
        for row in batch:
            sym = row.get("symbol") if isinstance(row, dict) else None
            if isinstance(sym, str) and sym.strip():
                rows[sym.strip().upper()] = row
        if len(batch) < SCREENER_PAGE_LIMIT:
            break
    else:
        raise ThemeSourceError("company-screener",
                               f"still full after {SCREENER_MAX_PAGES} pages")
    if len(rows) < MIN_UNIVERSE_ROWS:
        raise ThemeSourceError("company-screener", f"only {len(rows)} rows (< {MIN_UNIVERSE_ROWS})")
    return rows


# ── Seed ETFs ─────────────────────────────────────────────────────────────────────────

async def load_etf_holdings(fmp: FMPClient, etfs: Sequence[str],
                            counter: CallCounter) -> Tuple[Dict[str, Dict[str, float]], List[str]]:
    """{etf: {ticker: weight%}} for every ETF that answered, plus the ones that failed.

    An ETF answering with no holdings counts as failed (a thematic ETF is never empty).
    Callers decide per theme whether enough of its seed ETFs answered.
    """
    sem = asyncio.Semaphore(CONCURRENCY)
    out: Dict[str, Dict[str, float]] = {}
    failed: List[str] = []

    async def one(etf: str) -> None:
        async with sem:
            counter.add()
            try:
                rows = await fmp.get_etf_holdings_strict(etf)
            except Exception as e:
                logger.warning("theme rotation: ETF holdings %s failed (%s: %s)",
                               etf, type(e).__name__, e)
                failed.append(etf)
                return
        holdings: Dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = row.get("asset") or row.get("symbol")
            if not isinstance(sym, str) or not sym.strip() or sym.strip().upper() == etf.upper():
                continue
            weight = _finite(row.get("weightPercentage"))
            holdings[sym.strip().upper()] = max(0.0, weight) if weight is not None else 0.0
        if not holdings:
            logger.warning("theme rotation: ETF %s returned no usable holdings", etf)
            failed.append(etf)
            return
        out[etf.upper()] = holdings

    await asyncio.gather(*(one(e) for e in dict.fromkeys(x.upper() for x in etfs)))
    return out, sorted(failed)


# ── Profiles ──────────────────────────────────────────────────────────────────────────

async def load_profiles(fmp: FMPClient, tickers: Iterable[str],
                        counter: CallCounter) -> Dict[str, dict]:
    """{ticker: profile}; a ticker with no profile is simply absent (unknown)."""
    symbols = sorted(set(t.upper() for t in tickers))
    sem = asyncio.Semaphore(CONCURRENCY)
    out: Dict[str, dict] = {}
    failures: List[str] = []

    async def one(sym: str) -> None:
        async with sem:
            counter.add()
            try:
                profile = await fmp.get_company_profile(sym)
            except Exception as e:
                failures.append(f"{sym}:{type(e).__name__}")
                return
        if isinstance(profile, dict) and profile:
            out[sym] = profile

    await asyncio.gather(*(one(s) for s in symbols))
    _check_failures("profile", failures, len(symbols))
    return out


# ── Revenue segments ──────────────────────────────────────────────────────────────────

async def load_segments(fmp: FMPClient, tickers: Iterable[str],
                        counter: CallCounter) -> Dict[str, Optional[Dict[str, float]]]:
    """{ticker: latest annual {segment: revenue}} or None when there is no usable data.

    Not licensed or empty → None (unknown). A transient failure is also None for that
    ticker, but counts toward the systemic-failure threshold.
    """
    symbols = sorted(set(t.upper() for t in tickers))
    sem = asyncio.Semaphore(CONCURRENCY)
    out: Dict[str, Optional[Dict[str, float]]] = {}
    failures: List[str] = []

    async def one(sym: str) -> None:
        async with sem:
            counter.add()
            try:
                raw = await fmp.get_revenue_product_segmentation(sym, period="annual",
                                                                 structure="flat")
            except FMPNotEntitledException:
                out[sym] = None
                return
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (403, 404):
                    out[sym] = None
                    return
                failures.append(f"{sym}:{type(e).__name__}")
                out[sym] = None
                return
        out[sym] = latest_segments(raw)

    await asyncio.gather(*(one(s) for s in symbols))
    _check_failures("revenue-product-segmentation", failures, len(symbols))
    return out


def latest_segments(raw: object) -> Optional[Dict[str, float]]:
    """The most recent fiscal record's {segment: revenue}, metadata keys removed."""
    if not isinstance(raw, list):
        return None
    records = [r for r in raw if isinstance(r, dict)]
    if not records:
        return None
    records.sort(key=lambda r: (str(r.get("date") or ""), _finite(r.get("fiscalYear")) or 0))
    latest = records[-1]
    data = latest.get("data") if isinstance(latest.get("data"), dict) else {
        k: v for k, v in latest.items() if k not in _SEGMENT_META_KEYS}
    cleaned = {str(k): v for k, v in data.items()
               if k not in _SEGMENT_META_KEYS and _finite(v) is not None}
    return {k: float(v) for k, v in cleaned.items()} or None


# ── Price history ─────────────────────────────────────────────────────────────────────

async def load_price_stats(fmp: FMPClient, tickers: Iterable[str], *, as_of: date,
                           counter: CallCounter) -> Dict[str, Optional[PriceStats]]:
    """{ticker: PriceStats} or None when the symbol's history is not licensed/available."""
    symbols = sorted(set(t.upper() for t in tickers))
    sem = asyncio.Semaphore(CONCURRENCY)
    out: Dict[str, Optional[PriceStats]] = {}
    failures: List[str] = []
    start = (as_of - timedelta(days=HISTORY_LOOKBACK_DAYS)).isoformat()

    async def one(sym: str) -> None:
        if is_blocked_symbol(sym):
            out[sym] = None
            return
        async with sem:
            counter.add()
            try:
                raw = await fmp.get_historical_prices(sym, from_date=start, to_date=as_of.isoformat())
            except FMPNotEntitledException:
                out[sym] = None
                return
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (402, 403, 404):
                    out[sym] = None
                    return
                failures.append(f"{sym}:{type(e).__name__}")
                out[sym] = None
                return
        try:
            out[sym] = price_stats(_bars(raw), window_start=date.fromisoformat(start))
        except Exception as e:
            # One odd history must degrade ONE ticker, never escape the gather and fail
            # every theme. Counted, so a systematic parse bug still trips the threshold.
            logger.warning("theme rotation: price stats for %s unusable (%s: %s)",
                           sym, type(e).__name__, e)
            failures.append(f"{sym}:{type(e).__name__}")
            out[sym] = None

    await asyncio.gather(*(one(s) for s in symbols))
    # Blocked symbols were never requested — they must not dilute the failure rate.
    _check_failures("historical-price-eod", failures,
                    sum(1 for s in symbols if not is_blocked_symbol(s)))
    return out


def _bars(raw: object) -> List[dict]:
    rows = raw.get("historical") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    by_date: Dict[str, dict] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = str(r.get("date") or "")[:10]
        close = _finite(r.get("close") if r.get("close") is not None else r.get("adjClose"))
        if close is None or close <= 0 or not _is_iso_date(d):
            continue
        volume = _finite(r.get("volume"))
        # Missing volume is UNKNOWN, not zero: a zero would read as "no liquidity" and trip
        # the member floor.
        by_date[d] = {"date": d, "close": close,
                      "volume": volume if volume is not None and volume >= 0 else None}
    return [by_date[d] for d in sorted(by_date)]


def _is_iso_date(d: str) -> bool:
    """A real YYYY-MM-DD date. '0000-00-00' and '2026-13-45' are ten characters too, and
    one of them sorted first used to crash `date.fromisoformat` for the whole run."""
    if len(d) != 10 or d[4] != "-" or d[7] != "-":
        return False
    try:
        date.fromisoformat(d)
    except ValueError:
        return False
    return True


def price_stats(bars: List[dict], *, window_start: date) -> Optional[PriceStats]:
    """Returns, 6-month average traded value and session coverage from sorted daily bars."""
    if not bars:
        return None
    closes = [b["close"] for b in bars]

    def ret(n: int) -> Optional[float]:
        if len(closes) <= n:
            return None
        base = closes[-1 - n]
        return (closes[-1] / base - 1.0) if base > 0 else None

    recent = bars[-SESSIONS_6M:]
    traded = [b["close"] * b["volume"] for b in recent if b.get("volume") is not None]
    # Averaged over the days whose volume is KNOWN; unknown when most of them are missing
    # (a handful of known days is not a six-month average).
    adtv = mean(traded) if traded and len(traded) * 2 >= len(recent) else None
    first = date.fromisoformat(bars[0]["date"])
    # A listing younger than the look-back window: judge coverage against its own life.
    young = (first - window_start).days > 10
    expected = min(SESSIONS_6M, _approx_sessions(first, date.fromisoformat(bars[-1]["date"]))) \
        if young else SESSIONS_6M
    coverage = min(1.0, len(recent) / expected) if expected > 0 else None
    return PriceStats(
        ret_3m=ret(SESSIONS_3M), ret_6m=ret(SESSIONS_6M),
        adtv_6m=adtv if adtv is not None and math.isfinite(adtv) else None,
        session_coverage=coverage,
        sessions_listed=len(bars) if young else None,
    )


def _approx_sessions(start: date, end: date) -> int:
    """Weekdays between two dates inclusive (holidays ignored — a coverage floor of 90%
    absorbs the ~4 holidays a half-year has)."""
    if end < start:
        return 0
    days = (end - start).days + 1
    full_weeks, extra = divmod(days, 7)
    count = full_weeks * 5
    for i in range(extra):
        if (start + timedelta(days=full_weeks * 7 + i)).weekday() < 5:
            count += 1
    return count


# ── Helpers ───────────────────────────────────────────────────────────────────────────

def _check_failures(source: str, failures: List[str], asked: int) -> None:
    if not failures:
        return
    fraction = len(failures) / max(asked, 1)
    detail = f"{len(failures)}/{asked} failed ({', '.join(failures[:8])}{', ...' if len(failures) > 8 else ''})"
    if fraction > MAX_FAILURE_FRACTION:
        raise ThemeSourceError(source, detail)
    logger.warning("theme rotation: %s — %s (below the %.0f%% abort threshold)",
                   source, detail, MAX_FAILURE_FRACTION * 100)


def _finite(value: object) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def today_utc() -> date:
    return datetime.now(timezone.utc).date()
