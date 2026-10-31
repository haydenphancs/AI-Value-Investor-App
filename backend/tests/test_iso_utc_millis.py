"""`utils.iso_dates.iso_utc_millis` — the one wire shape for the "Date Added" sort key.

The Tracking feed and the Updates tabs send `watchlist_items.added_at` through it. Two
things make it easy to get quietly wrong:

  * iOS's `ISO8601DateFormatter` with `.withFractionalSeconds` reads EXACTLY three
    fractional digits, while PostgREST renders `timestamptz` with up to six and trims
    trailing zeros (".12345+00:00" is a real value). A wrong digit count decodes to nil and
    every row sorts as "unknown" — no crash, no log, just a sort that does nothing.
  * The value is a SORT KEY, so a wrong offset or a rounded-up millisecond reorders rows.

It must never raise: one malformed cell may cost one sort key, never the row.

No network.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest

import app.utils.iso_dates as iso_dates
from app.utils.iso_dates import iso_utc_millis

_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


@pytest.fixture(autouse=True)
def _non_utc_local_zone(monkeypatch):
    """Run every case with the process's LOCAL zone far from UTC. On a UTC host, reading a
    naive value as local time and reading it as UTC agree, so the naive-is-UTC cases would
    pass against the bug they exist to catch."""
    if not hasattr(time, "tzset"):
        yield
        return
    monkeypatch.setenv("TZ", "America/Denver")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _warnings(caplog):
    return [r for r in caplog.records
            if r.name == iso_dates.logger.name and r.levelno == logging.WARNING]


# ── strings PostgREST (and hand-written rows) actually produce ──────────────

@pytest.mark.parametrize("raw, expected", [
    # "Z"
    ("2026-10-31T12:34:56Z", "2026-10-31T12:34:56.000Z"),
    # RFC 3339 also allows a lowercase z; Python 3.11's fromisoformat does not.
    ("2026-10-31T12:34:56z", "2026-10-31T12:34:56.000Z"),
    # +00:00 with six digits — TRUNCATED to three, never rounded.
    ("2026-10-31T12:34:56.123456+00:00", "2026-10-31T12:34:56.123Z"),
    ("2026-10-31T12:34:56.999999+00:00", "2026-10-31T12:34:56.999Z"),
    # Postgres trims trailing zeros, so 5- and 1-digit fractions are real values.
    ("2026-10-31T12:34:56.12345+00:00", "2026-10-31T12:34:56.123Z"),
    ("2026-10-31T12:34:56.1+00:00", "2026-10-31T12:34:56.100Z"),
    ("2026-10-31T12:34:56.05+00:00", "2026-10-31T12:34:56.050Z"),
    # No fraction at all.
    ("2026-10-31T12:34:56+00:00", "2026-10-31T12:34:56.000Z"),
    # Another offset is CONVERTED to UTC — here across midnight and a month end.
    ("2026-10-31T23:30:00.5-05:00", "2026-11-01T04:30:00.500Z"),
    ("2026-11-01T03:00:00+05:30", "2026-10-31T21:30:00.000Z"),
    # Postgres' own text form: a space separator and an hour-only offset.
    ("2026-10-31 12:34:56.5+00", "2026-10-31T12:34:56.500Z"),
    # Seven digits: Python truncates to microseconds, we to milliseconds.
    ("2026-10-31T12:34:56.1234567+00:00", "2026-10-31T12:34:56.123Z"),
    # Surrounding whitespace is not a reason to lose the date.
    ("  2026-10-31T12:34:56Z\n", "2026-10-31T12:34:56.000Z"),
    # NAIVE: our own writes, read as UTC — never as the server's local time.
    ("2026-10-31T12:34:56.789", "2026-10-31T12:34:56.789Z"),
    # DATE-ONLY: documented choice — midnight UTC (a real day beats an unknown key).
    ("2026-10-31", "2026-10-31T00:00:00.000Z"),
    # A year below 1000 keeps four digits (strftime's %Y does not on glibc).
    ("0999-01-01T00:00:00Z", "0999-01-01T00:00:00.000Z"),
])
def test_strings_normalise_to_utc_milliseconds(raw, expected, caplog):
    with caplog.at_level(logging.WARNING, logger=iso_dates.logger.name):
        out = iso_utc_millis(raw, ticker="AAPL")
    assert out == expected
    assert _SHAPE.match(out)
    assert not _warnings(caplog), "a readable value must not warn"


def test_truncation_never_carries_into_the_next_day():
    """Rounding .9995 up would print 2027-01-01 for a 2026 row."""
    assert iso_utc_millis("2026-12-31T23:59:59.999999Z", ticker="X") == (
        "2026-12-31T23:59:59.999Z"
    )


# ── datetimes ───────────────────────────────────────────────────────────────

def test_an_aware_datetime_is_converted_to_utc():
    et = ZoneInfo("America/New_York")
    # 09:30 EDT is 13:30 UTC; 09:30 EST (after 2026-11-01) is 14:30 UTC.
    assert iso_utc_millis(datetime(2026, 10, 30, 9, 30, 0, 456789, tzinfo=et), ticker="X") == (
        "2026-10-30T13:30:00.456Z"
    )
    assert iso_utc_millis(datetime(2026, 11, 2, 9, 30, tzinfo=et), ticker="X") == (
        "2026-11-02T14:30:00.000Z"
    )
    fixed = timezone(timedelta(hours=-7))
    assert iso_utc_millis(datetime(2026, 10, 31, 20, 0, tzinfo=fixed), ticker="X") == (
        "2026-11-01T03:00:00.000Z"
    )


def test_a_naive_datetime_is_read_as_utc():
    assert iso_utc_millis(datetime(2026, 10, 31, 12, 0, 0, 1999), ticker="X") == (
        "2026-10-31T12:00:00.001Z"
    )


def test_a_tzinfo_with_no_offset_is_treated_as_naive_not_as_local_time():
    """`astimezone` reads a value whose utcoffset() is None as the SERVER's local time."""
    class _NoOffset(tzinfo):
        def utcoffset(self, dt): return None
        def dst(self, dt): return None
        def tzname(self, dt): return None

    value = datetime(2026, 10, 31, 12, 0, tzinfo=_NoOffset())
    assert iso_utc_millis(value, ticker="X") == "2026-10-31T12:00:00.000Z"


