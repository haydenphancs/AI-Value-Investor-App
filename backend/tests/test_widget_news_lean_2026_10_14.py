"""The widget and Ask Cay AI say what the Updates Insights card says — when it agrees.

TestFlight 2026-10-14: CRWV −15.46% (z 2.57, industry −4.0%) printed "Software -
Infrastructure fell 4.0%; CRWV moved far more. No clear catalyst in today's news." while the
app's Insights card for that session read "CoreWeave Faces Investor Scrutiny Amid Rising Bond
Yields" under a Bearish pill. The keyword classifier matched none of those words, and Ask Cay
AI's `explain_price_move` returned the same false sentence.

These tests cover the SERVICE half: `_session_news_lean` reads the card only behind the one
session gate `_build_mover` / `attribute_ticker_move` already run, both surfaces pass the same
lean to the pure `attribute()`, and the payload carries `cause.note`. The gate itself
(sentiment, direction, z ≥ 2, post-close, headline usability) is pinned in
`test_daily_move_attribution.py`.
"""
from __future__ import annotations

import inspect
from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.services import chat_market_tools as cmt
from app.services import widget_movers_service as wm
from app.services.daily_move_attribution import NEWS_LEAN_TAGS, NewsLean
from app.services.widget_movers_service import (
    RankedMover,
    WidgetMoversService,
    _MarketContext,
    _session_news_lean,
    rank_movers,
)

WED = date(2026, 10, 14)
WED_ISO = WED.isoformat()
HEADLINE = "CoreWeave Faces Investor Scrutiny Amid Rising Bond Yields"
NOTE = "Software - Infrastructure fell 4.0%."


def _card(headline=HEADLINE, sentiment="Bearish", generated_at="2026-10-14T17:09:05Z",
          ai_generated=True):
    card = {"scope": "CRWV", "headline": headline, "sentiment": sentiment,
            "generated_at": generated_at, "bullets": ["a", "b"]}
    if ai_generated is not None:
        card["ai_generated"] = ai_generated
    return card


def _crwv(pct=-15.46, z=2.57, stamp=WED_ISO):
    return RankedMover(ticker="CRWV", change_percent=pct, price=71.2,
                       company_name="CoreWeave, Inc.", sigma_daily=0.0602, z=z,
                       tier="Unusual", change_session=stamp)


def _other(stamp=WED_ISO):
    return RankedMover(ticker="NYAX", change_percent=2.08, price=40.0, company_name="Nayax Ltd.",
                       sigma_daily=0.0327, z=0.64, tier="Typical", change_session=stamp)


def _ctx(iso=WED_ISO, earnings=None):
    c = _MarketContext(news_available=True, industry_available=True,
                       industry_changes={"software - infrastructure": -4.0},
                       industry_dates={"software - infrastructure": iso},
                       earnings_available=earnings is not None, earnings=earnings or {})
    return c.for_tickers({"CRWV": "Software - Infrastructure"}, iso, {})


def _payload(monkeypatch, *, cards, live=WED, calendar=None, ranked=None, ctx=None):
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: live)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: calendar or live)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Closed")
    return WidgetMoversService()._payload(
        mode="portfolio",
        ranked=ranked or [_crwv(), _other()],
        cards=cards, ctx=ctx or _ctx(), basket=None, holdings_count=14,
    )


# ── _session_news_lean: only behind the gate the caller already ran ──────────────


def test_the_lean_reads_the_card_only_when_the_gate_said_it_is_this_sessions():
    lean = _session_news_lean(_card(), True, "CRWV", "CoreWeave, Inc.", rolling=False, day=WED)
    assert isinstance(lean, NewsLean)
    assert lean.headline == HEADLINE and lean.sentiment == "Bearish"
    assert "CRWV" in lean.subject_terms and "coreweave" in lean.subject_terms
    assert lean.generated_at == "2026-10-14T17:09:05Z" and lean.day == WED
    assert _session_news_lean(_card(), False, "CRWV", "CoreWeave, Inc.", rolling=False, day=WED) is None
    assert _session_news_lean(None, True, "CRWV", None, rolling=False, day=WED) is None


@pytest.mark.parametrize("ai", [False, None, "true", 1])
def test_only_an_ai_card_can_lean(ai):
    """The Updates fallback card ("Latest … headlines") is not a summary of anything."""
    assert _session_news_lean(_card(ai_generated=ai), True, "CRWV", None, rolling=False, day=WED) is None


