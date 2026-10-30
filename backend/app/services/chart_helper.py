"""
Shared chart data fetching — handles intraday, daily, and aggregated intervals
for all asset types (stocks, crypto, ETFs, indices, commodities).
"""

import logging
import math
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import settings
from app.integrations.fmp import FMPClient
from app.services.asset_class import uses_coingecko_price

logger = logging.getLogger(__name__)
from app.utils.market_hours import (
    ET,
    US_MARKET_EARLY_CLOSES,
    WIRE_STATUS_AFTER_HOURS,
    WIRE_STATUS_PRE_MARKET,
    is_trading_day,
    last_regular_session,
    previous_trading_day,
)


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


# ── Settled daily bars (shared by the stock / ETF / index / commodity screens) ──
#
# FMP `historical-price-eod/full` includes the CURRENT session's partial bar (F16-2; the
# live evidence is in `index_service`'s block comment). Every service that derives from or
# persists a daily history must drop it first. Each service computes its OWN cutoff from its
# module binding of `current_close_cycle_start` (so tests can freeze each clock), and hands
# it here; the filter itself exists once.


def settled_bars(historical: Any, cutoff: str) -> List[Dict]:
    """The daily rows whose session has settled: rows dated after ``cutoff`` (the ISO date
    of the current close cycle, i.e. the last settled session) are the in-progress bar or a
    stray future-dated row, and are dropped.

    Total for any input: ``None`` / a non-sequence gives ``[]``, non-dict rows are
    dropped, a row without a date is kept (it cannot be placed in time, and every consumer
    already tolerates it). A timestamped date compares on its first ten characters.
    """
    if not isinstance(historical, (list, tuple)):
        return []
    return [
        r for r in historical
        if isinstance(r, dict) and str(r.get("date") or "")[:10] <= cutoff
    ]


# ── Year-to-date (shared by all five asset-detail screens) ─────────────────────
#
# ONE definition, the industry one, and the one `theme_insights_service` already uses
# (`_period_start_index`, pinned by `test_ytd_on_the_first_session_of_the_year_is_the_one_day_move`):
# YTD runs from the LAST CLOSE OF THE PREVIOUS YEAR to the latest close. Five copies used
# to take the FIRST close OF the year instead, which (a) read 0.00% on the first session
# of the year and dropped that session's move from YTD for the rest of the year, and (b)
# keyed the year on UTC, blanking YTD from 19:00 ET on Dec 31 until the first new-year row.

#: How far before Jan 1 the previous year's last close may sit. The last session of a
#: year is at worst a few days before Dec 31 (a weekend plus a holiday); a series whose
#: newest pre-January row is older than this does not describe the year boundary, and a
#: YTD anchored on it would be a longer return under a "YTD" label.
YTD_BASELINE_MAX_GAP_DAYS = 7


def _row_day(row: Any) -> Optional[date_type]:
    """The calendar date of a history row (``YYYY-MM-DD`` or a timestamp), else None."""
    if not isinstance(row, dict):
        return None
    raw = row.get("date")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date_type.fromisoformat(raw[:10])
    except ValueError:
        return None


def ytd_return(prices: Optional[Iterable[Any]], now: Optional[datetime] = None) -> Optional[float]:
    """Year-to-date % return, or None when it cannot be stated honestly.

    * The year is the CURRENT ET year (``now`` in America/New_York), not UTC.
    * Baseline: the last row dated BEFORE Jan 1 of that year — the previous year's last
      close — and it must sit within ``YTD_BASELINE_MAX_GAP_DAYS`` of Dec 31, else None
      (a series that starts this year, or one with a hole over the year end).
    * End: the latest-dated row on or before today (ET). Rows are scanned, not indexed, so
      an unsorted series gives the same answer; a future-dated row is ignored.
    * A series with no row in the new year is an honest 0.0 only in the first
      ``YTD_BASELINE_MAX_GAP_DAYS`` days of January (no session has settled yet); after
      that it is a STALE series, and None rather than a fabricated flat year.
    * A non-finite, zero or negative close at either end gives None — a NaN here would
      serialize as an invalid JSON token and break the iOS decode of the whole screen.
    """
    if not prices:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(ET).date()
    year_start = date_type(today.year, 1, 1)

    base_row = base_day = end_row = end_day = None
    for row in prices:
        d = _row_day(row)
        if d is None or d > today:
            continue
        if d < year_start and (base_day is None or d >= base_day):
            base_row, base_day = row, d
        if end_day is None or d >= end_day:
            end_row, end_day = row, d

    if base_row is None or base_day is None or end_row is None or end_day is None:
        return None
    if (year_start - base_day).days > YTD_BASELINE_MAX_GAP_DAYS + 1:
        return None
    if end_day < year_start and (today - year_start).days >= YTD_BASELINE_MAX_GAP_DAYS:
        return None

    start = _finite_or_none(base_row.get("close") or base_row.get("adjClose"))
    end = _finite_or_none(end_row.get("close") or end_row.get("adjClose"))
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return ((end - start) / start) * 100

# A sparkline that covers the WHOLE plotting window — the value every degenerate
# branch of `intraday_span` returns, because it reproduces the pre-existing
# "stretch the series edge to edge" behaviour exactly. A bad span must never make
# the chart worse than it was before spans existed.
FULL_SPAN: Tuple[float, float] = (0.0, 1.0)

