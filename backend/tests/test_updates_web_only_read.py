"""The READ side of a web-only Insights card (2026-11-12, owner: "Web search fills it").

A live equity jumper with no FMP news in its window may get a card written from the Updates
jump search's web pages ALONE. `_row_to_card` marks it server-only (`web_only`), and two
readers must agree on when it is shown:

  * `GET /updates/feed` serves it only on the ET day it was written
    (`web_only_card_visible`), with the badge "24h" (never the recomputed corpus window),
    over an EMPTY window too; on a later day it is hidden — the headline fallback stands in
    when the window has news, nothing when it has none. A plain or MIXED card over an empty
    window stays hidden, as before. Neither marker (`web_only`, `web_assisted`) reaches
    the wire, and `is_stale` is served as read.
  * Ask Cay AI (`UPDATES_SCOPE`) calls the card "on screen" by the SAME helper, labels it as
    coming from web search results (a mixed card keeps "partly from"), and keeps the turn
    out of the audits.

Hermetic: the endpoint and the resolver are called directly with stubbed services.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

import app.api.v1.endpoints.updates as updates_endpoint
import app.services.news_cache_service as news_mod
import app.services.news_insight_service as insight_mod
import app.services.news_sentiment_trend_service as trend_mod
from app.services.chat_context_resolver import ChatContextResolver
from app.services.news_insight_service import (
    WEB_ONLY_BADGE,
    NewsInsightService,
    web_only_card_visible,
)

SCOPE = "JOBY"
HEADLINE = "Joby Aviation outlines a delay to its air taxi launch"
BULLETS = [
    "Joby Aviation said its commercial launch in Dubai moved to next year.",
    "The company cited certification timing with regulators.",
]
WEB_SOURCES = [
    {"title": "Joby pushes back Dubai launch", "url": "https://www.cnbc.com/joby-dubai",
     "publisher": "CNBC", "origin": "web"},
    {"title": "Joby delays air taxi service", "url": "https://www.reuters.com/joby",
     "publisher": "Reuters", "origin": "web"},
]
_PAGE_WORDS = ("Dubai", "certification", "air taxi", "Joby pushes back")


@pytest.fixture(autouse=True)
def _fresh_hide_memo():
    # The once-per-card hide warning is process-local: every test starts with none logged.
    updates_endpoint._web_only_hide_logged.clear()
    yield
    updates_endpoint._web_only_hide_logged.clear()


@pytest.fixture(autouse=True)
def _web_switch_on(monkeypatch):
    # `_row_to_card` withholds every web-sourced card while the switch is off.
    from app.config import settings
    monkeypatch.setattr(settings, "UPDATES_JUMP_WEB_SEARCH_ENABLED", True)


def _stored(*, generated_at, reason="band Notable->Unusual (-8.25%) + web_only",
            sources=None):
    """A card as production serves it: a DB row through the REAL `_row_to_card`."""
    svc = NewsInsightService.__new__(NewsInsightService)
    card = svc._row_to_card({
        "scope": SCOPE, "headline": HEADLINE, "bullets": list(BULLETS),
        "sentiment": "bearish", "article_count": 0,
        "generated_at": generated_at.isoformat(), "prompt_version": 8,
        "trigger_reason": reason,
        "sources": WEB_SOURCES if sources is None else sources,
    }, market_active=False)
    assert card is not None
    return card


def _now():
    return datetime.now(timezone.utc)


def _today_web_only():
    card = _stored(generated_at=_now())
    assert card["web_only"] is True and card["web_assisted"] is True
    return card


def _aged_web_only(hours=26):
    # 26 h is past any ET calendar day (23-25 h across DST), so never "today".
    card = _stored(generated_at=_now() - timedelta(hours=hours))
    assert card["web_only"] is True
    return card


def _mixed_today():
    """Web pages AND an FMP story: web-assisted, never web-only (even if tagged)."""
    card = _stored(generated_at=_now(), sources=[
        WEB_SOURCES[0],
        {"title": "Joby files 8-K", "url": "https://fmp/x", "publisher": "Benzinga"},
    ])
    assert card.get("web_only") is None and card["web_assisted"] is True
    return card


def _plain_today():
    card = _stored(generated_at=_now(), reason="new_articles (3)", sources=[
        {"title": "Joby files 8-K", "url": "https://fmp/x", "publisher": "Benzinga"},
    ])
    assert "web_only" not in card and "web_assisted" not in card
    return card


def _article(headline, tickers, *, hours_ago=1.0, i=0):
    return {
        "id": f"a-{i}-{hours_ago}", "headline": headline, "summary": "body",
        "summary_bullets": [], "sentiment": None, "sentiment_confidence": None,
        "source_name": "Benzinga", "source_logo_url": None,
        "published_at": (_now() - timedelta(hours=hours_ago, minutes=i)).isoformat(),
        "thumbnail_url": None, "article_url": f"https://n/{i}-{hours_ago}",
        "related_tickers": list(tickers), "ai_processed": False,
    }


SUBJECT_ROWS = [_article(f"Joby Aviation update number {i}", [SCOPE], i=i) for i in range(4)]
# Peer coverage only: FMP tags another lead ticker, so the SUBJECT window is empty while the
# FEED window is not (the PLUG round-up shape).
PEER_ROWS = [
    _article(f"Archer Aviation, Joby and eVTOL peers roundup {i}", ["ACHR", "EH", SCOPE], i=i)
    for i in range(3)
]


class _News:
    def __init__(self, rows):
        self.rows = rows

    async def get_ticker_news(self, scope, **kwargs):
        return {"articles": list(self.rows), "cached": True, "cache_age_seconds": 5}

    async def get_market_news(self, **kwargs):
        return {"articles": list(self.rows), "cached": True, "cache_age_seconds": 5}

    # The chat resolver reads the same rows from the cache tier.
    def get_cached_bulk(self, scopes, per_scope_limit=25):
        return {s: list(self.rows) for s in scopes}


class _Insights:
    def __init__(self, card):
        self.card = card
        self.fallback_calls = []

    async def get_cards(self, scopes):
        return {s: self.card for s in scopes} if self.card is not None else {}

    def build_fallback_card(self, scope, corpus):
        self.fallback_calls.append(list(corpus))
        return {
            "scope": scope, "headline": f"Latest {scope} headlines",
            "bullets": [r["headline"] for r in corpus][:3],
            "sentiment": "Neutral", "badge": "Latest headlines",
            "article_count": len(corpus), "generated_at": _now().isoformat(),
            "is_stale": False, "refreshing": False, "ai_generated": False,
            "trigger_reason": None, "sources": [],
        }


def _feed(monkeypatch, card, rows, *, offset=0):
    insights = _Insights(card)
    monkeypatch.setattr(updates_endpoint, "get_news_cache_service", lambda: _News(rows))
    monkeypatch.setattr(updates_endpoint, "get_news_insight_service", lambda: insights)
    resp = asyncio.run(
        updates_endpoint.get_updates_feed(scope=SCOPE, limit=50, offset=offset)
    )
    return resp, insights


def _wire(resp) -> dict:
    return json.loads(resp.model_dump_json())


# ── feed: a visible (today's) web-only card ──────────────────────────────────────

def test_an_empty_feed_serves_todays_web_only_card_badged_24h(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=updates_endpoint.__name__)
    resp, insights = _feed(monkeypatch, _today_web_only(), [])
    assert resp.insight is not None
    assert resp.insight.headline == HEADLINE
    assert resp.insight.badge == WEB_ONLY_BADGE == "24h"
    assert resp.insight.ai_generated is True
    assert [s.url for s in resp.insight.sources] == [s["url"] for s in WEB_SOURCES]
    assert insights.fallback_calls == [], "an empty window builds no fallback"
    assert any("insight=ai_web_only" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("rows", [[], SUBJECT_ROWS, PEER_ROWS], ids=["empty", "subject", "peer"])
def test_neither_server_only_marker_reaches_the_wire(monkeypatch, rows):
    resp, _ = _feed(monkeypatch, _today_web_only(), rows)
    wire = _wire(resp)
    assert wire["insight"] is not None
    # As KEYS, at any depth (the trigger_reason analytics string may say "web_only").
    def _keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from _keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from _keys(v)

    keys = set(_keys(wire))
    assert "web_only" not in keys and "web_assisted" not in keys
    # `origin` stays server-side too (the sanitizer never emits it).
    assert "origin" not in keys


def test_a_web_only_card_over_a_subject_window_keeps_24h_not_the_recomputed_window(
    monkeypatch,
):
    """30-hour-old subject stories would recompute to "48h". The web-only card summarised the
    pages, not that corpus: its badge stays "24h"."""
    rows = [_article(f"Joby Aviation older story {i}", [SCOPE], hours_ago=30, i=i)
            for i in range(4)]
    resp, insights = _feed(monkeypatch, _today_web_only(), rows)
    assert resp.insight is not None and resp.insight.ai_generated is True
    assert resp.insight.badge == "24h"
    assert insights.fallback_calls == []


def test_a_plain_card_over_the_same_window_still_gets_the_recompute(monkeypatch):
    """Anti-vacuity for the test above: without the web-only branch the badge moves."""
    rows = [_article(f"Joby Aviation older story {i}", [SCOPE], hours_ago=30, i=i)
            for i in range(4)]
    resp, _ = _feed(monkeypatch, _plain_today(), rows)
    assert resp.insight is not None and resp.insight.badge == "48h"


@pytest.mark.parametrize("stale", [True, False])
def test_is_stale_is_served_as_read_never_forced(monkeypatch, stale):
    card = {**_today_web_only(), "is_stale": stale}
    resp, _ = _feed(monkeypatch, card, [])
    assert resp.insight is not None and resp.insight.is_stale is stale


# ── feed: an aged / unreadable web-only card is hidden ──────────────────────────

def test_an_empty_feed_hides_an_aged_web_only_card_and_says_so(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=updates_endpoint.__name__)
    resp, insights = _feed(monkeypatch, _aged_web_only(), [])
    assert resp.insight is None
    assert insights.fallback_calls == []
    hides = [r for r in caplog.records
             if r.levelno == logging.WARNING and "hiding web-only card" in r.getMessage()]
    assert len(hides) == 1
    msg = hides[0].getMessage()
    assert f"scope={SCOPE}" in msg and "age_h=26." in msg
    # Scope and age only: no card text, no page title, no URL in any log line.
    for rec in caplog.records:
        line = rec.getMessage()
        assert HEADLINE not in line and "cnbc" not in line.lower()
        assert not any(word in line for word in _PAGE_WORDS)
    assert any("insight=none" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("rows", [SUBJECT_ROWS, PEER_ROWS], ids=["subject", "peer_only"])
def test_an_aged_web_only_card_over_news_gives_way_to_the_headline_fallback(
    monkeypatch, rows,
):
    resp, insights = _feed(monkeypatch, _aged_web_only(), rows)
    assert resp.insight is not None
    assert resp.insight.ai_generated is False
    assert resp.insight.headline == f"Latest {SCOPE} headlines"
    assert resp.insight.headline != HEADLINE
    assert len(insights.fallback_calls) == 1
    assert len(insights.fallback_calls[0]) == len(rows)


@pytest.mark.parametrize("generated_at", [None, "", "not a date", 12345, "2026-13-45T99:00:00Z"])
@pytest.mark.parametrize("rows", [[], SUBJECT_ROWS], ids=["empty", "subject"])
def test_a_web_only_card_with_an_unreadable_time_is_never_shown(monkeypatch, rows, generated_at):
    card = {**_today_web_only(), "generated_at": generated_at}
    resp, insights = _feed(monkeypatch, card, rows)
    if rows:
        assert resp.insight is not None and resp.insight.ai_generated is False
        assert len(insights.fallback_calls) == 1
    else:
        assert resp.insight is None


def test_a_web_only_card_dated_in_the_future_is_hidden(monkeypatch):
    card = {**_today_web_only(), "generated_at": (_now() + timedelta(hours=2)).isoformat()}
    resp, _ = _feed(monkeypatch, card, [])
    assert resp.insight is None


def test_a_web_only_card_within_clock_skew_is_shown(monkeypatch):
    card = {**_today_web_only(), "generated_at": (_now() + timedelta(minutes=2)).isoformat()}
    if not web_only_card_visible(card, _now()):
        pytest.skip("ET midnight falls inside the skew allowance right now")
    resp, _ = _feed(monkeypatch, card, [])
    assert resp.insight is not None and resp.insight.badge == "24h"


# ── feed: everything that is NOT web-only over an empty window stays hidden ─────

@pytest.mark.parametrize("make", [_mixed_today, _plain_today], ids=["mixed", "plain"])
def test_a_non_web_only_card_over_an_empty_feed_stays_hidden(monkeypatch, make):
    resp, insights = _feed(monkeypatch, make(), [])
    assert resp.insight is None
    assert insights.fallback_calls == []


@pytest.mark.parametrize("marker", ["true", 1, "yes", None, False])
def test_a_marker_that_is_not_exactly_true_is_not_web_only(monkeypatch, marker):
    """Only the boolean True `_row_to_card` writes counts: a truthy look-alike over an empty
    feed stays hidden (fail closed) and is never badged 24h."""
    card = {**_today_web_only(), "web_only": marker}
    resp, _ = _feed(monkeypatch, card, [])
    assert resp.insight is None


@pytest.mark.parametrize("junk", ["a string", 42, ["list"]])
def test_a_card_that_is_not_a_dict_degrades_to_the_fallback(monkeypatch, junk):
    resp, insights = _feed(monkeypatch, junk, SUBJECT_ROWS)
    assert resp.insight is not None and resp.insight.ai_generated is False
    assert len(insights.fallback_calls) == 1


def test_page_two_never_carries_a_web_only_card(monkeypatch):
    resp, _ = _feed(monkeypatch, _today_web_only(), [], offset=50)
    assert resp.insight is None


# ── Ask Cay AI agrees with the feed ──────────────────────────────────────────────

class _Trend:
    async def get_trend(self, scope, days, *, now=None):
        return {"scope": scope, "days": days, "series": [], "tracking_since": None}


def _install_chat(monkeypatch, card, rows):
    monkeypatch.setattr(insight_mod, "get_news_insight_service", lambda: _Insights(card))
    monkeypatch.setattr(news_mod, "get_news_cache_service", lambda: _News(rows))
    monkeypatch.setattr(trend_mod, "get_news_sentiment_trend_service", lambda: _Trend())


async def _chat(monkeypatch, card, rows):
    _install_chat(monkeypatch, card, rows)
    meta: dict = {}
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", SCOPE, meta=meta)
    return block, meta


@pytest.mark.asyncio
async def test_chat_calls_todays_web_only_card_on_screen_over_an_empty_feed(monkeypatch):
    block, meta = await _chat(monkeypatch, _today_web_only(), [])
    assert "Cay AI Insights card on screen" in block
    assert " from web search results (third-party pages, not Caydex data)" in block
    assert "partly from" not in block
    assert HEADLINE in block
    assert meta == {"web_assisted_card": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], SUBJECT_ROWS, PEER_ROWS], ids=["empty", "subject", "peer"])
async def test_chat_calls_an_aged_web_only_card_off_screen_whatever_the_window(
    monkeypatch, rows,
):
    block, meta = await _chat(monkeypatch, _aged_web_only(), rows)
    assert "Cay AI Insights card on screen" not in block
    assert ("came from web search results and is not on screen: such cards show only on "
            "the day they are written") in block
    assert meta == {"web_assisted_card": True}, "the audits stay skipped"
    if rows:
        assert "No Cay AI summary is on screen today" in block
        assert '"Latest headlines" list' in block


@pytest.mark.asyncio
async def test_chat_keeps_partly_for_a_mixed_card(monkeypatch):
    block, meta = await _chat(monkeypatch, _mixed_today(), SUBJECT_ROWS)
    assert "Cay AI Insights card on screen" in block
    assert "partly from web search results (third-party pages, not Caydex data)" in block
    assert meta == {"web_assisted_card": True}


@pytest.mark.asyncio
async def test_chat_names_no_vendor(monkeypatch):
    for card in (_today_web_only(), _aged_web_only(), _mixed_today()):
        for rows in ([], SUBJECT_ROWS):
            block, _ = await _chat(monkeypatch, card, rows)
            for vendor in ("Brave", "Google", "Gemini", "OpenAI"):
                assert vendor not in block


def _cases():
    return [
        ("web_only_today", _today_web_only),
        ("web_only_aged", _aged_web_only),
        ("web_only_no_time", lambda: {**_today_web_only(), "generated_at": None}),
        ("mixed_today", _mixed_today),
        ("plain_today", _plain_today),
    ]


@pytest.mark.parametrize("name,make", _cases(), ids=[c[0] for c in _cases()])
@pytest.mark.parametrize("rows", [[], SUBJECT_ROWS, PEER_ROWS], ids=["empty", "subject", "peer"])
def test_chat_on_screen_matches_what_the_feed_serves(monkeypatch, name, make, rows):
    """The parity the shared helper exists for: chat says "on screen" exactly when the feed
    serves the stored card (not the headline fallback, not nothing)."""
    card = make()
    resp, _ = _feed(monkeypatch, card, rows)
    feed_shows_card = resp.insight is not None and resp.insight.ai_generated is True
    block, _ = asyncio.run(_chat(monkeypatch, card, rows))
    assert ("Cay AI Insights card on screen" in (block or "")) is feed_shows_card, name


# ── review round: an aged web-only card's TEXT never reaches the model ────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], SUBJECT_ROWS, PEER_ROWS], ids=["empty", "subject", "peer"])
@pytest.mark.parametrize("make", [
    _aged_web_only,
    lambda: _aged_web_only(hours=90),                           # still readable to ~96 h
    lambda: {**_today_web_only(), "generated_at": None},        # unreadable time
    lambda: {**_today_web_only(), "generated_at": "not a date"},
    lambda: {**_today_web_only(),
             "generated_at": (_now() + timedelta(hours=6)).isoformat()},  # future
], ids=["aged26", "aged90", "no_time", "junk_time", "future"])
async def test_chat_gets_one_line_and_no_text_for_a_hidden_web_only_card(
    monkeypatch, rows, make,
):
    """Owner rule: web-only cards are visible the same ET day only — so chat on a later day
    is told such a card exists, and never its headline, sentiment or bullets."""
    block, meta = await _chat(monkeypatch, make(), rows)
    assert block is not None
    assert HEADLINE not in block
    for bullet in BULLETS:
        assert bullet not in block
        assert bullet[:30] not in block
    assert not any(word in block for word in _PAGE_WORDS)
    assert "sentiment bearish" not in block.lower() and "— sentiment" not in block
    assert "•" not in block, "no card bullet line at all"
    hidden = [ln for ln in block.splitlines() if "came from web search results" in ln]
    assert len(hidden) == 1, "exactly one line about the hidden card"
    assert "Cay AI Insights card on screen" not in block
    assert meta == {"web_assisted_card": True}, "the audits stay skipped"
    for row in rows[:3]:
        assert row["headline"] in block, "the window's own headlines still ground the turn"


@pytest.mark.asyncio
async def test_chat_still_gets_todays_web_only_card_text(monkeypatch):
    """Anti-vacuity twin: the same card on its own ET day keeps its headline and bullets."""
    block, _ = await _chat(monkeypatch, _today_web_only(), SUBJECT_ROWS)
    assert HEADLINE in block
    assert all(b[:30] in block for b in BULLETS)


# ── review round: the hide warning is logged once per card, not per request ──────

def _hide_warnings(caplog):
    return [r for r in caplog.records
            if r.levelno == logging.WARNING and "hiding web-only card" in r.getMessage()]


@pytest.mark.parametrize("rows", [[], SUBJECT_ROWS], ids=["empty", "subject"])
def test_the_hide_warning_fires_once_per_card_across_repolls(monkeypatch, caplog, rows):
    caplog.set_level(logging.DEBUG, logger=updates_endpoint.__name__)
    card = _aged_web_only()
    for _ in range(5):
        resp, _ = _feed(monkeypatch, card, rows)
        assert resp.insight is None or resp.insight.ai_generated is False
    assert len(_hide_warnings(caplog)) == 1
    later = [r for r in caplog.records if "still hiding web-only card" in r.getMessage()]
    assert len(later) == 4 and all(r.levelno == logging.DEBUG for r in later)
    for rec in caplog.records:
        assert HEADLINE not in rec.getMessage()


def test_a_new_card_or_another_scope_warns_again(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=updates_endpoint.__name__)
    first, second = _aged_web_only(hours=26), _aged_web_only(hours=30)
    assert first["generated_at"] != second["generated_at"]
    _feed(monkeypatch, first, [])
    _feed(monkeypatch, first, [])
    _feed(monkeypatch, second, [])          # regenerated: a new generated_at
    assert len(_hide_warnings(caplog)) == 2
    # Another scope holding a card with the same timestamp is its own key.
    assert updates_endpoint._first_web_only_hide("ACHR", first) is True
    assert updates_endpoint._first_web_only_hide("ACHR", first) is False


def test_the_hide_memo_is_bounded(monkeypatch):
    cap = updates_endpoint._WEB_ONLY_HIDE_LOG_CAP
    for i in range(cap + 10):
        updates_endpoint._first_web_only_hide("S", {"generated_at": f"t{i}"})
        assert len(updates_endpoint._web_only_hide_logged) <= cap
    # An unreadable / missing time still yields one key, never a crash.
    assert updates_endpoint._first_web_only_hide("S", {}) is True
    assert updates_endpoint._first_web_only_hide("S", {"generated_at": None}) is False


# ── review round: a failing card read logs no card text ──────────────────────────

@pytest.mark.parametrize("broken", [
    {"article_count": HEADLINE},               # int field holding page text
    {"bullets": HEADLINE},                     # list field holding page text
    {"headline": None, "sentiment": [HEADLINE]},
], ids=["count", "bullets", "headline_sentiment"])
def test_a_malformed_card_logs_fields_never_its_text(monkeypatch, caplog, broken):
    caplog.set_level(logging.DEBUG, logger=updates_endpoint.__name__)
    card = {**_today_web_only(), **broken}
    resp, _ = _feed(monkeypatch, card, [])
    assert resp.insight is None, "a card that does not validate is never served"
    fails = [r for r in caplog.records if "Updates insight read failed" in r.getMessage()]
    assert len(fails) == 1
    msg = fails[0].getMessage()
    assert f"scope={SCOPE}" in msg and "ValidationError" in msg
    for field in broken:
        assert field in msg, "the failing field is named"
    assert "errors=" in msg
    assert fails[0].exc_info is None, "no traceback: its message would carry the input"
    for rec in caplog.records:
        line = rec.getMessage()
        assert HEADLINE not in line
        assert "input_value" not in line
        assert not any(word in line for word in _PAGE_WORDS)


def test_a_non_validation_failure_logs_the_class_and_site_not_the_message(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=updates_endpoint.__name__)

    def boom(*_a, **_k):
        raise ValueError(f"leaked {HEADLINE}")

    monkeypatch.setattr(updates_endpoint, "AIInsightCardResponse", boom)
    resp, _ = _feed(monkeypatch, _today_web_only(), [])
    assert resp.insight is None
    fails = [r for r in caplog.records if "Updates insight read failed" in r.getMessage()]
    assert len(fails) == 1
    msg = fails[0].getMessage()
    assert "ValueError" in msg and " in boom" in msg
    assert HEADLINE not in msg and "leaked" not in msg
