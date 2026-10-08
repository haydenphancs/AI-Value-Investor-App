"""`UPDATES_SCOPE` — "Ask Cay AI" opened from the Updates tab (the Insights card, its detail).

The resolver grounds the chat on what that tab actually served: the stored Insight card,
the newest in-window headlines and the News Tone card — every window it offers, cut from ONE
90-day read, plus the day-level detail of the window the chart showed. Hermetic — every
service it reads is stubbed at the module the resolver imports it from.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.services.news_cache_service as news_mod
import app.services.news_insight_service as insight_mod
import app.services.news_sentiment_trend_service as trend_mod
from app.schemas.chat import ChatContextType
from app.services.agents.chat_tools import chip_scope_block
from app.services.chat_context_resolver import (
    ChatContextResolver,
    _updates_scope,
    updates_scope_class_hint,
)
from app.services.chat_service import ChatService
from app.services.news_cache_service import MARKET_SCOPE
from app.services.news_insight_service import NewsInsightService
from app.utils.market_hours import ET


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


def _et_today() -> date:
    """The ET day the resolver counts windows from. Trend rows are dated RELATIVE to it: the
    resolver cuts the series by window, so a fixed date would age out of the test."""
    return datetime.now(timezone.utc).astimezone(ET).date()


def _day(days_ago: int, bull: int, bear: int, neut: int, *, partial: bool = False) -> dict:
    total = bull + bear + neut
    return {"date": (_et_today() - timedelta(days=days_ago)).isoformat(), "bullish": bull,
            "bearish": bear, "neutral": neut, "total": total,
            "net_score": trend_mod.net_score(bull, bear, total), "is_partial": partial}


class _Insights:
    def __init__(self, card=None, exc=None):
        self.card, self.exc, self.asked = card, exc, []

    async def get_cards(self, scopes):
        self.asked.append(list(scopes))
        if self.exc:
            raise self.exc
        return {s: self.card for s in scopes}


class _News:
    def __init__(self, rows=None, exc=None):
        self.rows, self.exc, self.asked = rows or [], exc, []

    def get_cached_bulk(self, scopes, per_scope_limit=25):
        self.asked.append((list(scopes), per_scope_limit))
        if self.exc:
            raise self.exc
        return {s: list(self.rows) for s in scopes}


class _Trend:
    def __init__(self, data=None, exc=None):
        self.data, self.exc, self.asked = data, exc, []

    async def get_trend(self, scope, days, *, now=None):
        self.asked.append((scope, days))
        if self.exc:
            raise self.exc
        return self.data or {"scope": scope, "days": days, "series": [], "tracking_since": None}


def _install(monkeypatch, *, insights=None, news=None, trend=None):
    insights = insights or _Insights()
    news = news or _News()
    trend = trend or _Trend()
    monkeypatch.setattr(insight_mod, "get_news_insight_service", lambda: insights)
    monkeypatch.setattr(news_mod, "get_news_cache_service", lambda: news)
    monkeypatch.setattr(trend_mod, "get_news_sentiment_trend_service", lambda: trend)
    return insights, news, trend


def _stored_card():
    """A card exactly as production serves it: a DB row through the real `_row_to_card`.

    Hand-writing the dict is how the resolver once read `price_move["tag"]` — a key the
    sanitizer never emits (it is `catalyst_tag`) — under a green test."""
    svc = NewsInsightService.__new__(NewsInsightService)
    card = svc._row_to_card({
        "scope": "ORCL",
        "headline": "Oracle reports results after the close",
        "bullets": ["Cloud revenue rose.", "Backlog grew.", "Shares moved after hours."],
        "sentiment": "bullish",
        "article_count": 3,
        "generated_at": _iso(timedelta(hours=3)),
        "prompt_version": 7,   # a post-retirement row; older ones are never served
        # A row stored before the grounded catalyst was retired (2026-10-02) still holds its
        # block until migration 188; production never serves it (`price_move` is None).
        "price_move": {"tier": "Unusual", "catalyst_tag": "Earnings",
                       "reason": "Results beat on cloud.", "change_pct": 7.25},
    }, market_active=False)
    assert card is not None
    return card


CARD = _stored_card()


def _row(headline, *, hours=1, sentiment="bullish", processed=True, tickers=("ORCL",)):
    return {
        "external_id": f"https://x/{headline}", "headline": headline,
        "published_at": _iso(timedelta(hours=hours)), "sentiment": sentiment,
        "ai_processed": processed, "related_tickers": list(tickers),
    }


# ── the reference id ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("ref,expected", [
    ("__MARKET__", "__MARKET__"),
    (" orcl ", "ORCL"),
    ("ETHUSD", "ETHUSD"),
    ("BRK.B", "BRK.B"),
    ("^GSPC", "^GSPC"),
    ("__market__", None),          # the reserved key is exact, never case-folded into shape
    ("OR CL", None), ("A;DROP", None), ("X" * 33, None), ("", None), (None, None),
])
def test_updates_scope_accepts_only_the_feed_shapes(ref, expected):
    assert _updates_scope(ref) == expected


# ── the grounding block ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_ticker_feed_grounds_on_card_headlines_and_trend(monkeypatch):
    since = _et_today() - timedelta(days=15)
    insights, news, trend = _install(
        monkeypatch,
        insights=_Insights(CARD),
        news=_News([
            _row("Oracle beats on cloud"),
            _row("Oracle guidance preview", hours=2, processed=False),
        ]),
        trend=_Trend({"scope": "ORCL", "days": 90, "tracking_since": since.isoformat(),
                      "series": [_day(10, 3, 1, 0)]}),
    )
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "orcl")
    assert insights.asked == [["ORCL"]]
    assert news.asked == [(["ORCL"], 25)]
    # ONE read, of the widest window: every window of the card is cut from it.
    assert trend.asked == [("ORCL", 90)]

    assert block.startswith("The user is on the Updates tab, looking at the news feed for ORCL.")
    assert "Cay AI Insights card on screen, written " in block and "3 h ago" in block
    assert "sentiment Bullish: Oracle reports results after the close" in block
    assert "• Backlog grew." in block
    # The grounded "why it moved" block is never served, so chat is never grounded on it
    # (Google Search grounding retired 2026-10-02) — not even from a row stored before then.
    assert CARD["price_move"] is None
    assert "Why it moved" not in block and "Results beat on cloud." not in block
    assert "Oracle beats on cloud (bullish)" in block
    # An unscored row carries no label — never a default "neutral".
    assert "Oracle guidance preview" in block and "Oracle guidance preview (" not in block
    assert "News tone over the last 30 days" in block
    assert f"oldest scored headline on file: {trend_mod._fmt_day(since)}" in block
    assert "7D: no scored headlines; 30D: 4 headlines (3 positive, 1 negative, 0 neutral)" in block
    assert block.rstrip().endswith("that are not here or in a tool result.")


@pytest.mark.asyncio
async def test_the_market_feed_is_described_as_the_market(monkeypatch):
    insights, news, trend = _install(monkeypatch, news=_News([_row("Fed holds", tickers=())]))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", MARKET_SCOPE)
    assert "news feed for the overall market (general market news)." in block
    # No stored card, but the feed has news: the screen shows the plain headline list.
    assert 'the card on screen is the plain "Latest headlines" list' in block
    assert "Fed holds (bullish)" in block
    assert trend.asked == [(MARKET_SCOPE, 90)]


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["card", "headlines", "trend"])
async def test_each_read_fails_on_its_own(monkeypatch, caplog, broken):
    boom = RuntimeError("supabase blip")
    _install(
        monkeypatch,
        insights=_Insights(CARD, exc=boom if broken == "card" else None),
        news=_News([_row("Oracle beats on cloud")], exc=boom if broken == "headlines" else None),
        trend=_Trend(exc=boom if broken == "trend" else None),
    )
    with caplog.at_level(logging.WARNING):
        block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL")
    assert block, "one failed read must not drop the grounding"
    assert f"UPDATES_SCOPE {broken} read failed for ORCL" in caplog.text
    assert ("Oracle reports results" in block) is (broken != "card")
    assert ("Oracle beats on cloud" in block) is (broken != "headlines")


@pytest.mark.asyncio
async def test_an_invalid_reference_is_ungrounded_never_the_client_token(monkeypatch, caplog):
    """The Updates client context is a CONTROL token (`window=90`), not text: when the
    resolver builds nothing it must not become the chat's grounding."""
    insights, news, trend = _install(monkeypatch)
    with caplog.at_level(logging.WARNING):
        out = await ChatContextResolver().resolve("UPDATES_SCOPE", "A;DROP", client_context="window=90")
    assert out is None
    assert insights.asked == [] and news.asked == [] and trend.asked == []
    assert "invalid UPDATES_SCOPE" in caplog.text