@pytest.mark.parametrize("raw,want", [("bearish", "Bearish"), ("Negative", "Bearish"),
                                      ("positive", "Bullish"), ("Neutral", "Neutral"),
                                      (None, ""), ("Mixed", "")])
def test_the_card_sentiment_is_mapped_to_the_card_domain(raw, want):
    lean = _session_news_lean(_card(sentiment=raw), True, "CRWV", None, rolling=False, day=WED)
    assert lean.sentiment == want


def test_an_empty_headline_is_no_lean():
    assert _session_news_lean(_card(headline="  "), True, "CRWV", None, rolling=False, day=WED) is None


def test_a_name_lookup_failure_is_logged_and_falls_back_to_the_symbol(monkeypatch, caplog):
    def _boom(*_a, **_k):
        raise RuntimeError("names table missing")

    monkeypatch.setattr(wm, "price_terms", _boom)
    lean = _session_news_lean(_card(), True, "CRWV", "CoreWeave, Inc.", rolling=False, day=WED)
    assert lean.subject_terms == ("CRWV",)
    assert any("price_terms failed for CRWV" in r.getMessage() for r in caplog.records)


def test_the_lean_helper_adds_no_second_date_gate():
    """One gate, one implementation: `had_news` from `_classified_today_news` /
    `_classified_rolling_news`. A date comparison here could drift from that gate."""
    src = inspect.getsource(_session_news_lean)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "_et_date(" not in code
    assert "generated_at\")" not in code.replace('card.get("generated_at")', "")


# ── the widget payload ───────────────────────────────────────────────────────────


def test_the_crwv_tile_shows_the_insights_headline_and_the_industry_note(monkeypatch):
    head = _payload(monkeypatch, cards={"CRWV": _card()}).headline_mover
    assert head.ticker == "CRWV"
    assert head.cause.kind == "company_news"
    assert head.cause.tag == "Bearish News"
    assert head.cause.detail == HEADLINE + "."
    assert head.cause.detail_aged == head.cause.detail
    assert head.cause.note == NOTE
    assert "No clear catalyst" not in head.cause.detail


def test_installed_builds_decode_the_lean_as_an_established_cause(monkeypatch):
    """Clients from before 2026-10-14 know `company_news` (tag + primary text) and ignore
    `note`: the fix reaches them with the backend deploy alone."""
    d = _payload(monkeypatch, cards={"CRWV": _card()}).model_dump()
    cause = d["headline_mover"]["cause"]
    assert cause["kind"] in {"earnings", "analyst", "company_news", "sector", "market", "none"}
    assert set(cause) == {"kind", "tag", "detail", "detail_aged", "note"}


def test_a_neutral_card_keeps_the_honest_line(monkeypatch):
    head = _payload(monkeypatch, cards={"CRWV": _card(sentiment="Neutral")}).headline_mover
    assert head.cause.kind == "none"
    assert head.cause.detail == NOTE + " No clear catalyst in today's news."
    assert head.cause.note is None


def test_an_opposite_card_keeps_the_honest_line(monkeypatch):
    head = _payload(monkeypatch, cards={"CRWV": _card(sentiment="Bullish")}).headline_mover
    assert head.cause.kind == "none"
    assert "No clear catalyst" in head.cause.detail


def test_a_routine_move_with_an_agreeing_card_says_the_news_leaned(monkeypatch):
    head = _payload(monkeypatch, cards={"CRWV": _card()},
                    ranked=[_crwv(pct=-6.9, z=1.64), _other()]).headline_mover
    assert head.cause.kind == "none"
    assert head.cause.detail.endswith("Today's news leaned bearish."), head.cause.detail
    assert HEADLINE not in head.cause.detail
    assert head.cause.detail_aged.endswith("Wednesday's news leaned bearish."), head.cause.detail_aged


def test_a_card_from_a_prior_session_is_no_lean(monkeypatch):
    head = _payload(monkeypatch, cards={"CRWV": _card(generated_at="2026-10-13T17:00:00Z")}).headline_mover
    assert head.cause.kind == "none"
    assert head.cause.detail.endswith("No company news today."), head.cause.detail


def test_a_card_newer_than_the_session_is_unchecked_not_a_lean(monkeypatch):
    """Thursday pre-market tile describing Wednesday: the sweeper already replaced
    Wednesday's card with Thursday's, which says nothing about Wednesday."""
    head = _payload(monkeypatch, live=date(2026, 10, 15), cards={
        "CRWV": _card(generated_at="2026-10-15T10:05:00Z")},
        ranked=[_crwv(stamp=WED_ISO), _other(stamp=WED_ISO)]).headline_mover
    assert head.cause.kind != "company_news", head.cause.detail
    assert "leaned" not in head.cause.detail
    assert "No company news" not in head.cause.detail