# ── absent vs unreadable ────────────────────────────────────────────────────

def test_none_is_a_plain_absence_and_never_warns(caplog):
    with caplog.at_level(logging.DEBUG, logger=iso_dates.logger.name):
        assert iso_utc_millis(None, ticker="AAPL") is None
    assert not [r for r in caplog.records if r.name == iso_dates.logger.name]


@pytest.mark.parametrize("bad", [
    1_700_000_000,          # an epoch number would have to guess its unit
    1_700_000_000_000,
    1.5,
    True, False,            # bool is an int subclass
    "not a date",
    "",
    "   ",
    "2026-10-31T24:00:00",  # hour out of range
    "2026-02-30",           # no such day
    "nan",
    {"added_at": "2026-10-31"},
    ["2026-10-31"],
    b"2026-10-31T12:00:00Z",
    "0001-01-01T00:00:00+01:00",    # parses, but UTC is out of range (OverflowError)
    "9999-12-31T23:59:59-01:00",
])
def test_anything_unreadable_is_none_with_one_warning_naming_the_ticker(bad, caplog):
    with caplog.at_level(logging.WARNING, logger=iso_dates.logger.name):
        assert iso_utc_millis(bad, ticker="ORCL") is None
    records = _warnings(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    msg = records[0].getMessage()
    assert "ORCL" in msg
    assert type(bad).__name__ in msg


def test_the_warning_quotes_a_bounded_repr(caplog):
    """A malformed cell can be a megabyte of text; the log line must not be."""
    with caplog.at_level(logging.WARNING, logger=iso_dates.logger.name):
        assert iso_utc_millis("x" * 100_000, ticker="PLUG") is None
    records = _warnings(caplog)
    assert len(records) == 1
    msg = records[0].getMessage()
    assert "PLUG" in msg and len(msg) < 1_000, len(msg)


def test_a_hostile_datetime_never_raises(caplog):
    """'Never raises' is the contract: an unexpected failure inside a datetime subclass is
    one unknown key and a warning with a stack, not a failed feed row."""
    class _Boom(datetime):
        def utcoffset(self):
            raise RuntimeError("tz database exploded")

        def __repr__(self):
            raise RuntimeError("repr exploded too")

    value = _Boom(2026, 10, 31, 12, 0)
    with caplog.at_level(logging.WARNING, logger=iso_dates.logger.name):
        assert iso_utc_millis(value, ticker="NVDA") is None
    records = _warnings(caplog)
    assert len(records) == 1
    assert "NVDA" in records[0].getMessage()
    assert records[0].exc_info is not None, "an unexpected failure must carry its stack"


# ── the output is a usable sort key ─────────────────────────────────────────

def test_output_strings_sort_in_chronological_order_across_offsets():
    """iOS parses the value, but the property that matters is the ORDER: mixed offsets and
    fraction lengths must land in true chronological order."""
    raws = [
        "2026-10-31T12:00:00.5+00:00",
        "2026-10-31T07:00:00.4-05:00",      # 12:00:00.4 UTC
        "2026-10-31T12:00:00Z",
        "2026-10-31T17:30:00.45+05:30",     # 12:00:00.45 UTC
        "2026-10-30T23:59:59.999999Z",
    ]
    outs = [iso_utc_millis(r, ticker="X") for r in raws]
    instants = [datetime.fromisoformat(o.replace("Z", "+00:00")) for o in outs]
    assert sorted(outs) == [o for _, o in sorted(zip(instants, outs))]
    assert len(set(outs)) == len(outs), "distinct milliseconds must not tie"


def test_two_rows_inside_one_second_do_not_tie():
    """A bulk add writes several rows within one second; second precision would tie them
    and the 'Date Added' order would fall back to the ticker."""
    a = iso_utc_millis("2026-10-31T12:00:00.101+00:00", ticker="A")
    b = iso_utc_millis("2026-10-31T12:00:00.102+00:00", ticker="B")
    assert a < b