# Session windows as ET minutes-from-midnight. Equities/ETFs/indices trade the
# regular 09:30–16:00 bell; crypto and the continuously-quoted commodity futures
# run the whole calendar day, and FMP stamps their bars 00:00–23:55 ET (verified
# live: BTCUSD and GCUSD both start at 00:00 while ^GSPC starts at 09:30).
#
# `extended_hours=True` here means ROUND THE CLOCK (crypto). The equity
# pre/after-hours session (04:00–20:00 ET) is a user preference on the detail
# chart only — iOS `TradingDayHelper.SessionWindow.extended` — and is never
# expressed with this bool: card sparklines always sit on the bell, so card and
# chart agree with the toggle OFF and the chart is deliberately wider with it ON.
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

# The same window expressed in calendar days, for the services that slice a daily history
# by DATE rather than by bar count. ~210 trading days is ~320 calendar days once weekends
# and holidays are counted. Keep the two in step: they describe one window.
_WARMUP_CALENDAR_DAYS = 320


#: Minutes per bar of each intraday interval FMP serves.
_INTRADAY_INTERVAL_MINUTES = {
    "1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60, "4hour": 240,
}
# Session lengths in minutes: the regular bell, and FMP's `extended=true` 04:00-20:00. A
# half-day closes the bell at 13:00 and its after-hours at 17:00.
_REGULAR_SESSION_MINUTES = _REGULAR_CLOSE_MINUTE - _REGULAR_OPEN_MINUTE      # 390
_EARLY_REGULAR_SESSION_MINUTES = _EARLY_CLOSE_MINUTE - _REGULAR_OPEN_MINUTE  # 210
_EXTENDED_SESSION_MINUTES = 16 * 60                                           # 960
_EARLY_EXTENDED_SESSION_MINUTES = 13 * 60                                     # 780
#: The calendar floor every intraday warm-up keeps, whatever its bar count. It is what the
#: 5-minute chart (and the sparkline split off it) has always fetched, so 5min/1min
#: windows do not move.
_INTRADAY_WARMUP_FLOOR_DAYS = 7
#: Headroom over `_WARMUP_DATA_POINTS` for halts, missing prints and closures the
#: calendar does not know: counting sessions only ever OVER-estimates the bars FMP has.
_INTRADAY_WARMUP_HEADROOM = 1.1


def _session_bars(day: date_type, interval: str, extended_hours: bool) -> int:
    """How many ``interval`` bars one session on ``day`` yields (a 6.5 h bell is 7
    hourly bars: 09:30 … 15:30)."""
    minutes = _INTRADAY_INTERVAL_MINUTES.get(interval) or 5
    early = (day.year, day.month, day.day) in US_MARKET_EARLY_CLOSES
    if extended_hours:
        session = _EARLY_EXTENDED_SESSION_MINUTES if early else _EXTENDED_SESSION_MINUTES
    else:
        session = _EARLY_REGULAR_SESSION_MINUTES if early else _REGULAR_SESSION_MINUTES
    return max(1, math.ceil(session / minutes))


def _intraday_warmup_from(
    range_start: date_type, interval: str, extended_hours: bool
) -> date_type:
    """First calendar day to fetch so ≥ `_WARMUP_DATA_POINTS` bars precede ``range_start``.

    A flat 7 days used to be the warm-up for EVERY bar size: ~7 sessions of 1-hour bars
    is ~49 bars, so MA(200) could never draw on 15m/30m/1h and MA(50) barely on 1h.
    This walks back whole trading sessions (half-days counted short) until they hold the
    warm-up plus headroom, and never fetches less than the 7-day floor — which is all a
    5-minute chart needs, so its window is unchanged.
    """
    floor = range_start - timedelta(days=_INTRADAY_WARMUP_FLOOR_DAYS)
    need = math.ceil(_WARMUP_DATA_POINTS * _INTRADAY_WARMUP_HEADROOM)
    have = 0
    day = range_start
    # Bounded: 1-hour regular bars need ~33 sessions; 400 is far past any real walk.
    for _ in range(400):
        day = previous_trading_day(day)
        have += _session_bars(day, interval, extended_hours)
        if have >= need:
            break
    return min(floor, day)


def _warmup_calendar_days(interval: str) -> int:
    """Calendar days of extra history to fetch for indicator warm-up.

    Intraday answers the 7-day FLOOR only; the bar-count warm-up `fetch_chart_data`
    actually applies is `_intraday_warmup_from`.
    """
    if interval in INTRADAY_INTERVALS:
        return _INTRADAY_WARMUP_FLOOR_DAYS
    if interval in DAILY_INTERVALS:
        return _WARMUP_CALENDAR_DAYS
    if interval == "weekly":
        return 7 * _WARMUP_DATA_POINTS + 14  # ~210 weeks
    if interval in ("monthly", "quarterly"):
        return 30 * _WARMUP_DATA_POINTS + 30  # ~210 months
    return _WARMUP_CALENDAR_DAYS


