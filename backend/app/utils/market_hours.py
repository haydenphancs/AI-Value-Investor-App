"""
US Market Hours Utility
Determines whether US equity markets are in an active trading session.
"""

from datetime import date, datetime, timedelta, timezone
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
    # 2027 — extend forward so the calendar does not silently expire at the end
    # of 2026 and report a CLOSED holiday as an active session (a false-active
    # would let the insight sweeper spend Gemini budget on a shut market).
    (2027, 1, 1),   # New Year's Day
    (2027, 1, 18),  # MLK Day
    (2027, 2, 15),  # Presidents' Day
    (2027, 3, 26),  # Good Friday
    (2027, 5, 31),  # Memorial Day
    (2027, 6, 18),  # Juneteenth (observed — Jun 19 is a Saturday)
    (2027, 7, 5),   # Independence Day (observed — Jul 4 is a Sunday)
    (2027, 9, 6),   # Labor Day
    (2027, 11, 25), # Thanksgiving
    (2027, 12, 24), # Christmas (observed — Dec 25 is a Saturday)
}

# Half-days: NYSE/NASDAQ close at 13:00 ET (day after Thanksgiving, Christmas
# Eve when it is a trading day, July 3 when the 4th is a weekday). Without this
# the fixed 16:00 close reports 13:00-16:00 ET as REGULAR and 16:00-20:00 as
# AFTERHOURS on these days, so is_market_active() is True while the tape is shut
# — and the insight sweeper burns budget + mislabels the card's freshness.
US_MARKET_EARLY_CLOSES = {
    # 2025
    (2025, 7, 3),    # Independence Day eve
    (2025, 11, 28),  # Day after Thanksgiving
    (2025, 12, 24),  # Christmas Eve
    # 2026
    (2026, 11, 27),  # Day after Thanksgiving
    (2026, 12, 24),  # Christmas Eve
    # 2027
    (2027, 11, 26),  # Day after Thanksgiving
}


SESSION_CLOSED = "closed"
SESSION_PREMARKET = "premarket"
SESSION_REGULAR = "regular"
SESSION_AFTERHOURS = "afterhours"

# Minute-of-day boundaries, ET.
_PREMARKET_START = 4 * 60        # 04:00
_REGULAR_OPEN = 9 * 60 + 30      # 09:30
_REGULAR_CLOSE = 16 * 60         # 16:00
_EARLY_CLOSE = 13 * 60           # 13:00 (half-day close)
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
    ymd = (now.year, now.month, now.day)
    if ymd in US_MARKET_HOLIDAYS:
        return SESSION_CLOSED

    minute_of_day = now.hour * 60 + now.minute
    if minute_of_day < _PREMARKET_START or minute_of_day >= _AFTERHOURS_END:
        return SESSION_CLOSED
    # Half-day: pre-market and the 09:30-13:00 regular session run as normal, but
    # the market shuts at 13:00 ET — there is no 13:00-16:00 regular block and no
    # after-hours. Anything at/after 13:00 is CLOSED.
    if ymd in US_MARKET_EARLY_CLOSES and minute_of_day >= _EARLY_CLOSE:
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


# ── Which SESSION a set of numbers describes ──────────────────────────
#
# `session_phase` answers "what is happening right now". That is a different
# question from "which trading day do these numbers belong to", and conflating
# them is what let a Friday snapshot render on Monday with no label: the phase
# on Monday morning is `premarket`, so a phase-only reading calls Friday's close
# "pre-market" and says nothing about the date.
#
# The date is the honest anchor. It lets a client re-derive "Fri close" at RENDER
# time, days later, without anyone having to keep a staleness flag true.


def _close_minute(d: date) -> int:
    """Minute-of-day the tape shuts on ``d`` — 13:00 ET on a half-day, else 16:00."""
    return (
        _EARLY_CLOSE
        if (d.year, d.month, d.day) in US_MARKET_EARLY_CLOSES
        else _REGULAR_CLOSE
    )


def is_trading_day(d: date) -> bool:
    """True when the tape opens at all on ``d``. Half-days ARE trading days."""
    if d.weekday() >= 5:
        return False
    return (d.year, d.month, d.day) not in US_MARKET_HOLIDAYS


def previous_trading_day(d: date) -> date:
    """The most recent trading day strictly before ``d``.

    Bounded rather than a `while True`: the longest US market closure in modern
    history is four consecutive sessions, so ten calendar days cannot be exhausted
    by a real calendar. An unbounded loop here would hang a request on a malformed
    holiday table instead of returning a slightly wrong date, and a widget that
    hangs is worse than a widget that is a day off.
    """
    probe = d - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(probe):
            return probe
        probe -= timedelta(days=1)
    return probe