@pytest.mark.asyncio
async def test_headlines_are_capped_and_fence_text_is_left_to_the_fence(monkeypatch):
    rows = [_row(f"Story {i}", hours=1 + i / 10) for i in range(15)]
    _install(monkeypatch, news=_News(rows))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL")
    assert sum(1 for line in block.splitlines() if line.startswith("- ")) == 8


# ── registrations ───────────────────────────────────────────────────────────


def test_the_enum_carries_the_new_type():
    assert ChatContextType.UPDATES_SCOPE.value == "UPDATES_SCOPE"


@pytest.mark.parametrize("symbol,expected", [
    ("__MARKET__", "NORMAL"),
    ("ORCL", "STOCK"),
    ("ETHUSD", "CRYPTO"),
    ("GCUSD", "COMMODITY"),
    ("^GSPC", "INDEX"),
    # A watchlist ticker is the listed security, never a bare coin or an alias.
    ("LINK", "STOCK"),
    ("GOLD", "STOCK"),
    ("BTC", "STOCK"),
])
def test_asset_type_for_an_updates_scope(symbol, expected):
    assert ChatService._detect_asset_type(symbol, "UPDATES_SCOPE") == expected


def test_the_source_pill_names_the_feed_and_never_the_reserved_key():
    market = ChatService._build_sources("UPDATES_SCOPE", "__MARKET__", None, grounded=True)
    assert market == [{"label": "Updates feed", "detail": "Market"}]
    ticker = ChatService._build_sources("UPDATES_SCOPE", "orcl", None, grounded=True)
    assert ticker == [{"label": "Updates feed", "detail": "ORCL"}]
    assert ChatService._build_sources("UPDATES_SCOPE", "ORCL", None, grounded=False) == []


