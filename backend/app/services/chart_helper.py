"""
Shared chart data fetching — handles intraday, daily, and aggregated intervals
for all asset types (stocks, crypto, ETFs, indices, commodities).
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import FMPClient
from app.utils.market_hours import US_MARKET_EARLY_CLOSES


def _finite_or_none(v: Any) -> Optional[float]:
    """Coerce to a finite float, or None.

    Non-finite (NaN/Inf) and non-numeric values become None so they never reach
    a JSON payload as an invalid ``NaN``/``Infinity`` token — which crashes the
    iOS JSONDecoder on the WHOLE response (the chart arrays are typed
    ``List[Dict[str, Any]]`` / ``close: float`` and get no Pydantic guard).
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return f if math.isfinite(f) else None

# A sparkline that covers the WHOLE plotting window — the value every degenerate
# branch of `intraday_span` returns, because it reproduces the pre-existing
# "stretch the series edge to edge" behaviour exactly. A bad span must never make
# the chart worse than it was before spans existed.
FULL_SPAN: Tuple[float, float] = (0.0, 1.0)

# Session windows as ET minutes-from-midnight. Equities/ETFs/indices trade the
# regular 09:30–16:00 bell; crypto and the continuously-quoted commodity futures
# run the whole calendar day, and FMP stamps their bars 00:00–23:55 ET (verified
# live: BTCUSD and GCUSD both start at 00:00 while ^GSPC starts at 09:30).
_REGULAR_OPEN_MINUTE = 9 * 60 + 30    # 09:30
_REGULAR_CLOSE_MINUTE = 16 * 60       # 16:00
_EARLY_CLOSE_MINUTE = 13 * 60         # 13:00 — NYSE/NASDAQ half-days
_ROUND_THE_CLOCK_WINDOW = (0, 24 * 60)


def _bar_minute_of_day(date_str: Any) -> Optional[int]:
    """Minutes-from-midnight for an FMP intraday stamp, or None if it isn't one.

    FMP intraday dates are ``"YYYY-MM-DD HH:MM:SS"`` in ET with no offset. A
    daily bar (``"YYYY-MM-DD"``, length 10) has no time-of-day and returns None,
    which pushes the caller to the full-width fallback rather than inventing a
    midnight position for it.
    """
    if not isinstance(date_str, str) or len(date_str) <= 10:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.hour * 60 + dt.minute


def _session_window(date_str: Any, *, extended_hours: bool) -> Optional[Tuple[int, int]]:
    """(open, close) ET minutes for the session the given bar belongs to."""
    if extended_hours:
        return _ROUND_THE_CLOCK_WINDOW
    # Half-days close at 13:00, so a 12:55 bar is the LAST one of that session and
    # must read as a COMPLETE day, not as 60% of one. Same table `session_phase`
    # uses — one source, or the chart and the "Markets Closed" header disagree
    # about when the bell rang.
    if not isinstance(date_str, str) or len(date_str) < 10:
        return None
    try:
        day = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    close = (
        _EARLY_CLOSE_MINUTE
        if (day.year, day.month, day.day) in US_MARKET_EARLY_CLOSES
        else _REGULAR_CLOSE_MINUTE
    )
    return (_REGULAR_OPEN_MINUTE, close)


