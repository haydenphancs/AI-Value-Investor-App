"""The READ side of a move card (2026-11-27: a big mover's Insights card says WHY it moved).

A move card's stored ``trigger_reason`` leads with ``move:<direction>:<kind>`` (`insight_move`),
and `NewsInsightService._row_to_card` turns that into the SERVER-ONLY marker
``card["move"] = {"kind", "direction"}``. Two readers act on it:

  * `GET /updates/feed` badges a move card written on TODAY's ET date by its own window —
    ``max(24, cited_window_floor)`` — instead of the subject window recomputed from the feed,
    which read "72h" on a Monday morning because a thin weekend widened it back to Friday
    (CRWV 2026-11-23). A no-cause card ("no news explains the move yet") written on an EARLIER
    ET day is hidden, so the headline fallback shows — its "yet" and its move were that day's.
    A company or market card from an earlier day is an ordinary card with the ordinary badge.
    The marker never reaches the wire (no new key: old iOS builds decode unchanged).
  * Ask Cay AI (`UPDATES_SCOPE`) is told what kind of move card is on screen, and treats an
    earlier-day no-cause card as not on screen, exactly as the feed does.

Hermetic: the endpoint and the resolver are called directly with stubbed services; every card
text below is hand-written (a fictional company), never a production card or a web page.
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
from app.schemas.updates import AIInsightCardResponse
from app.services.chat_context_resolver import ChatContextResolver
from app.services.insight_move import KIND_COMPANY, KIND_MARKET, KIND_NONE, MoveTag
from app.services.news_insight_service import NewsInsightService, move_card_today

from test_updates_schema_parity import IOS_INSIGHT_KEYS

SCOPE = "KLMN"
NAME = "Kelmon Robotics"
CAUSE_HEADLINE = "Kelmon Robotics signs a warehouse automation contract with a grocer"
MARKET_HEADLINE = "Kelmon Robotics moves with the broader market after a jobs report"
NONE_HEADLINE = "No news yet explains Kelmon Robotics' move"
BULLETS = [
    "Kelmon Robotics said a regional grocer will deploy its picking arms in two warehouses.",
    "The company described the agreement as its largest retail order so far.",
]

# Monday 2026-11-23 10:33 ET (15:33 UTC): the last completed close is Friday 16:00 ET, so a
# thin subject window widens to 72h — the Monday morning the CRWV card read "72h".
MONDAY = datetime(2026, 11, 23, 15, 33, tzinfo=timezone.utc)
MONDAY_CARD_AT = datetime(2026, 11, 23, 14, 50, tzinfo=timezone.utc)      # 09:50 ET
TUESDAY = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)               # 11:00 ET
MONDAY_AFTERNOON = datetime(2026, 11, 23, 19, 0, tzinfo=timezone.utc)      # 14:00 ET


@pytest.fixture(autouse=True)
def _fresh_hide_memos():
    # The once-per-card hide warnings are process-local: every test starts with none logged.
    updates_endpoint._web_only_hide_logged.clear()
    updates_endpoint._move_none_hide_logged.clear()
    yield
    updates_endpoint._web_only_hide_logged.clear()
    updates_endpoint._move_none_hide_logged.clear()


def _url(tag: str) -> str:
    return f"https://news.example.com/kelmon/{tag}"


def _stored(*, generated_at: datetime, reason: str, headline: str = CAUSE_HEADLINE,
            sentiment: str = "bullish", cited=("fresh",), scope: str = SCOPE):
    """A card as production serves it: a DB row through the REAL `_row_to_card`."""
    svc = NewsInsightService.__new__(NewsInsightService)
    card = svc._row_to_card({
        "scope": scope, "headline": headline, "bullets": list(BULLETS),
        "sentiment": sentiment, "article_count": 3,
        "generated_at": generated_at.isoformat(), "prompt_version": 8,
        "trigger_reason": reason,
        "sources": [
            {"title": f"Kelmon Robotics story {tag}", "url": _url(tag), "publisher": "Benzinga"}
            for tag in cited
        ],
    }, market_active=False)
    assert card is not None, "the fixture card must be servable"
    return card


def _company(at, **kw):
    card = _stored(generated_at=at, reason="move:up:company + new_articles (3)", **kw)
    assert card["move"] == {"kind": KIND_COMPANY, "direction": "up"}
    return card


def _market(at, **kw):
    card = _stored(generated_at=at, headline=MARKET_HEADLINE, sentiment="bearish",
                   reason="move:down:market + band Notable->Unusual (-7.10%)", **kw)
    assert card["move"] == {"kind": KIND_MARKET, "direction": "down"}
    return card


def _none(at, **kw):
    card = _stored(generated_at=at, headline=NONE_HEADLINE, sentiment="neutral",
                   reason="move:up:none + new_articles (2)", **kw)
    assert card["move"] == {"kind": KIND_NONE, "direction": "up"}
    return card


def _plain(at, **kw):
    card = _stored(generated_at=at, reason="new_articles (3)", **kw)
    assert "move" not in card
    return card


def _article(tag, *, at: datetime, about=SCOPE, name=NAME):
    return {
        "id": f"a-{tag}", "headline": f"{name} story {tag}", "summary": "body",
        "summary_bullets": [], "sentiment": None, "sentiment_confidence": None,
        "source_name": "Benzinga", "source_logo_url": None,
        "published_at": at.isoformat(), "thumbnail_url": None,
        "article_url": _url(tag), "related_tickers": [about], "ai_processed": False,
    }


def _monday_rows():
    """One subject story this morning and two from Friday afternoon: the 24h and 48h windows
    hold one story each, the 72h stretch across the closed weekend holds three."""
    return [
        _article("fresh", at=MONDAY - timedelta(hours=3)),
        _article("fri1", at=MONDAY - timedelta(hours=68)),
        _article("fri2", at=MONDAY - timedelta(hours=69)),
    ]


class _News:
    def __init__(self, rows):
        self.rows = rows

    async def get_ticker_news(self, scope, **kwargs):
        return {"articles": list(self.rows), "cached": True, "cache_age_seconds": 5}

    async def get_market_news(self, **kwargs):
        return {"articles": list(self.rows), "cached": True, "cache_age_seconds": 5}

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
            "article_count": len(corpus), "generated_at": MONDAY.isoformat(),
            "is_stale": False, "refreshing": False, "ai_generated": False,
            "trigger_reason": None, "sources": [],
        }


def _frozen_at(monkeypatch, instant: datetime) -> None:
    """The endpoint reads its clock as `datetime.now(timezone.utc)`; freeze it at `instant`."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is None else instant.astimezone(tz)

    monkeypatch.setattr(updates_endpoint, "datetime", _Frozen)