def test_chips_from_updates_lead_with_the_news():
    block = chip_scope_block("STOCK", "UPDATES_SCOPE")
    assert "how the news tone has shifted" in block
    assert "how the news tone has shifted" not in chip_scope_block("STOCK", "STOCK")


# ── review 2026-09-27: what the model is told must match what the screen serves ──


@pytest.mark.asyncio
async def test_peer_wrap_headlines_are_described_like_the_feed_shows_them(monkeypatch):
    """PLUG: all recent coverage is sector round-ups. The feed shows them (and a fallback
    card built from them); the subject filter would have told the model there was no news."""
    _install(monkeypatch, news=_News([
        _row("Hydrogen stocks slide as sector sells off", tickers=("BE", "BLDP", "PLUG")),
        _row("Fuel-cell names mixed", hours=2, tickers=("FCEL", "PLUG")),
    ]))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "PLUG")
    assert "Hydrogen stocks slide as sector sells off" in block
    assert "Fuel-cell names mixed" in block
    assert 'the card on screen is the plain "Latest headlines" list' in block


@pytest.mark.asyncio
async def test_a_stored_card_over_an_empty_window_is_not_called_on_screen(monkeypatch):
    _install(monkeypatch, insights=_Insights(CARD), news=_News([]))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL")
    assert "Cay AI Insights card on screen" not in block
    assert "The most recent Cay AI Insights card (not on screen" in block


@pytest.mark.asyncio
async def test_nothing_read_means_no_grounding_and_no_source_pill(monkeypatch):
    boom = RuntimeError("supabase down")
    _install(monkeypatch, insights=_Insights(None), news=_News(exc=boom), trend=_Trend(exc=boom))
    out = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL")
    assert out is None
    assert ChatService._build_sources(
        "UPDATES_SCOPE", "ORCL", None, resolved_context=out,
    ) == [], "the 'Updates feed' pill must be earned by data that arrived"


@pytest.mark.asyncio
async def test_rows_without_titles_do_not_count_as_grounding(monkeypatch):
    _install(monkeypatch, news=_News([_row("")]))
    assert await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL") is None


@pytest.mark.parametrize("ref,scope,hint", [
    ("SPY|ETF", "SPY", "ETF"),
    ("spy|etf", "SPY", "ETF"),
    ("ORCL|STOCK", "ORCL", None),      # only a fund is declared; the rest is read from the symbol
    ("ORCL", "ORCL", None),
    ("__MARKET__|ETF", "__MARKET__", "ETF"),
    ("|ETF", None, "ETF"),
])
def test_the_reference_may_declare_a_fund(ref, scope, hint):
    assert _updates_scope(ref) == scope
    assert updates_scope_class_hint(ref) == hint