def test_a_card_blanked_behind_a_failure_is_no_lean(monkeypatch):
    head = _payload(monkeypatch, cards={"CRWV": None}).headline_mover
    assert head.cause.kind == "none"
    assert "leaned" not in head.cause.detail and HEADLINE not in head.cause.detail


def test_an_after_close_card_on_a_report_day_is_withheld(monkeypatch):
    ctx = _ctx(earnings={"CRWV": {"date": WED_ISO, "time": "amc",
                                  "epsActual": -0.21, "epsEstimated": 0.05}})
    head = _payload(monkeypatch, ctx=ctx, cards={
        "CRWV": _card(headline="CoreWeave Misses Revenue Estimates",
                      generated_at="2026-10-14T20:40:00Z")}).headline_mover
    assert head.cause.kind == "none"
    assert head.cause.detail.endswith("Today's news leaned bearish."), head.cause.detail


def test_rows_below_the_headline_carry_the_lean_but_never_an_aged_wording(monkeypatch):
    out = _payload(monkeypatch, cards={"CRWV": _card(), "NYAX": _card(
        headline="Nayax Expands Platform", sentiment="Bullish")},
        ranked=[_other(), _crwv()])
    assert out.headline_mover.ticker == "NYAX"
    crwv = [m for m in out.top_losers if m.ticker == "CRWV"]
    assert crwv and crwv[0].cause.kind == "company_news" and crwv[0].cause.note == NOTE
    rows = out.top_gainers + out.top_losers + out.runners_up
    assert all(m.cause.detail_aged is None for m in rows)


def test_a_round_the_clock_row_uses_its_own_calendar_day_card(monkeypatch):
    btc_card = _card(headline="Bitcoin Faces New Regulatory Scrutiny in Europe",
                     generated_at="2026-10-17T15:00:00Z")    # Saturday 11:00 ET
    out = _payload(
        monkeypatch, live=date(2026, 10, 16), calendar=date(2026, 10, 17),
        cards={"BTCUSD": btc_card},
        ranked=rank_movers([
            {"ticker": "BTCUSD", "change_percent": -6.1, "price": 104000.0},
            {"ticker": "AMD", "change_percent": -1.0, "price": 150.0, "change_session": "2026-10-16"},
        ], basis="abs_change"),
        ctx=_MarketContext(news_available=True),
    )
    btc = out.headline_mover
    assert btc.ticker == "BTCUSD"
    assert btc.cause.kind == "company_news", btc.cause.detail
    assert btc.cause.detail == "Bitcoin Faces New Regulatory Scrutiny in Europe."


def test_a_round_the_clock_rows_friday_card_is_not_saturdays_lean(monkeypatch):
    out = _payload(
        monkeypatch, live=date(2026, 10, 16), calendar=date(2026, 10, 17),
        cards={"BTCUSD": _card(headline="Bitcoin Faces New Regulatory Scrutiny in Europe",
                               generated_at="2026-10-16T18:00:00Z")},
        ranked=rank_movers([
            {"ticker": "BTCUSD", "change_percent": -6.1, "price": 104000.0},
            {"ticker": "AMD", "change_percent": -1.0, "price": 150.0, "change_session": "2026-10-16"},
        ], basis="abs_change"),
        ctx=_MarketContext(news_available=True),
    )
    assert out.headline_mover.cause.kind != "company_news"
    assert "leaned" not in out.headline_mover.cause.detail


def test_spy_in_a_portfolio_never_leans_on_a_story_about_the_market(monkeypatch):
    spy = RankedMover(ticker="SPY", change_percent=-3.5, price=640.0,
                      company_name="SPDR S&P 500 ETF Trust", sigma_daily=0.01, z=3.5,
                      tier="Extreme", change_session=WED_ISO)
    head = _payload(monkeypatch, ranked=[spy, _other()], ctx=_MarketContext(news_available=True),
                    cards={"SPY": _card(headline="Wall Street Falls as Treasury Yields Jump")}
                    ).headline_mover
    assert head.cause.kind == "none", head.cause.detail
    assert head.cause.detail.endswith("Today's news leaned bearish.")


# ── Ask Cay AI gives the widget's answer ───────────────────────────────────────


