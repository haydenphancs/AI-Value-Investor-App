"""A failed news fetch must not read as "no news was published today".

`get_stock_news` / `get_crypto_news` degrade a non-quota failure to `[]` so the News tab
renders an empty feed rather than an error — but every consumer read that `[]` as "this
ticker has no news", and Ask Cay AI's `explain_price_move` told the user "No company news was
published today" about an outage. The empty list now REMEMBERS it came from a failure
(`EmptyAfterFailure.fetch_failed`), the cache layer passes it through uncached, and the chat
tool answers "could not be checked" with an `error` the doors count.
"""
import pytest
from unittest.mock import AsyncMock

from app.integrations import fmp as fmp_mod
from app.integrations.fmp import EmptyAfterFailure, FMPClient
from app.services import chat_market_tools as cmt
from app.services import news_cache_service as ncs


def test_the_marker_is_an_empty_list_for_every_existing_consumer():
    e = EmptyAfterFailure("HTTPStatusError: 503")
    assert e == [] and not e and len(e) == 0 and list(e) == []
    assert e.fetch_failed is True and e.reason.startswith("HTTPStatusError")
    assert getattr([], "fetch_failed", False) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("method, endpoint", [("get_stock_news", "news/stock"), ("get_crypto_news", "news/crypto")])
async def test_a_generic_failure_returns_the_marker_and_quota_still_raises(method, endpoint):
    from app.integrations.fmp import FMPRateLimitException
    c = FMPClient.__new__(FMPClient)
    c._make_request = AsyncMock(side_effect=RuntimeError("503 after retries"))
    out = await getattr(c, method)("AAPL", limit=5)
    assert isinstance(out, EmptyAfterFailure) and out == []
    c._make_request = AsyncMock(side_effect=FMPRateLimitException("429"))
    with pytest.raises(FMPRateLimitException):
        await getattr(c, method)("AAPL", limit=5)
    c._make_request = AsyncMock(return_value=[])
    plain = await getattr(c, method)("AAPL", limit=5)
    assert plain == [] and not getattr(plain, "fetch_failed", False)


@pytest.mark.asyncio
async def test_the_cache_layer_passes_the_marker_through_and_does_not_cache_it():
    svc = ncs.NewsCacheService.__new__(ncs.NewsCacheService)
    svc.fmp = type("F", (), {"get_stock_news": AsyncMock(return_value=EmptyAfterFailure("x")),
                             "get_crypto_news": AsyncMock(return_value=EmptyAfterFailure("x"))})()
    built = []
    svc._build_and_cache_rows = lambda *a, **k: built.append(a) or []
    out = await svc._fetch_and_cache_raw("AAPL", 5)
    assert getattr(out, "fetch_failed", False) is True and built == []
    # A genuinely empty feed is a plain [] and the cache path is likewise skipped.
    svc.fmp = type("F", (), {"get_stock_news": AsyncMock(return_value=[]),
                             "get_crypto_news": AsyncMock(return_value=[])})()
    out2 = await svc._fetch_and_cache_raw("AAPL", 5)
    assert out2 == [] and not getattr(out2, "fetch_failed", False)


@pytest.mark.asyncio
async def test_the_chat_tool_reports_could_not_be_checked_with_an_error(monkeypatch):
    class _NC:
        async def get_ticker_news(self, *a, **k):
            return {"articles": EmptyAfterFailure("boom"), "fetch_failed": True, "ticker": "AAPL"}
    monkeypatch.setattr("app.services.news_cache_service.get_news_cache_service", lambda: _NC())
    out = await cmt.fetch_ticker_news("AAPL")
    assert out["news_available"] is False and "error" in out

    class _Empty:
        async def get_ticker_news(self, *a, **k):
            return {"articles": [], "ticker": "AAPL"}
    monkeypatch.setattr("app.services.news_cache_service.get_news_cache_service", lambda: _Empty())
    out2 = await cmt.fetch_ticker_news("AAPL")
    assert out2["news_available"] is True and out2["article_count"] == 0 and "error" not in out2