def _feed(monkeypatch, card, rows, *, at: datetime = MONDAY, offset=0, scope=SCOPE):
    _frozen_at(monkeypatch, at)
    insights = _Insights(card)
    monkeypatch.setattr(updates_endpoint, "get_news_cache_service", lambda: _News(rows))
    monkeypatch.setattr(updates_endpoint, "get_news_insight_service", lambda: insights)
    resp = asyncio.run(
        updates_endpoint.get_updates_feed(scope=scope, limit=50, offset=offset)
    )
    return resp, insights


def _wire_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _wire_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _wire_keys(v)


# ── the writer's seams this reader relies on ─────────────────────────────────────

def test_move_card_today_reads_the_marker_on_its_own_et_day_only():
    today = _company(MONDAY_CARD_AT)
    assert move_card_today(today, MONDAY) == MoveTag(kind=KIND_COMPANY, direction="up")
    assert move_card_today(today, TUESDAY) is None, "another ET day is never today's move"
    assert move_card_today(_plain(MONDAY_CARD_AT), MONDAY) is None, "a general card"


@pytest.mark.parametrize("reason", [
    "web_only + move:up:company",          # not leading
    "move:sideways:company",               # unknown direction
    "move:up:rumour",                      # unknown kind
    "moves:up:company",
    "",
])
def test_the_marker_is_only_for_a_valid_leading_tag(reason):
    card = _stored(generated_at=MONDAY_CARD_AT, reason=reason)
    assert "move" not in card
    assert move_card_today(card, MONDAY) is None


# ── feed: the badge of a same-day move card ──────────────────────────────────────