def test_a_declared_fund_chats_as_an_etf_not_an_operating_company():
    assert ChatService._detect_asset_type("SPY", "UPDATES_SCOPE", "SPY|ETF") == "ETF"
    assert ChatService._detect_asset_type("SPY", "UPDATES_SCOPE", "SPY") == "STOCK"
    # The hint never overrides the market feed, and never reaches another context type.
    assert ChatService._detect_asset_type("__MARKET__", "UPDATES_SCOPE", "__MARKET__|ETF") == "NORMAL"
    assert ChatService._detect_asset_type("SPY", "STOCK", "SPY|ETF") == "STOCK"
    assert ChatService._build_sources("UPDATES_SCOPE", "SPY|ETF", None, grounded=True) == [
        {"label": "Updates feed", "detail": "SPY"}
    ]


@pytest.mark.asyncio
async def test_a_declared_fund_is_grounded_on_its_own_feed(monkeypatch):
    insights, news, trend = _install(monkeypatch, news=_News([_row("SPY inflows", tickers=("SPY",))]))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "SPY|ETF")
    assert insights.asked == [["SPY"]] and news.asked == [(["SPY"], 25)] and trend.asked == [("SPY", 90)]
    assert "news feed for SPY." in block



# ── the chart's window and a history still being built (2026-09-27 deep-check) ───────────


@pytest.mark.parametrize("ctx,days", [
    ("window=90", 90), ("window=7", 7), (" window=30 ", 30),
    (None, 30), ("", 30), ("window=14", 30), ("window=90;x", 30), ("90", 30), ("window=-7", 30),
])
def test_the_trend_window_is_parsed_strictly(ctx, days):
    from app.services.chat_context_resolver import updates_trend_window

    assert updates_trend_window(ctx) == days


@pytest.mark.asyncio
async def test_ask_about_this_on_90d_is_grounded_on_90_days(monkeypatch):
    series = [_day(60, 1, 5, 0)]
    _, _, trend = _install(monkeypatch, news=_News([_row("Story")]),
                           trend=_Trend({"scope": "ORCL", "days": 90, "series": series,
                                         "tracking_since": None}))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL", client_context="window=90")
    assert trend.asked == [("ORCL", 90)]
    assert "over the last 90 days" in block
    assert "window=90" not in block, "the token is never grounding text"


@pytest.mark.asyncio
async def test_a_building_history_is_never_called_final(monkeypatch):
    series = [_day(k, 2, 1, 0, partial=k < 2) for k in range(5)]
    _install(monkeypatch, news=_News([_row("Story")]), trend=_Trend({
        "scope": "ORCL", "days": 90, "series": series, "tracking_since": None,
        "history_status": "building"}))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL")
    assert "earlier days are final" not in block
    assert block.count("still being built") == 1, "the caveat is said once, not per part"


def test_a_ready_history_keeps_its_final_wording():
    today = datetime.now(timezone.utc).date()
    series = [{"date": today.isoformat(), "bullish": 1, "bearish": 0, "neutral": 0, "total": 1,
               "net_score": 100, "is_partial": True}]
    text = trend_mod.summarize_trend(series, days=30, today=today, history_status="ready")
    assert "earlier days are final" in text and "still being built" not in text


# ── 2026-10-05 (TestFlight 1.0 (11)): the tone card lost its own "Ask about this"; its data ──
# ── rides in the Insights card's chat — every window, from ONE 90-day read ──────────────────


_SWIFT_CHART = (Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios" / "Views"
                / "Molecules" / "NewsSentimentTrendChart.swift")