def _attribution_service(monkeypatch, *, mover, card, earnings=None):
    svc = WidgetMoversService()
    rows = {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": -0.4,
                    "changeSession": WED_ISO}}

    async def _rank_and_read(_syms):
        return [mover], {mover.ticker: card}, True, rows

    async def _context(_tickers, index_rows, session_date):
        return _ctx(session_date, earnings=earnings).for_tickers(
            {"CRWV": "Software - Infrastructure"}, session_date, index_rows)

    monkeypatch.setattr(svc, "_rank_and_read", _rank_and_read)
    monkeypatch.setattr(svc, "_market_context", _context)
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: WED)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: WED)
    return svc


@pytest.mark.asyncio
async def test_ask_cay_gets_the_same_lean_as_the_widget(monkeypatch):
    svc = _attribution_service(monkeypatch, mover=_crwv(), card=_card())
    exp = await svc.attribute_ticker_move("CRWV")
    a = exp.attribution
    assert a.kind.value == "company_news" and a.tag == "Bearish News"
    assert a.detail == HEADLINE + "."
    assert a.note == NOTE

    widget = _payload(monkeypatch, cards={"CRWV": _card()}).headline_mover
    assert widget.cause.detail == a.detail and widget.cause.note == a.note


@pytest.mark.asyncio
async def test_explain_price_move_presents_the_lean_as_the_news_not_a_proven_catalyst(monkeypatch):
    svc = _attribution_service(monkeypatch, mover=_crwv(), card=_card())
    monkeypatch.setattr(wm, "get_widget_movers_service", lambda: svc)
    monkeypatch.setattr(cmt, "fetch_ticker_news",
                        AsyncMock(return_value={"news_available": True, "articles": [{"title": "x"}]}))
    out = await cmt.explain_price_move("CRWV")
    assert out["cause"] == "Bearish News"
    assert out["explanation"] == HEADLINE + "."
    assert out["explanation_basis"].startswith("news_lean:")
    assert out["move_context"] == NOTE
    assert "bottom_line" not in out and "no_single_catalyst" not in out


@pytest.mark.asyncio
async def test_a_withheld_lean_never_reaches_the_chat_as_no_catalyst(monkeypatch):
    svc = _attribution_service(monkeypatch, mover=_crwv(pct=-6.9, z=1.64), card=_card())
    monkeypatch.setattr(wm, "get_widget_movers_service", lambda: svc)
    monkeypatch.setattr(cmt, "fetch_ticker_news",
                        AsyncMock(return_value={"news_available": True, "articles": [{"title": "x"}]}))
    out = await cmt.explain_price_move("CRWV")
    assert out["no_single_catalyst"] is True
    assert "Today's news leaned bearish; no single item stands out as the cause." in out["bottom_line"]
    assert "No single catalyst stands out" not in out["bottom_line"]
    assert "explanation_basis" not in out


@pytest.mark.asyncio
async def test_a_failed_news_read_still_wins_over_a_lean_in_the_bottom_line(monkeypatch):
    """The chat's own news read failed: say it could not be checked, whatever the card."""
    svc = _attribution_service(monkeypatch, mover=_crwv(pct=-6.9, z=1.64), card=_card())
    monkeypatch.setattr(wm, "get_widget_movers_service", lambda: svc)
    monkeypatch.setattr(cmt, "fetch_ticker_news", AsyncMock(return_value={"news_available": False}))
    out = await cmt.explain_price_move("CRWV")
    assert "could not be checked" in out["bottom_line"]


def test_the_chat_lean_tags_are_the_attribution_modules():
    """COUPLING GUARD, like `_BEAT_TAG`: a reword in `daily_move_attribution` must fail here
    rather than silently drop `explanation_basis`."""
    assert cmt._LEAN_TAGS == frozenset(NEWS_LEAN_TAGS.values())


@pytest.mark.asyncio
async def test_the_chat_never_names_its_context_key_note(monkeypatch):
    """`note` is this tool's instruction to the model on its unreadable paths; the industry
    arithmetic must not collide with it."""
    svc = _attribution_service(monkeypatch, mover=_crwv(), card=_card())
    monkeypatch.setattr(wm, "get_widget_movers_service", lambda: svc)
    monkeypatch.setattr(cmt, "fetch_ticker_news",
                        AsyncMock(return_value={"news_available": True, "articles": []}))
    out = await cmt.explain_price_move("CRWV")
    assert "note" not in out