def test_a_monday_move_card_is_badged_24h_not_the_widened_weekend_window(monkeypatch):
    resp, insights = _feed(monkeypatch, _company(MONDAY_CARD_AT), _monday_rows())
    assert resp.insight is not None and resp.insight.ai_generated is True
    assert resp.insight.headline == CAUSE_HEADLINE
    assert resp.insight.badge == "24h"
    assert insights.fallback_calls == []


def test_the_same_card_without_its_move_tag_keeps_the_recomputed_72h(monkeypatch):
    """Anti-vacuity for the test above: the 72h IS what the recompute gives this window."""
    resp, _ = _feed(monkeypatch, _plain(MONDAY_CARD_AT), _monday_rows())
    assert resp.insight is not None and resp.insight.badge == "72h"


@pytest.mark.parametrize("cited, badge", [
    (("fresh",), "24h"),
    (("sun",), "48h"),          # a cited story 30 h old: the card covers 48h, never "24h"
    (("fri1",), "72h"),         # a cited Friday story: the honest floor is 72h
    (("elsewhere",), "24h"),    # cited URL not in the feed: nothing to floor on
    ((), "24h"),                # no sources at all
])
def test_a_move_card_badge_is_floored_by_what_it_cites(monkeypatch, cited, badge):
    rows = _monday_rows() + [_article("sun", at=MONDAY - timedelta(hours=30))]
    resp, _ = _feed(monkeypatch, _company(MONDAY_CARD_AT, cited=cited), rows)
    assert resp.insight is not None and resp.insight.badge == badge


@pytest.mark.parametrize("make", [_market, _none], ids=["market", "none"])
def test_every_kind_of_same_day_move_card_gets_the_move_badge(monkeypatch, make):
    resp, _ = _feed(monkeypatch, make(MONDAY_CARD_AT), _monday_rows())
    assert resp.insight is not None and resp.insight.ai_generated is True
    assert resp.insight.badge == "24h"


def test_a_same_day_move_card_over_peer_only_coverage_is_badged_24h(monkeypatch):
    """The subject window is empty (peer wraps only): a plain card keeps the schema default
    "48h"; a move card written today states its own window."""
    rows = [_article(f"peer{i}", at=MONDAY - timedelta(hours=2 + i), about="ACME")
            for i in range(3)]
    for row in rows:
        row["related_tickers"] = ["ACME", "BOLT", SCOPE]
        row["headline"] = f"Warehouse robot makers roundup {row['id']}"
    move, _ = _feed(monkeypatch, _company(MONDAY_CARD_AT), rows)
    plain, _ = _feed(monkeypatch, _plain(MONDAY_CARD_AT), rows)
    assert move.insight is not None and move.insight.badge == "24h"
    assert plain.insight is not None and plain.insight.badge == "48h"


# ── feed: a move card from an earlier ET day ─────────────────────────────────────

def _tuesday_rows():
    # Four subject stories 30 h old: the recompute gives 48h.
    return [_article(f"mon{i}", at=TUESDAY - timedelta(hours=30, minutes=i)) for i in range(4)]


@pytest.mark.parametrize("make", [_company, _market], ids=["company", "market"])
def test_yesterdays_cause_card_is_an_ordinary_card_with_the_ordinary_badge(monkeypatch, make):
    resp, insights = _feed(monkeypatch, make(MONDAY_AFTERNOON), _tuesday_rows(), at=TUESDAY)
    assert resp.insight is not None and resp.insight.ai_generated is True
    assert resp.insight.badge == "48h", "never the move badge on a later day"
    assert insights.fallback_calls == []


def test_an_older_no_cause_card_is_hidden_and_the_headline_fallback_shows(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=updates_endpoint.__name__)
    rows = _tuesday_rows()
    resp, insights = _feed(monkeypatch, _none(MONDAY_AFTERNOON), rows, at=TUESDAY)
    assert resp.insight is not None
    assert resp.insight.ai_generated is False
    assert resp.insight.headline == f"Latest {SCOPE} headlines"
    assert len(insights.fallback_calls) == 1
    hides = [r for r in caplog.records
             if r.levelno == logging.WARNING and "hiding no-cause move card" in r.getMessage()]
    assert len(hides) == 1
    msg = hides[0].getMessage()
    assert f"scope={SCOPE}" in msg and "age_h=21.0" in msg
    # Scope and age only: never the card's text.
    for rec in caplog.records:
        line = rec.getMessage()
        assert NONE_HEADLINE not in line and NAME not in line
        assert not any(b[:30] in line for b in BULLETS)


