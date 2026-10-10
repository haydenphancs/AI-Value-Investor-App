"""The Insights prompt's sense of time: the Now line, ET article stamps, EARNINGS line.

TestFlight ORCL, Thu 2026-09-10: the card said "Oracle is set to report" hours after
the 16:10 ET release. The prompt did not say what day it was, article stamps were bare
UTC `YYYY-MM-DDTHH:MM`, and nothing said whether the report had happened. Every clock
here is ET, built by hand (no `%-d`), and every instant is injected.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.earnings_window_service import EarningsStatus
from app.services.news_insight_service import (
    NewsInsightService,
    _article_stamp,
    _earnings_line,
    _now_line,
)

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# ── Now line ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("now,expected", [
    (_et(2026, 9, 10, 11, 5), "Now: Thu Sep 10 2026, 11:05 ET (US regular session open)."),
    (_et(2026, 9, 10, 7, 0), "Now: Thu Sep 10 2026, 07:00 ET (US pre-market; the regular session opens at 09:30 ET)."),
    (_et(2026, 9, 10, 17, 2), "Now: Thu Sep 10 2026, 17:02 ET (US after-hours; the regular session has closed)."),
    (_et(2026, 9, 12, 12, 0), "Now: Sat Sep 12 2026, 12:00 ET (US market closed for the weekend or a holiday)."),
    (_et(2026, 11, 26, 12, 0), "Now: Thu Nov 26 2026, 12:00 ET (US market closed for the weekend or a holiday)."),
    (_et(2026, 11, 27, 14, 0), "Now: Fri Nov 27 2026, 14:00 ET (US market closed)."),   # half-day
    (_et(2026, 9, 10, 21, 30), "Now: Thu Sep 10 2026, 21:30 ET (US market closed)."),
])
def test_now_line_labels_the_us_session(now, expected):
    assert _now_line("ORCL", now) == expected


def test_market_scope_gets_the_session_label():
    assert "(US regular session open)" in _now_line("__MARKET__", _et(2026, 9, 10, 11, 5))


def test_a_coin_gets_no_session_label():
    assert _now_line("ETHUSD", _et(2026, 9, 12, 12, 0)) == "Now: Sat Sep 12 2026, 12:00 ET."


def test_utc_late_evening_is_still_the_et_day():
    # 02:30Z on the 11th is 22:30 ET on the 10th.
    line = _now_line("ORCL", datetime(2026, 9, 11, 2, 30, tzinfo=timezone.utc))
    assert line.startswith("Now: Thu Sep 10 2026, 22:30 ET")


def test_a_naive_now_is_utc():
    assert _now_line("ORCL", datetime(2026, 9, 10, 15, 5)).startswith("Now: Thu Sep 10 2026, 11:05 ET")


@pytest.mark.parametrize("now,clock", [
    (datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc), "01:30 ET"),     # before spring-forward
    (datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc), "03:30 ET"),     # after it
    (datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), "01:30 ET"),    # first 01:30 (EDT)
    (datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), "01:30 ET"),    # second 01:30 (EST)
])
def test_now_line_across_dst(now, clock):
    assert clock in _now_line("ORCL", now)


# ── article stamps ───────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc)   # 17:00 ET


@pytest.mark.parametrize("published,expected", [
    ("2026-09-10T14:04:00+00:00", "Thu Sep 10 10:04 ET · 6h before Now"),
    ("2026-09-10T20:10:00Z", "Thu Sep 10 16:10 ET · 50m before Now"),
    ("2026-09-10T20:59:30+00:00", "Thu Sep 10 16:59 ET · just published"),
    ("2026-09-07T13:00:00+00:00", "Mon Sep 7 09:00 ET · 3d before Now"),
    ("2026-09-10T14:04", "Thu Sep 10 10:04 ET · 6h before Now"),           # naive = UTC
    (datetime(2026, 9, 10, 14, 4, tzinfo=timezone.utc), "Thu Sep 10 10:04 ET · 6h before Now"),
    ("2026-09-10T21:30:00+00:00", "Thu Sep 10 17:30 ET · just published"),  # future skew
    ("garbage", "time unknown"),
    (None, "time unknown"),
    ("", "time unknown"),
])
def test_article_stamp(published, expected):
    assert _article_stamp(published, NOW) == expected


def test_dst_fall_back_stamps_share_a_clock_but_not_an_age():
    now = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    first = _article_stamp("2026-11-01T05:30:00+00:00", now)
    second = _article_stamp("2026-11-01T06:30:00+00:00", now)
    assert "01:30 ET" in first and "01:30 ET" in second
    assert "2h before Now" in first and "1h before Now" in second


# ── EARNINGS line ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,needle", [
    (EarningsStatus("reported", date(2026, 9, 10)), "reported quarterly results on Thu Sep 10"),
    (EarningsStatus("due_today", date(2026, 9, 10)), "scheduled to report quarterly results on Thu Sep 10, the same date as Now"),
    (EarningsStatus("upcoming", date(2026, 9, 15)), "next scheduled to report quarterly results on Tue Sep 15"),
])
def test_earnings_line_per_status(status, needle):
    line = _earnings_line("ORCL", "ORCL", status)
    assert line.startswith("EARNINGS: ")
    assert needle in line
    # TIMING ONLY — never a figure (the owner's decision, 2026-09-27).
    for figure in ("$", "%", "EPS of", "est."):
        assert figure not in line


@pytest.mark.parametrize("scope,status", [
    ("__MARKET__", EarningsStatus("reported", date(2026, 9, 10))),
    ("ETHUSD", EarningsStatus("reported", date(2026, 9, 10))),
    ("ORCL", None),
    ("ORCL", "reported"),
    ("ORCL", EarningsStatus("late", date(2026, 9, 9))),
])
def test_no_earnings_line_when_it_does_not_apply(scope, status):
    assert _earnings_line(scope, scope, status) == ""


# ── the prompt as a whole ─────────────────────────────────────────────────────

@pytest.fixture
def svc():
    return object.__new__(NewsInsightService)


def test_the_prompt_carries_now_and_earnings_and_the_rules(svc):
    prompt = svc._build_prompt(
        "ORCL", [{"headline": "A", "published_at": "2026-09-10T14:04:00+00:00"}],
        "x", None, None, now=NOW,
        earnings=EarningsStatus("reported", date(2026, 9, 10)),
    )
    assert "Now: Thu Sep 10 2026, 17:00 ET" in prompt
    assert "EARNINGS: ORCL reported quarterly results on Thu Sep 10" in prompt
    assert "- EARNINGS. The EARNINGS line comes from an earnings calendar" in prompt
    assert "- TIME. This brief will be read hours or days after the Now line." in prompt
    assert "Japan-listed" in prompt
    assert '"conclusion": ONE sentence' in prompt


def test_no_earnings_rule_without_an_earnings_line(svc):
    prompt = svc._build_prompt("ORCL", [{"headline": "A"}], "x", None, None, now=NOW)
    assert "EARNINGS:" not in prompt
    assert "- EARNINGS." not in prompt


def test_the_should_care_wording_is_gone_from_both_prompts(svc):
    prompt = svc._build_prompt("ORCL", [{"headline": "A"}], "x", None, None, now=NOW)
    assert "should care" not in prompt.replace("Investors should care because", "")
    import inspect
    from app.services import news_cache_service
    src = inspect.getsource(news_cache_service._batch_enrich_articles) if hasattr(
        news_cache_service, "_batch_enrich_articles") else inspect.getsource(news_cache_service)
    assert "everyday investor should care" not in src


def test_prompts_a_minute_apart_differ(svc):
    a = svc._build_prompt("ORCL", [{"headline": "A"}], "x", None, None, now=NOW)
    b = svc._build_prompt(
        "ORCL", [{"headline": "A"}], "x", None, None,
        now=datetime(2026, 9, 10, 21, 1, tzinfo=timezone.utc),
    )
    assert a != b


def test_price_line_names_the_latest_regular_session(svc):
    """The market card's line (since v8 a ticker prompt carries no price line at all)."""
    prompt = svc._build_prompt(
        "__MARKET__", [{"headline": "A"}], "x", "notable", {"changePercentage": -5.2}, now=NOW,
    )
    assert "in the latest regular session" in prompt
    assert "in the current session" not in prompt
    ticker = svc._build_prompt(
        "ORCL", [{"headline": "A"}], "x", "notable", {"changePercentage": -5.2}, now=NOW,
    )
    assert "in the latest regular session" not in ticker


def test_the_points_count_is_the_schemas_max(svc):
    from app.services.news_insight_service import _INSIGHT_SCHEMA, MAX_POINTS

    prompt = svc._build_prompt("ORCL", [{"headline": "A"}], "x", None, None, now=NOW)
    # Owner, 2026-10-09: "ideally to have 2 - 3 bullet points. unless there are important so we
    # can have 4" — "1 to 4 points … cover the distinct threads" read as "fill all four".
    assert '"points": usually 2 or 3 points' in prompt
    assert f"Write a 4th (never more than {MAX_POINTS}) ONLY when" in prompt
    assert "1 to 4 points" not in prompt
    assert _INSIGHT_SCHEMA["properties"]["points"]["maxItems"] == MAX_POINTS == 4