def test_the_payloads_without_a_card_are_unchanged(monkeypatch):
    """No card, no sentiment, no `ai_generated`: every pre-existing fixture shape keeps the
    exact sentence it had (the lean is inert)."""
    head = _payload(monkeypatch, cards={}).headline_mover
    assert head.cause.kind == "none"
    assert head.cause.note is None
    legacy = _payload(monkeypatch, cards={"CRWV": {"headline": HEADLINE,
                                                   "generated_at": "2026-10-14T17:09:05Z"}}).headline_mover
    assert legacy.cause.detail == NOTE + " No clear catalyst in today's news."


def test_googl_headlines_name_their_subject_by_the_brand():
    """Live 2026-10-14: the one honest card refused for not naming its subject was GOOGL's
    "Google expands AI, secures energy for growth" — its name list only knows "Alphabet"."""
    from app.services.daily_move_attribution import usable_lean_headline

    lean = _session_news_lean(_card(headline="Google expands AI, secures energy for growth"),
                              True, "GOOGL", "Alphabet Inc.", rolling=False, day=WED)
    assert "Google" in lean.subject_terms
    assert usable_lean_headline(lean.headline, "GOOGL", lean.subject_terms, WED)
    # The alias is the widget's own: the Insights price guard's name list is unchanged.
    assert "Google" not in wm.price_terms("GOOGL", "Alphabet Inc.")


# 2026-10-16: each press-name alias, with the legal name `price_terms` knows. Every entry is
# needed: without its alias the honest headline is refused for not naming its subject.
_PRESS_NAMES = [
    ("DIS", "The Walt Disney Company", "Disney Cuts Streaming Outlook on Subscriber Slowdown"),
    ("DIS", "The Walt Disney Company", "Disney’s Parks Unit Cuts Annual Outlook"),
    ("LLY", "Eli Lilly and Company", "Lilly Halts Obesity Drug Trial on Safety Signal"),
    ("PG", "The Procter & Gamble Company", "P&G Cuts Annual Sales Forecast on Weak Demand"),
    ("TSM", "Taiwan Semiconductor Manufacturing Company Limited",
     "TSMC Raises Annual Revenue Outlook on AI Demand"),
    ("SMCI", "Super Micro Computer, Inc.", "Supermicro Faces Delisting Risk Over Late Filing"),
    ("BAC", "Bank of America Corporation", "BofA Settles Overdraft Fee Probe With Regulators"),
    ("JNJ", "Johnson & Johnson", "J&J Cuts Annual Outlook on Talc Charge"),
    ("BMY", "Bristol-Myers Squibb Company", "Bristol Myers Cuts Annual Outlook on Drug Pricing"),
    ("AXP", "American Express Company", "AmEx Cuts Annual Outlook as Card Spending Slows"),
    ("C", "Citigroup Inc.", "Citi Cuts Annual Outlook on Trading Slowdown"),
    ("SCHW", "The Charles Schwab Corporation", "Schwab Cuts Annual Outlook on Rate Cuts"),
    ("EL", "The Estée Lauder Companies Inc.", "Estee Lauder Cuts Annual Outlook on China Demand"),
    ("MSTR", "Strategy Inc", "MicroStrategy Faces Index Exclusion Review"),
    ("RY", "Royal Bank of Canada", "RBC Cuts Annual Outlook on Credit Losses"),
    ("YUM", "Yum! Brands, Inc.", "Yum Brands Cuts Annual Outlook on Weak Demand"),
    ("ON", "ON Semiconductor Corporation", "Onsemi Cuts Annual Outlook on Auto Chip Slump"),
    ("DE", "Deere & Company", "John Deere Cuts Annual Outlook on Farm Demand"),
    ("BUD", "Anheuser-Busch InBev SA/NV", "AB InBev Cuts Annual Outlook on Weak Demand"),
]


@pytest.mark.parametrize("ticker,legal,headline", _PRESS_NAMES)
def test_a_press_name_names_the_subject(ticker, legal, headline):
    from app.services.daily_move_attribution import usable_lean_headline

    lean = _session_news_lean(_card(headline=headline), True, ticker, legal,
                              rolling=False, day=WED)
    assert usable_lean_headline(lean.headline, ticker, lean.subject_terms, WED) == headline
    # the pin: the legal name alone does not name it, so the alias is what admits it
    bare = tuple(wm.price_terms(ticker, legal))
    assert usable_lean_headline(headline, ticker, bare, WED) is None, (headline, bare)


def test_every_alias_has_a_press_name_test():
    covered = {t for t, _, _ in _PRESS_NAMES} | {"GOOGL", "GOOG"}
    assert set(wm._LEAN_BRAND_ALIASES) == covered