def test_an_older_no_cause_card_over_an_empty_feed_shows_nothing(monkeypatch):
    resp, insights = _feed(monkeypatch, _none(MONDAY_AFTERNOON), [], at=TUESDAY)
    assert resp.insight is None
    assert insights.fallback_calls == []


def test_todays_no_cause_card_is_shown(monkeypatch):
    """Anti-vacuity twin of the hide: the same card on its own ET day is served."""
    resp, insights = _feed(monkeypatch, _none(MONDAY_CARD_AT), _monday_rows())
    assert resp.insight is not None and resp.insight.headline == NONE_HEADLINE
    assert resp.insight.sentiment == "Neutral"
    assert insights.fallback_calls == []


@pytest.mark.parametrize("generated_at", [None, "", "not a date", 12345])
def test_a_no_cause_card_with_an_unreadable_time_is_never_shown(monkeypatch, generated_at):
    card = {**_none(MONDAY_CARD_AT), "generated_at": generated_at}
    resp, insights = _feed(monkeypatch, card, _monday_rows())
    assert resp.insight is not None and resp.insight.ai_generated is False
    assert len(insights.fallback_calls) == 1


def test_a_no_cause_card_dated_in_the_future_is_hidden(monkeypatch):
    card = {**_none(MONDAY_CARD_AT), "generated_at": (MONDAY + timedelta(hours=2)).isoformat()}
    resp, _ = _feed(monkeypatch, card, _monday_rows())
    assert resp.insight is not None and resp.insight.ai_generated is False


def test_the_no_cause_hide_warning_fires_once_per_card(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=updates_endpoint.__name__)
    card = _none(MONDAY_AFTERNOON)
    for _ in range(4):
        _feed(monkeypatch, card, _tuesday_rows(), at=TUESDAY)
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "hiding no-cause move card" in r.getMessage()]
    later = [r for r in caplog.records if "still hiding no-cause move card" in r.getMessage()]
    assert len(warns) == 1 and len(later) == 3
    assert all(r.levelno == logging.DEBUG for r in later)


@pytest.mark.parametrize("marker", [
    {"kind": "none"},                                   # no direction
    {"kind": "none", "direction": "sideways"},
    {"kind": "nothing", "direction": "up"},
    "none",
    None,
])
def test_a_malformed_marker_is_never_read_as_a_no_cause_card(monkeypatch, marker):
    """Fail toward the ORDINARY card: only a valid marker hides or re-badges anything."""
    card = {**_plain(MONDAY_AFTERNOON), "move": marker}
    resp, insights = _feed(monkeypatch, card, _tuesday_rows(), at=TUESDAY)
    assert resp.insight is not None and resp.insight.ai_generated is True
    assert resp.insight.badge == "48h"
    assert insights.fallback_calls == []


# ── feed: the marker never reaches the wire ──────────────────────────────────────

@pytest.mark.parametrize("make", [_company, _market, _none], ids=["company", "market", "none"])
def test_the_move_marker_never_reaches_the_wire(monkeypatch, make):
    resp, _ = _feed(monkeypatch, make(MONDAY_CARD_AT), _monday_rows())
    wire = json.loads(resp.model_dump_json())
    assert wire["insight"] is not None
    assert set(wire["insight"]) == IOS_INSIGHT_KEYS == set(AIInsightCardResponse.model_fields)
    assert "move" not in set(_wire_keys(wire))
    # The tag still rides the analytics string iOS decodes and never renders (no new key).
    assert wire["insight"]["trigger_reason"].startswith("move:")


def test_move_is_a_server_only_key():
    assert "move" in updates_endpoint._SERVER_ONLY_CARD_KEYS
    assert {"web_only", "web_assisted"} <= updates_endpoint._SERVER_ONLY_CARD_KEYS


# ── Ask Cay AI agrees with the feed ──────────────────────────────────────────────
#
# The resolver reads its own wall clock, so these cards are dated from the real now: "today"
# is now, an earlier ET day is 26 h ago (past any ET calendar day, DST included).

def _now():
    return datetime.now(timezone.utc)


class _Trend:
    async def get_trend(self, scope, days, *, now=None):
        return {"scope": scope, "days": days, "series": [], "tracking_since": None}