def intraday_span(
    day_bars: List[Dict[str, Any]],
    *,
    extended_hours: bool,
    interval_minutes: int = 5,
) -> Tuple[float, float]:
    """Where a single session's bars sit inside that session, as (from, to) in [0, 1].

    A sparkline ships as a bare ``List[float]`` with no time axis, so the client
    used to spread N points across the FULL tile width — which made a 10:15 chart
    pixel-identical to a completed day and read as "the market already closed".
    This pair is the missing axis: iOS draws the series between ``width * from``
    and ``width * to`` and leaves the rest of the tile empty, matching what the
    asset-detail 1D chart has always done via ``TradingDayHelper.timeFractions``.

    ``day_bars`` must already be trimmed to ONE day (both callers do that with
    their ``last_day`` filter) and sorted oldest-first, which is what
    ``fetch_chart_data`` returns.

    ``to`` uses the last bar's END (``+ interval_minutes``) because a bar covers
    its interval — and because without it a session's only bar, stamped at the
    open, would produce ``to == 0`` and a zero-width chart.

    Every degenerate input returns ``FULL_SPAN``: an empty/1-element list, rows
    that aren't dicts, a missing/daily/unparseable date, an unknown session, or
    any ordering that yields ``to <= from``. Full width is the historical
    behaviour, so a span this function cannot compute costs nothing.
    """
    if not isinstance(day_bars, list) or len(day_bars) < 2:
        return FULL_SPAN

    dates = [b.get("date") for b in day_bars if isinstance(b, dict)]
    if len(dates) < 2:
        return FULL_SPAN

    first_minute = _bar_minute_of_day(dates[0])
    last_minute = _bar_minute_of_day(dates[-1])
    if first_minute is None or last_minute is None:
        return FULL_SPAN

    window = _session_window(dates[-1], extended_hours=extended_hours)
    if window is None:
        return FULL_SPAN
    open_minute, close_minute = window
    length = close_minute - open_minute
    if length <= 0:
        return FULL_SPAN

    step = interval_minutes if interval_minutes > 0 else 0
    start = (first_minute - open_minute) / length
    end = (last_minute + step - open_minute) / length

    start = min(max(start, 0.0), 1.0)
    end = min(max(end, 0.0), 1.0)
    if end <= start:
        # Bars entirely outside their own session window (bad upstream data, or a
        # symbol whose real venue this table doesn't model). Nothing honest to say
        # about position — fall back rather than draw a zero-width line.
        return FULL_SPAN

    return (round(start, 4), round(end, 4))


def sparkline_precision(values: List[float]) -> int:
    """Decimal places that preserve a mini-chart series' SHAPE at its magnitude.

    Lives here (not in a service) because BOTH sparkline builders need the same
    answer — ``tracking_service._get_all_sparklines`` for the holdings cards and
    ``home_dashboard_service._intraday_sparkline`` for the Market Pulse tiles —
    and the two already carry duplicated ``_downsample`` copies that have to be
    kept in sync by hand. One helper, one behaviour.

    A flat ``round(c, 2)`` collapses a $0.20 holding's entire session into 1–4
    distinct levels, so the card draws a dead-flat line beside a live "+0.39%".
    Capped at 8dp so a sub-cent coin can't bloat the payload.
    """
    smallest = min((abs(v) for v in values if v), default=0.0)
    if smallest >= 10:
        return 2
    if smallest >= 1:
        return 3
    if smallest >= 0.01:
        return 5
    return 8


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
        earliest = (page[0].get("date") or "")[:10]
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
        d = (p.get("date") or "")[:10]
        if d not in seen:
            seen.add(d)
            deduped.append(p)
    deduped.sort(key=lambda p: p.get("date") or "")
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
            raw.sort(key=lambda p: p.get("date") or "")
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
        date_str = p.get("date") or ""
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
    historical.sort(key=lambda p: p.get("date") or "")
    return historical


def _normalize_prices(prices: List[Dict]) -> List[Dict]:
    """Extract standard OHLCV fields from price data.

    Rows with a missing/blank ``date`` or a non-finite / non-positive ``close``
    are dropped: a ``date=None`` crashes ``_filter_regular_hours`` (``len(None)``)
    and downstream schemas that require ``date: str``, and a non-finite close
    poisons a REQUIRED numeric field with an invalid JSON token.
    """
    result = []
    for p in prices:
        date = p.get("date")
        if not date:
            continue
        close = _finite_or_none(p.get("close") or p.get("adjClose"))
        if close is None or close <= 0:
            continue
        result.append({
            "date": date,
            "open": _finite_or_none(p.get("open")),
            "high": _finite_or_none(p.get("high")),
            "low": _finite_or_none(p.get("low")),
            "close": close,
            "volume": _finite_or_none(p.get("volume")),
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
        date_str = (p.get("date") or "")[:10]
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
        opens = [f for f in (_finite_or_none(c.get("open")) for c in candles) if f is not None]
        highs = [f for f in (_finite_or_none(c.get("high")) for c in candles) if f is not None]
        lows = [f for f in (_finite_or_none(c.get("low")) for c in candles) if f is not None]
        closes = [
            f for f in (_finite_or_none(c.get("close") or c.get("adjClose")) for c in candles)
            if f is not None and f > 0
        ]
        volumes = [f for f in (_finite_or_none(c.get("volume")) for c in candles) if f]

        if closes:
            result.append({
                "date": (candles[-1].get("date") or "")[:10],
                "open": opens[0] if opens else None,
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None,
                "close": closes[-1],
                "volume": sum(volumes),
            })
    return result
