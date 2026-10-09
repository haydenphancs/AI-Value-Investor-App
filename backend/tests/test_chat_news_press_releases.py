"""The news tool's press-release leg (2026-10-08): "what guidance did X give" gets a licensed
answer — the company's own releases ride with the headlines as `press_releases`, labelled as
the company's statements.

Load-bearing:
  * listed securities only: never for a coin, an index or a futures contract; "LTC" on the LTC
    Properties screen (the handler passes `is_crypto=False`) IS the REIT;
  * the two reads run CONCURRENTLY, the release read bounded inside the news tool's ceiling;
  * a failed / still-running release read says "not loaded" — never "nothing announced"; a
    plain empty answer says none are on file;
  * the headlines answer on their own when the releases fail, and the releases survive a
    headline outage (whose `error` / `upstream` stay, so the turn's refund gate still counts it);
  * the releases shrink to fit the tool-result cap (texts, then the oldest) — the headlines are
    never cut to make room;
  * no vendor is named.

Hermetic: `fetch_ticker_news` and the press-release service's FMP seam are stubbed.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

import pytest

import app.services.chat_market_tools as cmt
import app.services.press_release_service as prs
from app.config import settings
from app.integrations.fmp import EmptyAfterFailure
from app.services.agents import chat_tools
from app.services.chat_service import ChatService

_VENDORS = re.compile(r"fmp|financial ?modeling ?prep|\bgemini(?!_)|\bbrave\b|\bgoogle(?!_)",
                      re.IGNORECASE)


def _svc() -> ChatService:
    return ChatService.__new__(ChatService)


def _news(sym="AAPL", n=2):
    return {"ticker": sym, "news_available": True, "article_count": n,
            "articles": [{"headline": f"Headline {i}", "source": "Reuters"} for i in range(n)],
            "note": "Headlines and summaries are third-party text."}


def _release(day, title="Apple reports fourth quarter results", text="Revenue grew."):
    return {"date": day, "title": title, "text": text,
            "publisher": "AAPL (the company's own press release)"}


@pytest.fixture
def stub(monkeypatch):
    """`fetch_ticker_news` and `get_press_releases` stubbed; records calls and their timing."""
    state = {"news": _news(), "releases": [_release("2026-10-01 08:30")], "calls": [],
             "news_delay": 0.0, "release_delay": 0.0}

    async def _fetch_news(ticker, is_crypto=False):
        state["calls"].append(("news", ticker, is_crypto))
        await asyncio.sleep(state["news_delay"])
        value = state["news"]
        if isinstance(value, BaseException):
            raise value
        return json.loads(json.dumps(value))

    async def _get_releases(ticker, *, wait=None):
        state["calls"].append(("releases", ticker, wait))
        await asyncio.sleep(state["release_delay"])
        value = state["releases"]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, EmptyAfterFailure):
            return value
        return json.loads(json.dumps(value))

    monkeypatch.setattr(cmt, "fetch_ticker_news", _fetch_news)
    monkeypatch.setattr(prs, "get_press_releases", _get_releases)
    return state


# ── which assets get releases ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_listed_company_gets_its_press_releases_beside_the_headlines(stub):
    out = await _svc()._fetch_ticker_news_data("AAPL")
    assert out["articles"] == _news()["articles"], "the headlines are untouched"
    assert out["press_releases"] == [_release("2026-10-01 08:30")]
    assert "company's own statements" in out["press_releases_note"]
    assert "never present one as independent reporting" in out["press_releases_note"]
    assert ("releases", "AAPL", ChatService._PRESS_RELEASE_WAIT_SECONDS) in stub["calls"]
    assert ("news", "AAPL", False) in stub["calls"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ticker,is_crypto", [
    ("BTCUSD", None), ("BTC", None), ("LTC", None), ("ETHUSD", True),   # coins
    ("^GSPC", None), ("^GSPC", False),                                   # an index
    ("GCUSD", None), ("GCUSD", False), ("CLUSD", False),                 # futures
])
async def test_no_release_read_for_a_coin_an_index_or_a_commodity(stub, ticker, is_crypto):
    kw = {} if is_crypto is None else {"is_crypto": is_crypto}
    out = await _svc()._fetch_ticker_news_data(ticker, **kw)
    assert [c for c in stub["calls"] if c[0] == "releases"] == []
    assert "press_releases" not in out and "press_releases_note" not in out


@pytest.mark.asyncio
async def test_the_equity_screens_ltc_reads_the_reits_releases(stub):
    """Through the real handler: on the LTC Properties screen the news handler passes
    `is_crypto=False`, and the release leg follows it (a bare-coin classification would have
    skipped the REIT's own announcements)."""
    handlers = chat_tools.build_chat_tool_handlers(_svc(), screen_symbol="LTC",
                                                   screen_asset_type="STOCK")
    stub["news"] = _news("LTC")
    await handlers["get_ticker_news"]({"ticker": "ltc"})
    assert ("news", "LTC", False) in stub["calls"]
    assert ("releases", "LTC", ChatService._PRESS_RELEASE_WAIT_SECONDS) in stub["calls"]
    # In a general chat a typed LTC is Litecoin: crypto news, no releases.
    stub["calls"].clear()
    await chat_tools.build_chat_tool_handlers(_svc())["get_ticker_news"]({"ticker": "ltc"})
    assert stub["calls"] == [("news", "LTCUSD", True)]


# ── concurrency and the bound ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_two_reads_run_concurrently(stub):
    stub["news_delay"] = stub["release_delay"] = 0.2
    started = time.monotonic()
    out = await _svc()._fetch_ticker_news_data("AAPL")
    elapsed = time.monotonic() - started
    assert elapsed < 0.35, f"the reads ran one after another ({elapsed:.2f}s)"
    assert out["press_releases"]


@pytest.mark.asyncio
async def test_a_slow_release_read_is_bounded_and_keeps_warming(monkeypatch):
    """The REAL press-release service behind a gated upstream: past the leg's bound the news
    tool answers without them ("not loaded"), and the fetch keeps going and fills the cache."""
    gate = asyncio.Event()

    class _FMP:
        calls = 0

        async def get_press_releases(self, sym, limit=10):
            _FMP.calls += 1
            await gate.wait()
            return [{"symbol": sym, "publishedDate": "2026-10-01 08:30:00",
                     "title": "Guidance raised", "text": "We now expect more."}]

    async def _fetch_news(ticker, is_crypto=False):
        return _news(ticker)

    prs.clear_memory()
    prs._inflight.clear()
    monkeypatch.setattr(prs, "_fmp", lambda: _FMP())
    monkeypatch.setattr(cmt, "fetch_ticker_news", _fetch_news)
    monkeypatch.setattr(ChatService, "_PRESS_RELEASE_WAIT_SECONDS", 0.05)
    try:   # an outer limit, so a lost bound fails here instead of hanging the suite
        out = await asyncio.wait_for(_svc()._fetch_ticker_news_data("SLOWCO"), 2.0)
    except asyncio.TimeoutError:
        gate.set()
        pytest.fail("the press-release leg has no bound of its own")
    assert "press_releases" not in out
    assert "could not be loaded in this answer" in out["press_releases_note"]
    assert "never say the company announced nothing" in out["press_releases_note"]
    gate.set()
    task = prs._inflight.get("SLOWCO")
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 1.0)
    again = await _svc()._fetch_ticker_news_data("SLOWCO")
    assert again["press_releases"][0]["title"] == "Guidance raised"
    assert _FMP.calls == 1, "the warm read must come from the cache"
    prs.clear_memory()
    prs._inflight.clear()


