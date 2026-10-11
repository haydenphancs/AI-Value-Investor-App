"""The Updates Insights card is never written from filler (2026-10-11).

Seen on MSFT: a bullet "Several institutional investors increased their stakes in
Microsoft during the second quarter." — the card had summarised the syndicated
quarterly-holdings template ("GAMMA Investing LLC Acquires 7,458 Shares of … $META") and
it would equally have summarised law-firm solicitations ("ROSEN, A LEADING LAW FIRM,
Encourages … Investors …"). Both are now dropped from the card's corpus by the News Tone
chart's own detectors (`news_tone_filters`), and kept in the feed.

What is pinned here is CONSISTENCY across every reader of that corpus, because the card,
its "N sources" list, its badge, the endpoint's show/hide gate and the Updates-scope chat
must agree about which stories exist:

* the selector (`select_recent_corpus`) drops them before the subject filter and the
  window choice — a filler-only window is EMPTY (no card), a mixed one keeps only news;
* the bulk read (`get_cached_bulk`) skips them before its per-scope cap, so a
  filler-heavy ticker still gets a full corpus, and the Market floor cannot re-admit one;
* the sweeper's gate fingerprints the filler-free corpus — a new filler row never
  re-keys a card (no regeneration loop), and the first pass after this change re-keys a
  filler-bearing card exactly once;
* the AI card cites and counts only real stories; the fallback lists only real headlines;
* the chat is grounded on the same filler-free window and says what was left out.

Hermetic: every service is stubbed, nothing reaches FMP, Gemini or Supabase.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import app.services.news_cache_service as news_mod
import app.services.news_insight_service as insight_mod
import app.services.news_sentiment_trend_service as trend_mod
import app.services.updates_insight_sweeper as sweeper_mod
from app.api.v1.endpoints import updates as updates_endpoint
from app.services.chat_context_resolver import ChatContextResolver
from app.services.market_news_relevance import is_market_story, stamp_model_scope
from app.services.news_cache_service import MARKET_SCOPE, NewsCacheService
from app.services.news_insight_service import (
    INSIGHT_MODEL,
    MIN_CORPUS_ARTICLES,
    NewsInsightService,
    _MAX_SOURCES,
    select_recent_corpus,
)
from app.services.news_tone_filters import (
    is_filler_article,
    is_law_firm_solicitation,
    is_routine_holdings_filing,
)
from app.services.ticker_report_cache import current_close_cycle_start
from app.services.updates_insight_sweeper import InsightSweeper
from app.services.updates_materiality import (
    ACTION_SKIP,
    Decision,
    compute_inputset_id,
    corpus_article_ids,
    decide,
)
from _price_fakes import PriceFromFMPFake

# A Wednesday mid-session (14:00 ET): the window never stretches across a closed market,
# so an empty answer reports exactly the 48h tier.
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc)

HOLDINGS = [
    "GAMMA Investing LLC Acquires 7,458 Shares of Microsoft Corporation $MSFT",
    "Microsoft Corporation $MSFT Shares Sold by DMC Group LLC",
    "Artemis Wealth Advisors LLC Acquires New Holdings in Microsoft Corporation $MSFT",
]
SOLICITATIONS = [
    "ROSEN, A LEADING LAW FIRM, Encourages Microsoft Corporation Investors to Secure "
    "Counsel Before Important Deadline in Securities Class Action - MSFT",
    "Pomerantz Law Firm Investigates Claims On Behalf of Investors of Microsoft "
    "Corporation - MSFT",
]
FILLER = HOLDINGS + SOLICITATIONS
# Real news that SHARES the filler's vocabulary — the detectors' must-not-match twins, so a
# test that drops "anything with 'stake' or 'lawsuit' in it" fails here.
NEWS = [
    "Microsoft unveils new Azure AI chips",
    "Microsoft Raises Stake in OpenAI",
    "Microsoft wins dismissal of securities fraud lawsuit",
    "Microsoft CEO Satya Nadella Sells 10,000 Shares of Microsoft Corporation $MSFT",
    "Microsoft expands data-centre build in Europe",
]


def test_the_fixture_headlines_are_what_they_claim_to_be():
    """Anti-vacuity: every assertion below leans on these labels."""
    assert all(is_routine_holdings_filing(t) for t in HOLDINGS)
    assert all(is_law_firm_solicitation(t) for t in SOLICITATIONS)
    assert not any(is_routine_holdings_filing(t) or is_law_firm_solicitation(t) for t in NEWS)


def _row(headline, *, hours, now=NOW, tickers=("MSFT",), sentiment=None):
    slug = "".join(c for c in headline.lower() if c.isalnum())[:60]
    return {
        "id": f"id-{slug}-{hours}",
        "external_id": f"https://news.example/{slug}-{hours}",
        "article_url": f"https://news.example/{slug}-{hours}",
        "headline": headline,
        "source_name": "Example Wire",
        "related_tickers": list(tickers),
        "published_at": (now - timedelta(hours=hours)).isoformat(),
        "sentiment": sentiment,
        "ai_processed": sentiment is not None,
    }


def _live(headline, *, hours, **kw):
    """Dated against the REAL clock, for the readers that call `datetime.now` themselves
    (the endpoint, the chat resolver, the sweeper)."""
    return _row(headline, hours=hours, now=datetime.now(timezone.utc), **kw)


def _mixed(now_fn=_row):
    """Filler interleaved with news, newest first — the filler is the NEWEST, the shape of a
    MarketBeat morning."""
    rows = []
    for i, title in enumerate(FILLER):
        rows.append(now_fn(title, hours=0.2 + i * 0.1))
    for i, title in enumerate(NEWS):
        rows.append(now_fn(title, hours=1 + i))
    return rows


def _titles(rows):
    return [r["headline"] for r in rows]


# ── 1. The row predicate ─────────────────────────────────────────────────────


def test_a_filler_row_is_recognised_by_its_cache_headline_or_its_raw_title():
    assert is_filler_article({"headline": HOLDINGS[0]})
    assert is_filler_article({"title": SOLICITATIONS[0]})               # raw FMP row
    # A blank cache headline falls back to the raw title, like the tone chart's reader.
    assert is_filler_article({"headline": "   ", "title": HOLDINGS[1]})
    assert not is_filler_article({"headline": NEWS[1], "title": HOLDINGS[0]}), (
        "the cache headline wins when present"
    )


@pytest.mark.parametrize("row", [
    None, "GAMMA Investing LLC Acquires 7,458 Shares of Microsoft Corporation $MSFT", 42,
    [], {}, {"headline": ""}, {"headline": None, "title": None}, {"headline": 7},
    {"headline": "x" * 10_000},
])
def test_non_rows_and_untitled_or_malformed_rows_are_not_filler(row):
    """Kept, never raised on: the corpus's own guards (window, subject, title checks)
    decide what to do with a malformed row."""
    assert is_filler_article(row) is False


# ── 2. The selector — the one choke point ────────────────────────────────────


@pytest.mark.parametrize("scope", ["MSFT", None])     # a ticker card, and the Market's
def test_a_window_of_only_filler_is_empty_so_there_is_no_card(scope):
    rows = [_row(t, hours=1 + i) for i, t in enumerate(FILLER * 3)]
    kept, hours = select_recent_corpus(rows, NOW, scope=scope)
    assert kept == []
    assert hours == 48, "an empty answer reports the widest window tried, not freshness"


def test_a_mixed_window_keeps_every_story_in_order_and_no_filler():
    kept, hours = select_recent_corpus(
        _mixed(), NOW, scope="MSFT", company_name="Microsoft Corporation",
    )
    assert _titles(kept) == NEWS, "real news — the look-alike twins included — in order"
    assert hours == 24


def test_the_market_corpus_drops_filler_too():
    rows = [
        _row("Fed holds rates steady as inflation cools", hours=1, tickers=()),
        _row(SOLICITATIONS[0], hours=1.5, tickers=()),
        _row("Treasury yields slip after jobs data", hours=2, tickers=()),
        _row(HOLDINGS[0], hours=2.5, tickers=()),
    ]
    kept, _ = select_recent_corpus(rows, NOW)       # scope=None, as the sweeper calls it
    assert _titles(kept) == [
        "Fed holds rates steady as inflation cools", "Treasury yields slip after jobs data",
    ]


def test_filler_never_counts_toward_the_24h_threshold():
    """Two stories in 24h is THIN — the window widens to pick up yesterday's. Counting the
    filler beside them would have badged a two-story card "24h" and left yesterday's story
    out of it."""
    rows = [_row(t, hours=0.5) for t in FILLER] + [
        _row(NEWS[0], hours=2), _row(NEWS[1], hours=5), _row(NEWS[4], hours=30),
    ]
    assert len(rows) - 1 >= MIN_CORPUS_ARTICLES, "precondition: 24h is full only with filler"
    kept, hours = select_recent_corpus(rows, NOW, scope="MSFT")
    assert hours == 48
    assert _titles(kept) == [NEWS[0], NEWS[1], NEWS[4]]


def test_non_dict_rows_still_reach_the_window_helper_and_are_skipped_there():
    rows = [None, "junk", _row(NEWS[0], hours=1), 7, _row(HOLDINGS[0], hours=1)]
    kept, _ = select_recent_corpus(rows, NOW)
    assert _titles(kept) == [NEWS[0]]


# ── 3. The gate's fingerprint — no regeneration loop, no stale card ──────────


def _decide(corpus, state):
    return decide(
        scope="MSFT", corpus=corpus, quote=None, state=state,
        market_change_percent=None, close_cycle_start=current_close_cycle_start(NOW),
        now=NOW, model=INSIGHT_MODEL, market_active=True, is_market_scope=False,
    )


def _settled_state(decision):
    return {
        "last_inputset_id": decision.inputset_id,
        "last_price_band": decision.price_band,
        "close_cycle": current_close_cycle_start(NOW).isoformat(),
        "last_generated_at": (NOW - timedelta(minutes=30)).isoformat(),
    }


def test_new_filler_arriving_does_not_re_key_the_card():
    """A MarketBeat morning drops a dozen filings on a ticker. None is news, so none may
    buy a regeneration — and the card must read as verified, not stale."""
    news_only = [_row(t, hours=1 + i) for i, t in enumerate(NEWS)]
    first = _decide(select_recent_corpus(news_only, NOW, scope="MSFT")[0], {})

    with_new_filler = [_row(t, hours=0.1) for t in FILLER] + news_only
    assert corpus_article_ids(with_new_filler) != corpus_article_ids(news_only), (
        "anti-vacuity: the unfiltered corpus WOULD change the fingerprint"
    )
    again = _decide(
        select_recent_corpus(with_new_filler, NOW, scope="MSFT")[0], _settled_state(first),
    )
    assert again.inputset_id == first.inputset_id
    assert again.reason == "fingerprint_unchanged"


def test_a_card_fingerprinted_over_filler_re_keys_once_then_settles():
    """The first sweep after this change: a card whose stored fingerprint covered filler
    is regenerated once from the real stories, and then stays put."""
    rows = _mixed()
    # What the gate stored before this change: the fingerprint of the corpus WITH filler.
    old_id = compute_inputset_id(corpus_article_ids(rows), "unknown", INSIGHT_MODEL)
    old_state = {
        "last_inputset_id": old_id, "last_price_band": "unknown",
        "close_cycle": current_close_cycle_start(NOW).isoformat(),
        "last_generated_at": (NOW - timedelta(hours=3)).isoformat(),
    }
    corpus = select_recent_corpus(rows, NOW, scope="MSFT")[0]
    first = _decide(corpus, old_state)
    assert first.inputset_id != old_id
    assert first.reason != "fingerprint_unchanged"

    settled = _decide(corpus, _settled_state(first))
    assert settled.reason == "fingerprint_unchanged"


def test_an_all_filler_window_is_no_corpus_never_a_generation():
    rows = [_row(t, hours=1) for t in FILLER]
    assert _decide(select_recent_corpus(rows, NOW, scope="MSFT")[0], {}).reason == "no_corpus"


# ── 4. The sweep hands the gate the filler-free corpus ───────────────────────


@pytest.fixture
def _fresh_earnings_singleton():
    """`run_sweep` reaches the real earnings-window service through a stub client, which
    stamps a negative TTL on the process singleton; reset so no other test inherits it."""
    from app.services.earnings_window_service import get_earnings_window_service

    get_earnings_window_service().reset()
    yield
    get_earnings_window_service().reset()


class _SweepStub(InsightSweeper):
    """Enough of a sweeper to reach the gate. The gate itself is recorded and answers
    skip, so nothing is claimed, generated or billed."""

    def __init__(self, corpora):
        self.supabase = None
        self.fmp = self
        self.price = PriceFromFMPFake(self.fmp)
        self.vol = self
        self.news = self
        self.insights = self
        self._catalyst_day = self._enrich_day = None
        self._catalyst_count = self._enrich_count = 0
        self._catalyst_scopes = set()
        self._corpora = corpora

    async def _universe(self):
        return [MARKET_SCOPE, "MSFT"]

    def _company_names(self, scopes):
        return {"MSFT": "Microsoft Corporation"}

    def _load_state(self, scopes):
        return {}

    def _record_skips(self, skips, now):
        pass

    async def get_batch_quotes_bulk(self, symbols):
        return []

    async def get_sigmas_bulk(self, symbols):
        return {}

    def get_cached_bulk(self, scopes, limit):
        # Filler included on purpose: the selector must drop it even if a reader upstream
        # (this stub, or a future bulk read) does not.
        return self._corpora

    async def mark_verified_current(self, scopes, market_active):
        return None


def test_the_sweep_gates_and_would_generate_from_a_filler_free_corpus(
    monkeypatch, _fresh_earnings_singleton,
):
    seen = {}

    def _recording_decide(**kwargs):
        seen[kwargs["scope"]] = list(kwargs["corpus"])
        return Decision(action=ACTION_SKIP, reason="no_corpus")

    monkeypatch.setattr(sweeper_mod, "decide", _recording_decide)
    monkeypatch.setattr(sweeper_mod, "is_market_active", lambda: True)
    market = [
        _live("Fed holds rates steady as inflation cools", hours=1, tickers=()),
        _live(SOLICITATIONS[1], hours=1.5, tickers=()),
    ]
    asyncio.run(_SweepStub({MARKET_SCOPE: market, "MSFT": _mixed(_live)}).run_sweep(
        refresh_news=False,
    ))

    assert _titles(seen["MSFT"]) == NEWS
    assert _titles(seen[MARKET_SCOPE]) == ["Fed holds rates steady as inflation cools"]


# ── 5. The bulk read spends its cap on news ──────────────────────────────────


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def select(self, cols):
        return self

    def in_(self, col, values):
        return self

    def gte(self, col, value):
        return self

    def order(self, col, **kw):
        return self

    def range(self, lo, hi):
        return self

    def execute(self):
        rows = self._rows

        class _R:
            data = rows
        return _R()


class _Supabase:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        assert name == "ticker_news_cache"
        return _Query(self._rows)


def _bulk_service(rows):
    svc = object.__new__(NewsCacheService)
    svc.supabase = _Supabase(rows)
    return svc


def test_the_bulk_read_skips_filler_before_its_per_scope_cap():
    """5 of MSFT's and 8 of AAPL's newest 25 cached rows were filler (measured
    2026-10-11). Capping first would hand the card 17-20 stories instead of 25."""
    rows = [{**r, "ticker": "MSFT"} for r in _mixed()]
    grouped = _bulk_service(rows).get_cached_bulk(["MSFT"], 3)
    assert _titles(grouped["MSFT"]) == NEWS[:3]


def test_a_filler_only_scope_comes_back_empty_not_padded():
    rows = [{**_row(t, hours=1), "ticker": "MSFT"} for t in FILLER]
    assert _bulk_service(rows).get_cached_bulk(["MSFT"], 25).get("MSFT", []) == []


def test_the_market_floor_cannot_put_filler_back():
    """The Market corpus re-admits its newest HIDDEN rows when fewer than three market
    stories survive. A solicitation hidden as one company's story must not be one of them;
    an ordinary hidden company story still is (the floor still works)."""
    company = stamp_model_scope(INSIGHT_MODEL, "company")
    solicitation = {**_row(SOLICITATIONS[0], hours=1, tickers=()), "ai_model": company,
                    "ticker": MARKET_SCOPE}
    apple = {**_row("Apple unveils a thinner iPhone", hours=2, tickers=()),
             "ai_model": company, "ticker": MARKET_SCOPE}
    fed = {**_row("Fed holds rates steady as inflation cools", hours=3, tickers=()),
           "ai_model": stamp_model_scope(INSIGHT_MODEL, "market"), "ticker": MARKET_SCOPE}
    assert not is_market_story(solicitation) and not is_market_story(apple), (
        "precondition: both are hidden, so only the floor could re-admit them"
    )
    assert is_market_story(fed)

    grouped = _bulk_service([solicitation, apple, fed]).get_cached_bulk([MARKET_SCOPE], 25)
    assert _titles(grouped[MARKET_SCOPE]) == [
        "Apple unveils a thinner iPhone", "Fed holds rates steady as inflation cools",
    ]


# ── 6. The endpoint: show/hide, fallback and badge agree with the corpus ─────


class _NewsStub:
    def __init__(self, articles):
        self._articles = articles

    async def get_ticker_news(self, scope, **kwargs):
        return {"articles": self._articles, "cached": True}

    async def get_market_news(self, **kwargs):
        return {"articles": self._articles, "cached": True}


class _Insights(NewsInsightService):
    """The REAL fallback builder over a stubbed card read (no Supabase, no Gemini)."""

    def __init__(self, card=None):
        self.supabase = None
        self.gemini = None
        self._cache = {}
        self._inflight = {}
        self._card = card

    async def get_cards(self, scopes):
        return {s: self._card for s in scopes} if self._card else {}


def _ai_card():
    return {
        "scope": "MSFT", "headline": "Microsoft pushes deeper into custom AI silicon",
        "bullets": ["Azure gets new chips.", "OpenAI ties deepen.",
                    "The cloud bet keeps widening."],
        "sentiment": "Bullish", "article_count": 3,
        "generated_at": "2026-10-07T17:00:00Z", "is_stale": False, "refreshing": False,
        "ai_generated": True, "trigger_reason": None, "sources": [],
    }


def _feed(monkeypatch, articles, card=None):
    monkeypatch.setattr(updates_endpoint, "get_news_cache_service", lambda: _NewsStub(articles))
    monkeypatch.setattr(updates_endpoint, "get_news_insight_service", lambda: _Insights(card))
    return asyncio.run(updates_endpoint.get_updates_feed(scope="MSFT", limit=50, offset=0))


@pytest.mark.parametrize("card", [None, "stored"])
def test_a_feed_of_only_filler_shows_no_card_and_still_lists_every_row(monkeypatch, card):
    articles = [_live(t, hours=0.5 + i) for i, t in enumerate(FILLER)]
    resp = _feed(monkeypatch, articles, _ai_card() if card else None)
    assert resp.insight is None, "no news in the window means no card, AI or fallback"
    assert [a.headline for a in resp.articles] == FILLER, "the feed keeps every row"


def test_the_fallback_lists_and_cites_only_real_headlines(monkeypatch):
    articles = _mixed(_live)
    resp = _feed(monkeypatch, articles)

    card = resp.insight
    assert card is not None and card.ai_generated is False
    assert card.bullets == NEWS[:3], "the three newest REAL headlines, not the filler above"
    assert [s.title for s in card.sources] == card.bullets, '"N sources" = the headlines shown'
    assert card.article_count == len(NEWS)
    assert not any(t in card.model_dump_json() for t in FILLER)
    assert len(resp.articles) == len(FILLER) + len(NEWS), "the feed keeps every row"


def test_the_ai_card_badge_is_measured_over_news_not_filler(monkeypatch):
    """Two stories in 24h plus yesterday's: the card the sweeper writes is a 48h card, and
    the endpoint must badge it so — the filler beside them must not make it "24h"."""
    articles = [_live(t, hours=0.3) for t in FILLER] + [
        _live(NEWS[0], hours=2), _live(NEWS[1], hours=6), _live(NEWS[4], hours=30),
    ]
    resp = _feed(monkeypatch, articles, _ai_card())
    assert resp.insight is not None and resp.insight.ai_generated is True
    assert resp.insight.badge == "48h"


# ── 7. The AI card cites and counts only what it summarised ──────────────────


class _Generating(NewsInsightService):
    def __init__(self):
        self.supabase = None
        self.gemini = None
        self._cache = {}
        self._inflight = {}
        self.model_input = None
        self.stored = None

    async def _generate_card(self, scope, articles, *args, **kwargs):
        self.model_input = list(articles)
        return ({"headline": "Microsoft pushes deeper into custom AI silicon",
                 "bullets": ["a.", "b.", "c."], "sentiment": "Bullish"}, None)

    def _store(self, scope, card, inputset_id, trigger_reason, article_count,
               market_active, sources=None):
        self.stored = {"article_count": article_count, "sources": sources}
        return True


@pytest.mark.parametrize("n_news", [1, 5, _MAX_SOURCES + 4])
def test_the_ai_card_is_written_cited_and_counted_from_news_only(n_news):
    news = [_row(f"Microsoft story number {i} on cloud demand", hours=1 + i * 0.5)
            for i in range(n_news)]
    filler = [_row(t, hours=0.1 + i * 0.4) for i, t in enumerate(FILLER * 3)]
    rows = sorted(news + filler, key=lambda r: r["published_at"], reverse=True)
    corpus, _ = select_recent_corpus(rows, NOW, scope="MSFT",
                                     company_name="Microsoft Corporation")

    svc = _Generating()
    card = asyncio.run(svc.generate_and_store(
        scope="MSFT", corpus=corpus, inputset_id="x", price_band=None,
        trigger_reason="new_articles", now=NOW,
    ))
    assert card is not None
    assert _titles(svc.model_input) == _titles(news), "the model reads only the news"
    assert svc.stored["article_count"] == n_news
    cited = [s["title"] for s in svc.stored["sources"]]
    assert len(cited) == min(n_news, _MAX_SOURCES)
    assert set(cited) <= set(_titles(news))


# ── 8. The chat is grounded on the same window ───────────────────────────────


class _ChatInsights:
    def __init__(self, card=None):
        self.card = card

    async def get_cards(self, scopes):
        return {s: self.card for s in scopes}


class _ChatNews:
    def __init__(self, rows):
        self.rows = rows

    def get_cached_bulk(self, scopes, per_scope_limit=25):
        return {s: list(self.rows) for s in scopes}


class _ChatTrend:
    async def get_trend(self, scope, days, *, now=None):
        return {"scope": scope, "days": days, "series": [], "tracking_since": None}


def _chat(monkeypatch, rows, card=None):
    monkeypatch.setattr(insight_mod, "get_news_insight_service", lambda: _ChatInsights(card))
    monkeypatch.setattr(news_mod, "get_news_cache_service", lambda: _ChatNews(rows))
    monkeypatch.setattr(trend_mod, "get_news_sentiment_trend_service", lambda: _ChatTrend())
    return asyncio.run(ChatContextResolver().resolve("UPDATES_SCOPE", "MSFT"))


def test_the_chat_headlines_are_the_cards_window_and_say_what_was_left_out(monkeypatch):
    block = _chat(monkeypatch, _mixed(_live))
    for title in NEWS[:3]:
        assert title in block
    for title in FILLER:
        assert title not in block
    assert "fund-holdings filings and law-firm solicitations left out" in block


def test_a_filler_only_window_does_not_put_the_card_on_screen_in_chat(monkeypatch):
    """The endpoint hides the card over a filler-only window; the chat must not tell the
    model it is on screen."""
    card = {"headline": "Microsoft pushes deeper into custom AI silicon",
            "bullets": ["Azure gets new chips."], "sentiment": "Bullish",
            "generated_at": datetime.now(timezone.utc).isoformat()}
    block = _chat(monkeypatch, [_live(t, hours=1) for t in FILLER], card)
    assert "Cay AI Insights card on screen" not in block
    assert "The most recent Cay AI Insights card (not on screen" in block
    assert not any(t in block for t in FILLER)
