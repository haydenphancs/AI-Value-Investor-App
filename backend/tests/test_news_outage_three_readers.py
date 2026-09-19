"""The three readers that still collapsed a FAILED news fetch into "no news" (F18-5).

`get_stock_news` / `get_crypto_news` answer an outage with `EmptyAfterFailure` — an empty
list that remembers it failed — so a reader can tell "FMP was down" from "a quiet week".
`news_cache_service._fetch_and_cache_raw` and the chat tool honoured it; three readers did
not:

  (a) the paid deep-research tool `fetch_more_news` iterated it and handed the model
      `{"articles": []}` with no `error` — and a 20-credit report then narrated "there is no
      recent news coverage", frozen for the close-aligned window;
  (b) `sentiment_service._fetch_news` rebuilt a plain `[]` from it, so `news_articles=0`,
      `▲0 =0 ▼0` were scored as MEASURED and — the price arm being measured — CACHED 15 min;
  (c) the index / commodity News reader returned a plain `[]`, so its envelope never carried
      the `fetch_failed` flag the ticker envelope does.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations.fmp import EmptyAfterFailure
from app.services import news_cache_service as ncs
from app.services import sentiment_service as sent
from app.services.agents import fmp_tools
from app.services.sentiment_service import SentimentService as S


# ── (a) fetch_more_news ──────────────────────────────────────────────────────

def _handlers(news_result):
    fmp = MagicMock()
    if isinstance(news_result, BaseException):
        fmp.get_stock_news = AsyncMock(side_effect=news_result)
    else:
        fmp.get_stock_news = AsyncMock(return_value=news_result)
    return fmp_tools.build_tool_handlers(fmp), fmp


@pytest.mark.asyncio
async def test_fetch_more_news_reports_an_outage_with_an_error_key_not_an_empty_feed(caplog):
    handlers, _ = _handlers(EmptyAfterFailure("HTTPStatusError: 503"))
    with caplog.at_level("WARNING", logger="app.services.agents.fmp_tools"):
        out = await handlers["fetch_more_news"]({"ticker": "aapl", "limit": 5})
    assert out["articles"] == []
    assert "error" in out and "unavailable" in out["error"]
    assert "do not say there is no news" in out.get("note", "")
    assert any("AAPL" in r.getMessage() and "FAILED" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_more_news_a_plain_empty_feed_is_still_a_measured_empty():
    handlers, _ = _handlers([])
    out = await handlers["fetch_more_news"]({"ticker": "AAPL"})
    assert out == {"articles": []}
    assert "error" not in out and "note" not in out


@pytest.mark.asyncio
async def test_fetch_more_news_a_real_feed_is_compressed_unchanged():
    handlers, _ = _handlers([
        {"title": "T", "publishedDate": "2026-09-16 10:00:00", "text": "x" * 400,
         "sentiment": "Positive"},
        {"title": None, "publishedDate": "", "text": None},           # malformed row
    ])
    out = await handlers["fetch_more_news"]({"ticker": "AAPL", "limit": 99})
    assert "error" not in out
    assert out["articles"][0]["title"] == "T" and len(out["articles"][0]["text"]) == 300
    assert out["articles"][1] == {"title": None, "date": "", "text": "", "sentiment": ""}


@pytest.mark.asyncio
async def test_fetch_more_news_a_raised_fetch_still_carries_error():
    handlers, _ = _handlers(RuntimeError("boom"))
    out = await handlers["fetch_more_news"]({"ticker": "AAPL"})
    assert out["articles"] == [] and out["error"] == "boom"


@pytest.mark.asyncio
async def test_fetch_more_news_caps_the_limit_at_15():
    handlers, fmp = _handlers([])
    await handlers["fetch_more_news"]({"ticker": "AAPL", "limit": 500})
    assert fmp.get_stock_news.await_args.args == ("AAPL", 15)


# ── (b) sentiment_service ────────────────────────────────────────────────────

def _svc(monkeypatch, articles_result, *, price_measured=True):
    """A SentimentService whose four upstream arms are stubbed; the news arm is
    `articles_result` (a list, a marker, or an exception to raise)."""
    monkeypatch.setattr(sent, "_cache", {})
    svc = S.__new__(S)

    async def _articles(self, ticker, is_crypto=False):
        if isinstance(articles_result, BaseException):
            raise articles_result
        return articles_result

    async def _price(self, ticker):
        return {"changesPercentage": 2.5} if price_measured else {}

    async def _hist(self, ticker):
        return []

    monkeypatch.setattr(S, "_get_articles", _articles)
    monkeypatch.setattr(S, "_fetch_price_data", _price)
    monkeypatch.setattr(S, "_fetch_historical_prices", _hist)

    social = MagicMock()
    social.get_mentions_24h = AsyncMock(return_value=(0, 0, False))
    social.get_mentions_7d = AsyncMock(return_value=(0, 0, False))
    monkeypatch.setattr(sent, "get_social_mentions_service", lambda: social)
    return svc


@pytest.mark.asyncio
async def test_a_failed_news_arm_is_served_but_NOT_cached(monkeypatch, caplog):
    svc = _svc(monkeypatch, EmptyAfterFailure("HTTPStatusError: 503"))
    with caplog.at_level("WARNING", logger="app.services.sentiment_service"):
        resp = await svc.get_sentiment("AAPL")
    # Served (the wire fields are non-Optional), degraded to 0 counts…
    assert resp.news_articles == 0 and resp.news_bullish == 0
    # …but the reading is not pinned: the next request retries the feed.
    assert "sentiment:AAPL" not in sent._cache
    assert any("news arm FAILED" in r.getMessage() and "AAPL" in r.getMessage()
               and "503" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_raised_news_arm_is_likewise_not_cached(monkeypatch):
    svc = _svc(monkeypatch, RuntimeError("db exploded"))
    resp = await svc.get_sentiment("AAPL")
    assert resp.news_articles == 0
    assert "sentiment:AAPL" not in sent._cache


@pytest.mark.asyncio
async def test_a_plain_empty_feed_with_a_measured_price_arm_IS_cached(monkeypatch):
    """Control: the quiet-week reading keeps its 15 min memo — only the outage is refused."""
    svc = _svc(monkeypatch, [])
    resp = await svc.get_sentiment("AAPL")
    assert resp.news_articles == 0
    assert "sentiment:AAPL" in sent._cache
    assert sent._cache_get("sentiment:AAPL") is resp


@pytest.mark.asyncio
async def test_a_failed_news_arm_with_no_other_signal_still_takes_the_total_failure_path(monkeypatch):
    svc = _svc(monkeypatch, EmptyAfterFailure("x"), price_measured=False)
    resp = await svc.get_sentiment("AAPL")
    assert resp.mood_score == 50 and "sentiment:AAPL" not in sent._cache


@pytest.mark.asyncio
async def test_the_next_request_after_an_outage_re_hits_the_feed(monkeypatch):
    calls = []
    results = [EmptyAfterFailure("503"), [{"title": "Fresh", "text": "",
                                            "publishedDate": "2026-09-16 10:00:00"}]]

    async def _articles(self, ticker, is_crypto=False):
        calls.append(ticker)
        return results[len(calls) - 1]

    _svc(monkeypatch, [])
    monkeypatch.setattr(S, "_get_articles", _articles)
    svc = S.__new__(S)
    await svc.get_sentiment("AAPL")
    await svc.get_sentiment("AAPL")
    assert calls == ["AAPL", "AAPL"], "the outage reading was served from cache"
    assert "sentiment:AAPL" in sent._cache


@pytest.mark.asyncio
@pytest.mark.parametrize("is_crypto", [False, True])
async def test_fetch_news_keeps_the_marker_and_rebuilds_nothing_else(monkeypatch, is_crypto):
    svc = S.__new__(S)
    svc.fmp = MagicMock()
    marker = EmptyAfterFailure("503")
    svc.fmp.get_stock_news = AsyncMock(return_value=marker)
    svc.fmp.get_crypto_news = AsyncMock(return_value=marker)
    out = await svc._fetch_news("AAPL", is_crypto=is_crypto)
    assert out is marker and getattr(out, "fetch_failed", False) is True

    svc.fmp.get_stock_news = AsyncMock(return_value=None)
    svc.fmp.get_crypto_news = AsyncMock(return_value=None)
    plain = await svc._fetch_news("AAPL", is_crypto=is_crypto)
    assert plain == [] and not getattr(plain, "fetch_failed", False)


@pytest.mark.asyncio
async def test_get_articles_returns_the_marker_only_when_there_are_no_stale_rows(monkeypatch):
    svc = S.__new__(S)
    marker = EmptyAfterFailure("503")

    async def _fetch(self, ticker, is_crypto=False):
        return marker

    monkeypatch.setattr(S, "_fetch_news", _fetch)

    # fresh DB miss + no stale rows → the marker survives
    monkeypatch.setattr(S, "_load_from_db", lambda self, t, stale_ok=False: None)
    out = await svc._get_articles("AAPL")
    assert out is marker

    # fresh DB miss + stale rows → the stale rows are a MEASURED fallback, served as before
    stale_rows = [{"title": "old"}]
    monkeypatch.setattr(S, "_load_from_db",
                        lambda self, t, stale_ok=False: stale_rows if stale_ok else None)
    out2 = await svc._get_articles("AAPL")
    assert out2 is stale_rows and not getattr(out2, "fetch_failed", False)

    # a plain empty feed and no stale rows → plain []
    async def _empty(self, ticker, is_crypto=False):
        return []

    monkeypatch.setattr(S, "_fetch_news", _empty)
    monkeypatch.setattr(S, "_load_from_db", lambda self, t, stale_ok=False: None)
    out3 = await svc._get_articles("AAPL")
    assert out3 == [] and not getattr(out3, "fetch_failed", False)


# ── (c) index / commodity news reader ────────────────────────────────────────

def _news_svc(news_result):
    svc = ncs.NewsCacheService.__new__(ncs.NewsCacheService)
    svc.fmp = MagicMock()
    svc.fmp.get_stock_news = AsyncMock(return_value=news_result)
    built = []
    svc._build_and_cache_rows = lambda *a, **k: built.append(a) or [{"title": "row"}]
    svc._get_cached = lambda *a, **k: []
    svc._inflight = {}
    return svc, built


@pytest.mark.asyncio
async def test_index_fetch_keeps_the_marker_and_writes_no_rows():
    svc, built = _news_svc(EmptyAfterFailure("503"))
    out = await svc._fetch_and_cache_index_news("^GSPC", "AAPL,MSFT", 10)
    assert getattr(out, "fetch_failed", False) is True and out == [] and built == []


@pytest.mark.asyncio
async def test_index_fetch_a_plain_empty_feed_is_a_plain_empty_list():
    svc, built = _news_svc([])
    out = await svc._fetch_and_cache_index_news("^GSPC", "AAPL", 10)
    assert out == [] and not getattr(out, "fetch_failed", False) and built == []


@pytest.mark.asyncio
async def test_get_index_news_envelope_carries_fetch_failed_like_the_ticker_one(monkeypatch):
    svc, _ = _news_svc(EmptyAfterFailure("503"))

    async def _deduped(self, key, factory):
        return await factory()

    monkeypatch.setattr(ncs.NewsCacheService, "_deduped", _deduped)
    out = await svc.get_index_news("^gspc", 10, news_tickers="AAPL")
    assert out["ticker"] == "^GSPC" and out["cached"] is False
    assert out["fetch_failed"] is True and out["articles"] == []

    svc2, _ = _news_svc([])
    out2 = await svc2.get_index_news("^GSPC", 10, news_tickers="AAPL")
    assert "fetch_failed" not in out2 and out2["articles"] == []

    svc3, _ = _news_svc([{"title": "x"}])
    out3 = await svc3.get_index_news("^GSPC", 10, news_tickers="AAPL")
    assert "fetch_failed" not in out3 and out3["articles"] == [{"title": "row"}]


# ── W2 regress-C-1: a RAISED FMP news fetch (429 / auth) is an outage too ────────


@pytest.mark.asyncio
async def test_a_news_fetch_that_raises_is_a_failure_marker_not_a_plain_empty(monkeypatch):
    """`get_stock_news` re-raises rate-limit and auth errors instead of degrading them; the
    catch-all in `_fetch_news` returned a plain `[]`, so `_get_articles` scored it as a
    measured 0 and `news_known` stayed True — the exact reading the marker keeps out of
    the 15-minute cache, on the failure the memory files record as routine."""
    from app.integrations.fmp import FMPRateLimitException
    svc = S.__new__(S)

    class _FMP:
        async def get_stock_news(self, **kw):
            raise FMPRateLimitException("429 Too Many Requests", retry_after=30)

        async def get_crypto_news(self, **kw):
            raise FMPRateLimitException("429 Too Many Requests", retry_after=30)
    svc.fmp = _FMP()
    out = await svc._fetch_news("AAPL", is_crypto=False)
    assert isinstance(out, EmptyAfterFailure) and out.fetch_failed
    assert "FMPRateLimitException" in out.reason
    assert (await svc._fetch_news("BTCUSD", is_crypto=True)).fetch_failed


@pytest.mark.asyncio
async def test_a_raised_news_fetch_reaches_get_sentiment_as_not_known(monkeypatch):
    """End to end through `_get_articles` (no fresh feed, no stale rows): the reading is
    served, `news_known` is False, and nothing is cached."""
    from app.integrations.fmp import FMPRateLimitException
    monkeypatch.setattr(sent, "_cache", {})
    svc = S.__new__(S)

    class _FMP:
        async def get_stock_news(self, **kw):
            raise FMPRateLimitException("429", retry_after=30)
    svc.fmp = _FMP()
    # No fresh rows (None) and no stale rows ([]) — the shape of a ticker nobody scored yet.
    monkeypatch.setattr(S, "_load_from_db", lambda self, ticker, stale_ok=False: [] if stale_ok else None)
    monkeypatch.setattr(S, "_fetch_price_data", lambda self, t: _async({"changesPercentage": 2.5}))
    monkeypatch.setattr(S, "_fetch_historical_prices", lambda self, t: _async([]))
    social = MagicMock()
    social.get_mentions_24h = AsyncMock(return_value=(0, 0, False))
    social.get_mentions_7d = AsyncMock(return_value=(0, 0, False))
    monkeypatch.setattr(sent, "get_social_mentions_service", lambda: social)
    resp = await svc.get_sentiment("AAPL")
    assert resp.news_articles == 0 and resp.news_known is False
    assert "sentiment:AAPL" not in sent._cache


async def _async(value):
    return value
