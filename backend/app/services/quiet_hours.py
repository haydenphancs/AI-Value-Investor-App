"""Quiet hours — pure time math, no I/O.

Everything in the push stack until now ran on Eastern Time, because ET is the trading
day and the only sender was a market-hours price move. That stops working the moment
notifications are not tied to the US session: a 09:35 ET earnings alert reaches a user
in Tokyo at 22:35 and one in Sydney at 00:35.

Two ideas here, and they are separate on purpose:

  * **The user's timezone** decides when their day rolls — which is what the
    per-category daily cap counts against. A Tokyo user's "3 per day" resetting at ET
    midnight resets at 2pm their time, which is not a day.
  * **Quiet hours** decide whether a notification may buzz *right now*. Outside them,
    it is DEFERRED, never dropped: the ledger row is written either way, so the inbox
    shows it immediately and only the buzz waits.

This module is pure so every DST and wraparound case below is testable with no clock,
no database and no network.

⚠️ THREE CLOCKS, NEVER INTERCHANGED — this repo has already shipped a bug from mixing
two of them (migration 089):
  * `trading_date_et()`      — the dedup bucket. ET, because that is the trading day.
  * `datetime.now(timezone.utc)` — the retention bucket. Absolute, monotonic-ish.
  * the user's tz            — the daily-cap bucket and quiet hours. Per person.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# The app's existing trading-day convention, and the fallback for an unusable timezone.
ET = ZoneInfo("America/New_York")

# Preference keys. `notify_`-prefixed so `user_settings_service.key_is_syncable`
# accepts them with no backend change (that prefix is the forward-compat contract).
PREF_QUIET_ENABLED = "notify_quiet_hours_enabled"
PREF_QUIET_START = "notify_quiet_start"
PREF_QUIET_END = "notify_quiet_end"
PREF_TIMEZONE = "notify_timezone"

DEFAULT_QUIET_START = "22:00"
DEFAULT_QUIET_END = "07:00"


@dataclass(frozen=True)
class QuietWindow:
    """A resolved, validated quiet-hours window. `enabled=False` means "never quiet"."""

    enabled: bool
    start: Optional[time]
    end: Optional[time]


def parse_hhmm(raw: Any) -> Optional[time]:
    """Parse a 24-hour ``"HH:MM"`` string, or ``None`` if it is not one.

    Strict on purpose. The only writer is the iOS picker, which always emits
    zero-padded ``HH:MM``, so anything else is corruption, a hand-edited row, or a
    future client bug — and the failure mode of guessing is silence at the wrong hour.
    A bare ``"7"`` is rejected rather than read as 07:00: it is equally readable as
    "7 minutes past midnight" and neither reading is safe to assume.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if ":" not in text:
        return None
    hh, _, mm = text.partition(":")
    try:
        hour, minute = int(hh), int(mm)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def resolve_timezone(preferences: Optional[Mapping[str, Any]]) -> ZoneInfo:
    """The user's timezone, falling back to ET.

    Fails to **ET**, not to "never quiet" and not to "always quiet". An unparseable
    timezone string must not silence someone forever (fail-closed) nor buzz them at 3am
    (fail-open); ET is the app's existing convention and the honest degradation, and it
    is what every user already got before this feature existed.
    """
    raw = (preferences or {}).get(PREF_TIMEZONE)
    if isinstance(raw, str) and raw.strip():
        try:
            return ZoneInfo(raw.strip())
        except Exception as e:
            # ZoneInfoNotFoundError, and anything else a malformed key can raise.
            logger.warning(
                "quiet_hours: unusable timezone %r (%s: %s) — falling back to ET",
                raw, type(e).__name__, e,
            )
    return ET


