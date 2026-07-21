"""
US Market Hours Utility
Determines whether US equity markets are in an active trading session.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# US market holidays for 2025-2026 (NYSE/NASDAQ closed)
US_MARKET_HOLIDAYS = {
    # 2025
    (2025, 1, 1),   # New Year's Day
    (2025, 1, 20),  # MLK Day
    (2025, 2, 17),  # Presidents' Day
    (2025, 4, 18),  # Good Friday
    (2025, 5, 26),  # Memorial Day
    (2025, 6, 19),  # Juneteenth
    (2025, 7, 4),   # Independence Day
    (2025, 9, 1),   # Labor Day
    (2025, 11, 27), # Thanksgiving
    (2025, 12, 25), # Christmas
    # 2026
    (2026, 1, 1),   # New Year's Day
    (2026, 1, 19),  # MLK Day
    (2026, 2, 16),  # Presidents' Day
    (2026, 4, 3),   # Good Friday
    (2026, 5, 25),  # Memorial Day
    (2026, 6, 19),  # Juneteenth
    (2026, 7, 3),   # Independence Day (observed)
    (2026, 9, 7),   # Labor Day
    (2026, 11, 26), # Thanksgiving
    (2026, 12, 25), # Christmas
}


SESSION_CLOSED = "closed"
SESSION_PREMARKET = "premarket"
SESSION_REGULAR = "regular"
SESSION_AFTERHOURS = "afterhours"

# Minute-of-day boundaries, ET.
_PREMARKET_START = 4 * 60        # 04:00
_REGULAR_OPEN = 9 * 60 + 30      # 09:30
_REGULAR_CLOSE = 16 * 60         # 16:00
_AFTERHOURS_END = 20 * 60        # 20:00


def session_phase(now: datetime | None = None) -> str:
    """Which part of the trading day it is, in ET.

    ``is_market_active()`` collapses pre-market, regular and after-hours into a
    single boolean, which is the right granularity for "should the sweeper run
    at all". It is the wrong granularity for spending a *daily* budget: the
    sweeper wakes at 04:00 ET and news flows continuously, so a busy scope
    exhausted its whole per-scope allowance in pre-market and was frozen for the
    entire 09:30-16:00 session — observed live, ``__MARKET__`` last generated at
    05:49 ET with ``regen_count_today = 6/6``.

    ``now`` is injectable so callers and tests stay clock-independent.
    """
    if now is None:
        now = datetime.now(ET)
    elif now.tzinfo is None:
        # A naive datetime has no offset, and `astimezone()` would read it as
        # LOCAL time — UTC on Railway, America/Denver on the dev machine. The
        # same input would then resolve to different sessions in different
        # environments. Every caller here means UTC, so say so explicitly.
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(ET)

    if now.weekday() >= 5:
        return SESSION_CLOSED
    if (now.year, now.month, now.day) in US_MARKET_HOLIDAYS:
        return SESSION_CLOSED

    minute_of_day = now.hour * 60 + now.minute
    if minute_of_day < _PREMARKET_START or minute_of_day >= _AFTERHOURS_END:
        return SESSION_CLOSED
    if minute_of_day < _REGULAR_OPEN:
        return SESSION_PREMARKET
    if minute_of_day < _REGULAR_CLOSE:
        return SESSION_REGULAR
    return SESSION_AFTERHOURS


def is_market_active(now: datetime | None = None) -> bool:
    """
    Check if US equity markets are in an active trading session.

    Returns True during:
    - Pre-market:  4:00 AM – 9:30 AM ET
    - Regular:     9:30 AM – 4:00 PM ET
    - After-hours: 4:00 PM – 8:00 PM ET

    Returns False during overnight (8 PM – 4 AM ET), weekends, and holidays.

    Defined as the complement of :data:`SESSION_CLOSED` rather than as a second
    copy of the boundary arithmetic. The two helpers gate the same sweeper — one
    decides whether it runs, the other how it spends — so a drift between them
    would let it run in a phase the budget gate believes is closed.

    ``now`` is injectable for tests; production calls it with no argument.
    """
    return session_phase(now) != SESSION_CLOSED