# ── degraded paths ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failed_release_read_is_never_nothing_announced(stub):
    stub["releases"] = EmptyAfterFailure("fetch failed")
    out = await _svc()._fetch_ticker_news_data("AAPL")
    assert "press_releases" not in out
    assert "could not be loaded" in out["press_releases_note"]
    assert out["articles"] == _news()["articles"] and "error" not in out


@pytest.mark.asyncio
async def test_none_on_file_is_said_as_none_on_file(stub):
    stub["releases"] = []
    out = await _svc()._fetch_ticker_news_data("AAPL")
    assert out["press_releases"] == []
    assert out["press_releases_note"] == (
        "No press releases from the company are on file in Caydex's data right now.")


@pytest.mark.asyncio
async def test_a_raising_release_read_leaves_the_headlines(stub, caplog):
    stub["releases"] = RuntimeError("FMPRateLimitException 429")
    with caplog.at_level("WARNING"):
        out = await _svc()._fetch_ticker_news_data("AAPL")
    assert out["articles"] == _news()["articles"] and "press_releases" not in out
    assert "could not be loaded" in out["press_releases_note"]
    assert "press releases raised for AAPL" in caplog.text
    assert not _VENDORS.search(json.dumps(out))


@pytest.mark.asyncio
async def test_a_headline_outage_keeps_its_error_and_the_releases(stub):
    stub["news"] = {"ticker": "AAPL", "news_available": False, "upstream": True,
                    "error": "news feed unavailable (upstream fetch failed)",
                    "note": "The news feed could not be reached; do not say there is no news."}
    out = await _svc()._fetch_ticker_news_data("AAPL")
    assert out["upstream"] is True and out["error"].startswith("news feed unavailable")
    assert out["press_releases"] == [_release("2026-10-01 08:30")]


@pytest.mark.asyncio
async def test_a_raising_headline_read_keeps_the_releases(stub, caplog):
    stub["news"] = RuntimeError("boom with apikey=SECRET")
    with caplog.at_level("WARNING"):
        out = await _svc()._fetch_ticker_news_data("AAPL")
    assert out["upstream"] is True and out["error"] == "news feed unavailable"
    assert "SECRET" not in json.dumps(out)
    assert out["press_releases"] == [_release("2026-10-01 08:30")]
    assert "headline fetch raised for AAPL" in caplog.text