def session_trading_date(now: datetime | None = None) -> date:
    """The ET calendar date of the session the current numbers describe.

    Not "today". At 03:00 ET on a Tuesday the freshest quotes available are
    Monday's close, and calling them Tuesday's is the lie this function exists to
    prevent.

        premarket / regular / afterhours  -> today (the session is live or just was)
        closed, and today's close already happened -> today
        anything else                     -> the previous trading day

    "Today's close already happened" is minute-of-day past the close, which is
    13:00 on a half-day. That matters: on the Friday after Thanksgiving at 14:00 ET
    `session_phase` returns `closed`, but the session did happen and the numbers
    are that Friday's.
    """
    if now is None:
        now = datetime.now(ET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(ET)
    today = now.date()

    if session_phase(now) != SESSION_CLOSED:
        return today

    if is_trading_day(today) and (now.hour * 60 + now.minute) >= _close_minute(today):
        return today

    return previous_trading_day(today)


# ── Detail-screen market-status wire contract ─────────────────────────
#
# The stock / ETF / index detail services each shipped their OWN copy of the
# session arithmetic, derived from weekday + hour only. All three therefore
# reported "open" at 11:00 ET on Thanksgiving, and kept reporting "open" until
# 16:00 on the half-days that shut at 13:00 — this module already knew both
# facts, and `home_dashboard_service` already delegated here.
#
# The wire strings are a CONTRACT with three iOS switches and deliberately differ
# from the SESSION_* constants above (`regular` -> `open`, `premarket` ->
# `pre_market`, `afterhours` -> `after_hours`). Mapping them in exactly one place
# is the point: a silent mismatch here degrades to an unrecognised status on the
# client, which is the failure mode this indirection exists to prevent.
_PHASE_TO_WIRE_STATUS: dict[str, str] = {
    SESSION_PREMARKET: "pre_market",
    SESSION_REGULAR: "open",
    SESSION_AFTERHOURS: "after_hours",
    SESSION_CLOSED: "closed",
}

# The four wire values, exported so a test can assert the mapping is total
# without re-typing the strings it is meant to be checking.
WIRE_STATUS_PRE_MARKET = "pre_market"
WIRE_STATUS_OPEN = "open"
WIRE_STATUS_AFTER_HOURS = "after_hours"
WIRE_STATUS_CLOSED = "closed"


def market_status_fields(now: datetime | None = None) -> dict[str, str | None]:
    """The `{status, date, time, timezone}` payload the detail screens render.

    Returned as a plain dict rather than a Pydantic model on purpose: `MarketStatusResponse`
    is declared separately in `schemas/index.py` and `schemas/etf.py`, and a utils module
    must not import the schema layer. Each service does `MarketStatusResponse(**fields)`.

    When the market is CLOSED the payload names the **last real close**, not "today at
    16:00". The old copies stamped `now.date()` at a hardcoded 16:00, so on a Saturday they
    announced a Saturday close that never happened, and on the Friday after Thanksgiving
    they reported 16:00 for a tape that shut at 13:00. `session_trading_date()` and
    `_close_minute()` already answer both questions.

    ``now`` is injectable so callers and tests stay clock-independent.
    """
    if now is None:
        now = datetime.now(ET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(ET)

    phase = session_phase(now)
    status = _PHASE_TO_WIRE_STATUS[phase]

    if status != WIRE_STATUS_CLOSED:
        # Live session: the client shows a live badge and never reads the other three.
        return {"status": status, "date": None, "time": None, "timezone": None}

    close_day = session_trading_date(now)
    close_min = _close_minute(close_day)
    # Build the instant ON the close date, not on `now` — the UTC offset and the
    # EST/EDT abbreviation belong to THAT day. Stamping a November close with
    # August's EDT is exactly the class of bug this replaces.
    close_et = datetime(
        close_day.year, close_day.month, close_day.day,
        close_min // 60, close_min % 60, tzinfo=ET,
    )
    hour12 = close_et.hour % 12 or 12
    meridiem = "AM" if close_et.hour < 12 else "PM"
    return {
        "status": WIRE_STATUS_CLOSED,
        "date": close_et.isoformat(),
        "time": f"{hour12}:{close_et.minute:02d} {meridiem}",
        "timezone": close_et.tzname() or "ET",
    }


def session_label(now: datetime | None = None) -> str:
    """One short sentence naming the session, true at ``now``.

    Deterministic and built server-side so the wording lives in one place — the
    same posture as the widget's `cause.detail` and `basket.text`. Clients may
    DOWNGRADE it as it ages (a "Live" label stops being true within minutes) but
    never compose their own.
    """
    if now is None:
        now = datetime.now(ET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(ET)

    phase = session_phase(now)
    if phase == SESSION_CLOSED:
        # Name the DAY, because that is the only thing still true tomorrow.
        return f"{session_trading_date(now).strftime('%a')} close"

    # `%-I` is glibc/BSD-only and silently differs on other libcs; do it by hand.
    hour12 = now.hour % 12 or 12
    meridiem = "AM" if now.hour < 12 else "PM"
    clock = f"{hour12}:{now.minute:02d} {meridiem} ET"

    if phase == SESSION_PREMARKET:
        return f"Pre-market {clock}"
    if phase == SESSION_AFTERHOURS:
        return f"After hours {clock}"
    return f"Live {clock}"
