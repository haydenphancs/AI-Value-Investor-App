"""Which symbols FMP actually PRICED in the latest US session — held in memory.

FMP's activity flag (`profile.isActivelyTrading`, `/stable/actively-trading-list`) answers
"is this listing alive?", and it can say "inactive" for a security that trades:

  • Measured 2026-10-08: that day's `batch-eod` priced 1,204 non-blocked, dot-free symbols
    the actively-trading list omits. Most are crypto and FX pairs, but they include real,
    trading securities: SPAC units (ARTCU traded 403 shares, CLBR-UN 95,227), OTC foreign
    ordinaries (AHEXF Adecco) and an ETF (SHUS).
  • A listing that MOVED is the case this exists for. Energy Transfer, Dillard's and USA
    Compression moved from NYSE to the Texas Stock Exchange on 2026-10-05. FMP carries no
    TXSE prices, so all three left `batch-eod` AND the list, with prices frozen at 10-05.
    If FMP later prices them but leaves the flag false, this registry is what notices.

The rule: a symbol with a close AND traded volume in the latest US session is being
priced, whatever the flag says. No truly delisted symbol can produce one (TWTR, FI and
AVGOP are absent from the same file), and a zero-volume row is not counted, so a
carried-forward reference close never revives anything.

Fed by `price_service.refresh_close_snapshot`, which already fetches the whole session
every hour (`_run_close_snapshot_loop`). IN MEMORY ONLY: the table it writes,
`market_close_snapshot`, keeps a symbol's LAST priced session forever, so it cannot say
"priced in the latest session" without a full scan. After a restart the registry is
empty until the first cycle (~90 s + the fetch). Every reader then gets None and keeps
the flag's answer, exactly the pre-registry behaviour. It only ever ADDS liveness.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from app.integrations.fmp_entitlements import is_blocked_symbol
from app.utils.market_hours import previous_trading_day, session_trading_date

logger = logging.getLogger(__name__)

# A healthy US session prices tens of thousands of non-blocked symbols. A payload far
# below this is truncated, and publishing it would only make readers fall back to the
# flag — but the unpriced-holdings report reads absence as news, so it must not see one.
_MIN_PRICED_SYMBOLS = 5_000

# A profile price this close to the stored close has not moved off it (float noise and
# a sub-cent print are not a trade): $0.005 or 0.01% of the close, whichever is larger.
_MOVED_ABS = 0.005
_MOVED_REL = 1e-4

_lock = threading.Lock()
# (session date, {symbol: close} for every symbol that traded in it). None until the
# first publish. The close is what `change_is_current` compares a profile price against.
_latest: Optional[Tuple[date, Mapping[str, float]]] = None


def _finite_positive(value: Any) -> Optional[float]:
    """The value as a float when it is finite and > 0, else None."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 and f != float("inf") else None  # NaN fails `> 0`


def publish_priced_session(trade_date: Any, rows: Iterable[Any]) -> Optional[int]:
    """Install `rows` (one `batch-eod` US session) as the latest priced session.

    Returns the number of symbols installed, or None when nothing was installed: an
    unreadable date, a payload under `_MIN_PRICED_SYMBOLS`, or a session OLDER than the
    one already held (a backfill call must never roll the registry back).
    """
    global _latest
    try:
        session = date.fromisoformat(str(trade_date)[:10])
    except ValueError:
        logger.warning("session_pricing: unreadable session date %r — not published", trade_date)
        return None
    symbols: Dict[str, float] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        sym = row.get("symbol")
        if not isinstance(sym, str):
            continue
        sym = sym.strip().upper()
        if not sym or is_blocked_symbol(sym):
            continue
        close = _finite_positive(row.get("close"))
        if close is None or _finite_positive(row.get("volume")) is None:
            continue  # no trade: a reference close proves nothing
        symbols[sym] = close
    if len(symbols) < _MIN_PRICED_SYMBOLS:
        logger.warning(
            "session_pricing: %s priced only %d traded symbols (floor %d) — a truncated "
            "payload; the registry keeps what it held",
            session, len(symbols), _MIN_PRICED_SYMBOLS,
        )
        return None
    with _lock:
        held = _latest
        if held is not None and session < held[0]:
            logger.info(
                "session_pricing: %s is older than the held session %s — not published",
                session, held[0],
            )
            return None
        _latest = (session, symbols)
    return len(symbols)


def latest_priced_session(now: Optional[datetime] = None) -> Optional[Tuple[date, Mapping[str, float]]]:
    """The held (session, {symbol: close}), or None when there is none or it is too old to say
    anything about now (older than the session before the one current numbers describe —
    the same bar `PriceService._snapshot_is_current` sets for a stored close)."""
    held = _latest
    if held is None:
        return None
    session, symbols = held
    if session < previous_trading_day(session_trading_date(now)):
        return None
    return held


def priced_in_latest_session(symbol: Any, now: Optional[datetime] = None) -> Optional[bool]:
    """True/False when the registry can answer, None when it cannot (cold or stale).

    Callers treat only True as evidence. None and False both mean "no override": the
    caller keeps whatever FMP's flag says.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    held = latest_priced_session(now)
    if held is None:
        return None
    return symbol.strip().upper() in held[1]


def change_is_current(symbol: Any, price: Any, now: Optional[datetime] = None) -> Optional[bool]:
    """May a quote for `symbol` at `price` describe the CURRENT session's move?

    Stricter than liveness. The ingest only lands session D after 00:00 UTC on D+1, so
    for all of day D the registry holds D-1. A listing FMP stopped pricing on D traded
    on D-1, and its frozen D-1 move must not read as D's. While the registry is a session
    behind, the price must have MOVED off the stored D-1 close (it traded on D). Once the
    registry holds the current session, having traded in it is enough. A symbol that has
    not traded yet today (pre-market) therefore reads as unknown, never as a stale move.

    None when the registry cannot answer (cold or stale); callers treat only True as evidence.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    held = latest_priced_session(now)
    if held is None:
        return None
    session, closes = held
    close = closes.get(symbol.strip().upper())
    if close is None:
        return False
    if session >= session_trading_date(now):
        return True
    p = _finite_positive(price)
    if p is None:
        return False
    return abs(p - close) > max(_MOVED_ABS, close * _MOVED_REL)


def _reset_for_tests() -> None:
    global _latest
    with _lock:
        _latest = None