def daily_range_days(range_code: str) -> int:
    """Calendar days of history one daily-interval chart range needs: the visible window
    PLUS the MA(200) warm-up.

    `TickerChartView.warmupCount` finds the first bar at or after the visible start and
    draws from there, feeding everything earlier to the indicator pass. So when the backend
    sends no leading bars, `warmupCount` is 0 and MA(200) is blank across the whole chart.

    ONE definition for all the detail services. It used to be four separate copies of a
    literal map and only index_service's copy carried the warm-up, so the ETF, crypto and
    commodity daily charts shipped with no MA(200) at all.

    1D/1W get a small weekend/holiday pad rather than the full 320: they resolve to an
    intraday interval on every service, so this branch is only reached when a caller has
    explicitly asked for daily bars over a very short window.
    """
    return {
        "1D": 2 + 7,
        "1W": 7 + 14,
        "3M": 90 + _WARMUP_CALENDAR_DAYS,
        "6M": 180 + _WARMUP_CALENDAR_DAYS,
        "1Y": 365 + _WARMUP_CALENDAR_DAYS,
        # 2Y carries NO MA(200) warm-up, and that is deliberate rather than an omission.
        # It exists for crypto, whose source (CoinGecko Basic) caps history at exactly
        # 730 days — so 730 + 320 is unreachable and asking for it returns 730 anyway.
        # Spending the window on warm-up instead would shorten the VISIBLE range to ~14
        # months under a "2Y" label. MA(50) still draws; MA(200) does not, on this range
        # only. See CRYPTO_HISTORY_YEARS.
        "2Y": 365 * 2,
        "5Y": 365 * 5 + _WARMUP_CALENDAR_DAYS,
        "ALL": 99999,
    }.get(range_code, 90 + _WARMUP_CALENDAR_DAYS)


# Default interval for each range when not explicitly specified
DEFAULT_INTERVALS = {
    "1D": "5min",
    "1W": "1hour",
    "3M": "daily",
    "6M": "daily",
    "1Y": "daily",
    "2Y": "daily",
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
    "2Y": {"daily", "weekly", "monthly"},
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
        "2Y": timedelta(days=365 * 2),
        "5Y": timedelta(days=365 * 5),
    }

    delta = deltas.get(range_code, timedelta(days=90))
    from_date = (today - delta).isoformat()
    return from_date, to_date


def window_by_range(
    bars: List[Dict], range_code: str, resolved_interval: str
) -> List[Dict]:
    """Trim locally-aggregated bars to one range, KEEPING the indicator warm-up.

    Aggregated ranges (5Y weekly, ALL monthly) used to come from `fetch_chart_data`,
    which extends `from_date` by `_warmup_calendar_days(interval)` -- 1,484 days for
    weekly. When commodity_service began deriving them from a shared daily history it
    windowed with `compute_date_range` alone, which dropped ~210 leading weekly bars and
    left MA(200) blank across the entire 5Y chart. That regression is the reason this
    function exists instead of a bare list comprehension at each call site.

    ALL is handled by the caller: it means the whole series, so there is nothing to trim.
    """
    from_date, _to = compute_date_range(range_code)
    # `from_date` is Optional -- None means "no lower bound", and comparing a str against
    # None raises. Serve the full series rather than trimming it to nothing.
    if not from_date:
        return bars
    try:
        from_dt = datetime.strptime(from_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return bars
    warmup = _warmup_calendar_days(resolved_interval)
    cutoff = (from_dt - timedelta(days=warmup)).isoformat()
    return [b for b in bars if (b.get("date") or "") >= cutoff]


#: FMP's row cap on one `historical-price-eod/full` response; a page this long may have
#: older rows behind it (FMP keeps the NEWEST rows of the requested window).
FMP_DAILY_PAGE_ROWS = 5000


def _daily_page_rows(raw: Any, symbol: str, page_no: int) -> List[Dict]:
    """One `historical-price-eod/full` page as rows, oldest first, or a typed error.

    A list (the `/stable` shape) or the legacy ``{"historical": [...]}`` dict is a page;
    an empty list is a real "nothing older". Anything else — FMP's ``{"Error Message":
    ...}`` body on a 200, a bare string — RAISES. `_parse_historical` reads such a body
    as ``[]``, and a paged reader then took it for the END of the history: a blip on page
    2 of INTC cut the series at ~2006, and the ETF/index services cached that truncated
    list for 12 h. A failed page must never look like the first trading day.
    """
    from app.integrations.fmp import FMPUnavailableException

    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict) and isinstance(raw.get("historical"), list):
        rows = raw["historical"]
    else:
        raise FMPUnavailableException(
            f"historical-price-eod/full page {page_no} for {symbol} returned a "
            f"{type(raw).__name__}, not a list of rows"
        )
    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=lambda p: p.get("date") or "")
    return rows


async def _iter_daily_pages(
    fmp: FMPClient,
    symbol: str,
    *,
    from_date: str = "1970-01-01",
    to_date: Optional[str] = None,
    max_pages: int = 5,
):
    """Yield FMP daily pages NEWEST first (each oldest-first), until the history ends.

    Each request asks for ``[from_date, to_date]``; FMP answers the newest
    `FMP_DAILY_PAGE_ROWS` rows of it, so a full page means there may be more, and the
    next request ends the day before that page's earliest row. A short page, an empty
    page or reaching ``from_date`` ends the walk. A malformed page raises
    (`_daily_page_rows`), so a consumer never mistakes a failure for the start of the
    history.
    """
    to = to_date or datetime.now(tz=timezone.utc).date().isoformat()
    for page_no in range(1, max_pages + 1):
        raw = await fmp.get_historical_prices(symbol, from_date, to)
        page = _daily_page_rows(raw, symbol, page_no)
        if not page:
            return
        yield page
        if len(page) < FMP_DAILY_PAGE_ROWS:
            return
        # The first DATED row (rows sort oldest-first, a dateless one first of all): a
        # single row without a date must not read as "no earliest date" and end the walk.
        earliest = next((str(r.get("date"))[:10] for r in page if r.get("date")), "")
        if not earliest or earliest <= from_date:
            return
        try:
            to = (date_type.fromisoformat(earliest) - timedelta(days=1)).isoformat()
        except ValueError:
            return
    logger.warning(
        "daily history for %s still had older rows after %d pages (%d rows each) — "
        "served as far back as the page budget reaches",
        symbol, max_pages, FMP_DAILY_PAGE_ROWS,
    )


