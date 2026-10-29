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