def _swift_func(name: str) -> str:
    """The brace-bound body of one Swift static func, comments stripped."""
    src = "\n".join(
        re.sub(r"\s//.*$", "", line) for line in _SWIFT_CHART.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("//")
    )
    header = f"static func {name}("
    assert src.count(header) == 1, f"{header!r} drifted — this parity scan would read nothing"
    open_at = src.index("{", src.index(header))
    depth = 0
    for i in range(open_at, len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[open_at:i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


@pytest.mark.asyncio
async def test_every_window_of_the_tone_card_is_grounded_from_one_read(monkeypatch):
    since = _et_today() - timedelta(days=80)
    series = [
        _day(2, 5, 1, 2, partial=True),   # in 7D, 30D and 90D
        _day(20, 1, 6, 0),                # in 30D and 90D
        _day(60, 9, 0, 1),                # in 90D only
    ]
    _, _, trend = _install(monkeypatch, insights=_Insights(CARD), news=_News([_row("Story")]),
                           trend=_Trend({"scope": "ORCL", "days": 90, "series": series,
                                         "tracking_since": since.isoformat(),
                                         "history_status": "ready"}))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL", client_context="window=7")
    assert trend.asked == [("ORCL", 90)], "one read, never one per window"
    tone = next(line for line in block.splitlines() if line.startswith("News tone for this feed"))
    assert f"oldest scored headline on file: {trend_mod._fmt_day(since)}" in tone
    assert "Positive / Negative / Neutral are Cay AI's bullish / bearish / neutral labels" in tone
    assert ("7D: 8 headlines (5 positive, 1 negative, 2 neutral), net +50 (Mostly positive); "
            "30D: 15 headlines (6 positive, 7 negative, 2 neutral), net -7 (Mixed); "
            "90D: 25 headlines (15 positive, 7 negative, 3 neutral), net +32 (Mostly positive).") in tone
    # The window the chart showed gets the day-level detail, after the windows.
    assert block.index("News tone for this feed") < block.index("News tone over the last 7 days")
    assert "over the last 30 days" not in block and "over the last 90 days" not in block


@pytest.mark.asyncio
async def test_a_window_with_no_scored_headline_says_so(monkeypatch):
    _install(monkeypatch, news=_News([_row("Story")]),
             trend=_Trend({"scope": "ORCL", "days": 90, "series": [_day(45, 2, 2, 0)],
                           "tracking_since": None}))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL")
    assert ("7D: no scored headlines; 30D: no scored headlines; "
            "90D: 4 headlines (2 positive, 2 negative, 0 neutral), net 0 (Mixed).") in block
    assert "on file" not in block and "scored for at least" not in block, (
        "no date is invented when none was served")
    # The 30D detail has nothing to describe; it is left out rather than said as zeros.
    assert "News tone over the last" not in block


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {"scope": "ORCL", "days": 90, "series": [], "tracking_since": "2026-07-01"},
    {"scope": "ORCL", "days": 90, "series": None, "tracking_since": None},
    {"scope": "ORCL", "days": 90, "tracking_since": None},   # no series key at all
    # Only days outside the 90-day window (malformed: the service never sends these).
    {"scope": "ORCL", "days": 90, "series": [_day(95, 3, 0, 0), _day(-3, 3, 0, 0)]},
], ids=["empty", "null-series", "no-series", "out-of-window"])
async def test_an_empty_trend_adds_no_tone_text(monkeypatch, data):
    _install(monkeypatch, insights=_Insights(CARD), news=_News([_row("Story")]), trend=_Trend(data))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL")
    assert "News tone for this feed" not in block and "News tone over" not in block
    assert "Oracle reports results" in block and "Story" in block, "the rest still grounds"


@pytest.mark.asyncio
async def test_an_empty_trend_alone_is_not_grounding(monkeypatch):
    """No card, no headline and a trend with nothing scored: nothing was read, so no block and
    no "Updates feed" pill — the same as before the windows were added."""
    _install(monkeypatch, insights=_Insights(None), news=_News([]),
             trend=_Trend({"scope": "ORCL", "days": 90, "series": [], "tracking_since": "2026-07-01"}))
    assert await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL") is None


@pytest.mark.asyncio
async def test_a_failed_trend_read_leaves_the_rest_and_says_why(monkeypatch, caplog):
    _install(monkeypatch, insights=_Insights(CARD), news=_News([_row("Story")]),
             trend=_Trend(exc=trend_mod.SentimentTrendUnavailable("PGRST202 news_sentiment_daily")))
    with caplog.at_level(logging.WARNING):
        block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL")
    assert "News tone for this feed" not in block and "Oracle reports results" in block
    assert "UPDATES_SCOPE trend read failed for ORCL: SentimentTrendUnavailable" in caplog.text