def resolve_window(preferences: Optional[Mapping[str, Any]]) -> QuietWindow:
    """Read the quiet-hours window out of a preferences blob.

    Disabled (i.e. never quiet) when: the master toggle is off, either time is
    unparseable, or ``start == end``.

    ``start == end`` is the interesting one. Read literally as a half-open interval it
    is a ZERO-length window, but read as "from 22:00 until 22:00" it is a window that
    covers the entire day — and a user who fat-fingers both pickers to the same value
    would silence the app forever with no error and no way to tell. Treat it as
    disabled and LOG it, so the degradation is visible rather than mysterious.
    """
    prefs = preferences or {}

    enabled_raw = prefs.get(PREF_QUIET_ENABLED)
    # Type the read; do not truthiness-cast. `bool("false")` is True — the same trap
    # `PushDispatchService.preference_enabled` documents, and nothing between here and
    # the database enforces the value's type.
    if isinstance(enabled_raw, bool):
        enabled = enabled_raw
    elif isinstance(enabled_raw, str):
        enabled = enabled_raw.strip().lower() not in {"false", "0", "no", "off", ""}
    elif isinstance(enabled_raw, (int, float)):
        enabled = enabled_raw != 0
    else:
        enabled = False  # absent or unreadable → quiet hours OFF (the shipped default)

    if not enabled:
        return QuietWindow(enabled=False, start=None, end=None)

    start = parse_hhmm(prefs.get(PREF_QUIET_START)) or parse_hhmm(DEFAULT_QUIET_START)
    end = parse_hhmm(prefs.get(PREF_QUIET_END)) or parse_hhmm(DEFAULT_QUIET_END)

    if start is None or end is None:  # unreachable given the defaults, but explicit
        return QuietWindow(enabled=False, start=None, end=None)

    if start == end:
        logger.warning(
            "quiet_hours: start == end (%s) — treating quiet hours as DISABLED rather "
            "than silencing every notification for this user",
            start.isoformat(timespec="minutes"),
        )
        return QuietWindow(enabled=False, start=None, end=None)

    return QuietWindow(enabled=True, start=start, end=end)


def is_within(window: QuietWindow, now_local: datetime) -> bool:
    """Is this local wall-clock instant inside the window?

    Half-open ``[start, end)`` so the boundary belongs to exactly one side and an
    alert landing precisely at the end time goes out immediately.

    Wraparound is the normal case: 22:00 → 07:00 spans midnight, so the window is
    "at or after start, OR before end". A non-wrapping window (13:00 → 14:00) is the
    simple containment. Getting this backwards inverts quiet hours — the app would go
    silent for the 15 hours the user is awake — so both branches are tested.
    """
    if not window.enabled or window.start is None or window.end is None:
        return False
    t = now_local.timetz().replace(tzinfo=None)
    if window.start < window.end:
        return window.start <= t < window.end
    return t >= window.start or t < window.end


def next_end_utc(window: QuietWindow, now: datetime, tz: ZoneInfo) -> Optional[datetime]:
    """The UTC instant when the current quiet period ends, or ``None`` if not quiet.

    ⚠️ DATE ARITHMETIC, NOT TIMEDELTA ARITHMETIC. Adding ``timedelta(days=1)`` to an
    aware datetime advances 24 absolute hours, which on a DST boundary lands on 06:00 or
    08:00 local rather than the 07:00 the user asked for. Building the next calendar
    date and re-attaching the zone keeps the WALL CLOCK promise, which is what a user
    setting "07:00" means.

    DST corner cases, both resolved by ZoneInfo's ``fold=0`` default and both asserted
    in the tests rather than left to chance:
      * **Spring forward** — 02:30 does not exist in America/New_York on that date.
        ZoneInfo resolves it with the pre-transition offset, so the instant lands at
        03:30 local. The notification goes out an hour "late" in wall-clock terms,
        which is the safe direction (later, never earlier than the user's quiet end).
      * **Fall back** — 01:30 happens twice. ``fold=0`` picks the FIRST occurrence, so
        the wake is the earlier of the two. It fires once; the row is claimed and moves
        to `sent`, so the second occurrence has nothing to re-deliver.
    """
    if not window.enabled or window.end is None:
        return None

    now_local = now.astimezone(tz)
    if not is_within(window, now_local):
        return None

    candidate_date: date = now_local.date()
    candidate = datetime.combine(candidate_date, window.end).replace(tzinfo=tz)
    if candidate <= now_local:
        candidate = datetime.combine(
            candidate_date + timedelta(days=1), window.end
        ).replace(tzinfo=tz)

    return candidate.astimezone(timezone.utc)


def local_day_start_utc(now: datetime, tz: ZoneInfo) -> datetime:
    """Midnight at the start of the user's current local day, as a UTC instant.

    This is the per-category daily-cap boundary. Built from the local calendar date
    rather than by subtracting hours, for the same DST reason as ``next_end_utc``.

    A user who changes timezone mid-day can land on a boundary that grants a partial
    second budget. That is accepted and bounded by the cap itself — the alternative is
    storing a per-user "budget day" that a timezone change would then have to migrate,
    which is a lot of machinery to prevent at most a handful of extra notifications on
    the one day someone flies.
    """
    now_local = now.astimezone(tz)
    return datetime.combine(now_local.date(), time(0, 0)).replace(tzinfo=tz).astimezone(
        timezone.utc
    )
