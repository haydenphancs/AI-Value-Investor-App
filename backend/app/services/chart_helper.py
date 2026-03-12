"""
Shared chart data fetching — handles intraday, daily, and aggregated intervals
for all asset types (stocks, crypto, ETFs, indices, commodities).
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import FMPClient

# Valid interval values
INTRADAY_INTERVALS = {"1min", "5min", "15min", "30min", "1hour", "4hour"}
DAILY_INTERVALS = {"daily"}
AGGREGATED_INTERVALS = {"weekly", "monthly", "quarterly"}
ALL_INTERVALS = INTRADAY_INTERVALS | DAILY_INTERVALS | AGGREGATED_INTERVALS

# Extra data points to fetch before the requested range for indicator warm-up.
# MA200 needs 200 bars; other indicators (MACD ~34, RSI ~14) need far less.
# 210 gives comfortable headroom for MA200 on the main chart overlay.
_WARMUP_DATA_POINTS = 210


def _warmup_calendar_days(interval: str) -> int:
    """Calendar days of extra history to fetch for indicator warm-up."""
    if interval in INTRADAY_INTERVALS:
        return 7   # a few extra trading days of intraday bars
    if interval in DAILY_INTERVALS:
        return 320  # ~210 trading days for MA200
    if interval == "weekly":
        return 7 * _WARMUP_DATA_POINTS + 14  # ~210 weeks
    if interval in ("monthly", "quarterly"):
        return 30 * _WARMUP_DATA_POINTS + 30  # ~210 months
    return 320

# Default interval for each range when not explicitly specified
DEFAULT_INTERVALS = {
    "1D": "5min",
    "1W": "1hour",
    "3M": "daily",
    "6M": "daily",
    "1Y": "daily",
    "5Y": "weekly",
    "ALL": "monthly",
}

# Allowed intervals per range — invalid combos fall back to the default
ALLOWED_INTERVALS = {
    "1D": {"1min", "5min", "15min", "30min", "1hour"},
    "1W": {"5min", "15min", "30min", "1hour"},
    "3M": {"daily", "weekly"},
    "6M": {"daily", "weekly"},
    "1Y": {"daily", "weekly", "monthly"},
    "5Y": {"weekly", "monthly"},
    "ALL": {"weekly", "monthly"},
}


def resolve_interval(range_code: str, interval: Optional[str]) -> str:
    """Resolve the actual interval to use given range + user selection."""
    allowed = ALLOWED_INTERVALS.get(range_code, ALL_INTERVALS)
    if interval and interval in allowed:
        return interval
    return DEFAULT_INTERVALS.get(range_code, "daily")


def compute_date_range(range_code: str) -> Tuple[Optional[str], str]:
    """Return (from_date, to_date) ISO strings for the given range code."""
    today = datetime.now(tz=timezone.utc).date()
    to_date = today.isoformat()

    if range_code == "ALL":
        # FMP caps results when from_date is omitted; use an explicit old date
        # to get full available history (FMP returns up to 5000 daily data points).
        return "1970-01-01", to_date

    deltas = {
        "1D": timedelta(days=3),   # 3 calendar days to guarantee intraday coverage
        "1W": timedelta(days=10),  # 10 calendar days to cover a full trading week
        "3M": timedelta(days=90),
        "6M": timedelta(days=180),
        "1Y": timedelta(days=365),
        "5Y": timedelta(days=365 * 5),
    }

    delta = deltas.get(range_code, timedelta(days=90))
    from_date = (today - delta).isoformat()
    return from_date, to_date


async def _fetch_all_daily(fmp: FMPClient, symbol: str) -> List[Dict]:
    """
    Fetch full daily history by paginating through FMP's 5000-point limit.

    Makes multiple requests, each fetching up to 5000 data points,
    working backwards from today until no more data is returned.
    """
    all_data: List[Dict] = []
    today = datetime.now(tz=timezone.utc).date()
    to_date = today.isoformat()
    from_date = "1970-01-01"
    max_pages = 5  # Safety: at most 5 requests (up to 25000 data points)

    for _ in range(max_pages):
        raw = await fmp.get_historical_prices(symbol, from_date, to_date)
        page = _parse_historical(raw)
        if not page:
            break

        all_data = page + all_data  # page is sorted oldest-first, prepend

        # If we got fewer than 5000, we have all the data
        if len(page) < 5000:
            break

        # Next request: fetch everything before the earliest date in this page
        earliest = page[0].get("date", "")[:10]
        if not earliest or earliest <= from_date:
            break
        # Set to_date to day before earliest to avoid overlap
        try:
            earliest_dt = datetime.strptime(earliest, "%Y-%m-%d").date()
            to_date = (earliest_dt - timedelta(days=1)).isoformat()
        except ValueError:
            break

    # Deduplicate by date (in case of overlap) and sort
    seen = set()
    deduped = []
    for p in all_data:
        d = p.get("date", "")[:10]
        if d not in seen:
            seen.add(d)
            deduped.append(p)
    deduped.sort(key=lambda p: p.get("date", ""))
    return deduped


async def fetch_chart_data(
    fmp: FMPClient,
    symbol: str,
    range_code: str,
    interval: Optional[str] = None,
    extended_hours: bool = False,
) -> List[Dict[str, Any]]:
    """
    Fetch chart data using the appropriate FMP endpoint.

    Returns list of {date, open, high, low, close, volume} dicts,
    sorted chronologically (oldest first).

    When *extended_hours* is False and the interval is intraday, data
    outside regular market hours (09:30–16:00 ET) is filtered out.
    """
    resolved_interval = resolve_interval(range_code, interval)
    from_date, to_date = compute_date_range(range_code)

    # Extend from_date to include extra data for indicator warm-up
    # (MACD, RSI, etc. need preceding data points to produce values).
    # The frontend trims the warm-up portion from the displayed chart.
    if from_date and from_date != "1970-01-01":
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d").date()
            warmup = _warmup_calendar_days(resolved_interval)
            from_date = (from_dt - timedelta(days=warmup)).isoformat()
        except ValueError:
            pass

    if resolved_interval in INTRADAY_INTERVALS:
        raw = await fmp.get_intraday_prices(
            symbol,
            interval=resolved_interval,
            from_date=from_date,
            to_date=to_date,
        )
        if isinstance(raw, list):
            raw.sort(key=lambda p: p.get("date", ""))
            prices = _normalize_prices(raw)
            if not extended_hours:
                prices = _filter_regular_hours(prices)
            return prices
        return []

    elif resolved_interval in AGGREGATED_INTERVALS:
        if range_code == "ALL":
            historical = await _fetch_all_daily(fmp, symbol)
        else:
            raw = await fmp.get_historical_prices(symbol, from_date, to_date)
            historical = _parse_historical(raw)
        return _aggregate_prices(historical, resolved_interval)

    else:
        # Daily (EOD) data
        if range_code == "ALL":
            historical = await _fetch_all_daily(fmp, symbol)
        else:
            raw = await fmp.get_historical_prices(symbol, from_date, to_date)
            historical = _parse_historical(raw)
        return _normalize_prices(historical)


def _filter_regular_hours(prices: List[Dict]) -> List[Dict]:
    """Keep only data points within regular US market hours (09:30–16:00 ET)."""
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    filtered = []
    for p in prices:
        date_str = p.get("date", "")
        if len(date_str) <= 10:
            # Daily data — always include
            filtered.append(p)
            continue
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=et)
        except ValueError:
            filtered.append(p)
            continue
        t = dt.hour * 60 + dt.minute
        if 9 * 60 + 30 <= t < 16 * 60:
            filtered.append(p)
    return filtered


def _parse_historical(raw) -> List[Dict]:
    """Parse FMP historical response into sorted list (oldest-first)."""
    historical = []
    if isinstance(raw, dict):
        historical = raw.get("historical", [])
    elif isinstance(raw, list):
        historical = raw
    historical.sort(key=lambda p: p.get("date", ""))
    return historical


def _normalize_prices(prices: List[Dict]) -> List[Dict]:
    """Extract standard OHLCV fields from price data."""
    result = []
    for p in prices:
        close = p.get("close") or p.get("adjClose")
        if close and float(close) > 0:
            result.append({
                "date": p.get("date"),
                "open": p.get("open"),
                "high": p.get("high"),
                "low": p.get("low"),
                "close": float(close),
                "volume": p.get("volume"),
            })
    return result


def _aggregate_prices(
    daily_prices: List[Dict], period: str
) -> List[Dict]:
    """Aggregate daily prices into weekly/monthly/quarterly OHLCV bars."""
    if not daily_prices:
        return []

    groups: Dict[str, List[Dict]] = {}
    for p in daily_prices:
        date_str = p.get("date", "")[:10]
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        if period == "weekly":
            key = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
        elif period == "monthly":
            key = dt.strftime("%Y-%m")
        elif period == "quarterly":
            q = (dt.month - 1) // 3 + 1
            key = f"{dt.year}-Q{q}"
        else:
            key = date_str

        groups.setdefault(key, []).append(p)

    result = []
    for key in sorted(groups.keys()):
        candles = groups[key]
        opens = [c.get("open") for c in candles if c.get("open")]
        highs = [c.get("high") for c in candles if c.get("high")]
        lows = [c.get("low") for c in candles if c.get("low")]
        closes = [
            c.get("close") or c.get("adjClose")
            for c in candles
            if c.get("close") or c.get("adjClose")
        ]
        volumes = [c.get("volume") or 0 for c in candles]

        if closes:
            result.append({
                "date": candles[-1].get("date", "")[:10],
                "open": opens[0] if opens else None,
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None,
                "close": float(closes[-1]),
                "volume": sum(v for v in volumes if v),
            })
    return result