async def _fetch_all_daily(
    fmp: FMPClient,
    symbol: str,
    *,
    from_date: str = "1970-01-01",
    to_date: Optional[str] = None,
) -> List[Dict]:
    """
    Fetch full daily history by paginating through FMP's 5000-point limit.

    Makes multiple requests, each fetching up to 5000 data points,
    working backwards from today until no more data is returned. A malformed page
    raises (see `_daily_page_rows`) instead of silently ending the history there.
    """
    all_data: List[Dict] = []
    # Safety: at most 5 requests (up to 25000 data points)
    async for page in _iter_daily_pages(
        fmp, symbol, from_date=from_date, to_date=to_date, max_pages=5
    ):
        all_data = page + all_data  # page is sorted oldest-first, prepend

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


async def _fetch_crypto_chart_data(
    symbol: str, range_code: str, resolved_interval: str
) -> List[Dict[str, Any]]:
    """Chart rows for a crypto pair, from CoinGecko, in the FMP row shape.

    Mirrors `crypto_service`'s own branch so both surfaces draw the SAME series — both
    end in `crypto_service.crypto_chart_rows`: intraday ranges take `market_chart`
    5-minute (1D) / hourly (1W, plus `_INTRADAY_WARMUP_DAYS` of warm-up) bars, daily
    takes the daily series windowed by `daily_range_days`, and weekly/monthly are
    bucketed from the WHOLE capped series before they are windowed (a bucket built from
    a mid-week window start is incomplete).

    ⚠️ Branch on the RANGE, not on `resolved_interval != "daily"`: `DEFAULT_INTERVALS`
    maps 5Y→"weekly" and ALL→"monthly", so an interval test sends those down the
    intraday path and serves SEVEN DAYS of hourly bars under a five-year label.

    Returns [] rather than raising when CoinGecko cannot serve it — an empty chart is
    the same degrade every other failure in this function produces.
    """
    from app.services.coingecko_adapter import crypto_base_symbol
    from app.services.crypto_service import (
        _INTRADAY_RANGES,
        _INTRADAY_WARMUP_DAYS,
        crypto_chart_rows,
    )

    base = crypto_base_symbol(symbol)
    # The SAME table `crypto_service` uses, so the chat card and the detail screen can
    # never disagree about which ranges are sub-daily.
    intraday_days = _INTRADAY_RANGES.get(range_code)
    # CoinGecko Basic caps history at CRYPTO_HISTORY_YEARS; asking past it is refused
    # locally by the client rather than spending a call.
    cap_days = max(1, int(getattr(settings, "CRYPTO_HISTORY_YEARS", 2)) * 365)
    if intraday_days:
        # The visible window stays `_INTRADAY_RANGES`; the warm-up rides in front of it.
        days = intraday_days + _INTRADAY_WARMUP_DAYS.get(range_code, 0)
    elif resolved_interval in AGGREGATED_INTERVALS:
        days = cap_days
    else:
        days = min(daily_range_days(range_code), cap_days)

    try:
        # ⚠️ Go through `_cg_history`, NOT the client directly — it is the CACHED reader
        # (120s intraday / 1h daily, keyed on symbol+days+granularity, with `_inflight`
        # dedup). Calling `get_market_chart` here bypassed all of that on the single
        # hottest crypto path in the app: `CryptoDetailViewModel` polls
        # `/stocks/{t}/chart` every 30 SECONDS while the screen is open and the default
        # range is 1D, so one continuously-open screen was ~2 calls/minute — about
        # 86,000/month against a plan the rest of this codebase sizes as **2.3
        # calls/minute sustained** (100,000/month). A handful of concurrent viewers would
        # have saturated the limiter and starved the price-alert sweeper with it.
        from app.services.crypto_service import get_crypto_service

        rows = await get_crypto_service()._cg_history(
            base, days, intraday=bool(intraday_days)
        )
    except Exception as e:
        logger.warning(
            "crypto chart unavailable for %s %s (%s: %s) — returning an empty series",
            symbol, range_code, type(e).__name__, e,
        )
        return []
    # Daily is trimmed to the requested window (the fetch is capped, not windowed);
    # weekly/monthly honour the request the way the FMP branch always did, and close-only
    # rows aggregate to close-only bars — open/high/low stay None.
    return crypto_chart_rows(rows, range_code, resolved_interval, cap_days=cap_days)


#: How far before an aggregated chart's window the daily history must reach so its
#: OLDEST kept bucket is complete: one full bucket (a quarter is the longest, 92 days).
_BUCKET_COVER_MARGIN_DAYS = 95


