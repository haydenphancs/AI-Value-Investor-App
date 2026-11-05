"""One wire shape for a stored timestamp that iOS SORTS on.

`iso_utc_millis` turns a Supabase `timestamptz` value into ``"YYYY-MM-DDTHH:MM:SS.mmmZ"``:
UTC, exactly three fractional digits. Exactly three because both ISO-8601 parsers the app
ships use `ISO8601DateFormatter` with `.withFractionalSeconds`, which accepts three and
nothing else (see `money_moves_content_service._iso_published_at`), while PostgREST renders
`timestamptz` with up to SIX digits and trims trailing zeros — ``".12345+00:00"`` is a real
value. Milliseconds rather than seconds because the value is a SORT KEY: a bulk add writes
several rows inside one second, and second precision would tie them.

Why not `market_hours.to_utc_instant`: that helper reads a NAIVE value as America/New_York
wall-clock time, which is right for FMP and wrong here. A naive value here can only be one of
our own writes, and those are UTC.

Never raises. A value it cannot read becomes None (the client sorts it as unknown, last) plus
one WARNING naming the ticker, so a malformed cell costs one sort key, never the row or the
response it sits in.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Longest repr a warning quotes. A malformed cell can be anything, including a huge string.
_REPR_MAX = 80


def _clip(text: str) -> str:
    return text if len(text) <= _REPR_MAX else text[: _REPR_MAX - 3] + "..."


def _short_repr(value: Any) -> str:
    try:
        return _clip(repr(value))
    except Exception as exc:  # noqa: BLE001 — a hostile __repr__ must not break the log line
        return f"<unrepresentable: {type(exc).__name__}>"


def _parse(value: Any) -> Optional[datetime]:
    """The aware UTC datetime `value` names, or None when it names none."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # RFC 3339 allows a lowercase "z"; Python 3.11's fromisoformat accepts only "Z".
        if s.endswith("z"):
            s = s[:-1] + "+00:00"
        # Python 3.11 reads "Z", any "±HH:MM" / "±HH" offset, a space separator and 1-6
        # fractional digits (more are truncated to microseconds). A DATE-ONLY string
        # ("2026-10-31") parses as midnight: kept, deliberately, as midnight UTC — it is a
        # real calendar day, and a day-precise sort key beats an unknown one. Postgres never
        # renders `timestamptz` that way, so it only appears from a hand-written value.
        dt = datetime.fromisoformat(s)
    else:
        # int / float / bool (bool is an int subclass) / dict / anything else: an epoch
        # number would have to guess its unit, so no non-string is read as a time.
        return None
    if dt.utcoffset() is None:
        # NAIVE (or a tzinfo that answers no offset): our own writes, which are UTC.
        # `astimezone` on a naive value would read it as the SERVER's local time instead.
        dt = dt.replace(tzinfo=timezone.utc)
    # OverflowError: "0001-01-01T00:00:00+01:00" parses, but moving it to UTC leaves the
    # datetime range. The caller catches it with ValueError.
    return dt.astimezone(timezone.utc)


def iso_utc_millis(value: Any, *, ticker: str) -> Optional[str]:
    """``value`` as ``"YYYY-MM-DDTHH:MM:SS.mmmZ"`` (UTC, milliseconds TRUNCATED, never
    rounded — rounding .9995 up could carry into the next second, minute or day), else None.

    Accepts an ISO-8601 string or a `datetime`; a naive one is read as UTC. ``None`` is a
    plain absence and returns None silently. Anything else — an empty or whitespace string,
    garbage text, an int, a bool, an out-of-range instant — returns None with one WARNING
    naming ``ticker``, the value's type and a truncated repr. Never raises.
    """
    if value is None:
        return None
    try:
        dt = _parse(value)
    except (ValueError, OverflowError, TypeError) as exc:
        # The exception text is clipped too: fromisoformat's ValueError quotes the WHOLE
        # input, so an unclipped `exc` put a 100 kB cell into the log line anyway.
        logger.warning(
            "[added_at] %s: unreadable timestamp (%s %s: %s: %s) — sent as null, sorted "
            "as unknown",
            ticker, type(value).__name__, _short_repr(value), type(exc).__name__,
            _clip(str(exc)),
        )
        return None
    except Exception as exc:  # noqa: BLE001 — "never raises" is the contract
        # A datetime subclass with a hostile utcoffset() and the like: not a value Supabase
        # sends, so an unexpected failure — still degraded to one unknown key, with a stack.
        logger.warning(
            "[added_at] %s: unexpected %s reading a timestamp (%s %s) — sent as null",
            ticker, type(exc).__name__, type(value).__name__, _short_repr(value),
            exc_info=True,
        )
        return None
    if dt is None:
        logger.warning(
            "[added_at] %s: not a timestamp (%s %s) — sent as null, sorted as unknown",
            ticker, type(value).__name__, _short_repr(value),
        )
        return None
    # Built by hand, not with strftime: `%Y` is not zero-padded below year 1000 on glibc,
    # and iOS's formatter needs exactly four year digits.
    return (
        f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}T"
        f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}.{dt.microsecond // 1000:03d}Z"
    )
