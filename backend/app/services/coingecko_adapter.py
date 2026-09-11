"""Adapt CoinGecko payloads into the row shape the rest of the app already speaks.

Everything downstream of a price fetch — `chart_helper`, `_extract_chart_data`,
`_compute_return`, `technical_analysis_service`, `_intraday_sparkline` — was written
against FMP's row shape: a list of dicts with a `date` string and OHLCV keys. Rather than
rewrite those consumers, this module translates. That keeps the migration to a source
swap instead of a rewrite, and leaves every existing math test meaningful.

Two conversions here are easy to get wrong and invisible when wrong:

**1. Timestamps are ET WALL-CLOCK, not UTC.** CoinGecko returns epoch milliseconds. The
app's contract is a naive local-time string in America/New_York — `chart_helper`'s
`_bar_minute_of_day` parses `"%Y-%m-%d %H:%M:%S"` with no offset, and iOS's
`ChartDateFormatters.inputDateTimeFormatter` pins `TimeZone(identifier: "America/New_York")`.
Emitting UTC would shift every crypto intraday bar by 4-5 hours: the crosshair would read
20:00 for a 16:00 ET print, and `_intraday_sparkline`'s `bars[-1]["date"][:10]` day-cut
would start "today" at 20:00 the previous evening.

**2. `prices` and `total_volumes` are joined on the TIMESTAMP, never zipped.** They are
independent arrays and their lengths do diverge (new listings, a partial trailing point).
A positional zip misaligns volume against price silently, which then feeds OBV and the
30-day average volume with no visible symptom.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

__all__ = [
    "ET",
    "epoch_ms_to_et",
    "market_chart_to_rows",
    "ohlc_to_rows",
    "markets_rows_by_id",
    "crypto_base_symbol",
]

ET = _ET


def _finite(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def epoch_ms_to_et(ms: Any, *, intraday: bool) -> Optional[str]:
    """Epoch milliseconds → the app's naive ET date string.

    `intraday=False` yields `YYYY-MM-DD`; `True` yields `YYYY-MM-DD HH:MM:SS`. Both are
    ET wall-clock with no offset suffix, matching every other producer in the app.
    """
    try:
        seconds = float(ms) / 1000.0
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds):
        return None
    try:
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(_ET)
    except (OverflowError, OSError, ValueError):
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S" if intraday else "%Y-%m-%d")


def market_chart_to_rows(
    payload: Optional[Dict[str, Any]], *, intraday: bool
) -> List[Dict[str, Any]]:
    """`{prices, total_volumes}` → oldest-first rows shaped like FMP's daily history.

    Emits `close` and `volume` only. **No `open`/`high`/`low`** — `market_chart` does not
    carry them, and synthesising them from the close would assert an intraday range that
    was never observed. Consumers that need OHLC must say so (see `ohlc_to_rows`).
    """
    if not isinstance(payload, dict):
        return []
    prices = payload.get("prices") or []
    volumes = payload.get("total_volumes") or []
    if not isinstance(prices, list):
        return []

    # Join on the timestamp. See the module docstring — these arrays are not parallel.
    vol_by_ts: Dict[Any, float] = {}
    if isinstance(volumes, list):
        for pair in volumes:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                v = _finite(pair[1])
                if v is not None:
                    vol_by_ts[pair[0]] = v

    # One row per (ET) date; on a repeat the NEWEST timestamp wins.
    #
    # ⚠️ "Keep the first" was wrong for the daily series. CoinGecko's daily `market_chart`
    # is one 00:00 UTC print per day PLUS the current price as the last element. The
    # midnight print maps to 20:00/19:00 ET of the PREVIOUS calendar day, so from 00:00
    # UTC until midnight ET (20:00–24:00 EDT) the midnight bar and the live point share
    # an ET date — and keeping the first DROPPED the live point. The chart's last close,
    # `_compute_return`'s `prices[-1]`, the sentiment 7d arm and the tracking daily path
    # were then 4–5 hours stale beside a live header every evening. For an intraday
    # series bucketed into days the newest point is equally the right survivor (the
    # day's last observed price, not its first).
    by_date: Dict[str, tuple] = {}
    for pair in prices:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        ts = pair[0]
        close = _finite(pair[1])
        if close is None or close <= 0:
            continue
        date = epoch_ms_to_et(ts, intraday=intraday)
        if date is None:
            continue
        try:
            order = float(ts)
        except (TypeError, ValueError):
            continue
        prev = by_date.get(date)
        if prev is None or order > prev[0]:
            by_date[date] = (order, {"date": date, "close": close, "volume": vol_by_ts.get(ts)})
    rows = [row for _, row in by_date.values()]
    rows.sort(key=lambda r: r["date"])
    return rows


def ohlc_to_rows(payload: Any) -> List[Dict[str, Any]]:
    """CoinGecko `/ohlc` `[[ts, o, h, l, c], ...]` → oldest-first OHLC rows.

    Used only for the 52-week band. The candles are coarse (4-day at ranges ≥ 90 days and
    CoinGecko offers nothing finer on this plan), but each candle's high/low ARE true
    intraday extremes over its bucket — so `max(high)` / `min(low)` across a 365-day
    request is a genuine 52-week band, which the close-only `market_chart` cannot give.
    """
    if not isinstance(payload, list):
        return []
    rows: List[Dict[str, Any]] = []
    for c in payload:
        if not isinstance(c, (list, tuple)) or len(c) < 5:
            continue
        date = epoch_ms_to_et(c[0], intraday=False)
        o, h, low, close = (_finite(c[1]), _finite(c[2]), _finite(c[3]), _finite(c[4]))
        if date is None or close is None or close <= 0:
            continue
        rows.append({"date": date, "open": o, "high": h, "low": low, "close": close})
    rows.sort(key=lambda r: r["date"])
    return rows


def markets_rows_by_id(rows: Any) -> Dict[str, Dict[str, Any]]:
    """`/coins/markets` response → `{coin_id: row}`.

    ⚠️ Keyed on the COIN ID, never on `row["symbol"]`. Two of our symbols resolve to the
    same id (`MATIC` and `POL` are both `polygon-ecosystem-token`), and CoinGecko answers
    with one canonical symbol — so keying by symbol silently drops the other, which
    appears in six related-coin sets. `/coins/markets` also orders by market cap rather
    than request order, so positional zipping against the requested list is wrong too.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(rows, list):
        return out
    for r in rows:
        if isinstance(r, dict) and r.get("id"):
            out[str(r["id"])] = r
    return out


def crypto_base_symbol(symbol: Any) -> str:
    """`BTCUSD` / `BTCUSDT` / `BTC` → `BTC`. The app's pair convention → CoinGecko's.

    CoinGecko addresses coins by a bare base symbol (which `resolve_coin_id` then maps
    to an id), while every symbol inside this app carries the FMP-style USD quote
    suffix. One helper so the strip is not re-implemented per call site — and so
    `USDT` is always tried BEFORE `USD`, which is the ordering bug waiting to happen:
    stripping "USD" from "BTCUSDT" leaves "BTCT", which resolves to nothing and
    silently drops the coin.

    A bare symbol passes through unchanged, and the suffix is only removed when
    something remains in front of it (so the ETF ticker `USD` is never emptied).
    """
    s = str(symbol or "").strip().upper()
    for suffix in ("USDT", "USD"):
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s