async def _fetch_equity_chart_rows(
    fmp: FMPClient,
    symbol: str,
    range_code: str,
    resolved_interval: str,
    from_date: Optional[str],
    to_date: str,
    extended_hours: bool,
) -> List[Dict[str, Any]]:
    """The FMP-served chart rows behind `fetch_chart_data` (cache-free; see its caller).

    * intraday — one bounded `historical-chart` read, warm-up already in ``from_date``;
    * daily, non-ALL — one bounded `historical-price-eod/full` read, so today's
      in-progress bar is still drawn;
    * weekly / monthly — bucketed from the SHARED settled daily history
      (`price_history_service`), then windowed with the interval's warm-up kept. The
      old single windowed call (a) started its first bucket mid-week/mid-month, so that
      candle carried part of a period, and (b) was cut at FMP's 5,000-row cap, so 5Y
      monthly (~22 years) lost MA(200) on its first ~21 visible months. Settled bars
      only, like every other asset class's aggregated chart;
    * ALL — the whole shared history.
    """
    from app.services.price_history_service import (
        EmptyPriceHistory,
        get_price_history_service,
    )

    if resolved_interval in INTRADAY_INTERVALS:
        if range_code == "1D" or not from_date:
            # 1D draws only the latest session, and FMP's newest-first answer (the last
            # 7 days of 5-minute bars at worst) already holds several sessions of warm-up
            # in front of it: one request, as it always was.
            return await _fetch_intraday_bars(
                fmp, symbol, resolved_interval, from_date, to_date, extended_hours
            )
        # 1W asks for more than one FMP request carries (5min: 17 days against 7; 30min:
        # ~36 against 28), so it is stitched — or the warm-up never arrives.
        return await _fetch_intraday_span(
            fmp, symbol, resolved_interval, from_date, to_date, extended_hours
        )

    history = get_price_history_service()
    if resolved_interval in AGGREGATED_INTERVALS or range_code == "ALL":
        covering: Optional[date_type] = None
        if range_code != "ALL" and from_date:
            try:
                covering = date_type.fromisoformat(from_date) - timedelta(
                    days=_BUCKET_COVER_MARGIN_DAYS
                )
            except ValueError:
                covering = None
        try:
            if resolved_interval in AGGREGATED_INTERVALS:
                bars = await history.aggregated_rows(
                    fmp, symbol, resolved_interval, covering=covering
                )
            else:
                bars = await history.daily_rows(fmp, symbol)
        except EmptyPriceHistory as e:
            # The source answered with no rows (a symbol with no EOD history yet). An
            # empty chart, as the single windowed call always gave — the paged history
            # route is the surface that must refuse to call this "start of history".
            logger.info("chart %s %s %s: %s — empty chart", symbol, range_code,
                        resolved_interval, e)
            return []
        if range_code != "ALL" and resolved_interval in AGGREGATED_INTERVALS:
            bars = window_by_range(bars, range_code, resolved_interval)
        return bars

    # Daily (EOD) data
    raw = await fmp.get_historical_prices(symbol, from_date, to_date)
    historical = _parse_historical(raw)
    return _normalize_prices(historical)


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

    # ── Crypto source gate ───────────────────────────────────────────────────
    # FMP 402s every crypto pair, so EVERY call below raises FMPNotEntitledException
    # for one. This is the shared chart fetcher, so that broke callers well beyond the
    # crypto screen — most visibly `/stocks/{ticker}/chart`, which the crypto detail
    # view model polls every 30s for its live intraday chart. Each tick failed and the
    # only trace was a `print` in the client's catch, so the chart silently never
    # updated.
    #
    # Routing HERE rather than at each call site is deliberate: it is the same
    # choke-point argument as `price_service.get_quotes`, and it means the fix reaches
    # every consumer without an iOS release. The FMP path below is untouched and comes
    # back with `CRYPTO_PRICE_SOURCE=fmp`.
    if uses_coingecko_price(symbol) and str(
        getattr(settings, "CRYPTO_PRICE_SOURCE", "coingecko") or ""
    ).lower() != "fmp":
        return await _fetch_crypto_chart_data(symbol, range_code, resolved_interval)

    from_date, to_date = compute_date_range(range_code)
    intraday = resolved_interval in INTRADAY_INTERVALS

    # Extend from_date to include extra data for indicator warm-up
    # (MACD, RSI, etc. need preceding data points to produce values).
    # The frontend trims the warm-up portion from the displayed chart.
    if from_date and from_date != "1970-01-01":
        try:
            from_dt = date_type.fromisoformat(from_date)
            if intraday:
                from_dt = _intraday_warmup_from(from_dt, resolved_interval, extended_hours)
            else:
                from_dt -= timedelta(days=_warmup_calendar_days(resolved_interval))
            from_date = from_dt.isoformat()
        except ValueError:
            pass

    # One shared fetch per (symbol, interval, session, window): `/overview/core`,
    # `/overview` and `/chart` all ask for the same bars at the same moment, and nothing
    # deduplicated them. 30 s for intraday; see `cached_chart_rows` for the rest.
    from app.services.price_history_service import get_price_history_service

    key = (
        symbol.upper(), range_code, resolved_interval,
        bool(extended_hours) if intraday else False, from_date, to_date,
    )
    return await get_price_history_service().cached_chart_rows(
        fmp,
        key,
        intraday=intraday,
        build=lambda: _fetch_equity_chart_rows(
            fmp, symbol, range_code, resolved_interval, from_date, to_date, extended_hours
        ),
    )