def _chat_rows(about=SCOPE, name=NAME):
    return [_article(f"c{i}", at=_now() - timedelta(hours=1, minutes=i), about=about, name=name)
            for i in range(3)]


async def _chat(monkeypatch, card, rows, scope=SCOPE):
    monkeypatch.setattr(insight_mod, "get_news_insight_service", lambda: _Insights(card))
    monkeypatch.setattr(news_mod, "get_news_cache_service", lambda: _News(rows))
    monkeypatch.setattr(trend_mod, "get_news_sentiment_trend_service", lambda: _Trend())
    meta: dict = {}
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", scope, meta=meta)
    return block, meta


def _card_line(block: str) -> str:
    lines = [ln for ln in block.splitlines() if "Cay AI Insights card" in ln]
    assert len(lines) == 1, block
    return lines[0]


@pytest.mark.asyncio
async def test_a_plain_card_line_carries_no_move_note(monkeypatch):
    block, _ = await _chat(monkeypatch, _plain(_now()), _chat_rows())
    line = _card_line(block)
    assert "today's move" not in line and line.endswith(CAUSE_HEADLINE)


def _parity_cases():
    return [
        ("company_today", lambda: _company(_now())),
        ("company_yesterday", lambda: _company(_now() - timedelta(hours=26))),
        ("none_today", lambda: _none(_now())),
        ("none_yesterday", lambda: _none(_now() - timedelta(hours=26))),
        ("none_no_time", lambda: {**_none(_now()), "generated_at": None}),
    ]


@pytest.mark.parametrize("name, make", _parity_cases(), ids=[c[0] for c in _parity_cases()])
@pytest.mark.parametrize("with_rows", [True, False], ids=["feed_news", "empty_feed"])
def test_chat_on_screen_matches_what_the_feed_serves(monkeypatch, name, make, with_rows):
    """Chat says "on screen" exactly when the feed serves the stored card."""
    card = make()
    rows = _chat_rows() if with_rows else []
    resp, _ = _feed(monkeypatch, card, rows, at=_now())
    feed_shows_card = resp.insight is not None and resp.insight.ai_generated is True
    block, _ = asyncio.run(_chat(monkeypatch, card, rows))
    assert ("Cay AI Insights card on screen" in (block or "")) is feed_shows_card, name


@pytest.mark.asyncio
async def test_the_chat_notes_name_no_vendor_and_no_model(monkeypatch):
    for make in (_company, _market, _none):
        block, _ = await _chat(monkeypatch, make(_now()), _chat_rows())
        for word in ("Brave", "Anthropic", "Claude", "Sonnet", "Google", "Gemini", "OpenAI"):
            assert word not in block


# ── coins: a coin's move card is current for a rolling 24 h, not an ET day ───────────────
#
# Review 2026-11-27. A coin trades around the clock and its move is the last 24 h
# (`news_insight_service._move_window_start`), so at 00:10 ET a coin still up 15% is still a
# jumper while its move corpus — and so its fingerprint — has not changed: the crypto pass
# answers `fingerprint_unchanged` and rewrites nothing. Hiding its 23:40 ET "no news yet
# explains the move" card by the ET day left "Latest headlines" / "Catching up…" in its place
# for hours. A coin's move card is therefore current until it is 24 h old — by then every story
# it read has left the coin's window, so the sweeper has a new fingerprint to act on. An equity
# keeps the ET-day rule: its move is a session's. Ask Cay AI agrees (parity below). A fictional
# coin, hand-written text.

COIN = "ZRKUSD"
COIN_NAME = "Zerkon"
COIN_NONE_HEADLINE = "No news yet explains Zerkon's move"
COIN_CAUSE_HEADLINE = "Zerkon's developers ship a long-delayed network upgrade"
COIN_CARD_AT = datetime(2026, 11, 24, 4, 40, tzinfo=timezone.utc)   # Monday 23:40 ET
AFTER_MIDNIGHT = datetime(2026, 11, 24, 5, 10, tzinfo=timezone.utc)  # Tuesday 00:10 ET
NEAR_A_DAY = datetime(2026, 11, 25, 4, 30, tzinfo=timezone.utc)      # Tuesday 23:30 ET, 23h50m
PAST_A_DAY = datetime(2026, 11, 25, 4, 50, tzinfo=timezone.utc)      # Tuesday 23:50 ET, 24h10m