@pytest.mark.asyncio
async def test_malformed_rows_cost_only_themselves(monkeypatch, caplog):
    good = _day(1, 2, 1, 0)
    series = [
        None, "junk", 7, [],
        {"date": "not-a-day", "bullish": 9},
        {"bullish": 9},                                              # undated
        {"date": datetime.now(timezone.utc), "bullish": 9},          # an instant, not a day
        {**_day(3, 0, 0, 0)},                                        # all zero: absent
        {"date": _day(4, 0, 0, 0)["date"], "bullish": -5, "bearish": True, "neutral": "x"},
        good,
        {**good, "bullish": 50},                                     # the same day again: first wins
        {"date": _day(5, 0, 0, 0)["date"], "bullish": "3", "total": 999, "is_partial": "yes"},
    ]
    _install(monkeypatch, news=_News([_row("Story")]),
             trend=_Trend({"scope": "ORCL", "days": 90, "series": series,
                           "tracking_since": "garbage", "history_status": {"not": "a status"}}))
    with caplog.at_level(logging.WARNING):
        block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL")
    assert "trend read failed" not in caplog.text, "a bad row must not drop the whole tone block"
    # Counted: `good` once (2/1/0) and the string "3" bullish (its `total` is recomputed).
    assert "7D: 6 headlines (5 positive, 1 negative, 0 neutral), net +67 (Mostly positive)" in block
    assert "on file" not in block and "scored for at least" not in block
    assert "still filling in" not in block, "only a literal true marks a day partial"


@pytest.mark.asyncio
async def test_a_building_history_with_an_empty_focus_window_still_says_so(monkeypatch):
    _install(monkeypatch, news=_News([_row("Story")]),
             trend=_Trend({"scope": "ORCL", "days": 90, "series": [_day(40, 1, 0, 0)],
                           "tracking_since": None, "history_status": "building"}))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL", client_context="window=7")
    assert "7D: no scored headlines" in block
    assert block.count("still being built") == 1
    assert "do not read a trend from them yet" in block


def test_the_tone_word_and_sign_match_the_card():
    """The chat must name the card's verdict in the card's words: `toneWord` and `signed` in
    NewsSentimentTrendChart.swift are the source; the backend mirrors them."""
    word = _swift_func("toneWord")
    thresholds = re.findall(r"net (>=|<=) (-?\d+)", word)
    assert thresholds == [(">=", str(trend_mod.TONE_WORD_THRESHOLD)),
                          ("<=", str(-trend_mod.TONE_WORD_THRESHOLD))], thresholds
    assert re.findall(r'return "([^"]+)"', word) == ["Mostly positive", "Mostly negative", "Mixed"]
    t = trend_mod.TONE_WORD_THRESHOLD
    assert [trend_mod.tone_word(n) for n in (t, t - 1, 0, -(t - 1), -t)] == [
        "Mostly positive", "Mixed", "Mixed", "Mixed", "Mostly negative"]
    sign = _swift_func("signed")
    assert 'value > 0 ? "+\\(value)" : "\\(value)"' in sign
    assert [trend_mod._signed(n) for n in (21, 0, -7)] == ["+21", "0", "-7"]


@pytest.mark.asyncio
@pytest.mark.parametrize("days_ago,edge", [(120, True), (119, False)])
async def test_the_chat_never_presents_a_trimmed_date_as_the_start(monkeypatch, days_ago, edge):
    """Review 2026-10-06: `tracking_since` is the oldest label still ON FILE (the log keeps
    120 days). At the retention edge it moves forward daily, so the chat is told "at least 120
    days" instead; before it, the date is worded as the oldest on file — never "tracked since"."""
    since = _et_today() - timedelta(days=days_ago)
    _install(monkeypatch, news=_News([_row("Story")]),
             trend=_Trend({"scope": "ORCL", "days": 90, "series": [_day(3, 2, 1, 0)],
                           "tracking_since": since.isoformat(), "history_status": "ready"}))
    block = await ChatContextResolver().resolve("UPDATES_SCOPE", "ORCL")
    assert "tracked since" not in block
    assert ("scored for at least 120 days" in block) is edge
    assert (trend_mod._fmt_day(since) in block) is not edge, "a trimmed date never reaches the model"


def test_the_windows_the_chat_cuts_are_the_cards_windows():
    """`summarize_tone_windows` walks TREND_DAYS, the toggle's options (pinned to the Swift
    enum by test_updates_sentiment_trend_ios_contract), so a fourth window cannot be missed."""
    series = [_day(k, 1, 0, 0) for k in range(0, 90, 3)]
    text = trend_mod.summarize_tone_windows(series, today=_et_today())
    assert [int(n) for n in re.findall(r"(\d+)D: ", text)] == sorted(trend_mod.TREND_DAYS)