async def _fetch_intraday_bars(
    fmp: FMPClient,
    symbol: str,
    interval: str,
    from_date: Optional[str],
    to_date: str,
    extended_hours: bool,
    *,
    strict: bool = False,
) -> List[Dict[str, Any]]:
    """One FMP intraday read, normalised: oldest first, regular session unless asked.

    Shared by the detail chart (`fetch_chart_data`, which widens ``from_date`` by its
    indicator warm-up) and the holdings-card sparkline (`fetch_sparkline_bars`, which
    does not). Extracted unchanged from `fetch_chart_data`'s intraday branch, so both
    paths forward ``extended`` and filter the same way. A non-list answer is ``[]`` —
    or, with ``strict``, an `FMPUnavailableException` (the paged history reads an empty
    window as "no older bars", so an FMP error body must not look like one).

    ⚠️ ONE request: FMP answers at most `FMP_INTRADAY_SPAN_DAYS` of it (the newest
    part). A longer window goes through `_fetch_intraday_span`.
    """
    # `extended` MUST reach the FMP call: the endpoint serves the regular session
    # only unless asked (see `FMPClient.get_intraday_prices`). Skipping the
    # regular-hours filter below is not enough on its own — that was the whole
    # defect behind the inert Extended Hours toggle (TestFlight, build 1.0 (8)).
    intraday_kwargs: Dict[str, Any] = {
        "interval": interval,
        "from_date": from_date,
        "to_date": to_date,
    }
    if extended_hours:
        intraday_kwargs["extended"] = True
    if strict:
        intraday_kwargs["strict"] = True
    raw = await fmp.get_intraday_prices(symbol, **intraday_kwargs)
    if isinstance(raw, list):
        raw.sort(key=lambda p: p.get("date") or "")
        prices = _normalize_prices(raw)
        if not extended_hours:
            # Belt and braces: FMP should already have omitted these, but a
            # pre/after-hours bar that slips through would clamp to the chart edge.
            prices = _filter_regular_hours(prices)
        return prices
    return []


#: FMP's per-request cap on `historical-chart/{interval}`: a request answers at most this
#: many calendar days back from its `to` date — the NEWEST part, the older part silently
#: dropped. Probed live 2026-10-21 (INTC, a 370-day request): 5min returned the last 7
#: days, 15min 42, 30min 28, 1hour 89 — the same sessions with `extended=true` (2.5x the
#: rows), so it is a calendar cap, not a row cap. 1min / 4hour were not probed.
#: Re-probe with `scripts/probe_fmp_intraday_depth.py` if FMP's plan changes.
FMP_INTRADAY_SPAN_DAYS = {"5min": 7, "15min": 42, "30min": 28, "1hour": 89}
#: The most requests one stitched intraday read may make (a 1W 5-minute chart with its
#: warm-up is 3; a paged window that FMP answers whole is 1).
_MAX_INTRADAY_CHUNKS = 6


def _first_trading_day_on_or_after(day: date_type) -> date_type:
    """The first session on or after ``day`` (bounded walk; see `previous_trading_day`)."""
    probe = day
    for _ in range(10):
        if is_trading_day(probe):
            return probe
        probe += timedelta(days=1)
    return day


async def _fetch_intraday_span(
    fmp: FMPClient,
    symbol: str,
    interval: str,
    from_date: str,
    to_date: str,
    extended_hours: bool,
    *,
    strict: bool = False,
) -> List[Dict[str, Any]]:
    """`_fetch_intraday_bars` over ANY window: stitched from newest-first requests.

    One request is answered with at most `FMP_INTRADAY_SPAN_DAYS` (its newest part), so a
    window longer than that — a 1W 5-minute chart with its warm-up is ~17 days — lost its
    oldest days, and with them every warm-up bar the chart's indicators needed. Each next
    request ends ON the oldest day the previous one returned (inclusive; rows are
    de-duplicated by stamp, so a day cut mid-way is re-read whole), until the window's
    first session is reached, a request answers nothing, or nothing older came back (a
    listing inside the window, or a closure the calendar does not know). A window FMP
    answers whole costs exactly one request, as before.
    """
    try:
        start = date_type.fromisoformat(from_date)
        to = date_type.fromisoformat(to_date)
    except (TypeError, ValueError):
        return await _fetch_intraday_bars(
            fmp, symbol, interval, from_date, to_date, extended_hours, strict=strict
        )
    span = FMP_INTRADAY_SPAN_DAYS.get(interval)
    first_session = _first_trading_day_on_or_after(start)
    merged: Dict[str, Dict[str, Any]] = {}
    oldest: Optional[date_type] = None
    for _ in range(_MAX_INTRADAY_CHUNKS):
        chunk_from = start if span is None else max(start, to - timedelta(days=span))
        rows = await _fetch_intraday_bars(
            fmp, symbol, interval, chunk_from.isoformat(), to.isoformat(), extended_hours,
            strict=strict,
        )
        dated = [r for r in rows if isinstance(r.get("date"), str) and len(r["date"]) >= 10]
        if not dated:
            break
        for r in dated:
            merged.setdefault(r["date"], r)
        try:
            earliest = date_type.fromisoformat(dated[0]["date"][:10])
        except ValueError:
            break
        if earliest <= first_session or (oldest is not None and earliest >= oldest):
            break
        oldest = earliest
        to = earliest
    else:
        logger.warning(
            "intraday %s %s %s..%s: still short of the window start after %d requests",
            symbol, interval, from_date, to_date, _MAX_INTRADAY_CHUNKS,
        )
    return [merged[k] for k in sorted(merged)]