@pytest.mark.asyncio
async def test_cancellation_propagates(stub):
    stub["news_delay"] = 5.0
    task = asyncio.ensure_future(_svc()._fetch_ticker_news_data("AAPL"))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("releases", [None, "junk", 7, [None, "x", 3], [{"title": "ok"}, "bad"]])
def test_malformed_releases_never_raise(releases):
    out = ChatService._with_press_releases(_news(), releases)
    assert out["articles"] == _news()["articles"]
    if releases is None:
        assert "press_releases" not in out
    else:
        assert all(isinstance(r, dict) for r in out.get("press_releases", []))


@pytest.mark.parametrize("news", [None, "junk", [], 5])
def test_a_non_dict_news_result_passes_through(news):
    assert ChatService._with_press_releases(news, [_release("2026-10-01")]) == news


# ── fitting under the tool-result cap ─────────────────────────────────────────

def _big_news(chars: int):
    """Headlines totalling about `chars` characters of JSON."""
    n = max(1, chars // 260)
    return {"ticker": "AAPL", "news_available": True, "article_count": n,
            "articles": [{"headline": "H" * 200, "source": "Reuters"} for _ in range(n)]}


def _five_releases(text_len=300, title_len=200):
    return [_release(f"2026-10-0{i} 08:30", title="T" * title_len, text="X" * text_len)
            for i in range(5, 0, -1)]


def _budget() -> int:
    return int(settings.GEMINI_TOOL_RESULT_MAX_CHARS) - ChatService._PRESS_RELEASE_MARGIN


def test_small_results_are_left_whole():
    out = ChatService._with_press_releases(_news(), _five_releases(text_len=40, title_len=40))
    assert len(out["press_releases"]) == 5 and "press_releases_shortened" not in out
    assert all(r["text"] == "X" * 40 for r in out["press_releases"])


def test_texts_shorten_first_then_drop_then_the_oldest_go():
    budget = _budget()
    # Leaves room for all five releases with short texts — but not full ones.
    news = _big_news(budget - 2200)
    out = ChatService._with_press_releases(news, _five_releases())
    assert len(json.dumps(out)) <= budget
    assert out["articles"] == news["articles"], "the headlines are never cut"
    assert len(out["press_releases"]) == 5
    assert all(len(r.get("text", "")) <= ChatService._PRESS_RELEASE_SHORT_TEXT
               for r in out["press_releases"])
    assert "press_releases_shortened" in out and "not shown" not in out["press_releases_shortened"]

    # Tighter: only the newest releases survive, without texts, newest first.
    news = _big_news(budget - 900)
    out = ChatService._with_press_releases(news, _five_releases())
    rows = out["press_releases"]
    assert 1 <= len(rows) < 5 and all("text" not in r for r in rows)
    assert [r["date"] for r in rows] == sorted((r["date"] for r in rows), reverse=True)
    assert rows[0]["date"] == "2026-10-05 08:30", "the newest release is the one kept"
    assert "older release" in out["press_releases_shortened"]
    assert "never treat what is missing as nothing announced" in out["press_releases_shortened"]
    assert out["articles"] == news["articles"]


def test_one_release_is_always_kept_even_when_the_headlines_alone_overflow():
    news = _big_news(_budget() + 3000)
    out = ChatService._with_press_releases(news, _five_releases())
    assert len(out["press_releases"]) == 1 and "text" not in out["press_releases"][0]
    assert out["articles"] == news["articles"], "the generic pruner, not this leg, trims headlines"


def test_extreme_release_strings_are_bounded_by_the_fit():
    rows = [_release("2026-10-01", title="T" * 50_000, text="X" * 1_000_000)]
    out = ChatService._with_press_releases(_news(), rows)
    assert "text" not in out["press_releases"][0]
    assert out["press_releases"][0]["title"] == "T" * 50_000, (
        "a title is the service's to bound (200 chars); this leg never rewrites one")


def test_the_fit_reads_the_live_cap(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_TOOL_RESULT_MAX_CHARS", 4000)
    news = _big_news(2500)
    out = ChatService._with_press_releases(news, _five_releases())
    assert len(json.dumps(out)) <= 4000 - ChatService._PRESS_RELEASE_MARGIN


@pytest.mark.parametrize("cap", [None, 0, "junk"])
def test_an_unreadable_cap_falls_back_to_the_default(monkeypatch, cap):
    monkeypatch.setattr(settings, "GEMINI_TOOL_RESULT_MAX_CHARS", cap)
    out = ChatService._with_press_releases(_news(), _five_releases())
    assert len(json.dumps(out)) <= 8000 - ChatService._PRESS_RELEASE_MARGIN


def test_no_vendor_in_any_note():
    texts = [ChatService._PRESS_RELEASES_NOTE]
    for releases in (None, [], EmptyAfterFailure("fetch failed"), _five_releases()):
        out = ChatService._with_press_releases(_big_news(7000), releases)
        texts += [v for k, v in out.items() if k.startswith("press_releases_") and isinstance(v, str)]
    for text in texts:
        assert not _VENDORS.search(text), text