def _coin_none(at, **kw):
    card = _stored(generated_at=at, headline=COIN_NONE_HEADLINE, sentiment="neutral",
                   reason="move:up:none + new_articles (2)", scope=COIN, **kw)
    assert card["move"] == {"kind": KIND_NONE, "direction": "up"}
    return card


def _coin_company(at, **kw):
    card = _stored(generated_at=at, headline=COIN_CAUSE_HEADLINE,
                   reason="move:up:company + new_articles (2)", scope=COIN, **kw)
    assert card["move"] == {"kind": KIND_COMPANY, "direction": "up"}
    return card


def _coin_rows():
    """The two stories the 23:40 ET card read; they stay in the feed's (widening) window."""
    return [_article(f"z{i}", at=COIN_CARD_AT - timedelta(minutes=30 + 40 * i), about=COIN,
                     name=COIN_NAME) for i in range(2)]


@pytest.mark.parametrize("at", [AFTER_MIDNIGHT, NEAR_A_DAY], ids=["after_midnight", "near_a_day"])
def test_a_coins_no_cause_card_stays_on_screen_across_et_midnight(monkeypatch, at):
    resp, insights = _feed(monkeypatch, _coin_none(COIN_CARD_AT), _coin_rows(), at=at, scope=COIN)
    assert resp.insight is not None and resp.insight.ai_generated is True
    assert resp.insight.headline == COIN_NONE_HEADLINE
    assert resp.insight.badge == "24h", "its own (move corpus) window, as on its write day"
    assert insights.fallback_calls == []


def test_a_coins_no_cause_card_gives_way_once_it_is_a_day_old(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=updates_endpoint.__name__)
    resp, insights = _feed(monkeypatch, _coin_none(COIN_CARD_AT), _coin_rows(), at=PAST_A_DAY,
                           scope=COIN)
    assert resp.insight is not None and resp.insight.ai_generated is False
    assert len(insights.fallback_calls) == 1
    hides = [r for r in caplog.records
             if r.levelno == logging.WARNING and "hiding no-cause move card" in r.getMessage()]
    assert len(hides) == 1
    msg = hides[0].getMessage()
    assert f"scope={COIN}" in msg and "age_h=24.2" in msg and "24 h" in msg
    for rec in caplog.records:
        line = rec.getMessage()
        assert COIN_NONE_HEADLINE not in line and COIN_NAME not in line


def test_an_equitys_no_cause_card_still_ends_at_et_midnight(monkeypatch):
    """The coin rule never reaches an equity: the same card and instants, an equity's is
    hidden at 00:10 ET (its move was Monday's session)."""
    rows = [_article(f"e{i}", at=COIN_CARD_AT - timedelta(minutes=30 + 40 * i)) for i in range(2)]
    resp, insights = _feed(monkeypatch, _none(COIN_CARD_AT), rows, at=AFTER_MIDNIGHT)
    assert resp.insight is not None and resp.insight.ai_generated is False
    assert len(insights.fallback_calls) == 1


@pytest.mark.parametrize("generated_at", [
    None, "", "not a date", (AFTER_MIDNIGHT + timedelta(hours=2)).isoformat(),
], ids=["none", "empty", "junk", "future"])
def test_a_coins_no_cause_card_with_an_unusable_time_is_never_shown(monkeypatch, generated_at):
    card = {**_coin_none(COIN_CARD_AT), "generated_at": generated_at}
    resp, insights = _feed(monkeypatch, card, _coin_rows(), at=AFTER_MIDNIGHT, scope=COIN)
    assert resp.insight is not None and resp.insight.ai_generated is False
    assert len(insights.fallback_calls) == 1