# ── Holdings-card sparkline (Tracking) ─────────────────────────────────────────

#: The bar size the holdings card draws — the same 5-minute series as the 1D detail chart
#: one tap away (`DEFAULT_INTERVALS["1D"]`).
SPARKLINE_INTERVAL = DEFAULT_INTERVALS["1D"]


def sparkline_window(today: date_type) -> Tuple[str, str]:
    """``(from_date, to_date)`` ISO strings for one holdings-card sparkline fetch.

    ``today`` is the ET calendar date. The window starts TWO trading days back, not one:

    * pre-market on a trading day there are no bars for ``today`` yet, so the card draws
      the previous session — one hop reaches it;
    * a closure the calendar does not know (``US_MARKET_HOLIDAYS`` plus the closures the
      close ingest learned) makes that one hop land on a day with no bars at all, and the
      card would go blank; the second hop still reaches a real session.

    Weekends and known holidays are stepped over by `previous_trading_day`, so the window
    is 2-3 sessions (~150-230 regular-session five-minute bars) instead of the 10
    calendar days `fetch_chart_data("1D")` asks for (3 days plus a 7-day indicator
    warm-up the card never draws). The caller keeps only the newest day, as before.
    """
    start = previous_trading_day(previous_trading_day(today))
    return start.isoformat(), today.isoformat()


async def fetch_sparkline_bars(
    fmp: FMPClient,
    symbol: str,
    *,
    extended_hours: bool = False,
    today: Optional[date_type] = None,
) -> List[Dict[str, Any]]:
    """The 5-minute bars behind one holdings-card sparkline: no indicator warm-up.

    Same rows, order and session filter as ``fetch_chart_data(fmp, symbol, "1D", ...)``
    over a shorter window (`sparkline_window`). ``today`` defaults to the ET date now; it
    is a parameter so tests can pin a calendar day.

    The crypto gate is the one `fetch_chart_data` applies (FMP 402s every crypto pair),
    so a coin that reaches this function is served from CoinGecko, never from FMP.
    """
    if uses_coingecko_price(symbol) and str(
        getattr(settings, "CRYPTO_PRICE_SOURCE", "coingecko") or ""
    ).lower() != "fmp":
        return await _fetch_crypto_chart_data(symbol, "1D", SPARKLINE_INTERVAL)

    day = today if today is not None else datetime.now(ET).date()
    from_date, to_date = sparkline_window(day)
    return await _fetch_intraday_bars(
        fmp, symbol, SPARKLINE_INTERVAL, from_date, to_date, extended_hours
    )


# ── Holdings-card extended-hours print (Tracking) ──────────────────────────────


