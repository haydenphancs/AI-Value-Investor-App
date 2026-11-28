"""The shared web-result hygiene module (moved out of the chat web search, 2026-10-28).

Chat and the Updates jump search must apply ONE host policy and ONE URL rule, and the chat
names must keep pointing at the moved objects (`endpoints/chat.py` and the chat tests read
them from `chat_web_search_service`). Plus the page-date parsers the jump search uses to keep
only pages from the last 24 hours.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import chat_web_search_service as cws
from app.services import web_result_hygiene as wrh
from app.services.web_result_hygiene import parse_page_age, parse_relative_age


@pytest.mark.parametrize("name", [
    "_safe_https_url", "_host_denied", "_publisher_for_host", "_clean_text", "_cap",
    "_iso_date", "_WS_RE", "_INJECTION_RE",
])
def test_chat_reads_the_very_same_objects(name):
    assert getattr(cws, name) is getattr(wrh, name)


def test_the_anchored_host_shape_pattern_stays_out_of_the_chat_module():
    # Re-exporting it would put a pattern that is only safe anchored back into the chat
    # module's swept namespace without its exemption.
    assert not hasattr(cws, "_HOST_SHAPE_RE")


@pytest.mark.parametrize("url,ok", [
    ("https://www.reuters.com/business/oracle-2026-10-28/", True),
    ("http://www.reuters.com/x", False),                  # https only
    ("https://user@reuters.com/x", False),                # credentials
    ("https://127.0.0.1/x", False),                       # IP literal
    ("https://reuters.com:8443/x", False),                # port
    ("https://intranet.local/x", False),
    ("https://finance.yahoo.com/quote/ORCL/", True),       # safe — the HOST policy refuses it
])
def test_safe_https_url(url, ok):
    assert (wrh._safe_https_url(url) is not None) is ok


@pytest.mark.parametrize("url,denied", [
    ("https://finance.yahoo.com/quote/ORCL/", True),
    ("https://www.reddit.com/r/stocks/", True),
    ("https://stockanalysis.com/stocks/orcl/", True),
    ("https://www.cnbc.com/2026/10/28/oracle-forecast-growth-2030.html", False),
])
def test_the_host_policy(url, denied):
    _, host, path = wrh._safe_https_url(url)
    assert wrh._host_denied(host, path) is denied


def test_publisher_names_come_from_the_host():
    assert wrh._publisher_for_host("www.cnbc.com") == "CNBC"
    assert wrh._publisher_for_host("news.example.org") == "news.example.org"


# ── page dates ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("2026-10-28T18:21:00", datetime(2026, 10, 28, 18, 21, tzinfo=timezone.utc)),
    ("2026-10-28T18:21:00Z", datetime(2026, 10, 28, 18, 21, tzinfo=timezone.utc)),
    ("2026-10-28T14:21:00-04:00", datetime(2026, 10, 28, 18, 21, tzinfo=timezone.utc)),
    ("2026-10-28T00:00:00+00:00", datetime(2026, 10, 28, tzinfo=timezone.utc)),  # stated offset
])
def test_parse_page_age_reads_an_instant(raw, expected):
    assert parse_page_age(raw) == expected


@pytest.mark.parametrize("raw", [
    "2026-10-28",                # a day, not an instant
    "2026-10-28T00:00:00",       # naive midnight: the way pages state only their day
    "2 hours ago", "", "   ", None, 1730140860, "2026-13-40T10:00:00",
    "1899-01-01T10:00:00", "9999-01-01T10:00:00", "x" * 41,
    # An offset at the edge of the calendar overflows the conversion (review 2026-10-29).
    "0001-01-01T00:30:00+01:00", "9999-12-31T23:30:00-01:00",
])
def test_parse_page_age_refuses_anything_else(raw):
    assert parse_page_age(raw) is None


NOW = datetime(2026, 10, 28, 19, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw,delta", [
    ("35 minutes ago", timedelta(minutes=35)),
    ("1 min ago", timedelta(minutes=1)),
    ("an hour ago", timedelta(hours=1)),
    ("2 hours ago", timedelta(hours=2)),
    ("3 hrs ago", timedelta(hours=3)),
    ("  4 Hours Ago ", timedelta(hours=4)),
])
def test_parse_relative_age(raw, delta):
    assert parse_relative_age(raw, NOW) == NOW - delta


@pytest.mark.parametrize("raw", [
    "October 28, 2026", "2 weeks ago", "yesterday", "", None, "1234 hours ago",
    "2 hours ago " + "x" * 40,
    # A day count is not an instant: Brave says "1 day ago" for anything 24-47 h old, and read as
    # exactly 24 h it passed the jump search's 24 h window (review 2026-10-29).
    "1 day ago", "a day ago", "one day ago", "2 days ago",
])
def test_parse_relative_age_refuses_anything_else(raw):
    assert parse_relative_age(raw, NOW) is None


def test_known_publishers_are_the_named_hosts():
    assert wrh._known_publisher("www.cnbc.com") and wrh._known_publisher("cnbc.com")
    assert not wrh._known_publisher("promo-signals.example")


# ── the jump search's fallback provider (2026-11-27) ─────────────────────

def test_the_denied_hosts_are_public_and_the_same_object():
    # The fallback provider passes them as its blocked-domain list: ONE host policy.
    assert wrh.DENIED_HOSTS is wrh._DENIED_HOSTS
    assert {"reddit.com", "x.com", "google.com", "robinhood.com"} <= wrh.DENIED_HOSTS
    assert all("/" not in h and h == h.lower() for h in wrh.DENIED_HOSTS)


# 14:00 ET, Wed 2026-11-25 — and 00:00 ET that day is 05:00 UTC.
FB_NOW = datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc)
ET_MIDNIGHT = datetime(2026, 11, 25, 5, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw,expected", [
    ("2026-11-25T16:10:00Z", (datetime(2026, 11, 25, 16, 10, tzinfo=timezone.utc), True)),
    ("2026-11-25T11:10:00-05:00", (datetime(2026, 11, 25, 16, 10, tzinfo=timezone.utc), True)),
    ("2 hours ago", (FB_NOW - timedelta(hours=2), True)),
    ("an hour ago", (FB_NOW - timedelta(hours=1), True)),
    # Today's ET date with no time: 00:00 ET, NOT precise (the card must print no time).
    ("2026-11-25", (ET_MIDNIGHT, False)),
    ("2026-11-25T00:00:00", (ET_MIDNIGHT, False)),
    ("November 25, 2026", (ET_MIDNIGHT, False)),
    ("Nov 25, 2026", (ET_MIDNIGHT, False)),
    ("  November 25 2026 ", (ET_MIDNIGHT, False)),
])
def test_parse_fallback_page_age_reads_instants_and_todays_date(raw, expected):
    assert wrh.parse_fallback_page_age(raw, FB_NOW) == expected


@pytest.mark.parametrize("raw", [
    "2026-11-24", "November 24, 2026", "Nov 26, 2026",   # another day
    "1 day ago", "2 days ago", "yesterday", "last week",
    "", "   ", None, 20261125, "x" * 41, "November 25, 2026 at 10:30 AM",
    "2026-02-30", "1899-01-01",
])
def test_parse_fallback_page_age_refuses_anything_else(raw):
    assert wrh.parse_fallback_page_age(raw, FB_NOW) == (None, False)


def test_todays_date_is_the_et_date_not_the_utc_date():
    # 23:30 ET on the 25th is already the 26th in UTC: the 25th is still "today".
    late = datetime(2026, 11, 26, 4, 30, tzinfo=timezone.utc)
    assert wrh.parse_fallback_page_age("November 25, 2026", late) == (ET_MIDNIGHT, False)
    assert wrh.parse_fallback_page_age("November 26, 2026", late) == (None, False)
    # A naive `now` is read as UTC, never as the machine's local time.
    assert wrh.parse_fallback_page_age("2026-11-25", FB_NOW.replace(tzinfo=None)) == (
        ET_MIDNIGHT, False)
