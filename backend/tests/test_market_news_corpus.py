"""
Market-feed corpus composition.

REGRESSION GUARD. `news/stock` with no `symbols` param does NOT return general
market news — FMP silently falls back to a single default symbol (AAPL), so the
Updates "Market" tab shipped a 100%-Apple feed while appearing to work. The
docstring on `get_stock_news` asserted the opposite, which is how it got past
review. These tests pin the corrected sources so nobody re-introduces it.
"""

import asyncio
import inspect

import pytest

from app.integrations.fmp import FMPClient, FMPRateLimitException
from app.services.news_cache_service import (
    MARKET_INDEX_SYMBOLS,
    MARKET_SCOPE,
    NewsCacheService,
)


class _StubService(NewsCacheService):
    """NewsCacheService with no network/DB clients (bypasses __init__)."""

    def __init__(self, general=None, index=None, general_exc=None, index_exc=None):
        self.supabase = None
        self.gemini = None
        self._inflight = {}
        self.calls = []
        self._general, self._index = general or [], index or []
        self._general_exc, self._index_exc = general_exc, index_exc

        outer = self

        class _FMP:
            async def get_general_news(self, limit=50, page=0):
                outer.calls.append(("general", limit))
                if outer._general_exc:
                    raise outer._general_exc
                return outer._general

            async def get_stock_news(self, ticker=None, limit=10, from_date=None,
                                     to_date=None, page=0):
                outer.calls.append(("stock", ticker))
                if outer._index_exc:
                    raise outer._index_exc
                return outer._index

        self.fmp = _FMP()


def _article(url, title, when="2026-07-20 18:00:00", symbol=None):
    return {
        "url": url, "title": title, "publishedDate": when,
        "symbol": symbol, "publisher": "Test", "text": "body", "image": None,
    }


def test_market_corpus_never_falls_back_to_a_single_symbol():
    """The market fetch must NOT call get_stock_news(None).

    That call is what produced the all-Apple feed.
    """
    svc = _StubService(
        general=[_article("g1", "Macro story")],
        index=[_article("i1", "S&P 500 hits resistance", symbol="SPY")],
    )
    asyncio.run(svc._fetch_market_raw(25))

    stock_calls = [c for c in svc.calls if c[0] == "stock"]
    assert stock_calls, "the index leg must be fetched"
    for _, ticker in stock_calls:
        assert ticker, "get_stock_news(None) returns AAPL, not general news"
        assert ticker == MARKET_INDEX_SYMBOLS


def test_market_corpus_uses_both_legs():
    svc = _StubService(
        general=[_article("g1", "Macro")],
        index=[_article("i1", "Index", symbol="SPY")],
    )
    asyncio.run(svc._fetch_market_raw(25))
    assert {c[0] for c in svc.calls} == {"general", "stock"}


def test_market_index_basket_covers_sp500_and_nasdaq():
    syms = MARKET_INDEX_SYMBOLS.upper()
    assert "SPY" in syms and "^GSPC" in syms      # S&P 500
    assert "QQQ" in syms and "^IXIC" in syms      # Nasdaq


def test_market_corpus_dedupes_across_legs():
    shared = _article("same-url", "Same story")
    svc = _StubService(general=[shared], index=[dict(shared)])
    out = asyncio.run(svc._fetch_market_raw(25))
    assert len(out) == 1


def test_market_corpus_is_newest_first():
    svc = _StubService(
        general=[_article("g1", "Older", when="2026-07-20 09:00:00")],
        index=[_article("i1", "Newer", when="2026-07-20 18:00:00")],
    )
    out = asyncio.run(svc._fetch_market_raw(25))
    assert [a["title"] for a in out] == ["Newer", "Older"]


def test_market_corpus_survives_one_dead_leg():
    # Losing macro must not empty the Market tab; the index leg still stands.
    svc = _StubService(
        general_exc=RuntimeError("upstream 502"),
        index=[_article("i1", "Index story", symbol="SPY")],
    )
    out = asyncio.run(svc._fetch_market_raw(25))
    assert len(out) == 1

    svc2 = _StubService(
        general=[_article("g1", "Macro story")],
        index_exc=RuntimeError("upstream 502"),
    )
    assert len(asyncio.run(svc2._fetch_market_raw(25))) == 1


def test_market_corpus_propagates_a_quota_failure():
    # Quota must surface as a structured error, not an empty feed.
    svc = _StubService(general_exc=FMPRateLimitException("429"), index=[])
    with pytest.raises(FMPRateLimitException):
        asyncio.run(svc._fetch_market_raw(25))


def test_market_corpus_skips_malformed_rows():
    svc = _StubService(
        general=[None, "junk", 42, _article("g1", "Real")],
        index=[{"no_url_or_title": True}],
    )
    out = asyncio.run(svc._fetch_market_raw(25))
    assert [a["title"] for a in out] == ["Real"]


def test_market_corpus_respects_the_limit():
    svc = _StubService(
        general=[_article(f"g{i}", f"G{i}") for i in range(40)],
        index=[_article(f"i{i}", f"I{i}", symbol="SPY") for i in range(40)],
    )
    assert len(asyncio.run(svc._fetch_market_raw(10))) == 10


def test_general_news_hits_the_general_latest_endpoint():
    src = inspect.getsource(FMPClient.get_general_news)
    assert "news/general-latest" in src


def test_get_stock_news_docstring_warns_about_the_none_fallback():
    # The old docstring claimed None returns general news. Anyone reading it
    # today must be told otherwise.
    doc = (FMPClient.get_stock_news.__doc__ or "").lower()
    assert "does not return general" in doc