@pytest.mark.parametrize("ticker,headline", [
    # a one-word alias names the company only as the headline's subject
    ("C", "Banks Brace as Citi Rivals Cut Outlook"),
    ("LLY", "Obesity Drug Makers Slide as Lilly Rival Halts Trial"),
    ("SCHW", "Brokers Cut Outlook as Schwab Rival Warns"),
    # an all-caps alias matches as written, never inside another word
    ("TSM", "TSMCs Supplier Cuts Outlook"),
])
def test_an_alias_never_names_a_peers_story(ticker, headline):
    from app.services.daily_move_attribution import usable_lean_headline

    lean = _session_news_lean(_card(headline=headline), True, ticker, None,
                              rolling=False, day=WED)
    assert usable_lean_headline(lean.headline, ticker, lean.subject_terms, WED) is None


# ── a web-sourced card (the Updates jump search, 2026-10-28) ─────────────────────

@pytest.mark.asyncio
async def test_a_lean_from_a_web_sourced_card_marks_the_chat_tool_result(monkeypatch):
    """Ask Cay reads the web-sourced card (owner: "Yes, Ask Cay reads it"); the tool result says
    so, which keeps the turn out of the grounding audit and the refund judge."""
    card = {**_card(), "web_assisted": True}
    svc = _attribution_service(monkeypatch, mover=_crwv(), card=card)
    exp = await svc.attribute_ticker_move("CRWV")
    assert exp.web_assisted is True
    monkeypatch.setattr(wm, "get_widget_movers_service", lambda: svc)
    monkeypatch.setattr(cmt, "fetch_ticker_news",
                        AsyncMock(return_value={"news_available": True, "articles": [{"title": "x"}]}))
    out = await cmt.explain_price_move("CRWV")
    assert out["explanation"] == HEADLINE + "." and out["web_assisted"] is True


@pytest.mark.asyncio
async def test_an_ordinary_card_or_an_unused_web_card_marks_nothing(monkeypatch):
    svc = _attribution_service(monkeypatch, mover=_crwv(), card=_card())
    assert (await svc.attribute_ticker_move("CRWV")).web_assisted is False
    # A web-sourced card the attribution did NOT read: written the session before, so neither
    # the cause nor the lean comes from it. (A withheld lean below z 2 still READS the card —
    # "Today's news leaned bearish" — so that case is marked, conservatively.)
    unused = _attribution_service(monkeypatch, mover=_crwv(),
                                  card={**_card(generated_at="2026-10-13T17:09:05Z"),
                                        "web_assisted": True})
    exp = await unused.attribute_ticker_move("CRWV")
    assert exp.attribution.kind.value != "company_news" and exp.attribution.lean is None
    assert exp.web_assisted is False
    withheld = _attribution_service(monkeypatch, mover=_crwv(pct=-6.9, z=1.64),
                                    card={**_card(), "web_assisted": True})
    assert (await withheld.attribute_ticker_move("CRWV")).web_assisted is True
    # Review 2026-10-29: a same-session web card made `lean` truthy, which marked EVERY
    # attribution — an earnings win, which prints no card text, kept its turn and follow-ups
    # out of the refund judge. Since 2026-10-30 an agreeing card on an Unusual move outranks
    # the earnings, and is marked because the answer IS its headline; the earnings win left
    # is the one below z 2, whose withheld lean must not ride along on it.
    miss = {"CRWV": {"date": WED_ISO, "time": "bmo", "epsActual": -0.21, "epsEstimated": 0.05}}
    earnings_win = _attribution_service(
        monkeypatch, mover=_crwv(pct=-6.9, z=1.64), card={**_card(), "web_assisted": True},
        earnings=miss,
    )
    exp = await earnings_win.attribute_ticker_move("CRWV")
    assert exp.attribution.kind.value == "earnings" and exp.attribution.lean is None
    assert exp.web_assisted is False
    lean_win = _attribution_service(
        monkeypatch, mover=_crwv(), card={**_card(), "web_assisted": True}, earnings=miss,
    )
    exp = await lean_win.attribute_ticker_move("CRWV")
    assert exp.attribution.kind.value == "company_news" and exp.attribution.detail == HEADLINE + "."
    assert exp.web_assisted is True
    monkeypatch.setattr(wm, "get_widget_movers_service", lambda: unused)
    monkeypatch.setattr(cmt, "fetch_ticker_news",
                        AsyncMock(return_value={"news_available": True, "articles": [{"title": "x"}]}))
    assert "web_assisted" not in await cmt.explain_price_move("CRWV")
