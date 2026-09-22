"""The Sentiment card's "Last 24H" and "Last 7D" showed the SAME News Sentiment for coins.

Developer (2026-09-21): *"i toggle Last 24H and Last 7D but nothing is change."* Live: the ETH
response carried identical 24h and 7d article counts; `ticker_news_cache` held 58 ETHUSD rows
whose oldest was 16.8 h (BTCUSD 87 rows, 10.8 h) while AAPL held 250 rows over 183 h.

Two halves, both in `sentiment_service`:
1. `_load_from_db` treated the table as a HIT whenever `cached_at` was fresh — and the News
   tab / Updates sweeper keep it fresh with their own 50-row page, pruned after 6 h — so the
   sentiment service never fetched its own 14-day set. "Fresh" was conflated with "covers
   the window".
2. `_fetch_news` for crypto asked FMP for `limit=1000` with NO date window and one page;
   FMP caps a page at 250 rows, so a coin's "7 days" was however far 250 articles reached.

Hermetic: a recording Supabase fake, recording FMP fakes, fixed clocks via monkeypatch.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.services.sentiment_service as ss
from app.integrations.fmp import EmptyAfterFailure
from app.services.sentiment_service import (
    _NEWS_MAX_PAGES,
    _NEWS_PAGE_SIZE,
    _NEWS_WINDOW_DAYS,
    _OWN_FETCH_TTL,
    SentimentService,
)

NOW = datetime(2026, 9, 21, 18, 0, tzinfo=timezone.utc)


# ── fakes ────────────────────────────────────────────────────────────────────


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Supabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return _Query(self.rows)


def _rows(n, *, oldest_hours, newest_hours=0.5, cached_age_hours=1.0):
    """`n` rows spread evenly from `newest_hours` to `oldest_hours` ago."""
    out = []
    for i in range(n):
        frac = i / (n - 1) if n > 1 else 0.0
        age = newest_hours + (oldest_hours - newest_hours) * frac
        out.append({
            "headline": f"h{i}", "summary": "", "sentiment": None, "sentiment_confidence": None,
            "published_at": (NOW - timedelta(hours=age)).isoformat(),
            "source_name": "x",
            "cached_at": (NOW - timedelta(hours=cached_age_hours)).isoformat(),
        })
    return out


def _svc(rows, *, own_fetch_ago=None):
    """`own_fetch_ago` seconds since this service last pulled its own 14-day window
    (None = never)."""
    svc = SentimentService.__new__(SentimentService)
    svc.supabase = _Supabase(rows)
    if own_fetch_ago is not None:
        import time as _t
        memo = svc._own_fetch_map()
        memo["ETHUSD"] = _t.monotonic() - own_fetch_ago
        memo["AAPL"] = _t.monotonic() - own_fetch_ago
    return svc


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(ss, "datetime", _DT)


# ── the coverage rule ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "n,oldest_h,expect_hit,why",
    [
        (58, 16.8, False, "ETH live: a cached owner page, all younger than a day → fetch"),
        (87, 10.8, False, "BTC live"),
        (8, 3.0, False, "the chat tool's 8-row write is a cached page too → fetch"),
        (250, 183.0, True, "AAPL live: reaches past the 7-day window → hit"),
        (12, 5 * 24.0, False, "a thin ticker we have never fetched ourselves → fetch once"),
        (50, 3 * 24.0, False, "an owner page, all inside the window → truncated"),
        (50, 7 * 24.0 + 1, True, "an owner-sized set that DOES reach the window → hit"),
        (300, 2 * 24.0, False, "our own set, but not yet fetched in this process → fetch"),
    ],
)
def test_a_never_fetched_ticker_is_a_hit_only_when_the_set_covers_the_window(
    n, oldest_h, expect_hit, why
):
    got = _svc(_rows(n, oldest_hours=oldest_h))._load_from_db("ETHUSD")
    assert (got is not None) is expect_hit, why
    if expect_hit:
        assert len(got) == n


@pytest.mark.parametrize(
    "n,oldest_h",
    [(300, 2 * 24.0), (500, 2.6 * 24.0), (58, 16.8), (12, 5 * 24.0)],
)
def test_after_our_own_fetch_a_short_set_is_a_hit_until_the_memo_expires(n, oldest_h):
    """⚠️ The refetch loop this prevents: `_fetch_news` is capped at two pages, so a busy
    ticker's own 500-row set spans ~2.6 days and can NEVER reach 7. Judged on coverage
    alone it would be "truncated" forever — two FMP calls and a 500-row upsert on every
    request past the 15-minute result cache."""
    rows = _rows(n, oldest_hours=oldest_h)
    assert _svc(rows, own_fetch_ago=60)._load_from_db("ETHUSD") is not None
    assert _svc(rows, own_fetch_ago=_OWN_FETCH_TTL + 60)._load_from_db("ETHUSD") is None


def test_the_memo_is_per_ticker():
    svc = _svc(_rows(58, oldest_hours=16.8), own_fetch_ago=60)
    assert svc._load_from_db("ETHUSD") is not None
    assert svc._load_from_db("SOLUSD") is None, "another ticker has its own window to pull"


def test_the_window_and_page_constants_are_pinned():
    assert _NEWS_WINDOW_DAYS == 7 and _OWN_FETCH_TTL == 14400
    assert _NEWS_PAGE_SIZE == 250 and _NEWS_MAX_PAGES == 2


@pytest.mark.asyncio
async def test_a_successful_page_zero_stamps_the_memo_and_a_failure_does_not():
    svc = _fetch_svc(_FMP([_page_rows(40, oldest_hours=30)]))
    await svc._fetch_news("ethusd", is_crypto=True)
    assert svc._fetched_own_window("ETHUSD"), "the memo is keyed upper-case"
    failed = _fetch_svc(_FMP([], raise_on_page=0))
    await failed._fetch_news("SOLUSD", is_crypto=True)
    assert not failed._fetched_own_window("SOLUSD"), "a raising fetch must not arm the memo"
    # The other failure shape, and the one an `if True:` would arm: page 0 came back as
    # the EmptyAfterFailure marker (a degraded 503), no exception raised. Arming there
    # would suppress the refetch for four hours on the strength of an outage.
    marker = _fetch_svc(_FMP([EmptyAfterFailure("503")]))
    out = await marker._fetch_news("XRPUSD", is_crypto=True)
    assert getattr(out, "fetch_failed", False) is True
    assert not marker._fetched_own_window("XRPUSD"), "a marker must not arm the memo"


def test_a_stale_set_is_a_miss_regardless_of_coverage_or_the_memo():
    rows = _rows(250, oldest_hours=183.0, cached_age_hours=5.0)     # > 4 h refresh TTL
    assert _svc(rows)._load_from_db("AAPL") is None
    assert _svc(rows, own_fetch_ago=60)._load_from_db("AAPL") is None


def test_stale_ok_returns_whatever_exists_even_when_truncated():
    rows = _rows(58, oldest_hours=16.8, cached_age_hours=9.0)
    got = _svc(rows)._load_from_db("ETHUSD", stale_ok=True)
    assert got is not None and len(got) == 58


def test_rows_with_unreadable_dates_do_not_break_the_rule():
    rows = _rows(250, oldest_hours=183.0)
    rows[0]["published_at"] = "garbage"
    rows[1]["published_at"] = None
    assert _svc(rows)._load_from_db("AAPL") is not None


def test_a_set_with_no_readable_dates_at_all_is_a_miss_then_served():
    rows = _rows(60, oldest_hours=3.0)
    for r in rows:
        r["published_at"] = None
    assert _svc(rows)._load_from_db("ETHUSD") is None, "no dates → no proof of coverage"
    assert _svc(rows, own_fetch_ago=60)._load_from_db("ETHUSD") is not None


def test_a_truncated_set_logs_the_oldest_age(caplog):
    with caplog.at_level("INFO"):
        _svc(_rows(58, oldest_hours=16.8))._load_from_db("ETHUSD")
    assert "TRUNCATED" in caplog.text and "16.8" in caplog.text


# ── the windowed, paged fetch ────────────────────────────────────────────────


def _page_rows(n, *, oldest_hours, newest_hours=0.5):
    out = []
    for i in range(n):
        frac = i / (n - 1) if n > 1 else 0.0
        age = newest_hours + (oldest_hours - newest_hours) * frac
        out.append({"title": f"t{i}", "text": "", "url": f"u{i}",
                    "publishedDate": (NOW - timedelta(hours=age)).strftime("%Y-%m-%d %H:%M:%S")})
    return out


class _FMP:
    def __init__(self, pages, *, crypto=True, raise_on_page=None):
        self.pages, self.calls, self.raise_on_page = pages, [], raise_on_page

    async def get_crypto_news(self, ticker=None, limit=10, page=0, from_date=None, to_date=None):
        self.calls.append(("crypto", ticker, limit, page, from_date, to_date))
        return self._serve(page)

    async def get_stock_news(self, ticker=None, limit=10, page=0, from_date=None, to_date=None):
        self.calls.append(("stock", ticker, limit, page, from_date, to_date))
        return self._serve(page)

    def _serve(self, page):
        if self.raise_on_page == page:
            raise RuntimeError("503")
        if page < len(self.pages):
            return self.pages[page]
        return []


def _fetch_svc(fmp):
    # No `_own_fetch_at`: an `__new__`-built double is exactly the shape a dozen existing
    # suites use, and the memo must survive it (see `_own_fetch_map`).
    svc = SentimentService.__new__(SentimentService)
    svc.fmp = fmp
    return svc


@pytest.mark.asyncio
async def test_crypto_is_fetched_with_the_14_day_window_and_the_page_size():
    fmp = _FMP([_page_rows(40, oldest_hours=30)])
    got = await _fetch_svc(fmp)._fetch_news("ETHUSD", is_crypto=True)
    assert len(got) == 40
    kind, ticker, limit, page, frm, to = fmp.calls[0]
    assert (kind, ticker, limit, page) == ("crypto", "ETHUSD", _NEWS_PAGE_SIZE, 0)
    assert frm == (NOW - timedelta(days=14)).strftime("%Y-%m-%d") and to == NOW.strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_a_full_first_page_inside_the_window_fetches_page_one():
    fmp = _FMP([_page_rows(250, oldest_hours=6 * 24), _page_rows(51, oldest_hours=7 * 24)])
    got = await _fetch_svc(fmp)._fetch_news("ETHUSD", is_crypto=True)
    assert len(got) == 301
    assert [c[3] for c in fmp.calls] == [0, 1]


@pytest.mark.asyncio
async def test_a_partial_first_page_never_pages():
    fmp = _FMP([_page_rows(249, oldest_hours=6 * 24), _page_rows(10, oldest_hours=8 * 24)])
    got = await _fetch_svc(fmp)._fetch_news("ETHUSD", is_crypto=True)
    assert len(got) == 249 and [c[3] for c in fmp.calls] == [0]


@pytest.mark.asyncio
async def test_a_full_first_page_that_already_reaches_the_window_never_pages():
    fmp = _FMP([_page_rows(250, oldest_hours=15 * 24), _page_rows(250, oldest_hours=20 * 24)])
    got = await _fetch_svc(fmp)._fetch_news("ETHUSD", is_crypto=True)
    assert len(got) == 250 and [c[3] for c in fmp.calls] == [0]


@pytest.mark.asyncio
async def test_never_more_than_the_page_cap():
    fmp = _FMP([_page_rows(250, oldest_hours=2 * 24), _page_rows(250, oldest_hours=4 * 24),
                _page_rows(250, oldest_hours=6 * 24)])
    got = await _fetch_svc(fmp)._fetch_news("ETHUSD", is_crypto=True)
    assert len(got) == 500 and [c[3] for c in fmp.calls] == [0, 1]


@pytest.mark.asyncio
async def test_a_page_one_failure_keeps_page_zero(caplog):
    fmp = _FMP([_page_rows(250, oldest_hours=2 * 24)], raise_on_page=1)
    with caplog.at_level("WARNING"):
        got = await _fetch_svc(fmp)._fetch_news("ETHUSD", is_crypto=True)
    assert len(got) == 250 and not getattr(got, "fetch_failed", False)
    assert "page 1 failed" in caplog.text


@pytest.mark.asyncio
async def test_a_page_one_marker_keeps_page_zero():
    fmp = _FMP([_page_rows(250, oldest_hours=2 * 24), EmptyAfterFailure("503")])
    got = await _fetch_svc(fmp)._fetch_news("ETHUSD", is_crypto=True)
    assert len(got) == 250 and not getattr(got, "fetch_failed", False)


@pytest.mark.asyncio
async def test_a_page_zero_marker_propagates_and_stops():
    fmp = _FMP([EmptyAfterFailure("503"), _page_rows(250, oldest_hours=2 * 24)])
    got = await _fetch_svc(fmp)._fetch_news("ETHUSD", is_crypto=True)
    assert getattr(got, "fetch_failed", False) is True and [c[3] for c in fmp.calls] == [0]


@pytest.mark.asyncio
async def test_a_page_zero_raise_is_the_marker():
    fmp = _FMP([], raise_on_page=0)
    got = await _fetch_svc(fmp)._fetch_news("ETHUSD", is_crypto=True)
    assert getattr(got, "fetch_failed", False) is True


@pytest.mark.asyncio
async def test_an_empty_feed_is_a_plain_empty_list():
    got = await _fetch_svc(_FMP([[]]))._fetch_news("ZZZUSD", is_crypto=True)
    assert got == [] and not getattr(got, "fetch_failed", False)


@pytest.mark.asyncio
async def test_stocks_take_the_same_window_and_paging():
    fmp = _FMP([_page_rows(250, oldest_hours=3 * 24), _page_rows(120, oldest_hours=9 * 24)])
    got = await _fetch_svc(fmp)._fetch_news("AAPL", is_crypto=False)
    assert len(got) == 370
    assert all(c[0] == "stock" and c[2] == _NEWS_PAGE_SIZE and c[4] and c[5] for c in fmp.calls)


# ── the whole chain: the two windows now differ ───────────────────────────────


def test_a_six_day_set_scores_different_24h_and_7d_windows():
    svc = SentimentService.__new__(SentimentService)
    articles = _page_rows(300, oldest_hours=6 * 24)
    _, cur24, _, _, _, _ = svc._compute_news_score(articles, hours=24)
    _, cur7d, _, _, _, _ = svc._compute_news_score(articles, hours=168)
    assert 0 < cur24 < cur7d == 300


# ── source pin: the client forwards the window ───────────────────────────────


def test_get_crypto_news_forwards_from_and_to():
    from app.integrations.fmp import FMPClient

    src = textwrap.dedent(inspect.getsource(FMPClient.get_crypto_news))
    assert 'params["from"] = from_date' in src and 'params["to"] = to_date' in src
    sig = inspect.signature(FMPClient.get_crypto_news)
    assert {"from_date", "to_date"} <= set(sig.parameters)