async def fetch_extended_bars(
    fmp: FMPClient,
    symbol: str,
    *,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """The 5-minute bars, pre-market and after-hours INCLUDED, behind one row's
    extended-hours print: from the last regular session through today (ET).

    The licensed source of an after-hours price. FMP's `aftermarket-quote` is in the
    "Real-time Market Data" package we did not buy, and the quote sources
    (`company-screener`, `profile`) carry the regular-session price only — but
    `historical-chart` serves the 04:00-20:00 ET bars when asked (`extended=true`).

    The window starts at `last_regular_session`, so it holds that session's last
    regular bar (what the print is measured after) and everything since: its
    after-hours, and before the open today's pre-market. One session (≤ 192 rows)
    after the close; that session plus today's pre-market before the bell.

    The caller gates crypto (24/7, no extended session) and licence-blocked symbols
    out before calling; this function does not re-check.
    """
    if now is None:
        now = datetime.now(ET)
    elif now.tzinfo is None:
        # `market_hours` reads a naive instant as UTC; so must the "today" below.
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(ET).date()
    return await _fetch_intraday_bars(
        fmp, symbol, SPARKLINE_INTERVAL,
        last_regular_session(now).isoformat(), today.isoformat(),
        extended_hours=True,
    )


def latest_extended_print(bars: Any) -> Optional[Dict[str, Any]]:
    """The newest pre-market / after-hours print after the last regular-session bar.

    ``bars`` is `fetch_extended_bars` output: oldest first, and already normalised
    (`_normalize_prices` dropped rows with no date or a non-positive close). Returns
    ``{"price": close, "session": "pre_market" | "after_hours"}`` — the wire values of
    `market_hours.WIRE_STATUS_*` — or None when no regular bar exists to measure from,
    or nothing traded after it.

    The bell comes from `_session_window`, not `_filter_regular_hours`: that filter
    closes every day at 16:00, so a half-day's 13:00-17:00 after-hours prints would
    read as regular session and never surface. The session is named by the print's
    OWN stamp (FMP's ET wall clock), never by the clock now: at 05:00 Monday, before
    any pre-market trade, the newest print is still Friday's after-hours one, and it
    says so.
    """
    if not isinstance(bars, list):
        return None
    last_regular: Optional[int] = None
    for idx, bar in enumerate(bars):
        if not isinstance(bar, dict):
            continue
        stamp = bar.get("date")
        minute = _bar_minute_of_day(stamp)
        window = _session_window(stamp, extended_hours=False)
        if minute is None or window is None:
            continue
        if window[0] <= minute < window[1]:
            last_regular = idx
    if last_regular is None:
        return None
    for bar in reversed(bars[last_regular + 1:]):
        if not isinstance(bar, dict):
            continue
        minute = _bar_minute_of_day(bar.get("date"))
        close = _finite_or_none(bar.get("close"))
        if minute is None or close is None or close <= 0:
            continue
        session = (
            WIRE_STATUS_PRE_MARKET if minute < _REGULAR_OPEN_MINUTE
            else WIRE_STATUS_AFTER_HOURS
        )
        return {"price": close, "session": session}
    return None


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


def _positive_or_none(v: Any) -> Optional[float]:
    """A finite price > 0, else None. An open/high/low of 0 (or below) is a missing
    print, not a price: drawn, it stretches the candle's wick to the axis and drags the
    Stochastic's 14-bar low to zero."""
    f = _finite_or_none(v)
    return f if f is not None and f > 0 else None


def _volume_or_none(v: Any) -> Optional[float]:
    """A finite volume ≥ 0, else None. 0 is a reported (halted) session; a negative
    count is malformed and is not a volume at all."""
    f = _finite_or_none(v)
    return f if f is not None and f >= 0 else None


def _normalize_prices(prices: List[Dict]) -> List[Dict]:
    """Extract standard OHLCV fields from price data.

    Rows with a missing/blank ``date`` or a non-finite / non-positive ``close``
    are dropped: a ``date=None`` crashes ``_filter_regular_hours`` (``len(None)``)
    and downstream schemas that require ``date: str``, and a non-finite close
    poisons a REQUIRED numeric field with an invalid JSON token. An open/high/low
    ≤ 0 becomes None (never fabricated from the close), and so does a negative volume.
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
            "open": _positive_or_none(p.get("open")),
            "high": _positive_or_none(p.get("high")),
            "low": _positive_or_none(p.get("low")),
            "close": close,
            "volume": _volume_or_none(p.get("volume")),
        })
    return result


def _aggregate_prices(
    daily_prices: List[Dict], period: str
) -> List[Dict]:
    """Aggregate daily prices into weekly/monthly/quarterly OHLCV bars.

    A bucket's ``date`` is its LAST trading day. ``volume`` is None when no row in the
    bucket reports one (FRED's close-only spot rows): a summed 0 would draw a confident
    zero-volume bar where no volume was ever observed. An open/high/low ≤ 0 is a missing
    print and takes no part in the bucket's range.
    """
    if not daily_prices:
        return []

    groups: Dict[str, List[Dict]] = {}
    for p in daily_prices:
        if not isinstance(p, dict):
            continue
        date_str = (p.get("date") or "")[:10]
        try:
            # `fromisoformat`, not `strptime`: ~5x faster per row, and this runs over a
            # whole multi-decade history on the event loop.
            dt = date_type.fromisoformat(date_str)
        except ValueError:
            continue

        if period == "weekly":
            key = (dt - timedelta(days=dt.weekday())).isoformat()
        elif period == "monthly":
            key = date_str[:7]
        elif period == "quarterly":
            q = (dt.month - 1) // 3 + 1
            key = f"{dt.year}-Q{q}"
        else:
            key = date_str

        groups.setdefault(key, []).append(p)

    result = []
    for key in sorted(groups.keys()):
        candles = groups[key]
        opens = [f for f in (_positive_or_none(c.get("open")) for c in candles) if f is not None]
        highs = [f for f in (_positive_or_none(c.get("high")) for c in candles) if f is not None]
        lows = [f for f in (_positive_or_none(c.get("low")) for c in candles) if f is not None]
        closes = [
            f for f in (_finite_or_none(c.get("close") or c.get("adjClose")) for c in candles)
            if f is not None and f > 0
        ]
        volumes = [
            f for f in (_volume_or_none(c.get("volume")) for c in candles) if f is not None
        ]

        if closes:
            result.append({
                "date": (candles[-1].get("date") or "")[:10],
                "open": opens[0] if opens else None,
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None,
                "close": closes[-1],
                "volume": sum(volumes) if volumes else None,
            })
    return result


def drop_partial_leading_bucket(
    bars: List[Dict], daily_rows: List[Dict], period: str
) -> List[Dict]:
    """Drop the first aggregated bucket when the daily series starts mid-bucket.

    For a series cut by a PROVIDER cap (CoinGecko Basic's 730 days) the first daily row
    is an arbitrary day, so the first weekly/monthly bucket holds only part of its
    period — its volume is short and its range is not the period's. A listing's own
    first week is different (the first day IS the start), so callers use this only for
    capped series. Crypto trades every calendar day, so "starts mid-bucket" is exact:
    the first row is not a Monday (weekly) or not the 1st (monthly / quarterly).
    """
    if not bars or not daily_rows:
        return bars
    first = _row_day(daily_rows[0])
    if first is None:
        return bars
    if period == "weekly":
        starts_bucket = first.weekday() == 0
    elif period == "monthly":
        starts_bucket = first.day == 1
    elif period == "quarterly":
        starts_bucket = first.day == 1 and first.month in (1, 4, 7, 10)
    else:
        return bars
    return bars if starts_bucket else bars[1:]