def test_a_coins_cause_card_keeps_its_move_badge_for_its_24h(monkeypatch):
    """One subject story in 24 h and three 30 h old: the recompute widens to 48h; the coin's
    move card, across ET midnight, keeps the move badge — the same rule as the visibility."""
    rows = [_article("z0", at=COIN_CARD_AT - timedelta(minutes=30), about=COIN, name=COIN_NAME)]
    rows += [_article(f"old{i}", at=AFTER_MIDNIGHT - timedelta(hours=30, minutes=i), about=COIN,
                      name=COIN_NAME) for i in range(3)]
    move, _ = _feed(monkeypatch, _coin_company(COIN_CARD_AT), rows, at=AFTER_MIDNIGHT, scope=COIN)
    plain = _stored(generated_at=COIN_CARD_AT, reason="new_articles (3)", scope=COIN)
    ordinary, _ = _feed(monkeypatch, plain, rows, at=AFTER_MIDNIGHT, scope=COIN)
    assert move.insight is not None and move.insight.badge == "24h"
    assert ordinary.insight is not None and ordinary.insight.badge == "48h"
    late, _ = _feed(monkeypatch, _coin_company(COIN_CARD_AT), rows, at=PAST_A_DAY, scope=COIN)
    assert late.insight is not None and late.insight.ai_generated is True, (
        "a cause card past its 24 h is an ordinary card, never hidden")


def _coin_previous_et_day_within_24h() -> datetime:
    """A real-clock instant on the PREVIOUS ET day that is still under 24 h ago (the midpoint of
    that interval, which is never empty)."""
    from app.utils.market_hours import ET

    now = _now()
    local = now.astimezone(ET)
    midnight = datetime(local.year, local.month, local.day, tzinfo=ET).astimezone(timezone.utc)
    earliest = now - timedelta(hours=24)
    return earliest + (midnight - earliest) / 2


def _coin_parity_cases():
    return [
        ("coin_none_previous_et_day", lambda: _coin_none(_coin_previous_et_day_within_24h())),
        ("coin_none_today", lambda: _coin_none(_now())),
        ("coin_none_over_24h", lambda: _coin_none(_now() - timedelta(hours=25))),
        ("coin_none_future", lambda: _coin_none(_now() + timedelta(hours=2))),
        ("coin_none_no_time", lambda: {**_coin_none(_now()), "generated_at": None}),
        ("coin_company_over_24h", lambda: _coin_company(_now() - timedelta(hours=25))),
    ]


@pytest.mark.parametrize("name, make", _coin_parity_cases(),
                         ids=[c[0] for c in _coin_parity_cases()])
@pytest.mark.parametrize("with_rows", [True, False], ids=["feed_news", "empty_feed"])
def test_chat_on_screen_matches_what_the_feed_serves_for_a_coin(monkeypatch, name, make,
                                                                   with_rows):
    card = make()
    rows = _chat_rows(about=COIN, name=COIN_NAME) if with_rows else []
    resp, _ = _feed(monkeypatch, card, rows, at=_now(), scope=COIN)
    feed_shows_card = resp.insight is not None and resp.insight.ai_generated is True
    block, _ = asyncio.run(_chat(monkeypatch, card, rows, scope=COIN))
    assert ("Cay AI Insights card on screen" in (block or "")) is feed_shows_card, name
    if with_rows and name in ("coin_none_previous_et_day", "coin_none_today"):
        assert feed_shows_card, f"anti-vacuity: {name} is served"
    if with_rows and name == "coin_none_over_24h":
        assert not feed_shows_card, "anti-vacuity: a day-old coin card is hidden"


@pytest.mark.asyncio
async def test_chat_says_why_a_day_old_coin_card_is_not_on_screen(monkeypatch):
    block, _ = await _chat(monkeypatch, _coin_none(_now() - timedelta(hours=25)),
                           _chat_rows(about=COIN, name=COIN_NAME), scope=COIN)
    line = _card_line(block)
    assert line.startswith(
        "The most recent Cay AI Insights card (not on screen: it said no news explained the "
        "coin's move when it was written, and such a card shows only for 24 hours)")
    assert "No Cay AI summary is on screen today" in block


@pytest.mark.asyncio
async def test_chat_notes_a_coins_card_from_before_midnight_as_a_move_card(monkeypatch):
    block, _ = await _chat(monkeypatch, _coin_none(_coin_previous_et_day_within_24h()),
                           _chat_rows(about=COIN, name=COIN_NAME), scope=COIN)
    line = _card_line(block)
    assert line.startswith("Cay AI Insights card on screen")
    assert line.endswith(COIN_NONE_HEADLINE + " (when written, it found no news explaining "
                         "today's move; answer from your own tools)")
